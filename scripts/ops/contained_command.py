"""Join a Windows Job before the parent permits a fixed entry command."""
import json
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.ops.local_ai_process import join_request_job


def main():
    job, metadata, *command = sys.argv[1:]
    join_request_job(job)
    ready, go = Path(metadata + ".ready"), Path(metadata + ".go")
    with ready.open("x", encoding="ascii") as stream:
        stream.write("JOINED")
    deadline = time.monotonic() + 15
    while not go.exists():
        if time.monotonic() >= deadline:
            raise RuntimeError("PARENT_HANDSHAKE_MISSING")
        time.sleep(.05)
    child = subprocess.Popen(command, stdin=subprocess.DEVNULL)
    with Path(metadata).open("x", encoding="utf-8") as stream:
        json.dump(dict(command_pid=child.pid, joined_before_command=True), stream)
    return child.wait()


if __name__ == "__main__":
    raise SystemExit(main())
