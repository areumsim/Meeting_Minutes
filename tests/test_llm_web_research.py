# -*- coding: utf-8 -*-
"""LLMClient.web_research — GPT responses 폴백의 flaky 미검색 재시도 + citation
추출 테스트 (실제 API 호출 없이 mock).
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meeting_minutes_app.common import llm_client as lc


def _text_item(text, annotations=None):
    content = SimpleNamespace(type="output_text", text=text, annotations=annotations or [])
    return SimpleNamespace(type="message", content=[content])


def _search_call_item():
    return SimpleNamespace(type="web_search_call")


def _annotation(url, title=None):
    return SimpleNamespace(url=url, title=title)


class FakeResponses:
    def __init__(self, outputs):
        self._outputs = list(outputs)
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        out = self._outputs[min(self.calls - 1, len(self._outputs) - 1)]
        return SimpleNamespace(output=out)


def make_llm(outputs):
    llm = lc.LLMClient.__new__(lc.LLMClient)
    llm.preferred = "gpt"
    llm.anthropic = None  # Anthropic 경로 스킵 — GPT 경로만 테스트
    llm.openai = SimpleNamespace(responses=FakeResponses(outputs))
    llm._call_count = 0
    llm._total_tokens = 0
    return llm


class TestSearchDetectionAndRetry:
    def test_real_search_returns_immediately(self):
        outputs = [[_search_call_item(), _text_item("실제 검색 결과입니다.")]]
        llm = make_llm(outputs)
        r = llm.web_research("질문")
        assert r["searched"] is True
        assert r["text"] == "실제 검색 결과입니다."
        assert llm.openai.responses.calls == 1

    def test_stub_without_search_call_retries_once(self):
        outputs = [
            [_text_item("찾아보겠습니다. 잠시만 기다려 주세요.")],  # 1차: 검색 미실행
            [_search_call_item(), _text_item("재시도 후 실제 결과.")],  # 2차: 성공
        ]
        llm = make_llm(outputs)
        r = llm.web_research("질문")
        assert llm.openai.responses.calls == 2
        assert r["searched"] is True
        assert r["text"] == "재시도 후 실제 결과."

    def test_stub_twice_still_returns_last_text_not_lowered_silently(self):
        outputs = [
            [_text_item("찾아보겠습니다.")],
            [_text_item("여전히 검색 안 함.")],
        ]
        llm = make_llm(outputs)
        r = llm.web_research("질문")
        assert llm.openai.responses.calls == 2
        # searched=False로 정직하게 표시하되, 텍스트는 버리지 않고 반환 (스킵 아님)
        assert r["searched"] is False
        assert r["text"] == "여전히 검색 안 함."

    def test_citations_extracted_from_annotations(self):
        anns = [_annotation("https://arxiv.org/abs/1234", "Some Paper")]
        outputs = [[_search_call_item(), _text_item("근거 있는 답변.", annotations=anns)]]
        llm = make_llm(outputs)
        r = llm.web_research("질문")
        assert r["sources"] == [{"title": "Some Paper", "url": "https://arxiv.org/abs/1234"}]
        assert "source_status" not in r

    def test_dedup_citations(self):
        anns = [_annotation("https://x.com/a"), _annotation("https://x.com/a")]
        outputs = [[_search_call_item(), _text_item("답변.", annotations=anns)]]
        llm = make_llm(outputs)
        r = llm.web_research("질문")
        assert len(r["sources"]) == 1

    def test_no_citations_marks_no_urls_status(self):
        outputs = [[_search_call_item(), _text_item("검색은 했지만 URL 없음.")]]
        llm = make_llm(outputs)
        r = llm.web_research("질문")
        assert r["sources"] == []
        assert r["source_status"] == "no_urls"

    def test_empty_text_falls_through_to_plain_chat(self):
        outputs = [[], []]  # 두 시도 모두 message 없음
        llm = make_llm(outputs)
        llm.chat = lambda system, query, temp=0.2, max_tokens=1500: "최종 폴백 답변"
        r = llm.web_research("질문")
        assert r["searched"] is False
        assert r["source_status"] == "model_fallback"
        assert r["text"] == "최종 폴백 답변"

    def test_exception_on_first_attempt_retries(self):
        class FlakyResponses:
            def __init__(self):
                self.calls = 0

            def create(self, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("일시 네트워크 오류")
                return SimpleNamespace(output=[_search_call_item(), _text_item("복구됨.")])

        llm = make_llm([])
        llm.openai.responses = FlakyResponses()
        r = llm.web_research("질문")
        assert r["searched"] is True
        assert r["text"] == "복구됨."
