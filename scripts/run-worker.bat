@echo off
setlocal EnableExtensions
call "%~dp0common.bat" :init
if not exist "%PYTHON%" (
    echo ERROR: backend virtual environment not found. Run scripts\install.bat first.
    exit /b 1
)
set "PYTHONPATH=%BACKEND%;%PYTHONPATH%"
set "WORKER_MODULE=app.workers.read_only_scheduler"
if /I "%~1"=="feishu-listener" set "WORKER_MODULE=app.workers.feishu_listener"
pushd "%ROOT%"
call "%PYTHON%" -m %WORKER_MODULE%
set "EXIT_CODE=%ERRORLEVEL%"
popd
exit /b %EXIT_CODE%
