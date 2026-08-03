"""업로드 입력 검증 — 파일명·형식·크기 (P0).

`multipart` 의 filename 과 본문 길이는 **클라이언트가 주는 값**인데 예전에는 둘 다 검증 없이
썼다.

- 파일명이 그대로 경로에 조립됐다: `UPLOADS_DIR / f"{ts}_{name}"`. 접두 타임스탬프가 `..`
  한 단계를 소모하기 때문에 `../../../x.mp3` 부터 **UPLOADS_DIR 밖으로 나간다**
  (실측: `web/uploads` 기준으로 `web/x.mp3`).
- 크기 상한이 없어 청크 루프가 디스크를 채울 수 있었다. 사내에서는 공격보다 **실수 업로드로
  디스크 고갈**이 현실적이고, 디스크가 가득 차면 SQLite 까지 위험해진다.
"""

from __future__ import annotations

import io
from pathlib import Path, PurePosixPath

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from meeting_minutes_app.common import config_loader
from web.backend.api import batch


@pytest.fixture
def client(tmp_path, monkeypatch):
    """업로드 디렉터리를 tmp 로 돌린 클라이언트.

    TestClient 기본 `client.host` 는 "testclient" 라 과금 관문(require_client)에 걸린다 —
    프로덕션 판정을 느슨하게 하는 대신 loopback 으로 접속한다.
    """
    monkeypatch.setattr(batch, "UPLOADS_DIR", tmp_path / "uploads")
    (tmp_path / "uploads").mkdir()
    monkeypatch.setattr(config_loader, "get_api_key", lambda *a, **k: "sk-test")
    app = FastAPI()
    app.include_router(batch.router, prefix="/api")
    return TestClient(app, client=("127.0.0.1", 12345))


def _post(client, filename, content=b"\x00\x01\x02", confirm="false"):
    return client.post("/api/upload",
                       files={"file": (filename, content, "audio/mpeg")},
                       data={"confirm": confirm})


class TestFilenameSanitize:
    @pytest.mark.parametrize("raw,expected_name", [
        ("a.mp3", "a.mp3"),
        ("../../../x.mp3", "x.mp3"),
        ("..\\..\\x.mp3", "x.mp3"),
        ("/etc/passwd.mp3", "passwd.mp3"),
        ("C:\\Windows\\evil.mp3", "evil.mp3"),
        ("회의 녹음.mp3", "회의 녹음.mp3"),          # 한글·공백은 그대로 살린다
    ])
    def test_directory_components_are_dropped(self, raw, expected_name):
        assert batch._safe_upload_name(raw) == expected_name

    def test_control_and_reserved_chars_replaced(self):
        out = batch._safe_upload_name('a<b>c:d"e|f?g*h\x01.mp3')
        assert out.endswith(".mp3")
        for ch in '<>:"|?*\x01':
            assert ch not in out

    def test_long_name_is_capped_keeping_extension(self):
        out = batch._safe_upload_name("a" * 400 + ".mp3")
        assert len(out) <= 120 and out.endswith(".mp3")

    def test_result_never_escapes_upload_dir(self, tmp_path):
        """정규화 결과를 실제로 조립해 이탈이 남지 않는지 확인한다."""
        base = tmp_path / "uploads"
        for raw in ["../../../x.mp3", "../../x.mp3", "..\\..\\..\\x.mp3", "/a/b/c.mp3"]:
            p = (base / f"20260803_120000_{batch._safe_upload_name(raw)}").resolve()
            assert str(p).startswith(str(base.resolve()))


class TestUploadRejects:
    def test_traversal_filename_stays_inside(self, client, tmp_path):
        r = _post(client, "../../../escaped.mp3")
        assert r.status_code == 200                    # 정상 업로드로 처리된다
        outside = tmp_path / "escaped.mp3"
        assert not outside.exists(), "UPLOADS_DIR 밖에 파일이 생겼다"
        assert list((tmp_path / "uploads").glob("*escaped.mp3")), "안에는 저장돼야 한다"

    def test_unsupported_extension(self, client):
        r = _post(client, "malware.exe")
        assert r.status_code == 400
        assert "지원하지 않는 형식" in r.json()["detail"]

    def test_missing_extension(self, client):
        r = _post(client, "noext")
        assert r.status_code == 400

    def test_empty_file(self, client, tmp_path):
        r = _post(client, "a.mp3", content=b"")
        assert r.status_code == 400
        assert list((tmp_path / "uploads").iterdir()) == [], "빈 파일 잔재가 남았다"

    def test_over_size_limit_is_rejected_and_partial_removed(self, client, tmp_path, monkeypatch):
        """상한 초과는 413 이고 **받은 조각을 남기지 않는다** — 부분 파일이 남으면
        다음 스캔·재시도가 그것을 정상 녹음으로 취급한다."""
        monkeypatch.setattr(batch, "_upload_limits", lambda: ({"mp3"}, 1))   # 1MB
        r = _post(client, "big.mp3", content=b"x" * (2 * 1024 * 1024))
        assert r.status_code == 413
        assert "너무 큽니다" in r.json()["detail"]
        assert list((tmp_path / "uploads").iterdir()) == [], "부분 파일이 남았다"

    def test_low_disk_space_is_refused(self, client, monkeypatch):
        import shutil
        monkeypatch.setattr(shutil, "disk_usage",
                            lambda p: type("U", (), {"free": 10 * 1024 * 1024})())
        r = _post(client, "a.mp3")
        assert r.status_code == 507
        assert "여유 공간" in r.json()["detail"]


class TestExtensionSourceIsShared:
    """확장자 목록이 두 곳에 적히면 "워처는 받는데 업로드는 거부"로 갈라진다.

    그래서 업로드는 워처와 **같은 설정 키**(`vault_watcher.supported_extensions`)를 읽고,
    비어 있으면 같은 기본값(`audio_watcher.DEFAULT_AUDIO_EXTS`)으로 떨어진다.
    """

    def test_config_key_is_honored(self, monkeypatch):
        monkeypatch.setattr(config_loader, "get",
                            lambda k, d=None: ["mp3", ".M4A"] if k == "vault_watcher.supported_extensions" else d)
        exts, _ = batch._upload_limits()
        assert exts == {"mp3", "m4a"}, "설정을 무시하면 워처와 판정이 갈라진다"

    def test_falls_back_to_the_shared_default(self, monkeypatch):
        from meeting_minutes_app.meeting_pipeline.audio_watcher import DEFAULT_AUDIO_EXTS
        monkeypatch.setattr(config_loader, "get", lambda k, d=None: None if "supported" in k else d)
        exts, _ = batch._upload_limits()
        assert exts == DEFAULT_AUDIO_EXTS

    def test_max_mb_default_when_unset(self, monkeypatch):
        monkeypatch.setattr(config_loader, "get", lambda k, d=None: None)
        _, max_mb = batch._upload_limits()
        assert max_mb == batch._DEFAULT_MAX_UPLOAD_MB
