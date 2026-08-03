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


# ── 중복 실행 방지 ────────────────────────────────
#: 프로세스 수명 동안 살아 있어야 하는 락 핸들. 지역 변수로 두면 GC 가 닫아 락이 풀린다.
_LOCK_FH = None

#: 락 파일과 그 옆의 정보 파일. 정보 파일을 분리하는 이유는 락을 건 바이트 영역과
#: 쓰기가 겹치지 않게 하려는 것이다(같은 파일에 쓰면 플랫폼별로 동작이 갈린다).
_LOCK_NAME = ".instance.lock"
_INFO_NAME = ".instance.json"


def _lock_paths(data_dir):
    from pathlib import Path
    d = Path(data_dir)
    return d / _LOCK_NAME, d / _INFO_NAME


def acquire_instance_lock(data_dir) -> Optional[dict]:
    """데이터 폴더 단위 배타 락을 잡는다. **성공하면 None**, 이미 실행 중이면 그 인스턴스 정보.

    왜 포트 검사로 안 되는가 — `find_free_port` 가 점유 시 **다른 포트로 옮기므로**,
    첫 인스턴스가 이미 랜덤 포트에 있을 수 있다. 8501 만 봐서는 못 찾는다. 겹치면 안 되는
    자원은 포트가 아니라 **데이터 폴더**(SQLite·config.json·워처 상태)이므로 거기서 잠근다.

    왜 잔류 락이 문제가 되지 않는가 — 파일 **내용**이 아니라 OS 의 바이트 범위 락이라,
    프로세스가 크래시해도 커널이 핸들을 닫으면서 자동으로 풀린다. (config 저장에 프로세스
    간 잠금을 두지 않기로 한 판단과 상충하지 않는다: 그쪽은 빈번한 짧은 락이고 여기는
    프로세스 수명 동안 핸들 하나다.)

    범위는 **웹 서버 런처 2종**뿐이다. CLI(`batch`·`realtime`)까지 막으면 웹 앱을 켜 둔 채
    CLI 를 쓰는 기존 사용 방식이 깨진다 — 그건 이 P0(같은 앱을 두 번 띄움)이 아니다.
    """
    global _LOCK_FH
    import json
    import os

    lock_path, info_path = _lock_paths(data_dir)
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(lock_path, "a+b")
    except OSError as e:
        print(f"  [중복실행] 락 파일을 열지 못해 검사를 건너뜁니다(무시): {e}")
        return None

    if not _try_lock(fh):
        fh.close()
        info = {}
        try:
            info = json.loads(info_path.read_text(encoding="utf-8"))
        except Exception:
            pass
        return info if isinstance(info, dict) else {}

    _LOCK_FH = fh                      # 프로세스가 끝날 때까지 살려 둔다
    try:
        info_path.write_text(json.dumps({"pid": os.getpid()}, ensure_ascii=False),
                             encoding="utf-8")
    except OSError:
        pass
    return None


def _try_lock(fh) -> bool:
    """비차단 배타 락. 잡았으면 True.

    Windows 는 `msvcrt.LK_NBLCK`(non-blocking 배타)이고 **현재 위치부터** n 바이트를
    잠그므로 seek(0) 이 필요하다. POSIX 상수(LOCK_EX/LOCK_NB)는 msvcrt 에 없다.
    """
    try:
        fh.seek(0)
    except OSError:
        return False
    try:
        import msvcrt                                  # Windows
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        return True
    except ImportError:
        try:
            import fcntl                               # POSIX(개발용 macOS/Linux)
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except Exception:
            return False
    except OSError:
        return False


def publish_instance_port(data_dir, port: int) -> None:
    """락을 잡은 뒤 확정된 포트를 정보 파일에 남긴다.

    두 번째 인스턴스가 "이미 실행 중"을 알리는 것만으로는 부족하다 — 사용자는 앱을 열려고
    두 번 누른 것이므로, **원래 창을 열어 주는 것**이 기대에 맞다. 그 주소가 여기서 온다.
    """
    import json
    import os

    _, info_path = _lock_paths(data_dir)
    try:
        info_path.write_text(
            json.dumps({"pid": os.getpid(), "port": int(port)}, ensure_ascii=False),
            encoding="utf-8")
    except OSError:
        pass


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


def register_shutdown_handle(server) -> bool:
    """uvicorn Server 핸들을 FastAPI app.state 에 심어 /api/shutdown 이 정상 종료를
    요청할 수 있게 한다. 성공하면 True.

    왜 필요한가 — 예전 종료 경로는 `os._exit(0)` 이었다. 그것은 atexit·finally·
    lifespan shutdown 을 모두 건너뛰므로 실시간 세션 정리(스레드풀 shutdown,
    tmpdir 삭제)가 실행되지 않고, 처리 중이던 세션이 DB 에 'processing' 으로 남아
    다음 실행에서 영구 고착됐다.

    폴백(SIGTERM)으로 대신할 수 없다 — **Windows 에서 SIGTERM 은 강제 종료**라
    정상 종료 훅이 돌지 않는다. 그래서 서버 객체를 직접 들고 있어야 한다.

    소스 런처(run_ui.py)와 포터블 런처(run_ui_exe.py)가 같은 함수를 쓴다. 이 등록을
    각자 복사하면 한쪽만 고쳐져 "배포본에서만 종료가 더럽다"가 된다.
    """
    try:
        from web.backend.app import app
        app.state.uvicorn_server = server
        return True
    except Exception as e:                     # pragma: no cover - 방어
        print(f"  [종료] 서버 핸들 등록 실패(무시): {e}")
        return False
