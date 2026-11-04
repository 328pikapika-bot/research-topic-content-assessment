# ContentAssessment Agent restart script
# Usage: .\restart-dev.ps1 [-ModelProfile server_v4_flash|personal_v4_flash]

param(
    [ValidateSet("server_v4_flash", "personal_v4_flash")]
    [string]$ModelProfile = "server_v4_flash"
)

$ErrorActionPreference = "Continue"
$projectRoot = $PSScriptRoot
$runtimeDir = Join-Path $projectRoot ".runtime"
$modelProfileFile = Join-Path $runtimeDir "model_profile.txt"

function Import-DotEnv {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path $Path)) {
        throw "Project .env file not found: $Path"
    }

    Get-Content -Path $Path | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) {
            return
        }
        $parts = $line.Split("=", 2)
        $key = $parts[0].Trim()
        $value = $parts[1].Trim()
        [Environment]::SetEnvironmentVariable($key, $value, "Process")
    }
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

Import-DotEnv (Join-Path $projectRoot ".env")
if ($env:LITERATURE_FULLTEXT_API_URL -match '^http://(127\.0\.0\.1|localhost):17609/v1/fulltext/search/?$') {
    try {
        $fulltextStart = Join-Path $projectRoot 'scripts\start-fulltext-local.ps1'
        if ($env:NSTL_FULLTEXT_SERVICE_ROOT) {
            & $fulltextStart -ServiceRoot $env:NSTL_FULLTEXT_SERVICE_ROOT
        } else {
            & $fulltextStart
        }
    } catch {
        Write-Warning "本地全文查询服务未能启动，平台仍可使用文献检索：$($_.Exception.Message)"
    }
}
Set-ModelProfileSelector -Profile $ModelProfile -SelectorFile $modelProfileFile

Write-Host "=== ContentAssessment Dev Restart ===" -ForegroundColor Cyan
Write-Host "Model profile: $ModelProfile" -ForegroundColor Cyan

# Step 1: kill old processes
Write-Host "[1] Killing old processes..." -ForegroundColor Yellow
$backendPort = if ($env:CONTENT_ASSESSMENT_LOCAL_BACKEND_PORT) { [int]$env:CONTENT_ASSESSMENT_LOCAL_BACKEND_PORT } else { 17641 }
$localAppUrl = "https://www.nstl.gov.cn/ai/agent/content_assessment/"
$backendConn = Get-NetTCPConnection -LocalPort $backendPort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($backendConn) {
    Stop-Process -Id $backendConn.OwningProcess -Force -ErrorAction SilentlyContinue
    Write-Host "  Killed local backend PID $($backendConn.OwningProcess)" -ForegroundColor Green
}
$frontendPort = if ($env:CONTENT_ASSESSMENT_LOCAL_FRONTEND_PORT) { [int]$env:CONTENT_ASSESSMENT_LOCAL_FRONTEND_PORT } else { 18093 }
$pFrontend = (Get-NetTCPConnection -LocalPort $frontendPort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1).OwningProcess
if ($pFrontend) { Stop-Process -Id $pFrontend -Force -ErrorAction SilentlyContinue; Write-Host "  Killed local frontend PID $pFrontend" -ForegroundColor Green }
$gatewayProcesses = Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -like '*nstl-www-https-proxy.mjs*' }
foreach ($gatewayProcess in $gatewayProcesses) {
    Stop-Process -Id $gatewayProcess.ProcessId -Force -ErrorAction SilentlyContinue
    Write-Host "  Killed local NSTL gateway PID $($gatewayProcess.ProcessId)" -ForegroundColor Green
}
Start-Sleep -Milliseconds 500

# Step 2: check NSTL setup
Write-Host "[2] Checking NSTL setup..." -ForegroundColor Yellow
$hostsOk = (Get-Content "$env:SystemRoot\System32\drivers\etc\hosts" -ErrorAction SilentlyContinue | Select-String "www.nstl.gov.cn")
if (-not $hostsOk) {
    Write-Host "  WARNING: hosts not configured. Run as admin first: npm run setup:nstl-local" -ForegroundColor Yellow
} else {
    Write-Host "  hosts OK" -ForegroundColor Green
}

# Step 3: align local runtime with MongoDB compatibility
Write-Host "[3] Preparing MongoDB compatibility..." -ForegroundColor Yellow
$mongoCompatDir = Join-Path $projectRoot ".runtime\python-mongo-compat"
if (-not (Test-Path (Join-Path $mongoCompatDir "pymongo"))) {
    python -m pip install --target $mongoCompatDir "pymongo>=3.13,<4.0"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  Failed to install PyMongo 3.x compatibility package." -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "  PyMongo compatibility package is ready." -ForegroundColor Green
}

# Step 4: start backend
Write-Host "[4] Starting local backend (port $backendPort)..." -ForegroundColor Yellow
$bDir = Join-Path $projectRoot "backend"
$env:PYTHONPATH = "$mongoCompatDir;$bDir"
$env:BACKEND_PORT = [string]$backendPort
$env:USE_MEMORY_STORE = "false"
Set-DefaultEnv "CONTENT_ASSESSMENT_TASK_COLLECTION" "content_assessment_tasks"
$env:ALLOW_DEFAULT_USER_SCOPE = "true"

$mongoEnvRaw = if ($env:MONGO_ENV) { $env:MONGO_ENV } else { "" }
$mongoEnv = $mongoEnvRaw.Trim().ToLowerInvariant()
if (-not $mongoEnv -or -not $env:MONGO_DATABASE) {
    Write-Host "[ERROR] MONGO_ENV and MONGO_DATABASE must be configured in .env." -ForegroundColor Red
    exit 1
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
if ($mongoEnv -in @("local", "本地")) {
    Write-Host "  Starting local MongoDB container..." -ForegroundColor Yellow
    & (Join-Path $projectRoot "scripts\start-mongo.ps1")
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  Local MongoDB startup failed." -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

Start-Process python -ArgumentList "-m","uvicorn","app.main:app","--host","0.0.0.0","--port",[string]$backendPort,"--reload" -WorkingDirectory $bDir -WindowStyle Minimized
Write-Host "  Backend starting..." -ForegroundColor Green
Write-Host "  Waiting for backend..." -ForegroundColor Gray
$ok = $false
for ($i = 0; $i -lt 15; $i++) {
    Start-Sleep -Seconds 1
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:$backendPort/system/health" -TimeoutSec 2 -ErrorAction SilentlyContinue
    if ($r -and $r.StatusCode -eq 200) { Write-Host "  Backend ready!" -ForegroundColor Green; $ok = $true; break }
}
if (-not $ok) { Write-Host "  Backend timeout, check manually" -ForegroundColor Yellow }

# Step 5: start frontend
Write-Host "[5] Starting local frontend (NSTL mode, port $frontendPort, proxy to $backendPort)..." -ForegroundColor Yellow
$fDir = Join-Path $projectRoot "frontend\web"
$env:VITE_PROXY_BACKEND = "http://127.0.0.1:$backendPort"
Start-Process npm.cmd -ArgumentList "run","dev:nstl" -WorkingDirectory $fDir -WindowStyle Minimized
Write-Host "  Frontend starting..." -ForegroundColor Green
Start-Sleep -Seconds 6

# Step 6: start local NSTL HTTPS gateway
Write-Host "[6] Starting local NSTL HTTPS gateway (port 443 -> frontend $frontendPort)..." -ForegroundColor Yellow
$env:NSTL_PROXY_LOCAL_FRONTEND_PORT = [string]$frontendPort
Start-Process node -ArgumentList "scripts\nstl-www-https-proxy.mjs" -WorkingDirectory $fDir -WindowStyle Minimized
Write-Host "  Local gateway starting..." -ForegroundColor Green
Start-Sleep -Seconds 2

Write-Host ""
Write-Host "=== Done! ===" -ForegroundColor Cyan
Write-Host "  Content assessment entry: $localAppUrl" -ForegroundColor White
Write-Host "  Vite internal frontend:   http://127.0.0.1:$frontendPort (internal only)" -ForegroundColor DarkGray
Write-Host "  HTTPS gateway:             https://www.nstl.gov.cn (public site root)" -ForegroundColor DarkGray
Write-Host "  Local backend:  http://127.0.0.1:$backendPort" -ForegroundColor White
Write-Host "  Model profile:  $ModelProfile" -ForegroundColor White
Write-Host "  Database:       $($env:MONGO_ENV) MongoDB ${selectedMongoHost}:${selectedMongoPort} / $($env:MONGO_DATABASE).content_assessment_tasks" -ForegroundColor White
Write-Host "  Docker containers are left running if present." -ForegroundColor White
Write-Host ""

Read-Host "Press Enter to open browser"
Start-Process $localAppUrl
