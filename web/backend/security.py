"""security.py — 로컬 API 접근 통제 (SEC-009).

이 앱은 loopback 에 바인딩하는 로컬 도구다. 그래서 "네트워크에서 안 보이니 안전하다"고
가정해 왔는데, 두 가지가 그 가정을 깬다.

1. **CORS 는 WebSocket 에 적용되지 않는다.** `/ws/realtime` 이 Origin 을 보지 않고 바로
   accept 하면, 사용자가 앱을 켜 둔 채 아무 웹페이지를 열기만 해도 그 페이지가
   `ws://127.0.0.1:8501/ws/realtime` 을 열어 실시간 전사(=사용자 키로 과금)를 시작시킬 수 있다.
   loopback 바인딩은 이걸 막지 못한다 — 브라우저가 사용자 PC 안에서 연결하기 때문이다.
2. **CORS 는 "단순 요청"의 전송 자체를 막지 않는다.** 응답을 읽는 것만 막는다. 그래서
   `POST /api/shutdown` 처럼 부수효과만 필요한 요청은 cross-origin 페이지에서도 성립한다
   (고전적인 CSRF). 앱 종료·기록 삭제가 여기 해당한다.

그래서 부수효과가 있거나 비밀을 읽는 엔드포인트는 **Origin 을 직접 검증**한다.
허용 목록은 CORS 미들웨어와 **같은 정규식 하나**를 쓴다 — 두 곳에 복사하면 한쪽만 고쳐져
갈라진다(이 리포에서 반복된 사고 유형이다).

실행 세션 토큰(SEC-002)은 별건이다. 그것이 들어오면 이 모듈의 검사에 토큰 확인을 더한다 —
Origin 검증은 그때도 남긴다(토큰이 유출된 경우의 2차 방어).
"""

from __future__ import annotations

import re

from fastapi import HTTPException, Request, WebSocket

#: 허용 오리진 — CORSMiddleware(app.py)와 이 모듈이 공유하는 단일 소스.
#: localhost 계열(dev Vite:5173 포함) + Capacitor 앱 스킴.
ALLOWED_ORIGIN_REGEX = (
    r"^(https?://(localhost|127\.0\.0\.1)(:\d+)?|capacitor://localhost|ionic://localhost)$"
)
_ORIGIN_RE = re.compile(ALLOWED_ORIGIN_REGEX)

#: loopback 주소. 프록시를 두지 않는 로컬 도구라 X-Forwarded-For 는 신뢰하지 않는다.
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def is_allowed_origin(origin: str | None) -> bool:
    """Origin 헤더가 허용 목록에 드는가.

    Origin 이 **없는** 경우도 허용한다 — curl·앱 내장 WebView·같은 오리진 내비게이션처럼
    브라우저가 Origin 을 붙이지 않는 정당한 호출이 있다. 이 검사의 목적은 "브라우저가
    다른 사이트에서 우리 API 를 부르는 것"을 막는 것이고, 그 경우 브라우저는 항상
    Origin 을 붙인다. Origin 없음을 거부하면 CLI·스크립트 호출만 깨지고 얻는 게 없다.
    """
    if not origin:
        return True
    return bool(_ORIGIN_RE.match(origin))


def is_loopback(client_host: str | None) -> bool:
    return (client_host or "") in _LOOPBACK_HOSTS


def require_local(request: Request) -> None:
    """부수효과·비밀 읽기 엔드포인트의 공통 관문.

    FastAPI 의존성으로 쓰거나 핸들러 안에서 직접 부른다. 거부는 403 이다 —
    401 은 "인증하면 된다"는 뜻이라 여기서는 부정확하다.
    """
    if not is_loopback(request.client.host if request.client else None):
        raise HTTPException(
            status_code=403,
            detail="이 요청은 이 PC(로컬)에서만 허용됩니다.",
        )
    if not is_allowed_origin(request.headers.get("origin")):
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
    if is_allowed_origin(origin) and is_loopback(ws.client.host if ws.client else None):
        return False
    await ws.close(code=1008, reason="origin not allowed")
    return True
