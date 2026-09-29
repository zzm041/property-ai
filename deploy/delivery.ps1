param(
    [Parameter(Position = 0, Mandatory = $true)]
    [ValidateSet("config", "init", "up", "stop", "status", "backup", "restore-check", "diagnose", "ollama-start", "ollama-stop", "ollama-status", "rag-start", "rag-stop", "rag-status", "work-ai-start", "work-ai-stop", "work-ai-status")]
    [string]$Action,
    [Parameter(Position = 1)]
    [string]$Argument,
    [Parameter(Position = 2)]
    [string]$RestoreDatabase
)

$ErrorActionPreference = "Stop"
$DeployDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ComposeFile = Join-Path $DeployDir "docker-compose.delivery.yml"
$EnvFile = Join-Path $DeployDir ".env.delivery"
$BootstrapEnvFile = Join-Path $DeployDir ".env.delivery.bootstrap"
$BackupDir = Join-Path $DeployDir "backups"
$OllamaRuntime = Join-Path $DeployDir "ollama-runtime.ps1"
$RagRuntime = Join-Path $DeployDir "rag-runtime.ps1"
$RagTokenPath = Join-Path (Split-Path -Parent $DeployDir) "tmp\delivery-rag-runtime\internal-token.txt"
$WorkAiRuntime = Join-Path $DeployDir "work-ai-runtime.ps1"
$WorkAiTokenPath = Join-Path (Split-Path -Parent $DeployDir) "tmp\delivery-work-ai-runtime\internal-token.txt"

if (-not (Test-Path -LiteralPath $EnvFile) -or -not (Test-Path -LiteralPath $BootstrapEnvFile)) {
    throw "Missing delivery env files; copy both examples and replace every placeholder."
}

function Invoke-Compose {
    if (Test-Path -LiteralPath $RagTokenPath -PathType Leaf) {
        $env:DELIVERY_RAG_INTERNAL_TOKEN = (Get-Content -Raw -LiteralPath $RagTokenPath).Trim()
    }
    if (Test-Path -LiteralPath $WorkAiTokenPath -PathType Leaf) {
        $env:DELIVERY_WORK_AI_INTERNAL_TOKEN = (Get-Content -Raw -LiteralPath $WorkAiTokenPath).Trim()
    }
    & docker compose --env-file $EnvFile -f $ComposeFile @args
    if ($LASTEXITCODE -ne 0) { throw "docker compose failed ($LASTEXITCODE)" }
}

function Read-DeliveryValue([string]$Name) {
    $line = Get-Content -LiteralPath $EnvFile | Where-Object { $_ -match "^$([regex]::Escape($Name))=" } | Select-Object -Last 1
    if (-not $line) { return $null }
    return ($line -split "=", 2)[1].Trim()
}

function Assert-NoPlaceholders {
    $unsafe = Select-String -LiteralPath $EnvFile,$BootstrapEnvFile -Pattern "replace-|change-me|<[^>]+>" -SimpleMatch:$false
    if ($unsafe) { throw "deploy/.env.delivery still contains placeholder secrets" }
    Invoke-Compose config --quiet
}

function Resolve-BackupPath([string]$Value, [bool]$MustExist) {
    $backupRoot = [IO.Path]::GetFullPath($BackupDir) + [IO.Path]::DirectorySeparatorChar
    $candidate = if ([IO.Path]::IsPathRooted($Value)) {
        [IO.Path]::GetFullPath($Value)
    } else {
        [IO.Path]::GetFullPath((Join-Path $BackupDir $Value))
    }
    if (-not $candidate.StartsWith($backupRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "backup path must stay inside deploy/backups"
    }
    if ([IO.Path]::GetExtension($candidate) -ne ".sql") {
        throw "backup path must end with .sql"
    }
    if ($MustExist -and -not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
        throw "dump does not exist"
    }
    return $candidate
}

function Read-BackupManifest([string]$DumpPath, [string]$Database) {
    $manifestPath = "$DumpPath.manifest.json"
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw "trusted backup manifest is missing"
    }
    $manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
    $actual = Get-Item -LiteralPath $DumpPath
    $actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $DumpPath).Hash
    if (
        $manifest.format -ne "property-ai-delivery-backup-v1" -or
        $manifest.database -ne $Database -or
        [long]$manifest.bytes -ne $actual.Length -or
        $manifest.sha256 -ne $actualHash
    ) {
        throw "backup manifest does not match the dump"
    }
    $forbidden = Select-String -LiteralPath $DumpPath -Pattern "(?i)^\s*(USE|CREATE\s+DATABASE|DROP\s+DATABASE|GRANT|REVOKE|CREATE\s+USER|ALTER\s+USER|DROP\s+USER|SET\s+GLOBAL|INSTALL\s+PLUGIN|UNINSTALL\s+PLUGIN)\b"
    if ($forbidden) { throw "dump contains a forbidden cross-database or global statement" }
    return $manifest
}

switch ($Action) {
    "work-ai-start" {
        & $WorkAiRuntime start
        if ($LASTEXITCODE -ne 0) { throw "delivery work AI start failed ($LASTEXITCODE)" }
    }
    "work-ai-stop" {
        & $WorkAiRuntime stop
        if ($LASTEXITCODE -ne 0) { throw "delivery work AI stop failed ($LASTEXITCODE)" }
    }
    "work-ai-status" {
        & $WorkAiRuntime status
        if ($LASTEXITCODE -ne 0) { throw "delivery work AI status failed ($LASTEXITCODE)" }
    }
    "ollama-start" {
        & $OllamaRuntime start
        if ($LASTEXITCODE -ne 0) { throw "delivery Ollama start failed ($LASTEXITCODE)" }
    }
    "ollama-stop" {
        & $OllamaRuntime stop
        if ($LASTEXITCODE -ne 0) { throw "delivery Ollama stop failed ($LASTEXITCODE)" }
    }
    "ollama-status" {
        & $OllamaRuntime status
        if ($LASTEXITCODE -ne 0) { throw "delivery Ollama status failed ($LASTEXITCODE)" }
    }
    "rag-start" {
        & $RagRuntime start
        if ($LASTEXITCODE -ne 0) { throw "delivery RAG start failed ($LASTEXITCODE)" }
    }
    "rag-stop" {
        & $RagRuntime stop
        if ($LASTEXITCODE -ne 0) { throw "delivery RAG stop failed ($LASTEXITCODE)" }
    }
    "rag-status" {
        & $RagRuntime status
        if ($LASTEXITCODE -ne 0) { throw "delivery RAG status failed ($LASTEXITCODE)" }
    }
    "config" {
        Assert-NoPlaceholders
        Write-Output "delivery configuration is syntactically valid"
    }
    "init" {
        Assert-NoPlaceholders
        Invoke-Compose up -d mysql redis rabbitmq etcd minio milvus
        Invoke-Compose --profile tools build migrate
        Invoke-Compose --profile tools run --rm migrate
        Invoke-Compose up -d api worker publisher beat nginx
        Invoke-Compose ps
    }
    "up" {
        Assert-NoPlaceholders
        Invoke-Compose up -d mysql redis rabbitmq etcd minio milvus api worker publisher beat nginx
        Invoke-Compose ps
    }
    "stop" {
        Invoke-Compose down
        Write-Output "services stopped; named volumes were retained"
    }
    "status" {
        Invoke-Compose ps
        $port = Read-DeliveryValue "HTTP_HOST_PORT"
        if (-not $port) { $port = "8093" }
        Invoke-RestMethod -TimeoutSec 5 -Uri "http://127.0.0.1:$port/healthz" | ConvertTo-Json -Depth 6
        Invoke-RestMethod -TimeoutSec 5 -Uri "http://127.0.0.1:$port/readyz" | ConvertTo-Json -Depth 6
    }
    "backup" {
        Assert-NoPlaceholders
        New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
        $database = Read-DeliveryValue "MYSQL_DATABASE"
        if ($database -notmatch "^[A-Za-z0-9_]+$") { throw "unsafe MYSQL_DATABASE" }
        $stamp = Get-Date -AsUTC -Format "yyyyMMddTHHmmssZ"
        $filename = if ($Argument) { $Argument } else { "$database-$stamp-$([guid]::NewGuid().ToString('N').Substring(0,8)).sql" }
        $target = Resolve-BackupPath $filename $false
        $manifestPath = "$target.manifest.json"
        $reservationPath = "$target.reserve"
        $reservation = $null
        try {
            try {
                $reservation = [IO.File]::Open(
                    $reservationPath,
                    [IO.FileMode]::CreateNew,
                    [IO.FileAccess]::Write,
                    [IO.FileShare]::None
                )
            } catch [IO.IOException] {
                throw "backup target is already reserved by another operation"
            }
            if ((Test-Path -LiteralPath $target) -or (Test-Path -LiteralPath $manifestPath)) {
                throw "backup target or manifest already exists; it will not be overwritten"
            }
            $operationId = [guid]::NewGuid().ToString("N")
            $containerTarget = "/tmp/$database-$operationId.sql"
            $hostTemp = Join-Path $BackupDir ".$([IO.Path]::GetFileName($target)).$operationId.tmp"
            $manifestTemp = "$hostTemp.manifest.json"
            try {
                $dumpCommand = 'exec mysqldump --single-transaction --routines --triggers -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE" > ''{0}''' -f $containerTarget
                Invoke-Compose exec -T mysql sh -c $dumpCommand
                Invoke-Compose cp "mysql:$containerTarget" $hostTemp
                if ((Get-Item -LiteralPath $hostTemp).Length -eq 0) { throw "backup failed" }
                $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $hostTemp).Hash
                $manifest = [ordered]@{
                    format = "property-ai-delivery-backup-v1"
                    database = $database
                    bytes = (Get-Item -LiteralPath $hostTemp).Length
                    sha256 = $hash
                    created_at_utc = (Get-Date).ToUniversalTime().ToString("o")
                }
                [IO.File]::WriteAllText(
                    $manifestTemp,
                    ($manifest | ConvertTo-Json),
                    [Text.UTF8Encoding]::new($false)
                )
                [IO.File]::Move($hostTemp, $target)
                [IO.File]::Move($manifestTemp, $manifestPath)
            } finally {
                Invoke-Compose exec -T mysql rm -f $containerTarget
                if (Test-Path -LiteralPath $hostTemp) {
                    Remove-Item -LiteralPath $hostTemp -Force
                }
                if (Test-Path -LiteralPath $manifestTemp) {
                    Remove-Item -LiteralPath $manifestTemp -Force
                }
            }
        } finally {
            if ($reservation) {
                $reservation.Dispose()
                Remove-Item -LiteralPath $reservationPath -Force
            }
        }
        Write-Output "backup=$target manifest=$manifestPath sha256=$hash"
    }
    "restore-check" {
        Assert-NoPlaceholders
        if (-not $Argument) { throw "restore-check requires an existing SQL dump path" }
        $database = Read-DeliveryValue "MYSQL_DATABASE"
        if ($database -notmatch "^[A-Za-z0-9_]+$") { throw "unsafe MYSQL_DATABASE" }
        $dump = Resolve-BackupPath $Argument $true
        $null = Read-BackupManifest $dump $database
        $restore = if ($RestoreDatabase) { $RestoreDatabase } else { "${database}_restore_check" }
        $approvedRestore = "${database}_restore_check_029"
        if ($RestoreDatabase -and $restore -ne $approvedRestore) {
            throw "explicit restore database is not the approved delivery target"
        }
        if ($restore -notmatch "^[A-Za-z0-9_]+_restore_check(?:_029)?$") { throw "unsafe restore database" }
        $existsCommand = 'mysql -N -uroot -p"$MYSQL_ROOT_PASSWORD" -e "SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name=''{0}''"' -f $restore
        $exists = (& docker compose --env-file $EnvFile -f $ComposeFile exec -T mysql sh -c $existsCommand | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or $exists -notin @("0", "1")) {
            throw "restore target exists or could not be verified; it will not be overwritten"
        }
        if ($exists -eq "1") {
            if (-not $RestoreDatabase -or $restore -ne $approvedRestore) {
                throw "restore target exists or could not be verified; it will not be overwritten"
            }
            $tableCountCommand = 'mysql -N -uroot -p"$MYSQL_ROOT_PASSWORD" -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=''{0}''"' -f $restore
            $tableCount = (& docker compose --env-file $EnvFile -f $ComposeFile exec -T mysql sh -c $tableCountCommand | Out-String).Trim()
            if ($LASTEXITCODE -ne 0 -or $tableCount -ne "0") {
                throw "approved restore continuation target is not empty; it will not be overwritten"
            }
        } else {
            $createCommand = 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -e "CREATE DATABASE {0} CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"' -f $restore
            Invoke-Compose exec -T mysql sh -c $createCommand
        }
        $containerDump = "/tmp/delivery-restore-$([guid]::NewGuid().ToString('N')).sql"
        $restoreUser = "delivery_restore_$([guid]::NewGuid().ToString('N').Substring(0,12))"
        $restorePassword = [guid]::NewGuid().ToString("N")
        $createUserCommand = 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -e "CREATE USER ''{0}''@''localhost'' IDENTIFIED BY ''{1}''; GRANT ALL PRIVILEGES ON {2}.* TO ''{0}''@''localhost'';"' -f $restoreUser,$restorePassword,$restore
        $dropUserCommand = 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -e "DROP USER IF EXISTS ''{0}''@''localhost''"' -f $restoreUser
        try {
            Invoke-Compose exec -T mysql sh -c $createUserCommand
            Invoke-Compose cp $dump "mysql:$containerDump"
            $restoreCommand = 'exec mysql -u"$DELIVERY_RESTORE_USER" "$DELIVERY_RESTORE_DATABASE" < "$DELIVERY_RESTORE_DUMP"'
            Invoke-Compose exec -T -e "MYSQL_PWD=$restorePassword" -e "DELIVERY_RESTORE_USER=$restoreUser" -e "DELIVERY_RESTORE_DATABASE=$restore" -e "DELIVERY_RESTORE_DUMP=$containerDump" mysql sh -c $restoreCommand
        } finally {
            Invoke-Compose exec -T mysql sh -c $dropUserCommand
            Invoke-Compose exec -T mysql rm -f $containerDump
        }
        Write-Output "restored to isolated database $restore with a target-only temporary account; primary database was not changed"
    }
    "diagnose" {
        Invoke-Compose ps
        Invoke-Compose logs --tail 120 api worker publisher beat nginx mysql rabbitmq redis milvus
    }
}
