"""D9: the prune command can only delete noise, never business history.

`--event-code CREATE` used to be accepted by the allow-list-less parser: the
dry run in t115 proved it would have deleted 22 real business rows. The list is
now closed (``PRUNABLE_EVENT_CODES``) and a refused call exits 2 *before* an
engine is opened, so the guarantee is "no connection, no delete", not "we
filtered afterwards".
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.service.log_retention import (
    DEFAULT_EVENT_CODES,
    PRUNABLE_EVENT_CODES,
    WORK_ORDER_LOGS,
    plan_prune,
)

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "backend" / "scripts" / "ops" / "prune_work_order_logs.py"


def load_cli():
    spec = importlib.util.spec_from_file_location("prune_work_order_logs_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def cli():
    return load_cli()


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    WORK_ORDER_LOGS.create(engine)
    connection = engine.connect()
    try:
        yield Session(bind=connection, autoflush=False, expire_on_commit=False), connection
    finally:
        connection.close()
        engine.dispose()


def seed(session, *, event_code: str, days_ago: int, work_order_id: int = 1, row_id: int) -> None:
    # SQLite only auto-increments INTEGER primary keys, so the fixture hands out
    # explicit ids; MySQL does it itself.
    session.execute(
        WORK_ORDER_LOGS.insert().values(
            id=row_id,
            work_order_id=work_order_id,
            event_code=event_code,
            request_id=f"{event_code}-{days_ago}",
            created_at=datetime.now(UTC) - timedelta(days=days_ago),
        )
    )
    session.commit()


def test_the_allow_list_holds_only_noise():
    assert PRUNABLE_EVENT_CODES == ("AUTO_DISPATCH_WAIT",)
    assert DEFAULT_EVENT_CODES == PRUNABLE_EVENT_CODES


@pytest.mark.parametrize("code", ["CREATE", "ASSIGN", "REASSIGN", "ANALYZE", "create"])
def test_the_planner_refuses_business_events(session, code):
    session_, _ = session
    with pytest.raises(ValueError) as caught:
        plan_prune(session_, days=30, event_codes=[code])
    message = str(caught.value)
    assert f"event code(s) {code} are not prunable" in message
    assert "allowed: AUTO_DISPATCH_WAIT" in message


def test_the_planner_refuses_a_mixed_request_entirely(session):
    session_, _ = session
    seed(session_, event_code="AUTO_DISPATCH_WAIT", days_ago=40, row_id=1)
    with pytest.raises(ValueError):
        plan_prune(session_, days=30, event_codes=["AUTO_DISPATCH_WAIT", "CREATE"])
    # Refused means nothing was planned, so nothing can be deleted.
    assert plan_prune(session_, days=30).expired_rows == 1


def test_the_cli_exits_two_for_create_without_opening_a_connection(cli, capsys, monkeypatch):
    opened: list[str] = []

    def explode(url):
        opened.append(url)
        raise AssertionError("a refused prune must not open a database connection")

    monkeypatch.setattr(cli, "create_database_engine", explode)

    code = cli.main(["--event-code", "CREATE", "--database-url", "mysql+pymysql://x/y"])

    assert code == 2
    assert opened == []
    stderr = capsys.readouterr().err
    assert "refused: event code(s) CREATE are not prunable" in stderr
    assert "allowed: AUTO_DISPATCH_WAIT" in stderr


def test_the_cli_refuses_even_when_only_one_of_several_codes_is_business(cli, monkeypatch):
    monkeypatch.setattr(
        cli, "create_database_engine", lambda url: pytest.fail("no connection expected")
    )
    code = cli.main(["--event-code", "AUTO_DISPATCH_WAIT", "--event-code", "CREATE"])
    assert code == 2


def test_the_cli_dry_runs_the_allowed_code(cli, tmp_path, capsys, monkeypatch):
    database = tmp_path / "logs.sqlite"
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    WORK_ORDER_LOGS.create(engine)
    with engine.begin() as connection:
        connection.execute(
            WORK_ORDER_LOGS.insert(),
            [
                {
                    "id": 1,
                    "work_order_id": 7,
                    "event_code": "AUTO_DISPATCH_WAIT",
                    "request_id": "old",
                    "created_at": datetime.now(UTC) - timedelta(days=45),
                },
                {
                    "id": 2,
                    "work_order_id": 7,
                    "event_code": "AUTO_DISPATCH_WAIT",
                    "request_id": "fresh",
                    "created_at": datetime.now(UTC) - timedelta(days=1),
                },
                {
                    "id": 3,
                    "work_order_id": 7,
                    "event_code": "CREATE",
                    "request_id": "business",
                    "created_at": datetime.now(UTC) - timedelta(days=45),
                },
            ],
        )
    engine.dispose()

    monkeypatch.setattr(cli, "create_database_engine", lambda url: create_engine(url))

    code = cli.main(
        [
            "--event-code",
            "AUTO_DISPATCH_WAIT",
            "--database-url",
            f"sqlite:///{database.as_posix()}",
        ]
    )

    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["mode"] == "dry-run"
    assert report["by_event_code"] == {"AUTO_DISPATCH_WAIT": 1}
    assert "deleted_rows" not in report

    # Nothing was removed - and the 45-day-old business row was never a candidate.
    with create_engine(f"sqlite:///{database.as_posix()}").connect() as connection:
        rows = connection.execute(
            WORK_ORDER_LOGS.select().order_by(WORK_ORDER_LOGS.c.id)
        ).fetchall()
    assert [row.event_code for row in rows] == [
        "AUTO_DISPATCH_WAIT",
        "AUTO_DISPATCH_WAIT",
        "CREATE",
    ]


def test_the_cli_still_rejects_a_zero_day_window(cli, capsys):
    assert cli.main(["--days", "0"]) == 2
    assert "--days must be at least 1" in capsys.readouterr().err


def test_the_cli_docstring_is_honest_about_refusing():
    text = io.open(SCRIPT, encoding="utf-8").read()
    assert "PRUNABLE_EVENT_CODES" in text
    assert "exits 2 before opening a" in text
