#Requires -Version 5.1
<#
.SYNOPSIS
    打包 nas-app 为 .fpk（Windows 版）
.DESCRIPTION
    工具链优先级：
      1. 同级 pack/fnpack.exe（仓库内置）
      2. $env=FNPACK 环境变量
      3. PATH 中的 fnpack
    用法：
      ./build.ps1                       # 三步自动回退
      $env:FNPACK="C:\path\fnpack.exe"; ./build.ps1   # 显式指定覆盖
    参考 fnpack：https://developer.fnnas.com/docs/cli/fnpack/
#>

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

Set-Location $PSScriptRoot

# 工具链解析：显式环境变量 > 仓库内置 pack/fnpack.exe > PATH
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
    exit 1
}

Write-Host "==> 在 $PSScriptRoot 执行 fnpack build ..." -ForegroundColor Cyan
& $FNPACK build
if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Host ""
Write-Host "==> 完成。生成的 .fpk 位于当前目录：" -ForegroundColor Green
Get-ChildItem "$PSScriptRoot\*.fpk" | ForEach-Object { Write-Host "  $($_.Name)" }
Write-Host ""
Write-Host "安装方式（在 NAS 上）：" -ForegroundColor Cyan
Write-Host "  appcenter-cli install-fpk <.fpk 文件>"
Write-Host "  或飞牛应用中心后台上传。"
