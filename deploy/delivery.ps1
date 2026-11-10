param(
    [Parameter(Position = 0, Mandatory = $true)]
    [ValidateSet("config", "init", "up", "stop", "status", "backup", "restore-check", "media-cycle-migrate", "diagnose", "ollama-start", "ollama-stop", "ollama-status", "rag-ollama-start", "rag-ollama-stop", "rag-ollama-status", "rag-start", "rag-stop", "rag-status", "work-ai-start", "work-ai-stop", "work-ai-status", "release", "verify-image")]
    [string]$Action,
    [Parameter(Position = 1)]
    [string]$Argument,
    [Parameter(Position = 2)]
    [string]$RestoreDatabase
)

$ErrorActionPreference = "Stop"
$DeployDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $DeployDir
$ComposeFile = Join-Path $DeployDir "docker-compose.delivery.yml"
$EnvFile = Join-Path $DeployDir ".env.delivery"
$BootstrapEnvFile = Join-Path $DeployDir ".env.delivery.bootstrap"
$BackupDir = Join-Path $DeployDir "backups"
$OllamaRuntime = Join-Path $DeployDir "ollama-runtime.ps1"
$RagOllamaRuntime = Join-Path $DeployDir "rag-ollama-runtime.ps1"
$RagRuntime = Join-Path $DeployDir "rag-runtime.ps1"
$RagTokenPath = Join-Path (Split-Path -Parent $DeployDir) "tmp\delivery-rag-runtime\internal-token.txt"
$WorkAiRuntime = Join-Path $DeployDir "work-ai-runtime.ps1"
$WorkAiTokenPath = Join-Path (Split-Path -Parent $DeployDir) "tmp\delivery-work-ai-runtime\internal-token.txt"

if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
    throw "Missing delivery env file; copy .env.delivery.example and replace every placeholder."
}
$RequiresBootstrap = $Action -in @("init", "media-cycle-migrate")
if ($RequiresBootstrap -and -not (Test-Path -LiteralPath $BootstrapEnvFile -PathType Leaf)) {
    throw "Missing .env.delivery.bootstrap; restore initialization credentials for this migration action."
}

function Invoke-Compose {
    if (Test-Path -LiteralPath $RagTokenPath -PathType Leaf) {
        $env:DELIVERY_RAG_INTERNAL_TOKEN = (Get-Content -Raw -LiteralPath $RagTokenPath).Trim()
    }
    if (Test-Path -LiteralPath $WorkAiTokenPath -PathType Leaf) {
        $env:DELIVERY_WORK_AI_INTERNAL_TOKEN = (Get-Content -Raw -LiteralPath $WorkAiTokenPath).Trim()
    }
    $composeFiles = @("-f", $ComposeFile)
    if ($RequiresBootstrap) {
        $composeFiles += @("-f", (Join-Path $DeployDir "docker-compose.bootstrap.yml"))
    }
    & docker compose --env-file $EnvFile @composeFiles @args
    if ($LASTEXITCODE -ne 0) { throw "docker compose failed ($LASTEXITCODE)" }
}

function Read-DeliveryValue([string]$Name) {
    $line = Get-Content -LiteralPath $EnvFile | Where-Object { $_ -match "^$([regex]::Escape($Name))=" } | Select-Object -Last 1
    if (-not $line) { return $null }
    return ($line -split "=", 2)[1].Trim()
}

function Assert-NoRetainedRollbackContainers {
    $project = if ($env:COMPOSE_PROJECT_NAME) { $env:COMPOSE_PROJECT_NAME } else { Read-DeliveryValue "COMPOSE_PROJECT_NAME" }
    if (-not $project) { $project = "property-ai-delivery-check" }
    $names = & docker ps -a --filter "label=com.docker.compose.project=$project" --format "{{.Names}}"
    if ($LASTEXITCODE -ne 0) { throw "Cannot verify retained rollback containers; refusing Compose mutation." }
    if ($names | Where-Object { $_.StartsWith($project + "-") -and $_ -match "-rollback-" }) {
        throw "Retained rollback containers exist. Use the verified exact-container cutover/recovery procedure in reports/delivery-closeout-20261103/RUNNING-CANDIDATE.md; Compose up/down would affect retained evidence."
    }
}

function Get-AiRuntimeSelection {
    $selectionPath = Join-Path $DeployDir "ai-runtime-selection.json"
    if (-not (Test-Path -LiteralPath $selectionPath)) { return @{} }
    $selection = Get-Content -Raw -LiteralPath $selectionPath | ConvertFrom-Json
    if (
        $selection.format -ne "property-ai-runtime-selection-v1" -or
        $selection.manifest -isnot [string] -or
        [string]::IsNullOrWhiteSpace($selection.manifest) -or
        $selection.sha256 -isnot [string] -or
        $selection.sha256 -cnotmatch "^[0-9a-f]{64}$"
    ) { throw "invalid delivery AI runtime selection; refusing historical fallback" }
    $repoRoot = [IO.Path]::GetFullPath((Split-Path -Parent $DeployDir))
    if ([IO.Path]::IsPathRooted($selection.manifest)) { throw "selected manifest must be workspace-relative" }
    $manifest = [IO.Path]::GetFullPath((Join-Path $repoRoot $selection.manifest))
    if (-not $manifest.StartsWith($repoRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "selected manifest must stay inside the workspace"
    }
    # Each runtime verifies this digest, all artifact files and the paired
    # backend manifest before it starts. Never fall back after a bad selection.
    return @{ ArtifactManifest = $manifest; ArtifactSha256 = $selection.sha256 }
}

function Assert-NoPlaceholders {
    $checkedFiles = @($EnvFile)
    if ($RequiresBootstrap) { $checkedFiles += $BootstrapEnvFile }
    $unsafe = Select-String -LiteralPath $checkedFiles -Pattern "replace-|change-me|<[^>]+>" -SimpleMatch:$false
    if ($unsafe) { throw "deploy/.env.delivery still contains placeholder secrets" }
    Invoke-Compose config --quiet
}

function Resolve-DeliverySource {
    # PA-116: the image tag is derived from the source commit, never a mutable
    # label, so a rebuilt tree can never silently reuse an older image.
    $sha = (& git -C $RepoRoot rev-parse --short=12 HEAD | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $sha -notmatch "^[0-9a-f]{12}$") {
        throw "cannot resolve the delivery source commit"
    }
    $dirty = (& git -C $RepoRoot status --porcelain --untracked-files=no | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { throw "cannot verify the delivery worktree state" }
    $tag = if ($dirty) { "$sha-wip" } else { $sha }
    return [pscustomobject]@{ Sha = $sha; Tag = $tag; Dirty = [bool]$dirty }
}

function Assert-DeliveryImageTag([string]$Tag) {
    if ($Tag -notmatch "^src-[0-9a-f]{12}(-wip)?$") {
        throw "DELIVERY_IMAGE_TAG must be content-addressed (src-<12-hex commit>); '$Tag' is a mutable tag (PA-116)"
    }
}

function Get-DeliveryProjectName {
    $project = if ($env:COMPOSE_PROJECT_NAME) { $env:COMPOSE_PROJECT_NAME } else { Read-DeliveryValue "COMPOSE_PROJECT_NAME" }
    if (-not $project) { $project = "property-ai-delivery-check" }
    return $project
}

function Get-DeliveryServiceContainer([string]$Service) {
    $project = Get-DeliveryProjectName
    $output = & docker ps --filter "label=com.docker.compose.project=$project" --filter "label=com.docker.compose.service=$Service" --format "{{.Names}}"
    if ($LASTEXITCODE -ne 0) { throw "cannot list delivery containers; refusing an unverified stack" }
    $names = @($output | Where-Object { $_ })
    if ($names.Count -ne 1) {
        throw "delivery service '$Service' must have exactly one running container (found $($names.Count))"
    }
    return $names[0]
}

function Get-DeliveryImageFacts([string]$Service) {
    $container = Get-DeliveryServiceContainer $Service
    $image = (& docker inspect --format "{{.Image}}" $container | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $image -notmatch "^sha256:[0-9a-f]{64}$") {
        throw "cannot resolve the image id of delivery service '$Service'"
    }
    $sourceSha = (& docker inspect --format '{{index .Config.Labels "property-ai.source-sha"}}' $image | Out-String).Trim()
    return [pscustomobject]@{ Service = $Service; Container = $container; Image = $image; SourceSha = $sourceSha }
}

function Assert-DeliveryImageDigest {
    $facts = @(foreach ($service in @("api", "worker", "beat", "publisher")) { Get-DeliveryImageFacts $service })
    $digests = @($facts.Image | Sort-Object -Unique)
    if ($digests.Count -ne 1) {
        $detail = ($facts | ForEach-Object { "$($_.Service)=$($_.Image)" }) -join ", "
        throw "delivery services run different images ($detail); recreate api/worker/beat/publisher together (PA-116)"
    }
    $sources = @($facts.SourceSha | Sort-Object -Unique | Where-Object { $_ -and $_ -ne "unknown" })
    if ($sources.Count -ne 1) {
        $detail = ($facts | ForEach-Object { "$($_.Service)=$($_.SourceSha)" }) -join ", "
        throw "delivery image does not carry a single baked source commit ($detail)"
    }
    return $facts
}

function Assert-DeliveryImageContent([object[]]$Facts) {
    $source = Resolve-DeliverySource
    $baked = @($Facts.SourceSha | Sort-Object -Unique)[0]
    if (-not $baked.StartsWith($source.Sha)) {
        throw "delivery image was built from $baked but HEAD is $($source.Sha)"
    }
    $contract = [ordered]@{
        "ai.py" = "backend/app/domain/ai.py"
        "observed_event.py" = "backend/app/domain/observed_event.py"
    }
    foreach ($row in $Facts) {
        $provenance = (Invoke-Compose exec -T $row.Service python -m app.cli.build_provenance | Out-String) | ConvertFrom-Json
        if ($provenance.shadow_source_tree) {
            throw "$($row.Service) still carries a second app copy at $($provenance.shadow_source_tree) (PA-116)"
        }
        $copies = @($provenance.app_copies)
        if ($copies.Count -ne 1) {
            throw "$($row.Service) resolves $($copies.Count) app copies; exactly one is allowed"
        }
        foreach ($name in $contract.Keys) {
            $relative = $contract[$name]
            $drift = (& git -C $RepoRoot status --porcelain -- $relative | Out-String).Trim()
            if ($drift) { throw "contract file $relative differs from HEAD; refusing to pin an image to a commit" }
            $expected = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $RepoRoot $relative)).Hash.ToLower()
            $actual = $provenance.files.PSObject.Properties[$name].Value.sha256
            if (-not $actual) { throw "$($row.Service) does not report $name provenance" }
            if ($actual -ne $expected) {
                throw "$($row.Service) runs $($name)=$actual but HEAD is $expected"
            }
        }
    }
    return $baked
}

function Invoke-DeliveryRelease([object]$Source) {
    $tag = "src-$($Source.Tag)"
    Assert-DeliveryImageTag $tag
    $env:DELIVERY_SOURCE_SHA = $Source.Tag
    $env:DELIVERY_IMAGE_TAG = $tag
    Invoke-Compose --profile tools build migrate
    # Every application service is recreated from the freshly built image; a bare
    # `restart` is what let worker drift three days behind api.
    Invoke-Compose up -d --force-recreate api worker publisher beat nginx
    $facts = Assert-DeliveryImageDigest
    $baked = Assert-DeliveryImageContent $facts
    if ($baked -ne $Source.Tag) { throw "built image reports source $baked, expected $($Source.Tag)" }
    return $facts
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
        $runtimeSelection = Get-AiRuntimeSelection
        & $WorkAiRuntime start @runtimeSelection
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
    "rag-ollama-start" { & $RagOllamaRuntime start; if ($LASTEXITCODE -ne 0) { throw "delivery RAG Ollama start failed ($LASTEXITCODE)" } }
    "rag-ollama-stop" { & $RagOllamaRuntime stop; if ($LASTEXITCODE -ne 0) { throw "delivery RAG Ollama stop failed ($LASTEXITCODE)" } }
    "rag-ollama-status" { & $RagOllamaRuntime status; if ($LASTEXITCODE -ne 0) { throw "delivery RAG Ollama status failed ($LASTEXITCODE)" } }
    "rag-start" {
        $runtimeSelection = Get-AiRuntimeSelection
        & $RagRuntime start @runtimeSelection
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
        Assert-NoRetainedRollbackContainers
        Assert-NoPlaceholders
        Invoke-Compose up -d mysql redis rabbitmq etcd minio milvus
        Invoke-Compose --profile tools build migrate
        Invoke-Compose --profile tools run --rm `
            -e "PROPERTY_AI_MEDIA_CYCLE_MIGRATION=approved" `
            -e "PROPERTY_AI_APPROVE_DELIVERY_TARGET=mysql:3306/property_ai_delivery_check" `
            migrate
        Invoke-Compose --profile tools run --rm storage-init
        Invoke-Compose up -d api worker publisher beat nginx
        Invoke-Compose ps
    }
    "release" {
        Assert-NoRetainedRollbackContainers
        Assert-NoPlaceholders
        $source = Resolve-DeliverySource
        $facts = Invoke-DeliveryRelease $source
        $tag = "src-$($source.Tag)"
        foreach ($row in $facts) {
            Write-Output ("{0}`t{1}`t{2}`tsource={3}" -f $row.Service, $row.Container, $row.Image, $row.SourceSha)
        }
        Write-Output ("delivery image property-ai-delivery:{0} is the single image behind api/worker/beat/publisher (source {1})" -f $tag, $source.Sha)
    }
    "verify-image" {
        $facts = Assert-DeliveryImageDigest
        $baked = Assert-DeliveryImageContent $facts
        foreach ($row in $facts) {
            Write-Output ("{0}`t{1}`t{2}`tsource={3}" -f $row.Service, $row.Container, $row.Image, $row.SourceSha)
        }
        Write-Output "delivery image digest is uniform and matches source $baked"
    }
    "up" {
        Assert-NoRetainedRollbackContainers
        Assert-NoPlaceholders
        Invoke-Compose up -d mysql redis rabbitmq etcd minio milvus
        Invoke-Compose --profile tools run --rm storage-init
        Invoke-Compose up -d api worker publisher beat nginx
        Invoke-Compose ps
    }
    "stop" {
        Assert-NoRetainedRollbackContainers
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
        $approvedMediaCycleDrill = "property_ai_delivery_media_cycle_check"
        if ($RestoreDatabase -and $restore -notin @($approvedRestore, $approvedMediaCycleDrill)) {
            throw "explicit restore database is not the approved delivery target"
        }
        if (
            $restore -notmatch "^[A-Za-z0-9_]+_restore_check(?:_029)?$" -and
            $restore -ne $approvedMediaCycleDrill
        ) { throw "unsafe restore database" }
        $existsCommand = 'mysql -N -uroot -p"$MYSQL_ROOT_PASSWORD" -e "SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name=''{0}''"' -f $restore
        $exists = (& docker compose --env-file $EnvFile -f $ComposeFile exec -T mysql sh -c $existsCommand | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or $exists -notin @("0", "1")) {
            throw "restore target exists or could not be verified; it will not be overwritten"
        }
        if ($exists -eq "1") {
            if ($restore -eq $approvedMediaCycleDrill) {
                throw "media-cycle drill target already exists; it will not be overwritten"
            }
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
    "media-cycle-migrate" {
        Assert-NoPlaceholders
        $database = if ($Argument) { $Argument } else { Read-DeliveryValue "MYSQL_DATABASE" }
        $liveDatabase = "property_ai_delivery_check"
        $drillDatabase = "property_ai_delivery_media_cycle_check"
        if ($database -notin @($liveDatabase, $drillDatabase)) {
            throw "media-cycle migration target is not approved"
        }
        $target = "mysql:3306/$database"
        if ($database -eq $drillDatabase) {
            $existsCommand = 'mysql -N -uroot -p"$MYSQL_ROOT_PASSWORD" -e "SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name=''{0}''"' -f $drillDatabase
            $exists = (& docker compose --env-file $EnvFile -f $ComposeFile exec -T mysql sh -c $existsCommand | Out-String).Trim()
            if ($LASTEXITCODE -ne 0 -or $exists -ne "1") {
                throw "media-cycle drill target does not exist or could not be verified"
            }
            $migrationUser = "delivery_migrate_$([guid]::NewGuid().ToString('N').Substring(0,12))"
            $migrationPassword = [guid]::NewGuid().ToString("N")
            $createMigrationUserCommand = 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -e "CREATE USER ''{0}''@''%'' IDENTIFIED BY ''{1}''; GRANT ALL PRIVILEGES ON {2}.* TO ''{0}''@''%'';"' -f $migrationUser,$migrationPassword,$drillDatabase
            $dropMigrationUserCommand = 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -e "DROP USER IF EXISTS ''{0}''@''%''"' -f $migrationUser
            try {
                Invoke-Compose exec -T mysql sh -c $createMigrationUserCommand
                $drillUrl = "mysql+pymysql://${migrationUser}:${migrationPassword}@mysql:3306/${drillDatabase}?charset=utf8mb4"
                Invoke-Compose --profile tools run --rm `
                    -e "DATABASE_URL=$drillUrl" `
                    -e "PROPERTY_AI_MEDIA_CYCLE_DRILL=approved" `
                    -e "PROPERTY_AI_APPROVE_DELIVERY_TARGET=$target" `
                    migrate
            } finally {
                Invoke-Compose exec -T mysql sh -c $dropMigrationUserCommand
            }
        } else {
            Invoke-Compose --profile tools run --rm `
                -e "PROPERTY_AI_MEDIA_CYCLE_MIGRATION=approved" `
                -e "PROPERTY_AI_APPROVE_DELIVERY_TARGET=$target" `
                migrate
        }
    }
    "diagnose" {
        Invoke-Compose ps
        Invoke-Compose logs --tail 120 api worker publisher beat nginx mysql rabbitmq redis milvus
    }
}
