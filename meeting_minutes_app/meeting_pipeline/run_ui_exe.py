#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
run_ui_exe.py — PyInstaller용 Web UI 엔트리포인트
================================================
exe로 패키징될 때 사용되는 진입점.
- npm/node/pip 의존성 체크 없음 (빌드된 정적 파일·번들 의존성 사용)
- 쓰기 가능한 데이터 폴더(MeetingMinutesData/) 자동 생성 + config 시드
- 빈 포트 자동 선택 → 서버 준비 확인 후 기본 브라우저 자동 오픈
- 단일 서버 모드 (프로덕션 전용)
"""

import os
import sys
import socket
import argparse
from pathlib import Path

# console=False(windowed) 빌드에서는 sys.stdout/stderr 가 None 이라 print/uvicorn 로깅이
# 깨진다. 우선 devnull 로 채워 크래시를 막고, 데이터 폴더를 안 뒤 로그파일로 재지정한다.
_WINDOWED = getattr(sys, "stdout", None) is None


def _fill_std_streams(target=None):
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            try:
                setattr(sys, name, target or open(os.devnull, "w"))
            except Exception:
                pass


_fill_std_streams()


def setup_paths():
    """sys.path 설정 + 데이터 폴더 초기화 + 데이터 베이스로 chdir.

    반환: (data_base, resource_root)
    """
    # 번들 리소스(_MEIPASS) 또는 저장소 루트를 sys.path에 추가해 기존 모듈 import 지원
    if getattr(sys, 'frozen', False):
        resource_root = Path(sys._MEIPASS)
    else:
        resource_root = Path(__file__).resolve().parent.parent.parent
    app_dir = resource_root / "meeting_minutes_app"
    for p in [str(app_dir), str(resource_root)]:
        if p not in sys.path:
            sys.path.insert(0, p)

    from meeting_minutes_app.common import app_paths

    # 데이터 폴더 생성 + config.json 시드(없을 때만)
    data_base = app_paths.ensure_base_dir()

    # config.json/data/·./output 등 상대경로가 데이터 폴더 기준으로 해석되도록 chdir
    os.chdir(str(data_base))
    return data_base, resource_root


def _lan_ipv4_addrs() -> list:
    """이 PC의 LAN IPv4 주소 목록(모바일 앱 접속 안내용). loopback 제외."""
    ips = []
    try:
        # 기본 라우트로 나가는 소켓의 로컬 주소 → 실제 사용 중인 LAN IP
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ips.append(s.getsockname()[0])
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    return ips



def main():
    parser = argparse.ArgumentParser(description="Meeting Minutes Web UI")
    parser.add_argument("--port", type=int, default=8501, help="서버 포트 (기본: 8501)")
    parser.add_argument("--host", default=None,
                        help="바인드 호스트 (기본: 127.0.0.1, server.lan_access=true 면 0.0.0.0)")
    parser.add_argument("--no-browser", action="store_true", help="브라우저 자동 열기 안 함")
    args = parser.parse_args()

    data_base, _ = setup_paths()

    # windowed 빌드: 콘솔이 없으므로 stdout/stderr(및 uvicorn 로그)를 로그파일로 보낸다.
    if _WINDOWED:
        try:
            logdir = Path(data_base) / "data" / "logs"
            logdir.mkdir(parents=True, exist_ok=True)
            # buffering=1 → 라인 버퍼링. 콘솔이 없는 windowed 빌드에서 stdout 이
            # 블록 버퍼(~8KB)에 쌓여 오류가 로그에 늦게(또는 크래시 시 아예 안) 남던
            # 문제 방지 — 사용자 문제 신고 시 로그 근거 확보를 위해 줄 단위 flush.
            logf = open(logdir / "web_exe.log", "a", encoding="utf-8",
                        errors="replace", buffering=1)
            sys.stdout = logf
            sys.stderr = logf
        except Exception:
            pass

    # 포트 선택·브라우저 열기 규칙은 소스 런처(run_ui.py)와 공용 모듈 하나를 쓴다 —
    # 과거엔 이 파일에만 있어 소스 런처가 8501 고정 + 바인딩 전 브라우저 열기로 갈라졌다.
    from meeting_minutes_app.common import server_launch
    port = server_launch.find_free_port(args.port)

    # LAN 접속 허용(config server.lan_access=true) 시 0.0.0.0 으로 바인딩해 같은 WiFi의
    # 아이폰·태블릿 앱이 접속할 수 있게 한다. 기본은 localhost 전용(안전).
    # 판정은 소스 런처와 공용 규칙 하나를 쓴다(server_launch.resolve_bind_host).
    lan_access = server_launch.lan_access_enabled()
    host = server_launch.resolve_bind_host(args.host)

    lan_ips = _lan_ipv4_addrs() if lan_access else []

    print(f"\n{'='*60}")
    print(f"  Meeting Minutes Web UI")
    print(f"  {'-'*56}")
    print(f"  서버 실행 중...  http://localhost:{port}")
    if lan_access:
        if lan_ips:
            print(f"  {'-'*56}")
            print(f"  모바일 앱(같은 WiFi)에서 접속할 주소:")
            for ip in lan_ips:
                print(f"    http://{ip}:{port}")
        else:
            print(f"  (LAN 접속 허용됨 — PC의 ipconfig IPv4 주소 + :{port} 로 접속)")
    print(f"  데이터 위치: {data_base}")
    print(f"  {'-'*56}")
    print(f"  브라우저가 자동으로 열립니다.")
    print(f"  종료: 웹 화면 [설정] → '앱 종료' (또는 작업관리자에서 MeetingMinutes 종료)")
    print(f"{'='*60}\n")

    if not args.no_browser:
        # expect_config_path: 그 포트가 우리 인스턴스일 때만 연다(데이터 폴더가 다른
        # 다른 인스턴스 화면을 열어 "설정이 사라졌다"로 오해하는 것을 막는다).
        from meeting_minutes_app.common import app_paths as _ap
        server_launch.open_browser_when_ready(
            port, expect_config_path=str(_ap.get_config_path()))

    # websockets 13.x로 잘못 빌드된 실행본이 잠깐 ready를 보낸 뒤 폴백하는 반쪽 동작을
    # 허용하지 않고 시작 단계에서 명확히 실패시킨다(판정은 소스 런처와 공용 함수 하나).
    server_launch.require_ws_decode_support()

    import uvicorn
    # Server 를 직접 만들어 /api/shutdown 이 정상 종료를 요청할 수 있게 한다
    # (소스 런처 run_ui.py 와 같은 이유·같은 함수 — 판정을 복사하지 않는다).
    config = uvicorn.Config(
        "web.backend.app:app",
        host=host,
        port=port,
        log_level="info",
        # WebSocket 구현 누락 시 HTTP-only로 조용히 기동해 녹음만 404가 되는
        # 상태를 허용하지 않는다. build_exe.bat도 빌드 전에 같은 의존성을 검사한다.
        ws="websockets",
    )
    server = uvicorn.Server(config)
    server_launch.register_shutdown_handle(server)
    server.run()


if __name__ == "__main__":
    main()
