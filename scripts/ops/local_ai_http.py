"""兼容层，二期删除：实现已搬到 backend/app/provider/local_ai。"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "backend"))
from app.provider.local_ai import *  # noqa: F401,F403
from app.provider.local_ai.http_api import main as _main

if __name__ == "__main__":
    raise SystemExit(_main())
