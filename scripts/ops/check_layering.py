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

# Legacy module -> package that now owns it. S1..S4 used this to require a thin
# re-export; S8 deleted the whole compat layer, so an entry here now means the
# opposite: the file must NOT exist. Anything reappearing at an old path is a
# half-finished revert or a new shortcut, and both rebuild the "two names for
# one module" problem this split removed.
SHIMMED_MODULES: dict[str, str] = {
    "app.domain.iam": "identity",
    "app.domain.phone": "identity",
    "app.domain.resident": "identity",
    "app.domain.property": "identity",
    # S2: the work_order domain modules moved into app.domain.work_order.
    "app.domain.work_order": "work_order",
    "app.domain.work_order_visibility": "work_order",
    "app.domain.receipt_handling": "work_order",
    "app.domain.diagnosis": "work_order",
    "app.domain.vision": "work_order",
    # S3: the analysis domain modules moved into app.domain.ai.
    "app.domain.ai": "ai",
    "app.domain.observed_event": "ai",
    # S4: knowledge/governance/dev moved into their own packages.
    "app.domain.knowledge": "knowledge",
    "app.domain.audit": "governance",
    "app.domain.incident": "governance",
    "app.domain.announcement": "governance",
    "app.domain.operations": "governance",
    "app.domain.dev_seed": "dev",
}

SHIM_MAX_EFFECTIVE_LINES = 8

# Legacy service modules that S5 replaced with an alias module. The alias must
# stay a pure indirection: `import sys` plus `sys.modules[__name__] = _module`,
# so `__file__`, `inspect.getsource` and monkeypatching all keep resolving to
# the single implementation inside `app.service.identity`.
SHIMMED_SERVICE_MODULES: dict[str, str] = {
    f"app.service.{name}": name
    for name in (
        "auth",
        "rbac",
        "rbac_mutation",
        "credential_auth",
        "wechat_auth",
        "identity_recovery",
        "identity_write_guard",
        "organization",
        "resident",
        "property",
        "property_admin",
        # S6: the work_order service group moved into app.service.work_order/.
        "sla",
        "sla_policy",
        "description_suggestion",
        "media_scan",
        "analysis_attempt",
    )
}


# Legacy provider modules that S7 replaced with an alias module. `local_ai` and the
# three packages that kept their own name (supervisor_agent, file_scanner,
# knowledge_parser) need no entry: the package *is* the old import path.
SHIMMED_PROVIDER_MODULES: dict[str, str] = {
    # S7: the LangChain LLM backend moved under its kind, app.provider.llm.
    "app.provider.langchain_llm": "llm.langchain_llm",
}


# What may live directly under backend/app/domain/. Everything else must be
# inside one of the packages listed in ALLOWED_DOMAIN_EDGES: a flat module that
# nobody maps is either a forgotten move or a new dependency shortcut, and both
# turn the package graph back into the pile of files this split is undoing.
# After S8 nothing but the package marker may sit directly under app/domain/.
DOMAIN_TOP_LEVEL_ALLOWED_FILES: frozenset[str] = frozenset({"__init__.py"})
DOMAIN_TOP_LEVEL_ALLOWED_PACKAGES: frozenset[str] = frozenset(ALLOWED_DOMAIN_EDGES)


# What may import what *inside* backend/app/provider/. `factory` is the
# composition root and may assemble any kind; a kind may use the shared
# transport/infrastructure modules (flattened on purpose: they sit below every
# kind) and otherwise only the kinds declared here. A new kind->kind edge has to
# be written down, because that is the shortcut that turns a set of
# interchangeable providers into a hierarchy nobody can test in isolation.
_ALL_SHIM_MODULES: frozenset[str] = frozenset(
    list(SHIMMED_MODULES) + list(SHIMMED_SERVICE_MODULES) + list(SHIMMED_PROVIDER_MODULES)
)
PROVIDER_ROOT = REPO_ROOT / "backend" / "app" / "provider"
PROVIDER_SHARED_MODULES: frozenset[str] = frozenset({"errors", "http", "usage"})
PROVIDER_COMPOSITION_ROOT = "factory"
# Allowed kind -> kind edges, and only these. Everything else is forbidden:
# the kinds are interchangeable adapters, so one of them reaching into another
# means the abstraction leaked (a reranker that knows about ASR is not a
# reranker). Cross-cutting modules every kind may import live below.
PROVIDER_ALLOWED_EDGES: dict[str, frozenset[str]] = {
    # The supervisor planner drives the LLM transport.
    "supervisor_agent": frozenset({"llm"}),
    # asr/vision pull the original bytes out of the storage provider: they are
    # consumers of a store, not layers on top of it.
    "asr": frozenset({"storage"}),
    "vision": frozenset({"storage"}),
}
# Targets outside the provider layer are not edges of this graph, so they are
# always fine: a kind may import app.core.*, app.domain.* and anything else
# below the provider layer. The two names below are accepted defensively in
# case a shared provider/core or provider/domain package ever appears.
PROVIDER_OPEN_DESTINATIONS: frozenset[str] = frozenset({"core", "domain"})


def provider_package(module: str) -> str | None:
    """app.provider.<kind>[.<rest>] -> <kind>"""
    parts = module.split(".")
    if len(parts) < 3 or parts[0] != "app" or parts[1] != "provider":
        return None
    return parts[2]


def check_provider_edges(
    provider_root: pathlib.Path | None = None,
    backend_root: pathlib.Path | None = None,
) -> list[str]:
    root = backend_root or (REPO_ROOT / "backend")
    problems: list[str] = []
    for path in sorted((provider_root or PROVIDER_ROOT).rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        importer = module_name(path, root)
        if importer in _ALL_SHIM_MODULES:
            # An alias module is indirection, not an edge: it re-exports the real
            # package and is deleted with the rest of the compat layer.
            continue
        source = provider_package(importer)
        if source is None:
            continue
        for target, line, nested in imports_of(parse(path), importer):
            dest = provider_package(target)
            if dest is None or dest == source:
                continue
            if source == PROVIDER_COMPOSITION_ROOT:
                continue
            if dest in PROVIDER_SHARED_MODULES or dest in PROVIDER_OPEN_DESTINATIONS:
                continue
            if dest == PROVIDER_COMPOSITION_ROOT:
                # any kind may reach the composition root by name; factory
                # itself is the one module allowed to import every kind.
                continue
            if dest in PROVIDER_ALLOWED_EDGES.get(source, frozenset()):
                continue
            kind = "lazy " if nested else ""
            problems.append(
                f"{path.relative_to(root)}:{line} provider.{source} -> provider.{dest} "
                f"({kind}import; declared kind edges: "
                f"{sorted(PROVIDER_ALLOWED_EDGES.get(source, frozenset())) or 'none'})"
            )
    return problems


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


def domain_tree_problems(domain_root: pathlib.Path | None = None) -> list[str]:
    """Entries directly under app/domain/ that no package claims."""
    root = domain_root or DOMAIN_ROOT
    problems: list[str] = []
    for entry in sorted(root.iterdir()):
        name = entry.name
        if name == "__pycache__":
            continue
        if entry.is_dir():
            if name not in DOMAIN_TOP_LEVEL_ALLOWED_PACKAGES:
                problems.append(
                    f"domain/{name}/: package is not declared in ALLOWED_DOMAIN_EDGES "
                    f"(declared: {sorted(DOMAIN_TOP_LEVEL_ALLOWED_PACKAGES)})"
                )
            continue
        if name not in DOMAIN_TOP_LEVEL_ALLOWED_FILES:
            problems.append(
                f"domain/{name}: unmapped top-level module — move it into a package and "
                f"register it in FLAT_MODULE_PACKAGE, or the layering gate cannot reason about it"
            )
    return problems


def _legacy_present(legacy: str) -> list[str]:
    """The compat layer is deleted: existence at the old path is the violation."""
    path = REPO_ROOT / "backend" / f"{legacy.replace('.', '/')}.py"
    if path.exists():
        return [
            f"{legacy}: the compat shim is back — import the package that owns it "
            f"(the alias layer was deleted in S8)"
        ]
    return []


def check_shims() -> list[str]:
    """No legacy path may exist any more (see the note above SHIMMED_MODULES)."""
    problems: list[str] = []
    for legacy in sorted(
        list(SHIMMED_MODULES) + list(SHIMMED_SERVICE_MODULES) + list(SHIMMED_PROVIDER_MODULES)
    ):
        problems += _legacy_present(legacy)
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
        provider = backend / "app" / "provider"
        for package in ("llm", "vision", "asr", "supervisor_agent", "errors"):
            (provider / package).mkdir(parents=True)
            (provider / package / "__init__.py").write_text("", encoding="utf-8")
        # allowed: the planner drives the llm transport, factory assembles kinds
        (provider / "supervisor_agent" / "ok.py").write_text(
            "from app.provider.llm import client  # noqa: F401\n", encoding="utf-8"
        )
        (provider / "factory.py").write_text(
            "from app.provider.vision import client  # noqa: F401\n"
            "from app.provider.errors import ProviderError  # noqa: F401\n",
            encoding="utf-8",
        )
        # planted 4: an interchangeable kind grabbing another kind
        (provider / "vision" / "wrong_kind.py").write_text(
            "from app.provider.llm import client  # noqa: F401\n", encoding="utf-8"
        )
        # planted 5: the same leak the other way round - the LLM transport must
        # not know that ASR exists (review's case: provider/llm/leak.py -> asr)
        (provider / "llm" / "leak.py").write_text(
            "from app.provider.asr import client  # noqa: F401\n", encoding="utf-8"
        )
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
        # planted 3: a flat module no package claims (the gate used to stay green)
        (domain / "rogue.py").write_text("VALUE = 1\n", encoding="utf-8")
        upward = check_upward(domain, backend)
        edges = check_domain_edges(domain, backend)
        tree = domain_tree_problems(domain)
        kind_edges = check_provider_edges(provider, backend)

    ok = (
        len(upward) == 1
        and "leaky.py" in upward[0]
        and "lazy import" in upward[0]
        and len(edges) == 1
        and "wrong_edge.py" in edges[0]
        and "domain.knowledge -> domain.work_order" in edges[0]
        and len(tree) == 1
        and "rogue.py" in tree[0]
        and len(kind_edges) == 2
        and any("wrong_kind.py" in item and "provider.vision -> provider.llm" in item
                for item in kind_edges)
        and any("leak.py" in item and "provider.llm -> provider.asr" in item
                for item in kind_edges)
    )
    for problem in upward + edges + tree + kind_edges:
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
    unmapped = [p.split(":", 1)[0] for p in domain_tree_problems()]
    print(f"# unmapped or undeclared domain entries: {unmapped or 'none'}")
    print(f"# shims declared: {len(SHIMMED_MODULES)} domain, "
          f"{len(SHIMMED_SERVICE_MODULES)} service, "
          f"{len(SHIMMED_PROVIDER_MODULES)} provider")
    kind_edges: set[tuple[str, str]] = set()
    for path in sorted(PROVIDER_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        importer = module_name(path)
        source = provider_package(importer)
        if source is None:
            continue
        for target, _line, _nested in imports_of(parse(path), importer):
            dest = provider_package(target)
            if dest and dest != source:
                kind_edges.add((source, dest))
    print("# provider kind edges")
    for source, dest in sorted(kind_edges):
        allowed = (
            source == PROVIDER_COMPOSITION_ROOT
            or dest in PROVIDER_SHARED_MODULES
            or dest in PROVIDER_ALLOWED_EDGES.get(source, frozenset())
        )
        print(f"{source:18s} -> {dest:18s} {'ok' if allowed else 'VIOLATION'}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="domain/service layering gate")
    parser.add_argument("--report", action="store_true", help="print the inventory and exit 0")
    parser.add_argument("--domain-edges", action="store_true", help="only check package edges")
    parser.add_argument("--shims", action="store_true", help="only check compat shims")
    parser.add_argument("--tree", action="store_true", help="only check the domain tree (unmapped modules/packages)")
    parser.add_argument("--provider-edges", action="store_true", help="only check provider kind edges")
    parser.add_argument("--self-test", action="store_true", help="plant violations in a temp tree and detect them")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()
    if args.report:
        report()
        return 0

    only = args.domain_edges or args.shims or args.tree or args.provider_edges
    problems: list[str] = []
    if not only or args.domain_edges:
        problems += check_upward()
        problems += check_domain_edges()
    if not only or args.shims:
        problems += check_shims()
    if not only or args.tree:
        problems += domain_tree_problems()
    if not only or args.provider_edges:
        problems += check_provider_edges()

    if problems:
        for problem in problems:
            print(f"VIOLATION: {problem}")
        print(f"{len(problems)} layering violation(s)")
        return 1
    print("layering OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
