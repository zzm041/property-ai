from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.engine import make_url

from app.cli import bootstrap_system_admin, delivery_migrate, delivery_storage_init


ROOT = Path(__file__).resolve().parents[3]


def _load_migration(filename: str, module_name: str):
    path = ROOT / "backend" / "alembic" / "versions" / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _delivery_settings(host: str = "mysql") -> SimpleNamespace:
    return SimpleNamespace(
        app_env="staging",
        active_database_url=(
            f"mysql+pymysql://property_delivery:secret@{host}:3306/"
            "property_ai_delivery_check?charset=utf8mb4"
        ),
    )


def _approve_delivery(monkeypatch: pytest.MonkeyPatch, host: str = "mysql") -> None:
    monkeypatch.setenv("PROPERTY_AI_DELIVERY_MIGRATION", "approved")
    monkeypatch.setenv("PROPERTY_AI_MEDIA_CYCLE_MIGRATION", "approved")
    monkeypatch.setenv(
        "PROPERTY_AI_APPROVE_DELIVERY_TARGET",
        f"{host}:3306/property_ai_delivery_check",
    )


def test_delivery_cli_guards_accept_only_exact_internal_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _delivery_settings()
    _approve_delivery(monkeypatch)
    monkeypatch.setattr(delivery_migrate, "get_settings", lambda: settings)
    assert delivery_migrate._target()[1] == "mysql:3306/property_ai_delivery_check"

    monkeypatch.setattr(delivery_migrate, "get_settings", lambda: _delivery_settings("db"))
    with pytest.raises(RuntimeError, match="exact configured target"):
        delivery_migrate._target()


def test_delivery_cli_accepts_only_the_explicit_media_cycle_drill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = "property_ai_delivery_media_cycle_check"
    settings = SimpleNamespace(
        app_env="staging",
        active_database_url=(
            "mysql+pymysql://delivery_migrate:secret@mysql:3306/"
            f"{database}?charset=utf8mb4"
        ),
    )
    monkeypatch.setenv("PROPERTY_AI_DELIVERY_MIGRATION", "approved")
    monkeypatch.setenv("PROPERTY_AI_MEDIA_CYCLE_DRILL", "approved")
    monkeypatch.setenv(
        "PROPERTY_AI_APPROVE_DELIVERY_TARGET", f"mysql:3306/{database}"
    )
    monkeypatch.setattr(delivery_migrate, "get_settings", lambda: settings)
    assert delivery_migrate._target()[1] == f"mysql:3306/{database}"

    monkeypatch.setenv("PROPERTY_AI_APPROVE_DELIVERY_TARGET", "mysql:3306/other")
    with pytest.raises(RuntimeError, match="TARGET_APPROVAL"):
        delivery_migrate._target()


def test_bootstrap_guard_does_not_accept_test_or_wrong_delivery_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _delivery_settings()
    _approve_delivery(monkeypatch)
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PHONE", "+8613900000000")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "delivery-test-42!")
    monkeypatch.setattr(bootstrap_system_admin, "get_settings", lambda: settings)
    _, phone, _ = bootstrap_system_admin._require_delivery_target()
    assert phone == "+8613900000000"

    monkeypatch.setenv("PROPERTY_AI_APPROVE_DELIVERY_TARGET", "mysql:3306/other")
    with pytest.raises(RuntimeError, match="exact delivery database"):
        bootstrap_system_admin._require_delivery_target()


class _ScalarResult:
    def __init__(self, value) -> None:
        self.value = value

    def scalar(self) -> str:
        return self.value

    def scalar_one(self) -> str:
        return self.value

    def scalars(self):
        values = self.value if isinstance(self.value, list) else [self.value]
        return iter(values)


class _Bind:
    def __init__(self, url: str, result: str) -> None:
        self.engine = SimpleNamespace(url=make_url(url))
        self.result = result

    def execute(self, statement, _parameters=None):
        sql = str(statement)
        if "information_schema.TABLES" in sql:
            return _ScalarResult([
                "work_order_media",
                "work_order_processes",
                "work_order_receipts",
            ])
        if "CONSTRAINT_NAME" in sql:
            return _ScalarResult([
                "uq_user_phones_active_e164",
                "uq_user_phones_active_user",
            ])
        if "SYSTEM_ADMIN" in sql:
            return _ScalarResult(1)
        return _ScalarResult(self.result)


def test_0009_guard_keeps_auth001_and_exact_delivery_branches_separate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_migration(
        "20260919_0009_official_auth_identity_dispatch.py", "migration_0009_delivery_test"
    )
    _approve_delivery(monkeypatch)
    monkeypatch.setenv("APP_ENV", "staging")
    bind = _Bind(
        "mysql+pymysql://u:p@mysql:3306/property_ai_delivery_check",
        "property_ai_delivery_check",
    )
    monkeypatch.setattr(module.op, "get_bind", lambda: bind)
    module._require_isolated_auth_schema()

    monkeypatch.setenv("PROPERTY_AI_APPROVE_DELIVERY_TARGET", "mysql:3306/other")
    with pytest.raises(RuntimeError, match="explicit delivery target"):
        module._require_isolated_auth_schema()


def test_0014_guard_accepts_exact_delivery_and_rejects_wrong_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_migration(
        "20260926_0014_identity_merge_sla_versions.py", "migration_0014_delivery_test"
    )
    _approve_delivery(monkeypatch)
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("PROPERTY_AI_APPROVE_0014_DATABASE", "property_ai_delivery_check")
    monkeypatch.setenv(
        "PROPERTY_AI_APPROVE_0014_TARGET", "mysql:3306/property_ai_delivery_check"
    )
    bind = _Bind(
        "mysql+pymysql://u:p@mysql:3306/property_ai_delivery_check",
        module.down_revision,
    )
    monkeypatch.setattr(module.op, "get_bind", lambda: bind)
    module._execution_guard()

    bind.result = "wrong_revision"
    with pytest.raises(RuntimeError, match="requires actual 0012"):
        module._execution_guard()


def test_0015_guard_accepts_only_exact_live_or_drill_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_migration(
        "20261003_0015_work_order_media_cycle_links.py",
        "migration_0015_delivery_test",
    )
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("PROPERTY_AI_DELIVERY_MIGRATION", "approved")
    monkeypatch.setenv("PROPERTY_AI_MEDIA_CYCLE_MIGRATION", "approved")
    monkeypatch.setenv("PROPERTY_AI_APPROVE_0015_DATABASE", "property_ai_delivery_check")
    monkeypatch.setenv(
        "PROPERTY_AI_APPROVE_0015_TARGET", "mysql:3306/property_ai_delivery_check"
    )
    bind = _Bind(
        "mysql+pymysql://u:p@mysql:3306/property_ai_delivery_check",
        module.down_revision,
    )
    monkeypatch.setattr(module.op, "get_bind", lambda: bind)
    module._execution_guard()

    monkeypatch.setenv("PROPERTY_AI_APPROVE_0015_DATABASE", "other")
    with pytest.raises(RuntimeError, match="exact delivery target"):
        module._execution_guard()


def test_delivery_compose_includes_runtime_isolation_and_persistence_fixes() -> None:
    compose = (ROOT / "deploy" / "docker-compose.delivery.yml").read_text(encoding="utf-8")
    assert 'redis-cli --no-auth-warning -a \\"$${REDIS_PASSWORD}\\"' in compose
    assert "MINIO_ACCESS_KEY_ID: ${MINIO_ROOT_USER:?set MINIO_ROOT_USER}" in compose
    assert "MINIO_SECRET_ACCESS_KEY: ${MINIO_ROOT_PASSWORD:?set MINIO_ROOT_PASSWORD}" in compose
    assert "beat-volume-init:" in compose
    assert "chown -R 10001:10001 /var/run/property-ai" in compose
    assert "127.0.0.1:${HTTP_HOST_PORT:-8093}:80" in compose
    nginx = (ROOT / "deploy" / "nginx.conf").read_text(encoding="utf-8")
    assert "resolver 127.0.0.11 valid=5s ipv6=off;" in nginx
    assert nginx.count("proxy_pass http://$api_upstream") == 4


def test_delivery_storage_init_accepts_only_exact_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(
        app_env="staging",
        storage_provider="production",
        storage_endpoint="http://minio:9000",
        storage_bucket="property-ai-delivery-check",
    )
    monkeypatch.setenv("PROPERTY_AI_DELIVERY_STORAGE_INIT", "approved")
    monkeypatch.setattr(delivery_storage_init, "get_settings", lambda: settings)
    assert delivery_storage_init._settings() is settings

    settings.storage_bucket = "property-ai-other"
    with pytest.raises(RuntimeError, match="exact MinIO target"):
        delivery_storage_init._settings()


def test_delivery_starts_guarded_storage_initializer() -> None:
    compose = (ROOT / "deploy" / "docker-compose.delivery.yml").read_text(
        encoding="utf-8"
    )
    launcher = (ROOT / "deploy" / "delivery.ps1").read_text(encoding="utf-8")
    assert "storage-init:" in compose
    assert "PROPERTY_AI_DELIVERY_STORAGE_INIT: approved" in compose
    assert 'app.cli.delivery_storage_init' in compose
    assert launcher.count("--profile tools run --rm storage-init") == 2


def test_delivery_rag_runtime_is_isolated_bounded_and_not_an_auto_switch() -> None:
    compose = (ROOT / "deploy" / "docker-compose.delivery.yml").read_text(encoding="utf-8")
    runtime = (ROOT / "deploy" / "rag-runtime.ps1").read_text(encoding="utf-8")
    example = (ROOT / "deploy" / ".env.delivery.example").read_text(encoding="utf-8")

    assert "LOCAL_AI_INTERNAL_TOKEN: ${DELIVERY_RAG_INTERNAL_TOKEN:-}" in compose
    assert "RAG_LLM_API_KEY: ${DELIVERY_RAG_INTERNAL_TOKEN:-}" not in compose
    assert '"host.docker.internal:host-gateway"' in compose
    assert 'scope = "rag_only"' in runtime
    assert 'embedding = 12; reranker = 8; qwen = 18' in runtime
    assert 'duration_seconds = 7200' in runtime
    assert "request_active" in runtime
    assert "process start time does not match" in runtime
    assert 'RAG_LLM_PROVIDER=inherit' in example
    assert 'LLM_PROVIDER=development' in example
    assert 'MILVUS_COLLECTION=knowledge_chunks_delivery_check' in example
    assert 'EMBEDDING_DIMENSIONS=8' in example


def test_delivery_work_ai_runtime_is_frozen_scoped_and_resource_guarded() -> None:
    compose=(ROOT/'deploy/docker-compose.delivery.yml').read_text(encoding='utf-8')
    runtime=(ROOT/'deploy/work-ai-runtime.ps1').read_text(encoding='utf-8')
    launcher=(ROOT/'deploy/delivery.ps1').read_text(encoding='utf-8')
    assert 'Add-Type -AssemblyName System.Net.Http' in runtime
    assert '$client.SendAsync($request).GetAwaiter().GetResult()' in runtime
    assert '$client.Send($request)' not in runtime
    assert '$existing = @(Get-Listener)' in runtime
    assert '$listener=@(Get-Listener)' in runtime
    assert 'LLM_API_KEY: ${DELIVERY_WORK_AI_INTERNAL_TOKEN:-}' in compose
    assert 'VLM_API_KEY: ${DELIVERY_WORK_AI_INTERNAL_TOKEN:-}' in compose
    assert 'ASR_API_KEY: ${DELIVERY_WORK_AI_INTERNAL_TOKEN:-}' in compose
    assert 'scope="work_order_only"' in runtime
    assert 'port=11437' in runtime
    assert 'runtime_binding_sha256=' in runtime
    assert 'runtime_binding_sha256=(Get-FileHash' in runtime and '.Hash.ToLowerInvariant()' in runtime
    assert '([DateTimeOffset]$ollama.started_at_utc).UtcDateTime.ToString("o")' in runtime
    assert 'asr_start_ram_mib=11264' in runtime
    assert 'asr=6;embedding=12;reranker=8;qwen=105' in runtime
    assert 'call_baseline=[ordered]@{asr=1;embedding=6;reranker=2;qwen=46}' in runtime
    assert 'duration_seconds=4336;duration_baseline_seconds=535.483' in runtime
    assert 'git -C $RepoRoot archive' in runtime
    assert 'adapter-$operationId.stdout.log' in runtime and 'adapter-$operationId.stderr.log' in runtime
    assert '__SOURCE_COMMIT__' not in runtime and '__ADAPTER_SHA256__' not in runtime
    assert '70BB10C801D695830FCC33630773DB82986427B86962432354A0726AF3A3C676' in runtime
    assert 'AD89639C05C18BF6D78FF28F32A62A105EE5B5ECFC9837EB5700B75DFCAAF320' in runtime
    assert '567B325C7130E6AA7047AB957E5C19B442B66C8DBA29F8FAEDA50EE60830C876' in runtime
    assert '61b762d9775845c47cf0b43f3003a3f4d1cb5519' in runtime
    assert 'E67FCAD3B8DC8DD542A351562142698129DD86936C644E9178C8978EE0978C3B' not in runtime
    assert '2e4c4ba34d67f599c39be95112100a2ce71ffc63' not in runtime
    assert 'call-001-resolution.json' in runtime
    assert 'call-008-replay-resolution-20261002.json' in runtime
    assert 'REAL-AI-INTEGRATION-027-CALL008-REPLAY-RESOLUTION-review.raw.json' in runtime
    assert 'call-013-replay-resolution-20261002.json' in runtime
    assert 'REAL-AI-INTEGRATION-027-CALL013-REPLAY-RESOLUTION-review.raw.json' in runtime
    assert 'call-014-replay-resolution-20261002.json' in runtime
    assert 'REAL-AI-INTEGRATION-027-CALL014-REPLAY-RESOLUTION-review.raw.json' in runtime
    assert 'PROPERTY_AI_LOCAL_QWEN_RUNTIME_MODEL' in runtime
    assert 'qwen38-27b-q3km-g55-ctx8192:7af3b8f' in runtime
    assert 'resolution_root=$ResolutionRoot' in runtime
    assert 'reservation_sha256=$ExpectedReservationSha256.ToLowerInvariant()' in runtime
    assert 'taskkill /PID ([int]$state.listener_pid) /F' in runtime
    assert 'work-ai-start' in launcher and 'work-ai-status' in launcher and 'work-ai-stop' in launcher


def test_delivery_ollama_restart_requires_exact_controlled_stop_evidence() -> None:
    runtime=(ROOT/'deploy/ollama-runtime.ps1').read_text(encoding='utf-8')
    assert 'D571E4CF0AD4456FCCD33D5B985B6D63BA5669C6E5A812391704083952AAE3D5' in runtime
    assert '$ExpectedVersion = "0.34.4"' in runtime
    assert '$version.version -ne $ExpectedVersion' in runtime
    assert 'function Assert-ReviewedRuntimeState' in runtime
    assert runtime.count('Assert-ReviewedRuntimeState $state $version') == 2
    assert '[string]$State.executable_sha256 -ne $ExpectedExeSha256' in runtime
    assert '(Get-FileHash -Algorithm SHA256 -LiteralPath ([string]$State.executable)).Hash -ne $ExpectedExeSha256' in runtime
    assert '[string]$Version.version -ne $ExpectedVersion' in runtime
    assert '[string]$State.host -ne $Endpoint' in runtime
    assert '[int]$State.max_loaded_models -ne 1' in runtime
    assert '[int]$State.concurrency -ne 1' in runtime
    assert '$ContextLength = 8192' in runtime
    assert '[int]$State.context_length -ne $ContextLength' in runtime
    assert '$env:OLLAMA_CONTEXT_LENGTH = [string]$ContextLength' in runtime
    assert '$process = Assert-StateProcess $state' in runtime
    assert 'function Retire-RebootedReceipt' in runtime
    assert 'ollama-0344-restart-resolution.json' in runtime
    assert 'CF966772B58D8E152211F4054F9F32FBAA8D1342688FFD1A657E5FC50C25D9E4' in runtime
    assert '99CEDBDEBD63523CACB40267B58008AB0B921A8CA6987A8A98AA781EA8165B88' in runtime
    assert '128943CB6C1B9CD653E1AB250B7D9D5330C2201184E2DA0B5399E3B4FB788317' in runtime
    assert '$recordedBoot -le $recordedStart' in runtime
    assert '$null -ne $currentPid' in runtime
    assert '$null -ne (Get-ListenerPid)' in runtime
    assert 'retired-restart-{0}.json' in runtime
    assert '$netstatLines = @(& netstat -ano -p TCP)' in runtime
    assert 'if ($LASTEXITCODE -ne 0) { throw "could not inspect the isolated Ollama listener" }' in runtime
    assert 'Get-CimInstance Win32_OperatingSystem -ErrorAction Stop' in runtime
    assert 'Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$State.pid)" -ErrorAction Stop' in runtime
    assert runtime.count('Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$State.pid)" -ErrorAction Stop') == 2
    assert 'calls\\call-003\\result.json' in runtime
    assert 'calls\\call-007\\result.json' in runtime
    assert 'C136825C9391FFC8F48395AB1A3E78A4638FC53F181FCAE84FBF47DF284BF85C' in runtime
    assert 'B725035C11585AF362F6652B7531BDB63DCBC3DF75F75182015EAF513A7603B1' in runtime
    assert 'calls\\call-008\\result.json' in runtime
    assert 'F34D19C9383FED33DE32535CBB48358D2F4C731AA537B349C767343C388FB5C4' in runtime
    assert 'calls\\call-027\\result.json' in runtime
    assert '95F9A3B51A6447796D6318321390562B424A90A96277CA2595EAC4C8CE90CE4B' in runtime
    assert '$evidence.remote_completion -eq "CONTROLLED_STOP"' in runtime
    assert 'Reason = "REMOTE_CLIENT_FAILED"' in runtime
    assert '$evidence.reason -eq $candidate.Reason' in runtime
    assert '$stopped.tree_exit_confirmed -eq $true' in runtime
    assert '$stopped.listener_exit_confirmed -eq $true' in runtime
    assert '[int]$verified.pid -eq [int]$State.pid' in runtime
    assert 'if ($null -eq $binding)' in runtime
    assert 'Get-CimInstance Win32_Process' in runtime
    assert '$currentPid.CreationDate' in runtime
    assert 'if ($sameProcess -or $null -ne (Get-ListenerPid))' in runtime
    assert 'controlled-stop receipt cannot be retired while execution may still exist' in runtime
    assert 'Move-Item -LiteralPath $StatePath -Destination $archive' in runtime
    assert 'ollama-stale-receipt-resolution-20261002.json' in runtime
    assert 'ollama-stale-receipt-resolution-20261002-wo19.json' in runtime
    assert '40210B4BC093BDC22554B7C64CAAB0D7EA29B7293FDD41A555EDBD5CCE3DEEF0' in runtime
    assert 'foreach ($candidate in $AuthorizedStaleResolutions)' in runtime
    assert '$resolution.semantics.historical_exit_mode -ne "UNKNOWN"' in runtime
    assert '$resolution.semantics.current_execution_absent -ne $true' in runtime
    assert '$ragState.qwen_endpoint -ne "http://127.0.0.1:11438"' in runtime


def test_delivery_rag_ollama_restart_requires_exact_stale_receipt_evidence() -> None:
    runtime=(ROOT/'deploy/rag-ollama-runtime.ps1').read_text(encoding='utf-8')
    assert 'rag-ollama-stale-receipt-resolution-20261002.json' in runtime
    assert 'property-ai-local-runtime-stale-receipt-resolution-v1' in runtime
    assert '[string]$resolution.original.receipt_sha256 -ne (Get-FileHash' in runtime
    assert '$null -ne $currentPid -or $null -ne (Get-ListenerPid)' in runtime
    assert '$resolution.semantics.historical_exit_mode -ne "UNKNOWN"' in runtime
    assert 'retired-authorized-stale-{0}.json' in runtime
    assert '$StartLockPath = Join-Path $RuntimeDir "start.lock"' in runtime
    assert '[IO.FileMode]::CreateNew' in runtime
    assert '[IO.FileShare]::None' in runtime
    assert 'Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction Stop' in runtime
    assert 'Get-ProcessTreeIds $process.Id' in runtime
    assert 'RAG Ollama process tree or listener remained after stop' in runtime
    assert 'if ($LASTEXITCODE -ne 0) { throw "could not inspect loopback listener state" }' in runtime


def test_delivery_starts_and_diagnoses_durable_task_publisher() -> None:
    compose=(ROOT/'deploy/docker-compose.delivery.yml').read_text(encoding='utf-8')
    launcher=(ROOT/'deploy/delivery.ps1').read_text(encoding='utf-8')
    example=(ROOT/'deploy/.env.delivery.example').read_text(encoding='utf-8')
    assert 'publisher:' in compose
    assert 'app.worker.task_intent_publisher' in compose
    assert 'DURABLE_TASKS_ENABLED=true' in example
    assert 'up -d api worker publisher beat nginx' in launcher
    assert 'up -d mysql redis rabbitmq etcd minio milvus' in launcher
    assert 'logs --tail 120 api worker publisher beat nginx' in launcher


def test_delivery_backup_uses_binary_safe_compose_copy() -> None:
    script = (ROOT / "deploy" / "delivery.ps1").read_text(encoding="utf-8")
    assert 'Invoke-Compose cp "mysql:$containerTarget" $hostTemp' in script
    assert "Invoke-Compose cp $dump \"mysql:$containerDump\"" in script
    assert "restore target exists or could not be verified" in script
    assert '"${database}_restore_check_029"' in script
    assert '"property_ai_delivery_media_cycle_check"' in script
    assert "media-cycle drill target already exists" in script
    assert "explicit restore database is not the approved delivery target" in script
    assert "approved restore continuation target is not empty" in script
    assert '"media-cycle-migrate"' in script
    assert "GRANT ALL PRIVILEGES ON {2}.* TO ''{0}''@''%''" in script
    assert '"DATABASE_URL=$drillUrl"' in script
    assert "DROP USER IF EXISTS ''{0}''@''%''" in script
    assert "$migrationUserCreated" not in script
    assert '"PROPERTY_AI_MEDIA_CYCLE_MIGRATION=approved"' in script
    assert "backup path must stay inside deploy/backups" in script
    assert "backup target or manifest already exists" in script
    assert "backup target is already reserved by another operation" in script
    assert "[IO.FileMode]::CreateNew" in script
    assert "[IO.FileShare]::None" in script
    assert "[IO.File]::Move($hostTemp, $target)" in script
    assert "[IO.File]::Move($manifestTemp, $manifestPath)" in script
    assert 'format = "property-ai-delivery-backup-v1"' in script
    assert "trusted backup manifest is missing" in script
    assert "backup manifest does not match the dump" in script
    assert "dump contains a forbidden cross-database or global statement" in script
    assert "GRANT ALL PRIVILEGES ON {2}.*" in script
    assert 'exec mysql -u\"$DELIVERY_RESTORE_USER\"' in script
    assert 'exec mysql -uroot -p\"$MYSQL_ROOT_PASSWORD\" \'\'{0}\'\'' not in script
