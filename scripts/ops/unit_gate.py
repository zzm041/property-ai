#!/usr/bin/env python3
"""Unit-test gate: compare the red set against the committed baseline (known-red).

判据（不是数数）：
    R = 本次失败 nodeid 集合, K = scripts/ops/known-red.txt
    R ⊆ K            → exit 0（K - R 非空时打印 SHRUNK: n，提示可以删掉已转绿的行）
    R 含 K 之外的新项 → 逐条打印 NEW RED: <nodeid>，exit 1
passed / failed / skipped 只打印，**不参与判定**：红数抖动不该当门槛。

用法（缺 env 会被 Settings 拦下，脚本自己也先自检一遍）：

    APP_ENV=test \
    DATABASE_URL='mysql+pymysql://property_test:change-me-test@127.0.0.1:3337/property_ai_scratch?charset=utf8mb4' \
    TEST_DATABASE_URL='mysql+pymysql://property_test:change-me-test@127.0.0.1:3307/property_ai_test?charset=utf8mb4' \
    python scripts/ops/unit_gate.py

    python scripts/ops/unit_gate.py --target backend/tests/unit/test_health.py   # 只跑一个文件
    python scripts/ops/unit_gate.py --verbose                                   # 透传 pytest 输出
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

# 控制台默认 codepage（cp936）会把中文与破折号打成乱码；判定文本是给人读的，统一按 UTF-8 输出。
for _stream in (sys.stdout, sys.stderr):
    if (_stream.encoding or "").lower() not in ("utf-8", "utf8"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_KNOWN_RED = REPO_ROOT / "scripts" / "ops" / "known-red.txt"
DEFAULT_TARGET = "backend/tests/unit"

# `-rf` 打印 FAILED，`-rE` 打印 ERROR（收集期失败也是红）
PYTEST_ARGS = ["-q", "--tb=no", "-p", "no:cacheprovider", "-rfE"]

SUMMARY = re.compile(
    r"(?:(?P<failed>\d+) failed)?[, ]*"
    r"(?:(?P<passed>\d+) passed)?[, ]*"
    r"(?:(?P<skipped>\d+) skipped)?[, ]*"
    r"(?:(?P<errors>\d+) error)?"
)
COUNTS = {
    "failed": re.compile(r"(\d+) failed"),
    "passed": re.compile(r"(\d+) passed"),
    "skipped": re.compile(r"(\d+) skipped"),
    "errors": re.compile(r"(\d+) errors?\b"),
}
RED_LINE = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)")


def normalise(nodeid: str) -> str:
    """Pytest prints Windows separators; known-red.txt is POSIX. Compare POSIX."""
    return nodeid.replace("\\", "/").strip()


def load_known_red(path: Path) -> set[str]:
    known: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        known.add(normalise(line))
    return known


def require_environment() -> None:
    """Fail fast with the same hint ENGINEERING.md §4 gives, instead of a 60s Settings crash."""
    missing = [name for name in ("DATABASE_URL", "TEST_DATABASE_URL") if not os.environ.get(name)]
    if missing:
        sys.stderr.write(
            "unit_gate: missing env " + ", ".join(missing) + "\n"
            "  这些变量是 Settings 的硬校验（两个库必须不同），见 ENGINEERING.md §4 的规范命令：\n"
            "  APP_ENV=test \\\n"
            "  DATABASE_URL='mysql+pymysql://property_test:change-me-test@127.0.0.1:3337/property_ai_scratch?charset=utf8mb4' \\\n"
            "  TEST_DATABASE_URL='mysql+pymysql://property_test:change-me-test@127.0.0.1:3307/property_ai_test?charset=utf8mb4' \\\n"
            "  python scripts/ops/unit_gate.py\n"
        )
        raise SystemExit(2)


def build_env() -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("APP_ENV", "test")
    paths = [str(REPO_ROOT), str(REPO_ROOT / "backend")]
    existing = env.get("PYTHONPATH")
    if existing:
        paths.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(paths)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def run_pytest(target: str, extra: list[str], verbose: bool) -> tuple[int, str]:
    command = [sys.executable, "-m", "pytest", target, *PYTEST_ARGS, *extra]
    if verbose:
        sys.stderr.write("unit_gate: " + " ".join(command) + "\n")
    completed = subprocess.run(
        command,
        cwd=str(REPO_ROOT),
        env=build_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.returncode, completed.stdout


def parse_red(output: str) -> set[str]:
    red: set[str] = set()
    for line in output.splitlines():
        match = RED_LINE.match(line.strip())
        if match:
            red.add(normalise(match.group(1)))
    return red


def parse_counts(output: str) -> dict[str, int]:
    counts = {"passed": 0, "failed": 0, "skipped": 0, "errors": 0}
    for key, pattern in COUNTS.items():
        found = pattern.findall(output)
        if found:
            counts[key] = int(found[-1])
    return counts


def run_script_import_check(verbose: bool) -> int:
    """S7-3: every `app.*` import under scripts/ must resolve to a real module.

    pytest never imports `scripts/`, so a renamed module leaves a dead import in
    a runbook script that nobody executes until the day it is needed. Run this
    before the suite so the failure is attributed to the tree rather than
    surfacing as a mysterious collection error much later.
    """
    script = REPO_ROOT / "scripts" / "ops" / "check_script_imports.py"
    if not script.is_file():
        sys.stderr.write(f"unit_gate: script import check missing: {script}\n")
        return 2
    completed = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if verbose:
        sys.stdout.write(completed.stdout or "")
        sys.stderr.write(completed.stderr or "")
    if completed.returncode != 0:
        sys.stderr.write(completed.stdout or "")
        sys.stderr.write(completed.stderr or "")
        sys.stderr.write(
            "unit_gate: FAILED - scripts/ holds an app.* import with no module behind it "
            f"(check_script_imports exit {completed.returncode}); fix the import or the path.\n"
        )
        return 1
    last = (completed.stdout or "").strip().splitlines()
    print(f"unit_gate: {last[-1] if last else 'script imports OK'}")
    return 0


def write_summary(path: str, *, counts: dict[str, int], red: set[str], known: set[str], verdict: str) -> None:
    """Append the run to a markdown summary file (CI job summary).

    Written before the exit code is chosen: a gate that fails is exactly the run whose
    numbers a reviewer needs, so "publish" cannot be conditional on "passed".
    """
    target = Path(path)
    new_red = sorted(red - known)
    lines = [
        "### unit gate",
        "",
        f"- verdict: **{verdict}**",
        f"- passed: {counts['passed']}",
        f"- failed: {counts['failed']}",
        f"- skipped: {counts['skipped']}",
        f"- errors: {counts['errors']}",
        f"- red: {len(red)} (known-red baseline: {len(known)})",
        "",
    ]
    if new_red:
        lines.append(f"<details><summary>NEW RED ({len(new_red)})</summary>")
        lines.append("")
        lines.extend(f"- `{nodeid}`" for nodeid in new_red)
        lines.append("")
        lines.append("</details>")
        lines.append("")
    with target.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Red-set gate against scripts/ops/known-red.txt")
    parser.add_argument("--target", default=DEFAULT_TARGET, help="pytest target (default: backend/tests/unit)")
    parser.add_argument("--known-red", default=str(DEFAULT_KNOWN_RED), help="baseline file")
    parser.add_argument("--pytest-arg", action="append", default=[], help="extra pytest arg (repeatable)")
    parser.add_argument("--verbose", action="store_true", help="echo the pytest command and full output length")
    parser.add_argument(
        "--skip-script-imports",
        action="store_true",
        help="skip the scripts/ import check (S7-3); only for a tree that is mid-edit",
    )
    parser.add_argument(
        "--summary-file",
        default=None,
        help=(
            "append a markdown summary of this run to PATH "
            "(CI passes $GITHUB_STEP_SUMMARY; written before the exit code is decided, "
            "so a failing gate still leaves its numbers in the job summary)"
        ),
    )
    args = parser.parse_args(argv)

    known_path = Path(args.known_red)
    if not known_path.is_file():
        sys.stderr.write(f"unit_gate: known-red file not found: {known_path}\n")
        return 2
    known = load_known_red(known_path)
    if not known:
        sys.stderr.write(f"unit_gate: known-red file is empty: {known_path}\n")
        return 2

    if not args.skip_script_imports:
        import_status = run_script_import_check(args.verbose)
        if import_status != 0:
            return import_status
    else:
        # t183: stay quiet no longer. A log that never mentions the check is
        # indistinguishable from one where it passed silently.
        print("unit_gate: SKIPPED script-import check (--skip-script-imports)")

    require_environment()
    exit_code, output = run_pytest(args.target, args.pytest_arg, args.verbose)
    if args.verbose:
        sys.stdout.write(output)
    red = parse_red(output)
    counts = parse_counts(output)

    print(
        f"unit_gate: passed={counts['passed']} failed={counts['failed']} "
        f"skipped={counts['skipped']} errors={counts['errors']} "
        f"(counts are informational, the verdict is set membership)"
    )
    print(f"unit_gate: red={len(red)} known-red={len(known)} pytest-exit={exit_code}")

    if not red and exit_code not in (0, 1):
        # 5 = 没收集到用例；其它非 0/1 是 pytest 自己崩了 —— 不能当成「零红」放行
        if args.summary_file:
            write_summary(
                args.summary_file,
                counts=counts,
                red=red,
                known=known,
                verdict=f"FAIL-CLOSED - pytest exit {exit_code} with no parsable result",
            )
        sys.stderr.write(
            "unit_gate: pytest produced no red and no usable summary "
            f"(exit {exit_code}); refusing to pass. Re-run with --verbose.\n"
        )
        return 2
    if exit_code == 1 and not red:
        if args.summary_file:
            write_summary(
                args.summary_file,
                counts=counts,
                red=red,
                known=known,
                verdict="FAIL-CLOSED - pytest exit 1 without a parsable FAILED/ERROR line",
            )
        sys.stderr.write(
            "unit_gate: pytest failed but no FAILED/ERROR line could be parsed; "
            "refusing to pass. Re-run with --verbose.\n"
        )
        return 2

    new_red = sorted(red - known)
    shrunk = sorted(known - red)

    for nodeid in new_red:
        print(f"NEW RED: {nodeid}")
    if shrunk:
        print(f"SHRUNK: {len(shrunk)}")
        for nodeid in shrunk:
            print(f"  now green (可以删掉这一行): {nodeid}")

    if args.summary_file:
        write_summary(
            args.summary_file,
            counts=counts,
            red=red,
            known=known,
            verdict=f"FAILED - {len(new_red)} new red outside known-red" if new_red else "OK - red \u2286 known-red",
        )

    if new_red:
        print(
            f"unit_gate: FAILED - {len(new_red)} new red test(s) outside known-red. "
            "先定性（产品缺陷 / 测试过时 / 环境缺失），再决定修掉或在 known-red.txt 里加白并写理由。"
        )
        return 1

    print("unit_gate: OK - red set is a subset of known-red")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
