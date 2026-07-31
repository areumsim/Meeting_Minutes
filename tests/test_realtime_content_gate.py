# -*- coding: utf-8 -*-
"""실시간 검색의 내용 게이트 — 검색할 발화를 순번이 아니라 **내용**으로 고른다.

예전 동작: `counter % realtime_search_interval == 0` 만 봤다. 순번이라 내용과 무관해서
한국어 녹음에서 이런 일이 났다(실측 재현):

    "남우진 교수님 볼츠만 머신 발표 내용을 정리해야 합니다."  → 스킵(순번 미달)
    "다음 회의는 다음 주 화요일입니다."                      → 검색 → Daily·Project 표시

알맹이 있는 발화는 버려지고 군더더기가 무관 노트를 화면에 띄웠다. 사용자에게는
"관련 노트가 안 나온다 + 이상한 게 뜬다"로 보인다.

지금은 `wiki.realtime_min_terms`(기본 3) 로 볼트 어휘와 일치하는 검색어 수를 보고,
스로틀 카운터는 **자격 있는 발화만** 센다.
"""

import pytest

from meeting_minutes_app.wiki_core import realtime_search as rsmod


class _FakeIndexer:
    """known_term_count 만 흉내내는 인덱서 — 볼트/네트워크 없이 게이트만 검증."""

    def __init__(self, table):
        self.table = table
        self.calls = []

    def known_term_count(self, text):
        self.calls.append(text)
        return self.table.get(text, 0)


FILLER = "안녕하세요 오늘 회의 시작하겠습니다"
FILLER2 = "다음 회의는 다음 주 화요일입니다"
SUBST = "남우진 교수님 볼츠만 머신 발표 내용을 정리해야 합니다"
SUBST2 = "NISQ 파라메트릭 퀀텀 서킷 학습 얘기가 나왔습니다"

TABLE = {FILLER: 0, FILLER2: 2, SUBST: 7, SUBST2: 5}


@pytest.fixture
def searcher(monkeypatch):
    monkeypatch.setattr(rsmod, "_c", lambda key, default=None: {
        "wiki.realtime_vault_search": True,
        "wiki.realtime_search_interval": 1,
        "wiki.realtime_min_terms": 3,
    }.get(key, default))
    s = rsmod.RealtimeVaultSearcher(topic="", on_notes=lambda n: None)
    s._indexer = _FakeIndexer(TABLE)
    s._init_done = True
    submitted = []
    s._pool = type("P", (), {"submit": lambda self, fn, *a: submitted.append(a[0])})()
    s.submitted = submitted
    return s


class TestContentGate:
    def test_filler_never_searched(self, searcher):
        searcher.offer_segment(FILLER)
        searcher.offer_segment(FILLER2)      # 2개 일치 — 문턱 3 미달
        assert searcher.submitted == []
        assert searcher._counter == 0, "자격 없는 발화는 스로틀 카운터도 올리지 않는다"

    def test_substantive_searched(self, searcher):
        searcher.offer_segment(SUBST)
        assert searcher.submitted == [SUBST]

    def test_filler_does_not_consume_throttle_slot(self, searcher):
        """군더더기가 카운터를 먹어 알맹이 발화가 밀리는 일이 없어야 한다."""
        for t in (FILLER, FILLER2, SUBST, FILLER, SUBST2):
            searcher.offer_segment(t)
        assert searcher.submitted == [SUBST, SUBST2]

    def test_empty_and_blank(self, searcher):
        searcher.offer_segment("")
        searcher.offer_segment("   ")
        assert searcher.submitted == []


class TestThrottleStillApplies:
    def test_interval_counts_only_qualifying(self, monkeypatch):
        monkeypatch.setattr(rsmod, "_c", lambda key, default=None: {
            "wiki.realtime_vault_search": True,
            "wiki.realtime_search_interval": 2,
            "wiki.realtime_min_terms": 3,
        }.get(key, default))
        s = rsmod.RealtimeVaultSearcher(topic="", on_notes=lambda n: None)
        s._indexer = _FakeIndexer(TABLE)
        s._init_done = True
        got = []
        s._pool = type("P", (), {"submit": lambda self, fn, *a: got.append(a[0])})()
        # 군더더기를 사이에 섞어도 '자격 있는 2번째'가 검색된다
        for t in (SUBST, FILLER, SUBST2, FILLER2):
            s.offer_segment(t)
        assert got == [SUBST2]


class TestFailSafe:
    def test_gate_off_when_zero(self, monkeypatch):
        monkeypatch.setattr(rsmod, "_c", lambda key, default=None: {
            "wiki.realtime_vault_search": True,
            "wiki.realtime_search_interval": 1,
            "wiki.realtime_min_terms": 0,
        }.get(key, default))
        s = rsmod.RealtimeVaultSearcher(topic="", on_notes=lambda n: None)
        s._indexer = _FakeIndexer(TABLE)
        s._init_done = True
        got = []
        s._pool = type("P", (), {"submit": lambda self, fn, *a: got.append(a[0])})()
        s.offer_segment(FILLER)
        assert got == [FILLER], "0 이면 게이트를 끈다(종전 동작)"

    def test_no_indexer_passes_through(self, searcher):
        """인덱스가 아직 lazy init 전이면 게이트가 통과시킨다 —
        초기화 지연을 '내용 없음'으로 오판해 검색을 막으면 안 된다."""
        searcher._indexer = None
        assert searcher.has_searchable_content(FILLER) is True

    def test_indexer_error_passes_through(self, searcher):
        class Boom:
            def known_term_count(self, text):
                raise RuntimeError("index broken")
        searcher._indexer = Boom()
        assert searcher.has_searchable_content(FILLER) is True
