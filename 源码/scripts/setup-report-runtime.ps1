param([string]$MsiPath, [string]$FontPath)
$ErrorActionPreference = 'Stop'
$reportProject = Split-Path -Parent $PSScriptRoot
$reportRuntime = Join-Path $reportProject '.runtime\libreoffice'
if (-not (Test-Path -LiteralPath (Join-Path $reportRuntime 'program\soffice.com'))) {
    if (-not $MsiPath) { throw '请传入官方 LibreOffice x64 MSI 的绝对路径。' }
    $reportMsi = (Resolve-Path -LiteralPath $MsiPath).Path
    $signature = Get-AuthenticodeSignature -LiteralPath $reportMsi
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'The Document Foundation') {
        throw 'LibreOffice MSI 签名无效。'
    }
    $process = Start-Process -FilePath 'C:\Windows\System32\msiexec.exe' -ArgumentList @('/a', "`"$reportMsi`"", '/qn', "TARGETDIR=`"$reportRuntime`"") -WindowStyle Hidden -PassThru
    $process.WaitForExit()
    if ($process.ExitCode -ne 0) { throw "服务运行时解压失败：$($process.ExitCode)" }
}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'report_runtime_policy.xcd') -Destination (Join-Path $reportRuntime 'share\registry\assessment-service.xcd')
if ($FontPath) {
    $font = (Resolve-Path -LiteralPath $FontPath).Path
    $fontDir = Join-Path $reportRuntime 'share\fonts\truetype'
    New-Item -ItemType Directory -Path $fontDir -Force | Out-Null
    Copy-Item -LiteralPath $font -Destination (Join-Path $fontDir 'NotoSansCJKsc-Regular.otf')
}
Write-Output "报告专用转换器：$reportRuntime\program\soffice.com"
