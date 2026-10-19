@echo off
setlocal EnableExtensions
call "%~dp0common.bat" :init

set "TASK_NAME=PanHotelWatch"
set "TASK_TRIGGER=ONLOGON"
if /I "%~1"=="--startup" set "TASK_TRIGGER=ONSTART"
set "TASK_RUN=\"%ROOT%\scripts\start-stack.bat\" --nopause"

echo Registering %TASK_NAME% with trigger %TASK_TRIGGER%...
schtasks /Create /TN "%TASK_NAME%" /TR "%TASK_RUN%" /SC %TASK_TRIGGER% /RL LIMITED /F
if errorlevel 1 (
    echo Failed. Run this file from an elevated Command Prompt if required.
    exit /b 1
)
echo Startup task registered. Default trigger is user logon; use --startup only for boot trigger.
if /I not "%~2"=="--nopause" pause
exit /b 0
