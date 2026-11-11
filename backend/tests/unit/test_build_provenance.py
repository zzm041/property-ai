"""PA-116: build provenance must be answerable from outside the container."""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.core import buildinfo
from app.domain import ai as ai_domain
from app.domain.ai import models as ai_models_domain
from app.domain.ai import rules as ai_rules_domain
from app.domain.work_order import VISION_ANALYSIS_PROMPT_VERSION


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_contract_digests_match_the_imported_modules() -> None:
    info = buildinfo.build_info()
    files = info["files"]
    assert isinstance(files, dict)
    # S4: the flat modules became shims, so the digests follow the package files
    # that actually carry the contract (see buildinfo.CONTRACT_MODULES).
    assert files["domain/ai/models.py"]["sha256"] == _digest(Path(ai_models_domain.__file__))
    assert files["domain/ai/rules.py"]["sha256"] == _digest(Path(ai_rules_domain.__file__))


def test_prompt_versions_come_from_the_contract_modules() -> None:
    info = buildinfo.build_info()
    versions = info["prompt_versions"]
    assert isinstance(versions, dict)
    assert versions["work_order"] == ai_domain.WORK_ORDER_ANALYSIS_PROMPT_VERSION
    # PA-172: build_info() reads the vision contract inside try/except, so a
    # broken import there degrades to "unknown" and nothing notices. Assert the
    # real value: the delivery image advertises this to /healthz and the worker.
    assert versions["vision"] == VISION_ANALYSIS_PROMPT_VERSION
    assert versions["vision"] != buildinfo.UNKNOWN


def test_app_copies_lists_every_importable_app_package() -> None:
    info = buildinfo.build_info()
    copies = info["app_copies"]
    assert isinstance(copies, list)
    # The probe is ``app/__init__.py`` (S4): it exists in every copy and survives
    # the shim removal, unlike the old ``app/domain/observed_event.py`` probe.
    assert str(Path(buildinfo.__file__).parents[1]) in copies
    assert len(copies) == len(set(copies))


def test_source_sha_comes_from_the_build_environment(monkeypatch) -> None:
    monkeypatch.setenv("PROPERTY_AI_SOURCE_SHA", "0f1e2d3c4b5a")
    buildinfo.build_info.cache_clear()
    try:
        assert buildinfo.build_info()["source_sha"] == "0f1e2d3c4b5a"
        monkeypatch.delenv("PROPERTY_AI_SOURCE_SHA")
        buildinfo.build_info.cache_clear()
        assert buildinfo.build_info()["source_sha"] == buildinfo.UNKNOWN
    finally:
        buildinfo.build_info.cache_clear()
