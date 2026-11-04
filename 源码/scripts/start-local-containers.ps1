param(
    [switch]$SkipNSTLProxy,
    [int]$ProxyPort = $(if ($env:NSTL_PROXY_LISTEN_PORT) { [int]$env:NSTL_PROXY_LISTEN_PORT } else { 37898 })
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$CommandPath,
        [string[]]$CommandArgs = @()
    )

    & $CommandPath @CommandArgs
    if ($LASTEXITCODE -ne 0) {
        throw "$CommandPath failed with exit code $LASTEXITCODE"
    }
}

function Get-DotEnvValue {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Default
    )

    $processValue = [Environment]::GetEnvironmentVariable($Name, "Process")
    if (-not [string]::IsNullOrWhiteSpace($processValue)) {
        return $processValue
    }

    $envFile = Join-Path $root ".env"
    if (Test-Path $envFile) {
        foreach ($line in Get-Content -Path $envFile) {
            $trimmed = $line.Trim()
            if (-not $trimmed -or $trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) {
                continue
            }
            $parts = $trimmed.Split("=", 2)
            if ($parts[0].Trim() -eq $Name) {
                $value = $parts[1].Trim()
                if (-not [string]::IsNullOrWhiteSpace($value)) {
                    return $value
                }
            }
        }
    }

    return $Default
}

Write-Host "============================================" -ForegroundColor Cyan
Write-Host " ContentAssessment local Docker startup" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan

if (-not (Test-Path ".env")) {
    Write-Host "[ERROR] Missing .env. Copy .env.example to .env and fill required values first." -ForegroundColor Red
    exit 1
}

if (-not $SkipNSTLProxy) {
    $proxyConn = Get-NetTCPConnection -LocalPort $ProxyPort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($proxyConn) {
        Write-Host "[1/4] NSTL proxy already listening on port $ProxyPort." -ForegroundColor Green
    }
    else {
        Write-Host "[1/4] Starting NSTL retrieval proxy on port $ProxyPort..." -ForegroundColor Yellow
        Start-Process powershell `
            -ArgumentList "-NoExit","-ExecutionPolicy","Bypass","-File",(Join-Path $PSScriptRoot "start-nstl-retrieval-proxy.ps1") `
            -WorkingDirectory $root `
            -WindowStyle Normal
        Start-Sleep -Seconds 3
    }
}
else {
    Write-Host "[1/4] Skipping NSTL proxy startup." -ForegroundColor Yellow
}

$mongoEnv = (Get-DotEnvValue "MONGO_ENV" "test").Trim().ToLowerInvariant()
$useLocalMongo = $mongoEnv -in @("local", "本地")
$composeFiles = @("-f", "docker/docker-compose.yml")
if ($useLocalMongo) {
    $composeFiles += @("-f", "docker/docker-compose.local-mongo.yml")
}
$composeFiles += @("-f", "docker/docker-compose.local-nstl-proxy.yml")

Write-Host "[2/4] Starting Docker compose stack..." -ForegroundColor Yellow
if ($useLocalMongo) {
    Write-Host "  Mongo mode: local container" -ForegroundColor White
}
else {
    Write-Host "  Mongo mode: test server" -ForegroundColor White
}
Invoke-Checked "docker" (@("compose") + $composeFiles + @("up", "-d"))

Write-Host "[3/4] Waiting for backend health..." -ForegroundColor Yellow
$backendReady = $false
for ($i = 0; $i -lt 45; $i++) {
    Start-Sleep -Seconds 2
    $status = & curl.exe -s -o NUL -w "%{http_code}" --max-time 2 "http://127.0.0.1:17607/system/health"
    if ($status -eq "200") {
        $backendReady = $true
        break
    }
    Write-Host "." -NoNewline
}
Write-Host ""

if ($backendReady) {
    Write-Host "  Backend is healthy." -ForegroundColor Green
}
else {
    Write-Host "  Backend health check timed out. Check logs with: docker compose $($composeFiles -join ' ') logs -f backend" -ForegroundColor Yellow
}

Write-Host "[4/4] Current containers:" -ForegroundColor Yellow
Invoke-Checked "docker" (@("compose") + $composeFiles + @("ps"))

Write-Host ""
Write-Host "Local Docker deployment is running." -ForegroundColor Green
Write-Host "Frontend: https://www.nstl.gov.cn/ai/agent/content_assessment/"
Write-Host "Backend (internal): http://127.0.0.1:17607"
Write-Host "Docs (internal):    http://127.0.0.1:17607/docs"
Write-Host ""
Write-Host "NSTL mixed retrieval path for local Docker:"
Write-Host "Container -> http://host.docker.internal:$ProxyPort/get_milvus_by_llama_index -> host proxy -> NSTL gateway"
