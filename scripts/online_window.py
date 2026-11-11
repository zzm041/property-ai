"""Windows operator controller for one approved, already provisioned demo stack.

Default mode only prints fingerprints. It never creates data, starts containers,
changes Tailscale login/policy or resets another service's forwarding settings.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


def source_digest():
    files = {Path(__file__), ROOT / "scripts/online_demo.py", ROOT / "deploy/nginx.conf"}
    for directory, pattern in (("scripts/selfhost", "*.py"), ("deploy/selfhost", "*.json"),
                               ("deploy/online-demo/web", "*")):
        files.update((ROOT / directory).glob(pattern))
    for name in ("contained_command.py", "local_ai_models.py", "local_ai_process.py", "local_ai_runtime_control.py"):
        files.add(ROOT / "scripts/ops" / name)
    digest = hashlib.sha256()
    for path in sorted(files):
        if path.is_file():
            digest.update(path.relative_to(ROOT).as_posix().encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


def clean_funnel_status(value):
    if type(value) is not dict or value:
        raise ValueError("FUNNEL_NOT_EMPTY")


def read_bound_json(path, expected):
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("PRIVATE_CONFIG_CHANGED")
    return json.loads(raw)


def listener_pid(port):
    output = subprocess.check_output(["netstat", "-ano", "-p", "TCP"], text=True, encoding="oem",
        errors="strict", timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    pattern = re.compile(rf"^\s*TCP\s+127\.0\.0\.1:{port}\s+\S+\s+LISTENING\s+(\d+)\s*$")
    owners = {int(match.group(1)) for line in output.splitlines() if (match := pattern.match(line))}
    if len(owners) > 1:
        raise RuntimeError("LISTENER_AMBIGUOUS")
    return next(iter(owners), None)


def process_in_job(job, pid):
    import ctypes
    kernel = job.k
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.IsProcessInJob.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        member = ctypes.c_int()
        return bool(kernel.IsProcessInJob(handle, job.handle, ctypes.byref(member)) and member.value)
    finally:
        kernel.CloseHandle(handle)


def listener_belongs(job, port):
    pid = listener_pid(port)
    return bool(pid and process_in_job(job, pid))


def probe_owned(client, job, ports, token):
    # Never send a private credential to a port before checking its listener owner.
    if not all(listener_belongs(job, port) for port in ports):
        return False
    model = client.get(f"http://127.0.0.1:{ports[1]}/healthz", headers={"Authorization":"Bearer " + token})
    browser = client.get("http://127.0.0.1:18094/")
    return model.status_code == browser.status_code == 200 and all(listener_belongs(job, port) for port in ports)


def verify_containers(actual, expected, window_id):
    if not expected or len(actual) != len(expected) or len({row.get("Id") for row in actual}) != len(expected):
        raise ValueError("CONTAINER_SCOPE")
    for row in actual:
        labels = row.get("Config", {}).get("Labels") or {}
        if (row.get("Id") not in expected or row.get("Image") != expected[row["Id"]]
                or labels.get("com.docker.compose.project") != "property-ai-" + window_id
                or labels.get("property-ai.window-id") != window_id):
            raise ValueError("CONTAINER_SCOPE")


def verify_stack(rows, all_ids, networks, window_id, backend_url, model_port, *, model_token=None, model_name=None):
    project = "property-ai-" + window_id
    required = {"api", "worker", "publisher", "beat", "mysql", "redis", "rabbitmq", "etcd", "minio", "milvus", "nginx", "scanner"}
    services = {}
    try:
        if set(all_ids) != {row["Id"] for row in rows}:
            raise ValueError()
        for row in rows:
            name = row["Config"]["Labels"]["com.docker.compose.service"]
            if name in services or name not in required | {"beat-volume-init"}:
                raise ValueError()
            services[name] = row
            for port, bindings in row["NetworkSettings"]["Ports"].items():
                if bindings and (name != "nginx" or port != "80/tcp"):
                    raise ValueError()
            if name in required and not row["State"]["Running"]:
                raise ValueError()
            for link in row["NetworkSettings"]["Networks"].values():
                if networks[link["NetworkID"]]["Labels"].get("com.docker.compose.project") != project:
                    raise ValueError()
            if not row["NetworkSettings"]["Networks"]:
                raise ValueError()
            for mount in row["Mounts"]:
                if mount["Type"] == "volume" and mount["Name"].startswith((project+"_", project+"-")):
                    continue
                if (name == "nginx" and mount["Type"] == "bind" and mount.get("RW") is False
                        and Path(mount["Source"]).resolve() == (ROOT / "deploy/nginx.conf").resolve()
                        and mount["Destination"] == "/etc/nginx/conf.d/default.conf"):
                    continue
                raise ValueError()
        if not required.issubset(services):
            raise ValueError()
        target = urlsplit(backend_url)
        if target.scheme != "http" or target.hostname != "127.0.0.1" or target.port in (None, 8080, 8093):
            raise ValueError()
        published = services["nginx"]["NetworkSettings"]["Ports"].get("80/tcp")
        if published != [{"HostIp":"127.0.0.1", "HostPort":str(target.port)}]:
            raise ValueError()
        base = f"http://host.docker.internal:{model_port}/v1"
        database = "online_demo_" + window_id.replace("-", "_")
        for service in ("api", "worker", "publisher", "beat"):
            items = [entry.split("=", 1) for entry in services[service]["Config"]["Env"]]
            env = dict(items)
            if len(items) != len(env) or env.get("APP_ENV") != "staging":
                raise ValueError()
            db = urlsplit(env["DATABASE_URL"])
            broker = urlsplit(env["CELERY_BROKER_URL"])
            if db.hostname != "mysql" or db.path != "/"+database or broker.hostname != "rabbitmq" or broker.path != "/"+database:
                raise ValueError()
            if (urlsplit(env["REDIS_URL"]).hostname != "redis" or env["STORAGE_ENDPOINT"] != "http://minio:9000"
                    or env["MILVUS_URI"] != "http://milvus:19530"):
                raise ValueError()
            if any(env.get(key) != base for key in ("LLM_BASE_URL", "VLM_BASE_URL", "ASR_BASE_URL", "RAG_LLM_BASE_URL")):
                raise ValueError()
            if env.get("EMBEDDING_ENDPOINT") != base+"/embeddings" or env.get("RERANKER_ENDPOINT") != base+"/rerank":
                raise ValueError()
            if model_token is not None:
                if any(env.get(key) != model_token for key in ("LLM_API_KEY", "VLM_API_KEY", "ASR_API_KEY", "RAG_LLM_API_KEY", "EMBEDDING_API_KEY", "RERANKER_API_KEY")):
                    raise ValueError()
                if any(env.get(key) != "production" for key in ("LLM_PROVIDER", "VLM_PROVIDER", "ASR_PROVIDER", "RAG_LLM_PROVIDER", "EMBEDDING_PROVIDER", "RERANKER_PROVIDER")):
                    raise ValueError()
                if any(env.get(key) != "true" for key in ("AI_ENQUEUE_ON_CREATE", "AI_AUTO_ASSIGN", "DURABLE_TASKS_ENABLED")):
                    raise ValueError()
                if env.get("CELERY_TASK_QUEUE") != "property_ai.online_demo."+window_id:
                    raise ValueError()
            if model_name is not None and env.get("LLM_MODEL") != model_name:
                raise ValueError()
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ValueError("STACK_BINDING") from None


def read_configuration(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    value.setdefault("remote_access", False)
    keys = {"window_id", "duration_seconds", "source_sha256", "python", "ollama_exe", "ollama_models",
            "tailscale_exe", "model_config", "demo_config", "run_dir", "containers", "docker_endpoint",
            "model_config_sha256", "demo_config_sha256", "remote_access"}
    if set(value) != keys or not re.fullmatch(r"online-[a-z0-9-]{1,32}", value["window_id"]):
        raise ValueError("WINDOW_CONFIG")
    if type(value["duration_seconds"]) is not int or not 60 <= value["duration_seconds"] <= 1800:
        raise ValueError("WINDOW_DURATION")
    if type(value["remote_access"]) is not bool:
        raise ValueError("WINDOW_PUBLIC_MODE")
    if value["docker_endpoint"] != "npipe:////./pipe/dockerDesktopLinuxEngine":
        raise ValueError("LOCAL_DOCKER_REQUIRED")
    if value["source_sha256"] != source_digest():
        raise ValueError("WINDOW_SOURCE_CHANGED")
    for key in ("model_config", "demo_config"):
        read_bound_json(value[key], value[key+"_sha256"])
    for key in ("python", "ollama_exe", "ollama_models", "tailscale_exe", "model_config", "demo_config", "run_dir"):
        if not Path(value[key]).is_absolute():
            raise ValueError("ABSOLUTE_PRIVATE_PATH_REQUIRED")
    expected = value["containers"]
    if type(expected) is not dict or not 1 <= len(expected) <= 16:
        raise ValueError("CONTAINER_SCOPE")
    if any(not re.fullmatch(r"[a-f0-9]{64}", key) or not re.fullmatch(r"sha256:[a-f0-9]{64}", image) for key, image in expected.items()):
        raise ValueError("CONTAINER_SCOPE")
    return value


def execute(cfg):
    if os.name != "nt":
        raise ValueError("WINDOWS_ONLY")
    from scripts.ops.local_ai_process import RequestJob
    from scripts.selfhost.config import RuntimeConfig
    from scripts.selfhost.preflight import check
    from scripts.selfhost.runner import available_ram_mib
    from scripts.online_demo import validate_config, read_secret
    import httpx

    env = dict(os.environ)
    for key in ("DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "PYTHONPATH", "PYTHONHOME"):
        env.pop(key, None)
    def command(args, timeout=15):
        result = subprocess.run(args, capture_output=True, timeout=timeout, env=env,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise RuntimeError("CONTROL_COMMAND_FAILED")
        return result.stdout
    def docker(*args, timeout=20):
        return command(["docker", "--host", cfg["docker_endpoint"], *args], timeout)
    def inspect():
        rows = json.loads(docker("inspect", *cfg["containers"]))
        verify_containers(rows, cfg["containers"], cfg["window_id"])
        return rows
    def funnel_status():
        return json.loads(command([cfg["tailscale_exe"], "funnel", "status", "--json"]))

    # Complete read-only checks before claiming the window or starting processes.
    rows = inspect()
    public = cfg.get("remote_access", False)
    expected_origin = "http://127.0.0.1:18094"
    if public:
        clean_funnel_status(funnel_status())
        tail = json.loads(command([cfg["tailscale_exe"], "status", "--json"]))
        if tail.get("BackendState") != "Running" or not tail.get("Self", {}).get("Online"):
            raise ValueError("TAILSCALE_NOT_READY")
        hostname = tail["Self"]["DNSName"].rstrip(".")
        if not re.fullmatch(r"[a-zA-Z0-9.-]+\.ts\.net", hostname):
            raise ValueError("TAILSCALE_DNS")
        expected_origin = "https://" + hostname
    model_data = read_bound_json(cfg["model_config"], cfg["model_config_sha256"])
    model = RuntimeConfig.from_dict(model_data, Path(cfg["model_config"]).parent)
    if model.bind != "127.0.0.1":
        raise ValueError("MODEL_LOOPBACK_REQUIRED")
    check(model)
    ollama_port = urlsplit(model.ollama_url).port
    ports = (18094, model.port, ollama_port)
    if len(set(ports)) != 3 or any(type(port) is not int or port < 1024 or port in (8080, 8093) for port in ports):
        raise ValueError("WINDOW_PORTS")
    if any(listener_pid(port) for port in ports):
        raise ValueError("PORT_OCCUPIED")
    if available_ram_mib() < model.min_available_ram_mib:
        raise ValueError("RESOURCE_ADMISSION")
    demo = read_bound_json(cfg["demo_config"], cfg["demo_config_sha256"])
    if demo["window_id"] != cfg["window_id"] or demo["origin"] != expected_origin:
        raise ValueError("DEMO_BINDING")
    all_ids = docker("ps", "--all", "--quiet", "--no-trunc", "--filter",
        "label=com.docker.compose.project=property-ai-"+cfg["window_id"]).decode().split()
    network_ids = {link["NetworkID"] for row in rows for link in row["NetworkSettings"]["Networks"].values()}
    networks = {row["Id"]:row for row in json.loads(docker("network", "inspect", *network_ids))}
    verify_stack(rows, all_ids, networks, cfg["window_id"], demo["backend_url"], model.port,
                 model_token=model.token(), model_name=model.chat_model)
    run_dir = Path(cfg["run_dir"])
    expires = time.time() + cfg["duration_seconds"]
    deadline = time.monotonic() + cfg["duration_seconds"]
    demo.update(expires_at=expires, ledger_file=str(run_dir / "browser.jsonl"))
    validate_config(demo)
    tokens = {key:read_secret(demo[key], invite=key=="invite_file")
              for key in ("operator_token_file", "resident_token_file", "invite_file")}
    model_token = model.token()
    run_dir.mkdir()  # exclusive claim; never reuse/clear a completed directory
    state = {"window": cfg["window_id"], "status": "STARTING", "cleanup_errors": [], "owned_processes_remaining": None,
             "execution_error": False, "invited_remote_mode": public}
    job = watcher = None
    funnel_started = False
    children, logs = [], []
    stopped = threading.Event()
    def watch():
        while not stopped.wait(.25):
            if time.time() >= expires or time.monotonic() >= deadline:
                state["stop_reason"] = "DEADLINE"
                job.k.TerminateJobObject(job.handle, 1)
                return
            try:
                if available_ram_mib() < model.reserve_ram_mib:
                    state["stop_reason"] = "RESOURCE_RESERVE"
                    job.k.TerminateJobObject(job.handle, 1)
                    return
            except Exception:
                state["stop_reason"] = "RESOURCE_CHECK_FAILED"
                job.k.TerminateJobObject(job.handle, 1)
                return
    def launch(name, args, additions=None):
        if state.get("stop_reason"):
            raise RuntimeError("WINDOW_CLOSED")
        metadata = run_dir / (name + ".command.json")
        log = (run_dir / (name + ".log")).open("xb")
        logs.append(log)
        child = subprocess.Popen([sys._base_executable, "-I", str(ROOT / "scripts/ops/contained_command.py"),
            job.name, str(metadata), *args], env=dict(env, **(additions or {})), cwd=ROOT,
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
        children.append(child)
        until = time.monotonic() + 12
        while not Path(str(metadata) + ".ready").exists():
            if state.get("stop_reason") or child.poll() is not None or time.monotonic() >= until:
                raise RuntimeError("CONTAINMENT_HANDSHAKE")
            time.sleep(.05)
        if state.get("stop_reason"):
            raise RuntimeError("WINDOW_CLOSED")
        Path(str(metadata) + ".go").touch(exist_ok=False)
    try:
        # Freeze private configuration and tokens after the exclusive claim. All failures clean up.
        account = os.environ["USERDOMAIN"] + "\\" + os.environ["USERNAME"]
        command(["icacls", str(run_dir), "/inheritance:r", "/grant:r",
            account + ":(OI)(CI)F", "*S-1-5-18:(OI)(CI)F"])
        for key, token in tokens.items():
            path = run_dir / (key + ".secret")
            path.write_text(token, encoding="ascii")
            demo[key] = str(path)
        (run_dir / "model.secret").write_text(model_token, encoding="ascii")
        model_data.update(model_root=str(model.model_root), bge_python=str(model.bge_python),
            asr_python=str(model.asr_python), token_file=str(run_dir / "model.secret"))
        (run_dir / "model-runtime.json").write_text(json.dumps(model_data), encoding="utf-8")
        (run_dir / "browser.json").write_text(json.dumps(demo), encoding="utf-8")
        (run_dir / "model-window.json").write_text(json.dumps(dict(window_id=cfg["window_id"],
            max_attempts=3, expires_at=expires, record=str(run_dir / "model.jsonl"))), encoding="utf-8")
        job = RequestJob()
        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        launch("ollama", [cfg["ollama_exe"], "serve"], {"OLLAMA_HOST": model.ollama_url.removeprefix("http://"),
            "OLLAMA_MODELS": cfg["ollama_models"], "OLLAMA_NUM_PARALLEL":"1", "OLLAMA_MAX_LOADED_MODELS":"1", "OLLAMA_KEEP_ALIVE":"0"})
        launch("model", [cfg["python"], "-m", "scripts.selfhost.server", "--config", str(run_dir / "model-runtime.json"),
            "--window", str(run_dir / "model-window.json")])
        launch("browser", [cfg["python"], "-m", "scripts.online_demo", "--config", str(run_dir / "browser.json")])
        ready_by = time.monotonic() + 90
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=2) as client:
            while True:
                if state.get("stop_reason") or any(c.poll() is not None for c in children) or time.monotonic() >= ready_by:
                    raise RuntimeError("STARTUP_FAILED")
                try:
                    if probe_owned(client, job, ports, model_token):
                        break
                except httpx.TransportError:
                    pass
                time.sleep(.25)
        if any(c.poll() is not None for c in children) or not all(listener_belongs(job, port) for port in ports):
            raise RuntimeError("FOREIGN_OR_MISSING_LISTENER")
        if public:
            clean_funnel_status(funnel_status())
            funnel_started = True
            launch("funnel", [cfg["tailscale_exe"], "funnel", "--https=443", "http://127.0.0.1:18094"])
        state["status"] = "INVITED_TUNNEL_PENDING" if public else "LOCAL_READY"
        print(state["status"], flush=True)
        # No automatic work order, model call, account/policy modification or public URL claim.
        while not state.get("stop_reason"):
            if any(c.poll() is not None for c in children):
                raise RuntimeError("CHILD_EXITED")
            if not all(listener_belongs(job, port) for port in ports):
                raise RuntimeError("LISTENER_OWNERSHIP_CHANGED")
            time.sleep(.25)
    except KeyboardInterrupt:
        state["stop_reason"] = "OPERATOR_END"
    except BaseException as exc:
        state["stop_reason"] = type(exc).__name__
        state["execution_error"] = True
    finally:
        stopped.set()
        if watcher and watcher.is_alive():
            watcher.join(timeout=2)
        try:
            if job:
                job.terminate_and_confirm()
            state["owned_processes_remaining"] = job.active() if job else 0
        except Exception:
            state["cleanup_errors"].append("PROCESS_EXIT_UNKNOWN")
        finally:
            if job:
                job.close()
        for child in children:
            try:
                child.wait(timeout=10)
            except Exception:
                state["cleanup_errors"].append("LAUNCHER_EXIT_UNKNOWN")
        for log in logs:
            log.close()
        if funnel_started:
            try:
                clean_funnel_status(funnel_status())
            except Exception:
                # Do not globally reset Tailscale or erase settings introduced by another user.
                state["cleanup_errors"].append("FUNNEL_CLOSURE_UNCONFIRMED")
        try:
            inspect()  # revalidate immutable IDs/images/owner labels immediately before stopping
            docker("stop", "--time", "10", *cfg["containers"], timeout=60)
            if any(row["State"]["Running"] for row in inspect()):
                raise RuntimeError("CONTAINER_STILL_RUNNING")
        except Exception:
            state["cleanup_errors"].append("CONTAINER_STOP_UNCONFIRMED")
        for port in ports:
            try:
                if listener_pid(port):
                    state["cleanup_errors"].append("LISTENER_REMAINS")
            except Exception:
                state["cleanup_errors"].append("LISTENER_CHECK_FAILED")
        state["status"] = "CLOSED_WITH_ERRORS" if state["cleanup_errors"] else "CLOSED_CLEAN"
        try:
            (run_dir / "result.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
        except OSError:
            state["cleanup_errors"].append("RESULT_NOT_SAVED")
            state["status"] = "CLOSED_WITH_ERRORS"
        print(json.dumps(state), flush=True)
    return int(bool(state["cleanup_errors"] or state["execution_error"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--approve-config", help="exact reviewed config SHA256; absent means read-only plan")
    args = parser.parse_args()
    try:
        cfg = read_configuration(args.config)
        digest = hashlib.sha256(args.config.read_bytes()).hexdigest()
        if args.approve_config is None:
            print(json.dumps({"status":"PREPARED_NOT_STARTED", "config_sha256":digest,
                              "source_sha256":source_digest(), "max_attempts":3, "seconds":cfg["duration_seconds"]}))
            return 0
        if args.approve_config != digest:
            raise ValueError("WINDOW_APPROVAL_MISMATCH")
        return execute(cfg)
    except (ValueError, OSError, KeyError, RuntimeError) as exc:
        print(json.dumps({"status":"NOT_STARTED_OR_CONTROL_FAILED", "error_type":type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
