"""Exact Windows process-tree control for the project-owned local AI runtime.

The caller supplies an immutable runtime receipt. PID, executable, start time
and loopback listener ownership must all match before the tree can be stopped.
"""

from __future__ import annotations

from datetime import datetime
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _process_identity(pid: int) -> dict[str, object]:
    command = (
        f"$p=Get-Process -Id {pid} -ErrorAction Stop; "
        "[ordered]@{pid=$p.Id;path=$p.Path;"
        "started_utc=$p.StartTime.ToUniversalTime().ToString('o')} | "
        "ConvertTo-Json -Compress"
    )
    return json.loads(
        subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", command],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    )


def bound_runtime_identity(runtime: dict[str, object]) -> dict[str, object]:
    pid = int(runtime["pid"])
    actual = _process_identity(pid)
    expected = datetime.fromisoformat(
        str(runtime["launched_at_utc"]).replace("Z", "+00:00")
    )
    observed = datetime.fromisoformat(
        str(actual["started_utc"]).replace("Z", "+00:00")
    )
    if (
        actual["pid"] != pid
        or Path(str(actual["path"])).resolve()
        != Path(str(runtime["executable"])).resolve()
        or abs((observed - expected).total_seconds()) > 1
    ):
        raise RuntimeError("MODEL_RUNTIME_IDENTITY_CHANGED")
    return actual


def _child_pids(parent_pid: int) -> list[int]:
    command = (
        f"$ids=@(Get-CimInstance Win32_Process -Filter 'ParentProcessId = {parent_pid}' "
        "-ErrorAction Stop | Select-Object -ExpandProperty ProcessId); "
        "[string]::Join(',', $ids)"
    )
    raw = subprocess.check_output(
        ["powershell", "-NoProfile", "-Command", command],
        text=True,
        stderr=subprocess.DEVNULL,
    ).strip()
    if not raw:
        return []
    return [int(value) for value in raw.split(",") if value]


def bound_process_tree(runtime: dict[str, object]) -> list[dict[str, object]]:
    """Capture identities for the exact root and every observable descendant."""

    root = bound_runtime_identity(runtime)
    identities = [root]
    pending = [int(root["pid"])]
    seen = set(pending)
    while pending:
        parent = pending.pop()
        for pid in _child_pids(parent):
            if pid in seen:
                continue
            seen.add(pid)
            try:
                identity = _process_identity(pid)
            except subprocess.CalledProcessError:
                continue
            identities.append(identity)
            pending.append(pid)
    return identities


def _same_process_is_alive(expected: dict[str, object]) -> bool:
    try:
        actual = _process_identity(int(expected["pid"]))
    except subprocess.CalledProcessError:
        return False
    expected_started = datetime.fromisoformat(
        str(expected["started_utc"]).replace("Z", "+00:00")
    )
    actual_started = datetime.fromisoformat(
        str(actual["started_utc"]).replace("Z", "+00:00")
    )
    return (
        Path(str(actual["path"])).resolve() == Path(str(expected["path"])).resolve()
        and abs((actual_started - expected_started).total_seconds()) <= 1
    )


def loopback_listener_pid(port: int) -> int | None:
    pattern = re.compile(
        rf"^\s*TCP\s+127\.0\.0\.1:{port}\s+\S+\s+LISTENING\s+(\d+)\s*$"
    )
    matches: set[int] = set()
    output = subprocess.check_output(
        ["netstat", "-ano", "-p", "TCP"], text=True, errors="replace"
    )
    for line in output.splitlines():
        match = pattern.match(line)
        if match:
            matches.add(int(match.group(1)))
    if len(matches) > 1:
        raise RuntimeError("MODEL_RUNTIME_LISTENER_OWNERSHIP_AMBIGUOUS")
    return next(iter(matches), None)


def loopback_listener_closed(port: int) -> bool:
    # The ownership check and the exit check deliberately use the same OS
    # listener table. A connect probe can return WSAEWOULDBLOCK while Windows
    # is tearing a socket down, which is not evidence of an active listener.
    return loopback_listener_pid(port) is None


def terminate_bound_runtime(
    runtime: dict[str, object], *, listener_port: int = 11435
) -> dict[str, object]:
    """Stop one receipt-bound runtime and confirm process and listener exit."""

    actual = bound_runtime_identity(runtime)
    pid = int(runtime["pid"])
    if loopback_listener_pid(listener_port) != pid:
        raise RuntimeError("MODEL_RUNTIME_LISTENER_IDENTITY_CHANGED")
    tree_before_stop = bound_process_tree(runtime)

    stopped = subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if stopped.returncode != 0:
        raise RuntimeError("MODEL_RUNTIME_TREE_TERMINATION_FAILED")
    process_exited = False
    process_deadline = time.monotonic() + 10
    while time.monotonic() < process_deadline:
        if not any(_same_process_is_alive(item) for item in tree_before_stop):
            process_exited = True
            break
        time.sleep(0.25)

    if not process_exited:
        raise RuntimeError("MODEL_RUNTIME_EXIT_UNCONFIRMED")

    listener_exited = False
    listener_deadline = time.monotonic() + 10
    while time.monotonic() < listener_deadline:
        if loopback_listener_closed(listener_port):
            listener_exited = True
            break
        time.sleep(0.25)

    if not listener_exited:
        raise RuntimeError("MODEL_RUNTIME_LISTENER_STILL_AVAILABLE")
    return {
        "verified_before_stop": actual,
        "verified_tree_before_stop": tree_before_stop,
        "taskkill_exit_code": stopped.returncode,
        "stopped": {
            "pid": pid,
            "captured_process_count": len(tree_before_stop),
            "tree_exit_confirmed": True,
            "listener_exit_confirmed": True,
        },
    }


def _wait_for_listener(pid: int, port: int) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if loopback_listener_pid(port) == pid:
            return
        time.sleep(0.1)
    raise RuntimeError("DUMMY_LISTENER_NOT_READY")


class _SilentHandler(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        self.send_response(204)
        self.end_headers()


def _dummy_runtime(port: int) -> None:
    child = subprocess.Popen(
        [sys.executable, "-c", "import time;time.sleep(300)"],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        ThreadingHTTPServer(("127.0.0.1", port), _SilentHandler).serve_forever()
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)


def selftest(port: int) -> dict[str, object]:
    """Exercise the exact stop path with one owned no-model dummy listener."""

    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "dummy-runtime", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        _wait_for_listener(process.pid, port)
        identity = _process_identity(process.pid)
        runtime = {
            "pid": process.pid,
            "executable": identity["path"],
            "launched_at_utc": identity["started_utc"],
        }
        result = terminate_bound_runtime(runtime, listener_port=port)
        if result["stopped"]["captured_process_count"] < 2:
            raise RuntimeError("DUMMY_DESCENDANT_NOT_CAPTURED")
        return {
            "test_type": "NO_MODEL_EXACT_PROCESS_TREE_STOP",
            "listener": f"127.0.0.1:{port}",
            "inference_calls": 0,
            "result": result,
        }
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["selftest", "dummy-runtime"])
    parser.add_argument("--port", type=int, required=True)
    arguments = parser.parse_args()
    if arguments.mode == "dummy-runtime":
        _dummy_runtime(arguments.port)
    else:
        print(json.dumps(selftest(arguments.port), ensure_ascii=False))
