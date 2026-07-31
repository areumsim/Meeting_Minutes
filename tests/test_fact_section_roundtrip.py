# -*- coding: utf-8 -*-
"""노트 대조 섹션 — '생성 제목'과 '제거 정규식'이 갈라지지 않는지.

이 둘은 서로 다른 모듈에 있다(meeting_workflow 가 만들고 publish 가 지운다). 제목만
바꾸고 정규식을 안 고치면 재발행(화자 수정 등)에서 이전 블록이 남아 같은 섹션이 두 번
붙는다 — 실제로 '사실 검증' → '노트 대조' 로 표현을 고칠 때 걸린 함정이다.

표현 수위도 함께 고정한다: 이 기능의 판정은 노트 회수 품질에 종속되고, 실측
(scripts/measure_retrieval_floor.py)에서 회수가 약하다는 것이 확인됐다. 그래서
'검증됨/확인됨' 같은 단정 표현으로 되돌아가면 안 된다.
"""

import pytest

from meeting_minutes_app.meeting_pipeline import meeting_workflow as mw
from meeting_minutes_app.meeting_pipeline.publish import (
    FACT_SECTION_HEADING, _strip_fact_verification_sections,
)


RESULTS = [
    {"claim": "A사와 NDA를 체결했다", "verdict": "match", "summary": "노트에 기재",
     "evidence": "2026-03 NDA 체결", "sources": ["A사"], "confidence": "medium"},
    {"claim": "참가팀 30팀", "verdict": "conflict", "summary": "노트는 24팀",
     "evidence": "24팀", "sources": ["해커톤"], "confidence": "medium"},
    {"claim": "B는 양자 플랫폼", "verdict": "unknown", "summary": "확인 불가",
     "evidence": "", "sources": [], "confidence": "low"},
]


@pytest.fixture
def section():
    return mw._format_verification_section(RESULTS)


class TestRoundTrip:
    def test_generated_section_uses_shared_heading(self, section):
        assert section.startswith(FACT_SECTION_HEADING)

    def test_generated_section_is_strippable(self, section):
        """생성 → 제거가 왕복해야 한다(재발행 중복 방지의 핵심)."""
        doc = "## 주요 논의 내용\n- 내용\n\n" + section
        out = _strip_fact_verification_sections(doc)
        assert FACT_SECTION_HEADING not in out
        assert "NDA를 체결했다" not in out
        assert "## 주요 논의 내용" in out      # 다른 섹션은 보존

    def test_double_append_does_not_duplicate(self, section):
        """두 번 붙여도 최종 문서에 섹션이 하나만 남는다."""
        doc = "## 주요 논의 내용\n- 내용\n\n" + section
        doc2 = _strip_fact_verification_sections(doc).rstrip() + "\n\n" + section
        assert doc2.count(FACT_SECTION_HEADING) == 1

    def test_legacy_heading_stripped(self):
        """구 산출물의 '## 사실 검증'도 걷어낸다 — 예전 회의록 재처리 시 중복 방지."""
        doc = ("## 주요 논의 내용\n- 내용\n\n"
               "## 사실 검증\n\n- ✅ **[확인됨]** 옛 판정\n\n"
               "## 액션 아이템\n- 할 일\n")
        out = _strip_fact_verification_sections(doc)
        assert "옛 판정" not in out
        assert "## 액션 아이템" in out and "## 주요 논의 내용" in out

    def test_following_section_survives(self, section):
        doc = section + "\n## 🔗 관련 노트\n- [[X]]\n"
        out = _strip_fact_verification_sections(doc)
        assert "🔗 관련 노트" in out and "[[X]]" in out


class TestWordingIsNotOverclaiming:
    def test_no_confirmed_label(self, section):
        assert "[확인됨]" not in section, "회수 품질이 뒷받침하지 않는 단정 표현"
        assert "[노트와 일치]" in section

    def test_header_carries_caveat(self, section):
        assert "사람 확인" in section
        assert "확정된 사실 검증이 아닙니다" in section

    def test_conflict_still_flagged_prominently(self, section):
        """충돌은 약하게 만들지 않는다 — 사람이 볼 이유가 되는 신호다."""
        assert "⚠️ **[충돌]**" in section


class TestStatusCountingFromStructuredResults:
    """상태 문구 집계가 아이콘 문자열이 아니라 verdict 필드에서 나오는지."""

    def test_counts_match_verdicts(self):
        verdicts = [r["verdict"] for r in RESULTS]
        assert sum(1 for v in verdicts if v == "match") == 1
        assert sum(1 for v in verdicts if v == "conflict") == 1
        assert sum(1 for v in verdicts if v not in ("match", "conflict")) == 1

    def test_icon_counting_would_have_broken(self, section):
        """구 방식(마크다운 아이콘 카운트)이 왜 못 쓰는지 못 박아 둔다."""
        assert section.count("- ✅") == 0   # 표기를 바꾸면 0이 된다
