"""Release-pack isolation checks. Does not touch the operator project.config.json."""

import importlib.util
import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
PACK = REPO / "scripts" / "ops" / "pack_miniprogram_release.py"
USER_CONFIG = REPO / "miniprogram" / "project.config.json"


def _load_pack():
    spec = importlib.util.spec_from_file_location("pack_miniprogram_release", PACK)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_release_pack_excludes_dev_login() -> None:
    module = _load_pack()
    original = USER_CONFIG.read_bytes()
    destination = REPO / "tmp" / "pytest-release-pack"
    if destination.exists():
        shutil.rmtree(destination)
    inventory = module.pack_release(destination)
    try:
        assert USER_CONFIG.read_bytes() == original
        assert inventory["dev_login_files"] == []
        assert inventory["dev_login_page_registered"] is False
        assert inventory["ordinary_login_has_seed_buttons"] is False
        assert inventory["env_local_copied"] is False
        assert inventory["user_project_config_copied"] is False
        assert inventory["test_env_note_present"] is True
        assert not (destination / "pages" / "dev-login").exists()
        assert "pages/dev-login/dev-login" not in inventory["app_json_pages"]
        assert (destination / "pages" / "login" / "login.wxml").exists()
        assert (destination / "pages" / "password-reset" / "password-reset.wxml").exists()
        packed_config = (destination / "project.config.json").read_text(encoding="utf-8")
        assert "wxcfa5fd7dc03cac56" not in packed_config
        assert (REPO / "miniprogram" / "pages" / "dev-login" / "dev-login.js").exists()
        assert (destination / ".property-ai-release-pack").exists()
    finally:
        shutil.rmtree(destination, ignore_errors=True)


def test_release_pack_refuses_protected_and_unmanaged_directories() -> None:
    module = _load_pack()
    app_json = REPO / "miniprogram" / "app.json"
    original = app_json.read_bytes()
    with pytest.raises(RuntimeError, match="protected path"):
        module.pack_release(REPO / "miniprogram")
    assert app_json.read_bytes() == original
    with pytest.raises(RuntimeError, match="protected path"):
        module.pack_release(REPO)
    victim = REPO / "reports" / "auth-identity-ai-001" / "pack-safety-keep"
    if victim.exists():
        shutil.rmtree(victim)
    victim.mkdir(parents=True)
    keep = victim / "KEEP_ME.txt"
    keep.write_text("keep", encoding="utf-8")
    try:
        with pytest.raises(RuntimeError, match="unmanaged directory"):
            module.pack_release(victim)
        assert keep.exists()
        assert keep.read_text(encoding="utf-8") == "keep"
    finally:
        shutil.rmtree(victim, ignore_errors=True)
