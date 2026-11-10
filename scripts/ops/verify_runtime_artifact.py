"""Read-only verification of a local AI snapshot paired with a backend image."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_path(root, relative):
    if not isinstance(relative, str) or not relative or '\\' in relative or ':' in relative:
        raise ValueError('INVALID_ARTIFACT_PATH')
    part = PurePosixPath(relative)
    if part.is_absolute() or any(x in {'.', '..', ''} for x in relative.split('/')):
        raise ValueError('INVALID_ARTIFACT_PATH')
    target = root.joinpath(*part.parts)
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError('ARTIFACT_PATH_ESCAPE')
    for path in (target, *target.parents):
        if path == root.parent:
            break
        if path.is_symlink() or path.is_junction():
            raise ValueError('ARTIFACT_LINK_REFUSED')
    return target


def verify_artifact(repo, manifest_path, expected_sha256):
    repo = Path(repo).resolve()
    manifest_path = Path(manifest_path).absolute()
    try:
        if not manifest_path.is_relative_to(repo):
            raise ValueError('MANIFEST_OUTSIDE_WORKSPACE')
        safe_path(repo, manifest_path.relative_to(repo).as_posix())
        if digest(manifest_path) != expected_sha256.lower():
            raise ValueError('MANIFEST_DIGEST_MISMATCH')
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if manifest['format'] != 'property-ai-runtime-artifact-v1':
            raise ValueError('ARTIFACT_FORMAT')
        root = safe_path(repo, manifest['root'])
        if not root.is_relative_to(repo / 'tmp/delivery-ai-artifacts') or not root.is_dir():
            raise ValueError('ARTIFACT_ROOT')
        build_path = safe_path(repo, manifest['backend_manifest'])
        if digest(build_path) != manifest['backend_manifest_sha256']:
            raise ValueError('BACKEND_MANIFEST_DRIFT')
        build = json.loads(build_path.read_text(encoding='utf-8'))
        if (build['status'] != 'CANDIDATE_SOURCE_AND_DEPENDENCIES_VERIFIED' or build['cleanup_errors']
                or build['candidate_image_id'] != manifest['candidate_image_id']):
            raise ValueError('BACKEND_CANDIDATE_MISMATCH')
        files = manifest['files']
        adapters = {'scripts/ops/' + name for name in (
            'local_ai_http.py', 'local_ai_models.py', 'local_ai_process.py', 'local_ai_runtime_control.py')}
        expected = {'backend/' + name: value for name, value in build['source_sha256'].items()
                    if name.startswith('app/')}
        if set(files) != set(expected) | adapters or not expected:
            raise ValueError('ARTIFACT_FILE_SET')
        for relative, checksum in files.items():
            path = safe_path(root, relative)
            if not isinstance(checksum, str) or re.fullmatch('[0-9a-f]{64}', checksum) is None:
                raise ValueError('ARTIFACT_DIGEST_FORMAT')
            if not path.is_file() or digest(path) != checksum:
                raise ValueError('ARTIFACT_FILE_DRIFT')
            if relative in expected and checksum != expected[relative]:
                raise ValueError('ARTIFACT_BACKEND_SOURCE_MISMATCH')
        actual = set()
        for path in root.rglob('*'):
            relative = path.relative_to(root).as_posix()
            safe_path(root, relative)
            if path.is_file():
                actual.add(relative)
        if actual != set(files):
            raise ValueError('UNREVIEWED_ARTIFACT_FILES')
        return dict(root=str(root), artifact_id=manifest['artifact_id'],
                    candidate_image_id=manifest['candidate_image_id'], files=files)
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError('INVALID_RUNTIME_ARTIFACT') from exc


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--sha256', required=True)
    args = parser.parse_args()
    verified = verify_artifact(Path(__file__).resolve().parents[2], args.manifest, args.sha256)
    print(json.dumps(verified))
