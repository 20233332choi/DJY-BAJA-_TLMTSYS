@echo off
setlocal
cd /d "%~dp0"

echo Starting HTTPS tunnel for phone GPS...
echo.
powershell -ExecutionPolicy Bypass -File "%~dp0start_gps_https.ps1"
echo.
pause
