"""Layering gate for the domain/service split (phase 2, step S0).

Checks are static (ast, never grep) because the codebase imports at function
scope in 33 places — a textual search would miss those and report a clean tree.

Usage:
    python scripts/ops/check_layering.py                # all checks
    python scripts/ops/check_layering.py --report       # inventory, always exit 0
    python scripts/ops/check_layering.py --domain-edges # only package edges
    python scripts/ops/check_layering.py --shims        # only compat shims

Exit codes: 0 clean, 1 violation, 2 usage/config problem.
"""
from __future__ import annotations

import argparse
import ast
import pathlib
import sys
from collections import defaultdict

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
APP_ROOT = REPO_ROOT / "backend" / "app"
DOMAIN_ROOT = APP_ROOT / "domain"

# A domain module may not depend on any of these (they sit above it).
FORBIDDEN_DOMAIN_TARGETS = {
    "app.service",
    "app.api",
    "app.schema",
    "app.repository",
    "app.worker",
    "app.cli",
    "app.model",
    "app.provider",
    "app.core",
}

# Package-level edges allowed inside app.domain (target -> who may import it).
ALLOWED_DOMAIN_EDGES: dict[str, set[str]] = {
    "identity": set(),
    "work_order": {"identity"},
    "ai": {"identity", "work_order"},
    "knowledge": {"identity"},
    "governance": {"identity"},
    "dev": {"identity", "work_order", "ai", "knowledge", "governance"},
}

# Future package for every module that is still flat today (S1..S4 target).
# Once a package exists, `app.domain.<pkg>.<module>` resolves directly and this
# table only keeps mapping the leftover re-export shims.
FLAT_MODULE_PACKAGE: dict[str, str] = {
    "iam": "identity",
    "phone": "identity",
    "resident": "identity",
    "property": "identity",
    "work_order": "work_order",
    "work_order_visibility": "work_order",
    "receipt_handling": "work_order",
    "diagnosis": "work_order",
    "vision": "work_order",
    "employee": "work_order",  # assignment/attendance policy; plan table §2 omitted it
    "ai": "ai",
    "observed_event": "ai",
    "knowledge": "knowledge",
    "audit": "governance",
    "incident": "governance",
    "announcement": "governance",
    "operations": "governance",
    "dev_seed": "dev",
}

# Legacy module -> package that now owns it. Filled in by S1..S4; an entry here
# makes --shims require the legacy file to be a thin re-export.
SHIMMED_MODULES: dict[str, str] = {
    "app.domain.iam": "identity",
    "app.domain.phone": "identity",
    "app.domain.resident": "identity",
    "app.domain.property": "identity",
}

SHIM_MAX_EFFECTIVE_LINES = 8


def module_name(path: pathlib.Path, backend_root: pathlib.Path | None = None) -> str:
    root = backend_root or (REPO_ROOT / "backend")
    rel = path.relative_to(root)
    parts = list(rel.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def resolve_relative(importer: str, node: ast.ImportFrom) -> list[str]:
    if not node.level:
        return [node.module] if node.module else []
    parts = importer.split(".")
    prefix = ".".join(parts[: len(parts) - node.level + 1])
    return [f"{prefix}.{node.module}" if node.module else prefix]


def imports_of(tree: ast.AST, importer: str) -> list[tuple[str, int, bool]]:
    """(target module, line, nested-inside-function)"""
    nested: set[int] = set()
    for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        for node in ast.walk(fn):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                nested.add(node.lineno)
    found: list[tuple[str, int, bool]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            targets = resolve_relative(importer, node)
        elif isinstance(node, ast.Import):
            targets = [alias.name for alias in node.names]
        else:
            continue
        for target in targets:
            found.append((target, node.lineno, node.lineno in nested))
    return found


def parse(path: pathlib.Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8", errors="replace"))


def domain_package(module: str) -> str | None:
    parts = module.split(".")
    if len(parts) < 3 or parts[0] != "app" or parts[1] != "domain":
        return None
    if len(parts) > 3:
        return parts[2]
    return FLAT_MODULE_PACKAGE.get(parts[2], parts[2])


def check_upward(domain_root: pathlib.Path | None = None, backend_root: pathlib.Path | None = None) -> list[str]:
    root = backend_root or (REPO_ROOT / "backend")
    problems: list[str] = []
    for path in sorted((domain_root or DOMAIN_ROOT).rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        importer = module_name(path, root)
        for target, line, nested in imports_of(parse(path), importer):
            area = ".".join(target.split(".")[:2])
            if area in FORBIDDEN_DOMAIN_TARGETS:
                kind = "lazy " if nested else ""
                problems.append(f"{path.relative_to(backend_root or (REPO_ROOT / 'backend'))}:{line} {importer} -> {target} ({kind}import)")
    return problems


def check_domain_edges(domain_root: pathlib.Path | None = None, backend_root: pathlib.Path | None = None) -> list[str]:
    root = backend_root or (REPO_ROOT / "backend")
    problems: list[str] = []
    for path in sorted((domain_root or DOMAIN_ROOT).rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        importer = module_name(path, root)
        source = domain_package(importer)
        if source is None:
            continue
        for target, line, _nested in imports_of(parse(path), importer):
            dest = domain_package(target)
            if dest is None or dest == source:
                continue
            allowed = ALLOWED_DOMAIN_EDGES.get(source, set())
            if dest not in allowed:
                problems.append(
                    f"{path.relative_to(root)}:{line} domain.{source} -> domain.{dest} "
                    f"(allowed: {sorted(allowed) or 'nothing'})"
                )
    return problems


def check_shims() -> list[str]:
    problems: list[str] = []
    for legacy, package in sorted(SHIMMED_MODULES.items()):
        path = REPO_ROOT / "backend" / f"{legacy.replace('.', '/')}.py"
        if not path.exists():
            problems.append(f"{legacy}: legacy module is missing (expected a re-export shim)")
            continue
        source = path.read_text(encoding="utf-8", errors="replace")
        effective = [
            line
            for line in source.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        if len(effective) > SHIM_MAX_EFFECTIVE_LINES:
            problems.append(
                f"{legacy}: {len(effective)} effective lines > {SHIM_MAX_EFFECTIVE_LINES}, "
                f"it still carries logic instead of re-exporting {package}"
            )
            continue
        if f"app.domain.{package}" not in source:
            problems.append(f"{legacy}: does not re-export app.domain.{package}")
    return problems


def self_test() -> int:
    """Prove the gate bites: build a throw-away tree with two planted violations."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        backend = pathlib.Path(tmp) / "backend"
        domain = backend / "app" / "domain"
        for package in ("identity", "work_order", "knowledge"):
            (domain / package).mkdir(parents=True)
            (domain / package / "__init__.py").write_text("", encoding="utf-8")
        # allowed edge: work_order -> identity
        (domain / "work_order" / "allowed.py").write_text(
            "from app.domain.identity import models  # noqa: F401\n", encoding="utf-8"
        )
        # planted 1: domain reaches up into service, at function scope
        (domain / "work_order" / "leaky.py").write_text(
            "def dispatch():\n"
            "    from app.service.work_order import WorkOrderService\n"
            "\n"
            "    return WorkOrderService\n",
            encoding="utf-8",
        )
        # planted 2: knowledge -> work_order is not an allowed package edge
        (domain / "knowledge" / "wrong_edge.py").write_text(
            "from app.domain.work_order import rules\n", encoding="utf-8"
        )
        upward = check_upward(domain, backend)
        edges = check_domain_edges(domain, backend)

    ok = (
        len(upward) == 1
        and "leaky.py" in upward[0]
        and "lazy import" in upward[0]
        and len(edges) == 1
        and "wrong_edge.py" in edges[0]
        and "domain.knowledge -> domain.work_order" in edges[0]
    )
    for problem in upward + edges:
        print(f"  detected: {problem}")
    print("self-test OK" if ok else "self-test FAILED")
    return 0 if ok else 1


def report() -> None:
    importers: dict[str, set[str]] = defaultdict(set)
    edges: set[tuple[str, str]] = set()
    for path in sorted((REPO_ROOT / "backend" / "app").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        importer = module_name(path)
        for target, _line, _nested in imports_of(parse(path), importer):
            if target.startswith("app."):
                importer_pkg = domain_package(importer)
                target_pkg = domain_package(target)
                if target_pkg:
                    importers[target_pkg].add(importer)
                if importer_pkg and target_pkg and importer_pkg != target_pkg:
                    edges.add((importer_pkg, target_pkg))
    print("# domain package importers (distinct files)")
    for package, who in sorted(importers.items()):
        print(f"{package:14s} {len(who):4d}")
    print("# domain package edges")
    for source, dest in sorted(edges):
        allowed = "ok" if dest in ALLOWED_DOMAIN_EDGES.get(source, set()) else "VIOLATION"
        print(f"{source:14s} -> {dest:14s} {allowed}")
    unmapped = sorted(
        path.stem
        for path in (DOMAIN_ROOT.rglob("*.py"))
        if "__pycache__" not in path.parts
        and path.stem != "__init__"
        and (DOMAIN_ROOT / path.stem).is_dir() is False
        and path.stem not in FLAT_MODULE_PACKAGE
    )
    print(f"# unmapped flat modules: {unmapped or 'none'}")
    print(f"# shims declared: {len(SHIMMED_MODULES)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="domain/service layering gate")
    parser.add_argument("--report", action="store_true", help="print the inventory and exit 0")
    parser.add_argument("--domain-edges", action="store_true", help="only check package edges")
    parser.add_argument("--shims", action="store_true", help="only check compat shims")
    parser.add_argument("--self-test", action="store_true", help="plant violations in a temp tree and detect them")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()
    if args.report:
        report()
        return 0

    only = args.domain_edges or args.shims
    problems: list[str] = []
    if not only or args.domain_edges:
        if not args.shims:
            problems += check_upward()
    if not only or args.domain_edges:
        problems += check_domain_edges()
    if not only or args.shims:
        problems += check_shims()

    if problems:
        for problem in problems:
            print(f"VIOLATION: {problem}")
        print(f"{len(problems)} layering violation(s)")
        return 1
    print("layering OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
