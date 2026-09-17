"""Bounded concurrency probe. Default method is the original cold-connect GET.

New methods are labeled and must not be compared as the same experiment.
Work-order POST uses a unique Idempotency-Key per logical request.
"""

from __future__ import annotations

import argparse
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from uuid import uuid4

import httpx


def _percentile(latencies: list[float], value: float) -> float:
    if not latencies:
        return 0.0
    index = min(len(latencies) - 1, max(0, int(round((value / 100) * (len(latencies) - 1)))))
    return round(latencies[index], 2)


def run_probe(
    *,
    base_url: str,
    path: str,
    method: str,
    concurrency: int,
    requests: int,
    mode: str,
    token: str | None,
    body: dict[str, object] | None,
) -> dict[str, object]:
    url = base_url.rstrip("/") + path
    latencies: list[float] = []
    errors = 0
    statuses: dict[int, int] = {}
    headers_base = {}
    if token:
        headers_base["Authorization"] = f"Bearer {token}"

    def one(index: int) -> tuple[float, int]:
        headers = dict(headers_base)
        json_body = dict(body or {})
        if method.upper() == "POST" and path.endswith("/work-orders"):
            headers["Idempotency-Key"] = f"perf-{uuid4()}"
            headers["X-Request-Id"] = f"perf-{index}-{uuid4().hex[:8]}"
        started = time.perf_counter()
        try:
            if mode == "cold":
                with httpx.Client(timeout=10.0) as client:
                    response = client.request(method, url, headers=headers, json=json_body or None)
            else:
                response = pooled.request(method, url, headers=headers, json=json_body or None)
            return (time.perf_counter() - started) * 1000, response.status_code
        except httpx.HTTPError:
            return (time.perf_counter() - started) * 1000, 0

    pooled: httpx.Client | None = None
    started_all = time.perf_counter()
    try:
        if mode == "pooled":
            pooled = httpx.Client(timeout=10.0)
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [pool.submit(one, index) for index in range(requests)]
            for future in as_completed(futures):
                latency, status = future.result()
                latencies.append(latency)
                statuses[status] = statuses.get(status, 0) + 1
                if status != 200:
                    errors += 1
    finally:
        if pooled is not None:
            pooled.close()
    elapsed = time.perf_counter() - started_all
    latencies.sort()
    return {
        "url": url,
        "method": method.upper(),
        "mode": mode,
        "requests": requests,
        "concurrency": concurrency,
        "errors": errors,
        "error_rate": round(errors / max(requests, 1), 4),
        "status_counts": statuses,
        "throughput_rps": round(requests / elapsed, 2) if elapsed else 0,
        "p50_ms": _percentile(latencies, 50),
        "p95_ms": _percentile(latencies, 95),
        "p99_ms": _percentile(latencies, 99),
        "elapsed_s": round(elapsed, 3),
        "note": "cold = new HTTP connection per request; pooled = shared httpx.Client",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8082")
    parser.add_argument("--path", default="/healthz")
    parser.add_argument("--method", default="GET")
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--mode", choices=("cold", "pooled"), default="cold")
    parser.add_argument("--token", default="")
    parser.add_argument("--house-id", type=int, default=0)
    args = parser.parse_args()
    body = None
    if args.method.upper() == "POST" and args.path.endswith("/work-orders"):
        if not args.token or args.house_id <= 0:
            print("POST /work-orders requires --token and --house-id")
            return 2
        body = {"house_id": args.house_id, "text": "Perf probe work order", "category": "ELECTRICAL"}
    result = run_probe(
        base_url=args.base_url,
        path=args.path,
        method=args.method,
        concurrency=args.concurrency,
        requests=args.requests,
        mode=args.mode,
        token=args.token or None,
        body=body,
    )
    print(result)
    return 0 if int(result["errors"]) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
