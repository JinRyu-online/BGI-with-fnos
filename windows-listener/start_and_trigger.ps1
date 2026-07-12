#Requires -Version 5.1
<#
.SYNOPSIS
    启动 BGI listener 并发送触发请求
.DESCRIPTION
    替代原 start_and_trigger.bat，解决中文乱码、相对路径和后台执行问题。
    如果 listener 已在运行，则跳过启动步骤、直接发送触发请求。
#>

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$PORT = 8765
$BASE_URL = "http://127.0.0.1:$PORT"

# 检查 listener 是否在运行：端口被 python 进程占用即视为已运行
function Test-ListenerRunning {
    param([int]$Port)
    $pids = @()
    netstat -ano | Select-String ":$Port\s" | ForEach-Object {
        $parts = $_ -split '\s+' | Where-Object { $_ -ne '' }
        $last = $parts[-1]
        if ($last -match '^\d+$') { $pids += [int]$last }
    }
    $pids = $pids | Select-Object -Unique
    if ($pids.Count -eq 0) { return @{ Running = $false } }
    $allPython = $true
    foreach ($p in $pids) {
        try {
            $proc = Get-Process -Id $p -ErrorAction Stop
            if ($proc.ProcessName -ne 'python') { $allPython = $false }
        } catch { $allPython = $false }
    }
    return @{ Running = $true; AllPython = $allPython; Pids = $pids }
}

# ===== 1. 获取 task_id =====
$TASK_ID = Read-Host "请输入 task_id（直接回车默认为 miner）"
if ([string]::IsNullOrWhiteSpace($TASK_ID)) {
    $TASK_ID = "miner"
}

# ===== 2. 构建绝对路径（避免 SSH/远程执行时工作目录不一致）=====
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

# ===== 3. 启动前检测：已在运行则跳过启动 =====
$skipStart = $false
$status = Test-ListenerRunning $PORT
if ($status.Running) {
    if ($status.AllPython) {
        Write-Host "`n==> listener 已在运行 (端口 $PORT, PID: $($status.Pids -join ', '))，跳过启动。" -ForegroundColor Cyan
        $skipStart = $true
    } else {
        Write-Host "[ERROR] 端口 $PORT 已被非 python 进程占用 (PID: $($status.Pids -join ', '))。" -ForegroundColor Red
        Write-Host "      请手动释放端口后重试。" -ForegroundColor Red
        Read-Host "按回车键退出"
        exit 1
    }
}

if (-not $skipStart) {
    # ===== 3a. 前台可见 + 非阻塞启动 listener =====
    Write-Host "`n[1/3] 正在启动 listener ..." -ForegroundColor Cyan
    Start-Process -FilePath $pythonExe `
        -ArgumentList "`"$listenerPy`"" `
        -WorkingDirectory $PSScriptRoot `
        -NoNewWindow

    # ===== 3b. 等待服务就绪 =====
    Write-Host "[2/3] 等待 3 秒让服务就绪 ..." -ForegroundColor Cyan
    Start-Sleep -Seconds 3
} else {
    Write-Host "`n[1/3] 跳过启动（已在运行）。" -ForegroundColor Cyan
    Write-Host "[2/3] 跳过等待。" -ForegroundColor Cyan
}

# ===== 4. 发送触发请求并打印完整结果 =====
Write-Host "[3/3] 发送触发请求，task_id = $TASK_ID ..." -ForegroundColor Cyan

$body = @{ task_id = $TASK_ID } | ConvertTo-Json -Compress
$headers = @{
    "User-Agent"    = "Apifox/1.0.0 (https://apifox.com)"
    "Authorization" = "Bearer 8c38afc257d60668177baad82f7bd351"
}

try {
    $response = Invoke-WebRequest -Uri "$BASE_URL/trigger" `
        -Method Post `
        -ContentType "application/json; charset=utf-8" `
        -Headers $headers `
        -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) `
        -UseBasicParsing

    Write-Host "HTTP Status: $($response.StatusCode) $($response.StatusDescription)" -ForegroundColor Green
    Write-Host "Response Body:" -ForegroundColor Yellow
    Write-Host $response.Content
} catch {
    $statusCode = $_.Exception.Response.StatusCode.value__
    $errorBody  = $_.ErrorDetails.Message

    Write-Host "HTTP Status: $statusCode (FAILED)" -ForegroundColor Red
    Write-Host "Error Response:" -ForegroundColor Red
    if ($errorBody) {
        Write-Host $errorBody
    } else {
        Write-Host $_.Exception.Message
    }
}

# ===== 完成 =====
Write-Host "`n===== 完成 =====" -ForegroundColor Green
Read-Host "按回车键退出"
