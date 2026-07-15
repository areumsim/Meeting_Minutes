@echo off
chcp 65001 >nul
title Build Meeting Minutes EXE
cd /d "%~dp0..\.."

echo.
echo  ==============================================
echo    Meeting Minutes - EXE Build
echo  ==============================================
echo.

:: 1. 프론트엔드 빌드 (EXE 배포용이므로 항상 새로 빌드)
echo  [1/5] Building frontend...
cd web\frontend
call npm install
call npm run build
cd ..\..
if not exist "web\frontend\dist\index.html" (
    echo  [ERROR] Frontend build failed.
    pause
    exit /b 1
)

:: 2. PyInstaller 확인
python -m pyinstaller --version >nul 2>&1
if errorlevel 1 (
    echo  [2/5] Installing PyInstaller...
    python -m pip install pyinstaller
) else (
    echo  [2/5] PyInstaller found. OK
)

:: 3. EXE 빌드
echo  [3/5] Building EXE...
echo.
python -m PyInstaller scripts\build\build_exe.spec --noconfirm --clean
echo.

if not exist "dist\MeetingMinutes\MeetingMinutes.exe" (
    echo  [ERROR] Build failed. Check output above.
    pause
    exit /b 1
)

:: 4. 사용법.txt 복사 (배포 폴더에 사용 안내 포함)
echo  [4/5] Copying 사용법.txt...
if exist "scripts\build\사용법.txt" (
    copy /y "scripts\build\사용법.txt" "dist\MeetingMinutes\사용법.txt" >nul
) else (
    echo  [WARN] scripts\build\사용법.txt not found - skipping.
)

:: 5. 배포 zip 생성
echo  [5/5] Creating distribution zip...
if exist "dist\MeetingMinutes.zip" del /q "dist\MeetingMinutes.zip"
powershell -NoProfile -Command "Compress-Archive -Path 'dist\MeetingMinutes\*' -DestinationPath 'dist\MeetingMinutes.zip' -Force"

echo.
echo  ============================================
echo   BUILD SUCCESS!
echo   Folder: dist\MeetingMinutes\
echo   Zip:    dist\MeetingMinutes.zip
echo   Run:    dist\MeetingMinutes\MeetingMinutes.exe
echo  ============================================
echo.
echo  배포: dist\MeetingMinutes.zip 파일 하나만 전달하면 됩니다.
echo  설정(OpenAI 키, Obsidian 폴더)은 실행 후 웹 화면의 [설정]에서 입력합니다.
echo  (config.json 을 손으로 만들 필요 없음 - 첫 실행 시 자동 생성됩니다.)
echo.
pause
