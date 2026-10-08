@echo off
title Forensic AI - public tunnel
echo.
echo  Starting the tunnel. Look for the line that says https://....trycloudflare.com
echo  and wait for "Registered tunnel connection" (no red errors). Copy that address.
echo.
cloudflared tunnel --url http://127.0.0.1:8000
pause
