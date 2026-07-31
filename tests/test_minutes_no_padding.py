# -*- coding: utf-8 -*-
"""회의록이 '내용보다 부풀지 않게' 만든 장치들의 회귀 테스트.

배경: 프롬프트가 **녹음 길이**에 분량 하한을 걸어 놨다("75분 회의 기준 최소 A4 2~3쪽",
"30분 발표 기준 최소 3~4쪽", "각 소주제마다 최소 3~5개 세부 불릿"). 알맹이가 적은
회의에서 모델이 그 하한을 맞출 방법은 배경·함의·권고를 지어내는 것뿐이다. 여기서
검증하는 것은 프롬프트 문구 자체다 — 프롬프트가 이 리포의 산출물 품질을 결정하는
'코드'이고, 하한 문구가 되돌아오면 부풀리기도 같이 돌아온다.
"""

import re

import pytest

from meeting_minutes_app.meeting_pipeline import minutes_generation as mg


ALL_TEMPLATES = {
    "_MINUTES_MEETING": mg._MINUTES_MEETING,
    "_MINUTES_SEMINAR": mg._MINUTES_SEMINAR,
    "_MINUTES_LECTURE": mg._MINUTES_LECTURE,
    "_MINUTES_MEMO": mg._MINUTES_MEMO,
    "_SUMMARY_MEETING": mg._SUMMARY_MEETING,
    "_SUMMARY_SEMINAR": mg._SUMMARY_SEMINAR,
    "_SUMMARY_LECTURE": mg._SUMMARY_LECTURE,
    "_SUMMARY_MEMO": mg._SUMMARY_MEMO,
}


class TestNoVolumeFloors:
    """분량 하한 문구가 어떤 템플릿에도 없어야 한다."""

    #: "최소 A4 2~3쪽", "A4 1.5~2쪽 이상" 등 — 쪽수 하한 표현.
    PAGE_FLOOR = re.compile(r"(최소|이상).{0,12}A4|A4.{0,12}(쪽|페이지).{0,6}이상")

    @pytest.mark.parametrize("name", sorted(ALL_TEMPLATES))
    def test_no_page_count_floor(self, name):
        assert not self.PAGE_FLOOR.search(ALL_TEMPLATES[name]), (
            f"{name} 에 쪽수 하한이 있다 — 분량은 내용량이 정해야 한다")

    @pytest.mark.parametrize("name", sorted(ALL_TEMPLATES))
    def test_no_per_item_bullet_quota(self, name):
        """'각 소주제마다 최소 N개 불릿' 류의 항목별 개수 하한 금지."""
        assert not re.search(r"마다\s*\*{0,2}최소\s*\d", ALL_TEMPLATES[name]), (
            f"{name} 에 항목별 불릿 개수 하한이 있다")

    @pytest.mark.parametrize("name", sorted(ALL_TEMPLATES))
    def test_no_anti_brevity_directive(self, name):
        """'짧은 기록이 되지 않도록' 류의 안티-간결 지시 금지."""
        assert "짧은 기록이 되지 않도록" not in ALL_TEMPLATES[name], (
            f"{name} 이 짧은 기록을 금지하고 있다")


class TestNoCutHasPaddingGuard:
    """공통 지시(_NO_CUT)는 '누락 금지'와 '분량 채우기 금지'를 함께 담아야 한다."""

    @pytest.mark.parametrize("block", [mg._NO_CUT, mg._NO_CUT_MEETING])
    def test_padding_ban_present(self, block):
        assert "누락 금지" in block or "누락은 금지" in block
        assert "만들지 마세요" in block or "만들지 말" in block
        assert "짧은 기록이 되지 않도록" not in block

    def test_memo_prompt_not_self_contradicting(self):
        """memo 템플릿은 '형식적 분량 채우기 금지'라고 쓴다 — 앞에 붙는 공통 지시가
        그것과 반대되는 말을 하면 같은 프롬프트 안에 상반된 지시가 들어간다."""
        assert "형식적 분량 채우기 금지" in mg._MINUTES_MEMO
        full = mg._get_minutes_prompt("memo")
        assert "짧은 기록이 되지 않도록" not in full


class TestNoFabricatedCitations:
    """발표에 없던 논문을 내부 지식에서 '최소 N개' 뽑으라는 지시가 없어야 한다."""

    def test_seminar_has_no_citation_quota(self):
        t = mg._MINUTES_SEMINAR
        assert "최소 3~5개" not in t
        assert "언급 여부 무관" not in t

    def test_summary_seminar_has_no_invented_reading_list(self):
        assert "추가 조사 권장 주제" not in mg._SUMMARY_SEMINAR


class TestExternalMeetingTemplate:
    """활성 템플릿(prompts/meeting_analysis.md — config analysis.templates_dir)."""

    @pytest.fixture
    def tmpl(self):
        t = mg._load_external_template("meeting")
        if not t:
            pytest.skip("analysis.templates_dir 미설정 — 내장 템플릿 사용 중")
        return t

    def test_no_org_specific_mandatory_sections(self, tmpl):
        """특정 회의에서 굳은 조직 고유 섹션이 모든 회의에 강제되면 안 된다."""
        for token in ("인재개발원", "지주사", "그룹사"):
            assert token not in tmpl, f"'{token}' 이 고정 목차에 남아 있다"

    def test_sections_are_conditional(self, tmpl):
        assert "고정 목차가 아니라" in tmpl
        assert "섹션 자체를 만들지 않습니다" in tmpl

    def test_no_volume_target(self, tmpl):
        assert "분량 목표는 없습니다" in tmpl
        assert not TestNoVolumeFloors.PAGE_FLOOR.search(tmpl)


class TestUsabilityGateIsNotAVolumeFloor:
    """품질 게이트는 표기 흔들림을 품질 미달로 오판하지 않는다."""

    def test_korean_only_action_header_passes(self):
        """`### 액션 아이템`(영문 병기 없음)도 통과해야 한다 — 예전엔 'Action Item'
        리터럴만 인정해 정상 회의록이 재생성으로 넘어가 LLM 호출이 한 번 더 나갔다."""
        text = (
            "## 260730 주간회의 회의록\n\n"
            "### 주요 논의 내용\n- 논의\n\n"
            "### 결정사항\n확정된 결정 없음\n\n"
            "### 액션 아이템\n도출된 액션 없음\n"
        ) + "본문 " * 200
        usable, reason = mg._minutes_is_usable(text, 3000, "meeting")
        assert usable, reason

    def test_english_header_still_passes(self):
        text = (
            "## 260730 Weekly 회의록\n\n"
            "### Discussion\n- x\n\n"
            "### Decisions\n none\n\n"
            "### Action Items\n none\n"
        ) + "본문 " * 200
        usable, reason = mg._minutes_is_usable(text, 3000, "meeting")
        assert usable, reason

    def test_short_script_skips_gate(self):
        """스크립트가 짧으면 짧은 회의록이 정상 — 게이트 자체를 건너뛴다."""
        usable, _ = mg._minutes_is_usable("### 결정사항\n없음", 120, "meeting")
        assert usable

    def test_missing_section_still_caught(self):
        text = "### 잡담\n- x\n### 기타\n- y\n" + "본문 " * 200
        usable, reason = mg._minutes_is_usable(text, 3000, "meeting")
        assert not usable and "결정" in reason
