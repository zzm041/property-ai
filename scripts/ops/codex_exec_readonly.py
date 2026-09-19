"""Invoke Codex CLI exec in read-only sandbox with a UTF-8 prompt file.

Does not bypass sandbox or approvals. Does not edit product source.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
CODEX = "codex.cmd"
SCHEMA = REPO / "reports" / "codex" / "review-schema.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt-file", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    prompt_path = Path(args.prompt_file)
    out_path = Path(args.out)
    log_path = Path(args.log)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    prompt = prompt_path.read_text(encoding="utf-8")
    command = [
        CODEX,
        "exec",
        "--sandbox",
        "read-only",
        "--color",
        "never",
        "--skip-git-repo-check",
        "-C",
        str(REPO),
        "--output-schema",
        str(SCHEMA),
        "--output-last-message",
        str(out_path),
        "-",
    ]
    completed = subprocess.run(
        command,
        input=prompt,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO),
        capture_output=True,
        timeout=args.timeout,
        check=False,
    )
    log_path.write_text(
        json.dumps(
            {
                "command": command,
                "returncode": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "out": str(out_path),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"codex_returncode={completed.returncode}")
    print(f"codex_out={out_path}")
    print(f"codex_log={log_path}")
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
