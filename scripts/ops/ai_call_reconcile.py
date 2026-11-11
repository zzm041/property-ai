"""PA-201 (t201): reconcile AI calls without double counting one request.

`ai_call_logs.request_id` is *not*, and must not become, a unique key. One
request legitimately writes several rows: since PA-200 a single RAG answer
leaves three of them (embed, rerank and the generate call), and a work-order
analysis leaves ASR + VLM + LLM. Adding ``uq_ai_call_logs_request_id`` would
therefore reject the normal fan-out, and "cleaning" the table to satisfy it
would mean deleting audit rows — the one thing an audit table may not do.

What that costs is a naive reconciliation: ``count(*)`` grouped by
``request_id`` counts provider *calls*, not *requests*, so anything that bills
or reports per request over-counts by the fan-out (measured on the delivery
database: 1648 calls over 635 requests, i.e. 2.6 rows per request).

So this tool makes the correct key explicit instead of adding a constraint:

* a **request** is a distinct ``request_id``;
* a **call** is a row (its primary key);
* the fan-out per request is expected and reported, never deduplicated away;
* a genuine retry shows up as the same ``(request_id, provider_type,
  operation)`` more than once — reported separately, because that is a signal
  about the provider, not a duplicate row to collapse.

Read-only: it selects, prints and exits. It never deletes, merges or rewrites a
row, and it does not create indexes; ``ix_ai_call_logs_request_id`` already
exists and is non-unique on purpose.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

DEFAULT_DSN_ENV = "DATABASE_URL"

SUMMARY_SQL = """
select request_id, provider_type, operation, status, latency_ms, input_hash, id
from ai_call_logs
"""

REQUEST_SQL = """
select id, request_id, provider_type, operation, status, model_version,
       latency_ms, prompt_version, error_code, work_order_id, created_at
from ai_call_logs
where request_id = :request_id
order by id
"""


@dataclass
class Summary:
    calls: int = 0
    requests: int = 0
    empty_request_id: int = 0
    fan_out: dict[int, int] = field(default_factory=dict)
    retried_calls: int = 0
    retried_requests: list[str] = field(default_factory=list)
    repeated_write_rows: int = 0
    repeated_write_requests: int = 0

    @property
    def rows_per_request(self) -> float:
        if not self.requests:
            return 0.0
        return round(self.calls / self.requests, 2)

    def as_dict(self) -> dict[str, object]:
        return {
            "calls": self.calls,
            "requests": self.requests,
            "rows_per_request": self.rows_per_request,
            "empty_request_id": self.empty_request_id,
            "fan_out": {str(k): v for k, v in sorted(self.fan_out.items())},
            "retried_calls": self.retried_calls,
            "retried_requests": self.retried_requests,
            "repeated_write_rows": self.repeated_write_rows,
            "repeated_write_requests": self.repeated_write_requests,
        }


def summarize(rows: Iterable[Mapping[str, object]]) -> Summary:
    """Count calls and requests with the key this table is actually keyed by."""

    per_request: dict[str, int] = defaultdict(int)
    per_call: Counter[tuple[str, str, str]] = Counter()
    per_write: Counter[tuple[str, str, str, str, object, str]] = Counter()
    summary = Summary()
    for row in rows:
        summary.calls += 1
        request_id = str(row.get("request_id") or "")
        if not request_id:
            summary.empty_request_id += 1
            continue
        per_request[request_id] += 1
        provider_type = str(row.get("provider_type") or "")
        operation = str(row.get("operation") or "")
        per_call[(request_id, provider_type, operation)] += 1
        per_write[
            (
                request_id,
                provider_type,
                operation,
                str(row.get("status") or ""),
                row.get("latency_ms"),
                str(row.get("input_hash") or ""),
            )
        ] += 1
    summary.requests = len(per_request)
    histogram: Counter[int] = Counter(per_request.values())
    summary.fan_out = dict(sorted(histogram.items()))
    retried = sorted(
        {key[0] for key, count in per_call.items() if count > 1}
    )
    summary.retried_calls = sum(
        count - 1 for count in per_call.values() if count > 1
    )
    summary.retried_requests = retried[:20]
    repeated = {
        key[0]: count for key, count in per_write.items() if count > 1
    }
    summary.repeated_write_rows = sum(count for count in repeated.values())
    summary.repeated_write_requests = len(repeated)
    return summary


def request_breakdown(
    rows: Iterable[Mapping[str, object]], request_id: str
) -> list[dict[str, object]]:
    """Every call that shares one request id, in write order."""

    selected = [
        dict(row)
        for row in rows
        if str(row.get("request_id") or "") == request_id
    ]
    selected.sort(key=lambda row: int(row.get("id") or 0))
    return selected


def dedupe_key(row: Mapping[str, object]) -> tuple[str, str, str]:
    """The finest key that identifies one provider call attempt."""

    return (
        str(row.get("request_id") or ""),
        str(row.get("provider_type") or ""),
        str(row.get("operation") or ""),
    )


def _connect(dsn: str):
    from sqlalchemy import create_engine

    return create_engine(dsn)


def _load(engine, sql: str, params: Mapping[str, object] | None = None):
    from sqlalchemy import text

    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(text(sql), params or ()).mappings()]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Report AI call volume by request and by call (read-only)."
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get(DEFAULT_DSN_ENV),
        help=f"SQLAlchemy DSN; defaults to ${DEFAULT_DSN_ENV}",
    )
    parser.add_argument(
        "--request-id",
        help="print every call that shares this request id",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error(f"no DSN: pass --database-url or set {DEFAULT_DSN_ENV}")

    engine = _connect(args.database_url)
    summary = summarize(_load(engine, SUMMARY_SQL))
    payload: dict[str, object] = {"summary": summary.as_dict()}
    if args.request_id:
        payload["request"] = request_breakdown(
            _load(engine, REQUEST_SQL, {"request_id": args.request_id}),
            args.request_id,
        )
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0

    data = summary.as_dict()
    print(f"calls={data['calls']} requests={data['requests']} "
          f"rows_per_request={data['rows_per_request']}")
    print("fan_out " + ", ".join(f"{k}:{v}" for k, v in data["fan_out"].items()))
    print(f"retried_calls={data['retried_calls']} "
          f"repeated_write_rows={data['repeated_write_rows']} "
          f"repeated_write_requests={data['repeated_write_requests']}")
    for row in payload.get("request", []):  # type: ignore[union-attr]
        print(
            f"  {row['id']} {row['provider_type']}/{row['operation']} "
            f"{row['status']} {row['latency_ms']}ms"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
