"""mcp_server.py 회귀 테스트 — LLM/네트워크 없이, 실제 data/wiki_graph.db는 건드리지 않는다.
FastMCP 프로토콜 자체(HTTP 라우팅 등)는 검증하지 않는다 — 도구 함수가 graph_db.py를
올바르게 감싸는지, 인증 검증기가 config.json의 토큰 목록을 정확히 확인하는지만 본다.

실행:
    python -m pytest tests/test_mcp_server.py -q
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from meeting_minutes_app.wiki_core import graph_db, graph_sync  # noqa: E402

# fastmcp 는 원격 MCP(/mcp) 전용 의존성으로 포터블 배포본에는 제외된다(pyproject 참고).
# 미설치 환경에서는 이 모듈 테스트만 스킵하고 나머지 스위트는 정상 수집되게 한다.
mcp_server = pytest.importorskip(
    "meeting_minutes_app.wiki_core.mcp_server",
    reason="fastmcp 미설치 — /mcp 서버 테스트 스킵",
)


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    p = tmp_path / "test_wiki_graph.db"
    graph_db.init_graph_db(db_path=p)
    monkeypatch.setattr(graph_db, "DB_PATH", p)
    return p


def _run(coro):
    return asyncio.run(coro)


class TestConfigTokenVerifier:
    def test_valid_token_accepted(self, monkeypatch):
        from meeting_minutes_app.common import config_loader

        monkeypatch.setattr(
            config_loader, "get",
            lambda key, default=None: [{"token": "abc123", "name": "alice"}] if key == "mcp.allowed_tokens" else default,
        )
        verifier = mcp_server.ConfigTokenVerifier()
        result = _run(verifier.verify_token("abc123"))
        assert result is not None
        assert result.client_id == "alice"

    def test_plain_string_token_accepted(self, monkeypatch):
        from meeting_minutes_app.common import config_loader

        monkeypatch.setattr(
            config_loader, "get",
            lambda key, default=None: ["plaintoken"] if key == "mcp.allowed_tokens" else default,
        )
        verifier = mcp_server.ConfigTokenVerifier()
        assert _run(verifier.verify_token("plaintoken")) is not None

    def test_unknown_token_rejected(self, monkeypatch):
        from meeting_minutes_app.common import config_loader

        monkeypatch.setattr(
            config_loader, "get",
            lambda key, default=None: [{"token": "abc123", "name": "alice"}] if key == "mcp.allowed_tokens" else default,
        )
        verifier = mcp_server.ConfigTokenVerifier()
        assert _run(verifier.verify_token("wrong")) is None

    def test_empty_allowlist_rejects_everything(self, monkeypatch):
        from meeting_minutes_app.common import config_loader

        monkeypatch.setattr(config_loader, "get", lambda key, default=None: default)
        verifier = mcp_server.ConfigTokenVerifier()
        assert _run(verifier.verify_token("anything")) is None


class TestGraphTools:
    def _seed(self, db_path):
        note_id = graph_sync._upsert_entity("note", "회의록 A", db_path=db_path)
        topic_id = graph_sync._upsert_entity("topic", "양자컴퓨팅", db_path=db_path)
        meeting_id = graph_sync._upsert_entity("meeting", "주간회의", db_path=db_path)
        action_open = graph_sync._upsert_entity("action", "벤치마크 준비", {"status": "open"}, db_path=db_path)
        action_done = graph_sync._upsert_entity("action", "킥오프 자료", {"status": "done"}, db_path=db_path)
        decision_id = graph_sync._upsert_entity("decision", "PoC 범위 확정", db_path=db_path)
        graph_db.upsert_edge(note_id, topic_id, "MENTIONED", db_path=db_path)
        graph_db.upsert_edge(meeting_id, note_id, "USED_CONTEXT", db_path=db_path)
        graph_db.upsert_edge(meeting_id, action_open, "CREATED", db_path=db_path)
        graph_db.upsert_edge(meeting_id, action_done, "CREATED", db_path=db_path)
        graph_db.upsert_edge(meeting_id, decision_id, "DECIDED", db_path=db_path)
        graph_db.upsert_edge(decision_id, topic_id, "AFFECTS", db_path=db_path)
        return {
            "note": note_id, "topic": topic_id, "meeting": meeting_id,
            "action_open": action_open, "action_done": action_done, "decision": decision_id,
        }

    def test_list_graph_nodes(self, db_path):
        self._seed(db_path)
        nodes = mcp_server.list_graph_nodes(type="topic")
        assert len(nodes) == 1
        assert nodes[0]["label"] == "양자컴퓨팅"

    def test_get_graph_node(self, db_path):
        ids = self._seed(db_path)
        node = mcp_server.get_graph_node(ids["meeting"])
        assert node["label"] == "주간회의"
        assert mcp_server.get_graph_node("does-not-exist") is None

    def test_get_graph_neighbors(self, db_path):
        ids = self._seed(db_path)
        result = mcp_server.get_graph_neighbors(ids["meeting"], depth=1)
        neighbor_labels = {n["label"] for n in result["neighbors"]}
        assert "회의록 A" in neighbor_labels
        assert "벤치마크 준비" in neighbor_labels

    def test_find_graph_path(self, db_path):
        ids = self._seed(db_path)
        path = mcp_server.find_graph_path(ids["meeting"], ids["topic"], max_depth=4)
        assert path is not None
        assert path[0]["node"]["id"] == ids["meeting"]
        assert path[-1]["node"]["id"] == ids["topic"]

    def test_get_topic_status_filters_open_actions_and_finds_decisions(self, db_path):
        self._seed(db_path)
        status = mcp_server.get_topic_status("양자컴퓨팅")
        assert status["topic"] is not None
        # topic의 1-hop 이웃은 decision(AFFECTS)뿐 — action/meeting은 2-hop 이상이라 여기 없음
        assert len(status["decisions"]) == 1
        assert status["decisions"][0]["label"] == "PoC 범위 확정"

    def test_get_topic_status_unknown_topic(self, db_path):
        status = mcp_server.get_topic_status("존재하지않는주제")
        assert status["topic"] is None
        assert status["open_actions"] == []

    def test_find_meetings_mentioning_two_hop(self, db_path):
        ids = self._seed(db_path)
        result = mcp_server.find_meetings_mentioning("양자컴퓨팅")
        assert result["entity"]["id"] == ids["topic"]
        meeting_labels = {m["label"] for m in result["meetings"]}
        assert "주간회의" in meeting_labels

    def test_find_meetings_mentioning_unknown_entity(self, db_path):
        result = mcp_server.find_meetings_mentioning("아무도아님")
        assert result["entity"] is None
        assert result["meetings"] == []
