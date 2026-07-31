# -*- coding: utf-8 -*-
"""'같은 인물이 같은 주제로 얘기한 자료' 회수 — 근거 있는 회수만 본문에 들인다.

배경: 유사도 회수(임베딩/TF-IDF)는 이 볼트에서 관련 문서를 못 찾는다 — 실측에서 전사에
대해 그 전사 자신의 회의록이 1위로 회수되는 비율이 임베딩 0%·TF-IDF 0%였다
(scripts/measure_retrieval_floor.py · docs/검색랭킹_이론과근거.md §2.2.1). 그래서 유사도
회수분은 회의록 본문에 넣지 않는다.

반면 그래프의 `note -[:MENTIONED]-> person|topic` 은 추정이 아니라 기록이다. 두 노트가
같은 person 노드를 가리키면 유사도가 아니라 동일성이고, "왜 걸렸나"를 문장으로 적을 수
있다. 근거를 적을 수 있는 회수만 본문에 들인다 — 이 경계가 이 파일의 검증 대상이다.
"""

import pytest

from meeting_minutes_app.wiki_core import graph_db, graph_sync
from meeting_minutes_app.meeting_pipeline import finalize as fz


@pytest.fixture
def graph(tmp_path, monkeypatch):
    """note→person/topic 엣지를 가진 작은 그래프."""
    db = tmp_path / "g.db"
    monkeypatch.setattr(graph_db, "_DB_PATH_OVERRIDE", db, raising=False)
    orig = graph_db._conn

    def _conn(db_path=None):
        return orig(db_path or db)
    monkeypatch.setattr(graph_db, "_conn", _conn)
    graph_db.init_graph_db(db)

    def node(t, label):
        return graph_db.upsert_node(
            type=t, label=label,
            canonical_key=graph_sync.resolve_canonical_key(t, label), db_path=db)

    n = {}
    for label in ("남우진 세미나 1", "남우진 세미나 2", "서지훈 세미나", "무관 회의"):
        n[label] = node("note", label)
    for label in ("남우진", "서지훈", "A"):
        n[label] = node("person", label)
    for label in ("양자 머신러닝", "NISQ", "볼츠만 머신"):
        n[label] = node("topic", label)

    def edge(a, b):
        graph_db.upsert_edge(from_node_id=n[a], to_node_id=n[b],
                             relation_type="MENTIONED", db_path=db)

    edge("남우진 세미나 1", "남우진"); edge("남우진 세미나 1", "양자 머신러닝")
    edge("남우진 세미나 1", "NISQ")
    edge("남우진 세미나 2", "남우진"); edge("남우진 세미나 2", "볼츠만 머신")
    edge("서지훈 세미나", "서지훈");   edge("서지훈 세미나", "양자 머신러닝")
    edge("서지훈 세미나", "NISQ")
    edge("무관 회의", "A")             # 자리표시자 인물만
    return db


class TestPersonMatch:
    def test_same_person_retrieved(self, graph):
        rows = graph_sync.notes_sharing_entities(["남우진"], [])
        titles = [r["title"] for r in rows]
        assert "남우진 세미나 1" in titles and "남우진 세미나 2" in titles
        assert "서지훈 세미나" not in titles

    def test_reason_is_stated(self, graph):
        rows = graph_sync.notes_sharing_entities(["남우진"], ["양자 머신러닝"])
        top = next(r for r in rows if r["title"] == "남우진 세미나 1")
        assert "같은 인물: 남우진" in top["reason"]
        assert "같은 주제: 양자 머신러닝" in top["reason"]

    def test_person_title_suffix_normalized(self, graph):
        """'남우진 교수'도 같은 사람으로 본다(resolve_canonical_key 의 직함 제거)."""
        rows = graph_sync.notes_sharing_entities(["남우진 교수"], [])
        assert [r["title"] for r in rows]

    def test_placeholder_speakers_ignored(self, graph):
        """화자 특정 실패로 생긴 'A'/'발언자A'로 서로 다른 회의가 묶이면 안 된다."""
        assert graph_sync.notes_sharing_entities(["A", "발언자B"], []) == []


class TestTopicMatch:
    def test_single_topic_overlap_rejected(self, graph):
        """주제 1개 겹침은 채택하지 않는다 — 'NISQ' 하나로 무관한 회의가 묶인다."""
        assert graph_sync.notes_sharing_entities([], ["NISQ"]) == []

    def test_two_topic_overlap_accepted(self, graph):
        rows = graph_sync.notes_sharing_entities([], ["NISQ", "양자 머신러닝"])
        titles = [r["title"] for r in rows]
        assert "남우진 세미나 1" in titles and "서지훈 세미나" in titles
        assert "남우진 세미나 2" not in titles   # 겹침 1개(볼츠만만)

    def test_unknown_topic_is_silent(self, graph):
        """그래프에 없는 용어는 조용히 0건 — 억지로 맞추지 않는다."""
        assert graph_sync.notes_sharing_entities([], ["존재하지않는용어", "또다른것"]) == []


class TestOrderingAndLimits:
    def test_person_match_ranks_above_topic_only(self, graph):
        rows = graph_sync.notes_sharing_entities(["남우진"], ["NISQ", "양자 머신러닝"])
        assert rows[0]["people"], "인물이 겹친 노트가 먼저 와야 한다"

    def test_exclude_titles_respected(self, graph):
        rows = graph_sync.notes_sharing_entities(
            ["남우진"], [], exclude_titles=["남우진 세미나 1"])
        assert "남우진 세미나 1" not in [r["title"] for r in rows]

    def test_limit_respected(self, graph):
        assert len(graph_sync.notes_sharing_entities(["남우진"], [], limit=1)) == 1

    def test_no_seed_no_query(self, graph):
        assert graph_sync.notes_sharing_entities([], []) == []


class TestRelatedNotesSectionWording:
    """머리말이 목록에 실제로 담긴 종류와 어긋나지 않아야 한다."""

    REASON = [{"title": "남우진 세미나", "filename": "a.md", "heading": "",
               "match_reason": "같은 인물: 남우진"}]
    SIM = [{"title": "비슷한노트", "filename": "b.md", "heading": "", "score": 0.31}]

    def test_reason_only_does_not_claim_unverified(self):
        out = fz.build_related_notes_section(self.REASON)
        assert "회의록 작성에 참고했습니다" in out
        assert "관련성은 검증되지 않았습니다" not in out
        assert "같은 인물: 남우진" in out

    def test_similarity_only_carries_caveat(self):
        out = fz.build_related_notes_section(self.SIM)
        assert "관련성은 검증되지 않았습니다" in out
        assert "회의록 작성에 참고했습니다" not in out

    def test_mixed_carries_both(self):
        out = fz.build_related_notes_section(self.REASON + self.SIM)
        assert "회의록 작성에 참고했습니다" in out
        assert "관련성은 검증되지 않았습니다" in out

    def test_titles_only_counts_as_similarity(self):
        out = fz.build_related_notes_section([], titles=["제목만노트"])
        assert "관련성은 검증되지 않았습니다" in out


class TestInjectedMemoBoundary:
    """본문에 들어가는 블록은 '사용 범위'를 명시해야 한다."""

    def test_memo_states_scope(self):
        rows = [{"title": "남우진 세미나", "people": ["남우진"], "topics": [],
                 "reason": "같은 인물: 남우진"}]
        from meeting_minutes_app.meeting_pipeline import meeting_workflow as mw
        memo = mw._entity_overlap_memo(rows, indexer=None, obs=None)
        assert "[[남우진 세미나]]" in memo
        assert "같은 인물: 남우진" in memo
        # 이전 회의 내용이 이번 회의 논의/결정으로 옮겨가지 않게 못 박는 문구
        assert "논의된 것처럼 쓰지 말고" in memo
        assert "결정·액션·일정으로" in memo

    def test_empty_rows_no_block(self):
        from meeting_minutes_app.meeting_pipeline import meeting_workflow as mw
        assert mw._entity_overlap_memo([], None, None) == ""
