"""
api/watcher.py — 오디오 폴더 자동 감시 웹 제어/상태 API
=====================================================================
CLI 전용이던 audio_watcher(AudioWatcher)를 웹에서 시작/중지/상태 조회할 수 있게
백그라운드 스레드로 감싼다. exe(완전 내장) 배포에서 watchdog 가 번들되므로 FS 이벤트
모드로, 없으면 폴링으로 동작한다(AudioWatcher.start 가 자동 폴백).

감시 폴더는 config 의 vault_watcher.watch_folders(리스트)에서 읽는다.
[설정] 하단 '폴더 자동 감시' 카드(WatcherCard)에서 추가/삭제한다.
vault_watcher.enabled / plan_watcher.enabled 가 켜져 있으면 앱 시작 시
autostart_from_config()가 자동으로 재개한다.
"""

import threading
from pathlib import Path

from fastapi import APIRouter

router = APIRouter(prefix="/watcher", tags=["watcher"])


class _WatcherManager:
    """앱 프로세스 내 단일 AudioWatcher 인스턴스를 스레드로 관리."""

    def __init__(self):
        self._lock = threading.Lock()
        self._watcher = None          # AudioWatcher
        self._thread = None           # threading.Thread
        self._folders: list = []
        self._error: str = ""

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> dict:
        with self._lock:
            if self.is_running():
                return {"ok": True, "running": True, "message": "이미 감시 중입니다.",
                        "folders": self._folders}
            try:
                from meeting_minutes_app.common import config_loader as cfg
                from meeting_minutes_app.meeting_pipeline.audio_watcher import (
                    AudioWatcher, _default_callback,
                )
            except Exception as e:
                self._error = f"감시 모듈 로드 실패: {e}"
                return {"ok": False, "running": False, "message": self._error}

            folders = [str(f) for f in (cfg.get("vault_watcher.watch_folders", []) or []) if f]
            if not folders:
                msg = ("감시할 폴더가 설정되지 않았습니다. [설정] 하단 '폴더 자동 감시' "
                       "카드에서 [폴더 추가]로 지정하세요.")
                return {"ok": False, "running": False, "message": msg, "folders": []}

            missing = [f for f in folders if not Path(f).is_dir()]

            watcher = AudioWatcher.from_config(_default_callback)
            watcher.watch_folders = folders
            self._watcher = watcher
            self._folders = folders
            self._error = ""

            def _run():
                try:
                    watcher.start()  # 블로킹 루프 (stop() 으로 종료)
                except Exception as e:  # pragma: no cover - 방어
                    self._error = f"감시 스레드 오류: {e}"

            t = threading.Thread(target=_run, name="vault-watcher", daemon=True)
            t.start()
            self._thread = t

            msg = f"감시를 시작했습니다 ({len(folders)}개 폴더)."
            if missing:
                msg += f" 주의: 존재하지 않는 폴더 {missing}"
            return {"ok": True, "running": True, "message": msg,
                    "folders": folders, "missing_folders": missing}

    def stop(self) -> dict:
        with self._lock:
            if self._watcher is not None:
                try:
                    self._watcher.stop()
                except Exception:
                    pass
            t = self._thread
        if t is not None:
            t.join(timeout=5.0)
        with self._lock:
            still = self.is_running()
            if not still:
                self._thread = None
                self._watcher = None
            return {"ok": not still, "running": still,
                    "message": "감시를 중지했습니다." if not still
                    else "중지 요청됨 — 처리 중인 파일이 끝나면 종료됩니다."}

    def status(self) -> dict:
        from meeting_minutes_app.common import config_loader as cfg
        enabled = bool(cfg.get("vault_watcher.enabled", False))
        folders = [str(f) for f in (cfg.get("vault_watcher.watch_folders", []) or []) if f]
        counts = {"done": 0, "failed": 0, "processing": 0, "skipped": 0, "total": 0}
        recent = []
        try:
            state_path = cfg.get("vault_watcher.processed_state_path", "data/processed_audio.json")
            p = Path(state_path)
            if not p.is_absolute():
                from meeting_minutes_app.common import app_paths
                p = app_paths.get_base_dir() / state_path
            if p.exists():
                import json
                with open(p, "r", encoding="utf-8") as f:
                    state = json.load(f)
                counts["total"] = len(state)
                items = []
                for path, entry in state.items():
                    st = entry.get("status", "?")
                    if st in counts:
                        counts[st] += 1
                    items.append({
                        "file": Path(path).name,
                        "status": st,
                        "processed_at": entry.get("processed_at", ""),
                        "note_path": entry.get("note_path", ""),
                        "error": entry.get("error", ""),
                    })
                items.sort(key=lambda x: x["processed_at"], reverse=True)
                recent = items[:10]
        except Exception:
            pass
        return {
            "running": self.is_running(),
            "config_enabled": enabled,
            "folders": folders,
            "counts": counts,
            "recent": recent,
            "error": self._error,
        }


_manager = _WatcherManager()


@router.get("/status")
def watcher_status():
    return _manager.status()


@router.post("/start")
def watcher_start():
    return _manager.start()


@router.post("/stop")
def watcher_stop():
    return _manager.stop()


# ── 계획 자동화 (plan-watcher/auto-process 통합) ──────────────────
# 볼트의 planned 노트에 사전 리서치를 자동 작성하고, 노트에 첨부된 새 녹음을
# 자동으로 회의록화한다(plan_watcher 의 처리 블록 재사용). auto-process 의 임베드
# 오디오 처리는 여기 _audio_pass 에 포섭된다.
class _PlanAutomationManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._thread = None
        self._stop = threading.Event()
        self._error = ""
        self.notes = 0
        self.audio = 0

    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self, interval: float = 15.0):
        with self._lock:
            if self.is_running():
                return {"ok": True, "running": True, "message": "이미 실행 중입니다."}
            from meeting_minutes_app.common import config_loader as cfg
            vault = (cfg.get("obsidian.vault_path", "") or cfg.get("indexing.vault_path", "") or "").strip()
            if not vault or not Path(vault).is_dir():
                return {"ok": False, "running": False,
                        "message": "Obsidian 볼트 폴더가 설정/존재하지 않습니다. [설정]에서 지정하세요."}
            notes_subdir = cfg.get("obsidian.notes_subdir", "00_Meetings") or "00_Meetings"
            self._stop.clear()
            self._error = ""
            self.notes = 0
            self.audio = 0

            def _run():
                try:
                    from meeting_minutes_app.meeting_pipeline import plan_watcher as pw
                    root = Path(vault)
                    watch_root = root / notes_subdir
                    if not watch_root.is_dir():
                        watch_root = root
                    llm, obs = pw._build_clients()
                    if llm is None:
                        self._error = "LLM 초기화 실패 — [설정]에서 API 키를 확인하세요. 자동화 중지."
                        return
                    seen = {}
                    for f in pw._scan(watch_root):
                        if self._stop.is_set():
                            break
                        # 노트 1건의 실패가 스레드를 죽여 자동화 전체가 조용히 멈추지
                        # 않도록 파일 단위로 격리한다(오디오 패스와 동일한 정책).
                        try:
                            if pw._process_file(f, llm, obs):
                                self.notes += 1
                        except Exception as e:
                            print(f"[plan-auto] 노트 처리 오류(건너뜀): {f} — {e}")
                        try:
                            seen[str(f)] = f.stat().st_mtime
                        except OSError:
                            pass
                    try:
                        self.audio += pw._audio_pass(root, notes_subdir)
                    except Exception as e:
                        print(f"[plan-auto] 오디오 패스 오류: {e}")
                    while not self._stop.is_set():
                        self._stop.wait(interval)
                        if self._stop.is_set():
                            break
                        for f in pw._scan(watch_root):
                            try:
                                mt = f.stat().st_mtime
                            except OSError:
                                continue
                            if seen.get(str(f)) == mt:
                                continue
                            seen[str(f)] = mt
                            try:
                                if pw._process_file(f, llm, obs):
                                    self.notes += 1
                            except Exception as e:
                                print(f"[plan-auto] 노트 처리 오류(건너뜀): {f} — {e}")
                        try:
                            self.audio += pw._audio_pass(root, notes_subdir)
                        except Exception as e:
                            print(f"[plan-auto] 오디오 패스 오류: {e}")
                    if obs:
                        obs.close()
                except Exception as e:  # pragma: no cover
                    self._error = f"자동화 스레드 오류: {e}"

            t = threading.Thread(target=_run, name="plan-automation", daemon=True)
            t.start()
            self._thread = t
            return {"ok": True, "running": True,
                    "message": "계획 자동화를 시작했습니다(planned 노트 사전 리서치 + 첨부 녹음 자동 처리)."}

    def stop(self):
        self._stop.set()
        t = self._thread
        if t is not None:
            t.join(timeout=5.0)
        with self._lock:
            still = self.is_running()
            if not still:
                self._thread = None
            return {"ok": not still, "running": still,
                    "message": "자동화를 중지했습니다." if not still
                    else "중지 요청됨 — 현재 처리 중인 작업이 끝나면 종료됩니다."}

    def status(self):
        from meeting_minutes_app.common import config_loader as cfg
        vault = (cfg.get("obsidian.vault_path", "") or cfg.get("indexing.vault_path", "") or "").strip()
        return {
            "running": self.is_running(),
            "vault": vault,
            "notes_researched": self.notes,
            "audio_processed": self.audio,
            "error": self._error,
        }


_plan = _PlanAutomationManager()


def autostart_from_config() -> None:
    """앱 시작 시 config 플래그 기준으로 백그라운드 자동화를 재개한다.

    exe 사용자는 앱을 껐다 켜는 게 일상이라, 버튼으로 켠 감시가 재시작 후
    사라지면 '자동 처리가 안 된다'로 체감된다. enabled 플래그가 켜져 있으면
    시작 시 자동으로 다시 켠다(실패해도 부팅은 계속).
    """
    from meeting_minutes_app.common import config_loader as cfg
    try:
        if bool(cfg.get("vault_watcher.enabled", False)):
            r = _manager.start()
            print(f"[watcher] 자동 시작: {r.get('message', '')}")
    except Exception as e:
        print(f"[watcher] 자동 시작 실패(무시): {e}")
    try:
        if bool(cfg.get("plan_watcher.enabled", False)):
            r = _plan.start()
            print(f"[plan-auto] 자동 시작: {r.get('message', '')}")
    except Exception as e:
        print(f"[plan-auto] 자동 시작 실패(무시): {e}")


@router.get("/plan/status")
def plan_status():
    return _plan.status()


@router.post("/plan/start")
def plan_start():
    return _plan.start()


@router.post("/plan/stop")
def plan_stop():
    return _plan.stop()
