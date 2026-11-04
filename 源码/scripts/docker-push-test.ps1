param(
    [string]$Registry = $(if ($env:REGISTRY) { $env:REGISTRY } else { "docker-hosted.example.invalid/nstl" }),
    [string]$Namespace = $(if ($env:IMAGE_NAMESPACE) { $env:IMAGE_NAMESPACE } else { "content-assessment" }),
    [string]$Version = $(if ($env:IMAGE_TAG) { $env:IMAGE_TAG } else { "v1.1.0" }),
    [string]$HarborUrl = $(if ($env:HARBOR_URL) { $env:HARBOR_URL } else { "docker-hosted.example.invalid" }),
    [string]$HarborUser = $env:HARBOR_USER,
    [string]$HarborPass = $env:HARBOR_PASS,
    [string]$DockerHost = $env:DOCKER_HOST
)

$ErrorActionPreference = "Stop"

$backendImage = "$Registry/$Namespace/content-assessment-backend:$Version"
$frontendImage = "$Registry/$Namespace/content-assessment-frontend:$Version"
$dockerArgs = @()
if ($DockerHost) {
    $dockerArgs += @("-H", $DockerHost)
}

if ($HarborUser -and $HarborPass) {
    Write-Host "Logging in to $HarborUrl as $HarborUser"
    $HarborPass | & docker @dockerArgs login $HarborUrl -u $HarborUser --password-stdin
}
else {
    Write-Host "Skipping docker login. Set HARBOR_USER and HARBOR_PASS if login is required."
}

Write-Host "Pushing $backendImage"
& docker @dockerArgs push $backendImage

Write-Host "Pushing $frontendImage"
& docker @dockerArgs push $frontendImage

Write-Host "Push complete"
Write-Host "Remote push example:"
Write-Host "  `$env:DOCKER_HOST='tcp://127.0.0.1:2375'; .\scripts\docker-push-test.ps1 -Version $Version"
