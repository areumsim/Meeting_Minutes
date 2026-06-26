@echo off
chcp 65001 >nul
title Vault 인덱스 재빌드 (Reindex)
cd /d "%~dp0"
REM Obsidian Vault 의 노트를 TF-IDF 인덱싱 (관련 노트 검색·Wiki Q&A 에 사용)
REM 볼트 경로는 config.json 의 obsidian.vault_path 또는 indexing.vault_path 에서 읽음
echo.
echo  [Reindex] Vault 인덱스 빌드 중...
echo.
python meeting_assistant.py reindex %*
if errorlevel 1 (
    echo.
    echo  [ERROR] reindex 실패. config.json 의 obsidian.vault_path 를 확인하세요.
    echo.
    pause
)
