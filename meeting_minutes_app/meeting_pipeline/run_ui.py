#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
run_ui.py — Web UI 서버 런처
================================
FastAPI + React 기반 웹 UI를 시작합니다.

사용법:
    python run_meeting.py web              # 프로덕션 모드 (빌드된 정적 파일)
    python run_meeting.py web --dev        # 개발 모드 (Vite dev server + FastAPI)
    python run_meeting.py web --port 8080  # 포트 변경
"""

import sys
import subprocess
import argparse
import shutil
import webbrowser
import time
from pathlib import Path

from meeting_minutes_app.common.console import force_utf8_console
force_utf8_console()

APP_DIR = Path(__file__).resolve().parent.parent  # meeting_minutes_app/
PROJECT_ROOT = APP_DIR.parent                       # repo root
WEB_DIR = PROJECT_ROOT / "web"
FRONTEND_DIR = WEB_DIR / "frontend"
DIST_DIR = FRONTEND_DIR / "dist"

# Windows에서 실제 실행 파일은 npm.exe가 아니라 npm.cmd라서, shell=True 없이
# subprocess.check_call(["npm", ...])을 그대로 쓰면 CreateProcess가 확장자를
# 못 찾아 FileNotFoundError([WinError 2])가 난다. shutil.which로 미리 실제
# 경로(...\npm.cmd)를 resolve해두면 크로스플랫폼으로 안전하게 호출된다.
NPM_CMD = shutil.which("npm") or "npm"


def check_python_deps():
    """Python 의존성 확인 및 설치."""
    # websockets가 없으면 uvicorn은 서버 자체는 정상 기동하지만 WebSocket
    # Upgrade 요청을 일반 HTTP로 넘겨 /ws/realtime에 404를 반환한다. 13.x도
    # OpenAI SDK 2.x가 쓰는 sync recv(decode=False)를 지원하지 않아 연결 직후
    # 실시간 전사가 깨진다. 두 경우를 모두 설치 단계에서 차단한다.
    required = ["fastapi", "uvicorn", "python-multipart"]
    missing = []
    for pkg in required:
        mod = pkg.replace("-", "_")
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)

    # 판정은 공용 함수 하나(server_launch.ws_decode_supported) — 포터블 런처도 같은 식을
    # 쓴다. 다만 여기서는 실패시키지 않고 pip 로 고쳐 준다(개발 편의).
    from meeting_minutes_app.common import server_launch
    if not server_launch.ws_decode_supported():
        missing.append(server_launch.WS_REQUIREMENT)

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
        subprocess.check_call([NPM_CMD, "install"], cwd=str(FRONTEND_DIR))
        print("  설치 완료.\n")


def dist_csp_profile() -> str:
    """`dist/index.html` 이 어느 CSP 프로파일로 빌드됐는지 — "packaged"|"standalone"|"".

    왜 필요한가 — **iOS 빌드와 PC 빌드가 같은 `dist/` 에 쓴다**
    (`npm run build` = packaged / `npm run build:standalone` = standalone, vite 의
    기본 outDir 하나). 그래서 아이폰용으로 한 번 빌드하면(`npm run ios:sync`) 리포의
    `dist/` 가 standalone 번들로 바뀌는데, 그 프로파일의 `connect-src` 는 **임의 호스트**
    (`http: https: ws: wss:`)를 허용한다. PC 웹 UI 는 같은 `dist/` 를 서빙하므로,
    아래 mtime 비교만으로는 "최신이니 스킵"이 되어 **좁혀 둔 CSP 가 조용히 풀린 채**
    돌아간다(vite.config.ts 의 프로파일 주석이 경고하는 바로 그 상황인데, 지금까지
    이를 막는 코드는 없었다).

    판정은 `connect-src` 지시자가 `'self'` **하나뿐인가**로 한다 — 프로파일 문자열의
    단일 소스는 `web/frontend/vite.config.ts` 의 `CSP_CONNECT` 이고, 이 함수는 그
    결과물을 읽을 뿐이다. 읽을 수 없거나 CSP 가 없으면 ""(알 수 없음)이고, 호출부는
    그 경우 다시 빌드한다(안전한 쪽).
    """
    import re

    try:
        html = (DIST_DIR / "index.html").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    m = re.search(r"connect-src([^;\"']*(?:'[^']*'[^;\"]*)*);", html)
    if not m:
        return ""
    sources = m.group(1).split()
    return "packaged" if sources == ["'self'"] else "standalone"


def build_frontend():
    """프론트엔드 빌드 (항상 최신 상태로)."""
    check_node_deps()

    # src 파일이 dist보다 새로우면 빌드 (dist 없으면 무조건 빌드)
    src_dir = FRONTEND_DIR / "src"
    needs_build = not DIST_DIR.exists()
    if not needs_build and src_dir.exists():
        dist_mtime = max((f.stat().st_mtime for f in DIST_DIR.rglob("*") if f.is_file()), default=0)
        src_mtime  = max((f.stat().st_mtime for f in src_dir.rglob("*")  if f.is_file()), default=0)
        needs_build = src_mtime > dist_mtime
    # 프로파일이 packaged 가 아니면 mtime 과 무관하게 다시 빌드한다(위 docstring).
    profile = "" if needs_build else dist_csp_profile()
    if not needs_build and profile != "packaged":
        print(f"  [알림] 현재 dist 는 PC 용 번들이 아닙니다(CSP 프로파일: "
              f"{profile or '알 수 없음'}) — 다시 빌드합니다.")
        needs_build = True

    if needs_build:
        print("\n  프론트엔드 빌드 중...")
        subprocess.check_call([NPM_CMD, "run", "build"], cwd=str(FRONTEND_DIR))
        print("  빌드 완료.\n")
    else:
        print("  프론트엔드 최신 상태 (빌드 스킵)\n")


def main():
    parser = argparse.ArgumentParser(description="Meeting Minutes Web UI")
    parser.add_argument("--dev", action="store_true", help="개발 모드 (Vite dev + FastAPI)")
    parser.add_argument("--port", type=int, default=8501, help="서버 포트 (기본: 8501)")
    parser.add_argument("--host", default=None,
                        help="바인드 호스트 (기본: 127.0.0.1, server.lan_access=true 면 0.0.0.0)")
    parser.add_argument("--no-browser", action="store_true", help="브라우저 자동 열기 안 함")
    args = parser.parse_args()

    # 의존성 체크
    check_python_deps()

    # 프로젝트 루트와 구현 모듈 경로를 sys.path에 추가
    for path in (PROJECT_ROOT, APP_DIR):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))

    from meeting_minutes_app.common import app_paths, server_launch

    # 바인딩 host 는 포터블 런처와 같은 규칙을 쓴다 — 기본은 이 PC 전용(127.0.0.1)이고
    # config server.lan_access=true 일 때만 0.0.0.0. 과거 이 런처만 기본값이 0.0.0.0 이라
    # 설정과 무관하게 사내망에 웹 UI 가 노출됐다(회의록·전사 열람, 업로드=과금 트리거가
    # 인증 없이 가능). --host 를 명시하면 그 값이 우선.
    host = server_launch.resolve_bind_host(args.host)

    if args.dev:
        # 개발 모드: Vite dev server + FastAPI 동시 실행
        # 여기서는 포트를 절대 바꾸지 않는다 — web/frontend/vite.config.ts 의 프록시가
        # localhost:8501 로 하드코딩돼 있어, 백엔드만 다른 포트로 옮기면 프록시가 조용히
        # 깨진다(화면은 뜨는데 모든 /api 요청이 실패). 점유 시엔 분명히 실패시킨다.
        #
        # 프로덕션 인스턴스가 8501 을 쥐고 있는 경우가 이 실패의 대부분이다 — 개발 모드는
        # 포트를 옮길 수 없으니 **여기서도 자동 종료를 지난다**(런처 정책은 하나다).
        # 다만 개발 서버는 목록에 등록하지 않는다(`publish_instance_port` 를 부르지 않는다):
        # `reload=True` 는 uvicorn 리로더가 자식 프로세스를 다시 띄우는 구조라, 다른 런처가
        # /api/shutdown 으로 자식을 내려도 부모가 곧 새로 띄워 '끈 것처럼 보이지만 살아
        # 있는' 상태가 된다. 개발 서버는 개발자가 직접 끄는 것이 맞다.
        _dev_takeover = server_launch.stop_other_instances()
        if _dev_takeover.get("busy"):
            print("\n  [알림] 진행 중인 회의가 있는 인스턴스가 떠 있어 그대로 두었습니다.")
            server_launch.open_existing(_dev_takeover["busy"])
            sys.exit(0)
        server_launch.wait_port_free(args.port)
        if not server_launch.is_port_free(args.port):
            print(f"\n  [오류] 포트 {args.port} 가 이미 사용 중입니다 — "
                  f"{server_launch.describe_port_holder(args.port)}.")
            print("         개발 모드는 프록시 때문에 포트를 바꿀 수 없습니다. 그 인스턴스를"
                  " 종료한 뒤 다시 실행하세요.")
            print("         (설정 화면의 [설정] → [앱 종료] 또는 작업 관리자에서 python/pythonw 종료)")
            sys.exit(1)
        check_node_deps()
        print(f"\n{'='*60}")
        print(f"  Meeting Minutes Web UI (Development)")
        print(f"  {'─'*56}")
        print(f"  Frontend: http://localhost:5173")
        print(f"  Backend:  http://localhost:{args.port}")
        print(f"{'='*60}\n")

        # Vite dev server 백그라운드 실행
        vite_proc = subprocess.Popen(
            [NPM_CMD, "run", "dev"],
            cwd=str(FRONTEND_DIR),
        )

        if not args.no_browser:
            time.sleep(2)
            webbrowser.open(f"http://localhost:5173")

        try:
            import uvicorn
            uvicorn.run(
                "web.backend.app:app",
                host=host,
                port=args.port,
                reload=True,
                reload_dirs=[str(WEB_DIR / "backend")],
                # 개발 모드도 배포본과 같은 WebSocket 구현을 사용해 녹음 경로의
                # 동작 차이와 의존성 누락을 즉시 드러낸다.
                ws="websockets",
            )
        finally:
            vite_proc.terminate()
    else:
        # 프로덕션 모드
        build_frontend()

        # 포트가 점유돼 있으면 빈 포트로 옮긴다. 그냥 8501 로 바인딩하면 Windows 에서는
        # 0.0.0.0 바인딩이 남의 127.0.0.1 바인딩과 공존해 버려서, 브라우저(localhost)는
        # 남의 앱을 보고 이 서버는 요청을 못 받는 상태가 된다 — 데이터 폴더가 다른 두
        # 인스턴스일 때 "내 설정이 사라졌다"로 오해하게 만든 원인이다(server_launch 참고).
        # 이제 그 폴백은 **남의 프로그램**이 8501 을 쥔 경우에만 발동한다 — 우리 인스턴스는
        # 아래에서 먼저 내리기 때문이다.
        _data_base = app_paths.get_base_dir()
        # 이 런처를 눌렀다 = "지금 이걸 쓰겠다". 앞서 떠 있던 인스턴스(포터블이든 소스든)를
        # 끄고 자리를 넘겨받는다 — 그래야 주소가 8501 하나로 고정된다. 창을 닫아도 서버가
        # 남는 일이 있어(콘솔 종료가 손자 프로세스까지 못 죽인다) 어제 띄운 서버가 오늘
        # 8501 을 쥐고 있는 상황이 실제로 반복됐다. 유일한 예외는 진행 중 회의다.
        _takeover = server_launch.stop_other_instances()
        if _takeover.get("busy"):
            print("\n  [알림] 진행 중인 회의가 있는 인스턴스가 떠 있어 그대로 두었습니다.")
            server_launch.open_existing(_takeover["busy"])
            sys.exit(0)

        running = server_launch.acquire_instance_lock(_data_base)
        if running is not None:
            # 자동 종료가 실패했을 때의 마지막 방어선(두 서버가 같은 폴더에 뜨면 워처가
            # 중복 과금하고 진행 중 세션이 error 로 표시된다).
            print(f"\n  [오류] 같은 데이터 폴더로 이미 실행 중이고 자동 종료도 실패했습니다: {_data_base}")
            server_launch.open_existing(running)
            sys.exit(1)

        # 방금 내린 인스턴스가 쥐고 있던 포트가 닫히기를 기다린다 — 안 기다리면
        # find_free_port 가 '점유'로 보고 또 랜덤 포트로 옮겨 앉는다.
        server_launch.wait_port_free(args.port)
        port = server_launch.find_free_port(args.port)
        server_launch.publish_instance_port(_data_base, port)
        if port != args.port:
            print(f"\n  [알림] 포트 {args.port} 는 "
                  f"{server_launch.describe_port_holder(args.port)} —")
            print(f"         이 창은 {port} 번 포트로 띄웁니다.")

        print(f"\n{'='*60}")
        print(f"  Meeting Minutes Web UI")
        print(f"  {'─'*56}")
        print(f"  URL: http://localhost:{port}")
        print(f"  데이터 폴더: {app_paths.get_base_dir()}")
        if host == "0.0.0.0":
            print(f"  LAN 접속 허용됨 (server.lan_access=true) — 같은 WiFi에서 접속 가능")
        print(f"{'='*60}\n")

        if not args.no_browser:
            # 서버가 실제로 응답한 뒤에(그리고 그 응답이 이 인스턴스일 때만) 브라우저를
            # 연다 — 과거엔 바인딩 전에 열어서 남의 앱 화면을 보여줬다.
            server_launch.open_browser_when_ready(
                port, expect_config_path=str(app_paths.get_config_path()))

        import uvicorn
        # uvicorn.run() 대신 Server 를 직접 만든다 — 그래야 /api/shutdown 이 정상 종료를
        # 요청할 수 있다(should_exit). 예전에는 종료가 os._exit(0) 이라 lifespan shutdown 을
        # 건너뛰어 실시간 세션 정리가 실행되지 않고 처리 중 세션이 'processing' 으로
        # 영구 고착됐다. Windows 에서는 SIGTERM 이 강제 종료라 그 폴백만으로는 부족하다.
        config = uvicorn.Config(
            "web.backend.app:app",
            host=host,
            port=port,
            # auto는 구현 패키지가 빠졌을 때 HTTP-only로 조용히 기동한다.
            # 녹음 필수 기능이므로 명시적으로 선택해 누락을 즉시 드러낸다.
            ws="websockets",
        )
        server = uvicorn.Server(config)
        server_launch.register_shutdown_handle(server)
        server.run()


if __name__ == "__main__":
    main()
