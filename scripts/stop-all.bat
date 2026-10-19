@echo off
setlocal EnableExtensions EnableDelayedExpansion
call "%~dp0common.bat" :init

echo Stopping managed HTTP services...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%BACKEND_PORT% .*LISTENING" /C:":%FRONTEND_PORT% .*LISTENING"') do (
    taskkill /PID %%P /T /F >nul 2>&1
)

REM V6 Step 2: the distill loop runs INSIDE the read-only scheduler worker
REM (app.workers.read_only_scheduler), so the pid-file kill below plus the
REM wmic commandline sweep already stop it together with EBK/competitor polls.
set "WORKER_PID_FILE=%RUNTIME%\read-only-worker.pid"
set "WORKER_PID="
if exist "%WORKER_PID_FILE%" set /p "WORKER_PID="<"%WORKER_PID_FILE%"
if defined WORKER_PID (
    echo Stopping read-only worker PID !WORKER_PID!...
    taskkill /PID !WORKER_PID! /T /F >nul 2>&1
    del /Q /F "%WORKER_PID_FILE%" >nul 2>&1
)

where wmic >nul 2>&1
if not errorlevel 1 (
    for /f "skip=1 tokens=1" %%P in ('wmic process where "CommandLine like '%%app.workers.read_only_scheduler%%'" get ProcessId 2^>nul') do if not "%%P"=="" taskkill /PID %%P /T /F >nul 2>&1
    for /f "skip=1 tokens=1" %%P in ('wmic process where "CommandLine like '%%app.workers.feishu_listener%%'" get ProcessId 2^>nul') do if not "%%P"=="" taskkill /PID %%P /T /F >nul 2>&1
) else (
    echo WARNING: wmic is unavailable; stop the worker manually with scripts\run-worker.bat window.
)

REM Stop Hermes Gateway processes
echo Stopping Hermes Gateway processes...
taskkill /F /IM hermes.exe >nul 2>&1

REM Kill orphaned pythonw.exe processes from Hermes venv (left by old start methods)
for /f "skip=1 tokens=1" %%P in ('wmic process where "name='pythonw.exe' and CommandLine like '%%hermes%%'" get ProcessId 2^>nul') do if not "%%P"=="" taskkill /PID %%P /T /F >nul 2>&1

echo Stop command completed.
if /I not "%~1"=="--nopause" pause
exit /b 0
