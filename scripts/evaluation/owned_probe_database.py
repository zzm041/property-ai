"""One loopback, tmpfs MySQL owned by a single evaluation window."""
from datetime import UTC, datetime
import json
import os
import secrets
import socket
import subprocess
import time

from sqlalchemy import create_engine, text

from probe_schema import create_probe_schema

IMAGE = "sha256:bf577825b52ab281d6281fb281eabbfdc73507eda8f2c2745790251533ef0306"
LABEL = "property-ai.combined-window-owner"


class OwnedProbeDatabase:
    def __init__(self, *, checkpoint, command=None):
        self.owner = secrets.token_hex(12)
        self.password = secrets.token_urlsafe(30)
        self.url = f"mysql+pymysql://root:{self.password}@127.0.0.1:13337/property_ai_test?charset=utf8mb4"
        self.env = dict(os.environ, MYSQL_ROOT_PASSWORD=self.password)
        self.checkpoint = checkpoint
        self.command = command or self._command
        self.cid = None
        self.engine = None
        self.state = dict(status="NOT_STARTED", cleanup="NOT_STARTED", port=13337,
                          owner=self.owner, image=IMAGE)

    def _command(self, args, timeout=60):
        completed = subprocess.run(args, env=self.env, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=timeout, check=False)
        if completed.returncode:
            raise RuntimeError("Owned database command failed: " +
                               completed.stderr.replace(self.password, "[redacted]")[-1000:])
        return completed.stdout

    def _save(self):
        self.checkpoint(dict(self.state))

    def start(self):
        with socket.socket() as port:
            port.bind(("127.0.0.1", 13337))
        image = self.command(["docker", "image", "inspect", "mysql:8.0.41", "--format", "{{.Id}}"])
        if image.strip() != IMAGE:
            raise RuntimeError("MYSQL_IMAGE_DRIFT")
        self.state["running_before"] = sorted(self.command(["docker", "ps", "-q", "--no-trunc"]).split())
        name = "property-ai-combined-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ").lower() + "-" + self.owner[:6]
        self.state.update(status="CREATING", name=name, cleanup="PENDING")
        self._save()
        self.cid = self.command(["docker", "create", "--name", name, "--label", f"{LABEL}={self.owner}",
            "--memory", "2g", "--cpus", "1", "--tmpfs", "/var/lib/mysql:rw,size=1g",
            "-p", "127.0.0.1:13337:3306", "-e", "MYSQL_ROOT_PASSWORD", "-e",
            "MYSQL_DATABASE=property_ai_test", IMAGE]).strip()
        self.state["container_id"] = self.cid
        self._save()
        self.command(["docker", "start", self.cid])
        self.engine = create_engine(self.url, connect_args={"connect_timeout": 2}, hide_parameters=True)
        deadline = time.monotonic() + 150
        while True:
            try:
                with self.engine.connect() as connection:
                    self.state["mysql_version"] = connection.execute(text("SELECT VERSION()")).scalar_one()
                    self.state["server_uuid"] = connection.execute(text("SELECT @@server_uuid")).scalar_one()
                break
            except Exception:
                if time.monotonic() >= deadline:
                    raise RuntimeError("MYSQL_STARTUP_TIMEOUT") from None
                time.sleep(1)
        create_probe_schema(self.engine)
        self.state.update(status="READY", schema_method="COPIED_ORM_PLUS_EXISTING_0009_AUTO_COLUMNS_NO_MIGRATION_STAMP")
        self._save()

    def close(self):
        dispose_failed = False
        if self.engine is not None:
            try:
                self.engine.dispose()
            except BaseException as exc:
                dispose_failed = True
                self.state["engine_dispose_error_type"] = type(exc).__name__
        try:
            if not self.cid:
                found = self.command(["docker", "ps", "-aq", "--no-trunc", "--filter",
                                      f"label={LABEL}={self.owner}"]).split()
                if len(found) > 1:
                    raise RuntimeError("AMBIGUOUS_MYSQL_OWNERSHIP")
                self.cid = found[0] if found else None
            if self.cid:
                info = json.loads(self.command(["docker", "inspect", self.cid]))[0]
                if (info["Id"] != self.cid or info["Config"]["Labels"].get(LABEL) != self.owner
                        or info["Mounts"] != [] or "/var/lib/mysql" not in info["HostConfig"]["Tmpfs"]):
                    raise RuntimeError("MYSQL_OWNERSHIP_OR_MOUNT_CHANGED")
                self.command(["docker", "stop", "-t", "30", self.cid])
                stopped = json.loads(self.command(["docker", "inspect", self.cid]))[0]["State"]
                self.state["stopped_exit_code"] = stopped["ExitCode"]
                self.command(["docker", "rm", self.cid])
                self.state["cleanup"] = "OWNED_CONTAINER_REMOVED"
            else:
                self.state["cleanup"] = "NO_OWNED_CONTAINER"
            after = sorted(self.command(["docker", "ps", "-q", "--no-trunc"]).split())
            self.state["running_after"] = after
            self.state["other_running_ids_unchanged"] = (
                self.state["running_before"] == after if "running_before" in self.state else None)
            if self.state["other_running_ids_unchanged"] is False:
                raise RuntimeError("OTHER_RUNNING_SET_CHANGED_REQUIRES_REVIEW")
            if dispose_failed:
                raise RuntimeError("ENGINE_DISPOSE_FAILED_AFTER_CONTAINER_CLEANUP")
        except BaseException as exc:
            self.state.update(status="FAILED_REQUIRES_REVIEW", cleanup_error_type=type(exc).__name__,
                              cleanup_error=str(exc).replace(self.password, "[redacted]"))
            raise
        finally:
            self._save()
