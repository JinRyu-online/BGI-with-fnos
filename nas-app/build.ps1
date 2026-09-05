#Requires -Version 5.1
<#
.SYNOPSIS
    打包 nas-app 为 .fpk（Windows 版）。自动先构建前端 SPA（npm run build:deploy）。
.DESCRIPTION
    流程：
      0. npm run build:deploy（frontend 存在时）——vue-tsc 类型检查 + vite 构建 + 复制到 static/spa/
      1. fnpack build 打包 .fpk
    工具链优先级：
      1. 同级 pack/fnpack.exe（仓库内置）
      2. $env:FNPACK 环境变量
      3. PATH 中的 fnpack
    用法：
      ./build.ps1                       # 全流程
      ./build.ps1 -SkipFrontend         # 跳过前端构建（仅重打包）
      $env:FNPACK="C:\path\fnpack.exe"; ./build.ps1   # 显式指定覆盖
    参考 fnpack：https://developer.fnnas.com/docs/cli/fnpack/
    注意：双击/右键"使用 PowerShell 运行"时，结尾会暂停等待按键，不会闪退。
#>

param(
    [switch]$SkipFrontend
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

Set-Location $PSScriptRoot

# 结尾暂停：双击运行时不闪退（仅交互式会话暂停；CI/输出重定向不暂停）
function Pause-End {
    if ([Environment]::UserInteractive -and -not [Console]::IsOutputRedirected) {
        Write-Host ""
        Write-Host "按任意键关闭..." -ForegroundColor Gray
        $null = $Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown')
    }
}

# ---------- 步骤 0：前端 SPA 构建（vue-tsc + vite + 复制到 static/spa） ----------
$frontendDir = Join-Path $PSScriptRoot "frontend"
if (-not $SkipFrontend -and (Test-Path (Join-Path $frontendDir "package.json"))) {
    Write-Host "==> [0/2] 构建前端 SPA（npm run build:deploy）..." -ForegroundColor Cyan
    Push-Location $frontendDir
    try {
        if (-not (Test-Path (Join-Path $frontendDir "node_modules"))) {
            Write-Host "    node_modules 不存在，先执行 npm install ..." -ForegroundColor Yellow
            npm install
            if ($LASTEXITCODE -ne 0) { Write-Host "npm install 失败" -ForegroundColor Red; . Pause-End; exit 1 }
        }
        npm run build:deploy
        if ($LASTEXITCODE -ne 0) { Write-Host "前端构建失败（vue-tsc 类型检查或 vite build 报错）" -ForegroundColor Red; . Pause-End; exit 1 }
    } finally {
        Pop-Location
    }
    Write-Host "==> 前端构建完成" -ForegroundColor Green
} elseif ($SkipFrontend) {
    Write-Host "==> [0/2] 跳过前端构建（-SkipFrontend）" -ForegroundColor Yellow
} else {
    Write-Host "==> [0/2] 未找到 frontend/package.json，跳过前端构建" -ForegroundColor Yellow
}

# ---------- 工具链解析：显式环境变量 > 仓库内置 pack/fnpack.exe > PATH ----------
$FNPACK = if ($env:FNPACK) {
    $env:FNPACK
} elseif (Test-Path (Join-Path $PSScriptRoot "pack\fnpack.exe")) {
    Join-Path $PSScriptRoot "pack\fnpack.exe"
} else {
    "fnpack"
}

if (-not (Get-Command $FNPACK -ErrorAction SilentlyContinue)) {
    Write-Host "错误：未找到 fnpack。已内置于 pack/fnpack.exe；" -ForegroundColor Red
    Write-Host "      或下载后设置 `$env:FNPACK=路径; ./build.ps1" -ForegroundColor Red
    Write-Host "      下载地址：https://developer.fnnas.com/docs/cli/fnpack/" -ForegroundColor Gray
    . Pause-End
    exit 1
}

# ---------- 步骤 1：fnpack 打包 ----------
Write-Host "==> [1/1] 在 $PSScriptRoot 执行 fnpack build ..." -ForegroundColor Cyan
& $FNPACK build
if ($LASTEXITCODE -ne 0) { . Pause-End; exit 1 }

Write-Host ""
Write-Host "==> 完成。生成的 .fpk 位于当前目录：" -ForegroundColor Green
Get-ChildItem "$PSScriptRoot\*.fpk" | ForEach-Object { Write-Host "  $($_.Name)" }
Write-Host ""
Write-Host "安装方式（在 NAS 上）：" -ForegroundColor Cyan
Write-Host "  appcenter-cli install-fpk <.fpk 文件>"
Write-Host "  或飞牛应用中心后台上传。"

# ---------- 结尾暂停：双击运行时不闪退 ----------
. Pause-End

function Pause-End {
    # 非交互式（CI/管道）不暂停
    if ([Environment]::UserInteractive -and -not [Console]::IsOutputRedirected) {
        Write-Host ""
        Write-Host "按任意键关闭..." -ForegroundColor Gray
        $null = $Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown')
    }
}
