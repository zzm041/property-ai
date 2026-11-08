"""One owned local evaluation ledger, shared by all inference entry points."""
import json
import math
import os
import time
from pathlib import Path
from threading import Lock


class WindowStopped(RuntimeError):
    pass


class WindowBudget:
    def __init__(self, directory, *, model, limits, max_attempts, wall_seconds, clock):
        if (not model or not limits or type(max_attempts) is not int or max_attempts < 1
                or wall_seconds <= 0 or any(type(v) is not int or v < 1 for v in limits.values())):
            raise ValueError("Invalid named-window limits")
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.model, self.limits, self.maximum = model, dict(limits), max_attempts
        self.clock, self.deadline = clock, clock() + wall_seconds
        self.lock, self.active = Lock(), False
        self.state = dict(attempts_reserved=0, by_case={key: 0 for key in limits},
                          stopped=False, records=[], max_attempts=max_attempts,
                          wall_seconds=wall_seconds, model=model)
        self._save()

    def _save(self):
        pending = self.directory / "state.tmp"
        with pending.open("w", encoding="utf-8") as handle:
            json.dump(self.state, handle, ensure_ascii=False, allow_nan=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(4):
            try:
                os.replace(pending, self.directory / "state.json")
                break
            except PermissionError:
                # Windows scanners can briefly hold the previous evidence file.
                # Retry this same atomic rename only, never the provider call.
                if attempt == 3:
                    raise
                time.sleep(.02 * (2 ** attempt))

    def _write_record(self, name, value):
        with (self.directory / name).open("x", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, allow_nan=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())

    def stop(self, reason):
        with self.lock:
            self.state.update(stopped=True, stop_reason=reason)
            self._save()

    def invoke(self, case_id, transport, **request):
        with self.lock:
            remaining = self.deadline - self.clock()
            timeout = request.get("timeout_seconds")
            valid = (not self.state["stopped"] and not self.active and remaining > 0
                     and type(timeout) in {int, float} and math.isfinite(timeout) and timeout > 0
                     and case_id in self.limits and self.state["attempts_reserved"] < self.maximum
                     and self.state["by_case"][case_id] < self.limits[case_id]
                     and request.get("method") == "POST"
                     and request.get("url") == "http://127.0.0.1:11435/v1/chat/completions"
                     and isinstance(request.get("json_body"), dict)
                     and request["json_body"].get("model") == self.model)
            if not valid:
                self.state.update(stopped=True, stop_reason="LIMIT_TARGET_OR_STATE_REJECTED")
                self._save()
                raise WindowStopped("Named-window request rejected before inference")
            self.state["attempts_reserved"] += 1
            self.state["by_case"][case_id] += 1
            index = self.state["attempts_reserved"]
            entry = dict(index=index, case_id=case_id, status="RESERVED")
            self.state["records"].append(entry)
            try:
                self._save()  # Reservation must reach disk before any provider IO.
                self._write_record(f"request-{index:03d}.json", request["json_body"])
            except BaseException:
                self.state["stopped"] = True
                raise
            remaining = self.deadline - self.clock()
            if remaining <= 0:
                entry["status"] = "NOT_STARTED_DEADLINE"
                self.state.update(stopped=True, stop_reason="DEADLINE_DURING_RESERVATION")
                self._save()
                raise WindowStopped("Deadline elapsed while recording reservation")
            self.active = True
            request["timeout_seconds"] = min(request["timeout_seconds"], remaining)
        try:
            response = transport(**request)
            self._write_record(f"response-{index:03d}.json", response)
            if self.state["stopped"] or self.clock() >= self.deadline:
                raise WindowStopped("Window stopped or deadline exceeded during request")
            entry["status"] = "RESPONSE_RECORDED"
            return response
        except BaseException as exc:
            entry.update(status="FAILED", error_type=type(exc).__name__)
            self.state.update(stopped=True, stop_reason="PROVIDER_OR_EVIDENCE_FAILURE")
            raise
        finally:
            with self.lock:
                self.active = False
                self._save()
