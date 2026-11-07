"""Isolated HTTP contract checks; no business database, model or network."""
import asyncio
import copy
import importlib
import json
from pathlib import Path
import sys
import time

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


def module():
    assert importlib.util.find_spec("scripts.online_demo") is not None, "online gateway is not implemented"
    return importlib.import_module("scripts.online_demo")


def settings(tmp_path):
    for name in ("resident", "operator", "invite"):
        (tmp_path / name).write_text(name + "-" + "x" * 48)
    return dict(window_id="online-a", expires_at=time.time() + 900,
                origin="http://127.0.0.1:18094", backend_url="http://127.0.0.1:18095",
                resident_token_file=str(tmp_path / "resident"), operator_token_file=str(tmp_path / "operator"),
                invite_file=str(tmp_path / "invite"), ledger_file=str(tmp_path / "claim.jsonl"),
                tenant_id=10, property_id=20, house_id=30, reporter_user_id=40)


def work_order(**updates):
    return dict(id=71, tenant_id=10, property_id=20, house_id=30, reporter_user_id=40,
                status="ASSIGNED", category="ELECTRICAL", severity="GENERAL", priority="MEDIUM",
                current_assignment_id=81, created_at="2026-11-08T00:03:59.123456Z", **updates)


class Upstream:
    def __init__(self):
        self.calls = []
        self.fail_create = False
        self.wrong_scope = False
        self.ai_status = "COMPLETED"
        self.auto = True
        self.order_status = "ASSIGNED"
        self.assignment_status = "ACTIVE"
        self.ops = {"app_env": "staging", "celery_queue": "property_ai.online_demo.online-a",
                    "celery_dlq": "property_ai.online_demo.online-a.dlq", "milvus_collection": "online_demo_online_a",
                    "storage_bucket": "online-demo-online-a", "storage_provider": "production", "llm_provider": "production"}

    def __call__(self, request):
        self.calls.append(request)
        if request.url.path == "/api/v1/opsz":
            return httpx.Response(200, json={"data": self.ops})
        if request.method == "POST":
            if self.fail_create:
                raise httpx.ReadTimeout("private upstream details must not leak")
            return httpx.Response(200, json={"data": work_order()})
        order = work_order()
        order["status"] = self.order_status
        if self.wrong_scope:
            order["house_id"] = 999
        return httpx.Response(200, json={"data": {"work_order": order,
            "display": {"analysis_status": self.ai_status, "assignee_names": {"8": "Private name"}},
            "assignments": [{"id": 81, "employee_id": 8, "status": self.assignment_status,
                             "reason": "AUTO_DISPATCH" if self.auto else "MANUAL"}],
            "logs": [{"reason": "Private raw log"}], "contact_phone": "private-phone"}})


def client(tmp_path, upstream=None, **overrides):
    m = module()
    config = settings(tmp_path) | overrides
    upstream = upstream or Upstream()
    app = m.create_app(config, transport=httpx.MockTransport(upstream))
    return TestClient(app, base_url=config["origin"]), upstream, config


def login(c):
    return c.post("/session", json={"code": "invite-" + "x" * 48}, headers={"Origin": str(c.base_url).rstrip("/")})


def submit(c, **payload):
    return c.post("/order", json=payload or {"scenario": "light"}, headers={"Origin": str(c.base_url).rstrip("/")})


def test_actual_api_projection_and_duplicate_submission(tmp_path):
    c, up, cfg = client(tmp_path)
    with c:
        assert login(c).status_code == 200
        a = submit(c)
        assert a.status_code == 200
        assert a.json()["submitted"] is True
        assert submit(c).json() == a.json()
        data = c.get("/order").json()
        assert data["auto_assigned"] is True
        assert data["created_at"] == "2026-11-08 08:03"
        assert not any(word in json.dumps(data) for word in ("private", "Private", "employee_id", "reporter_user_id"))
        posts = [r for r in up.calls if r.method == "POST"]
        assert len(posts) == 1
        assert json.loads(posts[0].content) == {"house_id": 30, "contact_phone": "13800000000", "text": "房间顶灯打开后不亮，请安排维修。", "file_ids": []}
        assert posts[0].headers["Idempotency-Key"]
        assert posts[0].headers["Authorization"].startswith("Bearer resident-")
    lines = [json.loads(x) for x in Path(cfg["ledger_file"]).read_text().splitlines()]
    assert [x["event"] for x in lines] == ["opened", "reserved", "created", "closed"]


def test_uncertain_create_consumes_quota_without_retry_or_leak(tmp_path):
    up = Upstream(); up.fail_create = True
    c, up, cfg = client(tmp_path, up)
    with c:
        login(c)
        assert submit(c).status_code == 502
        r = submit(c)
        assert r.status_code == 409
        assert "private" not in r.text
        assert len([r for r in up.calls if r.method == "POST"]) == 1
        c.cookies.clear(); login(c)
        assert submit(c).status_code == 429


@pytest.mark.parametrize("change", [{"app_env": "production"}, {"llm_provider": "development"},
    {"celery_queue": "property_ai.delivery"}, {"storage_bucket": "real-data"}, {"milvus_collection": "knowledge_chunks_v1"}])
def test_startup_refuses_shared_or_development_upstream(tmp_path, change):
    up = Upstream(); up.ops.update(change)
    c, up, cfg = client(tmp_path, up)
    with pytest.raises(RuntimeError, match="UPSTREAM_SCOPE"):
        with c: pass
    assert not any(r.method == "POST" for r in up.calls)


def test_auth_origin_body_scope_and_poll_limits(tmp_path):
    c, up, cfg = client(tmp_path)
    with c:
        assert c.get("/order").status_code == 401
        assert c.post("/session", json={"code": "wrong"}).status_code == 403
        assert c.post("/session", json={"code": "wrong"}, headers={"Origin": cfg["origin"]}).status_code == 401
        login(c)
        assert c.post("/order", json={"scenario": "light"}, headers={"Origin": "https://evil.example"}).status_code == 403
        assert submit(c, scenario="light", text="arbitrary real data").status_code == 400
        assert submit(c, scenario="unknown").status_code == 400
        assert c.post("/order", content=b"x" * 4097, headers={"Origin": cfg["origin"]}).status_code == 413
        submit(c); up.wrong_scope = True
        assert c.get("/order").status_code == 502
        assert c.get("/order").status_code == 429


@pytest.mark.parametrize("ai,auto", [("FAILED", True), ("COMPLETED", False), ("PENDING", True)])
def test_never_invents_auto_assignment(tmp_path, ai, auto):
    up = Upstream(); up.ai_status = ai; up.auto = auto
    c, _, _ = client(tmp_path, up)
    with c:
        login(c); submit(c)
        assert c.get("/order").json()["auto_assigned"] is False


def test_closed_window_cannot_be_restarted(tmp_path):
    c, up, cfg = client(tmp_path)
    with c: pass
    second = TestClient(module().create_app(cfg, transport=httpx.MockTransport(up)))
    with pytest.raises(FileExistsError):
        with second: pass


def test_window_expiry_blocks_new_and_existing_sessions(tmp_path):
    c, _, cfg = client(tmp_path)
    with c:
        login(c)
        c.app.state.runtime.expires_at = time.time() - 1
        assert submit(c).status_code == 410
        assert c.get("/order").status_code == 410
        assert c.get("/status").json()["open"] is False


@pytest.mark.parametrize("changes", [{"backend_url": "https://example.org"},
    {"backend_url": "http://user:pass@127.0.0.1:8093"}, {"backend_url": "http://127.0.0.1:8093"},
    {"origin": "http://example.org"}, {"expires_at": time.time() + 7200}])
def test_config_rejects_unsafe_or_protected_target(tmp_path, changes):
    with pytest.raises(ValueError):
        client(tmp_path, **changes)


def test_static_page_and_missing_runtime_are_explicit(tmp_path):
    c, _, _ = client(tmp_path)
    with c:
        page = c.get("/")
        assert page.status_code == 200
        assert "物业报修" in page.text
        assert "Content-Security-Policy" in page.headers
        assert c.get("/assets/../online_demo.py").status_code == 404


@pytest.mark.parametrize("analysis,status,assignment,expected", [
    ("LOW_CONFIDENCE", "PENDING_ANALYSIS", "ACTIVE", "REVIEW"),
    ("STALE", "PENDING_ANALYSIS", "ACTIVE", "REVIEW"),
    ("COMPLETED", "PENDING_ASSIGNMENT", "ENDED", "UNASSIGNED"),
    ("COMPLETED", "ASSIGNED", "REASSIGNED", "UNASSIGNED"),
    ("COMPLETED", "ACCEPTED", "ACTIVE", "ACCEPTED"),
    ("COMPLETED", "PROCESSING", "ACTIVE", "PROCESSING"),
    ("COMPLETED", "CANCELLED", "ENDED", "CANCELLED"),
])
def test_real_terminal_and_assignment_states_are_distinguished(tmp_path, analysis, status, assignment, expected):
    up = Upstream(); up.ai_status = analysis; up.order_status = status; up.assignment_status = assignment
    c, _, _ = client(tmp_path, up)
    with c:
        login(c); submit(c)
        data = c.get("/order").json()
        assert data["phase"] == expected
        assert data["analysis_status"] == analysis
        if expected in ("REVIEW", "UNASSIGNED", "CANCELLED"):
            assert data["auto_assigned"] is False


def test_concurrent_visitors_can_reserve_only_one_upstream_write(tmp_path):
    async def run():
        started, release = asyncio.Event(), asyncio.Event()
        upstream = Upstream()

        async def transport(request):
            if request.method == "POST":
                started.set()
                await release.wait()
            return upstream(request)

        cfg = settings(tmp_path)
        app = module().create_app(cfg, transport=httpx.MockTransport(transport))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=cfg["origin"]) as first:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=cfg["origin"]) as second:
                    headers = {"Origin": cfg["origin"]}
                    for c in (first, second):
                        assert (await c.post("/session", json={"code": "invite-" + "x" * 48}, headers=headers)).status_code == 200
                    pending = asyncio.create_task(first.post("/order", json={"scenario": "light"}, headers=headers))
                    await asyncio.wait_for(started.wait(), 2)
                    response = await second.post("/order", json={"scenario": "tap"}, headers=headers)
                    release.set()
                    assert response.status_code == 429
                    assert (await pending).status_code == 200
                    assert len([r for r in upstream.calls if r.method == "POST"]) == 1

    asyncio.run(run())

