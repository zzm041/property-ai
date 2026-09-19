"""Build a local miniprogram release tree that excludes development login files.

Does not read, copy, stage, overwrite, or restore miniprogram/project.config.json.
Does not upload, submit, or publish.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / "miniprogram"
DEFAULT_OUT = REPO / "reports" / "auth-identity-ai-001" / "release-pack"
SENTINEL_NAME = ".property-ai-release-pack"
SKIP_NAMES = {
    "env.local.js",
    "project.config.json",
    "project.private.config.json",
    "node_modules",
    "miniprogram_npm",
}
DEV_LOGIN_MARKERS = ("pages/dev-login", "dev-login")
PROTECTED_PATHS = frozenset(
    {
        REPO.resolve(),
        SOURCE.resolve(),
        (REPO / "backend").resolve(),
        (REPO / "miniprogram").resolve(),
    }
)


def _should_skip(path: Path) -> bool:
    relative = path.relative_to(SOURCE).as_posix()
    if path.name in SKIP_NAMES:
        return True
    if relative.startswith("pages/dev-login/") or relative == "pages/dev-login":
        return True
    return False


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def assert_safe_pack_destination(destination: Path) -> Path:
    dest = destination.expanduser().resolve()
    if dest in PROTECTED_PATHS:
        raise RuntimeError(f"refusing to pack into protected path: {dest}")
    allowed_roots = ((REPO / "reports").resolve(), (REPO / "tmp").resolve())
    if not any(_is_relative_to(dest, root) and dest != root for root in allowed_roots):
        raise RuntimeError(f"release pack destination must be under reports/ or tmp/: {dest}")
    if dest.exists():
        sentinel = dest / SENTINEL_NAME
        if dest != DEFAULT_OUT.resolve() and not sentinel.is_file():
            raise RuntimeError(
                f"refusing to delete unmanaged directory without {SENTINEL_NAME}: {dest}"
            )
    return dest


def pack_release(destination: Path) -> dict[str, object]:
    if not SOURCE.is_dir():
        raise FileNotFoundError(SOURCE)
    dest = assert_safe_pack_destination(destination)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    (dest / SENTINEL_NAME).write_text("property-ai-release-pack\n", encoding="utf-8")
    copied: list[str] = []
    for path in SOURCE.rglob("*"):
        if _should_skip(path):
            continue
        relative = path.relative_to(SOURCE)
        target = dest / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied.append(relative.as_posix())

    app_json_path = dest / "app.json"
    app_json = json.loads(app_json_path.read_text(encoding="utf-8"))
    pages = [page for page in app_json.get("pages", []) if page != "pages/dev-login/dev-login"]
    app_json["pages"] = pages
    app_json_path.write_text(
        json.dumps(app_json, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    project_config = {
        "miniprogramRoot": "./",
        "projectname": "property-ai-release-check",
        "appid": "wx0000000000000000",
        "compileType": "miniprogram",
        "setting": {
            "es6": True,
            "minified": True,
            "postcss": False,
            "enhance": False,
        },
        "packOptions": {"ignore": [{"type": "folder", "value": "pages/dev-login"}]},
        "editorSetting": {},
    }
    (dest / "project.config.json").write_text(
        json.dumps(project_config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    copied.append("project.config.json")

    leftover = [
        path.relative_to(dest).as_posix()
        for path in dest.rglob("*")
        if path.is_file()
        and any(marker in path.relative_to(dest).as_posix() for marker in DEV_LOGIN_MARKERS)
    ]
    login_wxml = (dest / "pages" / "login" / "login.wxml").read_text(encoding="utf-8")
    env_js = (dest / "config" / "env.js").read_text(encoding="utf-8")
    inventory = {
        "source": str(SOURCE),
        "destination": str(dest),
        "file_count": len(copied),
        "files": sorted(copied),
        "app_json_pages": pages,
        "dev_login_files": leftover,
        "ordinary_login_has_seed_buttons": any(
            label in login_wxml for label in ("以住户登录", "以物业登录", "以员工登录")
        ),
        "env_local_copied": (dest / "config" / "env.local.js").exists(),
        "user_project_config_copied": False,
        "test_env_note_present": "测试环境" in env_js,
        "dev_login_page_registered": "pages/dev-login/dev-login" in pages,
        "sentinel": SENTINEL_NAME,
    }
    if leftover or inventory["ordinary_login_has_seed_buttons"] or inventory["dev_login_page_registered"]:
        raise RuntimeError(f"release pack still contains development login: {inventory}")
    if dest.resolve() == DEFAULT_OUT.resolve():
        (dest.parent / "release-pack-inventory.json").write_text(
            json.dumps(inventory, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return inventory


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()
    inventory = pack_release(Path(args.out))
    print(f"release_pack={inventory['destination']}")
    print(f"file_count={inventory['file_count']}")
    print("dev_login_files=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
