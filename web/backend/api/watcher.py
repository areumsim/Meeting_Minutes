"""
api/watcher.py — 오디오 폴더 자동 감시 웹 제어/상태 API
=====================================================================
CLI 전용이던 audio_watcher(AudioWatcher)를 웹에서 시작/중지/상태 조회할 수 있게
백그라운드 스레드로 감싼다. exe(완전 내장) 배포에서 watchdog 가 번들되므로 FS 이벤트
모드로, 없으면 폴링으로 동작한다(AudioWatcher.start 가 자동 폴백).

감시 폴더는 config 의 vault_watcher.watch_folders(리스트)에서 읽는다. 폼에 없는
고급 설정이므로 [설정] → '고급: 전체 설정(JSON)'에서 편집한다.
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
                msg = ("감시할 폴더가 설정되지 않았습니다. [설정] → '고급: 전체 설정(JSON)'에서 "
                       "vault_watcher.watch_folders 에 폴더 경로를 추가하세요.")
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
