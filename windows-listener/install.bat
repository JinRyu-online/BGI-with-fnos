@echo off
REM Installs the BetterGI trigger listener as a Windows scheduled task.
REM Run once from an elevated prompt:  install.bat
setlocal

set BASE=%~dp0
set BASE=%BASE:~0,-1%
set PY=%BASE%\venv\Scripts\pythonw.exe
set SCRIPT=%BASE%\listener.py
set TASKNAME=BGI-Trigger-Listener

REM Admin check
net session >nul 2>&1
if errorlevel 1 (
  echo Please run this script as Administrator.
  pause
  exit /b 1
)

REM Create venv if missing
if not exist "%BASE%\venv" (
  echo Creating virtual environment...
  python -m venv "%BASE%\venv"
)

echo Installing dependencies...
"%BASE%\venv\Scripts\python.exe" -m pip install --upgrade pip >nul
"%BASE%\venv\Scripts\python.exe" -m pip install -r "%BASE%\requirements.txt"

REM Seed config / tasks from examples if absent
if not exist "%BASE%\config.toml" copy "%BASE%\config.toml.example" "%BASE%\config.toml" >nul
if not exist "%BASE%\tasks.json" copy "%BASE%\tasks.json.example" "%BASE%\tasks.json" >nul

REM Register the scheduled task: start on logon, highest privileges, restart on failure.
echo Registering scheduled task "%TASKNAME%"...
schtasks /Create /SC ONLOGON /RL HIGHEST /TN "%TASKNAME%" /TR "\"%PY%\" \"%SCRIPT%\"" /F

echo.
echo Done. On next logon the listener starts and (first run) shows the API key.
echo To show the key again:  "%PY%" "%SCRIPT%" --show-key
echo To start now:           "%PY%" "%SCRIPT%"
pause
