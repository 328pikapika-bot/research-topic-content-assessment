# ============================================================
# ContentAssessment Agent backend startup script
# ============================================================
# Prerequisite: MongoDB is running.
# Usage: .\scripts\start-backend.ps1 [-ModelProfile server_v4_flash|personal_v4_flash]
# ============================================================

param(
    [ValidateSet("server_v4_flash", "personal_v4_flash")]
    [string]$ModelProfile = "server_v4_flash"
)

$projectRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $projectRoot "backend"
$runtimeDir = Join-Path $projectRoot ".runtime"
$modelProfileFile = Join-Path $runtimeDir "model_profile.txt"

function Set-ModelProfileSelector {
    param(
        [Parameter(Mandatory = $true)][string]$Profile,
        [Parameter(Mandatory = $true)][string]$SelectorFile
    )

    $selectorDir = Split-Path -Parent $SelectorFile
    if (-not (Test-Path $selectorDir)) {
        New-Item -ItemType Directory -Path $selectorDir -Force | Out-Null
    }
    Set-Content -Path $SelectorFile -Value $Profile -Encoding utf8
}

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "ContentAssessment Agent - Backend Startup" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Set-ModelProfileSelector -Profile $ModelProfile -SelectorFile $modelProfileFile
Write-Host "[Model] Active model profile: $ModelProfile" -ForegroundColor Green

$envFile = Join-Path $projectRoot ".env"
if (-not (Test-Path $envFile)) {
    Write-Host "[ERROR] .env file not found. Create or restore the project-root .env first." -ForegroundColor Red
    exit 1
}
Write-Host "[OK] Found .env" -ForegroundColor Green

# Load .env into this PowerShell process so the selected port and config are
# visible before uvicorn starts.
Get-Content -Path $envFile | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) {
        return
    }
    $parts = $line.Split("=", 2)
    [Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim(), "Process")
}

function Set-DefaultEnv {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Value
    )

    if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($Name, "Process"))) {
        [Environment]::SetEnvironmentVariable($Name, $Value, "Process")
    }
}

Write-Host "[Install] Checking Python dependencies..." -ForegroundColor Yellow
pip install -r "$backendDir\requirements.txt" -q 2>$null

$mongoCompatDir = Join-Path $projectRoot ".runtime\python-mongo-compat"
if (-not (Test-Path (Join-Path $mongoCompatDir "pymongo"))) {
    python -m pip install --target $mongoCompatDir "pymongo>=3.13,<4.0"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Failed to install PyMongo 3.x compatibility package." -ForegroundColor Red
        exit 1
    }
}
Write-Host "[OK] Dependencies ready" -ForegroundColor Green

Write-Host "[Start] Starting FastAPI service..." -ForegroundColor Yellow

$port = if ($env:CONTENT_ASSESSMENT_LOCAL_BACKEND_PORT) {
    [int]$env:CONTENT_ASSESSMENT_LOCAL_BACKEND_PORT
} elseif ($env:BACKEND_PORT) {
    [int]$env:BACKEND_PORT
} else {
    17607
}

if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "[ERROR] Backend port $port is occupied. Set CONTENT_ASSESSMENT_LOCAL_BACKEND_PORT and retry." -ForegroundColor Red
    exit 1
}
$env:BACKEND_PORT = [string]$port
$env:PYTHONPATH = "$mongoCompatDir;$backendDir"
$env:USE_MEMORY_STORE = "false"
Set-DefaultEnv "CONTENT_ASSESSMENT_TASK_COLLECTION" "content_assessment_tasks"
$env:ALLOW_DEFAULT_USER_SCOPE = "true"

$mongoEnvRaw = if ($env:MONGO_ENV) { $env:MONGO_ENV } else { "" }
$mongoEnv = $mongoEnvRaw.Trim().ToLowerInvariant()
if (-not $mongoEnv -or -not $env:MONGO_DATABASE) {
    Write-Host "[ERROR] MONGO_ENV and MONGO_DATABASE must be configured in .env." -ForegroundColor Red
    exit 1
}
if ($mongoEnv -in @("local", "本地")) {
    $startLocalMongo = $true
} else {
    $startLocalMongo = $false
}

$selectedMongoHost = $env:MONGO_HOST
$selectedMongoPort = $env:MONGO_PORT
if ($mongoEnv -in @("local", "本地")) {
    $selectedMongoHost = $env:MONGO_LOCAL_HOST
    $selectedMongoPort = $env:MONGO_LOCAL_PORT
}
elseif ($mongoEnv -in @("test", "testing", "测试")) {
    $selectedMongoHost = $env:MONGO_TEST_HOST
    $selectedMongoPort = $env:MONGO_TEST_PORT
}
if (-not $selectedMongoHost -or -not $selectedMongoPort) {
    Write-Host "[ERROR] Mongo host and port must be configured in .env for MONGO_ENV '$mongoEnvRaw'." -ForegroundColor Red
    exit 1
}
if ($startLocalMongo) {
    Write-Host "[Mongo] Starting local MongoDB container..." -ForegroundColor Yellow
    & (Join-Path $PSScriptRoot "start-mongo.ps1")
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

Write-Host "  API docs:     http://localhost:$port/docs" -ForegroundColor White
Write-Host "  Health:       http://localhost:$port/system/health" -ForegroundColor White
Write-Host "  Mongo health: http://localhost:$port/system/health/mongo" -ForegroundColor White
Write-Host "  Mongo mode:   $($env:MONGO_ENV)" -ForegroundColor White
Write-Host "  Mongo:        ${selectedMongoHost}:${selectedMongoPort} / $($env:MONGO_DATABASE)" -ForegroundColor White
Write-Host ""

Set-Location $backendDir
python -m uvicorn app.main:app --host 0.0.0.0 --port $port --reload
