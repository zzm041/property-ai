"""PA-116: build provenance must be answerable from outside the container."""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.core import buildinfo
from app.domain import ai as ai_domain
from app.domain import observed_event as observed_event_domain


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_contract_digests_match_the_imported_modules() -> None:
    info = buildinfo.build_info()
    files = info["files"]
    assert isinstance(files, dict)
    assert files["domain/ai.py"]["sha256"] == _digest(Path(ai_domain.__file__))
    assert files["domain/observed_event.py"]["sha256"] == _digest(
        Path(observed_event_domain.__file__)
    )


def test_prompt_versions_come_from_the_contract_modules() -> None:
    info = buildinfo.build_info()
    versions = info["prompt_versions"]
    assert isinstance(versions, dict)
    assert versions["work_order"] == ai_domain.WORK_ORDER_ANALYSIS_PROMPT_VERSION


def test_app_copies_lists_every_importable_app_package() -> None:
    info = buildinfo.build_info()
    copies = info["app_copies"]
    assert isinstance(copies, list)
    # The probe has to be a plain module of the ``app`` package: ``app.domain.ai``
    # became a package in S3, so its ``__file__`` no longer sits one level below
    # ``app``.  The assertion is the same: the directory holding ``app`` is listed.
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
