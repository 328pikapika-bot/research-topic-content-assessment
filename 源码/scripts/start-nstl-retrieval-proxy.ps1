param(
    [string]$ListenHost = $(if ($env:NSTL_PROXY_LISTEN_HOST) { $env:NSTL_PROXY_LISTEN_HOST } else { "0.0.0.0" }),
    [int]$ListenPort = $(if ($env:NSTL_PROXY_LISTEN_PORT) { [int]$env:NSTL_PROXY_LISTEN_PORT } else { 37898 }),
    [string]$TargetBase = $(if ($env:NSTL_RETRIEVAL_TARGET_BASE) { $env:NSTL_RETRIEVAL_TARGET_BASE } else { "http://127.0.0.1:37899" }),
    [int]$Timeout = $(if ($env:NSTL_PROXY_TIMEOUT) { [int]$env:NSTL_PROXY_TIMEOUT } else { 180 })
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$script = Join-Path $PSScriptRoot "nstl-retrieval-proxy.py"

Write-Host "Starting NSTL retrieval proxy" -ForegroundColor Cyan
Write-Host "  Listen: http://${ListenHost}:$ListenPort" -ForegroundColor White
Write-Host "  Target: $TargetBase" -ForegroundColor White
Write-Host "  Docker URL: http://host.docker.internal:$ListenPort/get_milvus_by_llama_index" -ForegroundColor White
Write-Host ""
Write-Host "Keep this window open while testing local Docker containers." -ForegroundColor Yellow

Set-Location $root
python $script --listen-host $ListenHost --listen-port $ListenPort --target-base $TargetBase --timeout $Timeout
