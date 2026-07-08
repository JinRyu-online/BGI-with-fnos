@echo off
REM 打包 nas-app 为 .fpk（Windows）。
REM 用法：
REM   build.bat                       REM 使用 PATH 中的 fnpack
REM   set FNPACK=C:\path\fnpack.exe ^& build.bat
REM 下载 fnpack：https://developer.fnnas.com/docs/cli/fnpack/ （选 windows-amd64，改名 fnpack.exe）
setlocal
cd /d "%~dp0"

if "%FNPACK%"=="" set FNPACK=fnpack

where "%FNPACK%" >nul 2>&1
if errorlevel 1 (
  echo 错误：未找到 fnpack。请下载后放入 PATH，或 set FNPACK=路径 ^& build.bat
  exit /b 1
)

echo ==^> 在 %CD% 执行 fnpack build ...
call "%FNPACK%" build
if errorlevel 1 exit /b 1

echo.
echo ==^> 完成。生成的 .fpk 位于当前目录。
dir /b bgi-trigger*.fpk 2>nul || dir /b *.fpk
echo.
echo 安装到飞牛 NAS：
echo   appcenter-cli install-fpk ^<上述 .fpk 文件^>
echo   或在飞牛应用中心后台上传。
endlocal
