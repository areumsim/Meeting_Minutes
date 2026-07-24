@echo off
chcp 65001 >nul
title Meeting Minutes - Web UI
rem ============================================================
rem  루트 바로가기 런처 — 웹 UI를 http://localhost:8501 에 띄웁니다.
rem  (내부적으로 scripts\windows\run_ui.bat 을 그대로 실행)
rem
rem  사용법: 이 파일을 더블클릭하면 서버가 뜨고 브라우저가 열립니다.
rem  주의:   이 창을 닫거나 Ctrl+C 하면 서버도 함께 종료됩니다.
rem          (창 없이 계속 띄우려면 빌드된 MeetingMinutes.exe 를 쓰세요.)
rem ============================================================
call "%~dp0scripts\windows\run_ui.bat" %*
