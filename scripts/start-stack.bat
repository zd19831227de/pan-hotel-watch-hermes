@echo off
setlocal EnableExtensions EnableDelayedExpansion
call "%~dp0common.bat" :init
set "HERMES_EXE=%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\hermes.exe"
set "FEISHU_PROFILES_MANIFEST=%ROOT%\pan-hotel-hermes\config\feishu-profiles.yaml"
set "START_HERMES_GATEWAYS=%ROOT%\pan-hotel-hermes\scripts\start-hermes-gateways.ps1"

echo ===============================================
echo Pan Hotel Watch - one-click startup
echo Backend port:  %BACKEND_PORT%
echo Frontend port: %FRONTEND_PORT%
echo Feishu mode:   %FEISHU_AGENT_MODE%
echo ===============================================

if not exist "%PYTHON%" (
    echo ERROR: backend virtual environment not found. Run scripts\install.bat first.
    exit /b 1
)
if not exist "%VITE_CLI%" (
    echo ERROR: frontend dependencies not found. Run scripts\install.bat first.
    exit /b 1
)

set "BUILD_FRONTEND=0"
if /I "%~1"=="--rebuild" set "BUILD_FRONTEND=1"
if not exist "%FRONTEND%\dist\index.html" set "BUILD_FRONTEND=1"
if "%BUILD_FRONTEND%"=="1" (
    echo Building frontend because dist\index.html is missing...
    pushd "%FRONTEND%"
    call npm.cmd run build
    if errorlevel 1 (
        popd
        exit /b 1
    )
    popd
)

curl.exe -fsS "http://127.0.0.1:%BACKEND_PORT%/api/v1/health" >nul 2>&1
if not errorlevel 1 (
    echo Backend health is already available; keeping the existing process.
) else (
    netstat -ano | findstr /R /C:":%BACKEND_PORT% .*LISTENING" >nul
    if errorlevel 1 (
        echo Starting backend...
        cd /d "%ROOT%"
        start "Pan Hotel Watch Backend" /b "%PYTHON%" -m uvicorn app.main:app --app-dir "%BACKEND%" --host 0.0.0.0 --port %BACKEND_PORT% 1>>"%LOG_DIR%\backend.out.log" 2>>"%LOG_DIR%\backend.err.log"
    ) else (
        echo Backend port %BACKEND_PORT% is listening; waiting for its health endpoint.
    )
)

echo Waiting for backend health...
for /L %%N in (1,1,30) do (
    curl.exe -fsS "http://127.0.0.1:%BACKEND_PORT%/api/v1/health" >nul 2>&1
    if not errorlevel 1 goto backend_ready
    timeout /t 1 /nobreak >nul
)
echo ERROR: backend did not become healthy. Check .runtime\logs\backend.err.log
exit /b 1

:backend_ready
set "PYTHONPATH=%BACKEND%;%PYTHONPATH%"
set "WORKER_PID_FILE=%RUNTIME%\read-only-worker.pid"
set "WORKER_ALREADY_RUNNING=0"
set "WORKER_PID="
if exist "%WORKER_PID_FILE%" (
    set /p "WORKER_PID="<"%WORKER_PID_FILE%"
    if defined WORKER_PID (
        tasklist /FI "PID eq !WORKER_PID!" /NH 2>nul | findstr /R /C:"!WORKER_PID!" >nul
        if not errorlevel 1 set "WORKER_ALREADY_RUNNING=1"
    )
)
if "!WORKER_ALREADY_RUNNING!"=="1" (
    echo Read-only worker is already running with PID !WORKER_PID!; keeping the existing process.
) else (
    echo Starting read-only worker...
    start "Pan Hotel Watch Worker" /b "%PYTHON%" -m app.workers.read_only_scheduler 1>>"%LOG_DIR%\worker.out.log" 2>>"%LOG_DIR%\worker.err.log"
)

if /I "%FEISHU_AGENT_MODE%"=="builtin" (
    echo Starting built-in Feishu listener...
    start "Pan Hotel Watch Feishu Listener" /b "%PYTHON%" -m app.workers.feishu_listener 1>>"%LOG_DIR%\feishu-listener.out.log" 2>>"%LOG_DIR%\feishu-listener.err.log"
) else (
    echo Feishu natural-language entry is owned by Hermes Gateway; built-in listener skipped.
)

set "VITE_API_PROXY_TARGET=http://127.0.0.1:%BACKEND_PORT%"
netstat -ano | findstr /R /C:":%FRONTEND_PORT% .*LISTENING" >nul
if errorlevel 1 (
    echo Starting frontend...
    pushd "%FRONTEND%"
    start "Pan Hotel Watch Frontend" /b node "%VITE_CLI%" preview --host 0.0.0.0 --port %FRONTEND_PORT% 1>>"%LOG_DIR%\frontend.out.log" 2>>"%LOG_DIR%\frontend.err.log"
    popd
) else (
    echo Frontend port %FRONTEND_PORT% is already listening; keeping the existing process.
)

REM Hermes Gateway: auto-start when FEISHU_AGENT_MODE=hermes, unless --no-gateway is passed
set "START_GATEWAY=1"
if /I "%~1"=="--no-gateway" set "START_GATEWAY=0"
if /I "%~1"=="--start-gateway" set "START_GATEWAY=1"
if "!START_GATEWAY!"=="1" (
    if /I not "%FEISHU_AGENT_MODE%"=="builtin" (
        if exist "%HERMES_EXE%" (
            echo Starting Hermes Gateway profiles from the deployment manifest...
            powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%START_HERMES_GATEWAYS%" -ManifestPath "%FEISHU_PROFILES_MANIFEST%" -Replace
            if errorlevel 1 exit /b 1
        ) else (
            echo WARNING: Hermes executable not found; backend, worker and frontend are still running.
        )
    )
)

echo.
echo Startup commands completed.
echo Frontend: http://127.0.0.1:%FRONTEND_PORT%
echo Backend:  http://127.0.0.1:%BACKEND_PORT%/api/v1/health
echo Logs:     %LOG_DIR%
if /I not "%~1"=="--nopause" pause
exit /b 0
