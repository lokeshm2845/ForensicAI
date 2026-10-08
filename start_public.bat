@echo off
cd /d "%~dp0"
start "Forensic AI - API" cmd /k start_api.bat
timeout /t 12 /nobreak >nul
start "Forensic AI - tunnel" cmd /k start_tunnel.bat
echo.
echo Two windows opened: API and tunnel. Copy the https://....trycloudflare.com address from the tunnel window,
echo then on your Vercel site: About - Connection - paste - Test - Save.
pause
