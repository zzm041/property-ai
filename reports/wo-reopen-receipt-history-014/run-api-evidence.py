"""014 API negatives, real concurrent races and Worker redispatch, AUTH001 only.

Business writes exclusively use authenticated HTTP. DB queries are read-only.
The existing SLA service is invoked separately as a labelled scheduler check,
scoped to one new order; this is not a page action or cross-process task claim.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
import hashlib
import json
import runpy
import struct
import zlib

import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.service.sla import SlaService

REPO = Path(__file__).resolve().parents[2]
OUT = Path(__file__).parent
PRIVATE = REPO / "tmp/wo-reopen-receipt-history-014/context.json"
HELPERS = runpy.run_path(str(REPO / "reports/notification-events-inbox-013/run-runtime.py"))


def main():
    ctx = json.loads(PRIVATE.read_text(encoding="utf-8"))
    run_id = ctx["run_id"]
    page = json.loads((OUT / "page-evidence.json").read_text(encoding="utf-8"))
    assert page.get("status") == "PAGE_FLOW_PASS_PENDING_INDEPENDENT_REVIEW"
    settings = HELPERS["_guard"]()
    engine = create_engine(settings.test_database_url)
    client = HELPERS["Client"](run_id)
    state_path = OUT / "api-evidence.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {
        "task_id": "WO-REOPEN-RECEIPT-HISTORY-014", "run_id": run_id,
        "code_sha": page["code_sha"], "results": {}, "requests": [], "objects": {},
        "mode": "authenticated HTTP / read-only SQL / labelled direct SLA scheduler checks; Development providers",
    }
    tokens = {key: value["access_token"] for key, value in ctx["done"].items() if key.startswith("login-")}
    tokens = {key[6:]: value for key, value in tokens.items()}

    def save():
        state["requests"].extend(client.requests)
        client.requests.clear()
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    def step(label, operation):
        if label not in state["results"]:
            state["results"][label] = operation()
            save()
        return state["results"][label]

    def call(method, path, *, actor="resident", body=None, label, expected=(200,), headers=None):
        return client.call(method, path, token=tokens[actor], body=body, label=label, expected=expected, headers=headers)

    def data(method, path, **kwargs):
        return call(method, path, **kwargs)[1]["data"]

    def detail(order_id):
        return data("GET", f"/work-orders/{order_id}", label=f"detail-{order_id}")

    def sql(query, order_id):
        with engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            return [dict(row) for row in connection.execute(text(query), {"id": order_id}).mappings()]

    def clock(order_id):
        return sql("SELECT state, warning_at, complete_due_at FROM sla_clocks WHERE work_order_id=:id", order_id)[0]

    def scheduler(order_id, when):
        with Session(engine) as session:
            return SlaService(session, settings=settings).sweep_due(now=when, work_order_id=order_id).as_dict()

    def assigned(order_id):
        result = HELPERS["_wait_assigned"](client, tokens["resident"], order_id)
        assignment = result["assignments"][-1]
        assert assignment["reason"] == "AUTO_DISPATCH" and assignment["assigned_by_user_id"] is None
        staff = next(name for name, employee_id in ctx["employees"].items() if employee_id == assignment["employee_id"])
        return result, staff

    def to_processing(order_id, staff, label):
        result = detail(order_id)["work_order"]
        if result["status"] == "ASSIGNED":
            result = data("POST", f"/staff/work-orders/{order_id}/accept", actor=staff,
                          body={"version": result["version"]}, label=f"{label}-accept")
        if result["status"] == "ACCEPTED":
            result = data("POST", f"/staff/work-orders/{order_id}/start", actor=staff,
                          body={"version": result["version"]}, label=f"{label}-start")
        assert result["status"] == "PROCESSING"
        return result

    def receipt(order_id, staff, label, file_id=None):
        order = detail(order_id)["work_order"]
        if order["status"] == "WAIT_CONFIRM":
            return order
        return data("POST", f"/staff/work-orders/{order_id}/receipts", actor=staff, label=label, body={
            "version": order["version"], "problem_type": "ELECTRICAL", "method": f"{run_id}-{label}",
            "materials": "synthetic insulation", "fee_flag": "NONE", "resolved": True,
            "follow_up": "resident to verify", "after_file_ids": [] if file_id is None else [file_id],
        })

    def create_processing(label):
        created = step(f"{label}-create", lambda: data("POST", "/work-orders", label=f"{label}-create",
            body={"house_id": ctx["house_id"], "text": f"{run_id}-{label}: 灯具线路接触不良"},
            headers={"Idempotency-Key": f"{run_id}-{label}"}))
        order_id = int(created["id"])
        if label not in state["objects"]:
            _result, staff = assigned(order_id)
            state["objects"][label] = {"id": order_id, "staff": staff}
            save()
        staff = state["objects"][label]["staff"]
        step(f"{label}-processing", lambda: to_processing(order_id, staff, label))
        return order_id, staff

    def finish(order_id, staff, label):
        order = detail(order_id)["work_order"]
        if order["status"] == "PROCESSING":
            order = receipt(order_id, staff, f"{label}-final-receipt")
        if order["status"] == "WAIT_CONFIRM":
            order = data("POST", f"/work-orders/{order_id}/confirm", label=f"{label}-confirm", body={"version":order["version"]})
        assert order["status"] == "CLOSED"
        return detail(order_id)

    def race(order_id, actions):
        before = detail(order_id)
        version = before["work_order"]["version"]
        barrier = Barrier(2)
        def one(index, action):
            body = {"version": version}
            if action == "reopen": body["reason"] = f"{run_id}-race-{index} still failing"
            barrier.wait(timeout=10)
            response = httpx.post(f"http://127.0.0.1:8083/api/v1/work-orders/{order_id}/{action}",
                headers={"Authorization": f"Bearer {tokens['resident']}", "X-Request-Id": f"{run_id}-race-{order_id}-{index}"},
                json=body, timeout=30)
            payload = response.json()
            return {"action":action, "status":response.status_code, "error_code":HELPERS["_error_code"](payload)}
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(one, i, action) for i, action in enumerate(actions)]
            results = [future.result() for future in futures]
        assert sorted(row["status"] for row in results) == [200,409]
        after = detail(order_id)
        assert len(after["feedbacks"]) == len(before["feedbacks"]) + 1
        delta = len(after["processes"]) - len(before["processes"])
        assert delta == (1 if after["work_order"]["status"] == "PROCESSING" else 0)
        return {"parallel_start":"two submitted futures + barrier", "responses":results, "before":before, "after":after}

    try:
        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20260919_0009"
        order_id, staff = create_processing("redispatch")
        def upload():
            def chunk(kind, data):
                return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
            content = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB",8,8,8,2,0,0,0))
            content += chunk(b"IDAT", zlib.compress((b"\x00" + bytes((40,100,180))*8)*8)) + chunk(b"IEND", b"")
            metadata=data("POST", "/files", actor=staff, label="media-register", body={
                "tenant_id":ctx["tenant_id"], "property_id":ctx["property_id"], "mime_type":"image/png",
                "size_bytes":len(content), "original_name":"wo014-synthetic.png",
            })
            response=client.http.post(f"/files/{metadata['id']}/content", headers={"Authorization":f"Bearer {tokens[staff]}"}, files={"file":("wo014-synthetic.png",content,"image/png")})
            assert response.status_code==200
            return {"file_id":metadata["id"], "sha256":hashlib.sha256(content).hexdigest(), "bytes":len(content)}
        media=step("media-upload", upload)
        original_clock=step("clock-before", lambda: clock(order_id))
        # Advance only the scheduler's explicit test clock; never alter DB dates.
        warning_at=datetime.fromisoformat(str(original_clock["warning_at"])) if isinstance(original_clock["warning_at"],str) else original_clock["warning_at"]
        if warning_at.tzinfo is None:
            warning_at = warning_at.replace(tzinfo=UTC)
        step("scheduler-warning", lambda: scheduler(order_id, warning_at))
        receipted=step("redispatch-receipt", lambda: receipt(order_id,staff,"redispatch-first-receipt",media["file_id"]))
        before=step("redispatch-before",lambda:detail(order_id))
        assert before["work_order"]["status"]=="WAIT_CONFIRM"
        for actor in ("other","manager"):
            step(f"nonowner-{actor}", lambda actor=actor: call("POST",f"/work-orders/{order_id}/reopen",actor=actor,
                label=f"nonowner-{actor}",body={"reason":"not owner","version":receipted["version"]},expected=(403,)))
        for label,body,expected in (
            ("empty-reason",{"reason":"  ","version":receipted["version"]},(422,)),
            ("long-reason",{"reason":"a"*256,"version":receipted["version"]},(422,)),
            ("stale-version",{"reason":"still failing","version":1},(409,)),
        ):
            step(label,lambda label=label,body=body,expected=expected:call("POST",f"/work-orders/{order_id}/reopen",label=label,body=body,expected=expected))
        unchanged=step("negative-feedback-count",lambda:len(detail(order_id)["feedbacks"]))
        assert unchanged==len(before["feedbacks"])
        step("original-off-duty",lambda:data("POST","/staff/attendance/checkout",actor=staff,label="original-checkout",
            body={"tenant_id":ctx["tenant_id"],"property_id":ctx["property_id"]}))
        step("redispatch-reopen",lambda:data("POST",f"/work-orders/{order_id}/reopen",label="redispatch-reopen",
            body={"reason":f"{run_id} original employee off duty; issue persists","version":receipted["version"]}))
        def after_redispatch():
            result,new_staff=assigned(order_id)
            assert new_staff != staff
            assert result["assignments"][0]["status"]=="ENDED"
            assert result["receipts"]==before["receipts"] and result["media"]==before["media"]
            assert len(result["processes"])==1
            return {"detail":result,"staff":new_staff,"clock":clock(order_id)}
        reassigned=step("redispatch-after",after_redispatch)
        assert reassigned["clock"]["state"]=="WARNING"
        assert str(reassigned["clock"]["complete_due_at"])==str(original_clock["complete_due_at"])
        repeat=step("scheduler-repeat-warning",lambda:scheduler(order_id,warning_at))
        assert repeat["warnings"]==repeat["notifications"]==0
        new_staff=reassigned["staff"]
        step("redispatch-second-process",lambda:to_processing(order_id,new_staff,"redispatch-second"))
        final=step("redispatch-final",lambda:finish(order_id,new_staff,"redispatch"))
        assert [p["cycle_no"] for p in final["processes"]]==[1,2]
        assert final["receipts"][0]==before["receipts"][0]
        downloaded=client.http.get(f"/files/{media['file_id']}/content",headers={"Authorization":f"Bearer {tokens['resident']}"})
        assert downloaded.status_code==200 and hashlib.sha256(downloaded.content).hexdigest()==media["sha256"]
        step("original-on-duty-restored",lambda:data("POST","/staff/attendance/checkin",actor=staff,label="original-checkin-restored",
            body={"tenant_id":ctx["tenant_id"],"property_id":ctx["property_id"]}))
        step("closed-reopen-rejected",lambda:call("POST",f"/work-orders/{order_id}/reopen",label="closed-reopen-rejected",
            body={"reason":"late reopen","version":final["work_order"]["version"]},expected=(409,)))
        for label,actions in (("double-reopen",("reopen","reopen")),("confirm-reopen",("confirm","reopen"))):
            race_id,race_staff=create_processing(label)
            step(f"{label}-receipt",lambda:receipt(race_id,race_staff,f"{label}-receipt"))
            step(f"{label}-race",lambda:race(race_id,actions))
            step(f"{label}-closed",lambda:finish(race_id,race_staff,label))
        state["status"]="API_FLOW_PASS_PENDING_INDEPENDENT_REVIEW"
        state["completed_at_utc"]=datetime.now(UTC).isoformat()
        save()
        print(json.dumps({"run_id":run_id,"status":state["status"],"objects":state["objects"]}))
    finally:
        save(); client.close(); engine.dispose()


if __name__ == "__main__":
    main()
