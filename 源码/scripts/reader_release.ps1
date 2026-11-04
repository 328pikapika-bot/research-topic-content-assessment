# 按源码清单构建并推送第二期镜像；部署由 reader_deploy.py 更新既有 Stack。
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
$manifestPath = Join-Path (Split-Path $taskRoot -Parent) '资料/测试反馈/第二期优化/报告与结果页优化_第一版/源码与构建清单.json'
$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$taskDaemon = 'tcp://127.0.0.1:2375'
$taskRegistry = 'docker-hosted.example.invalid/nstl/content-assessment'
$taskBaseline = 'v1.2.0-20261014-0354'
Set-Location -LiteralPath $taskRoot
foreach ($kind in @('backend','frontend')) {
    $image = "$taskRegistry/content-assessment-${kind}:$($manifest.tag)"
    $base = "$taskRegistry/content-assessment-${kind}:$taskBaseline"
    & docker -H $taskDaemon build --progress=plain --build-arg "BASE_IMAGE=$base" --build-arg "BUILD_CREATED=$($manifest.created_at)" --build-arg "VCS_REF=$($manifest.source_manifest_sha256)" -f "docker/Dockerfile.${kind}.incremental" -t $image .
    if ($LASTEXITCODE -ne 0) { throw "构建失败：$kind" }
    & docker -H $taskDaemon push $image
    if ($LASTEXITCODE -ne 0) { throw "推送失败：$kind" }
}
