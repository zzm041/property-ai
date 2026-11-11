"""Resolve every `app.*` import that scripts/ops makes.

The ops scripts run *outside* the app package: they put `backend/` on sys.path
and import the application by module path. After the package moves (S1..S7) a
stale path costs nothing at commit time and everything at 3am, because these
scripts are run by hand and some of them only once a week. This gate resolves
each import with `importlib.util.find_spec`, which is exactly what the import
statement would do, minus the side effects of actually importing the module.

Usage:
    python scripts/ops/check_script_imports.py            # check scripts/ops
    python scripts/ops/check_script_imports.py --path .   # check another tree
    python scripts/ops/check_script_imports.py --self-test

Exit codes: 0 clean, 1 unresolved imports, 2 usage error.
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import pathlib
import sys
import tempfile

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_PATH = REPO_ROOT / "scripts" / "ops"


def _app_imports(tree: ast.AST) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module == "app" or node.module.startswith("app."):
                found.append((node.module, node.lineno))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "app" or alias.name.startswith("app."):
                    found.append((alias.name, node.lineno))
    return found


def check(root: pathlib.Path) -> tuple[int, list[str]]:
    if str(REPO_ROOT / "backend") not in sys.path:
        sys.path.insert(0, str(REPO_ROOT / "backend"))
    checked = 0
    problems: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            problems.append(f"{path}: cannot parse: {exc}")
            continue
        for module, line in _app_imports(tree):
            checked += 1
            try:
                spec = importlib.util.find_spec(module)
            except ModuleNotFoundError as exc:
                problems.append(f"{path.name}:{line} {module}: {exc}")
                continue
            if spec is None:
                problems.append(f"{path.name}:{line} {module}: no spec")
    return checked, problems


def self_test() -> int:
    """Prove the gate bites: a planted stale import must be reported."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        (root / "good.py").write_text("import app.core.logging\n", encoding="utf-8")
        (root / "stale.py").write_text("from app.service.gone import Thing\n", encoding="utf-8")
        checked, problems = check(root)
    ok = checked == 2 and len(problems) == 1 and "app.service.gone" in problems[0]
    for problem in problems:
        print(f"  detected: {problem}")
    print("self-test OK" if ok else "self-test FAILED")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="resolve the app.* imports made by ops scripts")
    parser.add_argument("--path", type=pathlib.Path, default=DEFAULT_PATH, help="tree to scan")
    parser.add_argument("--self-test", action="store_true", help="plant a stale import and detect it")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()
    if not args.path.is_dir():
        print(f"not a directory: {args.path}")
        return 2

    checked, problems = check(args.path)
    for problem in problems:
        print(f"FAIL {problem}")
    print(f"imports checked {checked} failures {len(problems)}")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
