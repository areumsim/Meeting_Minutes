"""
meeting_assistant.py — 통합 회의 비서 (하나의 진입점)
========================================================
회의 생애주기 전체를 한 명령으로 묶는다. 내부적으로 기존 모듈을 그대로 호출만 한다.

  회의 전   : prep      계획(planned) 노트의 안건으로 사전 리서치 자동 작성
  회의 중   : record    실시간 녹취 시작(run_realtime.py)
  회의 후   : process   녹음/영상 파일 → STT·회의록·요약·액션·이메일 + 계획 매칭/병합
  이후 정리 : schedule  일정 정리·충돌/이중예약 점검·대시보드 갱신
              merge     '병합 대기'(녹음↔계획) 확인 후 병합
              status    한눈에 보는 현재 상태

공통 로직(계획 매칭·사전자료 참고·Obsidian 발행)은 batch/realtime/web 모두 동일.
Cowork에서 "오늘 회의 준비해줘 / 회의 끝났어 정리해줘 / 겹치는 일정 있어?" 처럼
말로 시키면 이 명령들을 대신 실행한다.

예:
  python run_meeting.py status   --vault "D:\\Claude\\QC"
  python run_meeting.py prep      --vault "D:\\Claude\\QC"
  python run_meeting.py schedule  --vault "D:\\Claude\\QC" --write-dashboard
  python run_meeting.py process   회의녹음.m4a --title "주간보고" --type meeting
  python run_meeting.py merge      --vault "D:\\Claude\\QC"
  python run_meeting.py record    --type meeting --topic "주간보고"
"""

from __future__ import annotations

import os
import sys
import argparse
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent

for _stream in (sys.stdout, sys.stderr):
    if getattr(_stream, "encoding", None) and _stream.encoding.lower() in ("cp949", "euc-kr", "ansi"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

try:
    import config_loader as _cfg
    def _c(k, d=None): return _cfg.get(k, d)
except Exception:
    def _c(k, d=None): return d


def _vault(args) -> str:
    v = getattr(args, "vault", "") or _c("obsidian.vault_path", "")
    if not v:
        print("[assistant] 볼트 경로 필요: --vault \"<폴더>\" 또는 config.obsidian.vault_path")
        sys.exit(2)
    return v


# ── 회의 전: 사전 리서치 ──────────────────────────────────────
def cmd_prep(args):
    import plan_watcher as pw
    vault = _vault(args)
    root = Path(vault) / args.notes_subdir
    if not root.is_dir():
        root = Path(vault)
    llm, obs = pw._build_clients()
    if llm is None:
        print("[prep] LLM 초기화 실패 → 중단"); sys.exit(2)
    targets = [Path(args.note)] if args.note else sorted(root.rglob("*.md"))
    n = 0
    for f in targets:
        if f.name.startswith("_"):
            continue
        if pw._process_file(f, llm, obs):
            n += 1
    if obs:
        obs.close()
    print(f"[prep] 사전 리서치 갱신 {n}건")


# ── 회의 중: 실시간 녹취 ──────────────────────────────────────
def cmd_record(args):
    cmd = [sys.executable, str(HERE / "run_realtime.py")]
    if args.type:
        cmd += ["--type", args.type]
    if args.topic:
        cmd += ["--topic", args.topic]
    cmd += args.extra
    print(f"[record] 실시간 녹취 시작: {' '.join(cmd)}")
    raise SystemExit(subprocess.call(cmd))


# ── 회의 후: 파일 처리(STT→회의록→요약→액션→이메일→계획 매칭/병합) ──
def cmd_process(args):
    cmd = [sys.executable, str(HERE / "meeting_minutes.py"), args.file]
    if args.title:
        cmd += ["--title", args.title]
    if args.type:
        cmd += ["--type", args.type]
    if args.topic:
        cmd += ["--topic", args.topic]
    if args.notify:
        cmd += ["--notify", args.notify]
    cmd += args.extra
    print(f"[process] {' '.join(cmd)}")
    raise SystemExit(subprocess.call(cmd))


# ── 이후 정리: 일정/충돌/대시보드 ─────────────────────────────
def cmd_schedule(args):
    import plan_schedule as ps
    from datetime import datetime
    vault = _vault(args)
    now = datetime.now()
    ms = ps.load_meetings(vault, args.notes_subdir)
    conflicts = ps.detect_conflicts(ms)
    warns = ps.prep_warnings(ms, now)
    print(ps.summarize(ms, conflicts, warns, now, days=args.days))
    if args.write_dashboard:
        md = ps.build_dashboard_md(ms, conflicts, warns, now, days=args.days)
        path = ps.write_dashboard(vault, md, args.notes_subdir)
        print(f"\n대시보드 갱신 → {path}")


def cmd_status(args):
    args.days = None
    args.write_dashboard = False
    cmd_schedule(args)


# ── 이후 정리: 병합 대기 확인 후 병합 ─────────────────────────
def cmd_merge(args):
    import plan_schedule as ps
    from obsidian import ObsidianClient
    vault = _vault(args)
    ms = ps.load_meetings(vault, args.notes_subdir)
    pend = ps.pending_merges(ms)
    if not pend:
        print("[merge] 병합 대기 항목이 없습니다."); return
    obs = ObsidianClient.from_config()
    if obs is None or not obs.ping():
        print("[merge] Obsidian 연결 필요(병합은 REST로 기록). 플러그인을 켜주세요."); 
        if obs: obs.close()
        sys.exit(2)
    for rec, plan in pend:
        ptitle = plan["title"] if plan else rec.get("matched_plan", "")
        print(f"\n  녹음 '{rec['title']}'  →  계획 '{ptitle}'")
        if not args.yes:
            ans = input("  이 매칭이 맞나요? 병합할까요? [Y/n] : ").strip().lower()
            if ans not in ("", "y", "yes"):
                print("  건너뜀"); continue
        res = obs.merge_recording_into_plan(rec["path"], rec.get("matched_plan", ""),
                                            delete_recording=args.delete_recording)
        print(f"  ✅ 병합됨 → {res}" if res else "  ❌ 병합 실패")
    obs.close()


def cmd_people(args):
    import people as pp
    vault = _vault(args)
    if args.add:
        res = pp.sync_from_list(vault, [x.strip() for x in args.add.split(",") if x.strip()],
                                args.department, args.company, args.refs_subdir)
    else:
        res = pp.sync_from_meetings(vault, args.notes_subdir, args.department,
                                    args.company, args.refs_subdir)
    for name, st in res.items():
        print(f"  {st:9s} {name}")
    print(f"[people] {len(res)}명 처리")


def cmd_vault_audio(args):
    import vault_audio as va
    vault = _vault(args)
    n = va.process_vault(vault, args.notes_subdir, only_audio=args.audio,
                         dry_run=args.dry_run, notify=getattr(args, "notify", ""))
    print(f"[vault-audio] 처리 {n}건")


# ── 오디오 자동 감시 ────────────────────────────────────────
def cmd_watch(args):
    """폴더를 감시하며 새 오디오 파일을 자동 처리한다."""
    from audio_watcher import AudioWatcher, _default_callback
    folders = args.folders or list(_c("vault_watcher.watch_folders", []) or [])
    if not folders:
        print("[watch] 오류: --folders 또는 config.vault_watcher.watch_folders 설정 필요")
        sys.exit(2)
    watcher = AudioWatcher.from_config(_default_callback)
    watcher.watch_folders = folders
    if args.interval > 0:
        watcher.poll_interval = args.interval
    print(f"[watch] 감시 시작 (Ctrl+C로 중지): {folders}")
    try:
        watcher.start()
    except KeyboardInterrupt:
        watcher.stop()
        print("\n[watch] 중지")


# ── 단일 파일 수동 처리 ─────────────────────────────────────
def cmd_ingest(args):
    """특정 오디오 파일을 수동으로 처리한다."""
    from ingestion_pipeline import IngestionPipeline
    pipeline = IngestionPipeline()
    result = pipeline.ingest(
        audio_path=args.file,
        doc_type=args.type,
        title=args.title,
        topic=args.topic,
        force=args.force,
        send_email=False if getattr(args, "no_email", False) else None,
    )
    status = result["status"]
    print(f"\n[ingest] 결과: {status}")
    if result.get("note_path"):
        print(f"  노트: {result['note_path']}")
    if result.get("duration"):
        m, s = divmod(int(result["duration"]), 60)
        print(f"  길이: {m}분 {s}초")
    if result.get("error"):
        print(f"  오류: {result['error']}")
    sys.exit(0 if status in ("done", "skipped") else 1)


# ── Vault 재인덱싱 ──────────────────────────────────────────
def cmd_reindex(args):
    """Obsidian Vault 노트를 재인덱싱한다."""
    from vault_indexer import VaultIndexer
    vault = getattr(args, "vault_path", "") or _c("indexing.vault_path") or _c("obsidian.vault_path", "")
    if not vault:
        print("[reindex] 오류: --vault-path 또는 config.indexing.vault_path 설정 필요")
        sys.exit(2)
    index_path = _c("indexing.index_path", "data/vault_index.json")
    indexer = VaultIndexer(vault, index_path)
    n = indexer.build(verbose=True)
    print(f"\n[reindex] 완료: {n}개 노트 인덱싱")


# ── LLM Wiki Q&A ────────────────────────────────────────────
def cmd_ask(args):
    """Vault 지식 베이스를 기반으로 질문에 답한다."""
    from wiki_ask import WikiQA
    qa = WikiQA()
    result = qa.ask(args.question, max_context_notes=args.max_notes)

    print("\n" + "=" * 60)
    print(result["answer"])
    print("=" * 60)

    if args.show_sources and result["sources"]:
        print(f"\n[컨텍스트 노트 {len(result['sources'])}개]")
        for s in result["sources"]:
            print(f"  - {s['title']} (점수: {s.get('score', 0):.3f})")

    markers = []
    if result["has_conflict"]:
        markers.append("⚠️ 충돌 정보 있음")
    if result["unverified"]:
        markers.append("확인 불가 항목 있음")
    if markers:
        print("\n" + " | ".join(markers))


# ── 기존 노트/전사 재분석 ────────────────────────────────────
def cmd_analyze(args):
    """기존 오디오 또는 노트를 다시 분석한다."""
    path = args.path
    if not os.path.exists(path):
        print(f"[analyze] 파일 없음: {path}")
        sys.exit(2)

    ext = os.path.splitext(path)[1].lower()
    audio_exts = {".mp3", ".m4a", ".wav", ".webm", ".mp4", ".ogg", ".flac"}

    if ext in audio_exts:
        # 오디오 파일: force=True로 재처리
        from ingestion_pipeline import IngestionPipeline
        pipeline = IngestionPipeline()
        result = pipeline.ingest(
            audio_path=path,
            doc_type=args.type,
            title=args.title,
            topic=args.topic,
            force=True,
        )
        print(f"[analyze] 결과: {result['status']}")
        if result.get("note_path"):
            print(f"  노트: {result['note_path']}")
    else:
        print(f"[analyze] 오류: 지원하지 않는 파일 형식 ({ext})")
        print("  지원 형식: .mp3 .m4a .wav .webm .mp4 .ogg .flac")
        sys.exit(2)


def main():
    ap = argparse.ArgumentParser(description="통합 회의 비서")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_vault(p):
        p.add_argument("--vault", default="")
        p.add_argument("--notes-subdir", default=_c("obsidian.notes_subdir", "00_Meetings"))

    p = sub.add_parser("prep", help="회의 전: 사전 리서치"); add_vault(p)
    p.add_argument("--note", default="", help="특정 노트만(경로). 없으면 전체 planned")
    p.set_defaults(func=cmd_prep)

    p = sub.add_parser("record", help="회의 중: 실시간 녹취")
    p.add_argument("--type", default="meeting"); p.add_argument("--topic", default="")
    p.add_argument("extra", nargs="*"); p.set_defaults(func=cmd_record)

    p = sub.add_parser(
        "process",
        help="회의 후: 파일 처리(STT~메일~매칭). batch 옵션(--resume, --force-stt 등)도 전달 가능",
        epilog=(
            "batch 하위 옵션 예: --resume, --force-stt, --debug, --no-notify, "
            "--ssl-no-verify, --memo notes.md, --custom-prompt \"...\""
        ),
    )
    p.add_argument("file"); p.add_argument("--title", default="")
    p.add_argument("--type", default="meeting"); p.add_argument("--topic", default="")
    p.add_argument("--notify", default="", help="email 등")
    p.add_argument("extra", nargs="*"); p.set_defaults(func=cmd_process)

    p = sub.add_parser("schedule", help="이후 정리: 일정/충돌/대시보드"); add_vault(p)
    p.add_argument("--days", type=int, default=None)
    p.add_argument("--write-dashboard", action="store_true")
    p.set_defaults(func=cmd_schedule)

    p = sub.add_parser("status", help="현재 상태 요약"); add_vault(p)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("merge", help="이후 정리: 병합 대기 확인 후 병합"); add_vault(p)
    p.add_argument("--yes", action="store_true", help="확인 없이 모두 병합")
    p.add_argument("--delete-recording", action="store_true", help="병합 후 녹음 노트 삭제")
    p.set_defaults(func=cmd_merge)

    p = sub.add_parser("vault-audio", help="옵시디언 노트에 임베드된 녹음 처리·병합"); add_vault(p)
    p.add_argument("--audio", default="", help="특정 오디오 파일만")
    p.add_argument("--dry-run", action="store_true", help="대상만 표시")
    p.add_argument("--notify", default="", help="처리 후 이메일 발송: email")
    p.set_defaults(func=cmd_vault_audio)

    p = sub.add_parser("people", help="인물/팀 레지스트리(참석자→인물 노트)"); add_vault(p)
    p.add_argument("--refs-subdir", default=_c("obsidian.refs_subdir", "01_References"))
    p.add_argument("--add", default="", help="쉼표 구분 참석자(예: \"최민석(팀장),정하윤 수석\")")
    p.add_argument("--department", default="", help="기본 부서/팀")
    p.add_argument("--company", default="", help="기본 회사")
    p.set_defaults(func=cmd_people)

    p = sub.add_parser("watch", help="오디오 폴더 감시 데몬 시작")
    p.add_argument("--folders", nargs="+", default=[], help="감시할 폴더 경로들")
    p.add_argument("--interval", type=float, default=0, help="폴링 간격(초). 0=config값")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("ingest", help="특정 오디오 파일 수동 처리")
    p.add_argument("file", help="처리할 오디오 파일 경로")
    p.add_argument("--type", default="", help="문서 유형: meeting|seminar|lecture|memo")
    p.add_argument("--title", default="", help="노트 제목")
    p.add_argument("--topic", default="", help="주제")
    p.add_argument("--force", action="store_true", help="이미 처리된 파일도 재처리")
    p.add_argument("--no-email", action="store_true",
                   help="config notify.on_finish=email 이어도 이번 실행에서는 이메일을 보내지 않음")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("reindex", help="Vault 노트 인덱스 재빌드")
    p.add_argument("--vault-path", default="", help="볼트 경로 (config에 없을 때)")
    p.set_defaults(func=cmd_reindex)

    p = sub.add_parser("ask", help="Vault 지식 베이스 Q&A")
    p.add_argument("question", help="질문 텍스트")
    p.add_argument("--max-notes", type=int, default=0, help="컨텍스트 최대 노트 수")
    p.add_argument("--show-sources", action="store_true", help="출처 노트 목록 출력")
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("analyze", help="오디오 파일 재분석")
    p.add_argument("path", help="오디오 파일 경로")
    p.add_argument("--type", default="", help="문서 유형")
    p.add_argument("--title", default="", help="노트 제목")
    p.add_argument("--topic", default="", help="주제")
    p.set_defaults(func=cmd_analyze)

    args, unknown = ap.parse_known_args()
    if unknown:
        if getattr(args, "cmd", "") in ("process", "record") and hasattr(args, "extra"):
            args.extra.extend(unknown)
        else:
            ap.error("unrecognized arguments: " + " ".join(unknown))
    args.func(args)


if __name__ == "__main__":
    main()
