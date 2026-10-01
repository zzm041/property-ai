$ErrorActionPreference = "Stop"
$RepoRoot = "D:\property-ai"
$OutPath = Join-Path $RepoRoot "reports\real-ai-integration-027\delivery-auto-8093\final-runtime-raw.json"
if (Test-Path -LiteralPath $OutPath) { throw "final runtime evidence already exists" }

$dbCode = @'
import hashlib
import json
import os
from sqlalchemy import create_engine, text

engine = create_engine(os.environ["DATABASE_URL"])

def rows(connection, sql):
    return [dict(row._mapping) for row in connection.execute(text(sql)).all()]

with engine.connect() as connection:
    work_orders = rows(connection, """
        SELECT id, tenant_id, property_id, reporter_user_id, status,
               process_reason, current_assignment_id, version
        FROM work_orders WHERE id BETWEEN 12 AND 17 ORDER BY id
    """)
    task_intents = rows(connection, """
        SELECT id, operation, resource_id, resource_version, generation,
               status, publish_attempt, execution_attempt, last_error_code
        FROM task_intents
        WHERE operation = 'ANALYZE_WORK_ORDER' AND resource_id BETWEEN 12 AND 17
        ORDER BY resource_id, generation, id
    """)
    analyses = rows(connection, """
        SELECT work_order_id, analysis_version, status, category, subcategory,
               severity, priority, risk, required_role, required_skill,
               confidence, prompt_version, error_code
        FROM work_order_analyses WHERE work_order_id BETWEEN 12 AND 17
        ORDER BY work_order_id
    """)
    assignments = rows(connection, """
        SELECT a.id, a.work_order_id, a.employee_id, a.status,
               a.assigned_source, a.reason, w.tenant_id, w.property_id,
               e.tenant_id AS employee_tenant_id
        FROM work_order_assignments a
        JOIN work_orders w ON w.id = a.work_order_id
        JOIN employees e ON e.id = a.employee_id
        WHERE a.work_order_id BETWEEN 12 AND 17
        ORDER BY a.work_order_id, a.id
    """)
    daily_statuses = rows(connection, """
        SELECT employee_id, tenant_id, property_id, business_date, on_duty, current_load
        FROM employee_daily_statuses WHERE business_date = '2026-10-02'
        ORDER BY property_id, employee_id
    """)
    contents = rows(connection, """
        SELECT work_order_id, CHAR_LENGTH(COALESCE(asr_text, '')) AS asr_characters,
               COALESCE(SHA2(asr_text, 256), '') AS asr_sha256
        FROM work_order_contents WHERE work_order_id BETWEEN 12 AND 17
        ORDER BY work_order_id
    """)
    media = rows(connection, """
        SELECT work_order_id, kind, file_id, taken_phase, uploader_user_id
        FROM work_order_media WHERE work_order_id BETWEEN 12 AND 17
        ORDER BY work_order_id, id
    """)
    log_counts = rows(connection, """
        SELECT work_order_id, event_code, COUNT(*) AS event_count
        FROM work_order_logs WHERE work_order_id BETWEEN 12 AND 17
        GROUP BY work_order_id, event_code ORDER BY work_order_id, event_code
    """)
    notification_counts = rows(connection, """
        SELECT resource_id AS work_order_id, user_id, COUNT(*) AS notification_count,
               SUM(CASE WHEN read_at IS NOT NULL THEN 1 ELSE 0 END) AS read_count
        FROM notifications
        WHERE resource_type = 'WORK_ORDER' AND resource_id BETWEEN 12 AND 17
        GROUP BY resource_id, user_id ORDER BY resource_id, user_id
    """)
    delivery_counts = rows(connection, """
        SELECT n.resource_id AS work_order_id, d.channel, d.status,
               COUNT(*) AS delivery_count, SUM(d.attempt) AS total_attempts
        FROM notification_deliveries d
        JOIN notifications n ON n.id = d.notification_id
        WHERE n.resource_type = 'WORK_ORDER' AND n.resource_id BETWEEN 12 AND 17
        GROUP BY n.resource_id, d.channel, d.status
        ORDER BY n.resource_id, d.channel, d.status
    """)

print(json.dumps({
    "work_orders": work_orders,
    "task_intents": task_intents,
    "analyses": analyses,
    "assignments": assignments,
    "daily_statuses": daily_statuses,
    "contents": contents,
    "media": media,
    "log_counts": log_counts,
    "notification_counts": notification_counts,
    "delivery_counts": delivery_counts,
}, default=str, ensure_ascii=False))
'@

$dbRaw = & docker exec property-ai-delivery-check-api-1 python -c $dbCode
if ($LASTEXITCODE -ne 0) { throw "read-only database collection failed" }
$database = $dbRaw | ConvertFrom-Json

$vhostsRaw = & docker exec property-ai-delivery-check-rabbitmq-1 rabbitmqctl list_vhosts name --formatter json
if ($LASTEXITCODE -ne 0) { throw "vhost collection failed" }
$queuesRaw = & docker exec property-ai-delivery-check-rabbitmq-1 rabbitmqctl list_queues -p property_ai_delivery_check name messages_ready messages_unacknowledged consumers --formatter json
if ($LASTEXITCODE -ne 0) { throw "queue collection failed" }

$workStatePath = Join-Path $RepoRoot "tmp\delivery-work-ai-runtime\current.json"
$ragStatePath = Join-Path $RepoRoot "tmp\delivery-rag-runtime\current.json"
$ragOllamaStatePath = Join-Path $RepoRoot "tmp\delivery-rag-ollama-runtime\current.json"
$workTokenPath = Join-Path $RepoRoot "tmp\delivery-work-ai-runtime\internal-token.txt"
$ragTokenPath = Join-Path $RepoRoot "tmp\delivery-rag-runtime\internal-token.txt"
$workState = Get-Content -Raw -LiteralPath $workStatePath | ConvertFrom-Json
$ragState = Get-Content -Raw -LiteralPath $ragStatePath | ConvertFrom-Json
$ragOllamaState = Get-Content -Raw -LiteralPath $ragOllamaStatePath | ConvertFrom-Json
$workHealth = Invoke-RestMethod -Uri "http://127.0.0.1:11437/healthz" -Headers @{ Authorization = "Bearer $((Get-Content -Raw -LiteralPath $workTokenPath).Trim())" }
$ragHealth = Invoke-RestMethod -Uri "http://127.0.0.1:11436/healthz" -Headers @{ Authorization = "Bearer $((Get-Content -Raw -LiteralPath $ragTokenPath).Trim())" }
$apiHealth = Invoke-RestMethod -Uri "http://127.0.0.1:8093/healthz"
$apiReady = Invoke-RestMethod -Uri "http://127.0.0.1:8093/readyz"

$containers = foreach ($name in @(
    "property-ai-delivery-check-api-1",
    "property-ai-delivery-check-worker-1",
    "property-ai-delivery-check-publisher-1",
    "property-ai-delivery-check-beat-1"
)) {
    $inspect = (& docker inspect $name | ConvertFrom-Json)[0]
    [ordered]@{
        name = $name
        image_id = [string]$inspect.Image
        status = [string]$inspect.State.Status
        started_at = [string]$inspect.State.StartedAt
    }
}

$calls = foreach ($number in 16..26) {
    $directory = Join-Path $RepoRoot ("tmp\delivery-work-ai-runtime\calls\call-{0:d3}" -f $number)
    $input = Get-Content -Raw -LiteralPath (Join-Path $directory "input.json") | ConvertFrom-Json
    $result = Get-Content -Raw -LiteralPath (Join-Path $directory "result.json") | ConvertFrom-Json
    [ordered]@{
        call = $number
        kind = [string]$input.kind
        status = [string]$result.status
        model = [string]$result.model
        revision = [string]$result.revision
        total_seconds = [double]$result.total_seconds
        owned_exit_confirmed = [bool]$result.owned_exit_confirmed
        cleanup_error = $result.cleanup_error
        input_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $directory "input.json")).Hash
        result_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $directory "result.json")).Hash
    }
}

$evidence = [ordered]@{
    format = "real-ai-integration-027-final-runtime-raw-v1"
    captured_at_utc = [DateTime]::UtcNow.ToString("o")
    collection = [ordered]@{
        database = "read-only SQL through the running 8093 API container"
        rabbitmq = "rabbitmqctl metadata only; message bodies not read"
        runtime = "loopback health and immutable local receipts; tokens not persisted"
        business_write = $false
    }
    database = $database
    rabbitmq = [ordered]@{
        vhosts = ($vhostsRaw | ConvertFrom-Json)
        queues = ($queuesRaw | ConvertFrom-Json)
    }
    runtime = [ordered]@{
        api_health = $apiHealth
        api_ready = $apiReady
        containers = $containers
        work_ai_state = $workState
        work_ai_health = $workHealth
        rag_state = $ragState
        rag_health = $ragHealth
        rag_ollama_state = $ragOllamaState
    }
    calls = $calls
}

[IO.File]::WriteAllText($OutPath, ($evidence | ConvertTo-Json -Depth 12), [Text.UTF8Encoding]::new($false))
[ordered]@{
    output = $OutPath
    work_orders = @($database.work_orders).Count
    assignments = @($database.assignments).Count
    calls = @($calls).Count
    queue_ready = @($evidence.rabbitmq.queues | Where-Object name -eq "property_ai.delivery_check")[0].messages_ready
} | ConvertTo-Json -Depth 4
