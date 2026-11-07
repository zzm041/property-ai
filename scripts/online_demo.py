"""Bounded browser adapter for an isolated synthetic property-ai environment.

No model, database, subprocess or network activity occurs on import. This module
is not a public-network launcher or authorization to start an inference window.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hmac
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import secrets
import time
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse


ASSETS = Path(__file__).resolve().parents[1] / "deploy/online-demo/web"
SCENARIOS = {
    "light": {"title": "房间照明", "text": "房间顶灯打开后不亮，请安排维修。"},
    "tap": {"title": "水龙头漏水", "text": "厨房水龙头关闭后仍持续滴水，请安排维修。"},
    "door": {"title": "公共门故障", "text": "单元公共门的合页松动，门关不上，请安排维修。"},
}
MESSAGES = {
    "CLOSED": "本轮体验已结束，请等待下次开放。",
    "AUTH": "请先输入体验口令。", "ORIGIN": "请从本体验页面操作。",
    "INPUT": "请选择页面提供的报修场景。", "BODY_LIMIT": "提交内容过大。",
    "LIMIT": "本轮体验额度已用完。", "POLL_LIMIT": "请稍后刷新。",
    "UNKNOWN": "提交结果待核实，本轮不重复提交，请联系演示负责人。",
    "UPSTREAM": "业务服务暂时无法确认结果，请稍后查看。",
    "NO_ORDER": "请先选择场景并提交报修。",
}


class GatewayError(Exception):
    def __init__(self, code: str, status: int):
        self.code, self.status = code, status


@dataclass
class Session:
    attempted: bool = False
    order_id: int | None = None
    last_poll: float = 0


def validate_config(source: dict) -> dict:
    expected = {"window_id", "expires_at", "origin", "backend_url", "resident_token_file",
                "operator_token_file", "invite_file", "ledger_file", "tenant_id", "property_id",
                "house_id", "reporter_user_id"}
    if set(source) != expected:
        raise ValueError("CONFIG_FIELDS")
    cfg = dict(source)
    if not isinstance(cfg["window_id"], str) or not re.fullmatch(r"[a-z][a-z0-9-]{2,39}", cfg["window_id"]):
        raise ValueError("WINDOW_ID")
    expiry = cfg["expires_at"]
    if type(expiry) not in (int, float) or not math.isfinite(expiry) or not 0 < expiry - time.time() <= 1800:
        raise ValueError("WINDOW_EXPIRY")
    for key in ("tenant_id", "property_id", "house_id", "reporter_user_id"):
        if type(cfg[key]) is not int or cfg[key] <= 0:
            raise ValueError("SYNTHETIC_ID")
    for key in ("origin", "backend_url"):
        url = urlsplit(cfg[key])
        if url.username or url.password or url.query or url.fragment or url.path or not url.hostname:
            raise ValueError("URL_SHAPE")
        if key == "backend_url":
            ip = ipaddress.ip_address(url.hostname)
            if url.scheme != "http" or not (ip.is_private or ip.is_loopback) or ip.is_unspecified or ip.is_link_local or ip.is_multicast:
                raise ValueError("BACKEND_PRIVATE_ONLY")
            if url.port in (8080, 8093):
                raise ValueError("PROTECTED_LOCAL_SERVICE")
        elif url.scheme != "https" and not (url.scheme == "http" and url.hostname == "127.0.0.1"):
            raise ValueError("ORIGIN_HTTPS")
    for key in ("resident_token_file", "operator_token_file", "invite_file", "ledger_file"):
        if not Path(cfg[key]).is_absolute():
            raise ValueError("ABSOLUTE_PRIVATE_PATH_REQUIRED")
    return cfg


def read_secret(path: str, *, invite: bool = False) -> str:
    limit = 128 if invite else 8192
    with Path(path).open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("SECRET_SIZE")
    value = raw.decode("ascii").strip()
    if len(value) < 32 or not re.fullmatch(r"[A-Za-z0-9._~-]+", value):
        raise ValueError("SECRET_FORMAT")
    return value


class Runtime:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.expires_at = cfg["expires_at"]
        self.sessions: dict[str, Session] = {}
        self.login_attempts = 0
        self.reserved = False
        self.ready = False
        self.lock = asyncio.Lock()
        self.ledger = None

    def active(self) -> bool:
        return self.ready and time.time() < self.expires_at

    def require_open(self):
        if not self.active():
            raise GatewayError("CLOSED", 410)

    def record(self, event: str, **fields):
        self.ledger.write(json.dumps({"event": event, "at": datetime.now(timezone.utc).isoformat(), **fields}) + "\n")
        self.ledger.flush()
        os.fsync(self.ledger.fileno())

    def session(self, request: Request) -> Session:
        self.require_open()
        session = self.sessions.get(request.cookies.get("property_demo", ""))
        if session is None:
            raise GatewayError("AUTH", 401)
        return session

    def scope(self, order: dict):
        for key in ("tenant_id", "property_id", "house_id", "reporter_user_id"):
            if type(order.get(key)) is not int or order[key] != self.cfg[key]:
                raise GatewayError("UPSTREAM", 502)
        if type(order.get("id")) is not int or order["id"] <= 0:
            raise GatewayError("UPSTREAM", 502)

    async def upstream(self, method: str, path: str, *, operator=False, body=None, key=None):
        headers = {"Authorization": "Bearer " + (self.operator if operator else self.resident)}
        if key is not None:
            headers["Idempotency-Key"] = key
        try:
            async with asyncio.timeout(12):
                async with self.http.stream(method, self.cfg["backend_url"] + path, headers=headers, json=body) as response:
                    if response.status_code != 200:
                        raise GatewayError("UPSTREAM", 502)
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > 1024 * 1024:
                            raise GatewayError("UPSTREAM", 502)
                        chunks.append(chunk)
                    data = json.loads(b"".join(chunks))["data"]
                    if not isinstance(data, dict):
                        raise GatewayError("UPSTREAM", 502)
                    return data
        except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError) as error:
            raise GatewayError("UPSTREAM", 502) from error


async def read_body(request: Request) -> dict:
    raw = bytearray()
    try:
        async with asyncio.timeout(5):
            async for chunk in request.stream():
                if len(raw) + len(chunk) > 4096:
                    raise GatewayError("BODY_LIMIT", 413)
                raw.extend(chunk)
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise ValueError()
        return body
    except (ValueError, TimeoutError) as error:
        raise GatewayError("INPUT", 400) from error


def project_detail(data: dict, runtime: Runtime, session: Session) -> dict:
    try:
        order = data["work_order"]
        runtime.scope(order)
        if order["id"] != session.order_id:
            raise GatewayError("UPSTREAM", 502)
        analysis = data["display"].get("analysis_status")
        current = next((a for a in data["assignments"] if isinstance(a, dict)
                        and type(a.get("id")) is int and a["id"] == order.get("current_assignment_id")
                        and a.get("status") == "ACTIVE"), None)
        auto = (analysis == "COMPLETED" and current is not None
                and current.get("reason") == "AUTO_DISPATCH"
                and order.get("status") in ("ASSIGNED", "ACCEPTED", "PROCESSING"))
        created = datetime.fromisoformat(order["created_at"].replace("Z", "+00:00"))
        if created.tzinfo is None:
            raise ValueError()
        labels = {}
        for key in ("status", "category", "severity", "priority"):
            value = order.get(key)
            if not isinstance(value, str) or not re.fullmatch(r"[A-Z_]{1,48}", value):
                raise ValueError()
            labels[key] = value
        if analysis not in (None, "PROCESSING", "COMPLETED", "FAILED", "LOW_CONFIDENCE", "STALE"):
            analysis = "UNKNOWN"
        if order["status"] in ("ACCEPTED", "PROCESSING", "WAIT_CONFIRM", "COMPLETED", "CANCELLED", "CLOSED"):
            phase = order["status"]
        elif order["status"] == "ASSIGNED" and current is not None:
            phase = "AUTO_ASSIGNED" if auto else "MANUAL_ASSIGNED"
        elif analysis in ("LOW_CONFIDENCE", "STALE", "UNKNOWN"):
            phase = "REVIEW"
        elif analysis == "FAILED":
            phase = "FAILED"
        elif analysis == "COMPLETED":
            phase = "UNASSIGNED"
        else:
            phase = "ANALYZING"
        return {**labels, "analysis_status": analysis, "auto_assigned": bool(auto), "phase": phase,
                "created_at": created.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")}
    except (KeyError, ValueError, TypeError, AttributeError) as error:
        raise GatewayError("UPSTREAM", 502) from error


def create_app(config: dict, *, transport=None) -> FastAPI:
    cfg = validate_config(config)
    runtime = Runtime(cfg)

    @asynccontextmanager
    async def lifespan(app):
        runtime.resident = read_secret(cfg["resident_token_file"])
        runtime.operator = read_secret(cfg["operator_token_file"])
        runtime.invite = read_secret(cfg["invite_file"], invite=True)
        async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False, transport=transport) as client:
            runtime.http = client
            ops = await runtime.upstream("GET", "/api/v1/opsz", operator=True)
            window = cfg["window_id"]
            expected = {"app_env": "staging", "celery_queue": "property_ai.online_demo." + window,
                        "celery_dlq": "property_ai.online_demo." + window + ".dlq",
                        "milvus_collection": "online_demo_" + window.replace("-", "_"),
                        "storage_bucket": "online-demo-" + window,
                        "storage_provider": "production", "llm_provider": "production"}
            if any(ops.get(k) != v for k, v in expected.items()):
                raise RuntimeError("UPSTREAM_SCOPE")
            runtime.operator = ""  # Not retained for visitor requests.
            with Path(cfg["ledger_file"]).open("x", encoding="utf-8") as ledger:
                runtime.ledger = ledger
                runtime.record("opened", window=window, expires_at=runtime.expires_at)
                runtime.ready = True
                try:
                    yield
                finally:
                    runtime.ready = False
                    runtime.record("closed")
                    runtime.resident = runtime.invite = ""
                    runtime.sessions.clear()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.runtime = runtime

    @app.exception_handler(GatewayError)
    async def gateway_error(request, exc):
        return JSONResponse({"error": {"code": exc.code, "message": MESSAGES[exc.code]}}, status_code=exc.status)

    @app.middleware("http")
    async def headers(request, call_next):
        if request.method == "POST" and request.headers.get("origin") != cfg["origin"]:
            response = JSONResponse({"error": {"code": "ORIGIN", "message": MESSAGES["ORIGIN"]}}, status_code=403)
        else:
            response = await call_next(request)
        response.headers.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
            "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
        return response

    @app.get("/")
    async def page():
        return FileResponse(ASSETS / "index.html")

    @app.get("/assets/app.js")
    async def script():
        return FileResponse(ASSETS / "app.js", media_type="text/javascript")

    @app.get("/assets/style.css")
    async def stylesheet():
        return FileResponse(ASSETS / "style.css", media_type="text/css")

    @app.get("/status")
    async def status(request: Request):
        session = runtime.sessions.get(request.cookies.get("property_demo", ""))
        return {"open": runtime.active(), "expires_at": runtime.expires_at, "available": not runtime.reserved,
                "authenticated": session is not None,
                "attempted": bool(session and session.attempted),
                "submitted": bool(session and session.order_id), "scenarios": SCENARIOS}

    @app.post("/session")
    async def start_session(request: Request):
        runtime.require_open()
        runtime.login_attempts += 1
        if runtime.login_attempts > 50 or len(runtime.sessions) >= 32:
            raise GatewayError("LIMIT", 429)
        body = await read_body(request)
        code = body.get("code")
        if set(body) != {"code"} or not isinstance(code, str) or not hmac.compare_digest(code.encode(), runtime.invite.encode()):
            raise GatewayError("AUTH", 401)
        existing = request.cookies.get("property_demo", "")
        token = existing if existing in runtime.sessions else secrets.token_urlsafe(32)
        runtime.sessions.setdefault(token, Session())
        response = JSONResponse({"authenticated": True})
        response.set_cookie("property_demo", token, httponly=True, secure=cfg["origin"].startswith("https:"),
                            samesite="strict", max_age=max(0, int(runtime.expires_at - time.time())))
        return response

    @app.post("/order")
    async def create_order(request: Request):
        session = runtime.session(request)
        body = await read_body(request)
        scenario = body.get("scenario")
        if set(body) != {"scenario"} or not isinstance(scenario, str) or scenario not in SCENARIOS:
            raise GatewayError("INPUT", 400)
        async with runtime.lock:
            runtime.require_open()
            if session.order_id is not None:
                return {"submitted": True}
            if session.attempted:
                raise GatewayError("UNKNOWN", 409)
            if runtime.reserved:
                raise GatewayError("LIMIT", 429)
            # Reserve before I/O. Failures and cancellations never restore quota.
            runtime.reserved = session.attempted = True
            key = secrets.token_hex(24)
            runtime.record("reserved", scenario=scenario, idempotency_key=key)
        try:
            order = await runtime.upstream("POST", "/api/v1/work-orders", body={
                "house_id": cfg["house_id"], "contact_phone": "13800000000",
                "text": SCENARIOS[scenario]["text"], "file_ids": []}, key=key)
            runtime.scope(order)
            session.order_id = order["id"]
            runtime.record("created", order_id=order["id"])
        except GatewayError:
            runtime.record("uncertain")
            raise
        return {"submitted": True}

    @app.get("/order")
    async def current_order(request: Request):
        session = runtime.session(request)
        if session.order_id is None:
            raise GatewayError("NO_ORDER", 404)
        now = time.monotonic()
        if now - session.last_poll < 3:
            raise GatewayError("POLL_LIMIT", 429)
        session.last_poll = now
        data = await runtime.upstream("GET", f"/api/v1/work-orders/{session.order_id}")
        return project_detail(data, runtime, session)

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18094)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535 or args.port in (8080, 8093):
        parser.error("Use a dedicated unprivileged port")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    import uvicorn
    uvicorn.run(create_app(config), host="127.0.0.1", port=args.port, workers=1, access_log=False,
                proxy_headers=False, limit_concurrency=32, timeout_keep_alive=5)


if __name__ == "__main__":
    main()
