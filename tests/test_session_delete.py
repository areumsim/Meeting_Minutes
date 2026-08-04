"""세션 삭제 — 휴지통(soft delete) + 완전 삭제 시 폴더 정리 (FR-001 개정 · N-13).

하드 DELETE 였을 때 두 가지가 잘못됐다.

1. `sessions.output_dir` 을 DB 에 갖고 있는데도 폴더를 남겼다. 다음 시작에
   `session_scanner.scan_output_dir()` 이 "DB 에 없는 폴더"로 보고 **다시 임포트**했다 —
   사용자에게는 지운 회의가 재시작 후 되살아나는 것으로 보였다.
2. 되돌릴 방법이 없는데 확인은 프런트 `confirm()` 뿐이고, 서버는 없는 세션에도 무조건
   `success: True` 를 돌려줬다.
"""

from __future__ import annotations

import pytest

from web.backend import database as db


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    return tmp_path


def _make(out_dir="") -> str:
    sid = db.create_session(title="회의")
    if out_dir:
        db.update_session_status(sid, "completed", output_dir=str(out_dir))
    return sid


class TestSoftDelete:
    def test_delete_hides_but_keeps_the_row(self, fresh_db):
        sid = _make()
        assert db.delete_session(sid) is True
        assert [s["id"] for s in db.list_sessions()] == []
        assert [s["id"] for s in db.list_sessions(deleted=True)] == [sid]
        # 되돌리기가 성립하려면 본문이 남아 있어야 한다
        assert db.get_session(sid) is not None

    def test_delete_twice_is_reported_not_silently_ok(self, fresh_db):
        sid = _make()
        assert db.delete_session(sid) is True
        assert db.delete_session(sid) is False, "이미 삭제된 세션에 성공을 돌려주면 화면이 거짓말을 한다"

    def test_delete_missing_session_returns_false(self, fresh_db):
        assert db.delete_session("nope") is False

    def test_restore_brings_it_back(self, fresh_db):
        sid = _make()
        db.delete_session(sid)
        assert db.restore_session(sid) is True
        assert [s["id"] for s in db.list_sessions()] == [sid]
        assert db.list_sessions(deleted=True) == []

    def test_restore_only_works_on_deleted(self, fresh_db):
        sid = _make()
        assert db.restore_session(sid) is False

    def test_clear_all_is_also_restorable(self, fresh_db):
        """전량 삭제가 회복 불가인 것이 가장 위험했다."""
        a, b = _make(), _make()
        assert db.clear_all_sessions() == 2
        assert db.list_sessions() == []
        assert {s["id"] for s in db.list_sessions(deleted=True)} == {a, b}
        db.restore_session(a)
        assert [s["id"] for s in db.list_sessions()] == [a]

    def test_segments_survive_soft_delete(self, fresh_db):
        sid = _make()
        db.add_segment(sid, "화자1", "안녕하세요", 0.0, 1.0)
        db.delete_session(sid)
        assert len(db.get_segments(sid)) == 1, "전사를 지우면 되돌리기가 의미가 없다"


class TestPurge:
    def test_purge_removes_rows_and_returns_output_dir(self, fresh_db, tmp_path):
        out = tmp_path / "output" / "20260803_120000_회의"
        out.mkdir(parents=True)
        sid = _make(out)
        db.add_segment(sid, "화자1", "본문", 0.0, 1.0)

        assert db.purge_session(sid) == str(out)
        assert db.get_session(sid) is None
        assert db.get_segments(sid) == []
        assert db.list_sessions(deleted=True) == []

    def test_purge_missing_returns_none(self, fresh_db):
        assert db.purge_session("nope") is None

    def test_purge_removes_facilitation_observations(self, fresh_db):
        """관찰 로그의 span 에는 발화 원문 인용이 들어간다 — 완전 삭제가 남기면
        회의를 지웠는데 회의 내용 일부가 DB 에 영구 잔존한다(PRD §12)."""
        from meeting_minutes_app.wiki_core import facilitation
        sid = _make()
        facilitation.record_observation(sid, "scribe", span="지워져야 하는 발화 인용",
                                        db_path=db.DB_PATH)
        facilitation.record_triage(sid, model="gpt-4o-mini", ok=True,
                                   db_path=db.DB_PATH)
        assert facilitation.observations(sid, db_path=db.DB_PATH)
        db.purge_session(sid)
        assert facilitation.observations(sid, db_path=db.DB_PATH) == []
        assert facilitation.triages(sid, db_path=db.DB_PATH) == []

    def test_soft_delete_keeps_facilitation_for_restore(self, fresh_db):
        """휴지통은 되돌릴 수 있어야 한다 — related_notes 와 같은 규칙."""
        from meeting_minutes_app.wiki_core import facilitation
        sid = _make()
        facilitation.record_observation(sid, "scribe", span="발화",
                                        db_path=db.DB_PATH)
        db.delete_session(sid)
        assert facilitation.observations(sid, db_path=db.DB_PATH)


class TestScannerDoesNotResurrect:
    def test_deleted_session_folder_is_not_reimported(self, fresh_db, tmp_path, monkeypatch):
        """[회귀] 이것이 P0 의 핵심 — 지운 회의가 재시작 후 돌아오면 안 된다."""
        from web.backend import session_scanner

        out_root = tmp_path / "output"
        folder = out_root / "20260803_120000_회의"
        folder.mkdir(parents=True)
        sid = _make(folder)
        db.delete_session(sid)

        session_scanner.scan_output_dir(str(out_root))

        assert db.list_sessions() == [], "삭제한 세션이 스캔으로 되살아났다"
        assert [s["id"] for s in db.list_sessions(deleted=True)] == [sid]

    def test_unknown_folder_is_still_imported(self, fresh_db, tmp_path):
        """되살아남 방지가 정상 임포트를 막지는 않아야 한다(CLI 산출물 편입 경로)."""
        from web.backend import session_scanner

        out_root = tmp_path / "output"
        (out_root / "20260803_130000_새회의").mkdir(parents=True)
        session_scanner.scan_output_dir(str(out_root))
        assert len(db.list_sessions()) == 1

    def test_known_output_dirs_includes_deleted(self, fresh_db, tmp_path):
        sid = _make(tmp_path / "out1")
        db.delete_session(sid)
        assert str(tmp_path / "out1") in db.known_output_dirs()


class TestTrashUtility:
    def test_missing_path_is_success(self, tmp_path):
        from web.backend.trash import move_to_trash
        ok, msg = move_to_trash(tmp_path / "없는폴더")
        assert ok is True

    def test_falls_back_to_local_trash_without_send2trash(self, tmp_path, monkeypatch):
        """Send2Trash 가 없어도 **rmtree 로 떨어지지 않는다** — 배포본에서만 복구
        불가가 되는 것이 최악이다."""
        import builtins
        from web.backend import trash

        real_import = builtins.__import__

        def _no_send2trash(name, *a, **k):
            if name == "send2trash":
                raise ImportError("simulated: 포터블 빌드에서 누락")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", _no_send2trash)
        monkeypatch.setattr(trash, "_move_to_local_trash",
                            lambda p: (True, f"local:{p.name}"))
        target = tmp_path / "out"
        target.mkdir()
        ok, msg = trash.move_to_trash(target)
        assert ok and msg.startswith("local:")

    def test_local_trash_moves_instead_of_deleting(self, tmp_path, monkeypatch):
        from meeting_minutes_app.common import app_paths
        from web.backend import trash

        monkeypatch.setattr(app_paths, "get_data_dir", lambda: tmp_path / "data")
        target = tmp_path / "out"
        target.mkdir()
        (target / "회의록.md").write_text("본문", encoding="utf-8")

        ok, msg = trash._move_to_local_trash(target)
        assert ok, msg
        assert not target.exists()
        kept = list((tmp_path / "data" / ".trash").glob("*_out/회의록.md"))
        assert kept and kept[0].read_text(encoding="utf-8") == "본문", "내용이 사라졌다"


class TestPurgeOrdering:
    """FR-001 수용 기준: **파일 이동 실패 시 DB 레코드를 삭제하지 않는다.**

    반대 순서로 하면 폴더 이동이 실패했을 때 파일만 남고 그것을 가리키는 기록이 사라져
    아무도 모르는 고아 폴더가 된다 — 원래 결함이 바로 고아 파일이었으므로, 고치면서
    같은 결과를 만들면 의미가 없다.
    """

    def _client(self, monkeypatch):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from web.backend.api import sessions as api
        app = FastAPI()
        app.include_router(api.router, prefix="/api")
        return TestClient(app, client=("127.0.0.1", 12345))

    def test_keeps_record_when_folder_move_fails(self, fresh_db, tmp_path, monkeypatch):
        from web.backend.api import sessions as api

        out = tmp_path / "output" / "회의"
        out.mkdir(parents=True)
        sid = _make(out)
        db.delete_session(sid)

        monkeypatch.setattr("web.backend.trash.move_to_trash",
                            lambda p: (False, "권한이 없습니다"))
        r = self._client(monkeypatch).delete(f"/api/sessions/{sid}/purge")
        assert r.status_code == 500
        assert db.get_session(sid) is not None, "폴더가 남았는데 기록을 지우면 고아 폴더가 된다"
        assert [s["id"] for s in db.list_sessions(deleted=True)] == [sid]

    def test_purges_after_successful_move(self, fresh_db, tmp_path, monkeypatch):
        out = tmp_path / "output" / "회의"
        out.mkdir(parents=True)
        sid = _make(out)
        db.delete_session(sid)

        moved: list = []
        monkeypatch.setattr("web.backend.trash.move_to_trash",
                            lambda p: (moved.append(p) or (True, "휴지통으로 보냈습니다")))
        r = self._client(monkeypatch).delete(f"/api/sessions/{sid}/purge")
        assert r.status_code == 200 and r.json()["folder_removed"] is True
        assert moved == [str(out)]
        assert db.get_session(sid) is None


class TestTrashPathResolution:
    """상대 `output_dir` 을 **데이터 베이스 기준**으로 해석하는지.

    [실기 검증에서 발견 2026-08-03] 포터블은 `run_ui_exe.setup_paths()` 가 데이터
    폴더로 `os.chdir` 한다. `trash.move_to_trash` 가 상대 경로를 CWD 기준으로 풀어
    **폴더가 있는데도 "없다"** 고 판정했고, purge 가 그대로 진행돼 고아 폴더를 남기면서
    응답은 `folder_removed: true` 로 거짓 보고했다 — FR-001 이 없애려던 결함 그 자체다.

    `api/batch.py` 는 같은 함정을 `app_paths.get_output_dir()` 로 피하고 있었다(주석까지
    달려 있다). 규칙이 두 곳에서 갈라진 사례.

    테스트는 **OS 휴지통을 쓰지 않는다** — 실제 send2trash 를 타면 개발자 PC 의 휴지통에
    테스트 폴더가 쌓인다. import 를 막아 로컬 폴백(`data/.trash/`)으로 흘린다.
    """

    @pytest.fixture
    def no_os_trash(self, monkeypatch):
        import builtins
        real_import = builtins.__import__

        def _blocked(name, *a, **k):
            if name == "send2trash":
                raise ImportError("테스트: OS 휴지통을 쓰지 않는다")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", _blocked)

    def test_relative_path_resolves_against_data_base_not_cwd(
            self, tmp_path, monkeypatch, no_os_trash):
        from meeting_minutes_app.common import app_paths
        from web.backend import trash

        base = tmp_path / "MeetingMinutesData"
        target = base / "output" / "20260803_회의"
        target.mkdir(parents=True)
        (target / "회의록.md").write_text("본문", encoding="utf-8")

        monkeypatch.setattr(app_paths, "get_base_dir", lambda: base)
        monkeypatch.setattr(app_paths, "get_data_dir", lambda: base / "data")
        # CWD 는 전혀 다른 곳 — 예전 구현은 여기서 "폴더가 이미 없습니다"로 떨어졌다.
        monkeypatch.chdir(tmp_path)

        ok, msg = trash.move_to_trash(r"output/20260803_회의")
        assert ok, msg
        assert not target.exists(), f"상대 경로를 못 찾아 폴더가 그대로 남았다: {msg}"
        kept = list((base / "data" / ".trash").glob("*_20260803_회의/회의록.md"))
        assert kept and kept[0].read_text(encoding="utf-8") == "본문"

    def test_absolute_path_is_used_as_is(self, tmp_path, monkeypatch, no_os_trash):
        from meeting_minutes_app.common import app_paths
        from web.backend import trash
        target = tmp_path / "abs_out"
        target.mkdir()
        monkeypatch.setattr(app_paths, "get_data_dir", lambda: tmp_path / "data")
        ok, msg = trash.move_to_trash(target)
        assert ok and not target.exists(), msg

    def test_genuinely_missing_folder_is_still_success(self, tmp_path, monkeypatch):
        """사용자가 손으로 지운 폴더는 정상 케이스다 — 정규화 후에도 없으면 성공."""
        from meeting_minutes_app.common import app_paths
        from web.backend import trash
        monkeypatch.setattr(app_paths, "get_base_dir", lambda: tmp_path)
        ok, msg = trash.move_to_trash("output/없는폴더")
        assert ok and "이미 없" in msg
