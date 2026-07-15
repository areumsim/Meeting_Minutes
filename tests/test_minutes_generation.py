# -*- coding: utf-8 -*-
"""minutes_generation.py 테스트 — 긴 회의록 청크 분할(액션 아이템) +
회의록 생성 품질 게이트(트랙 B: 회의록 보완 기능 강화)."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meeting_minutes_app.meeting_pipeline import minutes_generation as mg


class FakeLLM:
    """.chat() 호출마다 미리 준비된 응답을 순서대로(마지막 것은 반복) 반환."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def chat(self, system, user, temp=0.1, **kw):
        self.calls += 1
        idx = min(self.calls - 1, len(self.responses) - 1)
        return self.responses[idx]


class TestExtractActionItemsChunking:
    def test_single_chunk_for_short_minutes(self):
        llm = FakeLLM(['[{"assignee":"김철수","task":"보고서 작성","deadline":null,"context":""}]'])
        result = mg.extract_action_items("짧은 회의록 내용", llm)
        assert llm.calls == 1
        items = json.loads(result)
        assert len(items) == 1
        assert items[0]["task"] == "보고서 작성"

    def test_long_minutes_split_into_multiple_chunks_and_deduped(self):
        long_minutes = "\n".join(
            f"논의 항목 {i}: 세부 내용을 설명합니다." for i in range(1, 600)
        )
        assert len(long_minutes) > 6000  # analysis.actions_source_max_chars 기본값 초과
        responses = [
            '[{"assignee":"팀A","task":"작업1","deadline":null,"context":""}]',
            '[{"assignee":"팀B","task":"작업2","deadline":null,"context":""}]',
            # 청크 오버랩으로 같은 액션이 재추출되는 상황을 시뮬레이션
            '[{"assignee":"팀A","task":"작업1","deadline":null,"context":""}]',
        ]
        llm = FakeLLM(responses)
        result = mg.extract_action_items(long_minutes, llm)
        assert llm.calls >= 2  # 여러 구간으로 나뉘어 여러 번 호출됨
        items = json.loads(result)
        tasks = {(i["assignee"], i["task"]) for i in items}
        assert ("팀A", "작업1") in tasks
        assert ("팀B", "작업2") in tasks
        assert len(items) == 2  # (assignee, task) 정규화 기준 중복 제거

    def test_non_meeting_doc_type_returns_none(self):
        llm = FakeLLM(["[]"])
        assert mg.extract_action_items("내용", llm, doc_type="seminar") is None
        assert llm.calls == 0


class TestMinutesIsUsable:
    def test_skips_gate_for_short_script(self):
        usable, _ = mg._minutes_is_usable("짧음", 100, "meeting")
        assert usable

    def test_empty_result_fails(self):
        usable, reason = mg._minutes_is_usable("", 2000, "meeting")
        assert not usable
        assert "비어" in reason

    def test_missing_required_sections_fails(self):
        text = "### A. 배경\n\n내용\n\n### B. 논의\n\n내용"
        usable, reason = mg._minutes_is_usable(text, 3000, "meeting")
        assert not usable
        assert "결정 사항" in reason or "Action Item" in reason

    def test_too_few_subsections_fails(self):
        text = "### 결정 사항\n\ncontent"
        usable, reason = mg._minutes_is_usable(text, 3000, "meeting")
        assert not usable

    def test_well_formed_meeting_minutes_passes(self):
        text = (
            "### A. 배경\n\n세부내용1\n\n### B. 논의\n\n세부내용2\n\n"
            "### 결정 사항\n\n1. 결정1\n\n### Action Item\n\n- 업무1\n"
        )
        usable, _ = mg._minutes_is_usable(text, 100, "meeting")
        assert usable

    def test_seminar_requires_different_headers(self):
        text = "### A. 발표\n\n내용\n\n### B. 질문\n\n내용"
        usable, reason = mg._minutes_is_usable(text, 3000, "seminar")
        assert not usable
        assert "핵심 인사이트" in reason


class TestGenerateMinutesQualityGate:
    def test_retries_once_when_output_too_short_then_accepts_result(self):
        bad = "짧음"
        good = (
            "### A. 배경\n\n" + ("세부내용 " * 40) + "\n\n### B. 논의\n\n" + ("세부내용 " * 40)
            + "\n\n### 결정 사항\n\n1. 결정\n\n### Action Item\n\n- 업무\n"
        )
        llm = FakeLLM([bad, good])
        segments = [{"speaker": "A", "text": "발화 " * 300, "start": 1}]
        result = mg.generate_minutes(segments, llm, doc_type="meeting")
        assert llm.calls == 2
        assert result == good

    def test_no_retry_when_first_result_is_usable(self):
        good = (
            "### A. 배경\n\n" + ("세부내용 " * 40) + "\n\n### B. 논의\n\n" + ("세부내용 " * 40)
            + "\n\n### 결정 사항\n\n1. 결정\n\n### Action Item\n\n- 업무\n"
        )
        llm = FakeLLM([good, "이건 재시도라면 나올 값(호출되면 안 됨)"])
        segments = [{"speaker": "A", "text": "발화 " * 300, "start": 1}]
        result = mg.generate_minutes(segments, llm, doc_type="meeting")
        assert llm.calls == 1
        assert result == good

    def test_short_script_skips_gate_no_retry(self):
        llm = FakeLLM(["짧은 스크립트라 짧은 결과도 정상"])
        segments = [{"speaker": "A", "text": "안녕하세요", "start": 0}]
        result = mg.generate_minutes(segments, llm, doc_type="meeting")
        assert llm.calls == 1
        assert result == "짧은 스크립트라 짧은 결과도 정상"
