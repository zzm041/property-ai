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


def parse_server_process_ms(headers: httpx.Headers | dict[str, str]) -> float | None:
    """Parse server processing time. Header names encode milliseconds; dur is W3C ms.

    Do not infer seconds vs milliseconds from magnitude.
    """

    raw = None
    if hasattr(headers, "get"):
        raw = headers.get("x-process-time-ms")
    if raw:
        try:
            return float(raw)
        except ValueError:
            return None
    timing = headers.get("server-timing") if hasattr(headers, "get") else None
    if not timing:
        return None
    for part in str(timing).split(","):
        for token in part.split(";"):
            token = token.strip()
            if token.startswith("dur="):
                try:
                    return float(token.split("=", 1)[1])
                except ValueError:
                    return None
    return None


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
    httpx_elapsed: list[float] = []
    server_samples: list[float] = []
    error_statuses: list[int] = []
    errors = 0
    server_missing = 0
    echo_mismatches = 0
    statuses: dict[int, int] = {}
    headers_base = {}
    if token:
        headers_base["Authorization"] = f"Bearer {token}"

    def one(index: int) -> tuple[float, int, float | None, float | None, bool]:
        headers = dict(headers_base)
        json_body = dict(body or {})
        request_id = f"perf-{index}-{uuid4().hex[:8]}"
        headers["X-Request-Id"] = request_id
        if method.upper() == "POST" and path.endswith("/work-orders"):
            headers["Idempotency-Key"] = f"perf-{uuid4()}"
        started = time.perf_counter()
        try:
            if mode in {"cold", "A"}:
                with httpx.Client(timeout=10.0) as client:
                    response = client.request(method, url, headers=headers, json=json_body or None)
            elif mode in {"nokeepalive", "B"}:
                response = nokeep.request(method, url, headers=headers, json=json_body or None)
            else:
                response = pooled.request(method, url, headers=headers, json=json_body or None)
            client_ms = (time.perf_counter() - started) * 1000
            httpx_elapsed_ms = response.elapsed.total_seconds() * 1000
            server_ms = parse_server_process_ms(response.headers)
            echoed = response.headers.get("x-request-id") == request_id
            if mode in {"elapsed", "D"}:
                # D reports httpx elapsed as the primary client metric. It is not server time.
                client_ms = httpx_elapsed_ms
            return client_ms, response.status_code, server_ms, httpx_elapsed_ms, echoed
        except httpx.HTTPError:
            return (time.perf_counter() - started) * 1000, 0, None, None, False

    pooled: httpx.Client | None = None
    nokeep: httpx.Client | None = None
    started_all = time.perf_counter()
    try:
        if mode in {"pooled", "C", "elapsed", "D"}:
            pooled = httpx.Client(timeout=10.0)
        if mode in {"nokeepalive", "B"}:
            nokeep = httpx.Client(
                timeout=10.0,
                limits=httpx.Limits(max_keepalive_connections=0, max_connections=concurrency + 2),
            )
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [pool.submit(one, index) for index in range(requests)]
            for future in as_completed(futures):
                latency, status, server_ms, httpx_ms, echoed = future.result()
                latencies.append(latency)
                if httpx_ms is not None:
                    httpx_elapsed.append(httpx_ms)
                if server_ms is not None:
                    server_samples.append(server_ms)
                else:
                    server_missing += 1
                if not echoed:
                    echo_mismatches += 1
                statuses[status] = statuses.get(status, 0) + 1
                if status != 200:
                    errors += 1
                    error_statuses.append(status)
    finally:
        if pooled is not None:
            pooled.close()
        if nokeep is not None:
            nokeep.close()
    elapsed = time.perf_counter() - started_all
    latencies.sort()
    httpx_elapsed.sort()
    server_samples.sort()
    return {
        "url": url,
        "method": method.upper(),
        "mode": mode,
        "requests": requests,
        "concurrency": concurrency,
        "errors": errors,
        "error_statuses": error_statuses,
        "error_rate": round(errors / max(requests, 1), 4),
        "status_counts": statuses,
        "throughput_rps": round(requests / elapsed, 2) if elapsed else 0,
        "client_lifecycle_p50_ms": _percentile(latencies, 50),
        "client_lifecycle_p95_ms": _percentile(latencies, 95),
        "client_lifecycle_p99_ms": _percentile(latencies, 99),
        "p50_ms": _percentile(latencies, 50),
        "p95_ms": _percentile(latencies, 95),
        "p99_ms": _percentile(latencies, 99),
        "httpx_elapsed_p50_ms": _percentile(httpx_elapsed, 50),
        "httpx_elapsed_p95_ms": _percentile(httpx_elapsed, 95),
        "httpx_elapsed_note": "httpx response.elapsed is transfer time, not exclusive server processing",
        "server_process_samples": len(server_samples),
        "server_process_missing": server_missing,
        "server_process_p50_ms": _percentile(server_samples, 50) if server_samples else None,
        "server_process_p95_ms": _percentile(server_samples, 95) if server_samples else None,
        "server_process_unit": "milliseconds",
        "request_id_echo_mismatches": echo_mismatches,
        "elapsed_s": round(elapsed, 3),
        "note": {
            "cold": "A: Client construct + request + close. Not pure server latency.",
            "A": "A: Client construct + request + close. Not pure server latency.",
            "nokeepalive": "B: Client initialized, keepalive disabled.",
            "B": "B: Client initialized, keepalive disabled.",
            "pooled": "C: shared httpx.Client connection pool reuse.",
            "C": "C: shared httpx.Client connection pool reuse.",
            "elapsed": "D: httpx response.elapsed; not exclusive server-side time.",
            "D": "D: httpx response.elapsed; not exclusive server-side time.",
        }.get(mode, mode),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8082")
    parser.add_argument("--path", default="/healthz")
    parser.add_argument("--method", default="GET")
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument(
        "--mode",
        choices=("cold", "pooled", "nokeepalive", "elapsed", "A", "B", "C", "D"),
        default="cold",
    )
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
