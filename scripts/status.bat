@echo off
setlocal EnableExtensions
call "%~dp0common.bat" :init

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0status.ps1" -BackendPort %BACKEND_PORT% -FrontendPort %FRONTEND_PORT%
if errorlevel 1 exit /b 1
if /I not "%~1"=="--nopause" pause
exit /b 0
