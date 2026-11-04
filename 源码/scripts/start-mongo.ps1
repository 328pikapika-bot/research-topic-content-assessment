$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $projectRoot ".env.local"
if (-not (Test-Path $envFile)) {
    $envFile = Join-Path $projectRoot ".env"
}
$composeFile = Join-Path $projectRoot "docker\compose.local.yml"

if (-not (Test-Path $envFile)) {
    throw "Missing local environment file. Copy .env.template to .env.local first."
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker is unavailable. Start Docker Desktop or Docker Engine first."
}

Push-Location $projectRoot
try {
    & docker compose --env-file $envFile -f docker/compose.local.yml up -d mongo
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose failed to start MongoDB (exit code $LASTEXITCODE)."
    }
}
finally {
    Pop-Location
}
