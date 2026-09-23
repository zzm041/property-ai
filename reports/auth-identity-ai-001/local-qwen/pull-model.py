"""One approved official free model; isolated local Ollama, no inference."""
from __future__ import annotations

import hashlib
import argparse
import json
from pathlib import Path
import time
import urllib.request

ROOT = Path(r"D:\property-ai")
OUT = ROOT / "reports/auth-identity-ai-001/local-qwen"
MODELS = ROOT / "tmp/selfhost-ai-local/models"
BASE = "http://127.0.0.1:11435"
MODEL = "qwen3.5:2b"
MAX_BYTES = 6 * 1024**3
# Do not route loopback traffic through an inherited proxy.
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get_json(path: str) -> dict:
    with HTTP.open(BASE + path, timeout=10) as response:
        return json.load(response)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024**2), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    global MODEL
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', choices=('qwen3.5:2b', 'qwen3.5:4b'), default='qwen3.5:2b')
    MODEL = parser.parse_args().model
    suffix = '-4b' if MODEL.endswith(':4b') else ''
    launch = json.loads((ROOT / "tmp/selfhost-ai-local/launch.json").read_text())
    assert launch["host"] == "127.0.0.1:11435"
    assert launch["models"] == str(MODELS) and launch["no_cloud"] is True
    assert get_json("/api/version")["version"] == "0.34.2"
    assert not get_json("/api/ps")["models"], "Do not disturb a loaded model"
    existing = get_json("/api/tags")["models"]
    assert all(item['name'] == 'qwen3.5:2b' for item in existing), 'Unexpected or already downloaded model; inspect'
    assert MODEL not in {item['name'] for item in existing}, 'Already downloaded: do not pull twice'
    evidence_path = OUT / f"download{suffix}-events.jsonl"
    assert not evidence_path.exists(), "Retain prior invocation; inspect before resuming"
    request = urllib.request.Request(
        BASE + "/api/pull",
        data=json.dumps({"model": MODEL, "stream": True}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    last_print = 0.0
    previous_status = None
    sizes: dict[str, int] = {}
    success = False
    with evidence_path.open("x", encoding="utf-8") as evidence:
        with HTTP.open(request, timeout=60) as response:
            for line in response:
                if time.monotonic() - started > 1200:
                    raise TimeoutError("Bounded 20-minute download exceeded; preserve partials")
                event = json.loads(line)
                if "error" in event:
                    raise RuntimeError("Local pull returned an error; inspect service log privately")
                safe = {key: event[key] for key in ("status", "digest", "total", "completed") if key in event}
                safe["received_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                safe["source"] = "ollama-api-pull; receive-time-is-local"
                evidence.write(json.dumps(safe) + "\n")
                evidence.flush()
                if "digest" in event and "total" in event:
                    sizes[event["digest"]] = event["total"]
                    assert sum(sizes.values()) <= MAX_BYTES, "Artifact exceeds approved reserved space"
                status = event.get("status")
                if status != previous_status or time.monotonic() - last_print >= 5:
                    print(json.dumps(safe), flush=True)
                    last_print = time.monotonic()
                    previous_status = status
                if status == "success":
                    success = True
    assert success, "No successful pull event; do not claim complete"
    manifest_path = MODELS / f"manifests/registry.ollama.ai/library/qwen3.5/{MODEL.split(':')[1]}"
    manifest = json.loads(manifest_path.read_text())
    artifacts = []
    for item in [manifest["config"], *manifest["layers"]]:
        digest = item["digest"]
        assert digest.startswith("sha256:") and len(digest) == 71
        blob = MODELS / "blobs" / digest.replace(":", "-")
        actual = sha256(blob)
        assert actual == digest.split(":")[1], "Downloaded blob checksum mismatch"
        assert blob.stat().st_size == item["size"]
        artifacts.append({"media_type": item["mediaType"], "size": item["size"], "sha256": actual})
    result = {
        "task_id": "SELFHOST-AI-LOCAL-001",
        "model": MODEL,
        "source": f"official registry.ollama.ai/library/qwen3.5/{MODEL.split(':')[1]}",
        "ollama_version": "0.34.2",
        "models_directory": str(MODELS),
        "manifest_sha256": sha256(manifest_path),
        "artifacts": artifacts,
        "total_blob_bytes": sum(item["size"] for item in artifacts),
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "hashes_verified": True,
        "inference_performed": False,
        "paid_calls": False,
        "tags": get_json("/api/tags")["models"],
    }
    (OUT / f"download{suffix}-result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
