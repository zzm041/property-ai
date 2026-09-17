"""Bounded concurrency probe against healthz. Not a thousand-user claim."""

from __future__ import annotations

import argparse
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8082")
    parser.add_argument("--path", default="/healthz")
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--requests", type=int, default=100)
    args = parser.parse_args()
    url = args.base_url.rstrip("/") + args.path
    latencies: list[float] = []
    errors = 0

    def one(_index: int) -> tuple[float, int]:
        started = time.perf_counter()
        try:
            response = httpx.get(url, timeout=5.0)
            return (time.perf_counter() - started) * 1000, response.status_code
        except httpx.HTTPError:
            return (time.perf_counter() - started) * 1000, 0

    started_all = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(one, index) for index in range(args.requests)]
        for future in as_completed(futures):
            latency, status = future.result()
            latencies.append(latency)
            if status != 200:
                errors += 1
    elapsed = time.perf_counter() - started_all
    latencies.sort()

    def pct(value: float) -> float:
        if not latencies:
            return 0.0
        index = min(len(latencies) - 1, max(0, int(round((value / 100) * (len(latencies) - 1)))))
        return round(latencies[index], 2)

    print(
        {
            "url": url,
            "requests": args.requests,
            "concurrency": args.concurrency,
            "errors": errors,
            "error_rate": round(errors / max(args.requests, 1), 4),
            "throughput_rps": round(args.requests / elapsed, 2),
            "p50_ms": pct(50),
            "p95_ms": pct(95),
            "p99_ms": pct(99),
            "elapsed_s": round(elapsed, 3),
        }
    )
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
