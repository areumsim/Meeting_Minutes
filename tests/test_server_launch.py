"""
런처 포트 선택 / 브라우저 열기 / 인스턴스 식별.

[실전 버그 2026-07-30] 포터블 배포본(데이터=MeetingMinutesData)이 8501 을 잡고 있는 상태에서
webUI_실행.bat(데이터=리포 루트)을 실행하자, 브라우저에 **포터블 앱**이 떠서 사용자에게는
"Obsidian·볼트·API 키가 전부 사라졌다"로 보였다. 원인 둘:
  (1) run_ui.py 가 uvicorn 바인딩 **전에** webbrowser.open 을 호출했다.
  (2) Windows 는 0.0.0.0 바인딩과 127.0.0.1 바인딩이 공존하고, localhost 연결은 더 구체적인
      쪽(127.0.0.1)으로 간다 → 두 서버가 같은 포트에 동시에 살아 있었다.
포터블 런처는 이미 올바른 헬퍼를 갖고 있었으므로(갈라진 상태), 공용 모듈로 수렴시켰다.
"""

import socket
import sys
import types

import pytest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from meeting_minutes_app.common import server_launch as sl  # noqa: E402


def _listen(host: str):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind((host, 0))
    s.listen(1)
    return s, s.getsockname()[1]


@pytest.fixture()
def occupied_port():
    """127.0.0.1 의 한 포트를 실제로 점유한 뒤 포트 번호를 준다(포터블 앱과 같은 형태)."""
    s, port = _listen("127.0.0.1")
    try:
        yield port
    finally:
        s.close()


@pytest.fixture()
def occupied_port_wildcard():
    """0.0.0.0 에 리스닝하는 점유자(소스 런처 기본 host 와 같은 형태)."""
    s, port = _listen("0.0.0.0")
    try:
        yield port
    finally:
        s.close()


class TestPortSelection:
    """주의: '방금 비운 포트가 여전히 비어 있다'에 의존하는 단정은 넣지 않는다.
    포트를 놓는 순간 이 PC 의 다른 프로세스(브라우저·개발 서버)가 그 번호를 가져갈 수 있어
    실제로 플래키였다 — 비어 있음/점유됨 분기는 모킹으로 결정적으로 검증한다."""

    def test_free_port_is_returned_as_is(self, monkeypatch):
        monkeypatch.setattr(sl, "is_port_free", lambda *_a, **_k: True)
        assert sl.find_free_port(8501) == 8501

    def test_occupied_port_falls_back_to_another(self, occupied_port):
        # 실제로 listen 중인 소켓 → 점유 판정은 결정적이다
        assert sl.is_port_free(occupied_port) is False
        assert sl.find_free_port(occupied_port) != occupied_port

    def test_detects_loopback_holder(self, occupied_port):
        """포터블 앱처럼 127.0.0.1 에만 리스닝하는 점유자를 잡는다."""
        assert sl.is_port_free(occupied_port) is False

    def test_detects_wildcard_holder(self, occupied_port_wildcard):
        """[회귀] 0.0.0.0 리스닝 점유자도 잡아야 한다.

        Windows 는 0.0.0.0 이 점유된 포트에 127.0.0.1 바인딩을 **허용**한다(실측).
        그래서 루프백만 검사하던 기존 포터블 런처는 이 경우를 '비었다'고 오판했고,
        두 서버가 같은 포트에 공존해 브라우저가 남의 앱을 보여줬다 — 이번 사고의 뿌리다."""
        assert sl._can_bind("127.0.0.1", occupied_port_wildcard) is True, (
            "이 단정이 깨지면 Windows 동작이 바뀐 것 — 아래 판정 근거를 재확인해야 한다")
        assert sl.is_port_free(occupied_port_wildcard) is False
        assert sl.find_free_port(occupied_port_wildcard) != occupied_port_wildcard

    def test_probe_hosts_cover_both_directions(self):
        assert set(sl._PROBE_HOSTS) == {"0.0.0.0", "127.0.0.1"}


class TestInstanceProbe:
    def test_no_server_returns_none(self, occupied_port):
        # 점유는 됐지만 HTTP 서버가 아니므로 우리 앱이 아니다
        assert sl.probe_instance(occupied_port, timeout=0.3) is None

    def test_describe_unknown_holder(self, occupied_port):
        msg = sl.describe_port_holder(occupied_port)
        assert "다른 프로그램" in msg

    def test_describe_our_instance_shows_data_dir(self, monkeypatch):
        monkeypatch.setattr(sl, "probe_instance", lambda *_a, **_k: {
            "mode": "portable", "base_dir": r"D:\MMP\MeetingMinutesData",
            "config_path": r"D:\MMP\MeetingMinutesData\config.json"})
        msg = sl.describe_port_holder(8501)
        assert "포터블 배포본" in msg and "MeetingMinutesData" in msg

    def test_describe_source_instance(self, monkeypatch):
        monkeypatch.setattr(sl, "probe_instance", lambda *_a, **_k: {
            "mode": "source", "base_dir": r"C:\repo", "config_path": r"C:\repo\config.json"})
        assert "소스 실행" in sl.describe_port_holder(8501)


class TestBrowserOpensOnlyForOurInstance:
    def _run(self, monkeypatch, info, expect_path):
        opened = []
        monkeypatch.setattr(sl.webbrowser, "open", lambda url: opened.append(url))
        monkeypatch.setattr(sl.urllib.request, "urlopen",
                            lambda *a, **k: _FakeHealth())
        monkeypatch.setattr(sl, "probe_instance", lambda *_a, **_k: info)
        sl.open_browser_when_ready(9999, timeout=2.0, expect_config_path=expect_path)
        # 데몬 스레드가 끝날 시간을 준다
        import time
        for _ in range(40):
            if opened:
                break
            time.sleep(0.05)
        return opened

    def test_opens_when_config_matches(self, monkeypatch):
        opened = self._run(monkeypatch, {"config_path": r"C:\repo\config.json"},
                           r"C:\repo\config.json")
        assert opened == ["http://localhost:9999"]

    def test_does_not_open_other_instance(self, monkeypatch):
        """다른 인스턴스가 그 포트에 응답하면 열지 않는다 — 이걸 열어서 사고가 났다."""
        opened = self._run(monkeypatch, {"config_path": r"D:\MMP\MeetingMinutesData\config.json"},
                           r"C:\repo\config.json")
        assert opened == []


class _FakeHealth:
    status = 200

    def read(self):
        return b'{"status":"ok"}'

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_uvicorn(calls: dict):
    """uvicorn 대역 — 프로덕션 경로는 `uvicorn.run()` 이 아니라 Config+Server 를 쓴다.

    /api/shutdown 이 정상 종료를 요청하려면 Server 객체가 필요하기 때문이다
    (`server_launch.register_shutdown_handle`). dev 경로는 여전히 run() 을 쓴다.
    """
    mod = types.ModuleType("uvicorn")

    class _Server:
        def __init__(self, config):
            self.config = config
            calls["server_created"] = True

        def run(self):
            calls["server_ran"] = True

    def _config(app, **kw):
        calls.update(kw)
        calls["app"] = app
        return {"app": app, **kw}

    mod.Config = _config
    mod.Server = _Server
    mod.run = lambda app, **kw: calls.update(kw) or calls.update({"app": app})
    return mod


class TestRunUiLauncher:
    """소스 런처가 공용 규칙을 실제로 쓰는지(포트 이동·브라우저 순서·dev 모드 실패)."""

    def _prepare(self, monkeypatch, argv):
        from meeting_minutes_app.meeting_pipeline import run_ui
        monkeypatch.setattr(run_ui, "check_python_deps", lambda: None)
        monkeypatch.setattr(run_ui, "build_frontend", lambda: None)
        monkeypatch.setattr(run_ui, "check_node_deps", lambda: None)
        monkeypatch.setattr(sys, "argv", ["run_ui", *argv])
        calls = {}
        monkeypatch.setitem(sys.modules, "uvicorn", _fake_uvicorn(calls))
        return run_ui, calls

    def test_production_moves_to_free_port_and_defers_browser(self, monkeypatch, occupied_port):
        run_ui, calls = self._prepare(monkeypatch, ["--port", str(occupied_port)])
        browser = {}
        monkeypatch.setattr(
            "meeting_minutes_app.common.server_launch.open_browser_when_ready",
            lambda port, **kw: browser.update({"port": port, **kw}))
        run_ui.main()
        assert calls["port"] != occupied_port, "점유된 포트를 그대로 바인딩하면 안 된다"
        assert calls["port"] == browser["port"], "브라우저는 실제 바인딩 포트로 열어야 한다"
        # 우리 인스턴스 확인용 config 경로를 넘긴다(남의 앱을 열지 않기 위해)
        assert browser["expect_config_path"].endswith("config.json")

    def test_production_keeps_requested_port_when_free(self, monkeypatch):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            free = probe.getsockname()[1]
        run_ui, calls = self._prepare(monkeypatch, ["--port", str(free)])
        monkeypatch.setattr(
            "meeting_minutes_app.common.server_launch.open_browser_when_ready",
            lambda *a, **k: None)
        run_ui.main()
        assert calls["port"] == free

    def test_dev_mode_refuses_instead_of_switching_port(self, monkeypatch, occupied_port):
        """--dev 는 포트를 바꾸지 않는다 — vite 프록시가 localhost:8501 하드코딩이라
        백엔드만 옮기면 화면은 뜨는데 모든 /api 가 조용히 깨진다."""
        run_ui, _ = self._prepare(monkeypatch, ["--dev", "--port", str(occupied_port)])
        with pytest.raises(SystemExit) as ei:
            run_ui.main()
        assert ei.value.code == 1


class TestBindHostRule:
    """바인딩 host 규칙도 두 런처가 하나를 쓴다.

    과거 소스 런처만 `--host` 기본값이 0.0.0.0 이라 **설정과 무관하게 사내망에 웹 UI 가
    노출**됐다(회의록·전사 열람, 업로드=과금 트리거가 인증 없이 가능). 포터블만
    server.lan_access 를 보던 비대칭이다."""

    def test_default_is_this_pc_only(self, monkeypatch):
        monkeypatch.setattr(sl, "lan_access_enabled", lambda: False)
        assert sl.resolve_bind_host() == "127.0.0.1"

    def test_lan_access_opens_wildcard(self, monkeypatch):
        monkeypatch.setattr(sl, "lan_access_enabled", lambda: True)
        assert sl.resolve_bind_host() == "0.0.0.0"

    def test_explicit_host_wins(self, monkeypatch):
        monkeypatch.setattr(sl, "lan_access_enabled", lambda: True)
        assert sl.resolve_bind_host("127.0.0.1") == "127.0.0.1"

    def test_lan_access_reads_config(self, monkeypatch):
        from meeting_minutes_app.common import config_loader
        monkeypatch.setattr(config_loader, "get",
                            lambda k, d=None: True if k == "server.lan_access" else d)
        assert sl.lan_access_enabled() is True

    def test_source_launcher_uses_the_rule(self, monkeypatch):
        """[회귀] run_ui.py 가 args.host 를 그대로 넘기지 않는지 — 넘기면 노출이 돌아온다."""
        from meeting_minutes_app.meeting_pipeline import run_ui
        monkeypatch.setattr(run_ui, "check_python_deps", lambda: None)
        monkeypatch.setattr(run_ui, "build_frontend", lambda: None)
        monkeypatch.setattr(sl, "lan_access_enabled", lambda: False)
        monkeypatch.setattr(sl, "open_browser_when_ready", lambda *a, **k: None)
        calls = {}
        monkeypatch.setitem(sys.modules, "uvicorn", _fake_uvicorn(calls))
        monkeypatch.setattr(sys, "argv", ["run_ui", "--no-browser"])
        run_ui.main()
        assert calls["host"] == "127.0.0.1"


class TestGracefulShutdownHandle:
    """종료가 os._exit 이 아니라 정상 경로를 타는지 — 핸들 등록이 그 전제다.

    회귀 배경: `/api/shutdown` 이 `os._exit(0)` 이라 lifespan shutdown 을 건너뛰어
    실시간 세션 정리가 실행되지 않고, 처리 중이던 세션이 DB 에 'processing' 으로
    남아 다음 실행에서 영구 고착됐다. Windows 에서는 SIGTERM 이 강제 종료라
    폴백만으로는 대체할 수 없어 Server 객체를 들고 있어야 한다.
    """

    def test_register_puts_handle_on_app_state(self):
        from web.backend.app import app
        sentinel = object()
        assert sl.register_shutdown_handle(sentinel) is True
        assert app.state.uvicorn_server is sentinel

    def test_source_launcher_registers(self, monkeypatch):
        from meeting_minutes_app.meeting_pipeline import run_ui
        monkeypatch.setattr(run_ui, "check_python_deps", lambda: None)
        monkeypatch.setattr(run_ui, "build_frontend", lambda: None)
        monkeypatch.setattr(sl, "lan_access_enabled", lambda: False)
        monkeypatch.setattr(sl, "open_browser_when_ready", lambda *a, **k: None)
        registered = {}
        monkeypatch.setattr(sl, "register_shutdown_handle",
                            lambda s: registered.update({"s": s}) or True)
        calls = {}
        monkeypatch.setitem(sys.modules, "uvicorn", _fake_uvicorn(calls))
        monkeypatch.setattr(sys, "argv", ["run_ui", "--no-browser"])
        run_ui.main()
        assert calls.get("server_created") and calls.get("server_ran")
        assert "s" in registered, "런처가 종료 핸들을 등록하지 않으면 graceful 종료가 안 된다"


class TestWsRequirementIsOneRule:
    def test_predicate_shared(self, monkeypatch):
        """판정식은 한 곳 — 반응만 런처별로 다르다(소스=pip 설치, 포터블=즉시 실패)."""
        monkeypatch.setattr(sl, "ws_decode_supported", lambda: False)
        with pytest.raises(RuntimeError) as ei:
            sl.require_ws_decode_support()
        assert sl.WS_REQUIREMENT in str(ei.value)

    def test_ok_when_supported(self, monkeypatch):
        monkeypatch.setattr(sl, "ws_decode_supported", lambda: True)
        sl.require_ws_decode_support()      # 예외 없음

    def test_real_environment_supports_it(self):
        """이 리포의 설치 환경은 실제로 지원해야 한다(아니면 녹음이 조용히 깨진다)."""
        assert sl.ws_decode_supported() is True


class TestSystemInfoEndpoint:
    def _client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from web.backend.api import settings as st
        app = FastAPI()
        app.include_router(st.router, prefix="/api")
        return TestClient(app)

    def test_source_mode_when_no_data_dir_env(self, monkeypatch):
        monkeypatch.delenv("MM_DATA_DIR", raising=False)
        r = self._client().get("/api/system/info")
        assert r.status_code == 200
        body = r.json()
        assert body["mode"] == "source"
        assert body["config_path"].endswith("config.json")

    def test_portable_mode_follows_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MM_DATA_DIR", str(tmp_path / "MeetingMinutesData"))
        body = self._client().get("/api/system/info").json()
        assert body["mode"] == "portable"
        assert "MeetingMinutesData" in body["base_dir"]

    def test_no_secrets_in_payload(self, monkeypatch):
        body = self._client().get("/api/system/info").json()
        assert set(body) == {"mode", "frozen", "base_dir", "config_path", "data_dir"}
        blob = repr(body).lower()
        for banned in ("sk-", "api_key", "password", "token"):
            assert banned not in blob


class TestDiagnoseShowsDataFolder:
    def test_data_folder_row_present(self):
        from web.backend.api import assistant
        res = assistant.obsidian_diagnose()
        names = [c["name"] for c in res["checks"]]
        assert "데이터 폴더" in names
        row = next(c for c in res["checks"] if c["name"] == "데이터 폴더")
        assert row["ok"] is True            # 식별 정보이므로 상태를 실패로 만들지 않는다
        assert "소스 실행" in row["detail"] or "포터블" in row["detail"]
