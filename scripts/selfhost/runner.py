"""Single-owner real inference, with bounded upstream and CPU subprocesses."""
import asyncio
import base64
import json
import os
from pathlib import Path
import signal
import tempfile

import httpx


class RuntimeFailure(Exception):
    def __init__(self, code, status=503):
        super().__init__(code)
        self.code, self.status = code, status


def available_ram_mib():
    if os.name == "nt":
        import ctypes
        class Memory(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong) for name in ("total", "available", "page_total", "page_available", "virtual_total", "virtual_available", "extended")]
        memory = Memory()
        memory.length = ctypes.sizeof(memory)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
            raise RuntimeFailure("RESOURCE_CHECK_FAILED")
        return memory.available // (1024 * 1024)
    fields = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
    return int(fields["MemAvailable"].split()[0]) // 1024


async def free_gpu_mib():
    """Read only: no GPU kernels, allocation or model load."""
    process = await asyncio.create_subprocess_exec("nvidia-smi", "--query-gpu=memory.free",
        "--format=csv,noheader,nounits", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        output, _ = await asyncio.wait_for(process.communicate(), 5)
        values = [int(line) for line in output.decode("ascii").strip().splitlines()]
        # Initial supported baseline is a dedicated single-NVIDIA-GPU host.
        if process.returncode or len(values) != 1:
            raise RuntimeFailure("GPU_CONFIGURATION_UNVERIFIED")
        return values[0]
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()


class ModelRunner:
    def __init__(self, config):
        self.config = config
        self.faulted = False

    async def run(self, kind, value):
        if self.faulted:
            raise RuntimeFailure("RUNTIME_FAULTED")
        try:
            operation = self.admitted(kind, value)
            return await asyncio.wait_for(self.monitored(operation), self.config.timeout_seconds)
        except RuntimeFailure:
            raise
        except asyncio.CancelledError:
            if kind == "chat":
                self.faulted = True
            raise
        except (TimeoutError, httpx.TimeoutException):
            if kind == "chat":
                self.faulted = True
            raise RuntimeFailure("MODEL_TIMEOUT", 504) from None
        except Exception:
            if kind == "chat":
                self.faulted = True
            raise RuntimeFailure("MODEL_FAILED") from None

    async def admitted(self, kind, value):
        async def admission(wait):
            while True:
                reason = "INSUFFICIENT_RAM" if available_ram_mib() < self.config.min_available_ram_mib else None
                if reason is None and kind == "chat" and await free_gpu_mib() < self.config.min_free_gpu_mib:
                    reason = "INSUFFICIENT_GPU"
                if reason is None:
                    return
                if not wait:
                    raise RuntimeFailure(reason)
                # Resource probes only; no inference/retry occurs in this loop.
                await asyncio.sleep(.25)
        try:
            if self.config.resource_wait_seconds > 0:
                async with asyncio.timeout(self.config.resource_wait_seconds):
                    await admission(True)
            else:
                await admission(False)
        except TimeoutError:
            raise RuntimeFailure("RESOURCE_ADMISSION_TIMEOUT") from None
        return await (self.chat(value) if kind == "chat" else self.cpu(kind, value))

    async def monitored(self, operation):
        task = asyncio.create_task(operation)
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=.5)
                if done:
                    return task.result()
                if available_ram_mib() < self.config.reserve_ram_mib:
                    raise RuntimeFailure("RESOURCE_RESERVE")
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    async def read_json(self, client, method, path, value=None):
        async with client.stream(method, self.config.ollama_url.rstrip("/") + path, json=value) as response:
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > 8 * 1024 * 1024:
                    raise RuntimeFailure("MODEL_RESPONSE_TOO_LARGE")
            return json.loads(body)

    async def chat(self, value):
        try:
            async with asyncio.timeout(self.config.timeout_seconds), httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=self.config.timeout_seconds) as client:
                version = await self.read_json(client, "GET", "/api/version")
                tags = await self.read_json(client, "GET", "/api/tags")
                matches = [item for item in tags["models"] if item.get("name") == self.config.chat_model]
                if version.get("version") != self.config.ollama_version or len(matches) != 1 or matches[0].get("digest") != self.config.chat_digest:
                    raise RuntimeFailure("MODEL_IDENTITY_MISMATCH")
                result = await self.read_json(client, "POST", "/v1/chat/completions", value)
                if not isinstance(result, dict) or not result.get("choices"):
                    raise RuntimeFailure("MODEL_INVALID_OUTPUT")
                return result
        except BaseException:
            # HTTP cancellation does not prove Ollama stopped generating. Require
            # operator inspection/restart; never automatically overlap a retry.
            self.faulted = True
            raise

    async def cpu(self, kind, value):
        from scripts.ops.local_ai_process import RequestJob
        job = RequestJob() if os.name == "nt" else None
        process = None
        python = self.config.asr_python if kind == "asr" else self.config.bge_python
        worker = Path(__file__).with_name("worker.py")
        try:
            with tempfile.TemporaryDirectory(prefix="property-ai-inference-") as folder:
                result_path = Path(folder) / "result.json"
                payload = {"kind": kind, "value": base64.b64encode(value).decode("ascii") if kind == "asr" else value,
                           "root": str(self.config.model_root), "cache": folder, "result": str(result_path),
                           "job": job.name if job else None}
                env = dict(os.environ, PYTHONNOUSERSITE="1", PYTHONUNBUFFERED="1")
                for key in ("PYTHONPATH", "PYTHONHOME"):
                    env.pop(key, None)
                process = await asyncio.create_subprocess_exec(str(python), "-I", str(worker),
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                    env=env, start_new_session=os.name != "nt")
                try:
                    await asyncio.wait_for(process.communicate(json.dumps(payload).encode()), self.config.timeout_seconds)
                    if process.returncode or not result_path.is_file() or result_path.stat().st_size > 8 * 1024 * 1024:
                        raise RuntimeFailure("MODEL_FAILED")
                    return json.loads(result_path.read_text(encoding="utf-8"))
                finally:
                    try:
                        if job:
                            await asyncio.to_thread(job.terminate_and_confirm)
                        else:
                            try:
                                os.killpg(process.pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                    except BaseException:
                        self.faulted = True
                        raise
                    finally:
                        try:
                            if process.returncode is None:
                                process.kill()
                            await asyncio.wait_for(process.wait(), 10)
                        except BaseException:
                            self.faulted = True
                            raise
        finally:
            if job:
                job.close()
