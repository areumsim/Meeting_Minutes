"""
api/assistant.py — 계획 비서 & Obsidian 진단 웹 API
=====================================================================
CLI 전용이던 일정/현황(status·schedule)·병합(merge)·노트 첨부 오디오 처리
(vault-audio)·Obsidian 연결 전체 진단을 웹에서 쓸 수 있게 노출한다.

- status/schedule/merge/diagnose: 빠른 동기 처리(노트 읽기·REST 조회만).
- vault-audio 실처리: STT/LLM 이 걸려 오래 걸리므로 백그라운드 스레드 + 상태 조회.
  dry_run(미리보기)은 빠르므로 동기.

볼트 경로/노트 폴더는 config(obsidian.vault_path 또는 indexing.vault_path,
obsidian.notes_subdir)에서 읽는다 — [설정]에서 이미 지정하는 값.
"""

import threading
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/assistant", tags=["assistant"])


def _cfg():
    from meeting_minutes_app.common import config_loader as c
    return c


def _vault() -> str:
    c = _cfg()
    return (c.get("obsidian.vault_path", "") or c.get("indexing.vault_path", "") or "").strip()


def _notes_subdir() -> str:
    return _cfg().get("obsidian.notes_subdir", "00_Meetings") or "00_Meetings"


def _no_vault():
    return {"ok": False, "message": "Obsidian 볼트 폴더가 설정되지 않았습니다. [설정]에서 지정하세요."}


# ── 일정/현황 요약 (status/schedule) ──────────────────────────
def _summary_payload(days, write_dashboard: bool):
    from datetime import datetime
    from meeting_minutes_app.meeting_pipeline import plan_schedule as ps
    vault = _vault()
    if not vault:
        return _no_vault()
    if not Path(vault).is_dir():
        return {"ok": False, "message": f"볼트 폴더가 존재하지 않습니다: {vault}"}
    sub = _notes_subdir()
    now = datetime.now()
    meetings = ps.load_meetings(vault, sub)
    conflicts = ps.detect_conflicts(meetings)
    warns = ps.prep_warnings(meetings, now)
    summary = ps.summarize(meetings, conflicts, warns, now, days=days)
    out = {
        "ok": True,
        "summary": summary,
        "counts": {
            "meetings": len(meetings),
            "conflicts": len(conflicts),
            "warnings": len(warns),
            "pending_merges": len(ps.pending_merges(meetings)),
        },
    }
    if write_dashboard:
        md = ps.build_dashboard_md(meetings, conflicts, warns, now, days=days)
        path = ps.write_dashboard(vault, md, sub)
        out["dashboard_path"] = str(path)
    return out


@router.get("/status")
def assistant_status(days: int = 7):
    """다가오는 회의·충돌·준비미비·병합대기 현황 요약(읽기 전용)."""
    try:
        return _summary_payload(days if days > 0 else None, write_dashboard=False)
    except Exception as e:
        return {"ok": False, "message": f"현황 조회 실패: {e}"}


class ScheduleReq(BaseModel):
    days: int = 14
    write_dashboard: bool = True


@router.post("/schedule")
def assistant_schedule(req: ScheduleReq):
    """일정 대시보드(_일정.md)를 갱신하고 요약을 반환."""
    try:
        return _summary_payload(req.days if req.days > 0 else None, write_dashboard=req.write_dashboard)
    except Exception as e:
        return {"ok": False, "message": f"일정 갱신 실패: {e}"}


# ── 병합 대기 (merge) ─────────────────────────────────────────
@router.get("/merges")
def assistant_merges():
    """녹음↔계획 병합 대기 목록."""
    try:
        from meeting_minutes_app.meeting_pipeline import plan_schedule as ps
        vault = _vault()
        if not vault:
            return _no_vault()
        meetings = ps.load_meetings(vault, _notes_subdir())
        items = []
        for rec, plan in ps.pending_merges(meetings):
            items.append({
                "recording_title": rec.get("title", ""),
                "recording_path": rec.get("path", ""),
                "plan_title": (plan.get("title") if plan else rec.get("matched_plan", "")) or "",
                "matched_plan": rec.get("matched_plan", ""),
            })
        return {"ok": True, "pending": items}
    except Exception as e:
        return {"ok": False, "message": f"병합 대기 조회 실패: {e}"}


class MergeReq(BaseModel):
    recording_path: str
    delete_recording: bool = False


@router.post("/merge")
def assistant_merge(req: MergeReq):
    """선택한 녹음을 매칭된 계획 노트에 병합(Obsidian REST 필요)."""
    try:
        from meeting_minutes_app.meeting_pipeline import plan_schedule as ps
        from meeting_minutes_app.wiki_core.obsidian import ObsidianClient
        vault = _vault()
        if not vault:
            return _no_vault()
        meetings = ps.load_meetings(vault, _notes_subdir())
        target = None
        for rec, plan in ps.pending_merges(meetings):
            if rec.get("path") == req.recording_path:
                target = rec
                break
        if target is None:
            return {"ok": False, "message": "해당 병합 대기 항목을 찾을 수 없습니다(이미 처리됐거나 목록이 바뀜)."}
        obs = ObsidianClient.from_config()
        if obs is None or not obs.ping():
            if obs:
                obs.close()
            return {"ok": False, "message": "병합은 Obsidian Local REST API가 필요합니다. [설정]에서 REST를 켜고 키를 넣으세요."}
        res = obs.merge_recording_into_plan(
            target["path"], target.get("matched_plan", ""),
            delete_recording=req.delete_recording,
        )
        obs.close()
        if res:
            return {"ok": True, "message": f"병합 완료 → {res}"}
        return {"ok": False, "message": "병합 실패(대상 계획 노트를 찾지 못했을 수 있음)."}
    except Exception as e:
        return {"ok": False, "message": f"병합 실패: {e}"}


# ── Obsidian 전체 진단 ────────────────────────────────────────
@router.get("/obsidian-diagnose")
def obsidian_diagnose():
    """볼트 경로·검색 인덱스·REST 연결을 종합 진단."""
    c = _cfg()
    checks = []

    def add(name, ok, detail):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    # 0) 이 화면이 어느 인스턴스인지 — 같은 PC 에 소스 실행과 포터블 배포본이 함께 뜰 수
    # 있고 둘은 데이터 폴더(=config.json)가 다르다. 구분이 안 되면 "설정이 전부 사라졌다"로
    # 오해한다(2026-07-30 실사고). 항상 ok=True — 상태가 아니라 식별 정보다.
    try:
        import os as _os
        from meeting_minutes_app.common import app_paths as _ap
        _kind = "포터블 배포본" if _os.environ.get("MM_DATA_DIR") else "소스 실행"
        add("데이터 폴더", True, f"{_ap.get_base_dir()}  ({_kind})")
    except Exception as e:
        add("데이터 폴더", False, f"확인 실패: {e}")

    # 1) 볼트 경로
    vault = _vault()
    if not vault:
        add("볼트 폴더", False, "설정되지 않음 — [설정]에서 지정")
    else:
        p = Path(vault)
        add("볼트 폴더", p.is_dir(), (str(vault) if p.is_dir() else f"경로 없음/폴더 아님: {vault}"))

    # 2) 검색 인덱스
    try:
        idx_cfg = c.get("indexing.index_path", "data/vault_index.json")
        ip = Path(idx_cfg)
        if not ip.is_absolute():
            from meeting_minutes_app.common import app_paths
            ip = app_paths.get_base_dir() / idx_cfg
        if ip.exists():
            import json
            data = json.loads(ip.read_text(encoding="utf-8"))
            n = len(data.get("notes", data) if isinstance(data, dict) else data)
            add("검색 인덱스", True, f"{n}개 노트 색인됨 ({ip.name})")
        else:
            add("검색 인덱스", False, "아직 없음 — [설정]에서 '검색 인덱스 재빌드' 실행")
    except Exception as e:
        add("검색 인덱스", False, f"확인 실패: {e}")

    # 3) REST 설정 + 연결
    rest_enabled = bool(c.get("obsidian.enabled", False))
    has_key = bool(c.get("obsidian.api_key", ""))
    if not rest_enabled and not has_key:
        add("Local REST API", True, "미사용(폴더 직접 쓰기 모드) — 정상. 실시간 반영이 필요하면 [설정]에서 켜세요.")
    else:
        try:
            from meeting_minutes_app.wiki_core.obsidian import ObsidianClient
            obs = ObsidianClient.from_config()
            if obs is None:
                add("Local REST API", False, "클라이언트 생성 실패 — 주소/키 확인")
            else:
                ok = obs.ping()
                obs.close()
                add("Local REST API", ok, "연결 성공" if ok else "연결 실패 — Obsidian 앱 실행/플러그인 활성/키 확인")
        except Exception as e:
            add("Local REST API", False, f"확인 실패: {e}")

    overall = all(ch["ok"] for ch in checks)
    return {"ok": overall, "checks": checks}


# ── 노트 첨부 오디오 처리 (vault-audio) ───────────────────────
class _VaultAudioState:
    def __init__(self):
        self.lock = threading.Lock()
        self.thread = None
        self.running = False
        self.done = 0
        self.message = ""

    def is_running(self):
        return self.thread is not None and self.thread.is_alive()


_va = _VaultAudioState()


class VaultAudioReq(BaseModel):
    dry_run: bool = False
    only_audio: str = ""
    # 완료 알림 채널("email"/"slack"/"teams"). 비우면 notify.on_finish 설정을 따른다.
    # run_vault_audio_email.bat(`vault-audio --notify email`)과 동등 기능.
    notify: str = ""


@router.post("/vault-audio")
def assistant_vault_audio(req: VaultAudioReq):
    """Obsidian 노트에 첨부된 오디오를 찾아 회의록화.
    dry_run: 처리 대상만 미리 집계(동기). 실처리는 백그라운드 실행."""
    from meeting_minutes_app.meeting_pipeline import vault_audio as va
    vault = _vault()
    if not vault:
        return _no_vault()
    sub = _notes_subdir()

    if req.dry_run:
        try:
            n = va.process_vault(vault, sub, only_audio=req.only_audio, dry_run=True)
            return {"ok": True, "dry_run": True, "count": n,
                    "message": f"처리 대상 {n}건 (미리보기). 실행하려면 다시 [처리]를 누르세요."}
        except Exception as e:
            return {"ok": False, "message": f"미리보기 실패: {e}"}

    with _va.lock:
        if _va.is_running():
            return {"ok": True, "running": True, "message": "이미 처리 중입니다."}

        # 요청에 채널이 없으면 전역 완료 알림 설정(notify.on_finish)을 따른다.
        notify = (req.notify or "").strip().lower()
        if not notify:
            try:
                from meeting_minutes_app.common import config_loader as _cfg
                ch = (_cfg.get("notify.on_finish", "none") or "none").lower()
                notify = "" if ch == "none" else ch
            except Exception:
                notify = ""

        def _run():
            try:
                n = va.process_vault(vault, sub, only_audio=req.only_audio, dry_run=False,
                                     notify=notify)
                _va.done = n
                _va.message = f"처리 완료: {n}건"
            except Exception as e:
                _va.message = f"처리 오류: {e}"
            finally:
                _va.running = False

        _va.running = True
        _va.done = 0
        _va.message = "처리 중..."
        t = threading.Thread(target=_run, name="vault-audio", daemon=True)
        t.start()
        _va.thread = t
        return {"ok": True, "running": True, "message": "노트 첨부 오디오 처리를 시작했습니다(백그라운드)."}


@router.get("/vault-audio/status")
def assistant_vault_audio_status():
    return {"running": _va.is_running(), "done": _va.done, "message": _va.message}
