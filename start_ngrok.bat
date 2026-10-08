@echo off
title Forensic AI - ngrok tunnel
cd /d "%~dp0"
if exist local_env.bat call local_env.bat
echo.
if "%NGROK_DOMAIN%"=="" (
  echo  No NGROK_DOMAIN set - you will get a random address that changes each time.
  echo  Look for the "Forwarding" line: https://xxxx.ngrok-free.app
  ngrok http 8000
) else (
  echo  Using your fixed address: https://%NGROK_DOMAIN%
  ngrok http --domain=%NGROK_DOMAIN% 8000
)
pause
