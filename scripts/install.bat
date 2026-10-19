@echo off
setlocal EnableExtensions
call "%~dp0common.bat" :init

echo ===============================================
echo Pan Hotel Watch - Windows dependency installer
echo ===============================================

where py >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python launcher py.exe was not found.
    echo Install Python 3.11 or newer and enable the PATH option.
    goto failed
)
where npm.cmd >nul 2>&1
if errorlevel 1 (
    echo ERROR: npm.cmd was not found.
    echo Install Node.js 20 or newer and enable the PATH option.
    goto failed
)

if not exist "%ROOT%\.env" (
    if exist "%ROOT%\.env.example" (
        copy /Y "%ROOT%\.env.example" "%ROOT%\.env" >nul
        echo Created .env from .env.example. Fill required credentials before live reads.
    ) else (
        echo WARNING: .env.example was not found.
    )
)

if not exist "%PYTHON%" (
    echo Creating backend virtual environment...
    py -3 -m venv "%ROOT%\.venv"
    if errorlevel 1 goto failed
)
echo Installing backend dependencies...
call "%PYTHON%" -m pip install --upgrade pip
if errorlevel 1 goto failed
call "%PYTHON%" -m pip install -r "%BACKEND%\requirements.txt"
if errorlevel 1 goto failed
call "%PYTHON%" -m playwright install chromium
if errorlevel 1 goto failed

if not exist "%HERMES_PYTHON%" (
    echo Creating Hermes virtual environment...
    call "%PYTHON%" -m venv "%ROOT%\pan-hotel-hermes\.venv"
    if errorlevel 1 goto failed
)
echo Installing Hermes dependencies...
call "%HERMES_PYTHON%" -m pip install --upgrade pip
if errorlevel 1 goto failed
call "%HERMES_PYTHON%" -m pip install -e "%ROOT%\pan-hotel-hermes"
if errorlevel 1 goto failed

echo Installing frontend dependencies and building the WebUI...
pushd "%FRONTEND%"
call npm.cmd install
if errorlevel 1 (
    popd
    goto failed
)
call npm.cmd run build
if errorlevel 1 (
    popd
    goto failed
)
popd

echo.
echo Install complete.
echo Configure .env, then run scripts\start-all.bat
if /I not "%~1"=="--nopause" pause
exit /b 0

:failed
echo.
echo Install failed. Review the error above and run this file again after fixing it.
if /I not "%~1"=="--nopause" pause
exit /b 1
