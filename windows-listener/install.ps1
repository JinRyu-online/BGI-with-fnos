#Requires -Version 5.1
<#
.SYNOPSIS
    BetterGI Trigger Listener - Windows 安装脚本
.DESCRIPTION
    功能：创建虚拟环境 -> 装依赖 -> 生成配置 -> 注册计划任务
    用法：右键「使用 PowerShell 运行」/ 管理员 PowerShell 中 ./install.ps1
    备注：优先用 uv（快），不可用时自动回退到 pip。
#>

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

# ---------- 管理员权限检查 ----------
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[提示] 需要管理员权限（注册计划任务 + 最高权限自启）。" -ForegroundColor Yellow
    Write-Host "      正在请求 UAC 提升..." -ForegroundColor Yellow
    Start-Process -FilePath "powershell.exe" `
        -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"" `
        -Verb RunAs
    exit
}

# ---------- 路径与变量 ----------
$BASE = $PSScriptRoot
$venv = Join-Path $BASE "venv"
$PY  = Join-Path $venv "Scripts\python.exe"
$PYW = Join-Path $venv "Scripts\pythonw.exe"
$SCRIPT = Join-Path $BASE "listener.py"
$TASKNAME = "BGI-Trigger-Listener"

# 镜像（清华默认；阿里云/腾讯云可改下面对应行）
$MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"
$TRUSTED = "pypi.tuna.tsinghua.edu.cn"

# uv 相关的环境变量
$env:UV_INDEX_URL = $MIRROR
$env:UV_HTTP_TIMEOUT = "120"

# ---------- 1. 创建虚拟环境 ----------
if (-not (Test-Path $venv)) {
    Write-Host "[1/5] 创建虚拟环境..." -ForegroundColor Cyan
    python -m venv $venv
} else {
    Write-Host "[1/5] 虚拟环境已存在，跳过。" -ForegroundColor Cyan
}

# ---------- 2. 安装依赖 ----------
Write-Host "[2/5] 安装依赖（$MIRROR）..." -ForegroundColor Cyan
Write-Host "      优先用 uv；失败则回退到 pip..." -ForegroundColor Gray

$depsInstalled = $false

# 先装 uv 进 venv
& $PY -m pip install uv -i $MIRROR --trusted-host $TRUSTED 2>$null
if ($LASTEXITCODE -eq 0) {
    & $PY -m uv pip install -r (Join-Path $BASE "requirements.txt")
    if ($LASTEXITCODE -eq 0) {
        $depsInstalled = $true
    }
}

if (-not $depsInstalled) {
    Write-Host "      uv 不可用/失败，回退到 pip 直接安装..." -ForegroundColor DarkYellow
    & $PY -m pip install -r (Join-Path $BASE "requirements.txt") -i $MIRROR --trusted-host $TRUSTED
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[错误] 依赖安装失败，请检查网络。" -ForegroundColor Red
        Read-Host "按回车键退出"
        exit 1
    }
}
Write-Host "[2/5] 依赖安装完成。" -ForegroundColor Green

# ---------- 3. 生成配置文件（从 .example） ----------
Write-Host "[3/5] 生成配置文件..." -ForegroundColor Cyan
if (-not (Test-Path (Join-Path $BASE "config.toml"))) {
    Copy-Item (Join-Path $BASE "config.toml.example") (Join-Path $BASE "config.toml")
}
$tasksDir = Join-Path $BASE "tasks"
if (-not (Test-Path $tasksDir)) {
    New-Item -ItemType Directory -Path $tasksDir | Out-Null
}
if (-not (Test-Path (Join-Path $tasksDir "tasks.json"))) {
    Copy-Item (Join-Path $tasksDir "tasks.json.example") (Join-Path $tasksDir "tasks.json")
}

# ---------- 4. 注册计划任务 ----------
# /SC ONLOGON  登录后触发
# /RL HIGHEST  最高权限（BetterGI 需要管理员）
# /F           强制覆盖同名任务
Write-Host "[4/5] 注册计划任务 $TASKNAME（登录自启 + 最高权限）..." -ForegroundColor Cyan
$tr = "`"$PYW`" `"$SCRIPT`""
schtasks /Create /SC ONLOGON /RL HIGHEST /TN $TASKNAME /TR $tr /F
if ($LASTEXITCODE -ne 0) {
    Write-Host "[错误] 计划任务注册失败。" -ForegroundColor Red
    Read-Host "按回车键退出"
    exit 1
}

# ---------- 5. 结束 ----------
Write-Host "[5/5] 安装完成。" -ForegroundColor Green
Write-Host ""
Write-Host "后续步骤：" -ForegroundColor Cyan
Write-Host "  1. 编辑 `"$BASE\config.toml`"，填写 bettergi.exe_path（反斜杠双写）"
Write-Host "  2. 编辑 `"$BASE\tasks\tasks.json`"，确保 groups 与 BetterGI 调度器组名一致"
Write-Host "     （支持热加载，改完下次 /tasks 即生效）"
Write-Host "  3. 首次运行会弹窗提示 API 密钥（复制到 NAS 应用）："
Write-Host "      `"$PYW`" `"$SCRIPT`""
Write-Host "  4. 下次登录时计划任务会自动启动服务。"
Write-Host ""
Read-Host "按回车键退出"
