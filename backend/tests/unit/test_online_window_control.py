"""Window control failures without starting Docker, a tunnel or a model."""
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from scripts.online_window import verify_containers, verify_stack, clean_funnel_status, source_digest, read_bound_json, listener_belongs, probe_owned


def container(identifier="a" * 64, **changes):
    value = {"Id": identifier, "Image": "sha256:" + "b" * 64,
        "Config": {"Labels": {"com.docker.compose.project": "property-ai-online-first",
                              "property-ai.window-id": "online-first"}},
        "State": {"Running": True}}
    value.update(changes)
    return value


def test_container_identity_is_complete_and_bound():
    expected = {"a" * 64: "sha256:" + "b" * 64}
    verify_containers([container()], expected, "online-first")
    for actual in ([], [container(), container()], [container(Id="c"*64)],
                   [container(Image="sha256:"+"c"*64)], [container(Config={"Labels":{}})]):
        with pytest.raises(ValueError, match="CONTAINER_SCOPE"):
            verify_containers(actual, expected, "online-first")


@pytest.mark.parametrize("value", [{"TCP":{"443":{}}}, {"Foreground":{"foreign":{}}}, [], "", False])
def test_existing_or_unknown_tunnel_configuration_refused(value):
    with pytest.raises(ValueError, match="FUNNEL_NOT_EMPTY"):
        clean_funnel_status(value)


def test_empty_funnel_and_source_fingerprint():
    clean_funnel_status({})
    assert len(source_digest()) == 64


def stack():
    services = ["api", "worker", "publisher", "beat", "mysql", "redis", "rabbitmq", "etcd", "minio", "milvus", "nginx", "scanner"]
    rows = []
    for i, service in enumerate(services):
        row = container(identifier=f"{i+1:064x}")
        row["Config"]["Labels"]["com.docker.compose.service"] = service
        row["Config"]["Env"] = ["APP_ENV=staging", "DATABASE_URL=mysql+pymysql://u:p@mysql/online_demo_online_first",
            "CELERY_BROKER_URL=amqp://u:p@rabbitmq/online_demo_online_first", "REDIS_URL=redis://redis/0",
            "STORAGE_ENDPOINT=http://minio:9000", "MILVUS_URI=http://milvus:19530",
            *[k+"=http://host.docker.internal:11439/v1" for k in ("LLM_BASE_URL","VLM_BASE_URL","ASR_BASE_URL","RAG_LLM_BASE_URL")],
            "EMBEDDING_ENDPOINT=http://host.docker.internal:11439/v1/embeddings",
            "RERANKER_ENDPOINT=http://host.docker.internal:11439/v1/rerank"]
        row["NetworkSettings"] = {"Networks":{"property-ai-online-first_default":{"NetworkID":"n"}}, "Ports":{}}
        row["Mounts"] = []
        if service == "nginx":
            row["NetworkSettings"]["Ports"] = {"80/tcp":[{"HostIp":"127.0.0.1","HostPort":"18095"}]}
        rows.append(row)
    return rows


def test_full_stack_rejects_foreign_backend_model_data_and_missing_owner():
    rows = stack()
    network = {"n":{"Labels":{"com.docker.compose.project":"property-ai-online-first"}}}
    def verify(value, ids=None):
        verify_stack(value, ids if ids is not None else [r["Id"] for r in value], network,
                     "online-first", "http://127.0.0.1:18095", 11439)
    verify(rows)
    import copy
    changes = [
        lambda r: r[0]["Config"]["Env"].append("LLM_BASE_URL=http://127.0.0.1:11435/v1"),
        lambda r: r[0]["Config"]["Env"].append("DATABASE_URL=mysql+pymysql://u:p@mysql/property_ai_delivery_check"),
        lambda r: r[0]["Mounts"].append({"Type":"volume","Name":"property-ai-delivery-check-mysql-data-v2"}),
        lambda r: r[0]["NetworkSettings"]["Networks"].update({"foreign":{"NetworkID":"foreign"}}),
        lambda r: r[10]["NetworkSettings"]["Ports"]["80/tcp"][0].update(HostIp="0.0.0.0"),
        lambda r: r[0]["NetworkSettings"]["Ports"].update({"8000/tcp":[{"HostIp":"0.0.0.0","HostPort":"18096"}]}),
        lambda r: r[4]["NetworkSettings"]["Ports"].update({"3306/tcp":[{"HostIp":"127.0.0.1","HostPort":"3307"}]}),
        lambda r: r.pop(),
    ]
    for mutate in changes:
        value = copy.deepcopy(rows)
        mutate(value)
        with pytest.raises(ValueError, match="STACK_BINDING"):
            verify(value)
    with pytest.raises(ValueError, match="STACK_BINDING"):
        verify(rows, [r["Id"] for r in rows]+["f"*64])


def test_approval_binds_nested_config_bytes(tmp_path):
    import hashlib
    path = tmp_path / "model.json"
    path.write_bytes(b'{"port":11439}')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert read_bound_json(path, digest) == {"port":11439}
    path.write_bytes(b'{"port":11435}')
    with pytest.raises(ValueError, match="PRIVATE_CONFIG_CHANGED"):
        read_bound_json(path, digest)


def test_foreign_or_missing_listener_never_counts_as_ready(monkeypatch):
    import scripts.online_window as module
    monkeypatch.setattr(module, "listener_pid", lambda port: 12)
    monkeypatch.setattr(module, "process_in_job", lambda job, pid: False)
    assert not listener_belongs(object(), 18094)
    monkeypatch.setattr(module, "process_in_job", lambda job, pid: True)
    assert listener_belongs(object(), 18094)
    monkeypatch.setattr(module, "listener_pid", lambda port: None)
    assert not listener_belongs(object(), 18094)


@pytest.mark.parametrize("public", [False, True])
@pytest.mark.parametrize("failure", ["config_write", "job_create"])
def test_failure_after_claim_stops_owned_stack_and_records_terminal_state(tmp_path, monkeypatch, failure, public):
    import hashlib
    import json
    from types import SimpleNamespace
    import scripts.online_window as module
    import scripts.selfhost.preflight as preflight
    import scripts.selfhost.runner as runner
    import scripts.ops.local_ai_process as processes
    import scripts.ops.local_ai_runtime_control as control
    token = tmp_path / "secret"
    token.write_text("x"*48)
    model_path = tmp_path / "model.json"
    model_path.write_text(json.dumps(dict(model_root="models", bge_python=sys.executable, asr_python=sys.executable,
        token_file=str(token), ollama_url="http://127.0.0.1:11440", ollama_version="0.34.4",
        chat_model="model", chat_digest="a"*64)))
    demo_path = tmp_path / "demo.json"
    demo_path.write_text(json.dumps(dict(window_id="online-first", expires_at=0, origin="https://device.test.ts.net" if public else "http://127.0.0.1:18094",
        backend_url="http://127.0.0.1:18095", resident_token_file=str(token), operator_token_file=str(token),
        invite_file=str(token), ledger_file=str(tmp_path/"ledger"), tenant_id=1, property_id=1, house_id=1, reporter_user_id=1)))
    rows = stack()
    stopped = []
    def command(args, **kwargs):
        if args[0] == "icacls":
            return SimpleNamespace(returncode=0, stdout=b"")
        if args[0] == "docker":
            verb = args[3]
            if verb == "inspect":
                return SimpleNamespace(returncode=0, stdout=json.dumps(rows).encode())
            if verb == "ps":
                return SimpleNamespace(returncode=0, stdout="\n".join(r["Id"] for r in rows).encode())
            if verb == "network":
                return SimpleNamespace(returncode=0, stdout=b'[{"Id":"n","Labels":{"com.docker.compose.project":"property-ai-online-first"}}]')
            if verb == "stop":
                stopped.append(args[6:])
                for row in rows: row["State"]["Running"] = False
                return SimpleNamespace(returncode=0, stdout=b"")
            pytest.fail("unexpected Docker action")
        assert public, "local mode must never contact Tailscale"
        if "funnel" in args:
            return SimpleNamespace(returncode=0, stdout=b"{}")
        return SimpleNamespace(returncode=0, stdout=b'{"BackendState":"Running","Self":{"Online":true,"DNSName":"device.test.ts.net."}}')
    monkeypatch.setattr(module.subprocess, "run", command)
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a,**k: pytest.fail("must not launch"))
    monkeypatch.setattr(module, "verify_stack", lambda *a,**k: None)
    monkeypatch.setattr(preflight, "check", lambda *a: None)
    monkeypatch.setattr(runner, "available_ram_mib", lambda: 65536)
    monkeypatch.setattr(module, "listener_pid", lambda *a: None)
    def no_job():
        raise OSError("synthetic")
    monkeypatch.setattr(processes, "RequestJob", no_job)
    if failure == "config_write":
        write = Path.write_text
        def blocked(path, *args, **kwargs):
            if path.name == "browser.json": raise OSError("synthetic")
            return write(path, *args, **kwargs)
        monkeypatch.setattr(Path, "write_text", blocked)
    cfg = dict(window_id="online-first", duration_seconds=60, docker_endpoint="npipe:////./pipe/dockerDesktopLinuxEngine",
        containers={r["Id"]:r["Image"] for r in rows}, tailscale_exe=str(tmp_path/"tailscale.exe"),
        model_config=str(model_path), model_config_sha256=hashlib.sha256(model_path.read_bytes()).hexdigest(),
        demo_config=str(demo_path), demo_config_sha256=hashlib.sha256(demo_path.read_bytes()).hexdigest(), run_dir=str(tmp_path/"run"))
    if public:
        cfg["remote_access"] = True
    assert module.execute(cfg) == 1
    assert len(stopped) == 1 and set(stopped[0]) == set(cfg["containers"])
    result = json.loads((tmp_path/"run/result.json").read_text())
    assert result["execution_error"] and result["status"] == "CLOSED_CLEAN"
    assert result["owned_processes_remaining"] == 0


def test_credential_probe_waits_for_listener_ownership(monkeypatch):
    import scripts.online_window as module
    from types import SimpleNamespace
    calls = []
    client = SimpleNamespace(get=lambda *a,**k: calls.append((a,k)) or SimpleNamespace(status_code=200))
    monkeypatch.setattr(module, "listener_belongs", lambda job, port: False)
    assert not probe_owned(client, object(), (18094,11439,11440), "synthetic")
    assert calls == []
    monkeypatch.setattr(module, "listener_belongs", lambda job, port: True)
    assert probe_owned(client, object(), (18094,11439,11440), "synthetic")
    assert len(calls) == 2
