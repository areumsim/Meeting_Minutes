@echo off
chcp 65001 >nul
title 오디오 폴더 감시 (Watch)
cd /d "%~dp0"
REM 지정 폴더를 감시하다가 새 오디오 파일이 생기면 자동으로 처리
REM 감시 폴더는 config.json 의 vault_watcher.watch_folders 에서 읽음
REM Ctrl+C 로 중지
echo.
echo  [Watch] 오디오 폴더 감시 시작... (Ctrl+C 로 중지)
echo.
python meeting_assistant.py watch %*
if errorlevel 1 (
    echo.
    echo  [ERROR] 감시 중 오류. config.json 의 vault_watcher.watch_folders 를 확인하세요.
    echo.
    pause
)
