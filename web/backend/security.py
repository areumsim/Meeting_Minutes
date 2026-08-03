"""security.py — 로컬 API 접근 통제 (SEC-009).

이 앱은 기본적으로 loopback 에 바인딩하는 로컬 도구다. 그래서 "네트워크에서 안 보이니
안전하다"고 가정해 왔는데, 두 가지가 그 가정을 깬다.

1. **CORS 는 WebSocket 에 적용되지 않는다.** `/ws/realtime` 이 Origin 을 보지 않고 바로
   accept 하면, 사용자가 앱을 켜 둔 채 아무 웹페이지를 열기만 해도 그 페이지가
   `ws://127.0.0.1:8501/ws/realtime` 을 열어 실시간 전사(=사용자 키로 과금)를 시작시킬 수 있다.
   loopback 바인딩은 이걸 막지 못한다 — 브라우저가 사용자 PC 안에서 연결하기 때문이다.
2. **CORS 는 "단순 요청"의 전송 자체를 막지 않는다.** 응답을 읽는 것만 막는다. 그래서
   `POST /api/shutdown` 이나 `POST /api/upload`(multipart) 처럼 부수효과만 필요한 요청은
   cross-origin 페이지에서도 성립한다(고전적인 CSRF). 앱 종료·기록 삭제·**과금**이 여기 해당한다.

그래서 부수효과가 있거나 비밀을 읽는 엔드포인트는 **Origin 을 직접 검증**한다.
허용 목록은 CORS 미들웨어와 **같은 정규식 하나**를 쓴다 — 두 곳에 복사하면 한쪽만 고쳐져
갈라진다(이 리포에서 반복된 사고 유형이다).

관문이 두 단계인 이유
---------------------
- `require_client()` — 허용된 클라이언트. loopback + (`server.lan_access` 가 켜져 있으면)
  같은 네트워크의 **사설 IP**. 아이폰 앱의 'PC 연결' 모드가 이 경로로 들어온다.
- `require_loopback()` — **이 PC 전용**. 비밀 원문 조회와 네이티브 대화상자처럼, LAN 기기에
  허용하면 그 자체가 목적을 배반하는 것들(폰이 PC 의 실제 키를 빼가거나, 원격 요청이
  서버 화면에 창을 띄우는 것).

`lan_access` 를 보는 이유 — SEC-009 초판은 두 관문 모두 loopback 을 강제했는데, 이 앱은
`server.lan_access=true` 면 0.0.0.0 에 바인딩해 같은 WiFi 의 폰을 받도록 설계돼 있다
(`common/server_launch.py`). 그래서 그 설정을 켠 사용자의 `/ws/realtime` 이 조용히 거부됐다.
정책은 여기 한 곳에서만 판정한다.

실행 세션 토큰(SEC-002)은 별건이다. 그것이 들어오면 이 모듈의 검사에 토큰 확인을 더한다 —
Origin 검증은 그때도 남긴다(토큰이 유출된 경우의 2차 방어).
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, WebSocket

#: 허용 오리진 — CORSMiddleware(app.py)와 이 모듈이 공유하는 단일 소스.
#: localhost 계열(dev Vite:5173 포함) + Capacitor 앱 스킴.
ALLOWED_ORIGIN_REGEX = (
    r"^(https?://(localhost|127\.0\.0\.1)(:\d+)?|capacitor://localhost|ionic://localhost)$"
)
_ORIGIN_RE = re.compile(ALLOWED_ORIGIN_REGEX)

#: loopback 주소. 프록시를 두지 않는 로컬 도구라 X-Forwarded-For 는 신뢰하지 않는다.
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _lan_enabled() -> bool:
    """`server.lan_access` — 설정 조회는 기존 단일 소스를 재사용한다."""
    try:
        from meeting_minutes_app.common import server_launch
        return server_launch.lan_access_enabled()
    except Exception:
        return False          # 판정 실패는 "허용 안 함" 쪽으로 떨어진다


def is_loopback(client_host: str | None) -> bool:
    return (client_host or "") in _LOOPBACK_HOSTS


def _is_private_ip(client_host: str | None) -> bool:
    """사설 IP(RFC1918·링크로컬 등)인가. 공인 IP 는 `lan_access` 를 켜도 허용하지 않는다.

    문자열 프리픽스 비교("192.168." 로 시작하나)를 쓰지 않는 이유는 172.16/12 처럼
    경계가 있는 대역을 틀리게 판정하기 때문이다.
    """
    try:
        return ipaddress.ip_address((client_host or "").split("%")[0]).is_private
    except ValueError:
        return False


def is_allowed_client_host(client_host: str | None) -> bool:
    """이 호스트에서 온 요청을 받아도 되는가.

    기본(`lan_access=false`)은 loopback 전용 — SEC-009 초판과 완전히 같다.
    """
    if is_loopback(client_host):
        return True
    return _lan_enabled() and _is_private_ip(client_host)


def is_allowed_origin(origin: str | None, host_header: str | None = None) -> bool:
    """Origin 헤더가 허용 목록에 드는가.

    Origin 이 **없는** 경우도 허용한다 — curl·앱 내장 WebView·같은 오리진 내비게이션처럼
    브라우저가 Origin 을 붙이지 않는 정당한 호출이 있다. 이 검사의 목적은 "브라우저가
    다른 사이트에서 우리 API 를 부르는 것"을 막는 것이고, 그 경우 브라우저는 항상
    Origin 을 붙인다. Origin 없음을 거부하면 CLI·스크립트 호출만 깨지고 얻는 게 없다.

    `lan_access` 가 켜져 있으면 **요청 Host 와 같은 오리진**도 허용한다 — 폰이나 같은
    WiFi 의 PC 가 `http://192.168.x.x:8501` 로 접속하면 Origin 이 그 주소가 되는데,
    정규식은 localhost 계열만 알기 때문이다. IP 목록을 하드코딩하지 않는 이유는
    사용자 IP 가 DHCP 로 바뀌기 때문이다(같은 오리진 판정이면 공격자가 쓸 수 없다).
    """
    if not origin:
        return True
    if _ORIGIN_RE.match(origin):
        return True
    if host_header and _lan_enabled():
        netloc = urlsplit(origin).netloc.lower()
        return bool(netloc) and netloc == host_header.strip().lower()
    return False


def require_client(request: Request) -> None:
    """부수효과·과금 엔드포인트의 공통 관문.

    FastAPI 의존성으로 쓰거나 핸들러 안에서 직접 부른다. 거부는 403 이다 —
    401 은 "인증하면 된다"는 뜻이라 여기서는 부정확하다.
    """
    if not is_allowed_client_host(request.client.host if request.client else None):
        raise HTTPException(
            status_code=403,
            detail="이 요청은 허용되지 않은 주소에서 왔습니다. "
                   "다른 기기에서 쓰려면 [설정]에서 'LAN 접속 허용'을 켜세요.",
        )
    if not is_allowed_origin(request.headers.get("origin"), request.headers.get("host")):
        raise HTTPException(
            status_code=403,
            detail="허용되지 않은 출처의 요청입니다. 앱 화면에서 다시 시도하세요.",
        )


def require_loopback(request: Request) -> None:
    """**이 PC 전용** 관문 — LAN 기기에 허용하면 목적 자체가 무너지는 엔드포인트용.

    비밀 원문 조회(폰이 PC 의 실제 키를 빼가는 것)와 네이티브 폴더 선택(원격 요청이
    서버 화면에 창을 띄우는 것)이 여기 해당한다. `lan_access` 와 무관하게 loopback 만 받는다.
    """
    if not is_loopback(request.client.host if request.client else None):
        raise HTTPException(
            status_code=403,
            detail="이 요청은 이 PC(로컬)에서만 허용됩니다.",
        )
    if not is_allowed_origin(request.headers.get("origin"), request.headers.get("host")):
        raise HTTPException(
            status_code=403,
            detail="허용되지 않은 출처의 요청입니다. 앱 화면에서 다시 시도하세요.",
        )


async def ws_reject_foreign_origin(ws: WebSocket) -> bool:
    """WebSocket 핸드셰이크 Origin 검증. 거부했으면 True.

    accept() **전에** 닫는다. accept 후에 닫으면 상대는 이미 연결에 성공한 것이고,
    그 사이에 보낸 프레임이 처리될 수 있다.
    1008 = policy violation.
    """
    origin = ws.headers.get("origin")
    host = ws.headers.get("host")
    if (is_allowed_origin(origin, host)
            and is_allowed_client_host(ws.client.host if ws.client else None)):
        return False
    await ws.close(code=1008, reason="origin not allowed")
    return True
