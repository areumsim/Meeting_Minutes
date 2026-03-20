@echo off
chcp 65001 >nul
title Build Meeting Minutes EXE
cd /d "%~dp0"

echo.
echo  ╔════════════════════════════════════════════╗
echo  ║  Meeting Minutes - EXE Build              ║
echo  ╚════════════════════════════════════════════╝
echo.

:: 1. 프론트엔드 빌드 확인
if not exist "web\frontend\dist\index.html" (
    echo  [1/3] Building frontend...
    cd web\frontend
    call npm install
    call npm run build
    cd ..\..
    if not exist "web\frontend\dist\index.html" (
        echo  [ERROR] Frontend build failed.
        pause
        exit /b 1
    )
) else (
    echo  [1/3] Frontend already built. OK
)

:: 2. PyInstaller 확인
python -m pyinstaller --version >nul 2>&1
if errorlevel 1 (
    echo  [2/3] Installing PyInstaller...
    python -m pip install pyinstaller
) else (
    echo  [2/3] PyInstaller found. OK
)

:: 3. EXE 빌드
echo  [3/3] Building EXE...
echo.
python -m PyInstaller build_exe.spec --noconfirm --clean
echo.

if exist "dist\MeetingMinutes\MeetingMinutes.exe" (
    echo  ════════════════════════════════════════════
    echo   BUILD SUCCESS!
    echo   Output: dist\MeetingMinutes\
    echo   Run:    dist\MeetingMinutes\MeetingMinutes.exe
    echo  ════════════════════════════════════════════
    echo.
    echo  To distribute, copy the entire dist\MeetingMinutes\ folder.
    echo  Place config.json next to MeetingMinutes.exe before running.
) else (
    echo  [ERROR] Build failed. Check output above.
)

echo.
pause
