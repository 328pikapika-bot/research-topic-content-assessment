param([string]$ServiceRoot = 'D:\project\vector_library_sync\vector_sync_service', [int]$Port = 17609)
$ErrorActionPreference = 'Stop'
$python = Join-Path $ServiceRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw "全文服务 Python 环境不存在：$python" }
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "全文查询端口 $Port 已监听，保留现有进程。"
    exit 0
}
$runtime = Join-Path $PSScriptRoot '..\.runtime'
New-Item -ItemType Directory -Path $runtime -Force | Out-Null
Start-Process -FilePath $python -ArgumentList @('-m', 'uvicorn', 'app.fulltext_api:app', '--host', '127.0.0.1', '--port', "$Port") -WorkingDirectory $ServiceRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtime 'fulltext-search.out.log') -RedirectStandardError (Join-Path $runtime 'fulltext-search.err.log') | Out-Null
Write-Host "已启动仅查询的全文服务，端口 $Port。不会启动数据入库任务。"
