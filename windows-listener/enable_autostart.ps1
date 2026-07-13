#Requires -Version 5.1
<#
.SYNOPSIS
    注册开机自启计划任务（控制台可见，不提升权限）
.DESCRIPTION
    用户登录时自动运行 start_listener.py，弹出 PowerShell 控制台窗口，
    可看到端口检测、启动日志和运行状态。
    任务名：BGI-Trigger-Listener-Console

    ⚠ 权限说明：默认 /RL LIMITED（不弹 UAC）。
      BetterGI 操作游戏窗口通常需要管理员权限。
      如果发现触发后 BetterGI 无法启动/聚焦，请改为 /RL HIGHEST
      （把本脚本中 $RL 的值改成 "HIGHEST"，并重新运行一次）。
      改 HIGHEST 后注册任务时需要管理员权限。
#>

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$BASE = $PSScriptRoot
$START_PS1 = Join-Path $BASE "start_listener.ps1"
$TASKNAME = "BGI-Trigger-Listener-Console"
$RL = "LIMITED"   # 需要管理员时改成 "HIGHEST"

# 预检查
if (-not (Test-Path $START_PS1)) {
    Write-Host "[ERROR] 找不到 $START_PS1" -ForegroundColor Red
    exit 1
}

# 已存在则先删除，保证干净覆盖
$existing = schtasks /Query /TN $TASKNAME 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "[提示] 任务 $TASKNAME 已存在，先删除旧任务..." -ForegroundColor Yellow
    schtasks /Delete /TN $TASKNAME /F | Out-Null
}

# 注册：登录触发，运行 start_listener.ps1，控制台可见
# 用 powershell.exe（非 powershell_ise / pythonw），保证有可见窗口
$tr = "powershell.exe -ExecutionPolicy Bypass -File `"$START_PS1`""
Write-Host "`n==> 注册计划任务 $TASKNAME ..." -ForegroundColor Cyan
Write-Host "    触发器 : 用户登录 (ONLOGON)" -ForegroundColor Gray
Write-Host "    权限   : $RL" -ForegroundColor Gray
Write-Host "    命令   : $tr" -ForegroundColor Gray
Write-Host ""

schtasks /Create /SC ONLOGON /RL $RL /TN $TASKNAME /TR $tr /F
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] 计划任务注册失败。" -ForegroundColor Red
    if ($RL -eq "HIGHEST") {
        Write-Host "       /RL HIGHEST 需要管理员权限，请用管理员 PowerShell 运行。" -ForegroundColor Yellow
    }
    Read-Host "按回车键退出"
    exit 1
}

# 验证
Write-Host "[OK] 注册成功。" -ForegroundColor Green
$info = schtasks /Query /TN $TASKNAME /V /FO LIST 2>$null | Select-String -Pattern "TaskName|Status|Next Run|Trigger"
if ($info) { $info | ForEach-Object { Write-Host "     $_" -ForegroundColor Gray } }

Write-Host ""
Write-Host "后续：" -ForegroundColor Cyan
Write-Host "  - 下次登录时会自动弹出控制台并启动 listener。" -ForegroundColor Gray
Write-Host "  - 如需注销，运行 .\disable_autostart.ps1" -ForegroundColor Gray
Write-Host "  - 如需改管理员权限，编辑本脚本 `$RL 后重跑。" -ForegroundColor Gray
Write-Host ""
Read-Host "按回车键退出"
