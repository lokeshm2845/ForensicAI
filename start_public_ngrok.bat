@echo off
cd /d "%~dp0"
start "Forensic AI - API" cmd /k start_api.bat
timeout /t 12 /nobreak >nul
start "Forensic AI - ngrok" cmd /k start_ngrok.bat
echo.
echo Two windows opened: API and ngrok. In the ngrok window find the "Forwarding" https address.
echo Then on your Vercel site: About - Connection - paste - Test - Save  (not needed if you set a fixed domain in Vercel).
pause
