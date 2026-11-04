param(
    [string]$Registry = $(if ($env:REGISTRY) { $env:REGISTRY } else { "docker-hosted.example.invalid/nstl" }),
    [string]$Namespace = $(if ($env:IMAGE_NAMESPACE) { $env:IMAGE_NAMESPACE } else { "content-assessment" }),
    [string]$Version = $(if ($env:IMAGE_TAG) { $env:IMAGE_TAG } else { "v1.1.0" }),
    [string]$PythonBaseImage = $(if ($env:PYTHON_BASE_IMAGE) { $env:PYTHON_BASE_IMAGE } else { "docker.1ms.run/library/python:3.12-slim" }),
    [string]$NodeBaseImage = $(if ($env:NODE_BASE_IMAGE) { $env:NODE_BASE_IMAGE } else { "docker.1ms.run/library/node:20-alpine" }),
    [string]$NginxBaseImage = $(if ($env:NGINX_BASE_IMAGE) { $env:NGINX_BASE_IMAGE } else { "docker.1ms.run/library/nginx:1.27-alpine" }),
    [string]$NpmRegistry = $(if ($env:NPM_REGISTRY) { $env:NPM_REGISTRY } else { "https://registry.npmmirror.com" }),
    [ValidateSet("server_v4_flash", "personal_v4_flash")]
    [string]$ModelProfile = "server_v4_flash",
    [switch]$UsePrebuiltFrontend,
    [string]$DockerHost = $env:DOCKER_HOST
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$backendImage = "$Registry/$Namespace/content-assessment-backend:$Version"
$frontendImage = "$Registry/$Namespace/content-assessment-frontend:$Version"
$frontendApiBase = "/api/service/nstl/ai/agent/content-assessment"
$frontendPublicBase = "/ai/agent/content_assessment/"
$dockerArgs = @()
if ($DockerHost) {
    $dockerArgs += @("-H", $DockerHost)
}

Write-Host "Building ContentAssessment test images"
Write-Host "Backend:  $backendImage"
Write-Host "Frontend: $frontendImage"
Write-Host "Python base: $PythonBaseImage"
Write-Host "Node base:   $NodeBaseImage"
Write-Host "Nginx base:  $NginxBaseImage"
Write-Host "NPM registry: $NpmRegistry"
Write-Host "Model profile: $ModelProfile"
if ($DockerHost) {
    Write-Host "Docker host: $DockerHost"
}

Write-Host "Removing old images if they exist"
foreach ($image in @($backendImage, $frontendImage)) {
    $existing = & docker @dockerArgs image ls --quiet $image
    if ($existing) {
        & docker @dockerArgs rmi -f $image
        Write-Host "  Removed $image"
    }
    else {
        Write-Host "  Skip missing image: $image"
    }
}

& docker @dockerArgs build `
    -f docker/Dockerfile.backend `
    --build-arg PYTHON_BASE_IMAGE=$PythonBaseImage `
    --build-arg CONTENT_ASSESSMENT_MODEL_PROFILE=$ModelProfile `
    -t $backendImage `
    .

if ($UsePrebuiltFrontend) {
    Write-Host "Building frontend dist locally"
    Push-Location frontend/web
    try {
        $previousApiBase = $env:VITE_API_BASE_URL
        $previousPublicBase = $env:VITE_PUBLIC_BASE
        $env:VITE_API_BASE_URL = $frontendApiBase
        $env:VITE_PUBLIC_BASE = $frontendPublicBase
        & npm run build
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

    & docker @dockerArgs build `
        -f docker/Dockerfile.frontend.dist `
        --build-arg NGINX_BASE_IMAGE=$NginxBaseImage `
        -t $frontendImage `
        .
}
else {
    & docker @dockerArgs build `
        -f docker/Dockerfile.frontend `
        --build-arg NODE_BASE_IMAGE=$NodeBaseImage `
        --build-arg NGINX_BASE_IMAGE=$NginxBaseImage `
        --build-arg NPM_REGISTRY=$NpmRegistry `
        --build-arg VITE_API_BASE_URL=$frontendApiBase `
        --build-arg VITE_PUBLIC_BASE=$frontendPublicBase `
        -t $frontendImage `
        .
}

Write-Host "Build complete"
Write-Host "Remote build example:"
Write-Host "  `$env:DOCKER_HOST='tcp://127.0.0.1:2375'; .\scripts\docker-build-test.ps1 -Version $Version"
