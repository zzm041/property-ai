"""A paired runtime must use exactly the reviewed snapshot, not nearby WIP."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validator():
    path = ROOT / "scripts/ops/verify_runtime_artifact.py"
    assert path.is_file(), "runtime artifact validator is not implemented"
    spec = importlib.util.spec_from_file_location("runtime_artifact", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.verify_artifact


@pytest.fixture
def artifact(tmp_path):
    files = {"backend/app/domain/ai.py": b"version = 'synthetic'\n"}
    for name in ("local_ai_http.py", "local_ai_models.py", "local_ai_process.py", "local_ai_runtime_control.py"):
        files["scripts/ops/" + name] = b"# synthetic reviewed source\n"
    root = tmp_path / "tmp/delivery-ai-artifacts/synthetic"
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    build = tmp_path / "reports/build/result.json"
    build.parent.mkdir(parents=True)
    build.write_text(json.dumps(dict(status="CANDIDATE_SOURCE_AND_DEPENDENCIES_VERIFIED", cleanup_errors=[],
        candidate_image_id="sha256:" + "a" * 64,
        source_sha256={"app/domain/ai.py": digest(root / "backend/app/domain/ai.py")})), encoding="utf-8")
    manifest = tmp_path / "reports/artifact.json"
    manifest.write_text(json.dumps(dict(format="property-ai-runtime-artifact-v1", artifact_id="synthetic",
        root="tmp/delivery-ai-artifacts/synthetic", backend_manifest="reports/build/result.json",
        backend_manifest_sha256=digest(build), candidate_image_id="sha256:" + "a" * 64,
        files={p: digest(root / p) for p in files})), encoding="utf-8")
    return tmp_path, root, manifest


def test_reviewed_complete_artifact_resolves_without_writing(artifact):
    repo, root, manifest = artifact
    before = {str(p): p.read_bytes() for p in repo.rglob("*") if p.is_file()}
    result = validator()(repo, manifest, digest(manifest))
    assert Path(result["root"]) == root
    assert result["candidate_image_id"] == "sha256:" + "a" * 64
    assert {str(p): p.read_bytes() for p in repo.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("change", ["hash", "missing", "extra", "bytecode", "traversal", "backend_mismatch", "manifest_hash", "paired_source", "root_escape", "backend_manifest"])
def test_changed_or_unreviewed_artifact_is_rejected(artifact, change):
    repo, root, manifest = artifact
    expected = digest(manifest)
    data = json.loads(manifest.read_text())
    if change == "hash": (root / "backend/app/domain/ai.py").write_bytes(b"changed")
    if change == "missing": (root / "scripts/ops/local_ai_http.py").unlink()
    if change == "extra": (root / "scripts/ops/unreviewed.py").write_bytes(b"unreviewed")
    if change == "bytecode":
        cache = root / "scripts/ops/__pycache__"
        cache.mkdir()
        (cache / "local_ai_http.cpython-312.pyc").write_bytes(b"unreviewed cache")
    if change == "traversal":
        data["files"]["../outside.py"] = "b" * 64
    if change == "backend_mismatch":
        data["candidate_image_id"] = "sha256:" + "b" * 64
    if change == "manifest_hash": expected = "0" * 64
    if change == "paired_source":
        path = root / "backend/app/domain/ai.py"
        path.write_bytes(b"changed but manifest also changed")
        data["files"]["backend/app/domain/ai.py"] = digest(path)
    if change == "root_escape": data["root"] = "tmp/delivery-ai-artifacts/../../unreviewed"
    if change == "backend_manifest": (repo / "reports/build/result.json").write_bytes(b"{}")
    if change in {"traversal", "backend_mismatch", "paired_source", "root_escape"}:
        manifest.write_text(json.dumps(data), encoding="utf-8")
        expected = digest(manifest)
    with pytest.raises(ValueError):
        validator()(repo, manifest, expected)


def test_artifact_link_is_rejected(artifact):
    repo, root, manifest = artifact
    target = root / "scripts/ops/local_ai_http.py"
    outside = repo / "external.py"
    outside.write_bytes(target.read_bytes())
    target.unlink()
    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip("This Windows account cannot create symlinks")
    with pytest.raises(ValueError):
        validator()(repo, manifest, digest(manifest))
