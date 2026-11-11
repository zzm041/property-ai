"""Check that the `.py` paths the docs mention still exist.

Docs drift silently: a rename leaves a convincing-looking path behind and the
next person reads it as gospel. Resolution is tiered, because prose is:

* a path with directories resolves if the token appears under the repo root, or
  as a suffix of any file in the tree (`cutover_delivery.py` is spelled in full
  once and shortened afterwards);
* a bare filename resolves if any file in the tree carries it.

Two kinds of `.py` token are not paths at all: the illustrative names the gate
documentation plants on purpose (`domain/rogue.py` shows what --self-test
detects) and the placeholder a commit-template example uses (`test_x.py`).

Usage:
    python scripts/ops/check_doc_paths.py                       # the four docs
    python scripts/ops/check_doc_paths.py docs/06-API-CONTRACT.md
    python scripts/ops/check_doc_paths.py --self-test

Exit codes: 0 every mentioned path resolves, 1 some do not, 2 usage error.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys
import tempfile

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_FILES = (
    "docs/06-API-CONTRACT.md",
    "docs/11-OPERATIONS.md",
    "docs/runbook.md",
    "ENGINEERING.md",
)

#: `<token>.py` inside prose, code spans and tables alike.
TOKEN_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_/.\-]*\.py\b")

#: Root prefixes a doc path may be written against.
SEARCH_ROOTS = ("", "backend", "backend/app", "scripts", "scripts/ops", "deploy")

#: Names the docs quote as examples rather than as existing files: the gate's
#: planted violations, the commit-template placeholder, and the three paths
#: ENGINEERING.md names precisely to say they must never come back.
ILLUSTRATIVE = frozenset(
    {
        "domain/rogue.py",
        "test_x.py",
        "backend/tests/unit/test_x.py",
        "service/work_order.py",
        "provider/file_scanner.py",
        "provider/langchain_llm.py",
    }
)

#: Only the live trees are indexed. `tmp/`, `reports/` and the old build
#: snapshots under them still hold copies of every module this repo ever had —
#: indexing those would resolve names the working tree deleted long ago. The one
#: report directory the docs do cite is listed explicitly.
INDEX_ROOTS = ("backend", "scripts", "deploy", "reports/pa116-cutover")


def _index() -> tuple[set[str], set[str]]:
    by_name: set[str] = set()
    suffixes: set[str] = set()
    candidates = [path for root in INDEX_ROOTS for path in (REPO_ROOT / root).rglob("*.py")]
    candidates += list(REPO_ROOT.glob("*.py"))
    for path in candidates:
        rel = path.relative_to(REPO_ROOT).as_posix()
        by_name.add(path.name)
        suffixes.add(rel)
        parts = rel.split("/")
        for start in range(1, len(parts)):
            suffixes.add("/".join(parts[start:]))
    return by_name, suffixes


def check(files: list[pathlib.Path], index: tuple[set[str], set[str]] | None = None) -> tuple[int, list[str]]:
    by_name, suffixes = index or _index()
    seen = 0
    problems: list[str] = []
    for path in files:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for token in TOKEN_RE.findall(line):
                seen += 1
                if token.startswith("-") or token in ILLUSTRATIVE:
                    continue
                if "/" in token:
                    ok = token.lstrip("./") in suffixes or any(
                        (REPO_ROOT / root / token).exists() for root in SEARCH_ROOTS
                    )
                else:
                    ok = token in by_name
                if not ok:
                    problems.append(f"{path.name}:{lineno} {token}")
    return seen, problems


def self_test() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        doc = pathlib.Path(tmp) / "doc.md"
        doc.write_text(
            "| `scripts/ops/unit_gate.py` | ok |\n"
            "old: `service/gone_module.py`\n"
            "example: `domain/rogue.py`\n",
            encoding="utf-8",
        )
        seen, problems = check([doc])
    ok = seen == 3 and len(problems) == 1 and "service/gone_module.py" in problems[0]
    for problem in problems:
        print(f"  detected: {problem}")
    print("self-test OK" if ok else "self-test FAILED")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="resolve the .py paths mentioned in the docs")
    parser.add_argument("files", nargs="*", help="markdown files (default: the four design docs)")
    parser.add_argument("--self-test", action="store_true", help="plant a stale path and detect it")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()
    targets = [REPO_ROOT / name for name in (args.files or DEFAULT_FILES)]
    missing_files = [str(path) for path in targets if not path.is_file()]
    if missing_files:
        print("not a file: " + ", ".join(missing_files))
        return 2

    seen, problems = check(targets)
    for problem in problems:
        print(f"STALE {problem}")
    print(f"paths checked {seen} unresolved {len(problems)}")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
