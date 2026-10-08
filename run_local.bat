@echo off
cd /d "%~dp0"
echo Forensic AI web app  ->  http://127.0.0.1:8000   (Ctrl+C to stop)
py -m uvicorn server.main:app --host 127.0.0.1 --port 8000
pause
