@echo off
setlocal EnableExtensions
call "%~dp0common.bat" :init
set "SERVICE=%~1"
if "%SERVICE%"=="" set "SERVICE=all"

if /I "%SERVICE%"=="all" goto all_logs
if /I "%SERVICE%"=="backend" goto backend_logs
if /I "%SERVICE%"=="worker" goto worker_logs
if /I "%SERVICE%"=="frontend" goto frontend_logs
if /I "%SERVICE%"=="feishu-listener" goto feishu_logs
echo Usage: scripts\show-logs.bat [all^|backend^|worker^|frontend^|feishu-listener]
exit /b 1

:all_logs
call :print_log "backend.out.log"
call :print_log "backend.err.log"
call :print_log "worker.out.log"
call :print_log "worker.err.log"
call :print_log "frontend.out.log"
call :print_log "frontend.err.log"
call :print_log "feishu-listener.out.log"
call :print_log "feishu-listener.err.log"
goto done

:backend_logs
call :print_log "backend.out.log"
call :print_log "backend.err.log"
goto done

:worker_logs
call :print_log "worker.out.log"
call :print_log "worker.err.log"
goto done

:frontend_logs
call :print_log "frontend.out.log"
call :print_log "frontend.err.log"
goto done

:feishu_logs
call :print_log "feishu-listener.out.log"
call :print_log "feishu-listener.err.log"
goto done

:print_log
echo.
echo ===== %~1 =====
if exist "%LOG_DIR%\%~1" (type "%LOG_DIR%\%~1") else (echo Log file not found.)
exit /b 0

:done
if /I not "%~2"=="--nopause" if /I not "%~1"=="--nopause" pause
exit /b 0
