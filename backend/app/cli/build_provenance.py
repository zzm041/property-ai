"""Print the baked build provenance as JSON.

Used by ``deploy/delivery.ps1 verify-image`` and by the PA-116 regression test:
the command runs inside the delivery image, so its output is the only trustworthy
statement about which source commit and contract files a container really runs.
"""

from __future__ import annotations

import json

from app.core.buildinfo import build_info


def main() -> int:
    print(json.dumps(build_info(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
