#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
run_ui_exe.py — PyInstaller용 Web UI 엔트리포인트
================================================
exe로 패키징될 때 사용되는 진입점.
- npm/node 의존성 체크 없음 (빌드된 정적 파일 포함)
- 브라우저 자동 열기
- 단일 서버 모드 (프로덕션 전용)
"""

import os
import sys
import argparse
import webbrowser
import time
import threading
from pathlib import Path


def get_base_dir():
    """PyInstaller 번들 또는 개발 환경의 베이스 디렉토리 반환."""
    if getattr(sys, 'frozen', False):
        # PyInstaller 번들: exe가 있는 디렉토리
        return Path(sys.executable).parent
    return Path(__file__).parent


def setup_paths():
    """sys.path 설정 — 기존 모듈 import를 위해."""
    base = get_base_dir()

    # PyInstaller 번들에서는 _MEIPASS 내부의 소스를 사용
    if getattr(sys, 'frozen', False):
        internal = Path(sys._MEIPASS)
    else:
        internal = base

    for p in [str(internal), str(base)]:
        if p not in sys.path:
            sys.path.insert(0, p)

    # config.json, profiles.json 등은 exe 옆에서 읽어야 함
    os.chdir(str(base))

    return base, internal


def open_browser_delayed(port: int, delay: float = 1.5):
    """서버 시작 후 브라우저를 지연 오픈."""
    def _open():
        time.sleep(delay)
        webbrowser.open(f"http://localhost:{port}")
    t = threading.Thread(target=_open, daemon=True)
    t.start()


def main():
    parser = argparse.ArgumentParser(description="Meeting Minutes Web UI")
    parser.add_argument("--port", type=int, default=8501, help="서버 포트 (기본: 8501)")
    parser.add_argument("--host", default="127.0.0.1", help="바인드 호스트 (기본: 127.0.0.1)")
    parser.add_argument("--no-browser", action="store_true", help="브라우저 자동 열기 안 함")
    args = parser.parse_args()

    base_dir, internal_dir = setup_paths()

    print(f"\n{'='*60}")
    print(f"  Meeting Minutes Web UI")
    print(f"  {'─'*56}")
    print(f"  URL: http://localhost:{args.port}")
    print(f"  Base: {base_dir}")
    print(f"{'='*60}\n")

    if not args.no_browser:
        open_browser_delayed(args.port)

    import uvicorn
    uvicorn.run(
        "web.backend.app:app",
        host=args.host,
        port=args.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
