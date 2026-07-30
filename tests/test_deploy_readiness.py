"""exe(web) 배포 준비 회귀 테스트 — 실제 OpenAI/네트워크 없이 실행.

방지하려는 재발 버그:
  1) 웹 UI에서 설정(키/모델/SSL) 저장 후에도 llm_client 등 모듈 전역이 import 시점
     값으로 고정돼 재시작 전까지 반영되지 않음. (config_loader.on_reload 훅으로 해결)
  2) OpenAI 키 없이 업로드하면 백그라운드에서 실패해 '오류로 중단됨'만 표시.
     (업로드 사전 점검 400 + error_detail 컬럼으로 해결)
  3) vault_watcher.watch_folders 가 폼에서 편집 불가(JSON 수동 편집만).
     (schema list 타입 + settings._coerce_value 목록 정규화로 해결)

실행:
    python -m pytest tests/test_deploy_readiness.py -q
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meeting_minutes_app.common import config_loader  # noqa: E402


# ━━━━━━━━ 버그 1: 설정 저장 즉시 반영(reload 훅) ━━━━━━━━

class TestConfigReloadHooks:
    @pytest.fixture()
    def temp_config(self, tmp_path, monkeypatch):
        """임시 config.json 로 config_loader 를 격리하고 환경변수 간섭 제거."""
        cfg_path = tmp_path / "config.json"
        monkeypatch.setattr(config_loader, "_CONFIG_PATH", cfg_path)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        def write(data: dict):
            cfg_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        yield write
        # 다른 테스트가 실제 config 을 보도록 캐시 원복
        config_loader._cache = None

    def test_llm_client_globals_refresh_on_reload(self, temp_config):
        from meeting_minutes_app.common import llm_client

        temp_config({"api": {"openai_api_key": "sk-old"}, "models": {"gpt_model": "gpt-4o-mini"}})
        config_loader.reload()
        assert llm_client.OPENAI_API_KEY == "sk-old"
        assert llm_client.GPT_MODEL == "gpt-4o-mini"

        temp_config({"api": {"openai_api_key": "sk-new"},
                     "models": {"gpt_model": "gpt-4o"}, "ssl": {"verify": False}})
        config_loader.reload()
        assert llm_client.OPENAI_API_KEY == "sk-new"
        assert llm_client.GPT_MODEL == "gpt-4o"
        assert llm_client.SSL_VERIFY is False

    def test_pipeline_module_copies_refresh_on_reload(self, temp_config):
        """from-import 로 복사된 meeting_minutes/stt/minutes_generation 전역도 갱신."""
        from meeting_minutes_app.meeting_pipeline import meeting_minutes as mm
        from meeting_minutes_app.meeting_pipeline import minutes_generation as mg
        from meeting_minutes_app.meeting_pipeline import stt

        temp_config({
            "api": {"openai_api_key": "sk-refresh"},
            "models": {"stt": "gpt-4o-transcribe", "minutes_model": "gpt-4o-mini"},
        })
        config_loader.reload()

        assert mm.DEFAULT_STT_MODEL == "gpt-4o-transcribe"
        assert mm.OPENAI_API_KEY == "sk-refresh"
        assert mm.MINUTES_MODEL == "gpt-4o-mini"
        assert stt.DEFAULT_STT_MODEL == "gpt-4o-transcribe"
        assert stt.OPENAI_API_KEY == "sk-refresh"
        assert mg.MINUTES_MODEL == "gpt-4o-mini"

    def test_reload_hook_failure_does_not_break_reload(self, temp_config, capsys):
        def boom():
            raise RuntimeError("hook fail")

        config_loader.on_reload(boom)
        try:
            temp_config({"api": {}})
            config_loader.reload()  # 예외가 전파되면 실패
        finally:
            config_loader._RELOAD_HOOKS.remove(boom)


# ━━━━━━━━ 버그 2: 키 없는 업로드 사전 점검 + 오류 상세 ━━━━━━━━

class TestUploadPreflight:
    def test_upload_without_key_returns_400_korean(self, monkeypatch, tmp_path):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from web.backend.api import batch

        monkeypatch.setattr(config_loader, "get_api_key", lambda *a, **k: "")
        app = FastAPI()
        app.include_router(batch.router, prefix="/api")
        client = TestClient(app)

        r = client.post(
            "/api/upload",
            files={"file": ("a.mp3", b"\x00\x01", "audio/mpeg")},
            data={"title": "t"},
        )
        assert r.status_code == 400
        assert "API 키" in r.json()["detail"]

    def test_error_detail_column_roundtrip(self, monkeypatch, tmp_path):
        from web.backend import database as db

        monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
        db.init_db()
        sid = db.create_session(title="x")
        db.update_session_status(sid, "error", error_detail="RuntimeError: 키 없음")
        assert db.get_session(sid)["error_detail"] == "RuntimeError: 키 없음"
        # 새 시도(processing)·성공(completed) 시 이전 실패 원인은 비워진다
        db.update_session_status(sid, "processing")
        assert db.get_session(sid)["error_detail"] is None
        db.update_session_status(sid, "error", error_detail="x")
        db.update_session_status(sid, "completed")
        assert db.get_session(sid)["error_detail"] is None


# ━━━━━━━━ 버그 3: watch_folders 폼 지원(list 타입) ━━━━━━━━

class TestListFieldCoercion:
    def _field(self):
        return {"type": "list", "label": "감시할 폴더 목록"}

    def test_multiline_string_to_list(self):
        from web.backend.api.settings import _coerce_value
        v = _coerce_value(self._field(), "D:\\Rec\n\n  E:\\Audio  \r\n")
        assert v == ["D:\\Rec", "E:\\Audio"]

    def test_list_passthrough_strips_empties(self):
        from web.backend.api.settings import _coerce_value
        assert _coerce_value(self._field(), [" a ", "", "b"]) == ["a", "b"]

    def test_invalid_type_raises(self):
        from web.backend.api.settings import _coerce_value
        with pytest.raises(ValueError):
            _coerce_value(self._field(), 123)

    def test_schema_has_plan_watcher_toggle(self):
        from meeting_minutes_app.common import config_schema
        assert config_schema.field_for("plan_watcher", "enabled") is not None


# ━━━━━━━━ 시작 시 자동화 재개 — 부팅을 깨지 않아야 함 ━━━━━━━━

class TestAutostart:
    def test_autostart_disabled_is_noop(self, monkeypatch):
        from web.backend.api import watcher as w
        monkeypatch.setattr(config_loader, "get",
                            lambda k, d=None: False if k.endswith(".enabled") else d)
        w.autostart_from_config()  # 예외 없이 통과해야 함
        assert not w._manager.is_running()
        assert not w._plan.is_running()

    def test_autostart_enabled_without_folders_does_not_raise(self, monkeypatch):
        from web.backend.api import watcher as w

        def fake_get(k, d=None):
            if k == "vault_watcher.enabled":
                return True
            if k == "vault_watcher.watch_folders":
                return []
            if k == "plan_watcher.enabled":
                return False
            return d

        monkeypatch.setattr(config_loader, "get", fake_get)
        w.autostart_from_config()  # 폴더 미설정 → 시작 실패 메시지, 예외는 없어야
        assert not w._manager.is_running()


# ━━━━━━━━ 앱 버전 — 배포본에서 읽히지 않던 문제 ━━━━━━━━

class TestAppVersion:
    """importlib.metadata 는 정본 배포본에서 항상 실패한다.

    포터블 빌드는 앱을 pip install 하지 않고 소스를 복사하므로 dist-info 가 없다.
    정작 버전을 알아야 하는 쪽(문제 신고를 받는 배포본)에서 "버전 정보 없음"만
    찍히던 상태였다. 이제 리터럴은 meeting_minutes_app.__version__ 한 곳이고
    pyproject 가 그것을 읽어간다."""

    def test_app_version_is_never_empty(self):
        from meeting_minutes_app.common.version import app_version
        v = app_version()
        assert v and v != "0", "설치 여부와 무관하게 버전이 나와야 한다"

    def test_pyproject_derives_version_from_code(self):
        """pyproject 에 버전을 하드코딩하면 두 값이 갈라진다 — dynamic 이어야 한다."""
        import re
        root = Path(__file__).resolve().parents[1]
        text = (root / "pyproject.toml").read_text(encoding="utf-8")
        assert re.search(r'^\s*dynamic\s*=\s*\[\s*"version"\s*\]', text, re.MULTILINE), \
            "[project] dynamic = [\"version\"] 이 없다"
        assert 'attr = "meeting_minutes_app.__version__"' in text
        assert not re.search(r'^\s*version\s*=\s*"[\d.]+"', text, re.MULTILINE), \
            "pyproject 에 버전 리터럴이 다시 하드코딩됐다"

    def test_build_commit_absent_without_build_info(self, tmp_path, monkeypatch):
        from meeting_minutes_app.common import app_paths, version as ver
        monkeypatch.setattr(app_paths, "get_resource_dir", lambda: tmp_path)
        assert ver.build_commit() == ""
        assert ver.version_label() == f"meeting-minutes {ver.app_version()}"

    def test_build_commit_parses_build_info(self, tmp_path, monkeypatch):
        from meeting_minutes_app.common import app_paths, version as ver
        (tmp_path / "BUILD_INFO.txt").write_text(
            "Meeting Minutes portable build\nbuilt_at : 2026-07-30 11:14:25\n"
            "commit   : 9dbf5f8\ndirty    : no\npython   : 3.13.1\n", encoding="utf-8")
        monkeypatch.setattr(app_paths, "get_resource_dir", lambda: tmp_path)
        assert ver.build_commit() == "9dbf5f8"
        assert "(build 9dbf5f8)" in ver.version_label()

    def test_dirty_build_is_marked(self, tmp_path, monkeypatch):
        """미커밋 변경이 섞인 빌드를 회의록 메타만 보고 구분할 수 있어야 한다."""
        from meeting_minutes_app.common import app_paths, version as ver
        (tmp_path / "BUILD_INFO.txt").write_text(
            "commit   : abc1234\ndirty    : YES — 미커밋 변경이 포함된 빌드\n", encoding="utf-8")
        monkeypatch.setattr(app_paths, "get_resource_dir", lambda: tmp_path)
        assert ver.build_commit() == "abc1234-dirty"

    def test_unknown_commit_is_treated_as_absent(self, tmp_path, monkeypatch):
        from meeting_minutes_app.common import app_paths, version as ver
        (tmp_path / "BUILD_INFO.txt").write_text(
            "commit   : unknown (git 없음)\ndirty    : no\n", encoding="utf-8")
        monkeypatch.setattr(app_paths, "get_resource_dir", lambda: tmp_path)
        assert ver.build_commit() == ""
