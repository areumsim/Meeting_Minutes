@echo off
chcp 65001 >nul
title Meeting Minutes - Web UI
rem ============================================================
rem  루트 바로가기 런처 — 웹 UI를 http://localhost:8501 에 띄웁니다.
rem  (8501이 이미 쓰이는 중이면 빈 포트로 옮기고 콘솔에 그 주소를 알려줍니다.
rem   브라우저는 서버가 응답한 뒤 자동으로 열립니다.)
rem  데이터(설정·회의록)는 이 저장소 폴더를 씁니다 — 포터블 배포본(MeetingMinutes.bat)은
rem  자기 폴더의 MeetingMinutesData 를 쓰므로 설정이 서로 별개입니다.
rem  (내부적으로 scripts\windows\run_ui.bat 을 그대로 실행)
rem
rem  사용법: 이 파일을 더블클릭하면 서버가 뜨고 브라우저가 열립니다.
rem          앞서 떠 있던 인스턴스(이 런처든 포터블이든)는 **자동으로 종료**되고 이 창이
rem          8501 을 넘겨받습니다. 단 진행 중인 회의가 있으면 끄지 않고 그 창을 열어 줍니다.
rem  주의:   Ctrl+C 로 끝내면 서버도 함께 종료됩니다. 그러나 **창을 X 로 닫으면 서버가
rem          남을 수 있습니다**(콘솔 종료가 손자 프로세스까지 확실히 죽이지 못합니다).
rem          확실히 끄려면 화면의 [설정] → [앱 종료] 를 쓰거나, 그냥 이 런처를 다시
rem          실행하세요(위의 자동 종료가 남은 서버를 정리합니다).
rem          (창 없이 띄우려면 포터블 배포본의 MeetingMinutes.bat 을 쓰세요 —
rem           구형 MeetingMinutes.exe 는 폐기됐고 dist 에 더 이상 만들지 않습니다.)
rem  전제:   Python 과 Node.js(npm)가 설치돼 있어야 합니다. 웹 화면 번들
rem          (web\frontend\dist, git 에 올리지 않음)은 이 런처가 **자동으로** 만듭니다 —
rem          run_ui.py 가 기동 전에 npm install + npm run build 를 돌리고(소스가 더
rem          새로우면 다시 빌드), 실패하면 멈춥니다. 수동 빌드는 필요 없습니다.
rem ============================================================
call "%~dp0scripts\windows\run_ui.bat" %*
