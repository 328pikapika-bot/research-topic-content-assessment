param(
    [string]$BackendImage = $(if ($env:CONTENT_ASSESSMENT_BACKEND_IMAGE) { $env:CONTENT_ASSESSMENT_BACKEND_IMAGE } else { "docker-hosted.example.invalid/nstl/content-assessment/content-assessment-backend:v1.1.0" }),
    [string]$FrontendImage = $(if ($env:CONTENT_ASSESSMENT_FRONTEND_IMAGE) { $env:CONTENT_ASSESSMENT_FRONTEND_IMAGE } else { "docker-hosted.example.invalid/nstl/content-assessment/content-assessment-frontend:v1.1.0" }),
    [string]$PythonBaseImage = $(if ($env:PYTHON_BASE_IMAGE) { $env:PYTHON_BASE_IMAGE } else { "docker.1ms.run/library/python:3.12-slim" }),
    [string]$NodeBaseImage = $(if ($env:NODE_BASE_IMAGE) { $env:NODE_BASE_IMAGE } else { "docker.1ms.run/library/node:20-alpine" }),
    [string]$NginxBaseImage = $(if ($env:NGINX_BASE_IMAGE) { $env:NGINX_BASE_IMAGE } else { "nginx:1.27-alpine" }),
    [string]$NpmRegistry = $(if ($env:NPM_REGISTRY) { $env:NPM_REGISTRY } else { "https://registry.npmmirror.com" }),
    [ValidateSet("server_v4_flash", "personal_v4_flash")]
    [string]$ModelProfile = "server_v4_flash",
    [switch]$UsePrebuiltFrontend = $true,
    [switch]$FrontendOnly
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$frontendApiBase = "/api/service/nstl/ai/agent/content-assessment"
$frontendPublicBase = "/ai/agent/content_assessment/"

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

Write-Host "============================================" -ForegroundColor Cyan
Write-Host " ContentAssessment local image rebuild" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "Backend image:  $BackendImage"
Write-Host "Frontend image: $FrontendImage"
Write-Host "Model profile:  $ModelProfile"
Write-Host "Mode:           $(if ($FrontendOnly) { 'frontend only' } else { 'backend + frontend' })"
Write-Host ""

if ($FrontendOnly) {
    Write-Host "[1/4] Stopping existing local frontend container only..." -ForegroundColor Yellow
    $frontendContainer = docker ps -a --filter "name=datahub-agentkit-content-assessment-frontend" --format "{{.ID}}"
    if ($LASTEXITCODE -ne 0) {
        throw "docker ps failed while checking frontend container"
    }
    if ($frontendContainer) {
        Invoke-Checked "docker" @("rm", "-f", "datahub-agentkit-content-assessment-frontend")
        Write-Host "  Removed frontend container: datahub-agentkit-content-assessment-frontend" -ForegroundColor Green
    }
    else {
        Write-Host "  Frontend container is not running or does not exist." -ForegroundColor Gray
    }
}
else {
    Write-Host "[1/4] Stopping existing local ContentAssessment containers..." -ForegroundColor Yellow
    Invoke-Checked "docker" @("compose", "-f", "docker/docker-compose.yml", "-f", "docker/docker-compose.local-nstl-proxy.yml", "down", "--remove-orphans")
}

Write-Host "[2/4] Removing old images..." -ForegroundColor Yellow
$imagesToRemove = if ($FrontendOnly) { @($FrontendImage) } else { @($BackendImage, $FrontendImage) }
foreach ($image in $imagesToRemove) {
    $existing = docker image ls --quiet $image
    if ($LASTEXITCODE -ne 0) {
        throw "docker image ls failed for $image"
    }
    if ($existing) {
        Invoke-Checked "docker" @("rmi", "-f", $image)
        Write-Host "  Removed $image" -ForegroundColor Green
    }
    else {
        Write-Host "  Skip missing image: $image" -ForegroundColor Gray
    }
}

if ($FrontendOnly) {
    Write-Host "[3/4] Skipping backend image because -FrontendOnly is set." -ForegroundColor Yellow
}
else {
    Write-Host "[3/4] Building backend image..." -ForegroundColor Yellow
    Invoke-Checked "docker" @(
        "build",
        "-f", "docker/Dockerfile.backend",
        "--build-arg", "PYTHON_BASE_IMAGE=$PythonBaseImage",
        "--build-arg", "CONTENT_ASSESSMENT_MODEL_PROFILE=$ModelProfile",
        "-t", $BackendImage,
        "."
    )
}

Write-Host "[4/4] Building frontend image..." -ForegroundColor Yellow
if ($UsePrebuiltFrontend) {
    Push-Location frontend/web
    try {
        $previousApiBase = $env:VITE_API_BASE_URL
        $previousPublicBase = $env:VITE_PUBLIC_BASE
        $env:VITE_API_BASE_URL = $frontendApiBase
        $env:VITE_PUBLIC_BASE = $frontendPublicBase
        Invoke-Checked "npm" @("run", "build")
    }
    finally {
        if ($null -eq $previousApiBase) {
            Remove-Item Env:\VITE_API_BASE_URL -ErrorAction SilentlyContinue
        }
        else {
            $env:VITE_API_BASE_URL = $previousApiBase
        }
        if ($null -eq $previousPublicBase) {
            Remove-Item Env:\VITE_PUBLIC_BASE -ErrorAction SilentlyContinue
        }
        else {
            $env:VITE_PUBLIC_BASE = $previousPublicBase
        }
        Pop-Location
    }

    Invoke-Checked "docker" @(
        "build",
        "-f", "docker/Dockerfile.frontend.dist",
        "--build-arg", "NGINX_BASE_IMAGE=$NginxBaseImage",
        "-t", $FrontendImage,
        "."
    )
}
else {
    Invoke-Checked "docker" @(
        "build",
        "-f", "docker/Dockerfile.frontend",
        "--build-arg", "NODE_BASE_IMAGE=$NodeBaseImage",
        "--build-arg", "NGINX_BASE_IMAGE=$NginxBaseImage",
        "--build-arg", "NPM_REGISTRY=$NpmRegistry",
        "--build-arg", "VITE_API_BASE_URL=$frontendApiBase",
        "--build-arg", "VITE_PUBLIC_BASE=$frontendPublicBase",
        "-t", $FrontendImage,
        "."
    )
}

Write-Host ""
Write-Host "Build complete." -ForegroundColor Green
Write-Host "Backend:  $BackendImage"
Write-Host "Frontend: $FrontendImage"

Write-Host ""
Write-Host "Local ContentAssessment images:" -ForegroundColor Cyan
docker image ls --all | Select-String -Pattern "content-assessment|REPOSITORY"
