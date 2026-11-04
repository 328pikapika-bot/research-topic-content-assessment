# check_nstl_local_env.ps1 — Windows PowerShell 本地开发环境检查脚本
#
# 检查项：
#   - hosts 文件中是否有 nstl-local 域名映射
#   - 是否安装了 mkcert 或其他本地HTTPS证书工具
#   - 常用端口（5173前端、8000后端）是否被占用
#   - Node.js、Python 版本
#   - 是否能访问 NSTL 开发环境域名
#   - 输出彩色（如支持）的检查结果摘要

param(
    [string]$NstlHost = "goodidea.nstl.gov.cn",
    [int]$FrontendPort = 5173,
    [int]$BackendPort = 8000
)

# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

function Write-ColorResult {
    param(
        [string]$Status,
        [string]$Name,
        [string]$Detail = ""
    )
    # PowerShell 7+ 支持 ANSI 颜色，Windows PowerShell 5.1 也支持部分
    $colorMap = @{
        "PASS" = "Green"
        "FAIL" = "Red"
        "WARN" = "Yellow"
    }
    $symbolMap = @{
        "PASS" = "[PASS]"
        "FAIL" = "[FAIL]"
        "WARN" = "[WARN]"
    }
    $color = $colorMap[$Status]
    $symbol = $symbolMap[$Status]

    if ($Detail) {
        Write-Host "  " -NoNewline
        Write-Host $symbol -ForegroundColor $color -NoNewline
        Write-Host " $Name"
        Write-Host "       $Detail"
    } else {
        Write-Host "  " -NoNewline
        Write-Host $symbol -ForegroundColor $color -NoNewline
        Write-Host " $Name"
    }
}

function Test-TcpPort {
    param([int]$Port)
    try {
        $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
        $listener.Start()
        $listener.Stop()
        return $false  # 端口未被占用
    } catch {
        return $true   # 端口已被占用
    }
}

# ---------------------------------------------------------------------------
# 开始检查
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  NSTL 本地开发环境检查" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

$passCount = 0
$failCount = 0
$warnCount = 0

# ---------------------------------------------------------------------------
# 1. 检查 hosts 文件中是否有 nstl-local 域名映射
# ---------------------------------------------------------------------------

Write-Host "--- 域名映射检查 ---" -ForegroundColor White

$hostsPath = "$env:SystemRoot\System32\drivers\etc\hosts"
if (Test-Path $hostsPath) {
    $hostsContent = Get-Content $hostsPath -ErrorAction SilentlyContinue
    $nstlEntry = $hostsContent | Where-Object { $_ -match "nstl" -and $_ -notmatch "^\s*#" }

    if ($nstlEntry) {
        $entry = ($nstlEntry | Select-Object -First 1).Trim()
        Write-ColorResult "PASS" "hosts 文件 NSTL 域名映射" "找到: $entry"
        $passCount++
    } else {
        Write-ColorResult "FAIL" "hosts 文件 NSTL 域名映射" "未找到 nstl 相关映射，需添加: 127.0.0.1 $NstlHost"
        $failCount++
    }
} else {
    Write-ColorResult "FAIL" "hosts 文件" "hosts 文件不存在: $hostsPath"
    $failCount++
}

# ---------------------------------------------------------------------------
# 2. 检查是否安装了 mkcert 或其他本地HTTPS证书工具
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "--- HTTPS 证书工具检查 ---" -ForegroundColor White

$mkcertFound = $null -ne (Get-Command "mkcert" -ErrorAction SilentlyContinue)
if ($mkcertFound) {
    $mkcertVersion = & mkcert -version 2>$null
    Write-ColorResult "PASS" "mkcert 已安装" "版本: $mkcertVersion"
    $passCount++
} else {
    # 检查 openssl
    $opensslFound = $null -ne (Get-Command "openssl" -ErrorAction SilentlyContinue)
    if ($opensslFound) {
        $opensslVersion = & openssl version 2>$null
        Write-ColorResult "WARN" "mkcert 未安装，但 openssl 可用" "OpenSSL: $opensslVersion；建议安装 mkcert 简化本地证书管理"
        $warnCount++
    } else {
        Write-ColorResult "FAIL" "HTTPS 证书工具" "未安装 mkcert 或 openssl，本地 HTTPS 开发需要证书工具"
        $failCount++
    }
}

# 检查本地证书文件
$certDirs = @(
    ".\certs",
    ".\ssl",
    ".\localhost"
)
$certFound = $false
foreach ($dir in $certDirs) {
    if (Test-Path $dir) {
        $pfxFiles = Get-ChildItem $dir -Filter "*.pfx" -ErrorAction SilentlyContinue
        $pemFiles = Get-ChildItem $dir -Filter "*.pem" -ErrorAction SilentlyContinue
        if ($pfxFiles -or $pemFiles) {
            $certCount = ($pfxFiles.Count + $pemFiles.Count)
            Write-ColorResult "PASS" "本地证书文件" "在 $dir 找到 $certCount 个证书文件"
            $certFound = $true
            $passCount++
            break
        }
    }
}
if (-not $certFound -and $mkcertFound) {
    Write-ColorResult "WARN" "本地证书文件" "mkcert 已安装但未找到本地证书，可能需要运行 mkcert 创建证书"
    $warnCount++
}

# ---------------------------------------------------------------------------
# 3. 检查常用端口是否被占用
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "--- 端口占用检查 ---" -ForegroundColor White

$frontendOccupied = Test-TcpPort -Port $FrontendPort
$backendOccupied = Test-TcpPort -Port $BackendPort

if ($frontendOccupied) {
    Write-ColorResult "WARN" "前端端口 $FrontendPort" "端口已被占用"
    $warnCount++
} else {
    Write-ColorResult "PASS" "前端端口 $FrontendPort" "端口可用"
    $passCount++
}

if ($backendOccupied) {
    Write-ColorResult "WARN" "后端端口 $BackendPort" "端口已被占用"
    $warnCount++
} else {
    Write-ColorResult "PASS" "后端端口 $BackendPort" "端口可用"
    $passCount++
}

# ---------------------------------------------------------------------------
# 4. 检查 Node.js、Python 版本
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "--- 运行时版本检查 ---" -ForegroundColor White

$nodeCmd = Get-Command "node" -ErrorAction SilentlyContinue
if ($nodeCmd) {
    $nodeVersion = & node --version 2>$null
    Write-ColorResult "PASS" "Node.js" "版本: $nodeVersion"
    $passCount++
} else {
    Write-ColorResult "FAIL" "Node.js" "未安装 Node.js"
    $failCount++
}

$npmCmd = Get-Command "npm" -ErrorAction SilentlyContinue
if ($npmCmd) {
    $npmVersion = & npm --version 2>$null
    Write-ColorResult "PASS" "npm" "版本: $npmVersion"
    $passCount++
} else {
    Write-ColorResult "WARN" "npm" "未安装 npm"
    $warnCount++
}

$pythonCmd = Get-Command "python" -ErrorAction SilentlyContinue
if ($pythonCmd) {
    $pythonVersion = & python --version 2>&1
    $pythonVersionStr = $pythonVersion.ToString().Trim()
    Write-ColorResult "PASS" "Python" "版本: $pythonVersionStr"
    $passCount++
} else {
    # 尝试 python3
    $python3Cmd = Get-Command "python3" -ErrorAction SilentlyContinue
    if ($python3Cmd) {
        $python3Version = & python3 --version 2>&1
        $python3VersionStr = $python3Version.ToString().Trim()
        Write-ColorResult "PASS" "Python" "版本: $python3VersionStr (python3)"
        $passCount++
    } else {
        Write-ColorResult "FAIL" "Python" "未安装 Python"
        $failCount++
    }
}

# ---------------------------------------------------------------------------
# 5. 检查是否能访问 NSTL 开发环境域名
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "--- NSTL 域名连通性检查 ---" -ForegroundColor White

$nstlUrls = @(
    @{ Name = "NSTL 登录服务"; Url = "https://login.nstl.gov.cn" },
    @{ Name = "NSTL 主站"; Url = "https://www.nstl.gov.cn" },
    @{ Name = "本地开发域名"; Url = "https://$NstlHost" }
)

foreach ($item in $nstlUrls) {
    try {
        $request = [System.Net.WebRequest]::Create($item.Url)
        $request.Timeout = 5000
        $request.Method = "HEAD"
        $response = $request.GetResponse()
        $statusCode = [int]$response.StatusCode
        $response.Close()
        Write-ColorResult "PASS" "$($item.Name) ($($item.Url))" "状态码: $statusCode"
        $passCount++
    } catch [System.Net.WebException] {
        $exMsg = $_.Exception.Message
        if ($exMsg -match "trust|certificate|SSL|TLS") {
            Write-ColorResult "WARN" "$($item.Name) ($($item.Url))" "SSL证书问题: $exMsg"
            $warnCount++
        } else {
            Write-ColorResult "FAIL" "$($item.Name) ($($item.Url))" "连接失败: $exMsg"
            $failCount++
        }
    } catch {
        $exMsg = $_.Exception.Message
        Write-ColorResult "FAIL" "$($item.Name) ($($item.Url))" "连接失败: $exMsg"
        $failCount++
    }
}

# ---------------------------------------------------------------------------
# 摘要
# ---------------------------------------------------------------------------

$totalCount = $passCount + $failCount + $warnCount

Write-Host ""
Write-Host "------------------------------------------------------------" -ForegroundColor White
Write-Host "  合计: $totalCount 项 | " -NoNewline
Write-Host "PASS: $passCount" -ForegroundColor Green -NoNewline
Write-Host " | " -NoNewline
Write-Host "FAIL: $failCount" -ForegroundColor Red -NoNewline
Write-Host " | " -NoNewline
Write-Host "WARN: $warnCount" -ForegroundColor Yellow
Write-Host "------------------------------------------------------------" -ForegroundColor White

if ($failCount -eq 0 -and $warnCount -eq 0) {
    Write-Host "  结论: 环境检查通过，可以开始本地开发" -ForegroundColor Green
} elseif ($failCount -gt 0) {
    Write-Host "  结论: 存在 $failCount 项 FAIL，请先修复后再进行开发" -ForegroundColor Red
} else {
    Write-Host "  结论: 基本通过，存在 $warnCount 项 WARN 建议优化" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
