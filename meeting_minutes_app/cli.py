"""Unified CLI dispatcher for meeting workflows.

Backs both the pip-installed `meeting-minutes` console script
(see pyproject.toml [project.scripts]) and the root-level run_meeting.py
shim kept for backward compatibility with existing .bat launchers/docs.

Each subcommand is still run as its own `python -m <module>` subprocess
rather than an in-process call — most of these modules are interactive
terminal UIs (input() prompts) or have their own argparse-based CLI with
process-level concerns (sys.exit, subprocess chaining), so keeping them
as isolated subprocesses is the least risky way to expose them through a
single entrypoint.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    if getattr(_s, "encoding", None) and _s.encoding.lower() in ("cp949", "euc-kr", "ansi"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

BASE_DIR = Path(__file__).resolve().parent.parent  # repo root

# meeting_minutes_app/*.py 스크립트는 common/wiki_core/meeting_pipeline 서브패키지로
# 나뉘어 있다 (내부적으로 절대 import를 쓰므로 `python <path>` 직접 실행이 아니라
# `python -m <dotted.module>` 형태로 실행해야 meeting_minutes_app 패키지가 resolve된다).
_MODULE_MAP = {
    "meeting_minutes.py":        "meeting_minutes_app.meeting_pipeline.meeting_minutes",
    "run_realtime.py":           "meeting_minutes_app.meeting_pipeline.run_realtime",
    "run_batch.py":              "meeting_minutes_app.meeting_pipeline.run_batch",
    "run_ui.py":                 "meeting_minutes_app.meeting_pipeline.run_ui",
    "meeting_assistant.py":      "meeting_minutes_app.meeting_pipeline.meeting_assistant",
    "plan_watcher.py":           "meeting_minutes_app.meeting_pipeline.plan_watcher",
    "auto_process_vault.py":     "meeting_minutes_app.meeting_pipeline.auto_process_vault",
    "profiles.py":               "meeting_minutes_app.meeting_pipeline.profiles",
    "speaker_cache.py":          "meeting_minutes_app.meeting_pipeline.speaker_cache",
    "audio_watcher.py":          "meeting_minutes_app.meeting_pipeline.audio_watcher",
    "watcher.py":                "meeting_minutes_app.meeting_pipeline.watcher",
    "realtime_transcription.py": "meeting_minutes_app.meeting_pipeline.realtime_transcription",
    "obsidian.py":                "meeting_minutes_app.wiki_core.obsidian",
    "vault_indexer.py":           "meeting_minutes_app.wiki_core.vault_indexer",
    "wiki_ask.py":                "meeting_minutes_app.wiki_core.wiki_ask",
    "wiki_knowledge.py":          "meeting_minutes_app.wiki_core.wiki_knowledge",
    "notifier.py":                "meeting_minutes_app.common.notifier",
}


def _run(args: list[str]) -> int:
    print("\n> " + subprocess.list2cmdline(args))
    try:
        return subprocess.call(args, cwd=str(BASE_DIR))
    except KeyboardInterrupt:
        return 130


def _py(script: str, *args: str) -> int:
    module = _MODULE_MAP.get(script)
    if module:
        return _run([sys.executable, "-m", module, *args])
    script_path = BASE_DIR / "meeting_minutes_app" / script
    if not script_path.exists():
        script_path = BASE_DIR / script
    return _run([sys.executable, str(script_path), *args])


def _usage() -> str:
    return """\
Usage:
  meeting-minutes                       # interactive menu
  meeting-minutes realtime [args]       # record|realtime — 실시간 마이크 녹음
  meeting-minutes batch [files...]      # file|files    — 파일 일괄 처리
      batch 주요 옵션: --resume(STT 재사용, 없으면 중단) / --force-stt(새 STT 강제)
  meeting-minutes ingest <audio> [args]           # 단일 파일 → Obsidian
  meeting-minutes vault-audio [args]              # Obsidian 임베드 오디오
  meeting-minutes watch [args]                    # 폴더 감시 자동 처리
  meeting-minutes reindex [args]                  # TF-IDF 인덱스 재빌드
  meeting-minutes ask <question> [args]           # Vault 질의
  meeting-minutes web [args]                      # Web UI 실행 (ui|web)
  meeting-minutes prep-brief --title "제목" [--topic "주제"]  # 회의 준비 브리프 생성

Advanced:
  meeting-minutes status|prep|process|schedule|merge [args]
  meeting-minutes obsidian|profiles|speaker-cache [args]
  meeting-minutes plan-watcher|auto-process [args]
  meeting-minutes audio-watcher|vault-indexer|wiki-ask [args]
  meeting-minutes legacy-watcher [args]           # 구형 파일 감시자

(레거시: `python run_meeting.py ...` 형태로도 동일하게 동작합니다.)
"""


def dispatch(argv: list[str]) -> int:
    if not argv:
        return menu()

    cmd, rest = argv[0].lower(), argv[1:]
    if cmd in ("-h", "--help", "help"):
        print(_usage())
        return 0
    if cmd in ("--version", "version"):
        print(_version())
        return 0
    if cmd in ("realtime", "record"):
        if rest and rest[0].lower() in ("-h", "--help", "help"):
            return _py("run_realtime.py", "--help")
        return _py("run_realtime.py", *rest)
    if cmd in ("batch", "file", "files"):
        if rest and rest[0].lower() in ("-h", "--help", "help"):
            return _py("meeting_minutes.py", "--help")
        if rest:
            return _py("meeting_minutes.py", *rest)
        return _py("run_batch.py")
    if cmd == "ingest":
        return _py("meeting_assistant.py", "ingest", *rest)
    if cmd in ("vault-audio", "vault_audio"):
        return _py("meeting_assistant.py", "vault-audio", *rest)
    if cmd == "watch":
        return _py("meeting_assistant.py", "watch", *rest)
    if cmd == "reindex":
        return _py("meeting_assistant.py", "reindex", *rest)
    if cmd == "ask":
        return _py("meeting_assistant.py", "ask", *rest)
    if cmd in ("status", "prep", "process", "schedule", "merge", "people"):
        return _py("meeting_assistant.py", cmd, *rest)
    if cmd in ("plan-watcher", "plan_watcher"):
        return _py("plan_watcher.py", *rest)
    if cmd in ("auto-process", "auto_process"):
        return _py("auto_process_vault.py", *rest)
    if cmd == "obsidian":
        return _py("obsidian.py", *rest)
    if cmd == "profiles":
        return _py("profiles.py", *rest)
    if cmd in ("speaker-cache", "speaker_cache"):
        return _py("speaker_cache.py", *rest)
    if cmd == "notifier":
        return _py("notifier.py", *rest)
    if cmd in ("audio-watcher", "audio_watcher"):
        return _py("audio_watcher.py", *rest)
    if cmd in ("legacy-watcher", "legacy_watcher"):
        return _py("watcher.py", *rest)
    if cmd in ("vault-indexer", "vault_indexer"):
        return _py("vault_indexer.py", *rest)
    if cmd in ("wiki-ask", "wiki_ask"):
        return _py("wiki_ask.py", *rest)
    if cmd in ("meeting-minutes", "meeting_minutes"):
        return _py("meeting_minutes.py", *rest)
    if cmd in ("realtime-raw", "realtime_transcription"):
        return _py("realtime_transcription.py", *rest)
    if cmd in ("prep-brief", "prep_brief"):
        return _py("wiki_knowledge.py", *rest)
    if cmd in ("web", "ui"):
        return _py("run_ui.py", *rest)
    if cmd == "assistant":
        return _py("meeting_assistant.py", *rest)

    print(f"Unknown command: {cmd}\n")
    print(_usage())
    return 2


def _version() -> str:
    try:
        from importlib.metadata import version
        return f"meeting-minutes {version('meeting-minutes')}"
    except Exception:
        return "meeting-minutes (버전 정보 없음 — 개발 모드/미설치 실행)"


def menu() -> int:
    while True:
        os.system("cls" if os.name == "nt" else "clear")
        print("Meeting Minutes - Unified Launcher")
        print("=" * 40)
        print("1. Realtime recording")
        print("2. File/batch processing")
        print("3. Ingest one audio file")
        print("4. Obsidian embedded audio")
        print("5. Watch audio folders")
        print("6. Reindex Vault")
        print("7. Ask Vault Wiki")
        print("8. Web UI")
        print("9. Prep brief (회의 준비 브리프)")
        print("10. Assistant status")
        print("11. Schedule dashboard")
        print("12. Merge pending recording")
        print("13. Obsidian connection/path")
        print("0. Exit")
        print("   (고급 커맨드: meeting-minutes --help)")
        choice = input("\nSelect >> ").strip()

        if choice == "1":
            return dispatch(["realtime"])
        if choice == "2":
            return dispatch(["batch"])
        if choice == "3":
            path = input("Audio path >> ").strip().strip('"')
            if path:
                return dispatch(["ingest", path])
            continue
        if choice == "4":
            return dispatch(["vault-audio"])
        if choice == "5":
            return dispatch(["watch"])
        if choice == "6":
            return dispatch(["reindex"])
        if choice == "7":
            q = input("Question >> ").strip()
            if q:
                return dispatch(["ask", q, "--show-sources"])
            continue
        if choice == "8":
            return dispatch(["web"])
        if choice == "9":
            t = input("회의 제목 >> ").strip()
            if t:
                return dispatch(["prep-brief", "--title", t])
            continue
        if choice == "10":
            return dispatch(["status"])
        if choice == "11":
            return dispatch(["schedule", "--write-dashboard"])
        if choice == "12":
            return dispatch(["merge"])
        if choice == "13":
            return dispatch(["obsidian", "--where"])
        if choice in ("0", ""):
            return 0


def main(argv: list[str] | None = None) -> int:
    return dispatch(sys.argv[1:] if argv is None else argv)


if __name__ == "__main__":
    raise SystemExit(main())
