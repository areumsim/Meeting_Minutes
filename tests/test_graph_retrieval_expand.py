"""meeting_workflow.graph_expand_titles() 회귀 테스트 — LLM/네트워크 없이,
실제 data/wiki_graph.db는 건드리지 않는다(graph_db.DB_PATH를 tmp_path로 monkeypatch).

실행:
    python -m pytest tests/test_graph_retrieval_expand.py -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meeting_minutes_app.wiki_core import graph_db, graph_sync  # noqa: E402
from meeting_minutes_app.meeting_pipeline import meeting_workflow as mw  # noqa: E402


def _seed_graph(db_path):
    """note("회의록 A") -[:MENTIONED]- topic("양자컴퓨팅"), person("홍길동") 를 심어둔다."""
    graph_db.init_graph_db(db_path=db_path)
    note_id = graph_sync._upsert_entity("note", "회의록 A", db_path=db_path)
    topic_id = graph_sync._upsert_entity("topic", "양자컴퓨팅", db_path=db_path)
    person_id = graph_sync._upsert_entity("person", "홍길동", db_path=db_path)
    decision_id = graph_sync._upsert_entity("decision", "PoC 범위 확정", db_path=db_path)
    graph_db.upsert_edge(note_id, topic_id, "MENTIONED", db_path=db_path)
    graph_db.upsert_edge(note_id, person_id, "MENTIONED", db_path=db_path)
    graph_db.upsert_edge(note_id, decision_id, "MENTIONED", db_path=db_path)


class TestGraphExpandTitles:
    def test_disabled_by_default_returns_empty(self, tmp_path, monkeypatch):
        db_path = tmp_path / "wiki_graph.db"
        _seed_graph(db_path)
        monkeypatch.setattr(graph_db, "DB_PATH", db_path)
        # config 플래그를 명시적으로 켜지 않았으므로 (기본 False) 빈 리스트여야 한다.
        monkeypatch.setattr(mw, "_c", lambda key, default=None: False if key == "wiki_knowledge.graph_retrieval_expand_enabled" else default)
        assert mw.graph_expand_titles(["회의록 A"]) == []

    def test_expands_to_connected_person_and_topic(self, tmp_path, monkeypatch):
        db_path = tmp_path / "wiki_graph.db"
        _seed_graph(db_path)
        monkeypatch.setattr(graph_db, "DB_PATH", db_path)
        monkeypatch.setattr(mw, "_c", lambda key, default=None: True if key == "wiki_knowledge.graph_retrieval_expand_enabled" else default)

        extra = mw.graph_expand_titles(["회의록 A"])
        assert set(extra) == {"양자컴퓨팅", "홍길동"}  # decision은 person/organization/topic이 아니므로 제외

    def test_no_matching_note_returns_empty(self, tmp_path, monkeypatch):
        db_path = tmp_path / "wiki_graph.db"
        _seed_graph(db_path)
        monkeypatch.setattr(graph_db, "DB_PATH", db_path)
        monkeypatch.setattr(mw, "_c", lambda key, default=None: True if key == "wiki_knowledge.graph_retrieval_expand_enabled" else default)

        assert mw.graph_expand_titles(["존재하지 않는 노트"]) == []

    def test_empty_titles_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mw, "_c", lambda key, default=None: True if key == "wiki_knowledge.graph_retrieval_expand_enabled" else default)
        assert mw.graph_expand_titles([]) == []

    def test_missing_graph_db_fails_gracefully(self, tmp_path, monkeypatch):
        # 그래프 DB가 아예 없어도(백필 전) 예외를 던지지 않고 빈 리스트를 반환해야 한다.
        db_path = tmp_path / "does_not_exist" / "wiki_graph.db"
        monkeypatch.setattr(graph_db, "DB_PATH", db_path)
        monkeypatch.setattr(mw, "_c", lambda key, default=None: True if key == "wiki_knowledge.graph_retrieval_expand_enabled" else default)
        assert mw.graph_expand_titles(["아무 노트"]) == []
