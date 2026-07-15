# -*- coding: utf-8 -*-
"""RealtimeVaultSearcher (wiki_core/realtime_search.py) 단위 테스트.

전부 오프라인 — 실제 인덱스/Obsidian/네트워크를 건드리지 않는다.
"""

import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meeting_minutes_app.wiki_core import realtime_search as rs


class FakeIndexer:
    def __init__(self, results=None):
        self.results = results if results is not None else []
        self.queries = []

    def search(self, query, limit=5):
        self.queries.append(query)
        return self.results


class FakeObs:
    def __init__(self, results=None):
        self.results = results or []
        self.closed = False

    def search_simple(self, query, context_length=150, limit=5):
        return self.results

    def close(self):
        self.closed = True


def make_searcher(monkeypatch, *, gate=True, interval=3, backend="auto",
                  topic="", on_notes=None):
    cfg = {
        "wiki.realtime_vault_search": gate,
        "wiki.realtime_search_interval": interval,
        "wiki.realtime_search_backend": backend,
    }
    monkeypatch.setattr(rs, "_c", lambda k, d=None: cfg.get(k, d))
    return rs.RealtimeVaultSearcher(topic=topic, on_notes=on_notes)


def inject_indexer(searcher, results):
    """lazy init을 우회해 가짜 인덱서 주입."""
    searcher._indexer = FakeIndexer(results)
    searcher._init_done = True
    return searcher._indexer


INDEX_HIT = {
    "path": "01_References/양자컴퓨팅.md",
    "title": "양자컴퓨팅",
    "wikilink_title": "양자컴퓨팅",
    "snippet": "양자컴퓨팅 개요...",
    "score": 1.23,
}


class TestGateAndThrottle:
    def test_gate_off_is_noop(self, monkeypatch):
        s = make_searcher(monkeypatch, gate=False)
        assert not s.enabled
        assert s._pool is None
        s.offer_segment("아무 발화")   # 예외 없이 조용히 무시
        assert s.collected_notes() == []
        s.shutdown()

    def test_throttle_interval(self, monkeypatch):
        s = make_searcher(monkeypatch, gate=True, interval=3)
        submitted = []
        s._pool = type("P", (), {
            "submit": lambda self, fn, *a: submitted.append(a),
            "shutdown": lambda self, wait=True: None,
        })()
        for i in range(9):
            s.offer_segment(f"발화 {i}")
        assert len(submitted) == 3  # 3, 6, 9번째만

    def test_empty_text_not_counted(self, monkeypatch):
        s = make_searcher(monkeypatch, gate=True, interval=1)
        submitted = []
        s._pool = type("P", (), {
            "submit": lambda self, fn, *a: submitted.append(a),
            "shutdown": lambda self, wait=True: None,
        })()
        s.offer_segment("")
        s.offer_segment("   ")
        assert submitted == []


class TestSearch:
    def test_index_hit_normalized(self, monkeypatch):
        collected = []
        s = make_searcher(monkeypatch, topic="양자", on_notes=collected.append)
        idx = inject_indexer(s, [INDEX_HIT])
        s._search("양자컴퓨팅 로드맵 이야기")
        notes = s.collected_notes()
        assert len(notes) == 1
        n = notes[0]
        assert n["title"] == "양자컴퓨팅"
        assert n["filename"] == "01_References/양자컴퓨팅.md"
        assert n["source"] == "index"
        assert n["segment_text"].startswith("양자컴퓨팅 로드맵")
        # topic이 쿼리에 포함됨
        assert "양자" in idx.queries[0]
        # on_notes 호출됨 (top3)
        assert collected and collected[0][0]["title"] == "양자컴퓨팅"

    def test_rest_fallback_normalized(self, monkeypatch):
        s = make_searcher(monkeypatch)
        s._obs = FakeObs([{"filename": "00_Meetings\\주간회의.md",
                           "score": 0.5, "matches": ["m1", "m2", "m3"]}])
        s._init_done = True
        s._search("발화")
        n = s.collected_notes()[0]
        assert n["title"] == "주간회의"
        assert n["source"] == "rest"
        assert n["matches"] == ["m1", "m2"]

    def test_consecutive_same_titles_shown_once(self, monkeypatch):
        shown = []
        s = make_searcher(monkeypatch, on_notes=shown.append)
        inject_indexer(s, [INDEX_HIT])
        s._search("첫 발화")
        s._search("둘째 발화")   # 같은 노트 세트 → 표시 생략
        assert len(shown) == 1
        # 수집은 계속 누적
        assert len(s.collected_notes()) == 2

    def test_collected_titles_unique_ordered(self, monkeypatch):
        s = make_searcher(monkeypatch)
        inject_indexer(s, [INDEX_HIT,
                           {**INDEX_HIT, "path": "b.md", "wikilink_title": "B노트"}])
        s._search("발화")
        s._search("발화2")
        assert s.collected_titles() == ["양자컴퓨팅", "B노트"]

    def test_on_notes_exception_swallowed(self, monkeypatch):
        def boom(notes):
            raise RuntimeError("표시 실패")
        s = make_searcher(monkeypatch, on_notes=boom)
        inject_indexer(s, [INDEX_HIT])
        s._search("발화")  # 예외 전파 없어야 함
        assert len(s.collected_notes()) == 1


class TestLazyInitFailure:
    def test_no_backend_silently_disables(self, monkeypatch):
        s = make_searcher(monkeypatch, backend="index")
        from meeting_minutes_app.wiki_core import vault_indexer as vi
        monkeypatch.setattr(vi.VaultIndexer, "from_config",
                            classmethod(lambda cls: None))
        s._search("발화")
        assert s._disabled
        assert not s.enabled
        assert s.collected_notes() == []
        # 이후 offer는 카운터만 돌고 제출 안 함
        s.offer_segment("다음 발화")
        s.shutdown()


class TestShutdown:
    def test_shutdown_drains_pending(self, monkeypatch):
        s = make_searcher(monkeypatch, interval=1)
        slow_done = threading.Event()

        class SlowIndexer:
            def search(self, query, limit=5):
                time.sleep(0.2)
                slow_done.set()
                return [INDEX_HIT]

        s._indexer = SlowIndexer()
        s._init_done = True
        s.offer_segment("발화")
        s.shutdown(wait=True)
        assert slow_done.is_set()
        assert len(s.collected_notes()) == 1

    def test_shutdown_closes_obs(self, monkeypatch):
        s = make_searcher(monkeypatch)
        obs = FakeObs()
        s._obs = obs
        s._init_done = True
        s.shutdown()
        assert obs.closed
