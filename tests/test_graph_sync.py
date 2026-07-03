"""
Wiki Knowledge Graph 회귀 테스트 — LLM/네트워크 없이, 실제 data/wiki_graph.db는 건드리지 않는다.

실행:
    python -m pytest tests/test_graph_sync.py -q
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meeting_minutes_app.wiki_core import graph_db  # noqa: E402
from meeting_minutes_app.wiki_core import graph_sync  # noqa: E402


# ━━━━━━━━━━━━━━━━━━━━ canonical_key / strip_wikilink ━━━━━━━━━━━━━━━━━━━━

class TestNormalization:
    def test_canonical_key_lowercases_and_strips(self):
        assert graph_sync.canonical_key("홍길동") == graph_sync.canonical_key("  홍길동  ")
        assert graph_sync.canonical_key("Hello World") == graph_sync.canonical_key("hello-world")

    def test_strip_wikilink_basic(self):
        assert graph_sync.strip_wikilink("[[홍길동]]") == "홍길동"

    def test_strip_wikilink_with_alias(self):
        assert graph_sync.strip_wikilink("[[Corp|약칭]]") == "Corp"

    def test_strip_wikilink_plain_string(self):
        assert graph_sync.strip_wikilink("그냥텍스트") == "그냥텍스트"

    def test_strip_wikilink_empty(self):
        assert graph_sync.strip_wikilink("") == ""
        assert graph_sync.strip_wikilink(None) == ""


class TestEntityResolver:
    def test_underscore_and_space_separator_merge(self):
        # 실제 vault 데이터에서 발견된 케이스: "260627_5" vs "260627 5"가 별개 노드로 남던 버그
        assert graph_sync.resolve_canonical_key("meeting", "260627_5") == \
            graph_sync.resolve_canonical_key("meeting", "260627 5")

    def test_person_title_suffix_stripped(self):
        assert graph_sync.resolve_canonical_key("person", "홍길동 팀장") == \
            graph_sync.resolve_canonical_key("person", "홍길동")
        assert graph_sync.resolve_canonical_key("person", "김철수 매니저") == \
            graph_sync.resolve_canonical_key("person", "김철수")

    def test_title_suffix_not_stripped_for_other_types(self):
        # person이 아닌 타입은 "팀장" 등을 라벨의 일부로 취급해야 한다 (topic/action 오탐 방지)
        assert graph_sync.resolve_canonical_key("topic", "홍길동 팀장") != \
            graph_sync.resolve_canonical_key("topic", "홍길동")


# ━━━━━━━━━━━━━━━━━━━━ graph_db upsert idempotency ━━━━━━━━━━━━━━━━━━━━

@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "test_wiki_graph.db"
    graph_db.init_graph_db(db_path=p)
    return p


class TestUpsertEntityResolution:
    def test_upsert_entity_merges_title_variant(self, db_path):
        id1 = graph_sync._upsert_entity("person", "홍길동 팀장", db_path=db_path)
        id2 = graph_sync._upsert_entity("person", "홍길동", db_path=db_path)
        assert id1 == id2
        assert len(graph_db.list_nodes(type="person", db_path=db_path)) == 1

    def test_upsert_entity_merges_underscore_variant(self, db_path):
        id1 = graph_sync._upsert_entity("meeting", "260627_5", db_path=db_path)
        id2 = graph_sync._upsert_entity("meeting", "260627 5", db_path=db_path)
        assert id1 == id2
        assert len(graph_db.list_nodes(type="meeting", db_path=db_path)) == 1


class TestUpsertIdempotency:
    def test_upsert_node_no_duplicate(self, db_path):
        id1 = graph_db.upsert_node("person", "홍길동", db_path=db_path)
        id2 = graph_db.upsert_node("person", "홍길동", db_path=db_path)
        assert id1 == id2
        nodes = graph_db.list_nodes(type="person", db_path=db_path)
        assert len(nodes) == 1

    def test_upsert_node_merges_attributes(self, db_path):
        graph_db.upsert_node("action", "벤치마크 준비", {"status": "open"}, db_path=db_path)
        node_id = graph_db.upsert_node("action", "벤치마크 준비", {"due_date": "2026-08-01"}, db_path=db_path)
        node = graph_db.get_node(node_id, db_path=db_path)
        assert node["attributes"]["status"] == "open"
        assert node["attributes"]["due_date"] == "2026-08-01"

    def test_upsert_edge_no_duplicate(self, db_path):
        n1 = graph_db.upsert_node("meeting", "주간회의", db_path=db_path)
        n2 = graph_db.upsert_node("decision", "PoC 범위 확정", db_path=db_path)
        e1 = graph_db.upsert_edge(n1, n2, "DECIDED", db_path=db_path)
        e2 = graph_db.upsert_edge(n1, n2, "DECIDED", db_path=db_path)
        assert e1 == e2
        edges = graph_db.list_edges(relation_type="DECIDED", db_path=db_path)
        assert len(edges) == 1

    def test_upsert_edge_distinct_by_source(self, db_path):
        n1 = graph_db.upsert_node("meeting", "주간회의2", db_path=db_path)
        n2 = graph_db.upsert_node("decision", "다른 결정", db_path=db_path)
        graph_db.upsert_edge(n1, n2, "DECIDED", source_session_id="s1", db_path=db_path)
        graph_db.upsert_edge(n1, n2, "DECIDED", source_session_id="s2", db_path=db_path)
        edges = graph_db.list_edges(from_node_id=n1, to_node_id=n2, db_path=db_path)
        assert len(edges) == 2

    def test_get_neighbors(self, db_path):
        n1 = graph_db.upsert_node("meeting", "이웃테스트회의", db_path=db_path)
        n2 = graph_db.upsert_node("action", "이웃테스트액션", db_path=db_path)
        graph_db.upsert_edge(n1, n2, "CREATED", db_path=db_path)
        result = graph_db.get_neighbors(n1, depth=1, db_path=db_path)
        assert result["node"]["id"] == n1
        assert len(result["edges"]) == 1
        assert any(n["id"] == n2 for n in result["neighbors"])


# ━━━━━━━━━━━━━━━━━━━━ backfill_from_registries end-to-end ━━━━━━━━━━━━━━━━━━━━

class TestBackfillFromRegistries:
    def test_counts_and_relations(self, tmp_path, monkeypatch):
        # registry JSON 파일 fabrication
        action_reg = {
            "version": "1.0",
            "actions": [
                {
                    "action_id": "ACT-260101-001",
                    "title": "벤치마크 자료 준비",
                    "owner": "김철수",
                    "due_date": "2026-08-01",
                    "status": "open",
                    "context": "",
                    "source_meeting": "주간회의",
                    "source_note": "00_Meetings/260101 주간회의.md",
                    "created_at": "2026-01-01",
                    "topics": ["벤치마크"],
                }
            ],
        }
        decision_reg = {
            "version": "1.0",
            "decisions": [
                {
                    "decision_id": "DEC-260101-001",
                    "summary": "PoC 범위를 3개 과제로 확정",
                    "source_meeting": "주간회의",
                    "source_note": "00_Meetings/260101 주간회의.md",
                    "status": "active",
                    "created_at": "2026-01-01",
                    "topics": ["PoC"],
                }
            ],
        }
        (tmp_path / "action_registry.json").write_text(
            json.dumps(action_reg, ensure_ascii=False), encoding="utf-8"
        )
        (tmp_path / "decision_registry.json").write_text(
            json.dumps(decision_reg, ensure_ascii=False), encoding="utf-8"
        )

        db_path = tmp_path / "wiki_graph.db"
        monkeypatch.setattr(graph_sync.wk, "DATA_DIR", tmp_path)
        monkeypatch.setattr(graph_db, "DB_PATH", db_path)

        counts = graph_sync.backfill_from_registries(dry_run=False)
        assert counts["nodes_would_add"] > 0
        assert counts["edges_would_add"] > 0

        meetings = graph_db.list_nodes(type="meeting", db_path=db_path)
        assert len(meetings) == 1
        assert meetings[0]["label"] == "주간회의"

        actions = graph_db.list_nodes(type="action", db_path=db_path)
        decisions = graph_db.list_nodes(type="decision", db_path=db_path)
        persons = graph_db.list_nodes(type="person", db_path=db_path)
        topics = graph_db.list_nodes(type="topic", db_path=db_path)
        assert len(actions) == 1
        assert len(decisions) == 1
        assert len(persons) == 1 and persons[0]["label"] == "김철수"
        assert {t["label"] for t in topics} == {"벤치마크", "PoC"}

        created_edges = graph_db.list_edges(relation_type="CREATED", db_path=db_path)
        decided_edges = graph_db.list_edges(relation_type="DECIDED", db_path=db_path)
        assigned_edges = graph_db.list_edges(relation_type="ASSIGNED_TO", db_path=db_path)
        affects_edges = graph_db.list_edges(relation_type="AFFECTS", db_path=db_path)
        assert len(created_edges) == 1
        assert len(decided_edges) == 1
        assert len(assigned_edges) == 1
        assert len(affects_edges) == 2  # decision->topic, action->topic

    def test_dry_run_does_not_write(self, tmp_path, monkeypatch):
        action_reg = {
            "version": "1.0",
            "actions": [
                {"title": "드라이런 액션", "owner": "", "status": "open",
                 "source_meeting": "드라이런회의", "source_note": "", "topics": []}
            ],
        }
        (tmp_path / "action_registry.json").write_text(
            json.dumps(action_reg, ensure_ascii=False), encoding="utf-8"
        )
        (tmp_path / "decision_registry.json").write_text(
            json.dumps({"version": "1.0", "decisions": []}, ensure_ascii=False), encoding="utf-8"
        )

        db_path = tmp_path / "wiki_graph.db"
        monkeypatch.setattr(graph_sync.wk, "DATA_DIR", tmp_path)
        monkeypatch.setattr(graph_db, "DB_PATH", db_path)

        counts = graph_sync.backfill_from_registries(dry_run=True)
        assert counts["nodes_would_add"] > 0

        # dry_run이므로 실제로는 아무 노드도 남지 않아야 한다
        assert graph_db.list_nodes(type="meeting", db_path=db_path) == []
        assert graph_db.list_nodes(type="action", db_path=db_path) == []
