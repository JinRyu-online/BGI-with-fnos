#Requires -Version 5.1
<#
.SYNOPSIS
    前台启动 BGI listener 服务
.DESCRIPTION
    仅启动 listener.py 并等待服务就绪，不发送任何触发请求。
    服务启动后窗保持打开，关闭窗口即停止服务。
#>

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

# ===== 1. 构建绝对路径（避免 SSH/远程执行时工作目录不一致）=====
$pythonExe  = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
$listenerPy = Join-Path $PSScriptRoot "listener.py"

# 预检查文件是否存在
if (-not (Test-Path $pythonExe)) {
    Write-Host "[ERROR] 找不到 Python: $pythonExe" -ForegroundColor Red
    Read-Host "按回车键退出"
    exit 1
}
if (-not (Test-Path $listenerPy)) {
    Write-Host "[ERROR] 找不到 listener.py: $listenerPy" -ForegroundColor Red
    Read-Host "按回车键退出"
    exit 1
}

# ===== 2. 前台启动 listener =====
Write-Host "`n==> 启动 BGI listener ..." -ForegroundColor Cyan
Write-Host "    Python : $pythonExe" -ForegroundColor Gray
Write-Host "    Script : $listenerPy" -ForegroundColor Gray
Write-Host "    工作目录: $PSScriptRoot" -ForegroundColor Gray
Write-Host ""
Write-Host "提示：关闭此窗口即停止服务。Ctrl+C 可中断。" -ForegroundColor DarkYellow
Write-Host ""

try {
    & $pythonExe $listenerPy
    $exitCode = $LASTEXITCODE
} catch {
    Write-Host "[ERROR] 启动失败: $_" -ForegroundColor Red
    $exitCode = 1
}

# ===== 3. 结束 =====
Write-Host ""
if ($exitCode -eq 0) {
    Write-Host "==> listener 已退出 (exit code: $exitCode)" -ForegroundColor Green
} else {
    Write-Host "==> listener 异常退出 (exit code: $exitCode)" -ForegroundColor Red
}
Read-Host "按回车键关闭"
exit $exitCode
