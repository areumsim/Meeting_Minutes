@echo off
chcp 65001 >nul
cd /d "%~dp0"
REM ============================================================
REM  볼트 녹음 자동 처리를 Windows 작업 스케줄러에 등록
REM  - 작업 이름 : MeetingMinutes_AutoProcess
REM  - 기본 주기 : 매 1시간 (새 녹음 없으면 그냥 스킵하므로 비용 없음)
REM  - 이 파일을 한 번만 더블클릭하면 등록 완료.
REM ============================================================
set TASK=MeetingMinutes_AutoProcess

schtasks /create /tn "%TASK%" /tr "\"%~dp0run_auto_process.bat\"" /sc HOURLY /mo 1 /st 08:00 /f

if %errorlevel%==0 (
  echo.
  echo [완료] '%TASK%' 등록됨 - 매시간 자동 실행됩니다.
  echo.
  echo  지금 즉시 한 번 실행 : schtasks /run /tn "%TASK%"
  echo  주기 바꾸기(예 매일 18시) : schtasks /create /tn "%TASK%" /tr "\"%~dp0run_auto_process.bat\"" /sc DAILY /st 18:00 /f
  echo  해제                  : schtasks /delete /tn "%TASK%" /f
) else (
  echo.
  echo [실패] 등록 실패. 이 파일을 마우스 우클릭 - "관리자 권한으로 실행" 으로 다시 시도하거나,
  echo        작업 스케줄러(taskschd.msc) GUI에서 run_auto_process.bat 를 수동 등록하세요.
)
echo.
pause
