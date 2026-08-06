@echo off
rem MeetingMinutes.bat - starts the web server in the BACKGROUND (no window).
rem "start" detaches pythonw.exe, so this console flashes briefly then closes
rem while the server keeps running.
rem (The old MeetingMinutes.vbs was blocked by corporate Windows Script Host
rem  policy - "access denied"; a .bat is not affected by that.)
rem To see logs for troubleshooting, run "Troubleshoot.bat" instead.
rem One instance at a time: starting this stops a Meeting Minutes server that is
rem already running (any launcher) and takes over port 8501, so the address never
rem changes. A server with a meeting in progress is NOT stopped - its window is
rem opened instead. See server_launch.stop_other_instances.
rem NOTE: keep this file ASCII/English only. Korean text here breaks cmd parsing.

cd /d "%~dp0"

rem Config, meeting notes, DB and logs are kept next to this launcher, in
rem MeetingMinutesData\ (app_paths.get_base_dir reads this env var first).
set "MM_DATA_DIR=%~dp0MeetingMinutesData"

if not exist "%~dp0python-embed\pythonw.exe" (
    echo [ERROR] python-embed\pythonw.exe not found.
    echo         The package may be damaged - please re-extract the whole zip.
    pause
    exit /b 1
)

rem start "" runs the server as a separate process, so this bat exits at once
rem (window closes) and the pythonw server keeps running in the background.
start "" "%~dp0python-embed\pythonw.exe" "%~dp0app\meeting_minutes_app\meeting_pipeline\run_ui_exe.py"
