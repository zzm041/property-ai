"""Labeled Day 4 perf retest. Prints metrics only; does not print tokens."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import create_engine, text

_SPEC = importlib.util.spec_from_file_location(
    "day4_perf_probe",
    Path(__file__).with_name("perf_probe.py"),
)
assert _SPEC is not None and _SPEC.loader is not None
_probe = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_probe)
run_probe = _probe.run_probe

BASE = os.environ.get("DAY4_BASE_URL", "http://127.0.0.1:8082").rstrip("/")
DB = os.environ.get("TEST_DATABASE_URL", "")


def _bound_identity() -> tuple[int, int]:
    engine = create_engine(DB)
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT user_id, house_id FROM resident_house_relations "
                "WHERE status='ACTIVE' AND valid_to IS NULL ORDER BY id DESC LIMIT 1"
            )
        ).first()
    engine.dispose()
    if row is None:
        raise SystemExit("no ACTIVE house relation")
    return int(row[0]), int(row[1])


def _token(user_id: int) -> str:
    with httpx.Client(timeout=20.0) as client:
        response = client.post(
            f"{BASE}/api/v1/auth/dev/token",
            json={"user_id": user_id},
            headers={"X-Request-Id": f"d4-perf-login-{uuid4().hex[:8]}"},
        )
    if response.status_code != 200:
        raise SystemExit(f"token status={response.status_code}")
    token = str((response.json().get("data") or {}).get("access_token") or "")
    if not token:
        raise SystemExit("token missing")
    return token


def main() -> int:
    if not DB:
        print("DAY4_PERF=NOT_RUN missing TEST_DATABASE_URL")
        return 2
    user_id, house_id = _bound_identity()
    token = _token(user_id)
    jobs = [
        ("healthz_cold_c1_n30_OLD_METHOD", "/healthz", "GET", 1, 30, "cold", None, 0),
        ("healthz_cold_c20_n100_OLD_METHOD", "/healthz", "GET", 20, 100, "cold", None, 0),
        ("healthz_pooled_c1_n30_NEW_METHOD", "/healthz", "GET", 1, 30, "pooled", None, 0),
        ("healthz_pooled_c20_n100_NEW_METHOD", "/healthz", "GET", 20, 100, "pooled", None, 0),
        ("wo_post_cold_c1_n30", "/api/v1/work-orders", "POST", 1, 30, "cold", token, house_id),
        ("wo_post_cold_c5_n20", "/api/v1/work-orders", "POST", 5, 20, "cold", token, house_id),
        ("wo_post_pooled_c1_n30_NEW_METHOD", "/api/v1/work-orders", "POST", 1, 30, "pooled", token, house_id),
        ("wo_post_pooled_c5_n20_NEW_METHOD", "/api/v1/work-orders", "POST", 5, 20, "pooled", token, house_id),
    ]
    print(f"perf_identity user_present=yes house_id={house_id} (token redacted)")
    failed = False
    for name, path, method, concurrency, requests, mode, auth, hid in jobs:
        body = None
        if method == "POST":
            body = {"house_id": hid, "text": "Perf probe work order", "category": "ELECTRICAL"}
        result = run_probe(
            base_url=BASE,
            path=path,
            method=method,
            concurrency=concurrency,
            requests=requests,
            mode=mode,
            token=auth,
            body=body,
        )
        result["label"] = name
        print(result)
        if int(result["errors"]) > 0:
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
