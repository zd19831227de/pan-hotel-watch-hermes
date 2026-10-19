@echo off
if /I "%~1"==":init" goto init
exit /b 0

:init
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "ROOT=%%~fI"
set "BACKEND=%ROOT%\backend"
set "FRONTEND=%ROOT%\frontend"
set "RUNTIME=%ROOT%\.runtime"
set "LOG_DIR=%RUNTIME%\logs"
set "PYTHON=%ROOT%\.venv\Scripts\python.exe"
set "HERMES_PYTHON=%ROOT%\pan-hotel-hermes\.venv\Scripts\python.exe"
set "VITE_CLI=%FRONTEND%\node_modules\vite\bin\vite.js"
set "BACKEND_PORT=8100"
set "FRONTEND_PORT=5173"
if defined PAN_HOTEL_WATCH_BACKEND_PORT set "BACKEND_PORT=%PAN_HOTEL_WATCH_BACKEND_PORT%"
if defined BACKEND_PORT_OVERRIDE set "BACKEND_PORT=%BACKEND_PORT_OVERRIDE%"
if defined FRONTEND_PORT_OVERRIDE set "FRONTEND_PORT=%FRONTEND_PORT_OVERRIDE%"
set "FEISHU_AGENT_MODE=hermes"

if exist "%ROOT%\.env" (
    for /f "usebackq tokens=1,* delims==" %%A in ("%ROOT%\.env") do (
        if /I "%%A"=="PAN_HOTEL_WATCH_BACKEND_PORT" set "BACKEND_PORT=%%B"
        if /I "%%A"=="BACKEND_PORT" set "BACKEND_PORT=%%B"
        if /I "%%A"=="FRONTEND_PORT" set "FRONTEND_PORT=%%B"
        if /I "%%A"=="FEISHU_AGENT_MODE" set "FEISHU_AGENT_MODE=%%B"
    )
)

set "VITE_API_PROXY_TARGET=http://127.0.0.1:%BACKEND_PORT%"
if not exist "%RUNTIME%" mkdir "%RUNTIME%"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
exit /b 0
