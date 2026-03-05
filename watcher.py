"""
watcher.py – 폴더 감시 자동 처리 (Watch Mode)
===============================================
지정 폴더에 음성/영상 파일이 들어오면 자동으로 회의록을 생성합니다.

설치:
    pip install watchdog

사용 예:
    python watcher.py ./recordings
    python watcher.py ./recordings --profile weekly
    python watcher.py ./recordings --profile weekly --notify email
    python watcher.py ./recordings --no-move
"""

from __future__ import annotations

import os
import sys
import time
import shutil
import logging
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Optional
from collections import deque

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    HAS_WATCHDOG = True
except ImportError:
    HAS_WATCHDOG = False


# ── 설정 ──────────────────────────────────────────────────

SUPPORTED_EXTENSIONS = {
    ".mp4", ".mp3", ".webm", ".wav", ".m4a", ".ogg",
    ".flac", ".aac", ".wma", ".mkv", ".avi", ".mov",
}

# 파일 쓰기 완료 대기(초) — 네트워크 드라이브 큰 파일은 늘릴 것
SETTLE_TIME = 5

# 처리 완료 파일을 옮길 하위 디렉토리 (None 이면 이동 안 함)
DONE_SUBDIR = "_processed"


# ── 핵심 핸들러 ───────────────────────────────────────────

class MeetingFileHandler(FileSystemEventHandler):
    """새 파일 감지 → 안정화 대기 → meeting_minutes.py 실행."""

    def __init__(
        self,
        script_path: str = "meeting_minutes.py",
        profile: str = "",
        extra_args: Optional[list[str]] = None,
        move_after: bool = True,
        notify: str = "",
    ):
        super().__init__()
        self.script_path = script_path
        self.profile = profile
        self.extra_args = extra_args or []
        self.move_after = move_after
        self.notify = notify
        self._queue: deque[str] = deque()
        self._processing = False
        self.logger = logging.getLogger("watcher")

    def on_created(self, event):
        if event.is_directory:
            return
        fpath = event.src_path
        ext = Path(fpath).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            return
        if DONE_SUBDIR in fpath:
            return
        self.logger.info(f"  새 파일 감지: {os.path.basename(fpath)}")
        self._queue.append(fpath)
        self._process_queue()

    def _process_queue(self):
        """큐에서 하나씩 처리 (동시 처리 방지)."""
        if self._processing:
            return
        self._processing = True
        try:
            while self._queue:
                fpath = self._queue.popleft()
                self._wait_for_stable(fpath)
                if os.path.exists(fpath):
                    self._run_processing(fpath)
        finally:
            self._processing = False

    def _wait_for_stable(self, fpath: str):
        """파일 쓰기가 완료될 때까지 대기."""
        self.logger.info(f"  파일 안정화 대기 ({SETTLE_TIME}초)...")
        prev_size = -1
        for _ in range(30):
            time.sleep(SETTLE_TIME)
            try:
                size = os.path.getsize(fpath)
            except OSError:
                return
            if size == prev_size and size > 0:
                return
            prev_size = size
        self.logger.warning(f"  파일 안정화 타임아웃: {fpath}")

    def _run_processing(self, fpath: str):
        """meeting_minutes.py를 서브프로세스로 실행."""
        fname = os.path.basename(fpath)
        self.logger.info(f"  처리 시작: {fname}")
        start = time.time()

        cmd = [sys.executable, self.script_path, fpath]
        if self.profile:
            cmd += ["--profile", self.profile]
        if self.notify:
            cmd += ["--notify", self.notify]
        cmd += self.extra_args

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3600,
            )
            elapsed = time.time() - start

            if result.returncode == 0:
                self.logger.info(f"  완료: {fname} ({elapsed:.0f}초)")
                self._on_success(fpath)
            else:
                self.logger.error(
                    f"  실패: {fname}\n"
                    f"  stderr: {result.stderr[-500:]}"
                )
                self._on_failure(fpath, result.stderr)

        except subprocess.TimeoutExpired:
            self.logger.error(f"  타임아웃: {fname}")
            self._on_failure(fpath, "처리 시간 1시간 초과")
        except Exception as e:
            self.logger.error(f"  예외: {fname} – {e}")
            self._on_failure(fpath, str(e))

    def _on_success(self, fpath: str):
        """성공 후처리: 파일 이동."""
        if self.move_after:
            done_dir = os.path.join(os.path.dirname(fpath), DONE_SUBDIR)
            os.makedirs(done_dir, exist_ok=True)
            dest = os.path.join(done_dir, os.path.basename(fpath))
            try:
                shutil.move(fpath, dest)
                self.logger.info(f"  이동: {DONE_SUBDIR}/{os.path.basename(fpath)}")
            except OSError as e:
                self.logger.warning(f"  파일 이동 실패: {e}")

    def _on_failure(self, fpath: str, error: str):
        """실패 시 에러 로그 파일 생성."""
        error_file = fpath + ".error.txt"
        with open(error_file, "w", encoding="utf-8") as f:
            f.write(f"처리 실패: {datetime.now().isoformat()}\n")
            f.write(f"파일: {fpath}\n\n")
            f.write(error)
        self.logger.info(f"  에러 로그 → {error_file}")


# ── 메인 워처 ─────────────────────────────────────────────

def run_watcher(
    watch_dir: str,
    script_path: str = "meeting_minutes.py",
    profile: str = "",
    extra_args: Optional[list[str]] = None,
    move_after: bool = True,
    notify: str = "",
):
    """폴더 감시를 시작합니다."""
    if not HAS_WATCHDOG:
        print("  watchdog 패키지가 필요합니다:")
        print("  pip install watchdog")
        sys.exit(1)

    watch_dir = os.path.abspath(watch_dir)
    os.makedirs(watch_dir, exist_ok=True)

    log_path = os.path.join(watch_dir, "watcher.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s │ %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_path, encoding="utf-8"),
        ],
    )
    logger = logging.getLogger("watcher")

    logger.info("=" * 50)
    logger.info(f"  폴더 감시 시작")
    logger.info(f"  경로: {watch_dir}")
    logger.info(f"  프로필: {profile or '(기본)'}")
    logger.info(f"  알림: {notify or '(없음)'}")
    logger.info(f"  지원 포맷: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")
    logger.info(f"  완료 후 이동: {DONE_SUBDIR if move_after else '(안 함)'}")
    logger.info(f"  종료: Ctrl+C")
    logger.info("=" * 50)

    # 기존 미처리 파일 확인
    existing = [
        f for f in os.listdir(watch_dir)
        if Path(f).suffix.lower() in SUPPORTED_EXTENSIONS and DONE_SUBDIR not in f
    ]
    if existing:
        logger.info(f"\n  기존 미처리 파일 {len(existing)}개 발견:")
        for f in existing:
            logger.info(f"     - {f}")
        answer = input("\n  기존 파일도 처리할까요? (Y/n): ").strip().lower()
        if answer in ("", "y", "yes"):
            handler_init = MeetingFileHandler(
                script_path=script_path, profile=profile,
                extra_args=extra_args or [], move_after=move_after, notify=notify,
            )
            for f in existing:
                handler_init._run_processing(os.path.join(watch_dir, f))

    # 감시 시작
    handler = MeetingFileHandler(
        script_path=script_path, profile=profile,
        extra_args=extra_args or [], move_after=move_after, notify=notify,
    )
    observer = Observer()
    observer.schedule(handler, watch_dir, recursive=False)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("\n  감시 종료")
        observer.stop()
    observer.join()


# ── CLI ───────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="폴더 감시 → 자동 회의록 생성",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예:
  python watcher.py ./recordings
  python watcher.py ./recordings --profile weekly
  python watcher.py ./recordings --profile weekly --notify email
  python watcher.py ./recordings --no-move
        """,
    )
    parser.add_argument("watch_dir", help="감시할 폴더 경로")
    parser.add_argument("--script", default="meeting_minutes.py", help="meeting_minutes.py 경로")
    parser.add_argument("--profile", default="", help="프로필 이름")
    parser.add_argument("--notify", choices=["email", "slack", "teams"],
                        help="완료 알림 채널")
    parser.add_argument("--no-move", action="store_true", help="처리 후 파일 이동 안 함")
    parser.add_argument("--type", choices=["meeting", "seminar", "lecture"],
                        help="문서 타입 (프로필 없을 때)")
    parser.add_argument("--translate", action="store_true", help="영→한 번역")
    parser.add_argument("--ssl-no-verify", action="store_true", help="SSL 우회")

    args = parser.parse_args()

    extra: list[str] = []
    if args.type:
        extra += ["--type", args.type]
    if args.translate:
        extra.append("--translate")
    if args.ssl_no_verify:
        extra.append("--ssl-no-verify")

    run_watcher(
        watch_dir=args.watch_dir,
        script_path=args.script,
        profile=args.profile,
        extra_args=extra,
        move_after=not args.no_move,
        notify=args.notify or "",
    )


if __name__ == "__main__":
    main()
