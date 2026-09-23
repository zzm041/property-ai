"""Read-only persisted evidence for the newly created 014 objects only."""
from datetime import UTC, datetime
from pathlib import Path
import json
import runpy
import subprocess

from sqlalchemy import create_engine, text

REPO = Path(__file__).resolve().parents[2]
OUT = Path(__file__).parent
helpers = runpy.run_path(str(REPO / "reports/notification-events-inbox-013/run-runtime.py"))
settings = helpers["_guard"]()
page = json.loads((OUT / "page-evidence.json").read_text(encoding="utf-8"))
api = json.loads((OUT / "api-evidence.json").read_text(encoding="utf-8"))
assert page["run_id"] == api["run_id"]
ids = [page["steps"]["08-closed-relogin"]["work_order"]["id"]] + [item["id"] for item in api["objects"].values()]
engine = create_engine(settings.test_database_url)
result = {
    "task_id": "WO-REOPEN-RECEIPT-HISTORY-014", "run_id": page["run_id"], "code_sha":page["code_sha"],
    "collected_at_utc":datetime.now(UTC).isoformat(), "mode":"read-only SQL, unique vhost queue metadata only",
    "database":"127.0.0.1:3307/property_ai_auth001_test", "objects":{},
}
queries = {
    "work_order":"SELECT id,tenant_id,property_id,house_id,reporter_user_id,status,version,current_assignment_id,resolved_at,closed_at FROM work_orders WHERE id=:id",
    "processes":"SELECT id,cycle_no,assignee_id,started_at,ended_at FROM work_order_processes WHERE work_order_id=:id ORDER BY cycle_no",
    "receipts":"SELECT id,process_id,problem_type,method,materials,fee_flag,fee_amount,resolved,follow_up,finished_at FROM work_order_receipts WHERE work_order_id=:id ORDER BY id",
    "feedbacks":"SELECT id,process_id,actor_user_id,confirmed,unresolved_flag,comment,created_at FROM work_order_feedbacks WHERE work_order_id=:id ORDER BY id",
    "assignments":"SELECT id,employee_id,assigned_by_user_id,status,reason,assigned_at,accept_at,ended_at FROM work_order_assignments WHERE work_order_id=:id ORDER BY id",
    "logs":"SELECT id,actor_user_id,actor_type,event_code,from_status,to_status,payload_json,created_at FROM work_order_logs WHERE work_order_id=:id ORDER BY id",
    "media":"SELECT id,file_id,kind,taken_phase,uploader_user_id,created_at FROM work_order_media WHERE work_order_id=:id ORDER BY id",
    "sla":"SELECT state,warning_at,complete_due_at FROM sla_clocks WHERE work_order_id=:id",
    "audit":"SELECT id,actor_user_id,action,resource_type,resource_id,tenant_id,property_id,created_at FROM audit_logs WHERE resource_type='WORK_ORDER' AND resource_id=:id ORDER BY id",
}
try:
    with engine.connect() as connection:
        connection.execute(text("SET TRANSACTION READ ONLY"))
        result["revision"]=connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert result["revision"]=="20260919_0009"
        for order_id in ids:
            rows={key:[dict(row) for row in connection.execute(text(query),{"id":order_id}).mappings()] for key,query in queries.items()}
            assert rows["work_order"][0]["status"]=="CLOSED"
            assert rows["sla"][0]["state"]=="STOPPED"
            assert [p["cycle_no"] for p in rows["processes"]]==list(range(1,len(rows["processes"])+1))
            assert len({r["process_id"] for r in rows["receipts"]})==len(rows["receipts"])
            assert len([log for log in rows["logs"] if log["event_code"]=="REOPEN"])<=1
            result["objects"][str(order_id)]=rows
        result["new_scope_employee_daily_statuses"]=[dict(row) for row in connection.execute(text(
            "SELECT employee_id,property_id,business_date,on_duty,current_load FROM employee_daily_statuses WHERE property_id=:property_id ORDER BY employee_id,business_date"
        ),{"property_id":page["steps"]["08-closed-relogin"]["work_order"]["property_id"]}).mappings()]
finally:
    engine.dispose()
queue=subprocess.run([
    "docker","exec","property-ai-rabbitmq-1","rabbitmqctl","list_queues","-p","property_ai_auth001",
    "name","messages_ready","messages_unacknowledged","consumers",
],capture_output=True,text=True,timeout=30)
assert queue.returncode==0
result["queue"]={"vhost":"property_ai_auth001","command":"rabbitmqctl list_queues -p property_ai_auth001 name messages_ready messages_unacknowledged consumers",
                 "actual_output":queue.stdout,"message_content_read":False,"shared_vhost_accessed":False}
(OUT / "database-queue-evidence.json").write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
print(json.dumps({"run_id":result["run_id"],"revision":result["revision"],"order_ids":ids,"status":"PERSISTED_EVIDENCE_VERIFIED"}))
