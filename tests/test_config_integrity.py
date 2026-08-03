"""config.json 무결성 — 원자 저장과 손상 시 저장 차단 (FR-004).

이 스위트가 지키는 회귀는 하나로 요약된다: **설정 하나 바꿨는데 API 키까지 전부
날아가는 일이 없어야 한다.** 원래 결함(N-12)은 두 개가 연쇄한 것이었다.

1. 저장이 제자리 덮어쓰기라, 쓰는 중 종료되면 config.json 이 잘린 JSON 으로 남는다.
2. 파싱 실패 시 `_cache = {}` 로 폴백하므로, 그 다음 저장이 **모든 설정이 사라진**
   config.json 을 기록한다.

FR-004 수용 기준("저장 도중 종료해도 완전한 JSON이 남는다", "손상 시 `{}` 폴백 대신
저장을 차단한다", "동시 저장 테스트와 강제 종료 테스트가 있다")을 그대로 옮긴다.
"""

from __future__ import annotations

import json

import pytest

from meeting_minutes_app.common import config_loader


@pytest.fixture
def cfg_file(tmp_path, monkeypatch):
    """config_loader 를 tmp 의 config.json 으로 향하게 하고 캐시를 비운다.

    `_CONFIG_PATH`·`_cache`·`_load_error` 는 모듈 전역이라, 되돌리지 않으면 다음
    테스트가 앞 테스트의 상태를 물려받는다(monkeypatch 가 원복을 보장한다).
    """
    path = tmp_path / "config.json"
    monkeypatch.setattr(config_loader, "_CONFIG_PATH", path)
    monkeypatch.setattr(config_loader, "_cache", None)
    monkeypatch.setattr(config_loader, "_load_error", None)
    return path


def _write(path, obj_or_text):
    if isinstance(obj_or_text, str):
        path.write_text(obj_or_text, encoding="utf-8")
    else:
        path.write_text(json.dumps(obj_or_text, ensure_ascii=False), encoding="utf-8")


def _tmp_leftovers(path):
    """원자 저장이 남긴 임시 파일 잔재."""
    return [p.name for p in path.parent.iterdir() if p.name.endswith(".tmp")]


class TestCorruptConfigBlocksSave:
    """손상된 config 를 만나면 읽기는 기본값으로 계속하되 **저장은 막는다**."""

    def test_parse_error_is_reported_not_swallowed(self, cfg_file):
        _write(cfg_file, '{"api": {"openai_api_key": "sk-real"')   # 잘린 JSON
        assert config_loader.get("api.openai_api_key", "기본값") == "기본값"
        assert "JSON" in (config_loader.load_error() or "")

    def test_save_refuses_and_leaves_file_untouched(self, cfg_file):
        broken = '{"api": {"openai_api_key": "sk-real"'
        _write(cfg_file, broken)
        with pytest.raises(config_loader.ConfigCorrupted):
            config_loader.save({"models": {"stt": "gpt-4o-transcribe"}})
        # 사람이 손으로 살릴 수 있어야 하므로 원본을 건드리지 않는다.
        assert cfg_file.read_text(encoding="utf-8") == broken

    def test_set_nested_refuses_too(self, cfg_file):
        """set_nested 도 같은 관문을 지나야 한다 — 자동 카테고리 등록 같은
        백그라운드 저장이 손상 상태에서 설정 전체를 날리던 자리다."""
        broken = '{"obsidian": {'
        _write(cfg_file, broken)
        with pytest.raises(config_loader.ConfigCorrupted):
            config_loader.set_nested("obsidian.meeting_categories", {"팀회의": {}})
        assert cfg_file.read_text(encoding="utf-8") == broken

    def test_force_allows_intentional_rewrite(self, cfg_file):
        """`init --force` 는 손상 config 를 의도적으로 재작성하는 복구 경로다 —
        여기서까지 막으면 사용자가 손상에서 빠져나올 방법이 없다."""
        _write(cfg_file, "{{{ 깨진 파일")
        config_loader.save({"api": {"openai_api_key": "sk-new"}}, force=True)
        assert json.loads(cfg_file.read_text(encoding="utf-8"))["api"]["openai_api_key"] == "sk-new"

    def test_missing_file_is_not_an_error(self, cfg_file):
        """파일 없음 = 첫 실행. 잃을 것이 없으므로 저장을 막지 않는다."""
        assert config_loader.load_error() is None
        config_loader.save({"models": {"stt": "gpt-4o-transcribe"}})
        assert json.loads(cfg_file.read_text(encoding="utf-8"))["models"]["stt"] == "gpt-4o-transcribe"


class TestAtomicSave:
    def test_saves_valid_json_and_keeps_backup(self, cfg_file):
        _write(cfg_file, {"api": {"openai_api_key": "sk-old"}})
        config_loader._load()
        config_loader.save({"api": {"openai_api_key": "sk-new"}})

        assert json.loads(cfg_file.read_text(encoding="utf-8"))["api"]["openai_api_key"] == "sk-new"
        bak = cfg_file.with_suffix(".json.bak")
        assert bak.exists(), "마지막 정상 설정 백업이 있어야 한다(FR-004)"
        assert json.loads(bak.read_text(encoding="utf-8"))["api"]["openai_api_key"] == "sk-old"

    def test_no_tmp_leftovers(self, cfg_file):
        config_loader.save({"a": 1})
        assert _tmp_leftovers(cfg_file) == []

    def test_crash_during_replace_leaves_original_intact(self, cfg_file, monkeypatch):
        """강제 종료 시뮬 — 교체 직전에 실패해도 기존 설정이 완전한 JSON 으로 남는다.

        제자리 덮어쓰기였을 때는 이 지점에서 파일이 잘린 채 남았고, 다음 실행의
        빈 dict 폴백이 나머지 설정을 지웠다.
        """
        original = {"api": {"openai_api_key": "sk-keep"}, "models": {"stt": "x"}}
        _write(cfg_file, original)
        config_loader._load()

        import os as _os
        monkeypatch.setattr(_os, "replace",
                            lambda *a, **k: (_ for _ in ()).throw(OSError("강제 종료 시뮬")))
        with pytest.raises(OSError):
            config_loader.save({"api": {"openai_api_key": "sk-lost"}})

        assert json.loads(cfg_file.read_text(encoding="utf-8")) == original
        assert _tmp_leftovers(cfg_file) == [], "실패한 저장의 tmp 는 정리돼야 한다"

    def test_cache_reflects_saved_value(self, cfg_file):
        config_loader.save({"models": {"stt": "gpt-4o-transcribe"}})
        assert config_loader.get("models.stt") == "gpt-4o-transcribe"


class TestConcurrentSave:
    def test_set_nested_preserves_keys_written_by_another_process(self, cfg_file):
        """다른 프로세스가 그 사이에 저장한 무관한 키를 덮어쓰지 않는다.

        set_nested 는 메모리 캐시가 아니라 **디스크를 다시 읽어** 병합한다.
        (엄밀한 파일 잠금은 하지 않는 로컬 단일 사용자 도구 기준이다.)
        """
        _write(cfg_file, {"api": {"openai_api_key": "sk-real"}})
        config_loader._load()                       # 캐시에 담긴 시점의 스냅샷

        # 그 사이 다른 프로세스가 새 키를 추가했다.
        on_disk = json.loads(cfg_file.read_text(encoding="utf-8"))
        on_disk["email"] = {"sender": "someone@example.com"}
        _write(cfg_file, on_disk)

        config_loader.set_nested("models.stt", "gpt-4o-transcribe")

        saved = json.loads(cfg_file.read_text(encoding="utf-8"))
        assert saved["models"]["stt"] == "gpt-4o-transcribe"
        assert saved["email"]["sender"] == "someone@example.com", "남이 저장한 키가 사라졌다"
        assert saved["api"]["openai_api_key"] == "sk-real"

    def test_set_nested_aborts_if_file_broke_since_load(self, cfg_file):
        """로드 시점엔 정상이었는데 저장 직전에 깨졌다 = 남이 쓰는 중이거나 손상.
        메모리 캐시로 덮어쓰면 그 사이 남이 저장한 키를 날린다."""
        _write(cfg_file, {"api": {"openai_api_key": "sk-real"}})
        config_loader._load()
        _write(cfg_file, '{"api": {')               # 저장 직전에 깨짐

        with pytest.raises(config_loader.ConfigCorrupted):
            config_loader.set_nested("models.stt", "gpt-4o-transcribe")


class TestRecovery:
    """손상 상태에서 빠져나오는 두 경로 — 사용자가 화면에서 명시적으로 고른다."""

    def test_restore_backup_brings_last_good_config(self, cfg_file):
        _write(cfg_file, {"api": {"openai_api_key": "sk-good"}})
        config_loader._load()
        config_loader.save({"api": {"openai_api_key": "sk-good2"}})   # .bak 생성
        _write(cfg_file, "{ 깨짐")
        config_loader.reload()
        assert config_loader.load_error()

        r = config_loader.recover(restore_backup=True)
        assert r["ok"] and r["restored"]
        assert config_loader.get("api.openai_api_key") == "sk-good"
        assert config_loader.load_error() is None

    def test_fresh_start_keeps_the_corrupt_file(self, cfg_file):
        """손상 파일을 지우지 않는다 — 사용자가 손으로 넣은 키가 들어 있을 수 있다."""
        _write(cfg_file, '{"api": {"openai_api_key": "sk-handwritten"')
        config_loader.reload()

        r = config_loader.recover(restore_backup=False)
        assert r["ok"] and not r["restored"]
        kept = list(cfg_file.parent.glob("config.json.corrupt-*"))
        assert len(kept) == 1
        assert "sk-handwritten" in kept[0].read_text(encoding="utf-8")
        assert config_loader.load_error() is None

    def test_restore_without_backup_does_not_touch_the_file(self, cfg_file):
        """백업이 없으면 손상 파일을 **치우기 전에** 거절한다 —
        치운 뒤 알리면 사용자는 아무것도 없는 상태로 떨어진다."""
        broken = '{"api": {'
        _write(cfg_file, broken)
        config_loader.reload()

        r = config_loader.recover(restore_backup=True)
        assert r["ok"] is False
        assert cfg_file.read_text(encoding="utf-8") == broken

    def test_recover_refuses_when_config_is_fine(self, cfg_file):
        """정상 상태에서의 오호출이 설정을 치워 버리면 안 된다."""
        _write(cfg_file, {"api": {"openai_api_key": "sk-real"}})
        config_loader._load()

        r = config_loader.recover(restore_backup=False)
        assert r["ok"] is False
        assert config_loader.get("api.openai_api_key") == "sk-real"
        assert list(cfg_file.parent.glob("config.json.corrupt-*")) == []

    def test_quarantine_name_is_gitignored(self):
        """보관본에는 실제 API 키가 들어 있다 — 커밋될 수 있으면 안 된다."""
        from pathlib import Path
        gitignore = Path(__file__).resolve().parent.parent / ".gitignore"
        assert "config.json.corrupt-*" in gitignore.read_text(encoding="utf-8")


class TestSingleSavePath:
    def test_no_module_writes_config_json_directly(self):
        """저장 경로가 다시 갈라지지 않게 못을 박는다.

        예전에는 5곳(set_nested·migrate·web settings·cli_init 2곳)이 각자
        `open(config.json, "w")` 를 했다. 원자성 수정이 3곳만 고쳤고, PRD 조차
        경로 수를 3으로 잘못 셌다. 이 테스트는 "사설 함수를 우회해서 부르는" 재발을 막는다.
        """
        import inspect
        from meeting_minutes_app import cli_init
        from web.backend.api import settings as settings_api

        for mod in (cli_init, settings_api):
            src = inspect.getsource(mod)
            assert "config_loader._atomic_write" not in src, (
                f"{mod.__name__} 이 사설 함수를 직접 부른다 — config_loader.save() 를 쓸 것")
            assert 'open(config_path, "w"' not in src, (
                f"{mod.__name__} 에 config.json 제자리 덮어쓰기가 남아 있다")
