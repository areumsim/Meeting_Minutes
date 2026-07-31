"""
plan_watcher.py — 계획 회의 노트 저장 시 '사전 리서치' 자동 실행
==================================================================
Obsidian 볼트의 회의 폴더(00_Meetings)를 감시하다가, 사용자가 status: planned
노트에 안건/주제를 적어 저장하면 자동으로 plan_research 를 실행해
'## 사전 조사' 섹션에 키워드·설명·관련 노트를 채워 넣는다.

실행:
    python run_meeting.py plan-watcher --vault "D:\\Claude\\QC"
    (또는 config.json 의 obsidian.vault_path / obsidian.notes_subdir 사용)
    python run_meeting.py plan-watcher --once     # 한 번만 전체 스캔하고 종료(테스트용)
    python run_meeting.py plan-watcher --interval 3

의존성: 표준 라이브러리만 사용(폴링 방식). Obsidian REST 가 켜져 있으면
참고노트 생성·볼트 검색까지 되고, 꺼져 있으면 글로서리 텍스트만 채운다.
"""

from __future__ import annotations

import os
import sys
import time
import argparse
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("meeting_minutes")

try:
    from meeting_minutes_app.common import config_loader as _cfg
    def _c(k, d=None): return _cfg.get(k, d)
except Exception:
    def _c(k, d=None): return d


def _resolve_vault(args) -> Path:
    vp = args.vault or _c("obsidian.vault_path", "")
    if not vp:
        print("[plan_watcher] 볼트 경로가 없습니다. --vault \"<볼트 폴더>\" 로 지정하거나\n"
              "               config.json 의 obsidian.vault_path 를 설정하세요.")
        sys.exit(2)
    p = Path(vp)
    if not p.is_dir():
        print(f"[plan_watcher] 볼트 폴더를 찾을 수 없음: {p}")
        sys.exit(2)
    return p


def _build_clients():
    """LLMClient 와 ObsidianClient 준비. 실패 시 (None, None) 가능."""
    llm = None
    obs = None
    try:
        from meeting_minutes_app.meeting_pipeline import meeting_minutes as mm
        llm = mm.LLMClient(preferred=_c("models.llm", "gpt"))
    except Exception as e:
        print(f"[plan_watcher] LLM 초기화 실패: {e}")
    try:
        from meeting_minutes_app.wiki_core.obsidian import ObsidianClient
        obs = ObsidianClient.from_config()
        if obs is not None and not obs.ping():
            print("[plan_watcher] Obsidian REST 연결 안 됨 → 글로서리만 작성(참고노트/볼트검색 생략)")
            obs.close(); obs = None
    except Exception as e:
        print(f"[plan_watcher] Obsidian 초기화 실패: {e}")
        obs = None
    return llm, obs


def _budget_blocked(est_cost: float = 0.0) -> str:
    """지출 한도를 넘었으면 사유. 웹 자동화와 CLI 워처가 같은 판정을 쓰게 여기 둔다.

    계획 자동화는 사용자가 화면을 보고 있지 않을 때 LLM 을 부르는데 지금까지 한도
    검사를 전혀 받지 않았다(한도는 업로드·임베딩 경로에만 있었다).

    `est_cost=0` 은 "이미 한도를 넘었는가"만 묻는 것이다. 리서치 1건의 비용을 미리
    알 수 없기 때문이다 — `plan_research.research_planned_note()` 가 토큰 usage 를
    돌려주지 않는다. 없는 숫자를 지어내 한도 계산에 넣는 대신, 넘긴 뒤에 멈추는
    쪽을 택했다(정확한 사전 추정은 usage 배관이 따로 필요하다).
    """
    try:
        from meeting_minutes_app.common import spend_guard
        return spend_guard.blocked(est_cost, check_per_item=False)
    except Exception:
        return ""


def _process_file(path: Path, llm, obs) -> bool:
    """단일 노트 처리. 갱신했으면 True."""
    try:
        content = path.read_text(encoding="utf-8")
    except Exception:
        return False
    if "status: planned" not in content and "status: \"planned\"" not in content:
        return False
    blocked = _budget_blocked()
    if blocked:
        print(f"[plan_watcher] 지출 한도로 사전 리서치 보류({path.name}): {blocked}")
        return False
    try:
        from meeting_minutes_app.meeting_pipeline import plan_research
        new = plan_research.research_planned_note(content, llm, obs=obs)
    except Exception as e:
        print(f"[plan_watcher] 리서치 실패({path.name}): {e}")
        return False
    if not new or new == content:
        return False
    try:
        path.write_text(new, encoding="utf-8")
        print(f"  ✅ 사전 리서치 작성 → {path.name}")
        return True
    except Exception as e:
        print(f"[plan_watcher] 쓰기 실패({path.name}): {e}")
        return False


def _scan(root: Path):
    yield from root.rglob("*.md")


_audio_seen = {}


def _audio_pass(vault, notes_subdir, min_age=6.0):
    """볼트의 새(안정화된) 임베드 오디오를 vault_audio 로 자동 STT·요약·정리·병합."""
    try:
        from meeting_minutes_app.meeting_pipeline import vault_audio as va
    except Exception:
        return 0
    done = 0
    for ap_ in va.find_audio_files(str(vault)):
        try:
            mt = os.path.getmtime(ap_)
        except OSError:
            continue
        if (time.time() - mt) < min_age:   # 아직 녹음 기록 중일 수 있음 → 다음 폴링에
            continue
        key = (ap_, mt)
        if _audio_seen.get(key):
            continue
        # 오디오는 길이로 비용을 추정할 수 있으므로 리서치와 달리 사전 판정이 가능하다.
        est = 0.0
        try:
            from meeting_minutes_app.common import spend_guard
            dur, est = spend_guard.estimate_audio_cost(ap_)
            if dur > 0:
                blocked = spend_guard.blocked(est)
                if blocked:
                    # _audio_seen 에 넣지 않는다 — 한도를 올리면 다음 폴링에 처리된다.
                    print(f"[plan_watcher] 지출 한도로 오디오 보류"
                          f"({os.path.basename(ap_)}): {blocked}")
                    continue
        except Exception:
            est = 0.0
        _audio_seen[key] = 1
        try:
            n = va.process_vault(str(vault), notes_subdir, only_audio=ap_)
            done += n
            # 이 경로도 DB 세션을 만들지 않아 월 합계에서 보이지 않았다.
            if n and est > 0:
                from meeting_minutes_app.common import spend_guard
                spend_guard.record(
                    spend_guard.KIND_PLAN_AUTOMATION, est,
                    note=f"계획 자동화 첨부 녹음: {os.path.basename(ap_)}",
                )
        except Exception as e:
            print(f"[plan_watcher] 오디오 처리 실패({os.path.basename(ap_)}): {e}")
    return done


def main():
    ap = argparse.ArgumentParser(description="계획 회의 노트 사전 리서치 워처")
    ap.add_argument("--vault", default="", help="Obsidian 볼트 폴더 경로")
    ap.add_argument("--notes-subdir", default=_c("obsidian.notes_subdir", "00_Meetings"))
    ap.add_argument("--interval", type=float, default=3.0, help="폴링 주기(초)")
    ap.add_argument("--once", action="store_true", help="한 번만 전체 스캔 후 종료")
    ap.add_argument("--no-audio", action="store_true", help="임베드 녹음 자동 처리 끄기")
    ap.add_argument("--audio-min-age", type=float, default=6.0, help="오디오 안정화 대기(초)")
    args = ap.parse_args()

    vault = _resolve_vault(args)
    watch_root = vault / args.notes_subdir
    if not watch_root.is_dir():
        watch_root = vault
    print(f"[plan_watcher] 감시 폴더: {watch_root}")

    llm, obs = _build_clients()
    if llm is None:
        print("[plan_watcher] LLM 없이는 리서치 불가 → 종료")
        sys.exit(2)

    # 최초 1회 전체 스캔(이미 안건이 적힌 planned 노트 처리)
    seen = {}
    n = 0
    for f in _scan(watch_root):
        if _process_file(f, llm, obs):
            n += 1
        try:
            seen[str(f)] = f.stat().st_mtime
        except OSError:
            pass
    if not args.no_audio:
        na = _audio_pass(vault, args.notes_subdir, args.audio_min_age)
        if na:
            print(f"[plan_watcher] 임베드 녹음 처리 {na}건")
    print(f"[plan_watcher] 초기 스캔 완료 (갱신 {n}건). 대기 중…  (Ctrl+C 종료)")
    if args.once:
        if obs: obs.close()
        return

    try:
        while True:
            time.sleep(args.interval)
            for f in _scan(watch_root):
                try:
                    mt = f.stat().st_mtime
                except OSError:
                    continue
                key = str(f)
                if seen.get(key) == mt:
                    continue
                # 변경 감지 → 처리(처리로 인한 쓰기 mtime 은 처리 후 갱신)
                _process_file(f, llm, obs)
                try:
                    seen[key] = f.stat().st_mtime
                except OSError:
                    seen[key] = mt
            if not args.no_audio:
                _audio_pass(vault, args.notes_subdir, args.audio_min_age)
    except KeyboardInterrupt:
        print("\n[plan_watcher] 종료")
    finally:
        if obs:
            obs.close()


if __name__ == "__main__":
    main()
