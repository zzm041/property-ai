"""PA-199 (t199): row-lock serialisation and pool sizing on the create path.

The 20-way burst used to answer 8-14 HTTP 500s because every report by the same
resident took `users(id) for update` before its first read, so the tail of the
burst waited on that row longer than the driver's 5 s read timeout, which drops
the socket (`OperationalError 2013`) and surfaces as SYS.INTERNAL_ERROR.
"""

from __future__ import annotations

import ast
import pathlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import TimeoutError as SQLAlchemyPoolTimeoutError

from app.core import database as database_module
from app.core.config import Settings
from app.core.errors import ErrorCode
from app.core.exception_handlers import install_exception_handlers

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
LIFECYCLE = REPO_ROOT / "backend" / "app" / "service" / "work_order" / "lifecycle.py"


def test_read_timeout_is_not_the_connect_timeout(monkeypatch) -> None:
    captured: dict[str, object] = {}
    sentinel = object()

    def fake_create_engine(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(database_module, "create_engine", fake_create_engine)
    monkeypatch.setattr(database_module, "event", type("E", (), {"listen": staticmethod(lambda *a, **k: None)}))
    # `_engines` is process-wide and `dispose_database_engines()` closes every
    # engine in it during application shutdown. A fake engine left in there
    # survives this test and makes an unrelated TestClient teardown call
    # `.dispose()` on a bare object, so register into a throwaway set instead.
    registry: set[object] = set()
    monkeypatch.setattr(database_module, "_engines", registry)

    database_module.create_database_engine(
        "mysql+pymysql://u:p@127.0.0.1:3306/db",
        connect_timeout=5,
        read_timeout=60,
    )

    assert registry == {sentinel}
    connect_args = captured["connect_args"]
    assert connect_args["connect_timeout"] == 5
    assert connect_args["read_timeout"] == 60
    assert connect_args["write_timeout"] == 60


def test_settings_default_read_timeout_outlives_the_innodb_lock_wait() -> None:
    settings = Settings(_env_file=None, app_env="test")
    # MySQL's own `innodb_lock_wait_timeout` is 50 s; the socket must outlive it,
    # otherwise a lock wait is reported as a dropped connection (2013).
    assert settings.db_read_timeout_seconds >= 50
    # A 40-way burst needs one connection per in-flight request.
    assert settings.db_pool_size + settings.db_max_overflow >= 40


def test_pool_exhaustion_is_reported_as_retryable_overload() -> None:
    app = FastAPI()
    install_exception_handlers(app)

    @app.get("/boom")
    def boom() -> None:
        raise SQLAlchemyPoolTimeoutError("QueuePool limit of size 20 overflow 20 reached")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/boom")

    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == ErrorCode.OVERLOADED
    assert body["error"]["details"]["reason"] == "DB_POOL_EXHAUSTED"
    assert response.headers["Retry-After"] == "2"


def _create_body() -> ast.FunctionDef:
    tree = ast.parse(LIFECYCLE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_create_work_order":
            return node
    raise AssertionError("_create_work_order not found")


def _call_lineno(node: ast.FunctionDef, name: str) -> int:
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
            if child.func.id == name:
                return child.lineno
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
            if child.func.attr == name:
                return child.lineno
    raise AssertionError(f"{name} not called inside _create_work_order")


def test_identity_lock_is_taken_at_the_write_not_before_the_reads() -> None:
    body = _create_body()
    lock_line = _call_lineno(body, "lock_active_identity")
    insert_line = _call_lineno(body, "add_work_order")
    duplicate_line = _call_lineno(body, "find_duplicate_content")

    # The read phase (de-duplication and the rest) must run before the hottest
    # row in the system is locked, and the lock must still precede the insert.
    assert duplicate_line < lock_line, "the identity lock is taken before the reads again"
    assert lock_line < insert_line, "the identity lock must guard the insert"


def test_repository_call_count_is_unchanged_by_the_move() -> None:
    """Guards against 'fixing' the lock by dropping one of the writes."""

    assert _call_lineno(_create_body(), "add_content") > _call_lineno(
        _create_body(), "add_work_order"
    )
    assert _call_lineno(_create_body(), "add_idempotency") > _call_lineno(
        _create_body(), "add_work_order"
    )
