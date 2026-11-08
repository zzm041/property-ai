"""Finite model admission independent of the browser's work-order quota."""
import asyncio
import json
from pathlib import Path
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from scripts.selfhost.runner import RuntimeFailure
from scripts.selfhost.window import WindowRunner, WindowSpec


class Runner:
    faulted = False
    calls = 0
    cancelled = False

    async def run(self, kind, value):
        self.calls += 1
        if value == "fail":
            raise RuntimeFailure("MODEL_FAILED")
        if value == "wait":
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        return {"ok": True}


def spec(tmp_path, **changes):
    data = dict(window_id="online-first", max_attempts=3, expires_at=time.time() + 60, record="window.jsonl")
    data.update(changes)
    return WindowSpec.from_dict(data, tmp_path)


def test_failed_attempt_counts_and_restart_is_refused(tmp_path):
    async def run():
        inner = Runner()
        window = WindowRunner(inner, spec(tmp_path))
        try:
            for _ in range(3):
                with pytest.raises(RuntimeFailure, match="MODEL_FAILED"):
                    await window.run("chat", "fail")
            with pytest.raises(RuntimeFailure, match="WINDOW_EXHAUSTED"):
                await window.run("chat", "ok")
            assert inner.calls == 3
            with pytest.raises(ValueError, match="WINDOW_ALREADY_CLAIMED"):
                WindowRunner(Runner(), spec(tmp_path))
        finally:
            window.close()
        rows = [json.loads(line) for line in (tmp_path / "window.jsonl").read_text().splitlines()]
        assert [r["attempt"] for r in rows if r["event"] == "reserved"] == [1, 2, 3]
        assert "fail" not in (tmp_path / "window.jsonl").read_text()
    asyncio.run(run())


def test_expiry_cancels_inflight_and_never_calls_again(tmp_path):
    async def run():
        inner = Runner()
        window = WindowRunner(inner, spec(tmp_path, expires_at=time.time() + .05))
        try:
            with pytest.raises(RuntimeFailure, match="WINDOW_EXPIRED"):
                await window.run("chat", "wait")
            with pytest.raises(RuntimeFailure, match="WINDOW_EXPIRED"):
                await window.run("chat", "ok")
            assert inner.calls == 1 and inner.cancelled
        finally:
            window.close()
    asyncio.run(run())


def test_record_failure_prevents_inference_and_latches(tmp_path, monkeypatch):
    import scripts.selfhost.window as module
    async def run():
        inner = Runner()
        window = WindowRunner(inner, spec(tmp_path))
        def fail(*args):
            raise OSError("synthetic secret must not escape")
        monkeypatch.setattr(module.os, "fsync", fail)
        try:
            with pytest.raises(RuntimeFailure, match="WINDOW_RECORD_FAILED"):
                await window.run("chat", "ok")
            assert inner.calls == 0 and window.faulted
        finally:
            window.close()
    asyncio.run(run())


def test_wall_clock_jump_cancels_inflight_without_original_timeout(tmp_path, monkeypatch):
    import scripts.selfhost.window as module
    async def run():
        inner = Runner()
        window = WindowRunner(inner, spec(tmp_path))
        task = asyncio.create_task(window.run("chat", "wait"))
        try:
            await asyncio.sleep(.01)
            jumped = time.time() + 120
            monkeypatch.setattr(module.time, "time", lambda: jumped)
            with pytest.raises(RuntimeFailure, match="WINDOW_EXPIRED"):
                await asyncio.wait_for(task, .8)
            assert inner.cancelled and inner.calls == 1
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            window.close()
    asyncio.run(run())


@pytest.mark.parametrize("changes", [dict(max_attempts=0), dict(max_attempts=4), dict(max_attempts=True),
    dict(expires_at=0), dict(expires_at=float("nan")), dict(expires_at=time.time()+3600), dict(window_id="../bad")])
def test_invalid_or_overlarge_window_refused(tmp_path, changes):
    with pytest.raises(ValueError):
        spec(tmp_path, **changes)
