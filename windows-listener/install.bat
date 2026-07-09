@echo off
REM ============================================================
REM BetterGI Trigger Listener - Windows 安装脚本
REM 作用：建虚拟环境 -> 装依赖 -> 生成配置 -> 注册计划任务
REM 用法：右键「以管理员身份运行」本脚本
REM
REM 依赖下载：默认走清华 PyPI 镜像（国内友好）；
REM           包管理器优先用 uv（Python 版 pnpm，快），
REM           uv 不可用时自动回退到 pip。
REM 注意：本文件以 GBK 编码保存，勿用 chcp 65001（会破坏中文行解析）。
REM ============================================================
setlocal

set BASE=%~dp0
set BASE=%BASE:~0,-1%
set VENV=%BASE%\venv
set PY=%VENV%\Scripts\python.exe
set PYW=%VENV%\Scripts\pythonw.exe
set SCRIPT=%BASE%\listener.py
set TASKNAME=BGI-Trigger-Listener

REM 国内 PyPI 镜像（清华）。如需更换，改此处即可：
REM   阿里云  https://mirrors.aliyun.com/pypi/simple
REM   腾讯云  https://mirrors.cloud.tencent.com/pypi/simple
set MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple
set TRUSTED=pypi.tuna.tsinghua.edu.cn

REM uv 读取此环境变量作为包索引（等价于 --index）
set UV_INDEX_URL=%MIRROR%
set UV_HTTP_TIMEOUT=120

REM ---------- 1. 管理员检测 ----------
net session >nul 2>&1
if errorlevel 1 (
  echo [错误] 请以管理员身份运行此脚本。
  pause
  exit /b 1
)

REM ---------- 2. 创建虚拟环境 ----------
if not exist "%VENV%" (
  echo [1/5] 创建虚拟环境...
  python -m venv "%VENV%"
) else (
  echo [1/5] 虚拟环境已存在，跳过创建。
)

REM ---------- 3. 安装依赖 ----------
echo [2/5] 安装依赖（镜像：%MIRROR%）
echo       先尝试 uv（更快的包管理器）...
"%PY%" -m pip install uv -i %MIRROR% --trusted-host %TRUSTED% >nul 2>&1
if errorlevel 1 goto pipfallback

"%PY%" -m uv pip install -r "%BASE%\requirements.txt"
if errorlevel 1 goto pipfallback
goto depsdone

:pipfallback
echo       uv 不可用，回退到 pip 直接安装...
"%PY%" -m pip install -r "%BASE%\requirements.txt" -i %MIRROR% --trusted-host %TRUSTED%
if errorlevel 1 (
  echo [错误] 依赖安装失败，请检查网络或镜像。
  pause
  exit /b 1
)

:depsdone
echo [2/5] 依赖安装完成。

REM ---------- 4. 生成配置文件（从 .example） ----------
echo [3/5] 生成配置文件...
if not exist "%BASE%\config.toml" copy "%BASE%\config.toml.example" "%BASE%\config.toml" >nul
if not exist "%BASE%\tasks" mkdir "%BASE%\tasks"
if not exist "%BASE%\tasks\tasks.json" copy "%BASE%\tasks\tasks.json.example" "%BASE%\tasks\tasks.json" >nul

REM ---------- 5. 注册计划任务 ----------
REM /SC ONLOGON  登录后触发
REM /RL HIGHEST  最高权限（BetterGI 需管理员）
REM /F           强制覆盖已存在的同名任务
echo [4/5] 注册计划任务 %TASKNAME%（登录后自启 + 最高权限）...
schtasks /Create /SC ONLOGON /RL HIGHEST /TN "%TASKNAME%" /TR "\"%PYW%\" \"%SCRIPT%\"" /F
if errorlevel 1 (
  echo [错误] 计划任务注册失败。
  pause
  exit /b 1
)

REM ---------- 6. 完成 ----------
echo [5/5] 安装完成。
echo.
echo 下一步：
echo   1. 编辑 "%BASE%\config.toml"，填写 bettergi.exe_path（BetterGI.exe 路径，反斜杠双写）
echo   2. 编辑 "%BASE%\tasks\tasks.json"，确保 groups 与 BetterGI「全自动-调度器」组名一致
echo      （支持热加载：改完无需重启，下一次 /tasks 即生效）
echo   3. 首次启动会弹窗显示 API 密钥（复制到 NAS 应用）：
echo        "%PYW%" "%SCRIPT%"
echo   4. 注销重新登录，计划任务自动启动监听器
echo.
pause
