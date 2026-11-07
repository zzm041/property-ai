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
    parser.add_argument(
        "--image",
        action="append",
        default=[],
        help="Attach a local image to the independent review (repeatable)",
    )
    args = parser.parse_args()
    prompt_path = Path(args.prompt_file)
    out_path = Path(args.out)
    log_path = Path(args.log)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    preamble = (REPO / "reports" / "codex" / "context-preamble.txt").read_text(
        encoding="utf-8"
    )
    task_prompt = prompt_path.read_text(encoding="utf-8")
    prompt = preamble.rstrip() + "\n\n" + task_prompt.lstrip()
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
    ]
    for image in args.image:
        image_path = Path(image).resolve()
        if not image_path.is_file():
            raise FileNotFoundError(f"Review image does not exist: {image_path}")
        command.extend(["--image", str(image_path)])
    command.append("-")
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
