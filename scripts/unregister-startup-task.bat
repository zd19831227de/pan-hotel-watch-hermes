@echo off
setlocal EnableExtensions
set "TASK_NAME=PanHotelWatch"
schtasks /Delete /TN "%TASK_NAME%" /F
if errorlevel 1 echo Task was not found or could not be removed.
if /I not "%~1"=="--nopause" pause
exit /b 0
