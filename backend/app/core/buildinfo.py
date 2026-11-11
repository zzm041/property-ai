"""Baked build provenance so "which code is running" is answerable from outside.

PA-116: the delivery image used to carry two copies of ``app`` (``/app/app`` from
``COPY backend/ /app/`` and the wheel-installed copy in ``site-packages``), so the
effective module depended on the process working directory and a stale container
could silently run pre-fix code.  The image now ships a single copy, and this
module reports the facts that prove it: the source commit baked at build time,
every ``app`` package still reachable through ``sys.path``, and the digests of
the contract modules whose regressions PA-001 / PA-101-A care about.
"""

from __future__ import annotations

import hashlib
import sys
from functools import lru_cache
from pathlib import Path

import app as app_package
from app.domain import ai as ai_domain
from app.domain.ai import models as ai_models_domain
from app.domain.ai import rules as ai_rules_domain

UNKNOWN = "unknown"

#: Files whose content decides the analysis contract; reported by name -> module.
#:
#: S4/PA-116: these used to point at the flat ``domain/ai.py`` and
#: ``domain/observed_event.py`` modules.  Both are now compatibility shims, so a
#: digest of them says nothing about the contract (and they disappear in phase
#: two).  Point at the package files that actually carry it: ``models`` holds the
#: observed-event contract and labels (PA-001), ``rules`` holds the skill aliases,
#: prompt version and label matching (PA-001 / PA-101-A).
CONTRACT_MODULES: dict[str, object] = {
    "domain/ai/models.py": ai_models_domain,
    "domain/ai/rules.py": ai_rules_domain,
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _app_copies() -> list[str]:
    """Every importable ``app`` package on ``sys.path``.

    The probe is ``app/__init__.py``: any copy of the package has it, including
    the pre-PA-116 ``/app/app`` duplicate, and it survives the shim removal in
    phase two (the old probe looked for ``app/domain/observed_event.py``, which is
    a shim today and gone tomorrow).
    """

    copies: list[str] = []
    for entry in sys.path:
        # An empty entry means "current working directory", which is exactly how
        # the pre-PA-116 image let ``/app/app`` win over site-packages.
        base = Path(entry) if entry else Path.cwd()
        candidate = base / "app" / "__init__.py"
        if candidate.is_file():
            copies.append(str(base / "app"))
    return sorted(set(copies))


@lru_cache(maxsize=1)
def code_version() -> str:
    """Which code wrote an audit row, as a single comparable string.

    F-7 (t214): the quarantine probe has to know whether the refusal it finds was
    produced by *this* code. ``build_info()["source_sha"]`` is baked at image
    build time and is ``unknown`` outside a built image (tests, a bare venv), so
    the in-tree digest of the two contract modules is appended as a fallback -
    otherwise every local run would share one version and the probe could not
    tell a fixed build from a broken one.
    """

    info = build_info()
    sha = str(info.get("source_sha") or UNKNOWN)
    if sha != UNKNOWN:
        return sha[:64]
    files = info.get("files") or {}
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(name.encode("utf-8"))
        digest.update(str(files[name]["sha256"]).encode("utf-8"))
    return f"tree-{digest.hexdigest()}"[:64]


@lru_cache(maxsize=1)
def build_info() -> dict[str, object]:
    """Static provenance; computed once per process (file reads only)."""

    import os

    files: dict[str, object] = {}
    for name, module in CONTRACT_MODULES.items():
        path = Path(module.__file__)  # type: ignore[attr-defined]
        files[name] = {"path": str(path), "sha256": _sha256(path)}

    prompt_versions = {
        "work_order": getattr(ai_domain, "WORK_ORDER_ANALYSIS_PROMPT_VERSION", UNKNOWN),
    }
    try:  # vision/rag contracts are optional for the analysis worker
        from app.domain.work_order import VISION_ANALYSIS_PROMPT_VERSION

        prompt_versions["vision"] = VISION_ANALYSIS_PROMPT_VERSION
    except Exception:  # pragma: no cover - optional dependency surface
        prompt_versions["vision"] = UNKNOWN

    return {
        "source_sha": os.environ.get("PROPERTY_AI_SOURCE_SHA") or UNKNOWN,
        "app_package": str(Path(app_package.__file__).parent),
        "app_copies": _app_copies(),
        "shadow_source_tree": _shadow_source_tree(),
        "files": files,
        "prompt_versions": prompt_versions,
    }


def _shadow_source_tree() -> str | None:
    """The pre-PA-116 second copy that used to win through ``sys.path[0]=''``."""

    legacy = Path("/app/app")
    try:
        return str(legacy) if legacy.exists() else None
    except OSError:  # pragma: no cover - defensive for exotic filesystems
        return None
