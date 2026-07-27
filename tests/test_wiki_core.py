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

from meeting_minutes_app.meeting_pipeline import json_utils as ju  # noqa: E402
from meeting_minutes_app.wiki_core import wiki_knowledge as wk  # noqa: E402
from meeting_minutes_app.wiki_core import vault_indexer as vi  # noqa: E402
from meeting_minutes_app.wiki_core import wiki_ask as wa  # noqa: E402
from meeting_minutes_app.meeting_pipeline import date_utils as du  # noqa: E402


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
        assert decisions == [
            {"summary": "PoC 범위를 3개 과제로 확정", "rationale": ""},
            {"summary": "차기 회의는 격주로 진행", "rationale": ""},
        ]

    def test_no_decision_section(self):
        assert wk.extract_decisions_from_minutes("# 회의록\n- 내용") == []

    def test_numbered_list_with_rationale_subline(self):
        minutes = (
            "## 결정 사항\n"
            "1. **결정 요약**: PoC 범위를 3개 과제로 확정\n"
            "   - 배경: 리소스 제약으로 우선순위가 높은 과제만 선정\n"
            "2. **결정 요약**: 차기 회의는 격주로 진행\n"
        )
        decisions = wk.extract_decisions_from_minutes(minutes)
        assert decisions == [
            {"summary": "**결정 요약**: PoC 범위를 3개 과제로 확정",
             "rationale": "리소스 제약으로 우선순위가 높은 과제만 선정"},
            {"summary": "**결정 요약**: 차기 회의는 격주로 진행", "rationale": ""},
        ]

    def test_duplicate_rationale_lines_prefers_real_content_over_placeholder(self):
        """[실전 검증 중 발견] LLM이 한 결정에 '배경:' 서브라인을 두 번 쓰는 경우가 있다
        (실제 내용 + '스크립트에 명시되지 않음' 플레이스홀더). 마지막 줄로 덮어써서
        실제 내용이 사라지면 안 된다 — 순서와 무관하게 실제 내용이 이겨야 한다."""
        minutes_real_then_placeholder = (
            "## 결정 사항\n"
            "1. **문제 정의 고도화**\n"
            "   - 배경: 화학 물성 최적화 문제를 양자 컴퓨팅에 적합하게 재구성하기로 결정\n"
            "   - 배경: 스크립트에 명시되지 않음\n"
        )
        decisions = wk.extract_decisions_from_minutes(minutes_real_then_placeholder)
        assert decisions[0]["rationale"] == "화학 물성 최적화 문제를 양자 컴퓨팅에 적합하게 재구성하기로 결정"

        minutes_placeholder_then_real = (
            "## 결정 사항\n"
            "1. **기념품 예산**\n"
            "   - 배경: 스크립트에 명시되지 않음\n"
            "   - 배경: 300만원 이하로 설정하여 품목 및 예산 조정\n"
        )
        decisions2 = wk.extract_decisions_from_minutes(minutes_placeholder_then_real)
        assert decisions2[0]["rationale"] == "300만원 이하로 설정하여 품목 및 예산 조정"

    def test_rationale_subline_not_treated_as_new_decision(self):
        """'- 배경: ...'도 '-'로 시작하지만 최상위 결정 항목으로 오인되면 안 된다."""
        minutes = "## 결정 사항\n- 예산은 300만원으로 확정\n  - 배경: 작년 대비 동결\n"
        decisions = wk.extract_decisions_from_minutes(minutes)
        assert len(decisions) == 1
        assert decisions[0]["rationale"] == "작년 대비 동결"


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


class TestFilterActionsByTopic:
    """[실전 검증 중 발견] owner가 빈 문자열인 액션이 모든 참석자와 매칭된 것처럼
    처리되던 버그(""가 임의 문자열의 부분문자열이라 `owner_norm in an`이 항상 True)."""

    def _actions(self):
        return [
            {"title": "기념품 예산 최종 승인", "owner": "", "status": "open", "topics": []},
            {"title": "아크라이트 협업 방안 검토", "owner": "최민석", "status": "open", "topics": ["아크라이트"]},
        ]

    def test_empty_owner_does_not_match_every_attendee(self):
        result = wk._filter_actions_by_topic(
            self._actions(), topic="양자컴퓨팅 연구 계획", attendees=["최민석", "심아름"], limit=10,
        )
        titles = {a["title"] for a in result}
        assert "기념품 예산 최종 승인" not in titles
        assert "아크라이트 협업 방안 검토" in titles

    def test_no_match_returns_empty_not_everything(self):
        """토픽/참석자 등 필터 기준은 있는데 매칭이 하나도 없으면 전체를 반환하지 않고
        빈 목록을 반환해야 한다 — 무관한 다른 프로젝트 항목을 잡음으로 보여주지 않기 위함."""
        actions = [{"title": "기념품 예산 최종 승인", "owner": "", "status": "open", "topics": []}]
        result = wk._filter_actions_by_topic(
            actions, topic="양자컴퓨팅 아크라이트", attendees=["강민호"], limit=10,
        )
        assert result == []

    def test_no_filter_criteria_returns_all(self):
        actions = self._actions()
        result = wk._filter_actions_by_topic(actions, topic="", attendees=[], limit=10)
        assert len(result) == 2

    def test_extra_keywords_from_memo_match(self):
        result = wk._filter_actions_by_topic(
            self._actions(), topic="", attendees=[], limit=10,
            extra_keywords=["아크라이트"],
        )
        assert [a["title"] for a in result] == ["아크라이트 협업 방안 검토"]


class TestFilterDecisionsByTopic:
    def _decisions(self):
        return [
            {"summary": "기념품 예산 최종 승인", "created_at": "2026-07-01", "topics": []},
            {"summary": "아크라이트과 협업 범위 확정", "created_at": "2026-07-02", "topics": ["아크라이트"]},
        ]

    def test_no_topic_returns_all_sorted_by_recency(self):
        result = wk._filter_decisions_by_topic(self._decisions(), topic="", limit=10)
        assert [d["summary"] for d in result] == ["아크라이트과 협업 범위 확정", "기념품 예산 최종 승인"]

    def test_matching_topic_filters_out_unrelated(self):
        result = wk._filter_decisions_by_topic(self._decisions(), topic="아크라이트", limit=10)
        assert [d["summary"] for d in result] == ["아크라이트과 협업 범위 확정"]

    def test_no_match_returns_empty(self):
        result = wk._filter_decisions_by_topic(self._decisions(), topic="양자컴퓨팅 연구 계획", limit=10)
        assert result == []

    def test_extra_keywords_from_memo_match(self):
        result = wk._filter_decisions_by_topic(
            self._decisions(), topic="", limit=10, extra_keywords=["아크라이트"],
        )
        assert [d["summary"] for d in result] == ["아크라이트과 협업 범위 확정"]


class TestDecisionRegistry:
    def test_accumulate_and_dedup(self, tmp_path):
        reg = tmp_path / "decision_registry.json"
        decisions = ["PoC 범위를 3개 과제로 확정"]
        assert wk.update_decision_registry_from_minutes(decisions, "주간회의", registry_path=reg) == 1
        assert wk.update_decision_registry_from_minutes(decisions, "주간회의", registry_path=reg) == 0
        data = json.loads(reg.read_text(encoding="utf-8"))
        assert data["decisions"][0]["decision_id"].startswith("DEC-")

    def test_dict_input_stores_rationale(self, tmp_path):
        """extract_decisions_from_minutes()가 반환하는 {"summary","rationale"} dict 입력."""
        reg = tmp_path / "decision_registry.json"
        decisions = [{"summary": "PoC 범위를 3개 과제로 확정", "rationale": "리소스 제약"}]
        assert wk.update_decision_registry_from_minutes(decisions, "주간회의", registry_path=reg) == 1
        data = json.loads(reg.read_text(encoding="utf-8"))
        assert data["decisions"][0]["summary"] == "PoC 범위를 3개 과제로 확정"
        assert data["decisions"][0]["rationale"] == "리소스 제약"

    def test_string_input_still_supported_with_empty_rationale(self, tmp_path):
        """ingestion_pipeline._extract_sections()["decisions"] 등 평문 문자열 입력 하위호환."""
        reg = tmp_path / "decision_registry.json"
        decisions = ["문자열 결정사항"]
        assert wk.update_decision_registry_from_minutes(decisions, "주간회의", registry_path=reg) == 1
        data = json.loads(reg.read_text(encoding="utf-8"))
        assert data["decisions"][0]["rationale"] == ""


class TestNormKey:
    def test_underscore_equals_space(self):
        # "260627_5" vs "260627 5" 가 같은 회의로 판정돼야 함 (과거 중복 버그)
        assert wk._norm_key("260627_5") == wk._norm_key("260627 5")

    def test_case_and_punctuation_ignored(self):
        assert wk._norm_key("Q-Day 대응") == wk._norm_key("q day 대응")


class TestRegistryJunkFilter:
    def test_junk_decisions_not_written(self, tmp_path):
        reg = tmp_path / "decision_registry.json"
        added = wk.update_decision_registry_from_minutes(
            ["--", "", "-", "PoC 범위 확정"], "주간회의", registry_path=reg)
        assert added == 1
        data = json.loads(reg.read_text(encoding="utf-8"))
        assert [d["summary"] for d in data["decisions"]] == ["PoC 범위 확정"]

    def test_junk_actions_not_written(self, tmp_path):
        reg = tmp_path / "action_registry.json"
        actions = json.dumps([
            {"task": "--", "assignee": "", "deadline": "", "context": ""},
            {"task": "벤치마크 자료 준비", "assignee": "", "deadline": "", "context": ""},
        ], ensure_ascii=False)
        assert wk.update_action_registry_from_actions(actions, "회의", registry_path=reg) == 1

    def test_extract_skips_separator_bullets(self):
        minutes = "## 결정사항\n- --\n- PoC 범위 확정\n"
        assert wk.extract_decisions_from_minutes(minutes) == [
            {"summary": "PoC 범위 확정", "rationale": ""}
        ]

    def test_underscore_meeting_dedups_against_space_meeting(self, tmp_path):
        reg = tmp_path / "decision_registry.json"
        assert wk.update_decision_registry_from_minutes(
            ["PoC 범위 확정"], "260627_5", registry_path=reg) == 1
        assert wk.update_decision_registry_from_minutes(
            ["PoC 범위 확정"], "260627 5", registry_path=reg) == 0


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

    def test_previous_decisions_include_rationale_from_registry(self, tmp_path):
        """decision_registry.json에 rationale이 있으면 컨텍스트 문자열에도 포함돼야 한다."""
        reg_path = tmp_path / "decision_registry.json"
        wk.update_decision_registry_from_minutes(
            [{"summary": "PoC 범위를 3개 과제로 확정", "rationale": "리소스 제약"}],
            "주간회의", registry_path=reg_path)
        # build_wiki_context_package는 data_dir 하위 decision_registry.json을 읽음
        (tmp_path / "action_registry.json").write_text(
            json.dumps({"version": "1.0", "actions": []}), encoding="utf-8")
        pkg = wk.build_wiki_context_package([], data_dir=tmp_path)
        assert any("배경: 리소스 제약" in d for d in pkg["previous_decisions"])


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

    def test_decision_proposal_includes_rationale_when_present(self):
        minutes = (
            "## 결정사항\n"
            "1. **결정 요약**: PoC 범위를 3개 과제로 확정\n"
            "   - 배경: 리소스 제약\n"
        )
        proposal = wk.build_wiki_update_proposal("주간회의", minutes, ["노트A"])
        decision_proposals = [p for p in proposal["proposals"] if p["section"] == "결정사항"]
        assert decision_proposals, "결정사항 후보가 있어야 함"
        assert "배경: 리소스 제약" in decision_proposals[0]["draft_content"]


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


def _make_multi_domain_indexer():
    """서로 다른 도메인 폴더에 걸친 노트로 path_prefixes 검색 필터를 검증."""
    ix = vi.VaultIndexer(vault_path="unused", index_path="unused.json")
    ix._built = True
    ix._notes = {
        "Archive/도메인_아카이브/01_회의_세미나/회의별/2026/양자회의.md": {
            "title": "양자회의", "wikilink_title": "양자회의",
            "snippet": "", "date": "", "type": "", "tf": {"논의": 1.0}},
        "Archive/PhysicalAI_통합아카이브/01_회의_세미나/회의별/2026/피지컬회의.md": {
            "title": "피지컬회의", "wikilink_title": "피지컬회의",
            "snippet": "", "date": "", "type": "", "tf": {"논의": 1.0}},
        "01_References/공통/공용용어.md": {
            "title": "공용용어", "wikilink_title": "공용용어",
            "snippet": "", "date": "", "type": "", "tf": {"논의": 1.0}},
    }
    ix._idf = {"논의": 1.0}
    return ix


class TestPathPrefixFiltering:
    """detect_query_domain()으로 찾은 도메인 스코프(domain_search_prefixes())를
    VaultIndexer.search()/find_related()에 넘기면 다른 도메인 노트가 제외돼야 한다."""

    def test_no_prefixes_returns_all_domains(self):
        ix = _make_multi_domain_indexer()
        titles = {r["title"] for r in ix.search("논의", limit=10)}
        assert titles == {"양자회의", "피지컬회의", "공용용어"}

    def test_quantum_prefix_excludes_other_domain(self):
        ix = _make_multi_domain_indexer()
        titles = {r["title"] for r in ix.search(
            "논의", limit=10,
            path_prefixes=["Archive/도메인_아카이브", "01_References"])}
        assert titles == {"양자회의", "공용용어"}
        assert "피지컬회의" not in titles

    def test_find_related_respects_path_prefixes(self):
        ix = _make_multi_domain_indexer()
        related = ix.find_related(
            "논의", limit=10, min_score=0.0,
            path_prefixes=["Archive/PhysicalAI_통합아카이브", "01_References"])
        assert "피지컬회의" in related
        assert "양자회의" not in related


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


class _FakeIndexer:
    is_built = True

    def __init__(self, results):
        self._results = results
        self._notes = {r["path"]: {"type": r.get("type", "")} for r in results}

    def search(self, query, limit=10, path_prefixes=None):
        return self._results

    def get_note_content(self, path):
        return "내용"

    def find_related(self, term, limit=2, path_prefixes=None):
        return []


class TestGetBriefRelatedNotesSelfReference:
    """[실전 검증 중 발견] 같은 제목으로 prep-brief를 재실행하면 직전 실행에서
    저장된 브리프 자신이 vault 검색에 걸려 "관련 노트"로 다시 포함되고, 그 안에
    자기 자신의 이전 요약이 통째로 중첩 인용되는 문제가 있었다."""

    def test_excludes_previous_self_brief(self):
        title = "퀀텀인텔리전트 정기미팅"
        results = [
            {"path": "a.md", "wikilink_title": f"{title} 준비브리프", "score": 0.5, "type": "prep-brief"},
            {"path": "b.md", "wikilink_title": "조직도_및_회사관계", "score": 0.5, "type": ""},
        ]
        indexer = _FakeIndexer(results)
        regular, _ = wk._get_brief_related_notes(title, "", indexer, None, limit=5)
        titles = [t for t, *_ in regular]
        assert f"{title} 준비브리프" not in titles
        assert "조직도_및_회사관계" in titles

    def test_excludes_note_matching_title_itself(self):
        title = "퀀텀인텔리전트 정기미팅"
        results = [{"path": "a.md", "wikilink_title": title, "score": 0.5, "type": ""}]
        indexer = _FakeIndexer(results)
        regular, _ = wk._get_brief_related_notes(title, "", indexer, None, limit=5)
        assert regular == []


class TestNoteDateResolution:
    """노트 날짜 인식: frontmatter 우선, 없으면 파일명(YYMMDD 등) 폴백."""

    def test_frontmatter_date_wins(self):
        d = vi._resolve_note_date({"date": "2026-07-07"}, "00_Meetings/260101 foo.md")
        assert d == "2026-07-07"

    def test_session_date_fallback(self):
        d = vi._resolve_note_date({"session_date": "2026-06-30"}, "x.md")
        assert d == "2026-06-30"

    def test_filename_yymmdd_fallback(self):
        # frontmatter에 날짜가 없으면 파일명 260707 → 2026-07-07
        d = vi._resolve_note_date({}, "00_Meetings/PhysicalAI/260707 로봇 세미나.md")
        assert d == "2026-07-07"

    def test_no_date_returns_empty(self):
        assert vi._resolve_note_date({}, "notes/조직도.md") == ""

    def test_wa_fname_iso(self):
        assert wa._fname_iso("2026-07-07 회의.md") == "2026-07-07"
        assert wa._fname_iso("조직도.md") == ""


class TestRecencyQueryDetection:
    def test_recency_terms_detected(self):
        for q in ["최근 회의가 언제야?", "가장 최신 세미나", "지난 회의 결정사항",
                  "요즘 논의된 주제", "언제 만났어?", "latest meeting"]:
            assert wa._is_recency_query(q), q

    def test_non_recency_not_detected(self):
        for q in ["NISQ가 뭐야?", "한빛 조직도 알려줘", "이 프로젝트 목표는?"]:
            assert not wa._is_recency_query(q), q


class TestPromptIncludesDate:
    """_build_prompt: 시스템 프롬프트에 오늘 날짜, 각 블록에 (작성일: ...) 포함."""

    def test_context_block_has_date_and_today(self):
        engine = wa.WikiQA.__new__(wa.WikiQA)
        engine._unverified = "확인 불가"
        engine._conflict = "⚠️ 충돌"
        engine._online = False
        notes = [{"title": "로봇 세미나", "heading": None,
                  "content": "본문", "date": "2026-07-07"}]
        system, user = engine._build_prompt("최근 회의?", notes)
        assert "작성일: 2026-07-07" in system
        from datetime import datetime
        assert datetime.now().strftime("%Y-%m-%d") in system


class TestQueryCenteredTruncation:
    """긴 노트에서 정답이 뒤쪽이면 앞부분만 자르지 않고 질문어 주변을 발췌한다."""

    def test_short_note_returned_whole(self):
        assert wa._truncate_note("짧은 내용", 100, terms=["내용"]) == "짧은 내용"

    def test_head_kept_when_hit_near_front(self):
        body = "핵심답변이다 " + ("x" * 500)
        out = wa._truncate_note(body, 100, terms=["핵심답변"])
        assert out.startswith("핵심답변이다")

    def test_centers_on_hit_when_answer_is_deep(self):
        body = "머리말 " + ("가" * 3000) + " 목표는연매출2배 " + ("나" * 200)
        out = wa._truncate_note(body, 400, terms=["목표는연매출2배"])
        assert "목표는연매출2배" in out       # 뒤쪽 정답이 발췌에 포함됨
        assert "중략" in out                    # head + 발췌 형태
        assert len(out) < len(body)

    def test_no_terms_falls_back_to_head(self):
        body = "a" * 5000
        out = wa._truncate_note(body, 100)
        assert out.startswith("a") and "truncated" in out
