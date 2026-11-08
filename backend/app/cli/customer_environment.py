"""Export only declared application settings, excluding infrastructure secrets."""
import argparse
from io import StringIO
import os
from pathlib import Path
import re

from dotenv.parser import parse_stream

from app.core.config import Settings


def export(source: Path, target: Path) -> int:
    with source.open("r", encoding="utf-8-sig") as stream:
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("CUSTOMER_ENV_SIZE")
    values = {}
    for item in parse_stream(StringIO(raw)):
        if item.error:
            raise ValueError("CUSTOMER_ENV_INPUT")
        if item.key is None:
            continue
        if (item.value is None or item.key in values or not re.fullmatch(r"[A-Z][A-Z0-9_]*", item.key)
                or any(c in item.value for c in ("\n", "\r", "\0")) or "${" in item.value):
            raise ValueError("CUSTOMER_ENV_INPUT")
        values[item.key] = item.value
    if values.get("APP_ENV") not in {"production", "staging"} or values.get("DEV_AUTH_ENABLED", "").lower() != "false":
        raise ValueError("CUSTOMER_ENV_MODE")
    allowed = {name.upper() for name in Settings.model_fields}
    output = {key: value for key, value in values.items() if key in allowed}
    # Raw Compose env_file preserves literal dollar signs, hashes and quotes;
    # there is no ambient host-variable interpolation in this export.
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write("# Generated application-only settings. Private; never commit.\n")
        stream.writelines(f"{key}={value}\n" for key, value in sorted(output.items()))
    return len(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        count = export(args.source, args.output)
    except (ValueError, OSError):
        print("CUSTOMER_ENV_EXPORT_FAILED")
        return 2
    print(f"CUSTOMER_ENV_EXPORTED {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
