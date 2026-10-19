@echo off
setlocal EnableExtensions
call "%~dp0common.bat" :init
if not exist "%PYTHON%" (
    echo ERROR: backend virtual environment not found. Run scripts\install.bat first.
    exit /b 1
)
pushd "%ROOT%"
call "%PYTHON%" -m uvicorn app.main:app --app-dir "%BACKEND%" --host 0.0.0.0 --port %BACKEND_PORT%
set "EXIT_CODE=%ERRORLEVEL%"
popd
exit /b %EXIT_CODE%
