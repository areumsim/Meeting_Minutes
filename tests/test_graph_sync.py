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


def _write_note(vault: Path, rel_path: str, frontmatter: dict, body: str = "") -> None:
    """parse_frontmatter가 읽을 수 있는 최소 YAML 프론트매터 + 본문을 가진 노트 작성."""
    fm_lines = ["---"]
    for k, v in frontmatter.items():
        if isinstance(v, list):
            fm_lines.append(f"{k}:")
            fm_lines.extend(f'  - "{item}"' for item in v)
        else:
            fm_lines.append(f'{k}: "{v}"')
    fm_lines.append("---")
    text = "\n".join(fm_lines) + "\n\n" + body
    path = vault / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


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


# ━━━━━━━━━━━━━━━━━━━━ backfill_from_vault (위키링크 기반 엔티티 추출) ━━━━━━━━━━━━━━━━━━━━

class TestBackfillFromVault:
    def _setup_vault(self, tmp_path, monkeypatch):
        vault = tmp_path / "vault"
        vault.mkdir()
        db_path = tmp_path / "wiki_graph.db"
        monkeypatch.setattr(graph_db, "DB_PATH", db_path)
        from meeting_minutes_app.common import config_loader
        monkeypatch.setattr(
            config_loader, "get",
            lambda key, default=None: str(vault) if key == "indexing.vault_path" else default,
        )
        return vault, db_path

    def test_wikilink_to_typed_reference_note_creates_edge(self, tmp_path, monkeypatch):
        vault, db_path = self._setup_vault(tmp_path, monkeypatch)
        # 참조 노트: category로 타입 판정 (실제 vault의 실제 스키마)
        _write_note(vault, "01_References/공통/양자 컴퓨팅.md",
                    {"title": "양자 컴퓨팅", "type": "reference", "category": "용어·기술"},
                    "양자 컴퓨팅 설명.")
        _write_note(vault, "01_References/People/서지훈.md",
                    {"title": "서지훈", "type": "reference", "category": "인물"},
                    "서지훈 교수 소개.")
        # 일반 노트: 본문에 위키링크로 참조
        _write_note(vault, "00_Meetings/세미나1.md",
                    {"title": "세미나1", "type": "meeting"},
                    "발표자: [[서지훈]]\n\n주제: [[양자 컴퓨팅]] 논의.")

        counts = graph_sync.backfill_from_vault(dry_run=False)
        assert counts["notes_found"] == 3

        topics = graph_db.list_nodes(type="topic", db_path=db_path)
        persons = graph_db.list_nodes(type="person", db_path=db_path)
        assert {t["label"] for t in topics} == {"양자 컴퓨팅"}
        assert {p["label"] for p in persons} == {"서지훈"}

        edges = graph_db.list_edges(relation_type="MENTIONED", db_path=db_path)
        # 세미나1 -> 양자 컴퓨팅, 세미나1 -> 서지훈 (참조 노트 자신은 스스로를 링크하지 않음)
        assert len(edges) == 2

    def test_prune_shadow_note_nodes_removes_only_orphans(self, tmp_path, monkeypatch):
        """1회성 마이그레이션: 그림자 사본 필터 이전에 들어온 note 노드 정리.

        재백필만으로는 사라지지 않는다(백필은 새 upsert 에만 작용하고 기존 행을 지우지
        않는다). 단 엣지가 붙은 노드는 그래프에 실질적으로 참여하고 있으므로 건너뛴다."""
        db = tmp_path / "g.db"
        monkeypatch.setattr(graph_db, "DB_PATH", db)
        graph_db.init_graph_db(db)

        keep = graph_db.upsert_node("note", "진짜 회의록", db_path=db)
        orphan = graph_db.upsert_node("note", "발표자료.pptx", db_path=db)
        orphan2 = graph_db.upsert_node("note", "data_loader.py", db_path=db)
        linked = graph_db.upsert_node("note", "README.md", db_path=db)
        topic = graph_db.upsert_node("topic", "양자", db_path=db)
        graph_db.upsert_edge(linked, topic, "MENTIONED", db_path=db)

        # dry-run 은 세지만 지우지 않는다
        pre = graph_sync.prune_shadow_note_nodes(dry_run=True, db_path=db)
        assert pre["pruned"] == 2 and pre["skipped_with_edges"] == 1
        assert len(graph_db.list_nodes(type="note", db_path=db)) == 4

        out = graph_sync.prune_shadow_note_nodes(db_path=db)
        assert out["pruned"] == 2
        assert out["skipped_with_edges"] == 1      # README.md 는 엣지가 있어 보존
        labels = {n["label"] for n in graph_db.list_nodes(type="note", db_path=db)}
        assert labels == {"진짜 회의록", "README.md"}
        assert keep and orphan and orphan2         # id 발급 자체는 정상이었다
        # 엣지는 그대로
        assert len(graph_db.list_edges(relation_type="MENTIONED", db_path=db)) == 1

    def test_shadow_copies_and_excluded_dirs_are_skipped(self, tmp_path, monkeypatch):
        """그래프 스캔은 인덱서와 **같은 노트 판정**을 써야 한다.

        갈라져 있었다: 인덱서는 그림자 사본(*.txt.md 등)과 indexing.exclude_dirs 를
        제외하는데 graph_sync 는 `_` 접두만 걸렀다. 실제 볼트에서 인덱서 473개 vs
        그래프 805개 — 그 차이만큼 위키 검색에는 없는 노트가 그래프 노드로 들어갔다
        (그림자 사본이 회의로 오인용되던 문제와 같은 뿌리)."""
        vault = tmp_path / "vault"
        vault.mkdir()
        monkeypatch.setattr(graph_db, "DB_PATH", tmp_path / "wiki_graph.db")
        from meeting_minutes_app.common import config_loader
        monkeypatch.setattr(
            config_loader, "get",
            lambda key, default=None: (
                str(vault) if key == "indexing.vault_path"
                else ["99_원본파일"] if key == "indexing.exclude_dirs"
                else default),
        )
        _write_note(vault, "00_Meetings/진짜회의.md",
                    {"title": "진짜회의", "type": "meeting"}, "내용")
        # 그림자 사본 — 텍스트추출 부산물이라 노트가 아니다
        _write_note(vault, "00_Meetings/requirements.txt.md",
                    {"title": "requirements.txt"}, "raw 텍스트")
        _write_note(vault, "00_Meetings/발표자료.pptx.md",
                    {"title": "발표자료.pptx"}, "raw 텍스트")
        # exclude_dirs 에 걸리는 바이너리 아카이브
        _write_note(vault, "99_원본파일/원본.md", {"title": "원본"}, "raw")
        # 언더스코어 접두(템플릿·색인) — 기존 규칙도 유지돼야 한다
        _write_note(vault, "00_Meetings/_index.md", {"title": "_index"}, "색인")

        counts = graph_sync.backfill_from_vault(dry_run=True)
        assert counts["notes_found"] == 1, "그림자 사본·제외폴더·_접두가 새어 들어왔다"

    def test_unresolved_wikilink_is_skipped_not_errored(self, tmp_path, monkeypatch):
        vault, db_path = self._setup_vault(tmp_path, monkeypatch)
        _write_note(vault, "00_Meetings/일반회의.md",
                    {"title": "일반회의", "type": "meeting"},
                    "참고: [[존재하지 않는 노트]], [[또 다른 회의]]")
        _write_note(vault, "00_Meetings/또 다른 회의.md",
                    {"title": "또 다른 회의", "type": "meeting"}, "내용")

        counts = graph_sync.backfill_from_vault(dry_run=False)
        assert counts["notes_found"] == 2
        # person/organization/topic으로 타입 판정되는 참조 노트가 없으므로 MENTIONED 엣지 0건
        assert graph_db.list_edges(relation_type="MENTIONED", db_path=db_path) == []

    def test_alias_and_heading_anchor_stripped(self, tmp_path, monkeypatch):
        vault, db_path = self._setup_vault(tmp_path, monkeypatch)
        _write_note(vault, "01_References/공통/AWS.md",
                    {"title": "AWS", "type": "reference", "category": "기업·기관"}, "AWS 설명")
        _write_note(vault, "00_Meetings/회의.md",
                    {"title": "회의", "type": "meeting"},
                    "파트너사: [[AWS|아마존웹서비스]] 및 [[AWS#파트너십]] 참고")

        graph_sync.backfill_from_vault(dry_run=False)
        orgs = graph_db.list_nodes(type="organization", db_path=db_path)
        assert {o["label"] for o in orgs} == {"AWS"}
        # 별칭/헤딩 앵커가 있는 두 링크 모두 같은 AWS 노드로 dedup
        edges = graph_db.list_edges(relation_type="MENTIONED", db_path=db_path)
        assert len(edges) == 1

    def test_generic_speaker_attendee_not_added_as_person(self, tmp_path, monkeypatch):
        vault, db_path = self._setup_vault(tmp_path, monkeypatch)
        _write_note(vault, "00_Meetings/회의.md",
                    {"title": "회의", "type": "meeting", "attendees": ["Speaker", "Speaker A"]},
                    "내용 없음")

        graph_sync.backfill_from_vault(dry_run=False)
        assert graph_db.list_nodes(type="person", db_path=db_path) == []

    def test_real_attendee_added_as_person(self, tmp_path, monkeypatch):
        vault, db_path = self._setup_vault(tmp_path, monkeypatch)
        _write_note(vault, "00_Meetings/회의.md",
                    {"title": "회의", "type": "meeting", "attendees": ["김철수", "이영희"]}, "")

        graph_sync.backfill_from_vault(dry_run=False)
        persons = graph_db.list_nodes(type="person", db_path=db_path)
        assert {p["label"] for p in persons} == {"김철수", "이영희"}

    def test_author_frontmatter_added_as_person(self, tmp_path, monkeypatch):
        vault, db_path = self._setup_vault(tmp_path, monkeypatch)
        _write_note(vault, "02_Papers/논문1.md",
                    {"title": "논문1", "type": "paper", "authors": ["Schuld, M."]}, "")

        graph_sync.backfill_from_vault(dry_run=False)
        persons = graph_db.list_nodes(type="person", db_path=db_path)
        assert {p["label"] for p in persons} == {"Schuld, M."}

    def test_legacy_frontmatter_arrays_still_supported(self, tmp_path, monkeypatch):
        """향후/외부 도구가 people/organizations/topics 배열을 쓰는 경우도 계속 지원."""
        vault, db_path = self._setup_vault(tmp_path, monkeypatch)
        _write_note(vault, "00_Meetings/회의.md",
                    {"title": "회의", "type": "meeting",
                     "people": ["홍길동"], "organizations": ["ACME"], "topics": ["PoC"]}, "")

        graph_sync.backfill_from_vault(dry_run=False)
        assert {p["label"] for p in graph_db.list_nodes(type="person", db_path=db_path)} == {"홍길동"}
        assert {o["label"] for o in graph_db.list_nodes(type="organization", db_path=db_path)} == {"ACME"}
        assert {t["label"] for t in graph_db.list_nodes(type="topic", db_path=db_path)} == {"PoC"}

    def test_dry_run_does_not_persist(self, tmp_path, monkeypatch):
        vault, db_path = self._setup_vault(tmp_path, monkeypatch)
        _write_note(vault, "01_References/공통/양자 컴퓨팅.md",
                    {"title": "양자 컴퓨팅", "type": "reference", "category": "용어·기술"}, "")
        _write_note(vault, "00_Meetings/세미나1.md",
                    {"title": "세미나1", "type": "meeting"}, "[[양자 컴퓨팅]]")

        counts = graph_sync.backfill_from_vault(dry_run=True)
        assert counts["edges_would_add"] == 1
        assert graph_db.list_nodes(type="topic", db_path=db_path) == []

    def test_empty_vault_path_returns_zero(self, tmp_path, monkeypatch):
        monkeypatch.setattr(graph_db, "DB_PATH", tmp_path / "wiki_graph.db")
        from meeting_minutes_app.common import config_loader
        monkeypatch.setattr(config_loader, "get", lambda key, default=None: default)
        counts = graph_sync.backfill_from_vault(dry_run=False)
        assert counts["notes_found"] == 0

    def test_reference_note_self_and_mention_merge_into_one_node(self, tmp_path, monkeypatch):
        """[known-limitation 해소] 이전엔 참조 노트 자신이 'note' 타입 노드로, 다른 글의
        위키링크가 person/organization/topic 타입 노드를 별도로 만들어 두 노드로
        분리됐다. 이제는 참조 노트 자신도 그 엔티티 타입으로 직접 upsert되어 하나로
        합쳐진다."""
        vault, db_path = self._setup_vault(tmp_path, monkeypatch)
        _write_note(vault, "01_References/공통/양자 컴퓨팅.md",
                    {"title": "양자 컴퓨팅", "type": "reference", "category": "용어·기술"},
                    "양자 컴퓨팅 설명.")
        _write_note(vault, "00_Meetings/세미나1.md",
                    {"title": "세미나1", "type": "meeting"},
                    "주제: [[양자 컴퓨팅]] 논의.")

        graph_sync.backfill_from_vault(dry_run=False)

        topics = graph_db.list_nodes(type="topic", db_path=db_path)
        assert len(topics) == 1  # 이중 정체성 해소 — 하나의 노드로 병합
        assert topics[0]["attributes"].get("path") == "01_References/공통/양자 컴퓨팅.md"

        notes = graph_db.list_nodes(type="note", db_path=db_path)
        assert {n["label"] for n in notes} == {"세미나1"}  # 참조 노트는 별도 note 노드를 안 만듦

        edges = graph_db.list_edges(relation_type="MENTIONED", db_path=db_path)
        assert len(edges) == 1
        assert edges[0]["to_node_id"] == topics[0]["id"]

    def test_reference_note_processed_before_mention_still_merges(self, tmp_path, monkeypatch):
        """파일 순회 순서와 무관하게(참조 노트가 먼저 처리되는 경우) 동일하게 병합돼야 한다."""
        vault, db_path = self._setup_vault(tmp_path, monkeypatch)
        # 파일명 정렬상 01_References가 00_Meetings보다 나중에 오도록 접두어를 바꿔
        # glob 순서가 달라져도 결과가 같은지 확인.
        _write_note(vault, "00_Meetings/세미나1.md",
                    {"title": "세미나1", "type": "meeting"},
                    "발표자: [[서지훈]]")
        _write_note(vault, "01_References/People/서지훈.md",
                    {"title": "서지훈", "type": "reference", "category": "인물"},
                    "서지훈 교수 소개.")

        graph_sync.backfill_from_vault(dry_run=False)
        persons = graph_db.list_nodes(type="person", db_path=db_path)
        assert len(persons) == 1
        assert persons[0]["attributes"].get("path") == "01_References/People/서지훈.md"


class TestResolveOrCreateNoteNode:
    def test_reuses_existing_entity_node(self, tmp_path):
        db_path = tmp_path / "wiki_graph.db"
        graph_db.init_graph_db(db_path=db_path)
        topic_id = graph_sync._upsert_entity("topic", "양자 컴퓨팅", db_path=db_path)

        resolved_id = graph_sync._resolve_or_create_note_node(
            "양자 컴퓨팅", {"extra": "x"}, db_path=db_path)

        assert resolved_id == topic_id
        assert graph_db.list_nodes(type="note", db_path=db_path) == []
        assert len(graph_db.list_nodes(type="topic", db_path=db_path)) == 1

    def test_creates_note_when_no_entity_matches(self, tmp_path):
        db_path = tmp_path / "wiki_graph.db"
        graph_db.init_graph_db(db_path=db_path)

        graph_sync._resolve_or_create_note_node("일반 노트", db_path=db_path)

        notes = graph_db.list_nodes(type="note", db_path=db_path)
        assert {n["label"] for n in notes} == {"일반 노트"}


class TestSyncSessionGraphNoteResolution:
    def test_related_note_title_reuses_existing_entity_node(self, tmp_path, monkeypatch):
        """related_note_titles로 넘어온 제목이 이미 person/organization/topic 노드로
        존재하면(그래프에 들어가 있는 참조 노트) 별도 'note' 노드를 만들지 않고 그
        엔티티 노드를 재사용해야 한다 — sync_session_graph 경로의 이중 정체성 방지."""
        db_path = tmp_path / "wiki_graph.db"
        graph_db.init_graph_db(db_path=db_path)
        topic_id = graph_sync._upsert_entity("topic", "양자 컴퓨팅", db_path=db_path)
        monkeypatch.setattr(graph_db, "DB_PATH", db_path)

        graph_sync.sync_session_graph(
            session_id="s1", title="세미나2",
            related_note_titles=["양자 컴퓨팅"],
        )

        topics = graph_db.list_nodes(type="topic", db_path=db_path)
        assert len(topics) == 1
        assert topics[0]["id"] == topic_id  # 새 노드가 아니라 기존 노드 재사용
        assert graph_db.list_nodes(type="note", db_path=db_path) == []

        edges = graph_db.list_edges(relation_type="USED_CONTEXT", db_path=db_path)
        assert len(edges) == 1
        assert edges[0]["to_node_id"] == topic_id

    def test_related_note_title_without_existing_entity_creates_note(self, tmp_path, monkeypatch):
        db_path = tmp_path / "wiki_graph.db"
        graph_db.init_graph_db(db_path=db_path)
        monkeypatch.setattr(graph_db, "DB_PATH", db_path)

        graph_sync.sync_session_graph(
            session_id="s1", title="세미나3",
            related_note_titles=["일반 회의 노트"],
        )
        notes = graph_db.list_nodes(type="note", db_path=db_path)
        assert {n["label"] for n in notes} == {"일반 회의 노트"}


class TestSyncSessionGraphDecisionRationale:
    def test_dict_decision_stores_rationale_attribute(self, tmp_path, monkeypatch):
        db_path = tmp_path / "wiki_graph.db"
        graph_db.init_graph_db(db_path=db_path)
        monkeypatch.setattr(graph_db, "DB_PATH", db_path)

        graph_sync.sync_session_graph(
            session_id="s1", title="회의4",
            decisions=[{"summary": "예산은 300만원으로 확정", "rationale": "작년 대비 동결"}],
        )
        decisions = graph_db.list_nodes(type="decision", db_path=db_path)
        assert len(decisions) == 1
        assert decisions[0]["attributes"].get("rationale") == "작년 대비 동결"

    def test_string_decision_still_works_without_rationale(self, tmp_path, monkeypatch):
        db_path = tmp_path / "wiki_graph.db"
        graph_db.init_graph_db(db_path=db_path)
        monkeypatch.setattr(graph_db, "DB_PATH", db_path)

        graph_sync.sync_session_graph(
            session_id="s1", title="회의5",
            decisions=["문자열 결정사항"],
        )
        decisions = graph_db.list_nodes(type="decision", db_path=db_path)
        assert {d["label"] for d in decisions} == {"문자열 결정사항"}
        assert not decisions[0]["attributes"].get("rationale")


class TestMergeNoteDuplicatesIntoEntities:
    """실전 검증(실제 vault 649개 노트 백필) 중 발견: 이중 정체성 수정 이전에 만들어진
    그래프에는 참조 노트가 여전히 "note" 타입 행으로 남아있어, 재백필만으로는 정리되지
    않고 새 엔티티 타입 노드가 "추가로" 생겨 오히려 중복이 늘어난 것처럼 보였다
    (실측: 43개 → 재백필 후 64개). 이 마이그레이션 함수로 기존 note 중복을 살아있는
    엔티티 노드로 병합한다."""

    def _seed_duplicate(self, db_path):
        graph_db.init_graph_db(db_path=db_path)
        # 이중 정체성 수정 이전 상태 재현: 참조 노트가 note 타입으로, 위키링크가 person 타입으로
        note_id = graph_sync._upsert_entity(
            "note", "서지훈", {"path": "01_References/People/서지훈.md", "note_type": "reference"},
            db_path=db_path)
        person_id = graph_sync._upsert_entity("person", "서지훈", db_path=db_path)
        other_note_id = graph_sync._upsert_entity("note", "세미나1", db_path=db_path)
        graph_db.upsert_edge(other_note_id, person_id, "MENTIONED", db_path=db_path)
        return note_id, person_id, other_note_id

    def test_dry_run_counts_without_modifying(self, tmp_path):
        db_path = tmp_path / "wiki_graph.db"
        note_id, person_id, _ = self._seed_duplicate(db_path)

        result = graph_sync.merge_note_duplicates_into_entities(dry_run=True, db_path=db_path)

        assert result["merged"] == 1
        assert graph_db.get_node(note_id, db_path=db_path) is not None  # 아직 삭제 안 됨
        assert len(graph_db.list_nodes(type="note", db_path=db_path)) == 2

    def test_merges_attrs_and_deletes_note_row(self, tmp_path):
        db_path = tmp_path / "wiki_graph.db"
        note_id, person_id, _ = self._seed_duplicate(db_path)

        result = graph_sync.merge_note_duplicates_into_entities(dry_run=False, db_path=db_path)

        assert result["merged"] == 1
        assert graph_db.get_node(note_id, db_path=db_path) is None  # note 중복 행 삭제됨
        persons = graph_db.list_nodes(type="person", db_path=db_path)
        assert len(persons) == 1
        assert persons[0]["attributes"].get("path") == "01_References/People/서지훈.md"
        # 다른 note("세미나1")는 참조 노트가 아니므로 병합 대상이 아니어야 함
        assert {n["label"] for n in graph_db.list_nodes(type="note", db_path=db_path)} == {"세미나1"}

    def test_edges_repointed_to_surviving_entity(self, tmp_path):
        db_path = tmp_path / "wiki_graph.db"
        note_id, person_id, other_note_id = self._seed_duplicate(db_path)
        # note 중복 쪽에도 엣지를 하나 걸어 재연결이 실제로 일어나는지 확인
        graph_db.upsert_edge(note_id, person_id, "MENTIONED", source_note="dup", db_path=db_path)

        graph_sync.merge_note_duplicates_into_entities(dry_run=False, db_path=db_path)

        neighbors = graph_db.get_neighbors(person_id, depth=1, db_path=db_path)
        # note_id를 향하던 엣지가 사라지지 않고 person_id 쪽으로 남아있어야 함(자기 자신 제외)
        neighbor_ids = {n["id"] for n in neighbors["neighbors"]}
        assert note_id not in neighbor_ids
        assert other_note_id in neighbor_ids

    def test_no_duplicates_is_noop(self, tmp_path):
        db_path = tmp_path / "wiki_graph.db"
        graph_db.init_graph_db(db_path=db_path)
        graph_sync._upsert_entity("person", "이영희", db_path=db_path)
        result = graph_sync.merge_note_duplicates_into_entities(dry_run=False, db_path=db_path)
        assert result["merged"] == 0
