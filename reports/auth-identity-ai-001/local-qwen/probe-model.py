"""Real bounded local inference probes, not UI/business/production acceptance."""
from __future__ import annotations

import argparse
import base64
import ctypes
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import threading
import time
import urllib.request
import zlib

ROOT = Path(r"D:\property-ai")
OUT = Path(__file__).parent
BASE = "http://127.0.0.1:11435"
MODEL = "qwen3.5:2b"
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class MemoryStatus(ctypes.Structure):
    _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
        (name, ctypes.c_ulonglong)
        for name in ("total_physical", "available_physical", "total_page", "available_page", "total_virtual", "available_virtual", "available_extended")
    ]


def resource_sample() -> dict:
    memory = MemoryStatus()
    memory.length = ctypes.sizeof(memory)
    assert ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory))
    result = subprocess.run([
        "nvidia-smi", "--query-gpu=memory.used,memory.free,utilization.gpu",
        "--format=csv,noheader,nounits",
    ], capture_output=True, text=True, timeout=8, check=True)
    used, free, utilization = [int(value.strip()) for value in result.stdout.strip().split(",")]
    return {
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ram_free_mib": round(memory.available_physical / 1024**2),
        "gpu_used_mib": used, "gpu_free_mib": free, "gpu_utilization_percent": utilization,
    }


def api(path: str, body: dict | None = None, timeout: int = 120) -> dict:
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
        method="POST" if body is not None else "GET",
    )
    with HTTP.open(request, timeout=timeout) as response:
        return json.load(response)


def calibration_png(rgb: tuple[int, int, int]) -> bytes:
    # New deterministic calibration fixture, not an edited photo or fake model reply.
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 256, 256, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\0" + bytes(rgb) * 256) * 256)) + chunk(b"IEND", b""))


def main() -> None:
    global MODEL
    parser = argparse.ArgumentParser()
    parser.add_argument("case", choices=("metadata", "text", "image-blue", "image-red"))
    parser.add_argument('--model', choices=('qwen3.5:2b', 'qwen3.5:4b'), default='qwen3.5:2b')
    args = parser.parse_args()
    case, MODEL = args.case, args.model
    suffix = '-4b' if MODEL.endswith(':4b') else ''
    destination = OUT / f"probe{suffix}-{case}.json"
    assert not destination.exists(), "Already recorded: inspect instead of repeating"
    downloaded = json.loads((OUT / f"download{suffix}-result.json").read_text())
    assert downloaded["hashes_verified"] and downloaded["model"] == MODEL
    assert api("/api/version")["version"] == "0.34.2"
    assert all(row['name'] == MODEL for row in api('/api/ps')['models']), 'Other resident model: wait, never evict'
    evidence = {"task_id": "SELFHOST-AI-LOCAL-001", "case": case, "model": MODEL,
                "manifest_sha256": downloaded["manifest_sha256"], "endpoint": BASE,
                "business_requests": False, "synthetic_only": True,
                "resource_scope": "Whole workstation samples; other applications remain running, not exclusive attribution."}
    if case == "metadata":
        raw = api("/api/show", {"model": MODEL})
        evidence.update({key: raw.get(key) for key in ("details", "capabilities", "parameters", "model_info", "projector_info")})
        evidence["status"] = "PASS" if "vision" in raw.get("capabilities", []) else "FAIL"
        destination.write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps({"case": case, "status": evidence["status"], "capabilities": evidence["capabilities"], "details": evidence["details"]}), flush=True)
        return

    baseline = resource_sample()
    evidence["baseline"] = baseline
    # Do not launch inference under visible existing memory pressure.
    if baseline["ram_free_mib"] < 4500 or baseline["gpu_free_mib"] < 6000:
        evidence["status"] = "RESOURCE_HEADROOM_BLOCKED"
        destination.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(evidence), flush=True)
        return
    if case == "text":
        content = "你是报修分类助手。只返回JSON，字段category（electrical/plumbing/other）及summary（中文），禁止选择员工。住户描述：厨房插座没有电，其他房间照明正常，没有冒烟。"
    else:
        color = case.split("-")[1]
        image = calibration_png((20, 60, 190) if color == "blue" else (190, 30, 30))
        fixture = OUT / f"calibration-{color}.png"
        if fixture.exists():
            assert fixture.read_bytes() == image, 'Do not overwrite existing calibration evidence'
        else:
            fixture.write_bytes(image)
        evidence["image_sha256"] = hashlib.sha256(image).hexdigest()
        evidence["image_boundary"] = "Solid-color calibration only, not a repair-photo quality benchmark."
        content = [{"type": "text", "text": "Return JSON with dominant_color (red, blue, green, white, other) and description. Describe only visible content, do not infer appliances or damage."},
                   {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(image).decode()}}]
    body = {"model": MODEL, "messages": [{"role": "user", "content": content}],
            "response_format": {"type": "json_object"}, "temperature": 0,
            "reasoning_effort": "none", "max_tokens": 256, "stream": False}
    evidence["request_controls"] = {key: value for key, value in body.items() if key != "messages"}
    samples: list[dict] = [baseline]
    done = threading.Event()

    def sample() -> None:
        while not done.wait(1):
            try:
                samples.append(resource_sample())
            except Exception as exc:
                samples.append({"sample_error_type": type(exc).__name__})

    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    started = time.perf_counter()
    print(json.dumps({"case": case, "phase": "real local inference", "baseline": baseline}), flush=True)
    try:
        response = api("/v1/chat/completions", body)
        evidence["response"] = response  # Synthetic prompts/outputs only; no user credentials or real resident data.
        message = response["choices"][0]["message"]
        answer = json.loads(message["content"])
        passed = answer.get("category") == "electrical" if case == "text" else answer.get("dominant_color") == case.split("-")[1]
        passed = passed and response["choices"][0].get("finish_reason") == "stop"
        evidence["status"] = "PASS_BOUNDED_REAL_MODEL_PROBE" if passed else "FAIL_PROBE_EXPECTATION"
        evidence["parsed_answer"] = answer
        evidence["loaded_models"] = api("/api/ps")["models"]
    except Exception as exc:
        evidence["status"] = "PROBE_ERROR"
        evidence["error_type"] = type(exc).__name__
        if hasattr(exc, "code"):
            evidence["http_status"] = exc.code
    finally:
        evidence["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        done.set()
        sampler.join(timeout=10)
        evidence["resource_samples"] = samples
        valid = [item for item in samples if "ram_free_mib" in item]
        evidence["min_ram_free_mib"] = min(item["ram_free_mib"] for item in valid)
        evidence["max_gpu_used_mib"] = max(item["gpu_used_mib"] for item in valid)
        destination.write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps({key: evidence[key] for key in ("case", "status", "elapsed_seconds", "min_ram_free_mib", "max_gpu_used_mib")}), flush=True)


if __name__ == "__main__":
    main()
