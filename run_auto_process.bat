@echo off
chcp 65001 >nul
title 볼트 녹음 자동 처리 (auto-process)
cd /d "%~dp0"
REM 볼트의 새 녹음을 찾아 전체 처리(STT->회의록->요약->액션->Obsidian 발행->메일).
REM 이미 처리한 파일은 자동으로 건너뜀. 작업 스케줄러가 이 파일을 주기 실행한다.
python auto_process_vault.py
