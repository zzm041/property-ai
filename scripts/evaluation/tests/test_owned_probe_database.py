"""Cleanup may affect only the exact owned CID with matching label and mounts."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
spec = importlib.util.spec_from_file_location("owned_probe_database", Path(__file__).parents[1] / "owned_probe_database.py")
database = importlib.util.module_from_spec(spec)
spec.loader.exec_module(database)


def owned(*, mutation=None, after="other", discovered="owned"):
    commands, states = [], []
    instance = database.OwnedProbeDatabase(checkpoint=states.append)
    instance.cid = "owned"
    instance.state["running_before"] = ["other"]
    info = dict(Id="owned", Config={"Labels": {database.LABEL: instance.owner}}, Mounts=[],
                HostConfig={"Tmpfs": {"/var/lib/mysql": "rw,size=1g"}}, State={"ExitCode": 0})
    if mutation:
        mutation(info)
    def command(args):
        commands.append(args)
        if args[1] == "inspect":
            return json.dumps([info])
        if args[1:3] == ["ps", "-aq"]:
            return discovered
        if args[1] == "ps":
            return after
        return ""
    instance.command = command
    return instance, commands, states


def test_only_exact_owned_container_is_stopped_and_removed():
    instance, commands, states = owned()
    instance.close()
    assert [c for c in commands if c[1] in {"stop", "rm"}] == [
        ["docker", "stop", "-t", "30", "owned"], ["docker", "rm", "owned"]]
    assert states[-1]["cleanup"] == "OWNED_CONTAINER_REMOVED"
    assert states[-1]["other_running_ids_unchanged"] is True


@pytest.mark.parametrize("mutation", [
    lambda info: info.update(Id="other"),
    lambda info: info["Config"]["Labels"].update({database.LABEL: "different-owner"}),
    lambda info: info.update(Mounts=[{"Source": "existing-volume"}]),
    lambda info: info["HostConfig"].update(Tmpfs={}),
])
def test_ownership_or_mount_drift_never_stops_anything(mutation):
    instance, commands, states = owned(mutation=mutation)
    with pytest.raises(RuntimeError, match="OWNERSHIP_OR_MOUNT"):
        instance.close()
    assert not any(c[1] in {"stop", "rm"} for c in commands)
    assert states[-1]["status"] == "FAILED_REQUIRES_REVIEW"


def test_other_container_set_change_is_reported_without_touching_other_containers():
    instance, commands, states = owned(after="other added")
    with pytest.raises(RuntimeError, match="OTHER_RUNNING_SET"):
        instance.close()
    assert states[-1]["cleanup"] == "OWNED_CONTAINER_REMOVED"
    assert all(c[-1] == "owned" for c in commands if c[1] in {"stop", "rm"})


def test_missing_cid_recovers_only_one_matching_label():
    instance, commands, states = owned()
    instance.cid = None
    instance.close()
    assert ["docker", "ps", "-aq", "--no-trunc", "--filter", f"label={database.LABEL}={instance.owner}"] in commands
    assert states[-1]["cleanup"] == "OWNED_CONTAINER_REMOVED"


def test_ambiguous_label_never_stops_candidates():
    instance, commands, _ = owned(discovered="owned another")
    instance.cid = None
    with pytest.raises(RuntimeError, match="AMBIGUOUS"):
        instance.close()
    assert not any(c[1] in {"stop", "rm"} for c in commands)


def test_dispose_failure_still_removes_only_owned_container_and_reports_error():
    from types import SimpleNamespace
    instance, commands, states = owned()
    def fail():
        raise OSError("connection pool dispose failed")
    instance.engine = SimpleNamespace(dispose=fail)
    with pytest.raises(RuntimeError, match="ENGINE_DISPOSE_FAILED"):
        instance.close()
    assert [c for c in commands if c[1] in {"stop", "rm"}] == [
        ["docker", "stop", "-t", "30", "owned"], ["docker", "rm", "owned"]]
    assert states[-1]["cleanup"] == "OWNED_CONTAINER_REMOVED"
    assert states[-1]["engine_dispose_error_type"] == "OSError"
    assert states[-1]["status"] == "FAILED_REQUIRES_REVIEW"
