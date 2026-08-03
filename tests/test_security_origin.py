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

    def __init__(self, host="127.0.0.1", origin=None, host_header=None):
        self.client = type("C", (), {"host": host})()
        self.headers = {}
        if origin is not None:
            self.headers["origin"] = origin
        if host_header is not None:
            self.headers["host"] = host_header


@pytest.fixture
def lan_off(monkeypatch):
    monkeypatch.setattr(sec, "_lan_enabled", lambda: False)


@pytest.fixture
def lan_on(monkeypatch):
    monkeypatch.setattr(sec, "_lan_enabled", lambda: True)


class TestRequireClient:
    def test_passes_for_app_origin(self, lan_off):
        sec.require_client(_Req(origin="http://localhost:8501"))     # 예외 없음

    def test_passes_without_origin(self, lan_off):
        sec.require_client(_Req())

    def test_rejects_foreign_origin(self, lan_off):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as ei:
            sec.require_client(_Req(origin="https://evil.example"))
        assert ei.value.status_code == 403

    def test_rejects_non_loopback_when_lan_off(self, lan_off):
        """기본값(lan_access=false)에서는 loopback 전용 — SEC-009 초판과 같다."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as ei:
            sec.require_client(_Req(host="192.168.0.10"))
        assert ei.value.status_code == 403


class TestLanAccess:
    """`server.lan_access` 를 켠 사용자의 아이폰 앱(PC 연결 모드)이 들어오는 경로.

    회귀 배경: SEC-009 초판이 두 관문 모두 loopback 을 강제해, 그 설정을 켜도
    `/ws/realtime` 이 1008 로 조용히 거부됐다. PRD 는 LAN 모드 유지를 명시한다.
    """

    def test_private_ip_allowed_when_on(self, lan_on):
        sec.require_client(_Req(host="192.168.0.10", origin="capacitor://localhost"))

    @pytest.mark.parametrize("host", ["10.0.0.5", "172.16.0.9", "192.168.1.2"])
    def test_private_ranges(self, lan_on, host):
        assert sec.is_allowed_client_host(host) is True

    @pytest.mark.parametrize("host", ["8.8.8.8", "1.1.1.1", "172.32.0.1"])
    def test_public_ip_still_rejected(self, lan_on, host):
        """켜도 공인 IP 는 받지 않는다. 172.32 는 172.16/12 **밖**이다 —
        문자열 프리픽스 비교로는 틀리는 경계라 ipaddress 로 판정한다.
        (203.0.113.0/24 같은 문서용 대역은 ipaddress 가 private 으로 보므로 예시로 쓰지 않는다.)"""
        assert sec.is_allowed_client_host(host) is False

    def test_same_origin_lan_address_allowed(self, lan_on):
        """`http://192.168.x.x:8501` 로 접속하면 Origin 이 그 주소가 된다 —
        정규식은 localhost 계열만 알기 때문에 Host 와 같은 오리진을 허용한다."""
        assert sec.is_allowed_origin("http://192.168.0.10:8501",
                                     "192.168.0.10:8501") is True

    def test_foreign_origin_not_saved_by_host_match(self, lan_on):
        assert sec.is_allowed_origin("https://evil.example",
                                     "192.168.0.10:8501") is False

    def test_host_match_ignored_when_off(self, lan_off):
        assert sec.is_allowed_origin("http://192.168.0.10:8501",
                                     "192.168.0.10:8501") is False

    def test_ws_accepts_lan_client_when_on(self, lan_on):
        ws = _WS(host="192.168.0.10", origin="capacitor://localhost")
        assert asyncio.run(sec.ws_reject_foreign_origin(ws)) is False


class TestRequireLoopback:
    """비밀 원문·네이티브 대화상자는 `lan_access` 와 무관하게 이 PC 전용이다."""

    def test_rejects_lan_client_even_when_lan_on(self, lan_on):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as ei:
            sec.require_loopback(_Req(host="192.168.0.10", origin="capacitor://localhost"))
        assert ei.value.status_code == 403

    def test_allows_loopback(self, lan_on):
        sec.require_loopback(_Req(origin="http://localhost:8501"))

    def test_reveal_secret_uses_loopback_gate(self, lan_on):
        """폰이 PC 의 실제 키를 빼가지 못하게 하는 것이 이 제한의 목적 자체다."""
        from fastapi import HTTPException
        from web.backend.api.settings import reveal_secret
        with pytest.raises(HTTPException) as ei:
            reveal_secret("api.openai_api_key", _Req(host="192.168.0.10"))
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


class TestBillingEndpointsAreGuarded:
    """과금·자동화 엔드포인트가 관문을 지나는지.

    SEC-009 는 종료·삭제·비밀읽기를 막았지만 **돈이 나가는 경로**를 빠뜨렸다.
    CORS 가 막지 못하는 것은 "단순 요청"인데, 그 정의에 정확히 해당하는 것들이
    남아 있었다(실측: 악성 Origin 으로 reindex·watcher/start·watcher/approve 가 200).

    - `POST /api/upload` — multipart/form-data 는 preflight 가 없다
    - 본문 없는 POST(reindex·watcher/start·watcher/approve)도 preflight 가 없다
      (JSON 본문 엔드포인트는 preflight 가 생겨 CORS 가 실제로 막는다)

    라우트의 의존성을 직접 본다 — 핸들러를 호출하려면 multipart 나 DB 가 필요해
    "관문을 지나는가"만 확인하는 편이 정확하고 깨지지 않는다.
    """

    GUARDED = [
        ("/api/upload", "POST"),
        ("/api/reindex", "POST"),
        ("/api/watcher/start", "POST"),
        ("/api/watcher/approve", "POST"),
    ]

    @pytest.mark.parametrize("path,method", GUARDED)
    def test_route_depends_on_require_client(self, path, method):
        from web.backend.app import app
        matches = [r for r in app.routes
                   if getattr(r, "path", None) == path and method in getattr(r, "methods", ())]
        assert matches, f"{method} {path} 라우트를 찾지 못했다"
        calls = [d.call for d in matches[0].dependant.dependencies]
        assert sec.require_client in calls, (
            f"{method} {path} 가 관문을 지나지 않는다 — 악성 페이지가 과금을 트리거할 수 있다")

    def test_cost_reducing_endpoints_stay_open(self):
        """감시 중지·업로드 취소는 막지 않는다 — 비용을 줄이는 방향이라 막아서 얻는 게 없고,
        취소가 실패하면 오히려 손해다."""
        from web.backend.app import app
        for path in ("/api/watcher/stop",):
            r = [x for x in app.routes if getattr(x, "path", None) == path]
            assert r, f"{path} 라우트를 찾지 못했다"
            assert sec.require_client not in [d.call for d in r[0].dependant.dependencies]


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
