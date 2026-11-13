#!/usr/bin/env python
"""三态一致性检查：代码 HEAD / 运行镜像 / 发布分支。

离开"我说完成了"这种口径：一条命令给出三个坐标，不一致就非零退出。
用法：python scripts/ops/consistency_check.py [--healthz URL]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request


def sh(*args: str) -> str:
    return subprocess.run(args, capture_output=True, text=True, check=False).stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--healthz", default="http://127.0.0.1:8093/healthz")
    ap.add_argument("--container", default="property-ai-delivery-check-api-1")
    args = ap.parse_args()

    head = sh("git", "rev-parse", "HEAD")
    main_ref = sh("git", "rev-parse", "origin/main") or "none"
    dirty = sh("git", "status", "--porcelain", "--untracked-files=no")
    image = sh("docker", "inspect", args.container, "--format", "{{.Config.Image}}")

    stack_sha = "unreachable"
    try:
        with urllib.request.urlopen(args.healthz, timeout=5) as resp:
            payload = json.load(resp)
        stack_sha = payload.get("data", {}).get("build", {}).get("source_sha", "missing")
    except Exception as exc:  # noqa: BLE001 - 报告用
        stack_sha = f"unreachable: {type(exc).__name__}"

    rows = [
        ("HEAD", head[:12]),
        ("origin/main", main_ref[:12]),
        ("stack source_sha", stack_sha[:12] if stack_sha else stack_sha),
        ("stack image", image or "missing"),
        ("worktree dirty", "yes (%d files)" % len(dirty.splitlines()) if dirty else "no"),
    ]

    width = max(len(r[0]) for r in rows)
    for key, value in rows:
        print(f"{key:<{width}}  {value}")

    ok = True
    if stack_sha and not stack_sha.startswith("unreachable"):
        ok = ok and head.startswith(stack_sha[:12])
    if main_ref != "none":
        print("\nnote: origin/main 是本地缓存 ref，联网后需 `git fetch` 才算最新")
    if dirty:
        ok = False
    print("\nRESULT:", "CONSISTENT" if ok else "INCONSISTENT")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
