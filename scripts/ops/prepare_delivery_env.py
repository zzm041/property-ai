"""Create one ignored local delivery-check env without printing secrets."""
from __future__ import annotations

from pathlib import Path
import argparse
import secrets


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "deploy" / ".env.delivery.example"
TARGET = ROOT / "deploy" / ".env.delivery"
BOOTSTRAP_SOURCE = ROOT / "deploy" / ".env.delivery.bootstrap.example"
BOOTSTRAP_TARGET = ROOT / "deploy" / ".env.delivery.bootstrap"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bootstrap-only", action="store_true")
    args = parser.parse_args()
    if BOOTSTRAP_TARGET.exists() or (TARGET.exists() and not args.bootstrap_only):
        raise RuntimeError("delivery env already exists and will not be overwritten")
    if not args.bootstrap_only:
        text = SOURCE.read_text(encoding="utf-8")
        replacements = {
            "replace-with-at-least-32-random-characters": secrets.token_urlsafe(48),
            "replace-mysql-password": secrets.token_urlsafe(30),
            "replace-mysql-root-password": secrets.token_urlsafe(30),
            "replace-redis-password": secrets.token_urlsafe(30),
            "replace-rabbit-password": secrets.token_urlsafe(30),
            "replace-minio-password": secrets.token_urlsafe(30),
        }
        for old, new in replacements.items():
            text = text.replace(old, new)
        if "replace-" in text or "change-me" in text:
            raise RuntimeError("delivery template still contains an unresolved placeholder")
        TARGET.write_text(text, encoding="utf-8", newline="\n")
    bootstrap = BOOTSTRAP_SOURCE.read_text(encoding="utf-8")
    bootstrap = bootstrap.replace(
        "replace-synthetic-or-authorized-phone", "+86139" + str(secrets.randbelow(10**8)).zfill(8)
    ).replace("replace-bootstrap-password", secrets.token_urlsafe(30))
    BOOTSTRAP_TARGET.write_text(bootstrap, encoding="utf-8", newline="\n")
    print("created ignored delivery env files with generated local-only secrets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
