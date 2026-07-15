# -*- coding: utf-8 -*-
"""enrichment.autolink_entities() 테스트 (트랙 C: 본문 자동 위키링크)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meeting_minutes_app.meeting_pipeline import enrichment


class TestAutolinkEntities:
    def test_links_first_occurrence_only(self):
        text = "한빛은 화학 기업이다. 한빛은 최근 양자컴퓨팅에 투자했다."
        result = enrichment.autolink_entities(text, {"한빛": "한빛"})
        assert result.count("[[한빛]]") == 1
        assert "한빛은 화학" not in result  # 첫 등장은 링크로 대체됨
        assert result.count("한빛") == 2  # 링크 안 1 + 두번째 등장(비링크) 1

    def test_uses_alias_syntax_when_display_name_differs(self):
        text = "서지훈 교수님이 발표했다."
        result = enrichment.autolink_entities(text, {"서지훈 교수": "서지훈"})
        # entity_links의 키는 "서지훈 교수"인데 본문엔 "서지훈 교수님"으로 등장 —
        # find()는 부분 일치이므로 "서지훈 교수" 부분만 링크로 감싼다.
        assert "[[서지훈|서지훈 교수]]" in result

    def test_skips_heading_lines(self):
        text = "## 한빛 관련 논의\n\n오늘 한빛 이야기를 했다."
        result = enrichment.autolink_entities(text, {"한빛": "한빛"})
        assert "## 한빛 관련 논의" in result  # 헤딩은 그대로 보존
        assert "[[한빛]]" in result  # 본문 등장은 링크됨
        assert result.count("[[한빛]]") == 1

    def test_skips_already_linked_occurrence(self):
        text = "관련 노트: [[한빛]] 참고. 한빛은 화학 기업이다."
        result = enrichment.autolink_entities(text, {"한빛": "한빛"})
        # 이미 링크된 등장은 건너뛰고 그 다음 미링크 등장을 링크해야 한다
        assert result.count("[[한빛]]") == 2

    def test_empty_inputs_return_text_unchanged(self):
        assert enrichment.autolink_entities("", {"a": "a"}) == ""
        assert enrichment.autolink_entities("본문", {}) == "본문"

    def test_short_names_are_ignored(self):
        text = "A는 좋다."
        result = enrichment.autolink_entities(text, {"A": "A"})
        assert result == text  # 1글자 이름은 오매칭 위험이 커 건너뜀

    def test_longer_names_matched_before_shorter_substrings(self):
        text = "한빛솔루션와 한빛은 서로 다른 법인이다."
        result = enrichment.autolink_entities(
            text, {"한빛솔루션": "한빛솔루션", "한빛": "한빛"})
        assert "[[한빛솔루션]]" in result
        assert "[[한빛]]" in result
