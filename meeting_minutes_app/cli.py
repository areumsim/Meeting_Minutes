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
  meeting-minutes init [--force]        # 최초 설정 마법사 (새 팀/새 설치용)
  meeting-minutes mcp-token [--name X]  # Wiki Graph MCP(Claude Cowork 연동) Bearer 토큰 발급
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
  # 주의: 아래 'prep'은 위의 'prep-brief'와 다른 별개 기능이다 —
  # prep-brief(wiki_knowledge.py)는 Vault 검색+Registry 기반 브리프 생성,
  # prep(meeting_assistant.py)은 planned 노트의 안건으로 사전 리서치를 갱신한다.
  meeting-minutes status|prep|process|schedule|merge [args]
  meeting-minutes obsidian|profiles|speaker-cache [args]
  meeting-minutes plan-watcher|auto-process [args]
  meeting-minutes audio-watcher|vault-indexer|wiki-ask [args]   # 폴더 감시는 audio-watcher 사용
  meeting-minutes legacy-watcher [args]           # 구형(deprecated) — 신규는 audio-watcher
  meeting-minutes prepare-local-stt [--status] [--model base]
      # 오프라인 최종 백업(faster-whisper) 가중치 미리 받기 (local-stt 로도 호출 가능).
      # 전사 중에는 절대 내려받지 않으므로 미리 준비해야 폴백 체인에 들어간다.
      # 웹 [설정]의 같은 버튼은 패키지 모드 전용 — 소스 실행에서는 이 명령을 쓴다.

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
        # deprecated: watcher.py(구형 감시자). 신규 코드/사용자는 audio-watcher(audio_watcher.py)를
        # 쓴다. 하위 호환을 위해 남겨둠 — 통합 시 회귀 위험이 있어 코드는 그대로 보존.
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
    if cmd == "init":
        from meeting_minutes_app.cli_init import run_init
        return run_init(rest)
    if cmd in ("mcp-token", "mcp_token"):
        from meeting_minutes_app.cli_init import run_mcp_token
        return run_mcp_token(rest)
    if cmd in ("prepare-local-stt", "prepare_local_stt", "local-stt", "local_stt"):
        from meeting_minutes_app.cli_init import run_prepare_local_stt
        return run_prepare_local_stt(rest)

    print(f"Unknown command: {cmd}\n")
    print(_usage())
    return 2


def _version() -> str:
    try:
        from importlib.metadata import version
        return f"meeting-minutes {version('meeting-minutes')}"
    except Exception:
        return "meeting-minutes (버전 정보 없음 — 개발 모드/미설치 실행)"


def _run_and_wait(argv: list[str]) -> None:
    """메뉴에서 한 작업을 실행하고, 결과를 화면에 남긴 채 Enter 입력을 기다린다.

    (성공/실패 여부와 무관하게 대기 — 실패했을 때만 멈추면 성공 시 결과가
    출력되자마자 창이 닫혀 사용자가 읽을 수 없다.)
    """
    dispatch(argv)
    input("\n계속하려면 Enter (메인 메뉴로 돌아갑니다)...")


def menu() -> int:
    while True:
        os.system("cls" if os.name == "nt" else "clear")
        print("Meeting Minutes - Unified Launcher")
        print("=" * 40)
        print("1. Realtime recording (실시간 회의 녹음 + 자동 회의록)")
        print("2. File/batch processing (녹음 파일 일괄 처리)")
        print("3. Ingest one audio file (오디오 파일 1개 수동 처리)")
        print("4. Obsidian embedded audio (옵시디언 노트에 첨부된 녹음 처리)")
        print("5. Watch audio folders (지정 폴더 자동 감시 처리)")
        print("6. Reindex Vault (옵시디언 볼트 검색 인덱스 재생성)")
        print("7. Ask Vault Wiki (볼트 지식 기반 질의응답)")
        print("8. Web UI (브라우저 대시보드 실행)")
        print("9. Prep brief (회의 준비 브리프)")
        print("10. Assistant status (일정/회의 현황 요약)")
        print("11. Schedule dashboard (일정 대시보드 갱신)")
        print("12. Merge pending recording (녹음-계획 매칭 병합 대기 처리)")
        print("13. Obsidian connection/path (옵시디언 연결/경로 진단)")
        print("0. Exit")
        print("   (고급 커맨드: meeting-minutes --help)")
        choice = input("\nSelect >> ").strip()

        if choice == "1":
            _run_and_wait(["realtime"]); continue
        if choice == "2":
            _run_and_wait(["batch"]); continue
        if choice == "3":
            path = input("Audio path >> ").strip().strip('"')
            if path:
                _run_and_wait(["ingest", path])
            continue
        if choice == "4":
            _run_and_wait(["vault-audio"]); continue
        if choice == "5":
            _run_and_wait(["watch"]); continue
        if choice == "6":
            _run_and_wait(["reindex"]); continue
        if choice == "7":
            q = input("Question >> ").strip()
            if q:
                _run_and_wait(["ask", q, "--show-sources"])
            continue
        if choice == "8":
            _run_and_wait(["web"]); continue
        if choice == "9":
            t = input("회의 제목 >> ").strip()
            if t:
                _run_and_wait(["prep-brief", "--title", t])
            continue
        if choice == "10":
            _run_and_wait(["status"]); continue
        if choice == "11":
            _run_and_wait(["schedule", "--write-dashboard"]); continue
        if choice == "12":
            _run_and_wait(["merge"]); continue
        if choice == "13":
            _run_and_wait(["obsidian", "--where"]); continue
        if choice in ("0", ""):
            return 0


def main(argv: list[str] | None = None) -> int:
    return dispatch(sys.argv[1:] if argv is None else argv)


if __name__ == "__main__":
    raise SystemExit(main())
