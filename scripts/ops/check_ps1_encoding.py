#!/usr/bin/env python3
"""Fail when a PowerShell script mixes non-ASCII text with a missing BOM.

PA-178 (t174/t186): Windows PowerShell 5.1 reads a BOM-less script as ANSI
(cp936 on this host). A Chinese comment that ends with a full-width full stop
(``。`` = ``E3 80 82``) leaves a lead byte that DBCS decoding pairs with the
following ``0x0A``, which swallows the newline - so the *next* line is silently
folded into the comment. ``deploy/work-ai-runtime.ps1`` lost
``$ProviderRuntimeControlPath = ...`` that way and the launcher died with
"cannot bind argument to parameter 'LiteralPath' because it is an empty string".

The rule is therefore: a ``*.ps1`` file that contains any non-ASCII code point
must start with a UTF-8 BOM. Files that are pure ASCII are fine either way,
because PS 5.1's ANSI read of ASCII is lossless.

Scope: the operational scripts (``deploy/*.ps1`` and ``scripts/**/*.ps1``) plus
any extra ``--path`` roots. ``reports/**`` is a frozen evidence archive and is
deliberately out of scope - rewriting historical artifacts would change the
hashes their own manifests pin.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import tempfile

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_ROOTS = ("deploy", "scripts")
UTF8_BOM = b"\xef\xbb\xbf"


def script_files(roots: list[pathlib.Path]) -> list[pathlib.Path]:
    found: list[pathlib.Path] = []
    for root in roots:
        if not root.exists():
            continue
        found.extend(sorted(root.rglob("*.ps1")))
    return sorted(set(found))


def _has_non_ascii(raw: bytes) -> bool:
    try:
        raw.decode("ascii")
    except UnicodeDecodeError:
        return True
    return False


def inspect_script(path: pathlib.Path) -> str | None:
    """Return a problem description, or ``None`` when the file is safe."""

    raw = path.read_bytes()
    if not _has_non_ascii(raw):
        return None
    if raw.startswith(UTF8_BOM):
        try:
            raw[len(UTF8_BOM) :].decode("utf-8")
        except UnicodeDecodeError as exc:
            return f"{path}: has a BOM but the body is not valid UTF-8 ({exc})"
        return None
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return (
            f"{path}: contains non-ASCII bytes that are neither UTF-8 nor BOM-prefixed "
            f"({exc}); PowerShell 5.1 would decode it as ANSI and can swallow the next line"
        )
    return (
        f"{path}: contains non-ASCII text but no UTF-8 BOM; PowerShell 5.1 decodes it as "
        f"ANSI (cp936 here), where a trailing '。' eats the newline and the next line "
        f"becomes part of the comment"
    )


def add_bom(path: pathlib.Path) -> bool:
    raw = path.read_bytes()
    if raw.startswith(UTF8_BOM) or not _has_non_ascii(raw):
        return False
    raw.decode("utf-8")  # refuses to "fix" a file we cannot safely rewrite
    path.write_bytes(UTF8_BOM + raw)
    return True


def check(roots: list[pathlib.Path], *, fix: bool) -> int:
    problems: list[str] = []
    fixed: list[pathlib.Path] = []
    files = script_files(roots)
    for path in files:
        if fix and add_bom(path):
            fixed.append(path)
            continue
        problem = inspect_script(path)
        if problem is not None:
            problems.append(problem)
    for path in fixed:
        print(f"fixed {path}: added a UTF-8 BOM")
    for problem in problems:
        print(f"FAIL {problem}")
    print(
        f"ps1 encoding checked {len(files)} file(s): "
        f"{len(problems)} problem(s), {len(fixed)} fixed"
    )
    return 1 if problems else 0


def self_test() -> int:
    """Prove both directions on a throwaway tree."""

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        (root / "good.ps1").write_bytes(UTF8_BOM + "# 中文注释\nWrite-Output 1\n".encode("utf-8"))
        (root / "ascii.ps1").write_bytes(b"# ascii only\nWrite-Output 1\n")
        (root / "bad.ps1").write_bytes("# 中文注释\nWrite-Output 1\n".encode("utf-8"))

        ok_good = inspect_script(root / "good.ps1") is None
        ok_ascii = inspect_script(root / "ascii.ps1") is None
        bad = inspect_script(root / "bad.ps1")
        ok_bad = bad is not None and "no UTF-8 BOM" in bad
        print(f"  bom script      : {'safe' if ok_good else 'UNEXPECTED PROBLEM'}")
        print(f"  ascii script    : {'safe' if ok_ascii else 'UNEXPECTED PROBLEM'}")
        print(f"  bom-less han    : {'detected' if ok_bad else 'MISSED'}")

        fixed = add_bom(root / "bad.ps1")
        ok_fixed = fixed and inspect_script(root / "bad.ps1") is None
        print(f"  after --fix     : {'clean' if ok_fixed else 'STILL DIRTY'}")

        ok_clean = ok_good and ok_ascii and ok_bad and ok_fixed
    print("self-test OK" if ok_clean else "self-test FAILED")
    return 0 if ok_clean else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--path",
        action="append",
        default=None,
        help="extra directory (relative to the repo root) to scan; repeatable",
    )
    parser.add_argument("--fix", action="store_true", help="add the missing BOM instead of failing")
    parser.add_argument("--self-test", action="store_true", help="run the built-in fixtures")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    roots = [REPO_ROOT / name for name in DEFAULT_ROOTS]
    for extra in args.path or []:
        roots.append(pathlib.Path(extra).resolve())
    return check(roots, fix=args.fix)


if __name__ == "__main__":
    sys.exit(main())
