#!/usr/bin/env python
"""网络恢复后自动收口：构建镜像 -> 切栈 -> 校验 -> 推 main。

可循环调用（每 5 分钟一次）：
- 网络/依赖不通时快速退出 exit 2（外部循环继续等）
- 全部完成且三态一致 exit 0（外部循环停止）
日志追加到 D:\\AI\\runtime\\finish-line.log。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime

ROOT = r"D:\property-ai"
LOG = r"D:\AI\runtime\finish-line.log"
PROXY = "http://127.0.0.1:7899"
HEALTHZ = "http://127.0.0.1:8093/healthz"
COMPOSE = [
    "docker", "compose", "-f", "deploy/docker-compose.delivery.yml",
    "--env-file", "deploy/.env.delivery", "-p", "property-ai-delivery-check",
]


def log(msg: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def sh(args: list[str], timeout: int = 1800, env: dict | None = None) -> tuple[int, str]:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    proc = subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                          timeout=timeout, env=merged)
    return proc.returncode, (proc.stdout + proc.stderr)


def http_ok(url: str, proxy: str | None = None, timeout: int = 8) -> bool:
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy}) if proxy
        else urllib.request.ProxyHandler({})
    )
    try:
        with opener.open(url, timeout=timeout) as resp:
            return 200 <= resp.status < 400
    except Exception:
        return False


def network() -> tuple[bool, bool]:
    pypi = http_ok("https://pypi.org/simple/") or http_ok("https://pypi.org/simple/", PROXY)
    github = http_ok("https://api.github.com/") or http_ok("https://api.github.com/", PROXY)
    return pypi, github


def head_sha() -> str:
    code, out = sh(["git", "rev-parse", "--short=12", "HEAD"], timeout=30)
    return out.strip() if code == 0 else "unknown"


def stack_sha() -> str:
    try:
        with urllib.request.urlopen(HEALTHZ, timeout=5) as resp:
            return json.load(resp)["data"]["build"]["source_sha"]
    except Exception:
        return "unreachable"


def build_and_cutover(sha: str, tag: str, image: str, pypi: bool) -> int:
    if not pypi:
        log("skip build: pypi unreachable")
        return 2

    log(f"build {image}")
    code, out = sh(["docker", "build", "-f", "deploy/Dockerfile", "-t", image, "."])
    if code != 0:
        log("build FAILED:\n" + out[-1500:])
        return 1
    log("build ok")

    log("cutover api/worker/beat/publisher")
    code, out = sh(COMPOSE + ["up", "-d", "--no-deps", "api", "worker", "beat", "publisher"],
                   env={"DELIVERY_IMAGE_TAG": tag})
    if code != 0:
        log("cutover FAILED:\n" + out[-1500:])
        return 1

    for _ in range(30):
        now = stack_sha()
        if now.startswith(sha):
            log(f"stack reports {now[:12]} ok")
            return 0
        time.sleep(4)
    log(f"cutover did not converge, stack={stack_sha()}")
    return 1


def main() -> int:
    pypi, github = network()
    sha = head_sha()
    tag = f"src-{sha}-wip"
    image = f"property-ai-delivery:{tag}"
    log(f"start head={sha} pypi={pypi} github={github}")

    if not stack_sha().startswith(sha):
        rc = build_and_cutover(sha, tag, image, pypi)
        if rc != 0:
            return rc
    else:
        log("stack already on this commit")

    if not github:
        log("github unreachable: push pending, will retry")
        return 2

    log("push main")
    code, out = 1, ""
    for attempt in range(6):
        code, out = sh(["git", "push", "origin", "HEAD:main"])
        if code == 0:
            log(f"push ok (attempt {attempt + 1})")
            break
        tail = (out.strip().splitlines() or [""])[-1][:160]
        log(f"push attempt {attempt + 1} failed: {tail}")
        time.sleep(20)
    if code != 0:
        log("push FAILED after retries")
        return 2

    code, out = sh([sys.executable, "scripts/ops/consistency_check.py"], timeout=120)
    last = (out.strip().splitlines() or ["<no output>"])[-1]
    log(f"consistency: {last}")
    return 0 if code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
