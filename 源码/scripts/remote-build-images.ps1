param(
    [string]$DockerHost = $(if ($env:REMOTE_DOCKER_HOST) { $env:REMOTE_DOCKER_HOST } elseif ($env:DOCKER_HOST) { $env:DOCKER_HOST } else { "tcp://127.0.0.1:2375" }),
    [string]$Registry = $(if ($env:REGISTRY) { $env:REGISTRY } else { "docker-hosted.example.invalid/nstl" }),
    [string]$Namespace = $(if ($env:IMAGE_NAMESPACE) { $env:IMAGE_NAMESPACE } else { "content-assessment" }),
    [string]$Version = $(if ($env:IMAGE_TAG) { $env:IMAGE_TAG } else { "v1.1.0" }),
    [string]$PythonBaseImage = $(if ($env:PYTHON_BASE_IMAGE) { $env:PYTHON_BASE_IMAGE } else { "docker.1ms.run/library/python:3.11-slim-bookworm" }),
    [string]$NodeBaseImage = $(if ($env:NODE_BASE_IMAGE) { $env:NODE_BASE_IMAGE } else { "docker.1ms.run/library/node:20-alpine" }),
    [string]$NginxBaseImage = $(if ($env:NGINX_BASE_IMAGE) { $env:NGINX_BASE_IMAGE } else { "docker.1ms.run/library/nginx:1.27-alpine" }),
    [string]$NpmRegistry = $(if ($env:NPM_REGISTRY) { $env:NPM_REGISTRY } else { "https://registry.npmmirror.com" }),
    [ValidateSet("server_v4_flash", "personal_v4_flash")]
    [string]$ModelProfile = "server_v4_flash",
    [string]$HarborUrl = $(if ($env:HARBOR_URL) { $env:HARBOR_URL } else { "docker-hosted.example.invalid" }),
    [string]$HarborUser = $env:HARBOR_USER,
    [string]$HarborPass = $env:HARBOR_PASS,
    [string]$NoCache = $(if ($env:NO_CACHE) { $env:NO_CACHE } else { "true" }),
    [switch]$CheckOnly,
    [switch]$SkipPush,
    [switch]$AllowDirtyWorktree,
    [switch]$AllowTagOverwrite
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$backendImage = "$Registry/$Namespace/content-assessment-backend:$Version"
$frontendImage = "$Registry/$Namespace/content-assessment-frontend:$Version"
$frontendApiBase = "/api/service/nstl/ai/agent/content-assessment"
$frontendPublicBase = "/ai/agent/content_assessment/"
$dockerArgs = @("-H", $DockerHost)
$buildCreated = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$buildOptions = @()
if ($NoCache -match '^(1|true|yes|on)$') {
    $buildOptions += "--no-cache"
}

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "============================================" -ForegroundColor Cyan
    Write-Host " $Message" -ForegroundColor Cyan
    Write-Host "============================================" -ForegroundColor Cyan
}

function Invoke-DockerChecked {
    param([Parameter(Mandatory = $true)][string[]]$CommandArgs)

    Write-Host "> docker $($dockerArgs -join ' ') $($CommandArgs -join ' ')" -ForegroundColor DarkGray
    & docker @dockerArgs @CommandArgs
    if ($LASTEXITCODE -ne 0) {
        throw "docker $($CommandArgs -join ' ') failed with exit code $LASTEXITCODE"
    }
}

Write-Step "ContentAssessment remote image build"
Write-Host "Remote Docker host: $DockerHost"
Write-Host "Backend image:      $backendImage"
Write-Host "Frontend image:     $frontendImage"
Write-Host "Python base image:  $PythonBaseImage"
Write-Host "Node base image:    $NodeBaseImage"
Write-Host "Nginx base image:   $NginxBaseImage"
Write-Host "NPM registry:       $NpmRegistry"
Write-Host "Model profile:      $ModelProfile"
Write-Host "No cache:           $(if ($buildOptions.Count -gt 0) { 'true' } else { 'false' })"
Write-Host "Build created:      $buildCreated"

Write-Step "Checking remote Docker Engine"
Invoke-DockerChecked -CommandArgs @("version")

if ($CheckOnly) {
    Write-Step "All images on remote Docker host"
    Invoke-DockerChecked -CommandArgs @("image", "ls", "--all")
    Write-Host ""
    Write-Host "Remote check finished. No image was removed, built, or pushed." -ForegroundColor Green
    exit 0
}

$gitCommit = (& git rev-parse --verify HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or -not $gitCommit) {
    throw "Unable to resolve the Git commit for this build."
}
$gitChanges = @(& git status --porcelain)
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect the Git worktree before the build."
}
if ($gitChanges.Count -gt 0 -and -not $AllowDirtyWorktree) {
    throw "The Git worktree has uncommitted changes. Commit/review them first, or explicitly pass -AllowDirtyWorktree."
}
Write-Host "Git revision:       $gitCommit"
Write-Host "Dirty worktree:     $(if ($gitChanges.Count -gt 0) { 'true' } else { 'false' })"

if (-not $SkipPush -and $HarborUser -and $HarborPass) {
    Write-Step "Logging in to registry"
    Write-Host "> docker $($dockerArgs -join ' ') login $HarborUrl -u $HarborUser --password-stdin" -ForegroundColor DarkGray
    $HarborPass | & docker @dockerArgs login $HarborUrl -u $HarborUser --password-stdin

    if ($LASTEXITCODE -ne 0) {
        throw "docker login failed with exit code $LASTEXITCODE"
    }
}
elseif (-not $SkipPush) {
    Write-Host ""
    Write-Host "Skipping registry login. If push is unauthorized, set HARBOR_USER and HARBOR_PASS first." -ForegroundColor Yellow
}

Write-Step "Checking immutable target image tags"
foreach ($image in @($backendImage, $frontendImage)) {
    $existing = & docker @dockerArgs image ls --quiet $image
    if ($LASTEXITCODE -ne 0) {
        throw "docker image ls failed for $image"
    }

    if ($existing) {
        if (-not $AllowTagOverwrite) {
            throw "Target image already exists on the remote Docker host: $image. Use a new version tag, or explicitly pass -AllowTagOverwrite."
        }
        Invoke-DockerChecked -CommandArgs @("rmi", "-f", $image)
    }
    else {
        Write-Host "No existing image: $image" -ForegroundColor DarkGray
    }
}

Write-Step "Building backend image on remote Docker host"
$backendBuildArgs = @(
    "build"
) + $buildOptions + @(
    "-f", "docker/Dockerfile.backend",
    "--build-arg", "PYTHON_BASE_IMAGE=$PythonBaseImage",
    "--build-arg", "CONTENT_ASSESSMENT_MODEL_PROFILE=$ModelProfile",
    "--build-arg", "BUILD_CREATED=$buildCreated",
    "--build-arg", "VCS_REF=$gitCommit",
    "-t", $backendImage,
    "."
)
Invoke-DockerChecked -CommandArgs $backendBuildArgs

Write-Step "Building frontend image on remote Docker host"
$frontendBuildArgs = @(
    "build"
) + $buildOptions + @(
    "-f", "docker/Dockerfile.frontend",
    "--build-arg", "NODE_BASE_IMAGE=$NodeBaseImage",
    "--build-arg", "NGINX_BASE_IMAGE=$NginxBaseImage",
    "--build-arg", "NPM_REGISTRY=$NpmRegistry",
    "--build-arg", "VITE_API_BASE_URL=$frontendApiBase",
    "--build-arg", "VITE_PUBLIC_BASE=$frontendPublicBase",
    "--build-arg", "BUILD_CREATED=$buildCreated",
    "--build-arg", "VCS_REF=$gitCommit",
    "-t", $frontendImage,
    "."
)
Invoke-DockerChecked -CommandArgs $frontendBuildArgs

Write-Step "Verifying remote image tags"
Invoke-DockerChecked -CommandArgs @("image", "inspect", "--format", "{{.Id}}", $backendImage)
Invoke-DockerChecked -CommandArgs @("image", "inspect", "--format", "{{.Id}}", $frontendImage)

if (-not $SkipPush) {
    Write-Step "Pushing images from remote Docker host"
    Invoke-DockerChecked -CommandArgs @("push", $backendImage)
    Invoke-DockerChecked -CommandArgs @("push", $frontendImage)
}
else {
    Write-Host ""
    Write-Host "SkipPush is enabled; images were built but not pushed." -ForegroundColor Yellow
}

Write-Step "All images on remote Docker host"
Invoke-DockerChecked -CommandArgs @("image", "ls", "--all")

Write-Host ""
if ($SkipPush) {
    Write-Host "Remote build finished successfully. Push was skipped." -ForegroundColor Green
}
else {
    Write-Host "Remote build and push finished successfully." -ForegroundColor Green
}
Write-Host "Backend:  $backendImage"
Write-Host "Frontend: $frontendImage"
Write-Host ""
if ($SkipPush) {
    Write-Host "Manual push commands on the remote computer:" -ForegroundColor Green
    Write-Host "  docker push $backendImage"
    Write-Host "  docker push $frontendImage"
    Write-Host ""
}
Write-Host "Deploy with these test-server compose files:" -ForegroundColor Green
Write-Host "  docker/docker-compose.test.yml (Portainer Stack source)"
Write-Host "Published ports: frontend 18100 -> 38003, backend 17613 -> 38002"
