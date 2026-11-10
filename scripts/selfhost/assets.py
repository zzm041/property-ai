"""Offline file identity checks; never downloads or imports a model."""
import hashlib
from pathlib import Path, PurePosixPath
import re


def verify_assets(root, manifest):
    root = Path(root).resolve()
    count = total = 0
    seen = set()
    controlled = set()
    for entry in manifest["files"]:
        name = entry["path"]
        relative = PurePosixPath(name)
        if not name or "\\" in name or ":" in name or relative.is_absolute() or ".." in relative.parts or name in seen:
            raise ValueError("ASSET_PATH")
        seen.add(name)
        controlled.add(relative.parent)
        path = (root / name).resolve()
        if not path.is_relative_to(root):
            raise ValueError("ASSET_PATH")
        if not path.is_file():
            raise ValueError("ASSET_MISSING")
        size = entry["size"]
        if type(size) is not int or size <= 0 or path.stat().st_size != size:
            raise ValueError("ASSET_SIZE")
        mode = entry["mode"]
        if mode not in ("sha256", "git-blob-sha1"):
            raise ValueError("ASSET_MODE")
        if not re.fullmatch(r"[a-f0-9]{64}" if mode == "sha256" else r"[a-f0-9]{40}", entry["expected"]):
            raise ValueError("ASSET_DIGEST")
        digest = hashlib.sha256() if mode == "sha256" else hashlib.sha1()
        if mode == "git-blob-sha1":
            digest.update(f"blob {size}\0".encode("ascii"))
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != entry["expected"]:
            raise ValueError("ASSET_DIGEST")
        count += 1
        total += size
    if not count:
        raise ValueError("ASSET_EMPTY")
    for directory in controlled:
        actual = {path.relative_to(root).as_posix() for path in (root / directory).rglob("*") if path.is_file() or path.is_symlink()}
        if actual - seen:
            # from_pretrained can prefer an extra safetensors/tokenizer file.
            # A valid listed hash is insufficient if a shadow file is present.
            raise ValueError("ASSET_EXTRA")
    return {"verified_files": count, "verified_bytes": total}
