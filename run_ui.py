#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
run_ui.py — Web UI 서버 런처
================================
FastAPI + React 기반 웹 UI를 시작합니다.

사용법:
    python run_ui.py              # 프로덕션 모드 (빌드된 정적 파일)
    python run_ui.py --dev        # 개발 모드 (Vite dev server + FastAPI)
    python run_ui.py --port 8080  # 포트 변경
"""

import os
import sys
import subprocess
import argparse
import webbrowser
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
WEB_DIR = SCRIPT_DIR / "web"
FRONTEND_DIR = WEB_DIR / "frontend"
DIST_DIR = FRONTEND_DIR / "dist"


def check_python_deps():
    """Python 의존성 확인 및 설치."""
    required = ["fastapi", "uvicorn", "python-multipart"]
    missing = []
    for pkg in required:
        mod = pkg.replace("-", "_")
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)

    if missing:
        print(f"\n  필요한 패키지 설치: {', '.join(missing)}")
        subprocess.check_call([
            sys.executable, "-m", "pip", "install",
            *missing, "uvicorn[standard]",
        ])
        print("  설치 완료.\n")


def check_node_deps():
    """Node.js 의존성 확인."""
    node_modules = FRONTEND_DIR / "node_modules"
    if not node_modules.exists():
        print("\n  프론트엔드 의존성 설치 중...")
        subprocess.check_call(["npm", "install"], cwd=str(FRONTEND_DIR), shell=True)
        print("  설치 완료.\n")


def build_frontend():
    """프론트엔드 빌드."""
    if not DIST_DIR.exists():
        print("\n  프론트엔드 빌드 중...")
        check_node_deps()
        subprocess.check_call(["npm", "run", "build"], cwd=str(FRONTEND_DIR), shell=True)
        print("  빌드 완료.\n")


def main():
    parser = argparse.ArgumentParser(description="Meeting Minutes Web UI")
    parser.add_argument("--dev", action="store_true", help="개발 모드 (Vite dev + FastAPI)")
    parser.add_argument("--port", type=int, default=8501, help="서버 포트 (기본: 8501)")
    parser.add_argument("--host", default="0.0.0.0", help="바인드 호스트 (기본: 0.0.0.0)")
    parser.add_argument("--no-browser", action="store_true", help="브라우저 자동 열기 안 함")
    args = parser.parse_args()

    # 의존성 체크
    check_python_deps()

    # ar_transcription 루트를 sys.path에 추가
    if str(SCRIPT_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPT_DIR))

    if args.dev:
        # 개발 모드: Vite dev server + FastAPI 동시 실행
        check_node_deps()
        print(f"\n{'='*60}")
        print(f"  Meeting Minutes Web UI (Development)")
        print(f"  {'─'*56}")
        print(f"  Frontend: http://localhost:5173")
        print(f"  Backend:  http://localhost:{args.port}")
        print(f"{'='*60}\n")

        # Vite dev server 백그라운드 실행
        vite_proc = subprocess.Popen(
            ["npm", "run", "dev"],
            cwd=str(FRONTEND_DIR),
            shell=True,
        )

        if not args.no_browser:
            time.sleep(2)
            webbrowser.open(f"http://localhost:5173")

        try:
            import uvicorn
            uvicorn.run(
                "web.backend.app:app",
                host=args.host,
                port=args.port,
                reload=True,
                reload_dirs=[str(WEB_DIR / "backend")],
            )
        finally:
            vite_proc.terminate()
    else:
        # 프로덕션 모드
        build_frontend()

        print(f"\n{'='*60}")
        print(f"  Meeting Minutes Web UI")
        print(f"  {'─'*56}")
        print(f"  URL: http://localhost:{args.port}")
        print(f"{'='*60}\n")

        if not args.no_browser:
            time.sleep(1)
            webbrowser.open(f"http://localhost:{args.port}")

        import uvicorn
        uvicorn.run(
            "web.backend.app:app",
            host=args.host,
            port=args.port,
        )


if __name__ == "__main__":
    main()
