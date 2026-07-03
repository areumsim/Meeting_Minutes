"""
audio_watcher.py — 오디오 파일 폴더 감시 + 자동 처리
======================================================
지정 폴더를 감시하다가 새 오디오 파일이 안정화되면 콜백(처리 파이프라인)을 호출한다.
상태 파일(JSON)로 처리된 파일을 추적해 중복 처리를 방지한다.

watchdog 라이브러리가 있으면 FS 이벤트 기반으로, 없으면 폴링으로 동작한다.

단독 실행 (watch 데몬):
    python run_meeting.py audio-watcher --folders "D:\\Recordings" "C:\\Vault\\recordings"
    python run_meeting.py audio-watcher --reprocess "D:\\Recordings\\file.m4a"
"""

from __future__ import annotations

import os
import json
import time
import threading
from pathlib import Path
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set

try:
    import config_loader as _cfg
    _cfg_ok = True
except ImportError:
    _cfg = None  # type: ignore
    _cfg_ok = False


def _c(key: str, default: Any = None) -> Any:
    return _cfg.get(key, default) if _cfg_ok else default


_DEFAULT_EXTS: Set[str] = {"mp3", "m4a", "wav", "webm", "mp4", "ogg", "flac", "mpga"}


class AudioWatcher:
    """오디오 파일 폴더 감시 + 중복 처리 방지 상태 저장소."""

    def __init__(
        self,
        watch_folders: List[str],
        state_path: str,
        callback: Callable[[str], Any],
        supported_exts: Optional[Set[str]] = None,
        poll_interval: float = 10.0,
        stability_checks: int = 3,
        stability_interval: float = 2.0,
        min_size_mb: float = 0.5,
    ):
        self.watch_folders = [str(f) for f in watch_folders if f]
        self.state_path = state_path
        self.callback = callback
        self.supported_exts = supported_exts or _DEFAULT_EXTS
        self.poll_interval = poll_interval
        self.stability_checks = stability_checks
        self.stability_interval = stability_interval
        self.min_size_bytes = int(min_size_mb * 1024 * 1024)
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    # ── 상태 파일 ──────────────────────────────────────────
    def _load_state(self) -> Dict[str, Any]:
        if not os.path.exists(self.state_path):
            return {}
        try:
            return json.load(open(self.state_path, encoding="utf-8"))
        except Exception:
            return {}

    def _save_state(self, state: Dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.state_path)), exist_ok=True)
        tmp = self.state_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.state_path)
        except Exception as e:
            print(f"[watcher] 상태 저장 실패: {e}")

    def _is_processed(self, abs_path: str) -> bool:
        with self._lock:
            state = self._load_state()
        entry = state.get(abs_path)
        if not entry:
            return False
        return entry.get("status") in ("done", "skipped")

    def _mark_processed(self, abs_path: str, status: str,
                        note_path: str = "", error: str = "") -> None:
        with self._lock:
            state = self._load_state()
            state[abs_path] = {
                "processed_at": datetime.now().isoformat(timespec="seconds"),
                "status": status,
                "note_path": note_path,
                "error": error,
            }
            self._save_state(state)

    def reprocess(self, abs_path: str) -> None:
        """상태를 초기화해 재처리를 허용한다."""
        with self._lock:
            state = self._load_state()
            state.pop(abs_path, None)
            self._save_state(state)
        print(f"[watcher] 재처리 상태 초기화: {Path(abs_path).name}")

    # ── 파일 안정성 체크 ──────────────────────────────────
    def _is_audio(self, path: str) -> bool:
        ext = Path(path).suffix.lstrip(".").lower()
        return ext in self.supported_exts

    def _is_stable(self, path: str) -> bool:
        """파일 크기가 stability_checks 회 연속으로 변하지 않으면 True."""
        if not os.path.isfile(path):
            return False
        if os.path.getsize(path) < self.min_size_bytes:
            return False
        try:
            prev_size = os.path.getsize(path)
        except OSError:
            return False
        for _ in range(self.stability_checks):
            time.sleep(self.stability_interval)
            try:
                size = os.path.getsize(path)
            except OSError:
                return False
            if size != prev_size or size == 0:
                return False
            prev_size = size
        return True

    # ── 처리 호출 ─────────────────────────────────────────
    def _handle_file(self, abs_path: str) -> None:
        if self._is_processed(abs_path):
            return
        if not self._is_stable(abs_path):
            print(f"[watcher] 파일 불안정(복사 중?), 건너뜀: {Path(abs_path).name}")
            return
        print(f"[watcher] 새 파일 처리 시작: {Path(abs_path).name}")
        self._mark_processed(abs_path, "processing")
        try:
            callback_result = self.callback(abs_path)
            if isinstance(callback_result, dict):
                status = str(callback_result.get("status") or "done")
                note_path = str(callback_result.get("note_path") or "")
                error = str(callback_result.get("error") or "")
                if status in ("done", "skipped"):
                    self._mark_processed(abs_path, status, note_path=note_path, error=error)
                else:
                    self._mark_processed(abs_path, "failed", note_path=note_path, error=error or status)
            else:
                self._mark_processed(abs_path, "done")
        except Exception as e:
            self._mark_processed(abs_path, "failed", error=str(e))
            print(f"[watcher] 처리 실패: {e}")

    # ── 폴링 스캔 ─────────────────────────────────────────
    def _scan_once(self) -> None:
        import glob
        from concurrent.futures import ThreadPoolExecutor
        candidates = []
        for folder in self.watch_folders:
            if not os.path.isdir(folder):
                continue
            for fpath in glob.glob(os.path.join(folder, "**", "*"), recursive=True):
                if os.path.isfile(fpath) and self._is_audio(fpath):
                    abs_path = os.path.abspath(fpath)
                    if not self._is_processed(abs_path):
                        candidates.append(abs_path)
        if candidates:
            with ThreadPoolExecutor(max_workers=4) as ex:
                for path in candidates:
                    ex.submit(self._handle_file, path)

    def _polling_loop(self) -> None:
        print(f"[watcher] 폴링 모드 (interval={self.poll_interval}s)")
        while not self._stop_event.is_set():
            self._scan_once()
            self._stop_event.wait(self.poll_interval)

    # ── 감시 시작/중지 ────────────────────────────────────
    def start(self) -> None:
        """감시를 시작한다. watchdog 있으면 FS 이벤트 기반, 없으면 폴링."""
        # 시작 시 기존 파일 스캔
        print(f"[watcher] 감시 폴더: {self.watch_folders}")
        self._scan_once()

        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler

            watcher = self

            class _Handler(FileSystemEventHandler):
                def on_created(self, event):
                    if event.is_directory:
                        return
                    path = os.path.abspath(event.src_path)
                    if watcher._is_audio(path):
                        threading.Thread(
                            target=watcher._handle_file,
                            args=(path,),
                            daemon=True,
                        ).start()

                def on_moved(self, event):
                    # 이동으로 완료된 파일(다운로드 등)
                    if not event.is_directory:
                        path = os.path.abspath(event.dest_path)
                        if watcher._is_audio(path):
                            threading.Thread(
                                target=watcher._handle_file,
                                args=(path,),
                                daemon=True,
                            ).start()

            observer = Observer()
            handler = _Handler()
            for folder in self.watch_folders:
                if os.path.isdir(folder):
                    observer.schedule(handler, folder, recursive=True)
            observer.start()
            print("[watcher] watchdog FS 이벤트 모드 시작")
            try:
                while not self._stop_event.is_set():
                    self._stop_event.wait(1)
            finally:
                observer.stop()
                observer.join()

        except ImportError:
            self._polling_loop()

    def stop(self) -> None:
        self._stop_event.set()

    # ── 팩토리 ────────────────────────────────────────────
    @classmethod
    def from_config(cls, callback: Callable[[str], Any]) -> "AudioWatcher":
        folders = list(_c("vault_watcher.watch_folders", []) or [])
        state_path = _c("vault_watcher.processed_state_path", "data/processed_audio.json")
        exts = set(_c("vault_watcher.supported_extensions", list(_DEFAULT_EXTS)))
        poll = float(_c("vault_watcher.poll_interval", 10))
        checks = int(_c("vault_watcher.stability_checks", 3))
        interval = float(_c("vault_watcher.stability_interval", 2.0))
        min_mb = float(_c("vault_watcher.min_size_mb", 0.5))
        return cls(
            watch_folders=folders,
            state_path=state_path,
            callback=callback,
            supported_exts=exts,
            poll_interval=poll,
            stability_checks=checks,
            stability_interval=interval,
            min_size_mb=min_mb,
        )


def _default_callback(audio_path: str) -> Dict[str, Any]:
    """기본 콜백: ingestion_pipeline.ingest_file() 호출."""
    from ingestion_pipeline import ingest_file
    result = ingest_file(audio_path)
    status = result.get("status", "?")
    note_path = result.get("note_path", "")
    error = result.get("error", "")
    if status == "done":
        print(f"[watcher] ✅ 완료: {note_path}")
    else:
        print(f"[watcher] ❌ {status}: {error}")
    return result


def main():
    import argparse
    ap = argparse.ArgumentParser(description="오디오 폴더 감시 데몬")
    ap.add_argument("--folders", nargs="+", default=[], help="감시할 폴더 경로들")
    ap.add_argument("--state", default="", help="상태 파일 경로")
    ap.add_argument("--interval", type=float, default=0, help="폴링 간격(초). 0=config값")
    ap.add_argument("--reprocess", default="", help="특정 파일 상태 초기화 후 재처리")
    args = ap.parse_args()

    folders = args.folders or list(_c("vault_watcher.watch_folders", []) or [])
    if not folders:
        print("[watcher] 오류: --folders 로 감시 폴더를 지정하거나 config.vault_watcher.watch_folders 설정")
        return 1

    state_path = args.state or _c("vault_watcher.processed_state_path", "data/processed_audio.json")

    watcher = AudioWatcher.from_config(_default_callback)
    watcher.watch_folders = folders
    watcher.state_path = state_path
    if args.interval > 0:
        watcher.poll_interval = args.interval

    if args.reprocess:
        watcher.reprocess(os.path.abspath(args.reprocess))

    try:
        watcher.start()
    except KeyboardInterrupt:
        print("\n[watcher] 중지")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main() or 0)
