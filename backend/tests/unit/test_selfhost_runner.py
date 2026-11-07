"""Model-free checks of real runner failure/cleanup controls."""
import asyncio
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from scripts.selfhost import runner as runtime


def config():
    return SimpleNamespace(min_available_ram_mib=4096, timeout_seconds=.02,
        ollama_url="http://127.0.0.1:11435", ollama_version="0.34.4",
        chat_model="private-qwen", chat_digest="a" * 64,
        min_free_gpu_mib=6000, reserve_ram_mib=2048, resource_wait_seconds=0)


@pytest.fixture(autouse=True)
def synthetic_gpu_probe(monkeypatch):
    async def gpu(): return 16000
    monkeypatch.setattr(runtime, "free_gpu_mib", gpu)


@pytest.mark.asyncio
async def test_low_memory_never_invokes_model(monkeypatch):
    monkeypatch.setattr(runtime, "available_ram_mib", lambda: 2048)
    runner = runtime.ModelRunner(config())
    with pytest.raises(runtime.RuntimeFailure, match="INSUFFICIENT_RAM"):
        await runner.run("chat", {})
    assert not runner.faulted


@pytest.mark.asyncio
async def test_timed_out_upstream_latches_and_never_retries(monkeypatch):
    monkeypatch.setattr(runtime, "available_ram_mib", lambda: 20000)
    runner = runtime.ModelRunner(config())
    calls = []
    async def blocked(value):
        calls.append(value)
        await asyncio.sleep(10)
    runner.chat = blocked
    with pytest.raises(runtime.RuntimeFailure, match="MODEL_TIMEOUT"):
        await runner.run("chat", {})
    with pytest.raises(runtime.RuntimeFailure, match="RUNTIME_FAULTED"):
        await runner.run("chat", {})
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_model_digest_mismatch_blocks_post(monkeypatch):
    runner = runtime.ModelRunner(config())
    async def gpu(): return 16000
    monkeypatch.setattr(runtime, "free_gpu_mib", gpu)
    methods = []
    async def response(client, method, path, value=None):
        methods.append(method)
        return {"version": "0.34.4"} if path.endswith("version") else {"models": [{"name": "private-qwen", "digest": "b" * 64}]}
    runner.read_json = response
    with pytest.raises(runtime.RuntimeFailure, match="MODEL_IDENTITY_MISMATCH"):
        await runner.chat({})
    assert methods == ["GET", "GET"] and runner.faulted


@pytest.mark.asyncio
async def test_upstream_body_has_a_response_limit():
    runner = runtime.ModelRunner(config())
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * (8 * 1024 * 1024 + 1)))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(runtime.RuntimeFailure, match="MODEL_RESPONSE_TOO_LARGE"):
            await runner.read_json(client, "GET", "/api/tags")


@pytest.mark.asyncio
async def test_cpu_timeout_cleans_owned_process_and_job(monkeypatch, tmp_path):
    from scripts.ops import local_ai_process
    events = []
    class Job:
        name = "owned"
        def terminate_and_confirm(self): events.append("terminate")
        def close(self): events.append("close")
    class Process:
        returncode = None
        async def communicate(self, payload): await asyncio.sleep(10)
        def kill(self): events.append("kill"); self.returncode = 1
        async def wait(self): events.append("wait"); return 1
    async def start(*args, **kwargs):
        events.append("start")
        return Process()
    monkeypatch.setattr(local_ai_process, "RequestJob", Job)
    monkeypatch.setattr(runtime.asyncio, "create_subprocess_exec", start)
    cfg = config()
    cfg.bge_python = cfg.asr_python = Path(sys.executable)
    cfg.model_root = tmp_path
    runner = runtime.ModelRunner(cfg)
    with pytest.raises(TimeoutError):
        await runner.cpu("embedding", ["test"])
    assert events == ["start", "terminate", "kill", "wait", "close"]


@pytest.mark.asyncio
async def test_unknown_cpu_cleanup_state_latches(monkeypatch, tmp_path):
    from scripts.ops import local_ai_process
    class Job:
        name = "owned"
        def terminate_and_confirm(self): raise RuntimeError("still running")
        def close(self): pass
    class Process:
        returncode = None
        async def communicate(self, payload): await asyncio.sleep(10)
        def kill(self): self.returncode = 1
        async def wait(self): return 1
    async def start(*args, **kwargs): return Process()
    monkeypatch.setattr(local_ai_process, "RequestJob", Job)
    monkeypatch.setattr(runtime.asyncio, "create_subprocess_exec", start)
    cfg = config()
    cfg.bge_python = cfg.asr_python = Path(sys.executable)
    cfg.model_root = tmp_path
    runner = runtime.ModelRunner(cfg)
    with pytest.raises(Exception):
        await runner.cpu("embedding", ["test"])
    assert runner.faulted


@pytest.mark.asyncio
async def test_gpu_floor_prevents_upstream_requests(monkeypatch):
    async def gpu(): return 1000
    monkeypatch.setattr(runtime, "free_gpu_mib", gpu)
    monkeypatch.setattr(runtime, "available_ram_mib", lambda: 20000)
    runner = runtime.ModelRunner(config())
    with pytest.raises(runtime.RuntimeFailure, match="INSUFFICIENT_GPU"):
        await runner.run("chat", {})
    assert not runner.faulted


@pytest.mark.asyncio
async def test_short_resource_release_delay_does_not_reject_next_request(monkeypatch):
    readings = iter([3000, 20000])
    monkeypatch.setattr(runtime, "available_ram_mib", lambda: next(readings, 20000))
    cfg = config()
    cfg.resource_wait_seconds = .4
    cfg.timeout_seconds = 1
    runner = runtime.ModelRunner(cfg)
    calls = []
    async def chat(value):
        calls.append(value)
        return {"choices": ["synthetic"]}
    runner.chat = chat
    assert (await runner.run("chat", {}))["choices"] == ["synthetic"]
    assert calls == [{}]


@pytest.mark.asyncio
async def test_resource_probe_cannot_overrun_admission_deadline(monkeypatch):
    monkeypatch.setattr(runtime, "available_ram_mib", lambda: 20000)
    async def slow_probe():
        await asyncio.sleep(.05)
        return 16000
    monkeypatch.setattr(runtime, "free_gpu_mib", slow_probe)
    cfg = config()
    cfg.resource_wait_seconds = .01
    cfg.timeout_seconds = 1
    runner = runtime.ModelRunner(cfg)
    calls = []
    async def chat(value): calls.append(value); return {}
    runner.chat = chat
    with pytest.raises(runtime.RuntimeFailure, match="RESOURCE_ADMISSION_TIMEOUT"):
        await runner.run("chat", {})
    assert calls == [] and not runner.faulted


@pytest.mark.asyncio
async def test_running_memory_floor_cancels_and_waits(monkeypatch):
    values = iter([20000, 1000])
    monkeypatch.setattr(runtime, "available_ram_mib", lambda: next(values, 1000))
    cfg = config()
    cfg.timeout_seconds = 5
    runner = runtime.ModelRunner(cfg)
    ended = []
    async def cpu(*args):
        try:
            await asyncio.sleep(10)
        finally:
            ended.append("cleaned")
    runner.cpu = cpu
    with pytest.raises(runtime.RuntimeFailure, match="RESOURCE_RESERVE"):
        await runner.run("embedding", ["test"])
    assert ended == ["cleaned"]


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object integration")
@pytest.mark.asyncio
async def test_real_owned_timeout_removes_synthetic_descendant(monkeypatch, tmp_path):
    """Launch only sleeping Python processes, not any model or server."""
    marker = tmp_path / "child.json"
    project = str(Path(__file__).resolve().parents[3])
    worker = tmp_path / "worker.py"
    worker.write_text(
        "import json,sys,subprocess,time\n"
        f"sys.path.insert(0,{project!r})\n"
        "from scripts.ops.local_ai_process import join_request_job\n"
        "from pathlib import Path\n"
        "payload=json.load(sys.stdin)\n"
        "join_request_job(payload['job'])\n"
        "child=subprocess.Popen([sys.executable,'-I','-c','import time;time.sleep(60)'])\n"
        f"Path({str(marker)!r}).write_text(json.dumps({{'pid':child.pid}}))\n"
        "time.sleep(60)\n", encoding="utf-8")
    monkeypatch.setattr(runtime, "__file__", str(tmp_path / "runner.py"))
    cfg = config()
    cfg.timeout_seconds = 2
    cfg.bge_python = cfg.asr_python = Path(sys.executable)
    cfg.model_root = tmp_path
    runner = runtime.ModelRunner(cfg)
    with pytest.raises(TimeoutError):
        await runner.cpu("embedding", ["synthetic"])
    assert marker.is_file(), "fixture never reached descendant creation"
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x100000, False, json.loads(marker.read_text())["pid"])
    if handle:
        try:
            assert kernel.WaitForSingleObject(handle, 0) == 0
        finally:
            kernel.CloseHandle(handle)
    else:
        assert ctypes.get_last_error() == 87
