@echo off
setlocal EnableExtensions
call "%~dp0common.bat" :init
if not exist "%VITE_CLI%" (
    echo ERROR: frontend dependencies not found. Run scripts\install.bat first.
    exit /b 1
)
set "VITE_API_PROXY_TARGET=http://127.0.0.1:%BACKEND_PORT%"
pushd "%FRONTEND%"
if /I "%~1"=="dev" (
    call node "%VITE_CLI%" --host 0.0.0.0 --port %FRONTEND_PORT%
) else (
    if /I "%~1"=="--build" call npm.cmd run build
    call node "%VITE_CLI%" preview --host 0.0.0.0 --port %FRONTEND_PORT%
)
set "EXIT_CODE=%ERRORLEVEL%"
popd
exit /b %EXIT_CODE%
