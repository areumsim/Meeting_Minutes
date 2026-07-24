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

:: 1.5. ffmpeg 번들 확인 — 없으면 exe 가 영상 포맷(mkv/avi/mov)·대용량 변환을 못 한다.
:: 배포용 빌드에서 조용히 빠지는 사고를 막기 위해 기본은 중단. (의도적으로 뺄 때만
:: SKIP_FFMPEG=1 로 실행)
if not exist "vendor\ffmpeg\ffmpeg.exe" (
    if "%SKIP_FFMPEG%"=="1" (
        echo  [WARN] vendor\ffmpeg\ffmpeg.exe 없음 - ffmpeg 없이 빌드합니다. ^(SKIP_FFMPEG=1^)
    ) else (
        echo  [ERROR] vendor\ffmpeg\ffmpeg.exe 가 없습니다.
        echo          배포 exe 에 ffmpeg 이 포함되지 않으면 영상 변환 기능이 동작하지 않습니다.
        echo          vendor\ffmpeg\README.txt 안내대로 ffmpeg.exe/ffprobe.exe 를 넣거나,
        echo          의도적으로 빼려면  set SKIP_FFMPEG=1  후 다시 실행하세요.
        pause
        exit /b 1
    )
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

:: (중요) 재빌드가 dist\MeetingMinutes 를 통째로 새로 만들면서 그 안의
:: MeetingMinutesData(사용자 설정·API키·회의록)까지 지워지는 것을 방지 —
:: 빌드 전에 옆으로 옮겨두고 빌드 후 복원한다.
set "MM_DATA_BAK=%TEMP%\MM_DATA_BAK_%RANDOM%"
if exist "dist\MeetingMinutes\MeetingMinutesData" (
    echo  기존 설정/데이터 백업: "%MM_DATA_BAK%"
    move "dist\MeetingMinutes\MeetingMinutesData" "%MM_DATA_BAK%" >nul
)

python -m PyInstaller scripts\build\build_exe.spec --noconfirm --clean
echo.

:: 사용자 데이터 복원 (빌드 성공/실패와 무관하게 되돌린다)
if exist "%MM_DATA_BAK%" (
    if exist "dist\MeetingMinutes" (
        echo  설정/데이터 복원 중...
        if exist "dist\MeetingMinutes\MeetingMinutesData" rmdir /s /q "dist\MeetingMinutes\MeetingMinutesData"
        move "%MM_DATA_BAK%" "dist\MeetingMinutes\MeetingMinutesData" >nul
    ) else (
        rmdir /s /q "%MM_DATA_BAK%"
    )
)

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

:: 5. 배포 zip 생성 (사용자 데이터/시크릿 제외)
:: (중요) dist\MeetingMinutes\MeetingMinutesData 에는 개발 PC의 실제 API 키·이메일
:: 앱 비밀번호·회의록이 들어 있다. 이걸 그대로 zip 하면 배포본에 시크릿이 유출된다.
:: → 임시 스테이징으로 복사한 뒤 MeetingMinutesData 를 제거하고 zip 한다.
::   (첫 실행 시 config.example.json 에서 깨끗한 config.json 이 자동 생성됨)
echo  [5/5] Creating distribution zip (excluding user data/secrets)...
if exist "dist\MeetingMinutes.zip" del /q "dist\MeetingMinutes.zip"
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; $stage = Join-Path $env:TEMP ('MM_STAGE_' + [System.IO.Path]::GetRandomFileName()); Copy-Item 'dist\MeetingMinutes' $stage -Recurse -Force; $data = Join-Path $stage 'MeetingMinutesData'; if (Test-Path $data) { Remove-Item $data -Recurse -Force }; Compress-Archive -Path (Join-Path $stage '*') -DestinationPath 'dist\MeetingMinutes.zip' -Force; Remove-Item $stage -Recurse -Force"
if not exist "dist\MeetingMinutes.zip" (
    echo  [ERROR] Zip creation failed.
    pause
    exit /b 1
)

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
