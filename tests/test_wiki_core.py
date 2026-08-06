"""
규칙 기반 코어 회귀 테스트 — LLM/네트워크 없이 실행 가능.

실행:
    python -m pytest tests/ -q
"""

import json
import os
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


class TestTopicMatchingIsNotWhitespaceSensitive:
    """[실사용 2026-08-06] 띄어쓰기 하나로 '이전 회의 대조' 가 죽었다.

    녹음 화면 '주제' 칸에 `양자컴퓨터` 라고 붙여 쓰면, registry 본문의 `양자 컴퓨팅` 과
    한 글자도 매칭되지 않아 지난 결정·미완료 액션이 **0건**이 됐다. 재료가 0건이면 이전
    회의 대조는 조용히 아무 일도 하지 않으므로(무관한 결정을 주입하지 않으려는 설계)
    사용자는 기능이 없는 줄 안다. 실측(실제 registry 37건):
        "양자컴퓨터 도입 검토"  → 결정 0 / 액션 2   (수정 전)
        "양자컴퓨터 도입 검토"  → 결정 1 / 액션 5   (수정 후)
    """

    def test_direct_hit_ignores_whitespace(self):
        assert wk._keyword_hit("양자컴퓨팅", "양자 컴퓨팅 도입 확정") == 2

    def test_compound_keyword_matches_by_prefix_token(self):
        """붙여 쓴 복합어 — 본문 토큰이 키워드의 **접두사**면 약한 일치."""
        assert wk._keyword_hit("양자컴퓨터", "양자 컴퓨팅 도입 확정") == 1

    def test_common_tail_word_does_not_match(self):
        """`검토`·`계획` 같은 흔한 뒤쪽 낱말로는 걸리지 않는다 — 포함이 아니라 접두사만
        허용하는 이유다. 열어 두면 registry 전체가 후보가 된다."""
        assert wk._keyword_hit("예산검토", "기술 검토 일정 조정") == 0

    def test_unrelated_text_scores_zero(self):
        assert wk._keyword_hit("양자컴퓨터", "기념품 예산 최종 승인") == 0

    def test_short_keyword_has_no_weak_match(self):
        """2~3자 키워드는 이미 부분문자열로 충분히 걸린다 — 접두사까지 열면 오탐."""
        assert wk._keyword_hit("양자", "양각 도장 제작") == 0

    def test_registry_lookup_finds_spaced_summary(self):
        decisions = [{"summary": "양자 컴퓨팅 파일럿 범위 확정",
                      "created_at": "2026-07-02", "topics": []}]
        got = wk._filter_decisions_by_topic(decisions, topic="양자컴퓨터 도입", limit=5)
        assert len(got) == 1, "붙여 쓴 주제가 띄어 쓴 본문을 못 찾았다"

    def test_weak_match_scores_below_direct_match(self):
        """약한 일치가 직접 일치를 앞지르면, 여러 프로젝트가 섞인 registry 에서 무관한
        항목이 상위로 올라온다."""
        decisions = [
            {"summary": "양자 컴퓨팅 로드맵 확정", "created_at": "2026-07-01", "topics": []},
            {"summary": "양자컴퓨터 예산 확정", "created_at": "2026-07-02", "topics": []},
        ]
        got = wk._filter_decisions_by_topic(decisions, topic="양자컴퓨터", limit=5)
        assert got[0]["summary"] == "양자컴퓨터 예산 확정"


class TestTitleTagsDropMeaninglessFragments:
    """자동 생성 제목의 조각이 topics 태그가 되면 주제 필터가 무력화된다.

    실측: 실제 registry 37건의 태그가 `실시간·2026년·07월·31일·09:15` 뿐이었다 —
    주제에 그 낱말이 하나라도 들어가면 **전 항목이 매칭**됐다. 이 앱이 만드는 제목이
    `실시간 녹음 260806-1048` 형태라 구조적으로 그렇게 된다.
    """

    def test_auto_generated_titles_yield_no_tags(self):
        for title in ("실시간 2026년 07월 31일 09:15", "새로운 녹음 4",
                      "session_20260715_091717", "실시간 녹음 260806-1048"):
            assert wk._extract_topic_keywords_from_title(title) == [], title

    def test_meaningful_title_keeps_tags(self):
        assert wk._extract_topic_keywords_from_title("양자 컴퓨팅 예산 검토") == [
            "양자", "컴퓨팅", "예산", "검토"]

    def test_date_prefix_dropped_but_subject_kept(self):
        assert wk._extract_topic_keywords_from_title("260806 nvidia") == ["nvidia"]

    def test_stored_garbage_tags_are_ignored_when_reading(self):
        """**이미 저장된** 항목에도 즉시 들어야 한다 — 태그는 사용자 데이터라 고칠 수 없다."""
        assert wk.useful_topic_tags(["실시간", "2026년", "07월", "31일", "09:15"]) == []
        assert wk.useful_topic_tags(["양자", "컴퓨팅"]) == ["양자", "컴퓨팅"]

    def test_garbage_tag_does_not_pull_unrelated_decision(self):
        """주제 '실시간' 이 (제목 조각으로 태깅된) 무관한 결정을 끌어오지 않는다."""
        decisions = [{"summary": "기념품 예산 최종 승인", "created_at": "2026-07-01",
                      "topics": ["실시간", "2026년", "09:15"]}]
        assert wk._filter_decisions_by_topic(decisions, topic="실시간 회의", limit=5) == []


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
                # 후보가 _SEMANTIC_Z_MIN_SAMPLES 미만이라 z 컷은 자동으로 비활성이다
                # (표본이 적을 때 억지로 통계를 쓰지 않는다) — 이 테스트는 융합 회수만 본다.
                "wiki_knowledge.embedding_min_z": 1.5,
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
        assert sem["score"] == 0.0 and sem["cosine"] > 0.0
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


class TestPathMatcher:
    """`path_matcher()` — 폴더 필터 규칙 정본.

    실시간 논문 arm 과 배지 판정이 각각 다른 규칙을 손으로 구현해 갈라졌던 버그가
    있었다(하위 폴더에 있는 논문 74+9노트가 arm 에서 영구히 0건). 규칙을 여기 한
    곳으로 모았으므로 두 모드의 의미를 고정한다.
    """

    QC = "Archive/도메인_아카이브/02_이론_학습/01_기초.md"

    def test_empty_means_no_filter(self):
        assert vi.path_matcher(None) is None
        assert vi.path_matcher([]) is None
        assert vi.path_matcher(["", "  ", "/"]) is None

    def test_prefix_mode_is_root_anchored(self):
        m = vi.path_matcher(["02_이론_학습"])          # 기본 = prefix
        assert m("02_이론_학습/x.md")
        assert not m(self.QC)                          # 하위에 묻힌 폴더는 불일치

    def test_segment_mode_matches_mid_path(self):
        m = vi.path_matcher(["02_이론_학습"], "segment")
        assert m("02_이론_학습/x.md")
        assert m(self.QC)
        assert not m("00_Meetings/주간회의.md")

    def test_segment_mode_requires_whole_folder_name(self):
        """부분 문자열이 아니라 폴더 세그먼트 단위로 맞아야 한다."""
        m = vi.path_matcher(["원문추출"], "segment")
        assert m("Archive/QC/원문추출/논문.md")
        assert not m("Archive/QC/원문추출본_백업/논문.md")
        assert not m("Archive/원문추출.md")            # 파일명은 폴더가 아니다

    def test_trailing_slash_and_backslash_normalized(self):
        m = vi.path_matcher(["01_References/"], "segment")
        assert m("01_References\\Companies\\Acme.md")

    def test_search_honors_segment_mode(self):
        """search(path_match="segment") 로 하위 폴더 노트가 회수된다."""
        ix = _make_multi_domain_indexer()
        prefix_only = {r["title"] for r in ix.search(
            "논의", limit=10, path_prefixes=["01_회의_세미나"])}
        assert prefix_only == set()                    # 루트에 없으므로 0건
        segment = {r["title"] for r in ix.search(
            "논의", limit=10, path_prefixes=["01_회의_세미나"], path_match="segment")}
        assert segment == {"양자회의", "피지컬회의"}


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

    def test_normalize_iso_date_formats(self):
        # 실기기 인덱스에서 발견: frontmatter가 한글 날짜로 저장돼 정렬이 깨졌다.
        assert du.normalize_iso_date("2026-07-08") == "2026-07-08"
        assert du.normalize_iso_date("2026년 06월 29일 14:10") == "2026-06-29"
        assert du.normalize_iso_date("2026/6/29") == "2026-06-29"
        assert du.normalize_iso_date("2026.07.08") == "2026-07-08"
        assert du.normalize_iso_date("20260708") == "2026-07-08"
        assert du.normalize_iso_date("2026-07-08T14:00:00") == "2026-07-08"
        assert du.normalize_iso_date("2026-13-40") == ""   # 유효하지 않은 날짜
        assert du.normalize_iso_date("") == ""


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


def _make_dated_indexer():
    """날짜/type이 있는 노트로 recent_notes(최근성 회수)를 검증하는 수동 인덱서."""
    ix = vi.VaultIndexer(vault_path="unused", index_path="unused.json")
    ix._built = True
    ix._notes = {
        "m1.md": {"title": "옛 회의", "wikilink_title": "옛 회의", "snippet": "s",
                  "date": "2025-09-16", "type": "meeting", "tf": {}},
        "m2.md": {"title": "최신 회의", "wikilink_title": "최신 회의", "snippet": "s",
                  "date": "2026-07-24", "type": "meeting", "tf": {}},
        "m3.md": {"title": "중간 세미나", "wikilink_title": "중간 세미나", "snippet": "s",
                  "date": "2026/06/25", "type": "seminar", "tf": {}},  # 슬래시 형식
        "ref.md": {"title": "참고자료", "wikilink_title": "참고자료", "snippet": "s",
                   "date": "", "type": "reference", "tf": {}},  # 날짜 없음 → 제외
    }
    ix._idf = {}
    return ix


class TestRecentNotes:
    """[실전 버그] '가장 최근 회의'가 키워드 관련도 풀에 안 들어와 최신 회의를 놓치던 문제.
    recent_notes 는 관련도와 무관하게 날짜 내림차순으로 최신 노트를 직접 회수한다."""

    def test_orders_by_date_desc_and_excludes_undated(self):
        ix = _make_dated_indexer()
        titles = [r["title"] for r in ix.recent_notes(limit=10)]
        assert titles == ["최신 회의", "중간 세미나", "옛 회의"]  # 날짜 없는 참고자료 제외

    def test_type_filter_meeting_family(self):
        ix = _make_dated_indexer()
        got = ix.recent_notes(limit=10, types=("meeting", "seminar", "lecture"))
        assert [r["title"] for r in got] == ["최신 회의", "중간 세미나", "옛 회의"]
        only_meeting = ix.recent_notes(limit=10, types=("meeting",))
        assert [r["title"] for r in only_meeting] == ["최신 회의", "옛 회의"]

    def test_limit_keeps_newest(self):
        ix = _make_dated_indexer()
        assert [r["title"] for r in ix.recent_notes(limit=1)] == ["최신 회의"]

    def test_mixed_date_formats_sort_chronologically(self):
        # 2026/06/25(슬래시)가 2025-09-16(대시)보다 최신으로 정렬돼야 한다.
        ix = _make_dated_indexer()
        titles = [r["title"] for r in ix.recent_notes(limit=10)]
        assert titles.index("중간 세미나") < titles.index("옛 회의")


class TestIndexableNoteFilter:
    """[실전 버그] 텍스트추출 그림자 사본(requirements.txt.md 등)이 회의로 오인용되던 문제."""

    def test_excludes_shadow_copies(self):
        assert not vi._is_indexable_note("a/requirements.txt.md")
        assert not vi._is_indexable_note("b/NEXT_STEPS.md.md")
        assert not vi._is_indexable_note("c/data.json.md")
        assert not vi._is_indexable_note("d/slides.pptx.md")

    def test_excludes_hwp_msg_sh_shadow_copies(self):
        """[실전 버그] 이 4개가 _SHADOW_EXTS에 없어 24건이 인덱스에 들어와 있었다
        (2026-07-30 실측, 그중 6건이 실명 참가신청서·개인정보동의서)."""
        assert not vi._is_indexable_note("Inbox/[첨부1] 참가신청서(홍길동).hwp.md")
        assert not vi._is_indexable_note("Inbox/[별첨1] 프로그램 신청서.hwpx.md")
        assert not vi._is_indexable_note("메일/Re 임원 참여 계획 회신.msg.md")
        assert not vi._is_indexable_note("code/setup_tmux_vscode.sh.md")

    def test_keeps_real_notes(self):
        assert vi._is_indexable_note("회의별/260625 메가존 해커톤 회의.md")
        assert vi._is_indexable_note("notes/2026-07-08.md")
        assert vi._is_indexable_note("프로젝트 계획.md")

    def test_exclude_dirs_substring(self):
        ex = ["99_원본파일", "바이너리"]
        assert not vi._is_indexable_note("Archive/QC/99_원본파일(바이너리)/x.md", ex)
        assert vi._is_indexable_note("Meetings/x.md", ex)

    def test_iter_note_files_is_the_shared_gate(self, tmp_path, monkeypatch):
        """파일 목록 자체를 한 함수에서 받는다 — 규칙을 복제하면 다시 갈라진다.

        인덱서·graph_sync 백필·그림자 노드 마이그레이션이 모두 이 함수를 쓴다.
        exclude_dirs 기본값도 여기(default_exclude_dirs) 하나뿐이어야 한다."""
        vault = tmp_path / "v"
        (vault / "00_Meetings").mkdir(parents=True)
        (vault / "99_원본파일").mkdir()
        for rel in ("00_Meetings/진짜회의.md", "00_Meetings/requirements.txt.md",
                    "00_Meetings/_템플릿.md", "99_원본파일/원본.md"):
            (vault / rel).write_text("x", encoding="utf-8")
        (vault / "루트노트.md").write_text("x", encoding="utf-8")

        monkeypatch.setattr(vi, "_c", lambda key, default=None: default)
        assert vi.default_exclude_dirs() == vi._DEFAULT_EXCLUDE_DIRS
        got = {os.path.relpath(f, vault).replace("\\", "/")
               for f in vi.iter_note_files(str(vault))}
        assert got == {"00_Meetings/진짜회의.md", "루트노트.md"}

        # 설정을 바꾸면 한 곳만 바꿔도 목록이 따라온다(복제돼 있으면 여기서 깨진다)
        monkeypatch.setattr(vi, "_c",
                            lambda key, default=None: (["00_Meetings"] if key ==
                                                       "indexing.exclude_dirs" else default))
        got2 = {os.path.relpath(f, vault).replace("\\", "/")
                for f in vi.iter_note_files(str(vault))}
        assert got2 == {"루트노트.md", "99_원본파일/원본.md"}

    def test_iter_note_files_handles_missing_vault(self):
        assert vi.iter_note_files("") == []


class TestReferenceFilesIndexing:
    """[PRD_Natively 트랙 B] 회의 자료(PDF/PPTX/DOCX) 추출본을 통제된 경로로만 편입.

    그림자 사본 규칙 자체는 정당하다 — 이진 원본의 텍스트덤프가 검색 인덱스를
    오염시키던 장치다. 그래서 규칙을 없애지 않고 **경로 ∩ 문서형 확장자** 교집합으로
    예외를 연다. 경로만으로 열면 실볼트 기준 비문서 그림자 사본 170건이 함께 들어와
    인덱스가 474 → 약 780 이 된다(과거 '인덱서 473 vs 그래프 805' 사고의 재현).
    """

    REF = ["원문추출"]

    def test_off_by_default(self):
        """옵트인 — 켜기 전에는 동작이 전혀 바뀌지 않는다."""
        assert not vi._is_indexable_note("A/원문추출/발표자료.pdf.md", (), [])
        assert not vi.is_reference_note("A/원문추출/발표자료.pdf.md", [])

    def test_includes_document_extensions(self):
        for ext in ("pdf", "pptx", "docx", "xlsx", "hwp", "hwpx"):
            rel = f"A/원문추출/자료.{ext}.md"
            assert vi._is_indexable_note(rel, (), self.REF), ext

    def test_excludes_code_and_data_in_the_same_folder(self):
        """[핵심] 확장자 교집합이 없으면 여기서 인덱스가 부푼다."""
        for name in ("main.py.md", "run.sh.md", "notes.md.md", "config.yaml.md",
                     "data.json.md", "requirements.txt.md", "nb.ipynb.md"):
            rel = f"A/원문추출/{name}"
            assert not vi._is_indexable_note(rel, (), self.REF), name

    def test_segment_match_is_exact_not_substring(self):
        """`원문추출_보완`은 스캔본 빈 껍데기와 실명 개인정보 문서가 모인 곳이다."""
        assert not vi._is_indexable_note("A/원문추출_보완/신청서.pdf.md", (), self.REF)
        assert not vi._is_indexable_note("A/추출/x.pdf.md", (), self.REF)
        assert vi._is_indexable_note("A/B/원문추출/C/x.pdf.md", (), self.REF)

    def test_exclude_dirs_win_over_reference_dirs(self):
        """바이너리 원본 아카이브 안의 추출본까지 끌어오지 않는다."""
        assert not vi._is_indexable_note(
            "99_원본파일/원문추출/x.pdf.md", ["99_원본파일"], self.REF)

    def test_outside_reference_dirs_still_excluded(self):
        assert not vi._is_indexable_note("B/발표자료.pdf.md", (), self.REF)

    def test_underscore_prefix_still_excluded(self, tmp_path, monkeypatch):
        vault = tmp_path / "v"
        (vault / "원문추출").mkdir(parents=True)
        (vault / "원문추출" / "_index.pdf.md").write_text("x", encoding="utf-8")
        monkeypatch.setattr(vi, "_c", lambda k, d=None:
                            self.REF if k == "indexing.reference_dirs" else d)
        assert vi.iter_note_files(str(vault)) == []

    def test_iter_note_files_diff_is_exactly_the_intended_files(self, tmp_path, monkeypatch):
        """[PRD §3.3] 편입 전/후 반환 집합 diff 가 의도한 파일만 늘었는지."""
        vault = tmp_path / "v"
        (vault / "회의" / "원문추출").mkdir(parents=True)
        (vault / "회의" / "원문추출_보완").mkdir(parents=True)
        files = {
            "회의/진짜회의.md": True,
            "회의/원문추출/발표자료.pdf.md": True,       # 편입 대상
            "회의/원문추출/슬라이드.pptx.md": True,      # 편입 대상
            "회의/원문추출/script.py.md": False,
            "회의/원문추출_보완/신청서.pdf.md": False,
            "회의/바깥자료.pdf.md": False,
        }
        for rel in files:
            (vault / rel).write_text("x", encoding="utf-8")

        def cfg(ref):
            return lambda k, d=None: (ref if k == "indexing.reference_dirs" else d)

        monkeypatch.setattr(vi, "_c", cfg([]))
        before = {os.path.relpath(f, vault).replace("\\", "/")
                  for f in vi.iter_note_files(str(vault))}
        monkeypatch.setattr(vi, "_c", cfg(self.REF))
        after = {os.path.relpath(f, vault).replace("\\", "/")
                 for f in vi.iter_note_files(str(vault))}

        assert before == {"회의/진짜회의.md"}
        assert after - before == {"회의/원문추출/발표자료.pdf.md",
                                  "회의/원문추출/슬라이드.pptx.md"}
        assert before - after == set()

    def test_graph_sync_sees_the_same_set(self, tmp_path, monkeypatch):
        """판정이 갈라지면 위키엔 있는데 그래프엔 없는 노트가 생긴다(과거 사고의 거울상)."""
        from meeting_minutes_app.common import config_loader
        from meeting_minutes_app.wiki_core import graph_sync as gs

        vault = tmp_path / "v"
        (vault / "원문추출").mkdir(parents=True)
        (vault / "원문추출" / "자료.pdf.md").write_text("x", encoding="utf-8")
        (vault / "원문추출" / "code.py.md").write_text("x", encoding="utf-8")

        def cfg(k, d=None):
            if k == "indexing.reference_dirs":
                return self.REF
            if k in ("indexing.vault_path", "obsidian.vault_path"):
                return str(vault)
            return d

        monkeypatch.setattr(vi, "_c", cfg)
        monkeypatch.setattr(config_loader, "get", cfg)
        idx = {os.path.relpath(f, vault).replace("\\", "/")
               for f in vi.iter_note_files(str(vault))}
        graph = {os.path.relpath(f, v).replace("\\", "/")
                 for f, v, _ in gs._iter_vault_notes()}
        assert idx == graph == {"원문추출/자료.pdf.md"}

    def test_reference_notes_are_not_recent_meetings(self, tmp_path, monkeypatch):
        """[실전 버그 방지] `01_회의_세미나`가 meeting_dirs 의 "회의"에 substring 으로
        걸려 발표자료가 '가장 최근 회의'로 승격되던 자리(실측 30건 해당)."""
        vault = tmp_path / "v"
        (vault / "01_회의_세미나" / "원문추출").mkdir(parents=True)
        (vault / "01_회의_세미나" / "원문추출" / "250807_발표자료.pdf.md").write_text(
            '---\ndate: "2026-07-30"\n---\n발표 내용', encoding="utf-8")
        (vault / "01_회의_세미나" / "260101 진짜 회의.md").write_text(
            '---\ndate: "2026-01-01"\ntype: "meeting"\n---\n회의 내용', encoding="utf-8")

        def cfg(k, d=None):
            if k == "indexing.reference_dirs":
                return self.REF
            if k == "wiki_knowledge.embedding_enabled":
                return False
            return d

        monkeypatch.setattr(vi, "_c", cfg)
        ix = vi.VaultIndexer(str(vault), str(tmp_path / "idx.json"))
        assert ix.build() == 2                      # 둘 다 인덱싱된다(검색은 가능)
        titles = [n["path"] for n in
                  ix.recent_notes(limit=10, types=("meeting", "seminar", "lecture"))]
        assert titles == ["01_회의_세미나/260101 진짜 회의.md"]


class TestIndexSaveReplaceRetry:
    """[실전 버그] reindex 가 WinError 32(웹 서버가 인덱스 점유)로 조용히 실패해
    낡은 인덱스가 그대로 남고, 사용자는 '재빌드했다'고 믿는 문제."""

    def test_retries_on_winerror_32(self, tmp_path, monkeypatch):
        src, dst = tmp_path / "a.tmp", tmp_path / "b.json"
        src.write_text("new", encoding="utf-8")
        dst.write_text("old", encoding="utf-8")
        calls = {"n": 0}
        real = vi.os.replace

        def flaky(s, d):
            calls["n"] += 1
            if calls["n"] < 3:
                err = OSError("locked")
                err.winerror = 32
                raise err
            return real(s, d)
        monkeypatch.setattr(vi.os, "replace", flaky)
        monkeypatch.setattr(vi.time, "sleep", lambda *_: None)
        vi._replace_with_retry(str(src), str(dst))
        assert dst.read_text(encoding="utf-8") == "new"
        assert calls["n"] == 3

    def test_gives_up_and_raises_after_attempts(self, tmp_path, monkeypatch):
        src, dst = tmp_path / "a.tmp", tmp_path / "b.json"
        src.write_text("new", encoding="utf-8")
        dst.write_text("old", encoding="utf-8")

        def always_locked(s, d):
            err = OSError("locked")
            err.winerror = 32
            raise err
        monkeypatch.setattr(vi.os, "replace", always_locked)
        monkeypatch.setattr(vi.time, "sleep", lambda *_: None)
        with pytest.raises(OSError):
            vi._replace_with_retry(str(src), str(dst), attempts=3)
        assert dst.read_text(encoding="utf-8") == "old"   # 기존 인덱스 보존

    def test_other_oserror_not_retried(self, tmp_path, monkeypatch):
        calls = {"n": 0}

        def boom(s, d):
            calls["n"] += 1
            raise OSError("disk full")
        monkeypatch.setattr(vi.os, "replace", boom)
        with pytest.raises(OSError):
            vi._replace_with_retry("x", "y")
        assert calls["n"] == 1     # 재시도하지 않는다

    def test_save_failure_keeps_old_index_and_cleans_tmp(self, tmp_path, monkeypatch, capsys):
        ix = vi.VaultIndexer(vault_path=str(tmp_path),
                             index_path=str(tmp_path / "idx.json"))
        ix._notes = {"a.md": {"title": "A", "tf": {}}}
        ix._idf = {}
        (tmp_path / "idx.json").write_text('{"notes":{"old.md":{}}}', encoding="utf-8")

        def always_locked(s, d):
            err = OSError("locked")
            err.winerror = 32
            raise err
        monkeypatch.setattr(vi.os, "replace", always_locked)
        monkeypatch.setattr(vi.time, "sleep", lambda *_: None)
        ix._save()
        out = capsys.readouterr().out
        assert "낡은" in out and "사용 중" in out          # 원인·대처를 알린다
        assert "old.md" in (tmp_path / "idx.json").read_text(encoding="utf-8")
        assert not list(tmp_path.glob("*.tmp"))            # tmp 정리됨


class TestSectionsInNotes:
    """후보 노트 안에서만 근거 섹션을 특정한다 (실시간 경로용 — 전체 섹션 스캔 회피).

    전체 볼트 섹션 검색(search_sections)은 노트 수에 선형이라 실시간 경로에서 비싸다
    (실측 802노트·12,140섹션에서 ~256ms). 이 메서드는 후보만 보므로 규모와 무관하다.
    """

    def _ix(self):
        ix = vi.VaultIndexer(vault_path="unused", index_path="unused.json")
        ix._built = True
        ix._idf = {"큐비트": 2.0, "로드맵": 1.5, "예산": 3.0}
        ix._notes = {
            "a.md": {
                "title": "A", "wikilink_title": "A", "snippet": "sa", "date": "",
                "type": "", "tf": {},
                "sections": [
                    {"heading": "개요", "level": 2, "snippet": "개요 본문",
                     "tf": {"큐비트": 0.1}},
                    {"heading": "큐비트 로드맵", "level": 2, "snippet": "로드맵 본문",
                     "tf": {"큐비트": 0.5, "로드맵": 0.4}},
                ]},
            "b.md": {
                "title": "B", "wikilink_title": "B", "snippet": "sb", "date": "",
                "type": "", "tf": {},
                "sections": [
                    {"heading": "예산", "level": 2, "snippet": "예산 본문",
                     "tf": {"예산": 0.3}},
                ]},
            "c.md": {   # 섹션 인덱스 없이 빌드된 노트
                "title": "C", "wikilink_title": "C", "snippet": "sc", "date": "",
                "type": "", "tf": {}},
        }
        return ix

    def test_picks_best_matching_section(self):
        got = self._ix().sections_in_notes("큐비트 로드맵 논의", ["a.md"])
        assert got["a.md"]["heading"] == "큐비트 로드맵"
        assert got["a.md"]["snippet"] == "로드맵 본문"
        assert got["a.md"]["score"] > 0

    def test_only_requested_notes_scanned(self):
        got = self._ix().sections_in_notes("큐비트 예산", ["b.md"])
        assert list(got) == ["b.md"]       # a.md 는 후보가 아니므로 결과 없음

    def test_unmatched_note_omitted(self):
        got = self._ix().sections_in_notes("전혀 다른 주제", ["a.md", "b.md"])
        assert got == {}

    def test_note_without_sections_omitted(self):
        got = self._ix().sections_in_notes("큐비트", ["c.md"])
        assert got == {}

    def test_empty_query_or_paths(self):
        ix = self._ix()
        assert ix.sections_in_notes("", ["a.md"]) == {}
        assert ix.sections_in_notes("큐비트", []) == {}
        assert ix.sections_in_notes("큐비트", ["없는노트.md"]) == {}


class TestResolveNoteDate:
    """[실전 버그] frontmatter 없는 노트가 조부모 폴더명 날짜를 훔쳐 오래된 오답 날짜를 갖던 문제."""

    def test_no_grandparent_date_leak(self):
        # 251117(조부모)에서 날짜를 끌어오면 안 됨 — 파일명·직속부모엔 날짜 없음
        assert vi._resolve_note_date({}, "251117_양자회의/child/NEXT_STEPS.md") == ""

    def test_immediate_parent_date_kept(self):
        # 직속 부모 폴더의 날짜는 정상 노트를 위해 보존
        assert vi._resolve_note_date({}, "260625 회의/transcript.md") == "2026-06-25"

    def test_filename_date_priority(self):
        assert vi._resolve_note_date({}, "회의별/260625 메가존.md") == "2026-06-25"

    def test_frontmatter_date_wins(self):
        assert vi._resolve_note_date({"date": "2026-07-08"}, "260625 폴더/x.md") == "2026-07-08"


class TestRecentNotesFolderRescue:
    """[실전 버그] frontmatter type이 비어 있는 실제 회의가 recent_notes 유형필터에서 탈락하던 문제."""

    def _ix(self):
        ix = vi.VaultIndexer(vault_path="unused", index_path="unused.json")
        ix._built = True
        ix._notes = {
            "00_Meetings/팀회의/260701 팀회의.md": {
                "title": "260701 팀회의", "wikilink_title": "260701 팀회의",
                "snippet": "s", "date": "2026-07-01", "type": "", "tf": {}},
            "01_References/some_ref.md": {
                "title": "참고", "wikilink_title": "참고", "snippet": "s",
                "date": "2026-07-05", "type": "reference", "tf": {}},
        }
        ix._idf = {}
        return ix

    def test_rescues_empty_type_meeting_in_folder(self):
        got = self._ix().recent_notes(limit=10, types=("meeting", "seminar", "lecture"))
        titles = [r["title"] for r in got]
        assert "260701 팀회의" in titles   # type='' 이지만 회의폴더 → 구제
        assert "참고" not in titles          # type=reference → 폴더 무관하게 제외


class TestIsMeetingNote:
    """시점질의 최종 회의 필터(_is_meeting_note) — 정크가 작성일만으로 상위 오르는 것 차단."""

    def test_type_meeting(self):
        assert wa._is_meeting_note({"type": "meeting", "path": "x.md"})
        assert wa._is_meeting_note({"type": "seminar", "path": "x.md"})

    def test_meeting_folder(self):
        assert wa._is_meeting_note({"type": "", "path": "00_Meetings/x.md"}, ["00_Meetings"])

    def test_title_keyword(self):
        assert wa._is_meeting_note(
            {"type": "", "path": "a/260701 팀회의.md", "title": "260701 팀회의"})

    def test_rejects_non_meeting(self):
        assert not wa._is_meeting_note(
            {"type": "reference", "path": "01_Ref/requirements.txt.md",
             "title": "requirements.txt"}, ["00_Meetings"])


class TestRecencyDateKey:
    def test_normalizes_separators_and_trims_time(self):
        assert wa._recency_date_key({"date": "2026/07/24"}) == "2026-07-24"
        assert wa._recency_date_key({"date": "2026-07-24T15:00:00"}) == "2026-07-24"
        assert wa._recency_date_key({"date": ""}) == ""
        assert wa._recency_date_key({}) == ""

    def test_korean_date_normalized(self):
        # 한글 날짜가 '가장 최근'으로 잘못 정렬되던 실기기 버그 회귀 방지.
        assert wa._recency_date_key({"date": "2026년 06월 29일 14:10"}) == "2026-06-29"
        # 한글 6월이 ISO 7월보다 앞서야(작음) 한다 — 사전식 정렬이 시간순과 일치.
        assert (wa._recency_date_key({"date": "2026년 06월 29일"})
                < wa._recency_date_key({"date": "2026-07-08"}))


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


class _FakeLLM:
    """chat()만 흉내내는 LLM 스텁."""
    def __init__(self, reply):
        self._reply = reply
        self.calls = []

    def chat(self, system, user, temp=0.1):
        self.calls.append((system, user))
        return self._reply


class TestQueryPlanner:
    """질문 → 검색 계획(LLM 쿼리 플래너). 실패·비활성 시 휴리스틱 폴백."""

    def _engine(self, llm):
        qa = wa.WikiQA.__new__(wa.WikiQA)
        qa._llm = llm
        return qa

    def test_fallback_when_disabled_does_not_call_llm(self, monkeypatch):
        monkeypatch.setattr(wa, "_c",
                            lambda k, d=None: False if k == "wiki.query_planner_enabled" else d)
        llm = _FakeLLM("{}")
        plan = self._engine(llm)._plan_query("가장 최근 회의 3개")
        assert plan["intent"] == "recency"
        assert "meeting" in plan["types"]
        assert llm.calls == []  # 비활성 시 LLM 호출 없음

    def test_parses_llm_plan(self, monkeypatch):
        monkeypatch.setattr(wa, "_c",
                            lambda k, d=None: True if k == "wiki.query_planner_enabled" else d)
        llm = _FakeLLM('{"intent":"aggregate","date_from":"2026-06-01",'
                       '"date_to":"2026-06-30","types":["meeting"],"top_k":3,'
                       '"entities":["레이더"]}')
        plan = self._engine(llm)._plan_query("6월 회의 3개")
        assert plan["intent"] == "aggregate"
        assert plan["date_from"] == "2026-06-01" and plan["date_to"] == "2026-06-30"
        assert plan["types"] == ["meeting"] and plan["top_k"] == 3
        assert plan["entities"] == ["레이더"]

    def test_invalid_date_string_dropped(self, monkeypatch):
        monkeypatch.setattr(wa, "_c",
                            lambda k, d=None: True if k == "wiki.query_planner_enabled" else d)
        llm = _FakeLLM('{"intent":"lookup","date_from":"6월","date_to":"",'
                       '"types":[],"top_k":0,"entities":["NISQ"]}')
        plan = self._engine(llm)._plan_query("NISQ 정의")
        assert plan["date_from"] == "" and plan["intent"] == "lookup"

    def test_llm_failure_falls_back(self, monkeypatch):
        monkeypatch.setattr(wa, "_c",
                            lambda k, d=None: True if k == "wiki.query_planner_enabled" else d)

        class _Boom:
            def chat(self, *a, **k):
                raise RuntimeError("x")
        plan = self._engine(_Boom())._plan_query("최근 세미나")
        assert plan["intent"] == "recency"


class TestContextRankScore:
    """병합 랭킹이 하이브리드(의미) 관련도를 실제로 반영하는지."""

    def test_relevance_adds_weight(self):
        base = wa._context_rank_score(title="t", path="p", snippet="s", terms=[],
                                      source="index", order=0, relevance=0.0)
        hi = wa._context_rank_score(title="t", path="p", snippet="s", terms=[],
                                    source="index", order=0, relevance=1.0)
        assert hi - base == pytest.approx(30.0)

    def test_semantic_index_beats_bare_obsidian(self):
        # 제목엔 질문어가 없지만 의미 관련도 높은 인덱스 노트가, 관련도 0인 Obsidian 노트를 이긴다.
        idx = wa._context_rank_score(title="무관", path="", snippet="", terms=["레이더"],
                                     source="index", order=0, relevance=1.0)
        obs = wa._context_rank_score(title="무관", path="", snippet="", terms=["레이더"],
                                     source="obsidian", order=0, relevance=0.0)
        assert idx > obs


class TestCitationToggle:
    """citation_required 토글이 프롬프트 규칙2를 실제로 분기하는지(과거엔 죽은 replace)."""

    def _engine(self):
        e = wa.WikiQA.__new__(wa.WikiQA)
        e._unverified = "확인 불가"
        e._conflict = "⚠️ 충돌"
        e._online = False
        return e

    def _note(self):
        return [{"title": "n", "heading": None, "content": "본문", "date": ""}]

    def test_required_true_inserts_firm_rule(self, monkeypatch):
        monkeypatch.setattr(wa, "_c",
                            lambda k, d=None: True if k == "wiki.citation_required" else d)
        system, _ = self._engine()._build_prompt("q", self._note())
        assert "근거 노트를 인용" in system and "필수 아님" not in system

    def test_required_false_makes_optional(self, monkeypatch):
        monkeypatch.setattr(wa, "_c",
                            lambda k, d=None: False if k == "wiki.citation_required" else d)
        system, _ = self._engine()._build_prompt("q", self._note())
        assert "필수 아님" in system


class _GatherFakeIndexer:
    """_gather_context 통합 테스트용 덕타이핑 인덱서(섹션 없음, TF-IDF 매치 없음)."""
    def __init__(self, notes):
        self._data = {n["path"]: n for n in notes}

    def find_related_sections(self, q, limit=10, path_prefixes=None):
        return []

    def search(self, q, limit=10, path_prefixes=None):
        # 일반어 질의라 관련도 매치가 없다고 가정(빈 결과) — recency 주입만이 최신 노트를 넣는다.
        return []

    def get_note_content(self, path):
        return self._data.get(path, {}).get("content", "내용")

    def recent_notes(self, limit=10, types=None):
        tset = {t.lower() for t in types} if types else None
        rows = [n for n in self._data.values()
                if n.get("date") and (tset is None or n.get("type", "").lower() in tset)]
        rows.sort(key=lambda x: x["date"], reverse=True)
        return [{"title": n["title"], "path": n["path"], "wikilink_title": n["title"],
                 "snippet": "", "score": 0.0, "date": n["date"], "type": n.get("type", "")}
                for n in rows[:limit]]


class TestGatherContextTemporal:
    """플래너 의도로 최신노트 주입·기간 필터가 컨텍스트에 반영되는지."""

    def _engine(self, notes):
        qa = wa.WikiQA.__new__(wa.WikiQA)
        qa._llm = None
        qa._obs = None
        qa._indexer = _GatherFakeIndexer(notes)
        qa._max_chars = 2000
        qa._max_notes = 10
        return qa

    _NOTES = [
        {"path": "m1.md", "title": "옛 회의", "date": "2025-09-16", "type": "meeting", "content": "옛"},
        {"path": "m2.md", "title": "최신 회의", "date": "2026-07-24", "type": "meeting", "content": "신"},
        {"path": "s1.md", "title": "6월 세미나", "date": "2026-06-25", "type": "seminar", "content": "세"},
        {"path": "ref.md", "title": "참고자료", "date": "", "type": "reference", "content": "무관노이즈"},
    ]

    def test_recency_injects_newest_meeting_first(self, monkeypatch):
        monkeypatch.setattr(wa, "_c", lambda k, d=None: d)  # 모든 config 기본값
        eng = self._engine(self._NOTES)
        plan = {"intent": "recency", "date_from": "", "date_to": "",
                "types": ["meeting"], "top_k": 0, "entities": ["회의"]}
        out = eng._gather_context("가장 최근 회의", 10, plan)
        titles = [n["title"] for n in out]
        assert titles and titles[0] == "최신 회의"
        assert "참고자료" not in titles  # 날짜 없는 노이즈 제외

    def test_date_range_filters_to_june(self, monkeypatch):
        monkeypatch.setattr(wa, "_c", lambda k, d=None: d)
        eng = self._engine(self._NOTES)
        plan = {"intent": "aggregate", "date_from": "2026-06-01", "date_to": "2026-06-30",
                "types": [], "top_k": 0, "entities": ["세미나"]}
        out = eng._gather_context("6월에 뭐 했지", 10, plan)
        titles = [n["title"] for n in out]
        assert titles == ["6월 세미나"]  # 기간 밖 회의·무날짜 노트 모두 제외


class TestVerifyCitations:
    """인용 근거 검증(P3-6): [출처: [[X]]]가 컨텍스트에 없으면 환각 인용으로 잡는다."""

    _CTX = [{"title": "최신 회의"}, {"title": "양자 세미나"}]

    def test_valid_citation_not_flagged(self):
        ans = "결론입니다 [출처: [[최신 회의]]]."
        assert wa._verify_citations(ans, self._CTX) == []

    def test_heading_anchor_matches_title(self):
        ans = "…[출처: [[최신 회의#결정/합의]]]"
        assert wa._verify_citations(ans, self._CTX) == []

    def test_hallucinated_citation_flagged(self):
        ans = "양자회의가 열렸습니다 [출처: [[requirements.txt]]]."
        assert wa._verify_citations(ans, self._CTX) == ["requirements.txt"]

    def test_lenient_partial_match_not_flagged(self):
        # 컨텍스트 '양자 세미나'와 부분 포함되면 정당 인용으로 관대하게 인정.
        ans = "…[출처: [[양자]]]"
        assert wa._verify_citations(ans, self._CTX) == []

    def test_non_citation_wikilinks_ignored(self):
        # [출처:] 형식이 아닌 본문 위키링크는 검증 대상이 아니다.
        ans = "관련해서 [[아무노트]] 를 참고. [출처: [[최신 회의]]]"
        assert wa._verify_citations(ans, self._CTX) == []

    def test_ask_appends_warning_and_sets_unverified(self, monkeypatch):
        monkeypatch.setattr(wa, "_c", lambda k, d=None: d)  # verify_citations 기본 true
        qa = wa.WikiQA.__new__(wa.WikiQA)
        qa._online = False
        qa._max_notes = 10
        qa._unverified = "확인 불가"
        qa._conflict = "⚠️ 충돌"
        qa._llm = _FakeLLM("## 요약 답변\n양자회의 열림 [출처: [[requirements.txt]]]")
        monkeypatch.setattr(qa, "_ensure_clients", lambda: None)
        monkeypatch.setattr(qa, "_plan_query", lambda q: {})
        monkeypatch.setattr(qa, "_gather_context",
                            lambda q, lim, plan=None: [{"title": "최신 회의"}])
        res = qa.ask("최근 회의?")
        assert res["citation_issues"] == ["requirements.txt"]
        assert res["unverified"] is True
        assert "인용 검증" in res["answer"]
