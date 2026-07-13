#Requires -Version 5.1
<#
.SYNOPSIS
    注销开机自启计划任务（BGI-Trigger-Listener-Console）
.DESCRIPTION
    删除 enable_autostart.ps1 注册的计划任务，取消登录自启。
#>

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$TASKNAME = "BGI-Trigger-Listener-Console"

$existing = schtasks /Query /TN $TASKNAME 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "任务 $TASKNAME 不存在，无需注销。" -ForegroundColor Cyan
    Read-Host "按回车键退出"
    exit 0
}

Write-Host "==> 删除计划任务 $TASKNAME ..." -ForegroundColor Yellow
schtasks /Delete /TN $TASKNAME /F
if ($LASTEXITCODE -eq 0) {
    Write-Host "[OK] 已注销，登录时不再自启。" -ForegroundColor Green
} else {
    Write-Host "[ERROR] 删除失败，任务可能仍存在。" -ForegroundColor Red
}
Read-Host "按回车键退出"
