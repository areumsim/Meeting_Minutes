"""SEC-009 — 로컬 API 접근 통제 회귀 테스트.

배경. 이 앱은 loopback 에 바인딩하는 로컬 도구라 "네트워크에서 안 보이니 안전하다"고
가정해 왔다. 두 가지가 그 가정을 깬다.

1. **CORS 는 WebSocket 에 적용되지 않는다.** `/ws/realtime` 이 Origin 을 보지 않고 accept
   하면, 사용자가 앱을 켜 둔 채 아무 웹페이지를 열기만 해도 그 페이지가
   `ws://127.0.0.1:8501/ws/realtime` 을 열어 실시간 전사(=사용자 키로 과금)를 시작시킬
   수 있다. loopback 바인딩은 막지 못한다 — 브라우저가 사용자 PC 안에서 연결한다.
2. **CORS 는 단순 요청의 전송을 막지 않는다.** 응답을 읽는 것만 막는다. 그래서
   `POST /api/shutdown`·`/api/sessions/clear` 처럼 부수효과만 필요한 요청은 cross-origin
   페이지에서도 성립한다(고전적 CSRF).

여기서 고정하는 것: 허용 목록 판정, 외부 Origin 거부, Origin 없음 허용(CLI), 그리고
허용 목록이 CORS 와 **같은 소스**인지.
"""

import asyncio

import pytest

from web.backend import security as sec


class TestOriginAllowlist:
    @pytest.mark.parametrize("origin", [
        "http://localhost:5173",          # dev Vite
        "http://localhost:8501",
        "http://127.0.0.1:8501",
        "https://localhost",              # iOS(capacitor iosScheme https)
        "capacitor://localhost",
        "ionic://localhost",
    ])
    def test_allows_app_origins(self, origin):
        assert sec.is_allowed_origin(origin) is True

    @pytest.mark.parametrize("origin", [
        "https://evil.example",
        "http://evil.example",
        "http://localhost.evil.example",   # 접두만 같은 도메인
        "http://127.0.0.1.evil.example",
        "https://sub.localhost",           # 서브도메인은 허용 목록이 아니다
        "null",                            # sandboxed iframe
    ])
    def test_rejects_foreign_origins(self, origin):
        assert sec.is_allowed_origin(origin) is False

    def test_missing_origin_is_allowed(self):
        """Origin 없음은 허용한다 — curl·스크립트·앱 WebView 의 정당한 호출이다.

        이 검사의 목적은 '브라우저가 다른 사이트에서 우리 API 를 부르는 것'을 막는
        것이고, 그 경우 브라우저는 항상 Origin 을 붙인다. 없음을 거부하면 CLI 만 깨진다.
        """
        assert sec.is_allowed_origin(None) is True
        assert sec.is_allowed_origin("") is True

    def test_cors_and_endpoint_check_share_one_source(self):
        """허용 목록이 두 곳에 복사되면 한쪽만 고쳐져 갈라진다(이 리포의 반복 사고)."""
        import web.backend.app as appmod
        # app.py 가 security 의 상수를 그대로 CORS 에 넘기는지
        assert sec.ALLOWED_ORIGIN_REGEX in [
            m.kwargs.get("allow_origin_regex")
            for m in appmod.app.user_middleware
            if hasattr(m, "kwargs")
        ]


class TestLoopback:
    @pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
    def test_loopback_hosts(self, host):
        assert sec.is_loopback(host) is True

    @pytest.mark.parametrize("host", ["192.168.0.10", "10.0.0.5", "", None])
    def test_non_loopback(self, host):
        assert sec.is_loopback(host) is False


class _Req:
    """Request 대역 — client.host 와 headers 만 본다."""

    def __init__(self, host="127.0.0.1", origin=None):
        self.client = type("C", (), {"host": host})()
        self.headers = {} if origin is None else {"origin": origin}


class TestRequireLocal:
    def test_passes_for_app_origin(self):
        sec.require_local(_Req(origin="http://localhost:8501"))     # 예외 없음

    def test_passes_without_origin(self):
        sec.require_local(_Req())

    def test_rejects_foreign_origin(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as ei:
            sec.require_local(_Req(origin="https://evil.example"))
        assert ei.value.status_code == 403

    def test_rejects_non_loopback(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as ei:
            sec.require_local(_Req(host="192.168.0.10"))
        assert ei.value.status_code == 403


class _WS:
    def __init__(self, host="127.0.0.1", origin=None):
        self.client = type("C", (), {"host": host})()
        self.headers = {} if origin is None else {"origin": origin}
        self.closed = None
        self.accepted = False

    async def close(self, code=1000, reason=""):
        self.closed = (code, reason)

    async def accept(self):
        self.accepted = True


class TestWebSocketOrigin:
    """pytest-asyncio 를 쓰지 않는다 — 이 리포의 기존 async 테스트와 같이 asyncio.run 이다."""

    def test_rejects_foreign_origin_before_accept(self):
        ws = _WS(origin="https://evil.example")
        assert asyncio.run(sec.ws_reject_foreign_origin(ws)) is True
        assert ws.accepted is False, "accept 후에 닫으면 그 사이 프레임이 처리될 수 있다"
        assert ws.closed[0] == 1008          # policy violation

    def test_allows_app_origin(self):
        ws = _WS(origin="http://localhost:5173")
        assert asyncio.run(sec.ws_reject_foreign_origin(ws)) is False
        assert ws.closed is None

    def test_rejects_non_loopback_client(self):
        ws = _WS(host="192.168.0.10", origin="http://localhost:8501")
        assert asyncio.run(sec.ws_reject_foreign_origin(ws)) is True


class TestShutdownGuards:
    """종료가 인증 없이·진행 중 작업 확인 없이 되던 것을 막는다."""

    def test_rejects_foreign_origin(self):
        from fastapi import HTTPException
        from web.backend.app import shutdown
        with pytest.raises(HTTPException) as ei:
            shutdown(_Req(origin="https://evil.example"))
        assert ei.value.status_code == 403

    def test_conflicts_when_processing(self, monkeypatch):
        from fastapi import HTTPException
        from web.backend import database as db
        from web.backend.app import shutdown
        monkeypatch.setattr(db, "list_sessions",
                            lambda *a, **k: [{"id": "s1", "title": "진행 중 회의",
                                              "status": "processing"}])
        with pytest.raises(HTTPException) as ei:
            shutdown(_Req(origin="http://localhost:8501"))
        assert ei.value.status_code == 409
        assert ei.value.detail["count"] == 1

    def test_force_skips_processing_check(self, monkeypatch):
        """사용자가 화면에서 승인하면 force=true 로 온다 — 그때는 진행해야 한다."""
        from web.backend import database as db
        from web.backend.app import app, shutdown
        monkeypatch.setattr(db, "list_sessions",
                            lambda *a, **k: [{"id": "s1", "status": "processing"}])
        # 실제로 프로세스를 죽이지 않도록 종료 스레드를 막는다
        import threading
        monkeypatch.setattr(threading, "Thread",
                            lambda *a, **k: type("T", (), {"start": lambda self: None})())
        monkeypatch.setattr(app.state, "uvicorn_server", object(), raising=False)
        r = shutdown(_Req(origin="http://localhost:8501"), force=True)
        assert r["ok"] is True
        assert r["graceful"] is True         # 핸들이 있으면 정상 종료 경로
