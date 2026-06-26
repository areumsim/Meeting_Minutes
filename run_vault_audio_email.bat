@echo off
chcp 65001 >nul
title 옵시디언 녹음 → 요약 → 메일 (vault-audio)
cd /d "%~dp0"
REM 옵시디언 볼트의 새 녹음(노트에 임베드된 오디오)을 처리하고 요약본을 메일로 발송
REM   - STT → 회의록/요약/액션 생성 → 해당 노트에 '## 회의 기록' 병합
REM   - config.json email 섹션으로 메일 발송 (Gmail/Naver/Outlook 자동 인식)
REM   - 이미 처리한 녹음은 frontmatter audio_processed 로 건너뜀(중복 방지)
REM   - 볼트 경로는 config.json 의 obsidian.vault_path 에서 읽음
echo.
python meeting_assistant.py vault-audio --notify email %*
if errorlevel 1 (
    echo.
    echo  [ERROR] vault-audio 실패. config.json 의 obsidian.vault_path 와 email 섹션을 확인하세요.
    echo.
    pause
)
