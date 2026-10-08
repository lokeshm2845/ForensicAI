@echo off
title Forensic AI - API (port 8000)
cd /d "%~dp0"
rem Optional secrets live in local_env.bat (NOT uploaded to GitHub). See local_env.example.bat
if exist local_env.bat call local_env.bat
if "%CORS_ORIGINS%"=="" set CORS_ORIGINS=*
if "%STORE_FILENAMES%"=="" set STORE_FILENAMES=0
echo.
echo  Forensic AI API starting...  open http://127.0.0.1:8000 for the full app on this PC
echo  (keep this window open; Ctrl+C stops it)
echo.
py -m uvicorn server.main:app --host 127.0.0.1 --port 8000
pause
