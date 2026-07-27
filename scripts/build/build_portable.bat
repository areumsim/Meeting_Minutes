@echo off
chcp 65001 >nul
title Build Meeting Minutes (Portable)
:: build_portable.bat — 포터블 배포본 조립 래퍼
:: 실제 로직은 build_portable.ps1 에 있다. 더블클릭/실행 편의를 위한 얇은 래퍼.
:: (ffmpeg 없이 빌드하려면  set SKIP_FFMPEG=1  후 실행)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_portable.ps1"
echo.
pause
