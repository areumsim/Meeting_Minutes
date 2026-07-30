"""
server_launch.py — 웹 UI 런처 공용 유틸 (포트 선택 / 브라우저 열기)
=====================================================================
소스 실행(`run_ui.py`)과 포터블 배포본(`run_ui_exe.py`)이 **같은 규칙**을 쓰도록 여기 한
곳에 둔다. 과거엔 포터블에만 있었고 소스 런처는 8501 고정 + 서버가 뜨기 전에 브라우저를
열어서, 다른 인스턴스가 이미 떠 있으면 **남의 앱 화면을 자기 앱으로 착각**하게 만들었다.

**점유 검사는 와일드카드와 루프백을 모두 본다** — 이것이 사고의 뿌리다.
Windows 는 `0.0.0.0:8501` 바인딩과 `127.0.0.1:8501` 바인딩이 **동시에 성립**하고,
`localhost` 로 들어온 연결은 더 구체적인 바인딩(127.0.0.1)을 가진 프로세스에게 간다.
실제로 포터블(127.0.0.1 기본)과 소스(0.0.0.0 기본)가 8501 에 함께 떠 있었고, 브라우저는
포터블 앱을 보여주는데 소스 서버는 요청을 못 받았다 — 두 앱은 데이터 폴더가 달라
(app_paths.get_base_dir) 사용자에게는 "설정이 전부 사라졌다"로 보였다.

2026-07-30 실측(같은 PC, Windows 11):

    점유자                     bind 127.0.0.1 검사   bind 0.0.0.0 검사
    0.0.0.0 리스닝(소스 기본)   비었다고 오판          점유 탐지
    127.0.0.1 리스닝(포터블)    점유 탐지              비었다고 오판

즉 **한 주소만 검사하면 반드시 한쪽을 놓친다.** 그래서 두 주소를 모두 시도하고, 하나라도
실패하면 점유로 본다. SO_REUSEADDR 는 검사에 쓰지 않는다 — 판정을 바꾸지도 않으면서
Windows 에서는 남의 바인딩을 덮어쓰는 방향이라 검사 목적과 반대다.
"""

from __future__ import annotations

import socket
import threading
import time
import urllib.request
import webbrowser
from typing import Optional

#: 브라우저가 접속하는 주소.
LOOPBACK = "127.0.0.1"
#: 점유 검사에 쓰는 주소들(위 실측 표 참고 — 둘 다 봐야 한다).
_PROBE_HOSTS = ("0.0.0.0", LOOPBACK)


def lan_access_enabled() -> bool:
    """config `server.lan_access` — 같은 WiFi의 모바일 앱 접속을 허용하는지."""
    try:
        from meeting_minutes_app.common import config_loader as _cfg
        return bool(_cfg.get("server.lan_access", False))
    except Exception:
        return False


def resolve_bind_host(explicit: Optional[str] = None) -> str:
    """바인딩 host — 기본은 이 PC 전용(127.0.0.1), `server.lan_access` 일 때만 0.0.0.0.

    두 런처가 같은 규칙을 쓰게 하려고 여기 둔다. 과거 소스 런처는 `--host` 기본값이
    무조건 `0.0.0.0` 이라 **설정과 무관하게 사내망에 웹 UI 가 노출**됐다(회의록·전사 열람,
    업로드=과금 트리거가 인증 없이 가능). 포터블만 lan_access 를 보고 있던 비대칭이다.
    `--host` 를 명시하면 그 값이 우선(디버깅·특수 배치용).
    """
    if explicit:
        return explicit
    return "0.0.0.0" if lan_access_enabled() else LOOPBACK


#: 실시간 녹음에 필요한 websockets 범위(설치 안내·의존성 명세에 같은 문자열을 쓴다).
WS_REQUIREMENT = "websockets>=14,<16"


def ws_decode_supported() -> bool:
    """OpenAI SDK 2.x Realtime 이 쓰는 sync `Connection.recv(decode=False)` 지원 여부.

    websockets 13.x 는 서버가 뜨긴 하지만 녹음 시작 직후 전사가 깨진다. **판정**은 여기
    하나만 두고, 그 뒤 처리는 런처마다 다르다 — 소스 실행은 pip 로 고쳐 주고(개발 편의),
    포터블은 고칠 수 없으니 즉시 실패한다. 판정식이 두 곳에 복사돼 갈라지는 것만 막는다.
    """
    try:
        import inspect
        from websockets.sync.connection import Connection
        return "decode" in inspect.signature(Connection.recv).parameters
    except (ImportError, AttributeError):
        return False


def require_ws_decode_support() -> None:
    """지원되지 않으면 명확한 한국어 메시지로 실패(포터블 런처용 — pip 로 고칠 수 없다)."""
    if not ws_decode_supported():
        raise RuntimeError(
            f"호환되지 않는 websockets 버전입니다. 실시간 녹음에는 {WS_REQUIREMENT} 가 "
            f"필요합니다(13.x 는 녹음 경로가 조용히 깨집니다)."
        )


def _can_bind(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def is_port_free(port: int, hosts: tuple = _PROBE_HOSTS) -> bool:
    """그 포트가 정말 비어 있는지 — 와일드카드/루프백 어느 쪽에도 점유자가 없어야 한다."""
    return all(_can_bind(h, port) for h in hosts)


def find_free_port(preferred: int) -> int:
    """preferred 포트가 비어 있으면 그대로, 아니면 OS가 주는 빈 포트를 사용.

    OS 가 준 포트도 같은 기준으로 재확인한다(와일드카드 할당이라 사실상 비어 있지만,
    검사 기준을 한 곳으로 유지해 '비었다'의 정의가 갈라지지 않게 한다).
    """
    if is_port_free(preferred):
        return preferred
    for _ in range(5):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("0.0.0.0", 0))
            candidate = s.getsockname()[1]
        if is_port_free(candidate):
            return candidate
    return candidate


def probe_instance(port: int, timeout: float = 1.5) -> Optional[dict]:
    """그 포트의 서버가 우리 앱이면 `/api/system/info` 응답(dict), 아니면 None.

    포트 충돌 안내에 "상대가 어느 데이터 폴더를 쓰는 인스턴스인지"를 적기 위한 용도.
    우리 앱이 아니거나 응답하지 않으면 None — 호출부는 '다른 프로그램'으로 안내한다.
    """
    import json
    url = f"http://{LOOPBACK}:{port}/api/system/info"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            data = json.loads(resp.read().decode("utf-8", "replace"))
            return data if isinstance(data, dict) and "base_dir" in data else None
    except Exception:
        return None


def describe_port_holder(port: int) -> str:
    """포트를 점유한 상대를 사람이 읽을 문구로. 안내 메시지에 그대로 넣는다."""
    info = probe_instance(port)
    if not info:
        return "다른 프로그램이 이 포트를 쓰고 있습니다"
    kind = "포터블 배포본" if info.get("mode") == "portable" else "소스 실행"
    return f"이미 실행 중인 Meeting Minutes({kind}, 데이터 폴더: {info.get('base_dir')})"


def open_browser_when_ready(port: int, timeout: float = 30.0,
                            expect_config_path: Optional[str] = None) -> None:
    """`/api/health` 가 응답할 때까지 폴링한 뒤 브라우저를 연다(백그라운드 스레드).

    expect_config_path 를 주면 그 포트의 인스턴스가 **우리 인스턴스인지**(같은 config.json)
    확인한 뒤에만 연다 — 위 docstring 의 이중 바인딩 상황에서 남의 앱을 열지 않기 위함이다.
    확인에 실패하면 열지 않고 이유를 출력한다(사용자가 잘못된 화면을 보는 것보다 낫다).
    """
    def _wait_and_open() -> None:
        url = f"http://localhost:{port}"
        health = f"{url}/api/health"
        deadline = time.time() + timeout
        ready = False
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(health, timeout=1) as resp:
                    if resp.status == 200:
                        ready = True
                        break
            except Exception:
                time.sleep(0.4)
        if not ready:
            print(f"  [브라우저] 서버 응답이 없어 자동으로 열지 않았습니다 — {url} 을 직접 열어 보세요.")
            return
        if expect_config_path:
            info = probe_instance(port)
            mine = str(expect_config_path)
            theirs = str((info or {}).get("config_path") or "")
            if theirs and theirs.lower() != mine.lower():
                print(f"  [브라우저] {url} 은 다른 인스턴스입니다(설정: {theirs}) — "
                      f"자동으로 열지 않았습니다.")
                return
        webbrowser.open(url)

    threading.Thread(target=_wait_and_open, daemon=True).start()
