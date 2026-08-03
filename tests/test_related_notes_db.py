# -*- coding: utf-8 -*-
"""실시간 관련 노트 사이드카(SQLite) 회귀 테스트 — 임시 DB만 사용.

방지하려는 재발 버그:
  1) 관련 노트가 프런트 state 에만 있어 정지/재시작 시 소실 → 회의별 영속 저장.
  2) 같은 노트가 발화마다 중복 행으로 쌓임 → (session_id, note_path) 갱신.
  3) 세션 삭제 후 관련 노트만 남아 교차 집계가 유령 회의를 센다 → 삭제 전파.

실행:
    python -m pytest tests/test_related_notes_db.py -q
"""

import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def db(tmp_path, monkeypatch):
    """DB_PATH 를 임시 파일로 바꿔 새로 초기화한 database 모듈."""
    from web.backend import database as _db
    importlib.reload(_db)
    monkeypatch.setattr(_db, "DB_PATH", tmp_path / "test.db")
    _db.init_db()
    return _db


ROWS = [
    {"filename": "02_이론_학습/QAOA.md", "title": "QAOA", "heading": "요약",
     "section_path": "QAOA › 요약", "source_type": "paper", "found_by": "section",
     "score": 1.42, "rank_score": 0.02, "hits": 3, "snippet": "조합최적화",
     "segment_text": "QAOA 논의", "elapsed_sec": 12.5},
    {"filename": "00_Meetings/주간회의.md", "title": "주간회의",
     "source_type": "note", "found_by": "note", "score": 0.31,
     "rank_score": 0.01, "hits": 1, "snippet": "지난주 결정"},
]


class TestPersistence:
    def test_saved_and_read_back_with_evidence(self, db):
        sid = db.create_session("회의1")
        assert db.add_related_notes(sid, ROWS) == 2
        got = db.get_related_notes(sid)
        assert [g["title"] for g in got] == ["QAOA", "주간회의"]  # rank_score 내림차순
        top = got[0]
        assert top["heading"] == "요약"
        assert top["section_path"] == "QAOA › 요약"
        assert top["source_type"] == "paper"
        assert top["hits"] == 3
        assert top["snippet"] == "조합최적화"
        assert top["segment_text"] == "QAOA 논의"
        assert top["elapsed_sec"] == pytest.approx(12.5)

    def test_same_note_updated_not_duplicated(self, db):
        sid = db.create_session("회의1")
        db.add_related_notes(sid, ROWS)
        db.add_related_notes(sid, [{**ROWS[0], "hits": 7, "score": 2.0}])
        got = db.get_related_notes(sid)
        assert len(got) == 2
        assert got[0]["hits"] == 7 and got[0]["score"] == pytest.approx(2.0)

    def test_untitled_rows_skipped(self, db):
        sid = db.create_session("회의1")
        assert db.add_related_notes(sid, [{"snippet": "경로·제목 없음"}]) == 0
        assert db.get_related_notes(sid) == []

    def test_empty_input_noop(self, db):
        sid = db.create_session("회의1")
        assert db.add_related_notes(sid, []) == 0
        assert db.add_related_notes("", ROWS) == 0

    def test_long_text_truncated(self, db):
        sid = db.create_session("회의1")
        db.add_related_notes(sid, [{"title": "T", "filename": "t.md",
                                    "snippet": "가" * 900,
                                    "segment_text": "나" * 900}])
        row = db.get_related_notes(sid)[0]
        assert len(row["snippet"]) == 400 and len(row["segment_text"]) == 400


class TestDeletionPropagates:
    """삭제가 관련 노트까지 정리하는지.

    **계약 변경(FR-001 개정)**: `delete_session`/`clear_all_sessions` 는 이제 휴지통으로
    보내는 soft delete 다. 되돌리기가 성립해야 하므로 그 시점에는 관련 노트를 **남긴다**.
    실제 정리는 사용자가 '완전 삭제'를 누를 때(`purge_session`) 일어난다.
    """

    def test_soft_delete_keeps_related_for_restore(self, db):
        sid = db.create_session("회의1")
        db.add_related_notes(sid, ROWS)
        db.delete_session(sid)
        assert db.get_related_notes(sid), "soft delete 가 지우면 되돌리기가 반쪽이 된다"
        db.restore_session(sid)
        assert len(db.get_related_notes(sid)) == len(ROWS)

    def test_purge_removes_related(self, db):
        sid = db.create_session("회의1")
        db.add_related_notes(sid, ROWS)
        db.purge_session(sid)
        assert db.get_related_notes(sid) == []
        assert db.related_notes_cross_sessions() == []

    def test_clear_all_is_soft_and_purge_cleans_up(self, db):
        sid = db.create_session("회의1")
        db.add_related_notes(sid, ROWS)
        db.clear_all_sessions()
        assert db.get_related_notes(sid), "전량 삭제도 되돌릴 수 있어야 한다"
        db.purge_session(sid)
        assert db.get_related_notes(sid) == []


class TestCrossSessionAggregation:
    def test_counts_distinct_sessions(self, db):
        s1 = db.create_session("회의1")
        s2 = db.create_session("회의2")
        s3 = db.create_session("회의3")
        db.add_related_notes(s1, [ROWS[0]])
        db.add_related_notes(s2, [ROWS[0], ROWS[1]])
        db.add_related_notes(s3, [ROWS[0]])
        cross = db.related_notes_cross_sessions()
        assert cross[0]["title"] == "QAOA"
        assert cross[0]["session_count"] == 3
        assert cross[0]["total_hits"] == 9      # hits 3 × 3회의
        assert cross[1]["session_count"] == 1

    def test_recent_window_limits_sessions(self, db):
        """오래된 회의는 시간창(recent_sessions) 밖으로 밀려 집계되지 않는다."""
        old = db.create_session("옛회의")
        db.add_related_notes(old, [ROWS[1]])
        for i in range(3):
            sid = db.create_session(f"새회의{i}")
            db.add_related_notes(sid, [ROWS[0]])
        cross = db.related_notes_cross_sessions(recent_sessions=3)
        titles = [c["title"] for c in cross]
        assert "QAOA" in titles
        assert "주간회의" not in titles

    def test_limit_respected(self, db):
        sid = db.create_session("회의1")
        db.add_related_notes(sid, [{"title": f"n{i}", "filename": f"{i}.md"}
                                   for i in range(6)])
        assert len(db.related_notes_cross_sessions(limit=2)) == 2
