"""Portable install controls: no services, downloads or inference."""
import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from scripts.selfhost.assets import verify_assets
from scripts.selfhost.config import RuntimeConfig


def manifest(tmp_path, mode="sha256"):
    target = tmp_path / "model" / "revision" / "weights"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"model-data")
    payload = b"model-data" if mode == "sha256" else b"blob 10\0model-data"
    digest = hashlib.sha256(payload) if mode == "sha256" else hashlib.sha1(payload)
    return {"files": [{"path": "model/revision/weights", "size": 10,
                       "mode": mode, "expected": digest.hexdigest()}]}


@pytest.mark.parametrize("mode", ["sha256", "git-blob-sha1"])
def test_hashes_and_missing_file(tmp_path, mode):
    spec = manifest(tmp_path, mode)
    assert verify_assets(tmp_path, spec)["verified_files"] == 1
    (tmp_path / spec["files"][0]["path"]).unlink()
    with pytest.raises(ValueError, match="ASSET_MISSING"):
        verify_assets(tmp_path, spec)


def test_same_size_corruption_is_detected(tmp_path):
    spec = manifest(tmp_path)
    (tmp_path / spec["files"][0]["path"]).write_bytes(b"evil-data!")
    with pytest.raises(ValueError, match="ASSET_DIGEST"):
        verify_assets(tmp_path, spec)


@pytest.mark.parametrize("name", ["model.safetensors", "tokenizer.json"])
def test_extra_loading_files_cannot_shadow_verified_assets(tmp_path, name):
    spec = manifest(tmp_path)
    (tmp_path / "model/revision" / name).write_text("unverified")
    with pytest.raises(ValueError, match="ASSET_EXTRA"):
        verify_assets(tmp_path, spec)


def test_unrelated_models_are_not_forbidden(tmp_path):
    spec = manifest(tmp_path)
    (tmp_path / "other-model").mkdir()
    (tmp_path / "other-model" / "file").write_text("unrelated")
    assert verify_assets(tmp_path, spec)["verified_files"] == 1


@pytest.mark.parametrize("path", ["../outside", "/outside", "C:/outside", "a/../../outside", "a\\..\\outside"])
def test_manifest_cannot_escape_root(tmp_path, path):
    spec = manifest(tmp_path)
    spec["files"][0]["path"] = path
    with pytest.raises(ValueError, match="ASSET_PATH"):
        verify_assets(tmp_path, spec)


def config(tmp_path):
    token = tmp_path / "token"
    token.write_text("a" * 48)
    return {"model_root": "models", "bge_python": sys.executable,
            "asr_python": sys.executable, "token_file": "token",
            "ollama_url": "http://127.0.0.1:11435", "ollama_version": "0.34.4",
            "chat_model": "private-qwen", "chat_digest": "b" * 64,
            "bind": "127.0.0.1", "port": 11439}


def test_relative_config_resolves_against_file_not_cwd(tmp_path):
    value = RuntimeConfig.from_dict(config(tmp_path), tmp_path)
    assert value.model_root == tmp_path / "models"
    assert value.token_file == tmp_path / "token"
    assert value.token() == "a" * 48
    assert "a" * 48 not in repr(value)


@pytest.mark.parametrize("url", ["http://example.com:11434", "http://8.8.8.8", "http://127.0.0.1@8.8.8.8", "http://127.0.0.1/api", "http://127.0.0.1?x=y", "http://169.254.169.254", "http://0.0.0.0"])
def test_upstream_cannot_target_external_or_metadata_service(tmp_path, url):
    data = config(tmp_path)
    data["ollama_url"] = url
    with pytest.raises(ValueError, match="CONFIG"):
        RuntimeConfig.from_dict(data, tmp_path)


@pytest.mark.parametrize("field,value", [("port", True), ("timeout_seconds", float("nan")), ("max_body_bytes", 0), ("bind", "0.0.0.0"), ("chat_digest", "latest")])
def test_invalid_limits_and_public_bind_fail(tmp_path, field, value):
    data = config(tmp_path)
    data[field] = value
    with pytest.raises(ValueError, match="CONFIG"):
        RuntimeConfig.from_dict(data, tmp_path)


def test_secret_must_be_explicit_and_strong(tmp_path):
    value = RuntimeConfig.from_dict(config(tmp_path), tmp_path)
    value.token_file.write_text("replace-me")
    with pytest.raises(ValueError, match="CONFIG_TOKEN"):
        value.token()
