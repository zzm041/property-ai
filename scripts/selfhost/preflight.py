"""Read-only checks; no network, model execution or package installation."""
import argparse
import json
from pathlib import Path
import subprocess

from .assets import verify_assets
from .config import RuntimeConfig

CATALOG = Path(__file__).resolve().parents[2] / "deploy" / "selfhost"


def check_interpreter(path, requirements):
    probe = "import importlib.metadata as m,json,sys;print(json.dumps({'python':list(sys.version_info[:2]),'packages':{n:m.version(n) for n in json.loads(sys.argv[1])}}))"
    try:
        process = subprocess.run([str(path), "-I", "-c", probe, json.dumps(list(requirements))],
                                 capture_output=True, timeout=30, check=True)
        value = json.loads(process.stdout)
        if value["python"] != [3, 12] or value["packages"] != requirements:
            raise ValueError()
        return value
    except (OSError, ValueError, subprocess.SubprocessError):
        raise ValueError("RUNTIME_DEPENDENCIES") from None


def check(config):
    config.token()
    assets = verify_assets(config.model_root, json.loads((CATALOG / "cpu-assets.json").read_text()))
    versions = json.loads((CATALOG / "runtime-versions.json").read_text())
    return {"status": "OFFLINE_PREFLIGHT_PASSED", "assets": assets,
            "bge": check_interpreter(config.bge_python, versions["bge"]),
            "asr": check_interpreter(config.asr_python, versions["asr"]),
            "live_inference": "NOT_EXECUTED", "ollama": "NOT_CONTACTED"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    try:
        result = check(RuntimeConfig.read(args.config))
    except (ValueError, OSError, KeyError):
        print(json.dumps({"status": "PREFLIGHT_FAILED"}))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
