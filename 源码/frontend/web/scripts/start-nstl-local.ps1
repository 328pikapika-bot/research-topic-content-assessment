param()

$ErrorActionPreference = "Stop"

$webRoot = Split-Path -Parent $PSScriptRoot
$projectRoot = Split-Path -Parent (Split-Path -Parent $webRoot)
$backendPort = if ($env:CONTENT_ASSESSMENT_LOCAL_BACKEND_PORT) { [int]$env:CONTENT_ASSESSMENT_LOCAL_BACKEND_PORT } else { 17641 }
$env:CONTENT_ASSESSMENT_LOCAL_BACKEND_PORT = [string]$backendPort
$env:VITE_PROXY_BACKEND = "http://127.0.0.1:$backendPort"

function Test-ListeningPort {
  param([int]$Port)

  return [bool](Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
}

function Get-ListeningProcess {
  param([int]$Port)

  $connection = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
    Select-Object -First 1
  if (-not $connection) {
    return $null
  }

  return Get-CimInstance Win32_Process -Filter "ProcessId = $($connection.OwningProcess)"
}

function Test-ProcessCommand {
  param(
    $Process,
    [string]$Pattern
  )

  return $Process -and $Process.CommandLine -match $Pattern
}

function Get-InputRuleVersion {
  param([string]$ApiBase)

  # Empty input checks the actual proxy destination without invoking a model,
  # uploading files or creating an assessment task.
  $preview = Invoke-RestMethod -Method Post `
    -Uri "$ApiBase/v1/content-assessment/preview-nine-fields" `
    -Headers @{ "X-User-Scope" = "platform_admin" } `
    -ContentType "application/json" `
    -Body '{"user_input":"","uploaded_files":[],"artifact_type":"auto"}' `
    -TimeoutSec 5
  return $preview.validation_version
}

function Start-NpmScript {
  param([string]$ScriptName)

  Start-Process `
    -FilePath "npm.cmd" `
    -ArgumentList @("run", $ScriptName) `
    -WorkingDirectory $webRoot `
    -WindowStyle Hidden | Out-Null
}

function Start-LocalBackend {
  $backendScript = Join-Path $projectRoot "scripts\start-backend.ps1"
  if (-not (Test-Path $backendScript)) {
    throw "Local backend startup script is missing: $backendScript"
  }

  Start-Process `
    -FilePath "powershell.exe" `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $backendScript) `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden | Out-Null
}

if (-not (Test-ListeningPort -Port $backendPort)) {
  Start-LocalBackend
  $backendReady = $false
  for ($attempt = 0; $attempt -lt 20; $attempt += 1) {
    Start-Sleep -Seconds 1
    try {
      $health = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$backendPort/system/health" -TimeoutSec 2
      if ($health.StatusCode -eq 200) {
        $backendReady = $true
        break
      }
    } catch {}
  }
  if (-not $backendReady) {
    throw "Local backend did not become ready on port $backendPort. Check scripts/start-backend.ps1 output."
  }
}

$ruleVersion = Get-InputRuleVersion -ApiBase "http://127.0.0.1:$backendPort"
if ($ruleVersion -ne 'assessment-input-semantic-v2') {
  throw "Backend on port $backendPort is using obsolete assessment input rules. Restart that backend before opening the local entry."
}

$viteProcess = Get-ListeningProcess -Port 18093
if ($viteProcess -and -not (Test-ProcessCommand -Process $viteProcess -Pattern 'vite.*nstl-local')) {
  Stop-Process -Id $viteProcess.ProcessId -Force
  Start-Sleep -Milliseconds 500
  $viteProcess = $null
}

if ($viteProcess) {
  $proxyVersion = $null
  try {
    $proxyVersion = Get-InputRuleVersion -ApiBase 'http://127.0.0.1:18093/api/service/nstl/ai/agent/content-assessment'
  } catch {}
  if ($proxyVersion -ne $ruleVersion) {
    Write-Host "Refreshing local frontend proxy to backend port $backendPort."
    Stop-Process -Id $viteProcess.ProcessId -Force
    $viteProcess = $null
  }
}

if (-not $viteProcess) {
  Start-NpmScript -ScriptName "dev:nstl"
  Start-Sleep -Seconds 2
}

$proxyProcess = Get-ListeningProcess -Port 443
if ($proxyProcess -and -not (Test-ProcessCommand -Process $proxyProcess -Pattern 'nstl-www-https-proxy\.mjs')) {
  throw "Port 443 is occupied by '$($proxyProcess.Name)' (PID $($proxyProcess.ProcessId)), not the local NSTL HTTPS gateway."
}

if (-not $proxyProcess) {
  Start-NpmScript -ScriptName "proxy:nstl-www"
  Start-Sleep -Seconds 1
}

if (-not (Test-ListeningPort -Port 18093)) {
  throw "Vite did not start on 127.0.0.1:18093. Check the frontend terminal output."
}

if (-not (Test-ListeningPort -Port 443)) {
  throw "The HTTPS gateway did not start on port 443. Check that the local certificate is trusted and port 443 is available."
}

Write-Host "Local NSTL entry is ready:"
Write-Host "  https://www.nstl.gov.cn/ai/agent/content_assessment/"
Write-Host "  Local backend: http://127.0.0.1:$backendPort"
