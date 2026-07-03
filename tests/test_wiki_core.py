"""
규칙 기반 코어 회귀 테스트 — LLM/네트워크 없이 실행 가능.

실행:
    python -m pytest tests/ -q
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meeting_minutes_app.common import json_utils as ju  # noqa: E402
from meeting_minutes_app.wiki_core import wiki_knowledge as wk  # noqa: E402
from meeting_minutes_app.wiki_core import vault_indexer as vi  # noqa: E402
from meeting_minutes_app.common import date_utils as du  # noqa: E402


# ━━━━━━━━━━━━━━━━━━━━ json_utils ━━━━━━━━━━━━━━━━━━━━

class TestParseJsonLoose:
    def test_plain_list(self):
        assert ju.parse_json_loose('[{"a": 1}]', expect="list") == [{"a": 1}]

    def test_code_fence(self):
        raw = '```json\n[{"claim": "x"}]\n```'
        assert ju.parse_json_loose(raw, expect="list") == [{"claim": "x"}]

    def test_leading_prose(self):
        raw = '다음은 추출 결과입니다:\n[{"claim": "y"}]\n이상입니다.'
        assert ju.parse_json_loose(raw, expect="list") == [{"claim": "y"}]

    def test_bracket_inside_string(self):
        # 문자열 리터럴 안의 대괄호가 정규식 방식처럼 과잉 매칭되지 않아야 함
        raw = 'result: [{"t": "배열 [1,2] 포함 텍스트"}] trailing'
        parsed = ju.parse_json_loose(raw, expect="list")
        assert parsed == [{"t": "배열 [1,2] 포함 텍스트"}]

    def test_trailing_comma_repair(self):
        raw = '[{"a": 1,},]'
        assert ju.parse_json_loose(raw, expect="list") == [{"a": 1}]

    def test_smart_quotes_repair(self):
        raw = '{“key”: “value”}'
        assert ju.parse_json_loose(raw, expect="dict") == {"key": "value"}

    def test_expect_mismatch_returns_default(self):
        assert ju.parse_json_loose('{"a": 1}', expect="list", default=[]) == []

    def test_garbage_returns_default(self):
        assert ju.parse_json_loose("완전한 산문 응답입니다.", default=None) is None

    def test_empty_and_none(self):
        assert ju.parse_json_loose("", default=[]) == []
        assert ju.parse_json_loose(None, default={}) == {}

    def test_nested_dict(self):
        raw = '설명\n{"verdict": "match", "meta": {"depth": [1, 2]}}'
        parsed = ju.parse_json_loose(raw, expect="dict")
        assert parsed["verdict"] == "match"
        assert parsed["meta"]["depth"] == [1, 2]


class TestExtractBalanced:
    def test_escaped_quote_in_string(self):
        raw = '[{"t": "인용 \\" 부호"}]'
        assert ju.extract_balanced(raw, "[", "]") == raw

    def test_unbalanced_returns_none(self):
        assert ju.extract_balanced('[{"a": 1}', "[", "]") is None


# ━━━━━━━━━━━━━━━━━━ wiki_knowledge ━━━━━━━━━━━━━━━━━━

MINUTES_SAMPLE = """# 회의록

## 논의 내용
- 일반 논의

## 결정사항
- PoC 범위를 3개 과제로 확정
- 차기 회의는 격주로 진행

## 액션 아이템
- 김철수: 벤치마크 자료 준비
"""


class TestDecisionExtraction:
    def test_extracts_bullets_under_decision_header(self):
        decisions = wk.extract_decisions_from_minutes(MINUTES_SAMPLE)
        assert decisions == ["PoC 범위를 3개 과제로 확정", "차기 회의는 격주로 진행"]

    def test_no_decision_section(self):
        assert wk.extract_decisions_from_minutes("# 회의록\n- 내용") == []


class TestActionRegistry:
    def test_accumulate_and_dedup(self, tmp_path):
        reg = tmp_path / "action_registry.json"
        actions = json.dumps([
            {"task": "벤치마크 자료 준비", "assignee": "김철수", "deadline": "", "context": ""},
        ], ensure_ascii=False)
        assert wk.update_action_registry_from_actions(actions, "주간회의", registry_path=reg) == 1
        # 동일 (회의, task) → 중복 스킵
        assert wk.update_action_registry_from_actions(actions, "주간회의", registry_path=reg) == 0
        # 다른 회의명 → 새 항목
        assert wk.update_action_registry_from_actions(actions, "월간회의", registry_path=reg) == 1
        data = json.loads(reg.read_text(encoding="utf-8"))
        assert len(data["actions"]) == 2
        assert all(a["action_id"].startswith("ACT-") for a in data["actions"])

    def test_invalid_json_ignored(self, tmp_path):
        reg = tmp_path / "action_registry.json"
        assert wk.update_action_registry_from_actions("not-json", "회의", registry_path=reg) == 0


class TestDecisionRegistry:
    def test_accumulate_and_dedup(self, tmp_path):
        reg = tmp_path / "decision_registry.json"
        decisions = ["PoC 범위를 3개 과제로 확정"]
        assert wk.update_decision_registry_from_minutes(decisions, "주간회의", registry_path=reg) == 1
        assert wk.update_decision_registry_from_minutes(decisions, "주간회의", registry_path=reg) == 0
        data = json.loads(reg.read_text(encoding="utf-8"))
        assert data["decisions"][0]["decision_id"].startswith("DEC-")


class TestFeatureGates:
    @pytest.fixture
    def disabled(self, monkeypatch):
        orig = wk._c

        def fake_c(key, default=None):
            if key == "wiki_knowledge.enabled":
                return False
            return orig(key, default)

        monkeypatch.setattr(wk, "_c", fake_c)

    def test_gate_off_blocks_everything(self, disabled, tmp_path):
        assert wk._feature_enabled() is False
        assert wk.build_wiki_context_package(["노트"]) == {}
        assert wk.build_wiki_update_proposal("회의", MINUTES_SAMPLE, ["노트"]) == {}
        assert wk.update_action_registry_from_actions(
            '[{"task":"x"}]', "회의", registry_path=tmp_path / "a.json") == 0
        assert wk.update_decision_registry_from_minutes(
            ["d"], "회의", registry_path=tmp_path / "d.json") == 0

    def test_save_skips_empty(self, tmp_path):
        assert wk.save_wiki_context_package({}, tmp_path) is None
        assert wk.save_wiki_update_proposal({}, tmp_path) is None
        assert not list(tmp_path.iterdir())

    def test_sub_gate(self, monkeypatch):
        orig = wk._c

        def fake_c(key, default=None):
            if key == "wiki_knowledge.update_proposals_enabled":
                return False
            return orig(key, default)

        monkeypatch.setattr(wk, "_c", fake_c)
        assert wk._feature_enabled() is True
        assert wk._feature_enabled("update_proposals_enabled") is False
        assert wk.build_wiki_update_proposal("회의", MINUTES_SAMPLE, ["노트"]) == {}


class TestWikiContextFormat:
    def test_priority_order(self):
        pkg = {
            "previous_decisions": ["D1"],
            "open_actions": ["A1"],
            "related_notes": ["N1"],
            "known_entities": ["E1"],
        }
        out = wk.format_wiki_context_for_prompt(pkg)
        assert out.index("[이전 결정사항]") < out.index("[미완료 액션]") < out.index("[관련 노트]")
        assert "[[N1]]" in out

    def test_max_chars_truncation(self):
        pkg = {"previous_decisions": [f"결정사항 {i} " + "상세내용" * 20 for i in range(30)]}
        out = wk.format_wiki_context_for_prompt(pkg, max_chars=300)
        assert len(out) <= 320  # 잘림 마커 포함 여유

    def test_empty_package(self):
        assert wk.format_wiki_context_for_prompt({}) == ""


class TestProposalStructure:
    def test_proposal_v2_sections(self):
        claim_results = [
            {"claim": "충돌 주장", "verdict": "conflict", "summary": "s",
             "evidence": "e", "sources": ["노트A"]},
            {"claim": "미확인 주장", "verdict": "unknown", "summary": "",
             "evidence": "", "sources": [], "no_vault_data": True},
        ]
        proposal = wk.build_wiki_update_proposal(
            "주간회의", MINUTES_SAMPLE, ["노트A"], claim_results=claim_results)
        assert proposal["source_meeting"] == "주간회의"
        assert proposal["proposals"], "관련 노트 참조 후보가 있어야 함"
        assert all(p["status"] == "suggested" for p in proposal["proposals"])
        assert proposal["conflicts"], "conflict verdict → conflicts 섹션 생성"


# ━━━━━━━━━━━━━━━━━━ vault_indexer ━━━━━━━━━━━━━━━━━━

class TestRRF:
    def test_fusion_ordering(self):
        # A: 1위+2위, B: 2위+1위, C: 3위 단독
        fused = vi._rrf_fuse([["A", "B", "C"], ["B", "A"]])
        assert fused["A"] == pytest.approx(1 / 61 + 1 / 62)
        assert fused["B"] == pytest.approx(1 / 62 + 1 / 61)
        assert fused["C"] == pytest.approx(1 / 63)
        assert fused["A"] > fused["C"]

    def test_l2_normalize_and_dot(self):
        v = vi._l2_normalize([3.0, 4.0])
        assert vi._dot(v, v) == pytest.approx(1.0)


def _make_hybrid_indexer():
    """네트워크 없이 하이브리드 검색을 검증하기 위한 수동 구성 인덱서."""
    ix = vi.VaultIndexer(vault_path="unused", index_path="unused.json")
    ix._built = True
    ix._notes = {
        "a.md": {"title": "키워드노트", "wikilink_title": "키워드노트",
                 "snippet": "", "date": "", "type": "", "tf": {"양자": 1.0}},
        "b.md": {"title": "의미노트", "wikilink_title": "의미노트",
                 "snippet": "", "date": "", "type": "", "tf": {}},
    }
    ix._idf = {"양자": 1.0}
    # 임베딩: 쿼리와 b.md가 거의 동일 방향, a.md는 직교
    ix._emb_loaded = True
    ix._emb = {"notes": {
        "a.md": {"h": "x", "v": [0.0, 1.0]},
        "b.md": {"h": "y", "v": [1.0, 0.0]},
    }}
    return ix


class TestHybridSearch:
    def test_embedding_only_note_recovered(self, monkeypatch):
        monkeypatch.setattr(
            vi, "_c",
            lambda key, default=None: {
                "wiki_knowledge.embedding_enabled": True,
                "wiki_knowledge.embedding_min_cosine": 0.25,
            }.get(key, default),
        )
        ix = _make_hybrid_indexer()
        ix._query_vec_cache["양자 검색"] = [1.0, 0.0]  # 네트워크 호출 회피
        results = ix.search("양자 검색", limit=5)
        titles = [r["title"] for r in results]
        # TF-IDF 매치(키워드노트)와 임베딩 매치(의미노트) 모두 회수
        assert "키워드노트" in titles
        assert "의미노트" in titles
        sem = next(r for r in results if r["title"] == "의미노트")
        assert sem["score"] == 0.0 and sem["cosine"] >= 0.25
        # find_related는 임베딩 전용 노트도 유지
        related = ix.find_related("양자 검색", limit=5)
        assert "의미노트" in related

    def test_fallback_without_embeddings(self, monkeypatch):
        monkeypatch.setattr(
            vi, "_c",
            lambda key, default=None: {"wiki_knowledge.embedding_enabled": False}.get(key, default),
        )
        ix = _make_hybrid_indexer()
        results = ix.search("양자", limit=5)
        assert [r["title"] for r in results] == ["키워드노트"]
        assert "cosine" not in results[0]


# ━━━━━━━━━━━━━━━━━━━ date_utils ━━━━━━━━━━━━━━━━━━━

class TestDateUtils:
    def test_parse_iso_dashed(self):
        assert du.parse_iso_date_from_text("2026-07-02 주간회의.m4a") == "2026-07-02"

    def test_parse_iso_compact(self):
        assert du.parse_iso_date_from_text("20260702_회의.mp3") == "2026-07-02"

    def test_session_dt_with_time(self):
        out = du.parse_session_dt_from_path("2026-07-02 14.30 주간회의.m4a")
        assert out == "2026년 07월 02일 14:30"

    def test_invalid_date_rejected(self):
        assert du.parse_iso_date_from_text("2026-13-45 잘못된날짜") == ""

    def test_iso_to_yymmdd(self):
        assert du.iso_to_yymmdd("2026-07-02") == "260702"
