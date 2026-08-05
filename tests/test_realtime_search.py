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
from meeting_minutes_app.wiki_core import vault_indexer as vi


class FakeIndexer:
    """search()/sections_in_notes() 를 흉내내는 가짜 인덱서.

    search 는 실제 VaultIndexer 와 **같은 path_matcher 를 그대로 써서** 필터한다 —
    과거엔 이 fake 가 손으로 구현한 중간일치 필터를 갖고 있어서, 정작 구현이
    접두사만 보던 버그를 테스트가 덮어주지 못했다(실제 볼트에서는 논문 폴더
    83노트가 arm ② 에서 영구히 0건이었다). sections_in_notes 는 노트별 최적 섹션
    (근거 위치 특정)을 돌려준다 — 랭킹에는 관여하지 않는다.
    """

    def __init__(self, results=None, sections=None):
        self.results = results if results is not None else []
        #: {rel_path: {"heading","snippet","score"}}
        self.sections = sections if sections is not None else {}
        self.queries = []          # (query, path_prefixes, path_match)
        self.located = []          # sections_in_notes 로 넘어온 후보 목록

    def search(self, query, limit=5, path_prefixes=None, path_match="prefix"):
        self.queries.append((query, tuple(path_prefixes or ()), path_match))
        rows = self.results
        match = vi.path_matcher(path_prefixes, path_match)
        if match is not None:
            rows = [r for r in rows if match(str(r.get("path", "")))]
        return rows[:limit]

    def sections_in_notes(self, query, rel_paths):
        self.located.append(list(rel_paths))
        return {rel: self.sections[rel] for rel in rel_paths if rel in self.sections}


class FakeObs:
    def __init__(self, results=None):
        self.results = results or []
        self.closed = False

    def search_simple(self, query, context_length=150, limit=5):
        return self.results

    def close(self):
        self.closed = True


def make_searcher(monkeypatch, *, gate=True, interval=3, backend="auto",
                  topic="", on_notes=None, on_status=None, extra_cfg=None):
    cfg = {
        "wiki.realtime_vault_search": gate,
        "wiki.realtime_search_interval": interval,
        "wiki.realtime_search_backend": backend,
    }
    cfg.update(extra_cfg or {})
    monkeypatch.setattr(rs, "_c", lambda k, d=None: cfg.get(k, d))
    return rs.RealtimeVaultSearcher(topic=topic, on_notes=on_notes,
                                    on_status=on_status)


def inject_indexer(searcher, results, sections=None):
    """lazy init을 우회해 가짜 인덱서 주입."""
    searcher._indexer = FakeIndexer(results, sections)
    searcher._init_done = True
    return searcher._indexer


INDEX_HIT = {
    "path": "01_References/양자컴퓨팅.md",
    "title": "양자컴퓨팅",
    "wikilink_title": "양자컴퓨팅",
    "snippet": "양자컴퓨팅 개요...",
    "score": 1.23,
}

NOTE_HIT_MEETING = {
    "path": "00_Meetings/주간회의.md",
    "title": "주간회의",
    "wikilink_title": "주간회의",
    "snippet": "주간회의 노트 스니펫",
    "score": 0.42,
    "date": "2026-07-20",
}

#: sections_in_notes 반환 형태 (근거 위치 특정 결과)
SECTION_OF_MEETING = {
    "heading": "큐비트 로드맵",
    "level": 2,
    "snippet": "로드맵 논의 내용...",
    "score": 0.91,
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
        assert "양자" in idx.queries[0][0]
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

    def test_rest_fallback_gives_papers_no_boost(self, monkeypatch):
        """REST 폴백도 인덱스 경로와 같은 순위 규칙 — 논문 1.2배 가산이 없다.

        과거엔 이쪽에만 가산이 남아 있어 같은 발화가 백엔드에 따라 다른 순서로
        보였다(실측에서 반박된 그 가산이다: MRR 0.920→0.713)."""
        s = make_searcher(monkeypatch,
                          extra_cfg={"wiki.realtime_paper_dirs": ["01_References"]})
        s._obs = FakeObs([
            {"filename": "00_Meetings/주간회의.md", "score": 0.9},
            {"filename": "01_References/논문.md", "score": 0.1},
        ])
        s._init_done = True
        s._search("발화")
        notes = s.collected_notes()
        assert [n["title"] for n in notes] == ["주간회의", "논문"]
        assert [n["source_type"] for n in notes] == ["note", "paper"]
        # 순위 그대로 1/(k+rank+1) — 논문이라고 곱해지지 않는다
        assert notes[0]["rank_score"] == round(1.0 / (vi.RRF_K + 1), 6)
        assert notes[1]["rank_score"] == round(1.0 / (vi.RRF_K + 2), 6)

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

    def test_display_count_configurable(self, monkeypatch):
        shown = []
        s = make_searcher(monkeypatch, on_notes=shown.append,
                          extra_cfg={"wiki.realtime_display_count": 2})
        inject_indexer(s, [INDEX_HIT,
                           {**INDEX_HIT, "path": "b.md", "wikilink_title": "B"},
                           {**INDEX_HIT, "path": "c.md", "wikilink_title": "C"}])
        s._search("발화")
        assert len(shown[0]) == 2       # 후보 3개 중 표시 2개
        assert len(s.collected_notes()) == 3   # 수집은 전부


class TestInternalFirst:
    """FR-11 — 노트 랭킹 주축 + 논문 폴더 보강 + 후보 안에서 근거 섹션 특정.

    랭킹 구조는 실측(docs/검색랭킹_이론과근거.md)으로 고른 것이다. 여기서 고정하는
    계약: (a) 볼트 전체 섹션 검색을 랭킹에 섞지 않는다, (b) 논문 폴더는 **순위를
    전혀 우대하지 않고** 별도 검색 arm 으로 후보 진입만 보장한다, (c) 그 arm 의 폴더
    매칭은 경로 중간 일치(`path_match="segment"`)다.
    """

    def test_section_located_within_candidate(self, monkeypatch):
        s = make_searcher(monkeypatch)
        inject_indexer(s, [NOTE_HIT_MEETING],
                       {"00_Meetings/주간회의.md": SECTION_OF_MEETING})
        s._search("큐비트 로드맵 관련 발화")
        n = s.collected_notes()[0]
        assert n["heading"] == "큐비트 로드맵"
        assert n["section_path"] == "주간회의 › 큐비트 로드맵"
        assert n["found_by"] == "section"
        assert n["snippet"] == "로드맵 논의 내용..."   # 섹션 스니펫이 노트 스니펫을 대체

    def test_section_lookup_scoped_to_candidates(self, monkeypatch):
        """섹션 채점은 후보 노트에만 — 볼트 전체 섹션 스캔을 하지 않는다(지연 방지)."""
        s = make_searcher(monkeypatch)
        idx = inject_indexer(s, [INDEX_HIT, NOTE_HIT_MEETING], {})
        s._search("발화")
        assert idx.located == [["01_References/양자컴퓨팅.md", "00_Meetings/주간회의.md"]]
        assert not hasattr(idx, "search_sections")   # 전체 섹션 검색은 쓰지 않는다

    def test_no_section_match_falls_back_to_note_snippet(self, monkeypatch):
        s = make_searcher(monkeypatch)
        inject_indexer(s, [NOTE_HIT_MEETING], {})
        s._search("발화")
        n = s.collected_notes()[0]
        assert n["heading"] == ""
        assert n["found_by"] == "note"
        assert n["section_path"] == "주간회의"
        assert n["snippet"] == "주간회의 노트 스니펫"

    def test_paper_note_gets_no_rank_advantage(self, monkeypatch):
        """논문 폴더는 점수도 순위도 우대하지 않는다 — 후보 진입만 보장한다."""
        s = make_searcher(monkeypatch,
                          extra_cfg={"wiki.realtime_paper_dirs": ["02_이론_학습"]})
        일반 = {"path": "00_Meetings/주간회의.md", "wikilink_title": "주간회의",
                "snippet": "s", "score": 9.0}
        논문 = {"path": "02_이론_학습/QAOA.md", "wikilink_title": "QAOA",
                "snippet": "s", "score": 0.1}
        # 노트 검색 1위=일반, 2위=논문 → 가산점이 없으므로 순서 유지
        inject_indexer(s, [일반, 논문], {})
        s._search("QAOA 이야기")
        assert [n["title"] for n in s.collected_notes()] == ["주간회의", "QAOA"]
        # 논문 한정 검색으로만 들어온 후보는 노트 arm 결과 **뒤에** 합류한다
        s2 = make_searcher(monkeypatch,
                           extra_cfg={"wiki.realtime_paper_dirs": ["02_이론_학습"],
                                      "wiki.realtime_note_candidates": 1})
        inject_indexer(s2, [일반, 논문], {})
        s2._search("QAOA 이야기")
        titles = [n["title"] for n in s2.collected_notes()]
        assert titles == ["주간회의", "QAOA"]   # 논문은 보강 arm 으로 뒤에 합류

    def test_rank_scores_are_unique_so_no_tiebreak_can_fire(self, monkeypatch):
        """rank_score 는 유일한 순위의 단조 변환 — 동점이 없다.

        과거엔 '동점이면 논문 우선' tie-break 정렬축이 있었지만 이 성질 때문에 한
        번도 발동하지 않는 죽은 코드였다. 되살리려면 rank_score 설계부터 바꿔야
        하고, 그 방향(논문 우대)은 실측에서 반박됐다 — 그래서 여기서 고정한다.
        """
        s = make_searcher(monkeypatch,
                          extra_cfg={"wiki.realtime_paper_dirs": ["02_이론_학습"],
                                     "wiki.realtime_note_candidates": 3})
        rows = [{"path": "00_Meetings/n0.md", "wikilink_title": "n0",
                 "snippet": "s", "score": 5.0},
                {"path": "02_이론_학습/p1.md", "wikilink_title": "p1",
                 "snippet": "s", "score": 4.0},
                {"path": "00_Meetings/n2.md", "wikilink_title": "n2",
                 "snippet": "s", "score": 3.0}]
        inject_indexer(s, rows, {})
        s._search("발화")
        scores = [n["rank_score"] for n in s.collected_notes()]
        assert len(scores) == len(set(scores)), "동점이 생기면 tie-break 재검토 필요"
        assert scores == sorted(scores, reverse=True)
        # rank 는 0-기반 순위이고 rank_score 는 그 변환 — 둘이 일관해야 한다
        for n in s.collected_notes():
            assert n["rank_score"] == round(1.0 / (vi.RRF_K + n["rank"] + 1), 6)
        assert max(scores) <= rs.RANK_SCORE_TOP

    def test_paper_dir_search_matches_mid_path_folders(self, monkeypatch):
        """논문 arm 은 볼트 하위에 묻힌 폴더도 찾는다(path_match="segment").

        실제 볼트에서 `02_이론_학습`(74노트)·`원문추출`(9노트)은 루트가 아니라
        `Archive/도메인_아카이브/` 아래에 있다. 접두사 매칭만 하던
        과거엔 이 arm 이 그 노트들을 영구히 0건으로 돌려줬다.
        """
        s = make_searcher(monkeypatch,
                          extra_cfg={"wiki.realtime_paper_dirs": ["02_이론_학습"],
                                     "wiki.realtime_paper_candidates": 4,
                                     "wiki.realtime_note_candidates": 1})
        묻힌논문 = {"path": "Archive/QC_통합아카이브/02_이론_학습/QAOA.md",
                    "wikilink_title": "QAOA", "snippet": "s", "score": 0.5}
        일반 = {"path": "00_Meetings/주간회의.md", "wikilink_title": "주간회의",
                "snippet": "s", "score": 9.0}
        idx = inject_indexer(s, [일반, 묻힌논문], {})
        s._search("발화")
        # 노트 검색 1회 + 논문 폴더 한정 1회, 후자는 segment 매칭을 요구한다
        assert len(idx.queries) == 2
        assert idx.queries[0][1] == () and idx.queries[0][2] == "prefix"
        assert idx.queries[1][1] == ("02_이론_학습",)
        assert idx.queries[1][2] == "segment"
        # 노트 상한(1) 밖이었지만 보강 arm 으로 후보에 들어오고, 배지도 논문이다
        titles = [n["title"] for n in s.collected_notes()]
        assert titles == ["주간회의", "QAOA"]
        assert [n["source_type"] for n in s.collected_notes()] == ["note", "paper"]

    def test_paper_stage_guarantees_pool_entry(self, monkeypatch):
        """일반 검색 상한에 밀린 논문 노트도 보강 arm 으로 후보에 들어온다."""
        s = make_searcher(monkeypatch,
                          extra_cfg={"wiki.realtime_paper_dirs": ["01_References"],
                                     "wiki.realtime_note_candidates": 2})
        rows = [{"path": f"00_Meetings/n{i}.md", "wikilink_title": f"n{i}",
                 "snippet": "s", "score": 9 - i} for i in range(3)]
        rows.append({**INDEX_HIT})    # 01_References/양자컴퓨팅.md — 상한 밖 4번째
        inject_indexer(s, rows, {})
        s._search("발화")
        titles = [n["title"] for n in s.collected_notes()]
        assert titles[:2] == ["n0", "n1"]      # 일반 상한 2건
        assert "양자컴퓨팅" in titles           # 논문은 보강으로 진입

    def test_arm_rank_is_per_arm_while_rank_stays_combined(self, monkeypatch):
        """순위 컷이 논문 arm 을 전멸시키지 않게 하는 필드(회의록 max_rank 전용).

        rank 는 arm ②(논문 폴더 한정)를 arm ① 뒤에 이어 붙인 **통합 순번**이라 논문
        arm 의 1위가 노트 후보 수만큼 밀린 값을 갖는다. 그것으로 노이즈 컷을 하면
        wiki.related_notes_max_rank 를 1~10 중 무엇으로 둬도 논문 arm 만이 찾은 노트가
        100% 탈락했다. arm_rank 는 자기 arm 안에서의 순위여서 arm 간 비교가 된다.

        정렬·표시는 계속 rank_score(=통합 순번 기반)를 쓴다 — arm_rank 로 정렬하면
        실측에서 반박된 '논문 폴더 우대'와 같아진다."""
        s = make_searcher(monkeypatch,
                          extra_cfg={"wiki.realtime_paper_dirs": ["01_References"],
                                     "wiki.realtime_note_candidates": 2})
        rows = [{"path": f"00_Meetings/n{i}.md", "wikilink_title": f"n{i}",
                 "snippet": "s", "score": 9 - i} for i in range(2)]
        rows.append({**INDEX_HIT})     # 01_References — 노트 상한 밖, 논문 arm 으로 진입
        inject_indexer(s, rows, {})
        s._search("발화")

        notes = s.collected_notes()
        by_title = {n["title"]: n for n in notes}
        assert by_title["n0"]["rank"] == 0 and by_title["n0"]["arm_rank"] == 0

        논문 = by_title["양자컴퓨팅"]
        assert 논문["source_type"] == "paper"
        assert 논문["rank"] == 2          # 통합 순번은 노트 후보 수만큼 밀려 있다
        assert 논문["arm_rank"] == 0      # 자기 arm 에서는 1위

        # 표시 순서는 그대로 — 논문을 끌어올리지 않는다
        assert [n["title"] for n in notes] == ["n0", "n1", "양자컴퓨팅"]

    def test_candidate_pool_wider_than_display(self, monkeypatch):
        shown = []
        s = make_searcher(monkeypatch, on_notes=shown.append,
                          extra_cfg={"wiki.realtime_note_candidates": 12,
                                     "wiki.realtime_display_count": 3})
        rows = [{"path": f"00_Meetings/n{i}.md", "wikilink_title": f"n{i}",
                 "snippet": "s", "score": 10 - i} for i in range(12)]
        inject_indexer(s, rows, {})
        s._search("발화")
        assert len(shown[0]) == 3            # 표시는 상위 3
        assert len(s.collected_notes()) == 12  # 후보는 넉넉히 누적

    def test_query_uses_more_than_60_chars(self, monkeypatch):
        s = make_searcher(monkeypatch, extra_cfg={"wiki.realtime_query_chars": 180})
        idx = inject_indexer(s, [INDEX_HIT])
        long_text = "가" * 200
        s._search(long_text)
        assert len(idx.queries[0][0]) == 180

    def test_same_title_deduped_for_display_but_kept_in_pool(self, monkeypatch):
        """같은 제목의 노트가 여러 폴더에 있으면 **칩은 1건**, **누적은 전량**.

        표시는 `[[제목]]` 위키링크라 두 건이 구분되지 않으므로 하나만 보여준다.
        하지만 서로 다른 노트이므로(같은 제목의 다른 회의록이 실제로 존재한다)
        누적 검토·사이드카에서는 경로로 구분해 남긴다 — 과거엔 검색 단계에서
        걸러 누적분까지 함께 사라졌다.
        """
        shown = []
        s = make_searcher(monkeypatch, on_notes=shown.append)
        rows = [
            {"path": "원문추출/논문A.md", "wikilink_title": "논문A",
             "snippet": "s1", "score": 5.0},
            {"path": "01_References/논문A.md", "wikilink_title": "논문A",
             "snippet": "s2", "score": 4.0},
            {"path": "00_Meetings/다른노트.md", "wikilink_title": "다른노트",
             "snippet": "s3", "score": 1.0},
        ]
        inject_indexer(s, rows, {})
        s._search("발화")
        # 표시: 제목 중복 제거 → 상위 1건만
        assert [n["title"] for n in shown[0]] == ["논문A", "다른노트"]
        assert shown[0][0]["filename"] == "원문추출/논문A.md"
        # 누적: 3건 전부 (경로로 구분)
        notes = s.collected_notes()
        assert [n["filename"] for n in notes] == [
            "원문추출/논문A.md", "01_References/논문A.md", "00_Meetings/다른노트.md"]
        # 근거 누적도 경로 단위 — 같은 제목 두 노트가 각각 남는다
        assert len(s.collected_evidence()) == 3

    def test_sections_in_notes_failure_keeps_results(self, monkeypatch):
        """섹션 인덱스가 없는(구버전) 인덱서여도 결과는 살아남는다(heading 만 빈다)."""
        s = make_searcher(monkeypatch)
        idx = inject_indexer(s, [INDEX_HIT], {})

        def boom(*a, **kw):
            raise RuntimeError("sections_in_notes 없음")
        idx.sections_in_notes = boom
        s._search("발화")
        assert len(s.collected_notes()) == 1
        assert s.collected_notes()[0]["found_by"] == "note"


class TestEvidence:
    """FR-4 — 근거 누적 스냅샷."""

    def test_evidence_dedupes_and_counts_hits(self, monkeypatch):
        s = make_searcher(monkeypatch)
        inject_indexer(s, [INDEX_HIT])
        s._search("첫 발화")
        s._search("둘째 발화")
        ev = s.collected_evidence()
        assert len(ev) == 1
        assert ev[0]["hits"] == 2
        assert ev[0]["title"] == "양자컴퓨팅"
        assert ev[0]["segment_text"] in ("첫 발화", "둘째 발화")
        assert ev[0]["elapsed_sec"] >= 0

    def test_evidence_sorted_by_rank_score(self, monkeypatch):
        s = make_searcher(monkeypatch)
        rows = [
            {"path": "a.md", "wikilink_title": "A", "snippet": "s", "score": 5.0},
            {"path": "b.md", "wikilink_title": "B", "snippet": "s", "score": 1.0},
        ]
        inject_indexer(s, rows, {})
        s._search("발화")
        ev = s.collected_evidence()
        assert [e["title"] for e in ev] == ["A", "B"]
        assert ev[0]["rank_score"] >= ev[1]["rank_score"]

    def test_evidence_limit(self, monkeypatch):
        s = make_searcher(monkeypatch)
        rows = [{"path": f"{i}.md", "wikilink_title": f"n{i}", "snippet": "s",
                 "score": 10 - i} for i in range(8)]
        inject_indexer(s, rows, {})
        s._search("발화")
        assert len(s.collected_evidence(limit=3)) == 3


class TestStatus:
    """FR-1 — 비활성 사유 노출."""

    def test_gate_off_status_reason(self, monkeypatch):
        reported = []
        s = make_searcher(monkeypatch, gate=False, on_status=reported.append)
        st = s.status()
        assert st["gate"] is False and st["enabled"] is False
        assert st["reason"] == "off"
        assert "꺼져" in st["reasonText"]
        s.warmup()   # 풀이 없으므로 즉시 보고
        assert reported and reported[0]["reason"] == "off"

    def test_no_vault_reason(self, monkeypatch):
        reported = []
        s = make_searcher(monkeypatch, backend="index", on_status=reported.append)
        from meeting_minutes_app.wiki_core import vault_indexer as vi
        monkeypatch.setattr(vi.VaultIndexer, "from_config",
                            classmethod(lambda cls: None))
        s._search("발화")
        st = s.status()
        assert st["reason"] == "no_vault"
        assert st["enabled"] is False
        assert reported and reported[0]["reason"] == "no_vault"
        s.shutdown()

    def test_index_missing_reason(self, monkeypatch):
        s = make_searcher(monkeypatch, backend="index")
        from meeting_minutes_app.wiki_core import vault_indexer as vi

        class Dead:
            def load(self):
                return False
        monkeypatch.setattr(vi.VaultIndexer, "from_config",
                            classmethod(lambda cls: Dead()))
        s._search("발화")
        assert s.status()["reason"] == "index_missing"
        s.shutdown()

    def test_connected_status_has_no_reason(self, monkeypatch):
        reported = []
        s = make_searcher(monkeypatch, on_status=reported.append)
        inject_indexer(s, [INDEX_HIT])
        s._search("발화")
        st = s.status()
        assert st["enabled"] is True and st["reason"] == ""
        assert st["backend"] == "index"
        assert reported and reported[0]["backend"] == "index"

    def test_status_reported_once(self, monkeypatch):
        reported = []
        s = make_searcher(monkeypatch, on_status=reported.append)
        inject_indexer(s, [INDEX_HIT])
        s._search("발화1")
        s._search("발화2")
        assert len(reported) == 1

    def test_on_status_exception_swallowed(self, monkeypatch):
        def boom(st):
            raise RuntimeError("배지 표시 실패")
        s = make_searcher(monkeypatch, on_status=boom)
        inject_indexer(s, [INDEX_HIT])
        s._search("발화")   # 예외 전파 없어야 함
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
            """sections_in_notes 가 없는 구버전 인덱서 — 후보는 그대로 살아야 한다."""

            def search(self, query, limit=5, path_prefixes=None, path_match="prefix"):
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


class TestSearchNow:
    """개입 카드용 동기 검색 — offer_segment 와 **다른 계약**을 지키는지.

    같은 랭킹 함수를 쓰되(중복 금지), 스로틀·내용 게이트·표시 콜백·누적은 지나지
    않는다. 누적에 섞이면 개입이 화면의 관련 노트 바를 흔든다.
    """

    def test_returns_hits_without_accumulating_or_displaying(self, monkeypatch):
        shown = []
        s = make_searcher(monkeypatch, interval=5, on_notes=shown.append)
        inject_indexer(s, [INDEX_HIT], {})
        hits = s.search_now("큐비트 결맞음 시간이 100마이크로초라고 했는데요")
        assert [h["title"] for h in hits] == ["양자컴퓨팅"]
        assert s.collected_notes() == []      # 누적 오염 없음
        assert shown == []                    # 화면 콜백도 안 부른다
        s.shutdown()

    def test_ignores_throttle_and_content_gate(self, monkeypatch):
        """스로틀 5 여도 매번 검색하고, min_terms 게이트도 지나가지 않는다."""
        s = make_searcher(monkeypatch, interval=5,
                          extra_cfg={"wiki.realtime_min_terms": 99})
        idx = inject_indexer(s, [INDEX_HIT], {})
        idx.known_term_count = lambda t: 0        # 게이트라면 전부 막힐 값
        for _ in range(3):
            assert s.search_now("발화") != []
        note_arm = [q for q in idx.queries if not q[1]]   # 논문 arm 은 별도 호출이다
        assert len(note_arm) == 3
        s.shutdown()

    def test_uses_same_ranking_path_as_offer_segment(self, monkeypatch):
        """논문 보강 arm·섹션 특정까지 동일 — 규칙을 복제하지 않았다는 확인."""
        s = make_searcher(monkeypatch, extra_cfg={
            "wiki.realtime_paper_dirs": ["02_이론_학습"]})
        paper = dict(INDEX_HIT, path="Archive/x/02_이론_학습/논문.md",
                     title="논문", wikilink_title="논문")
        idx = inject_indexer(s, [NOTE_HIT_MEETING, paper],
                             {"00_Meetings/주간회의.md": SECTION_OF_MEETING})
        hits = s.search_now("큐비트 로드맵")
        assert [h["source_type"] for h in hits] == ["note", "paper"]
        assert hits[0]["heading"] == "큐비트 로드맵"     # 섹션 특정도 그대로
        assert any(q[1] for q in idx.queries)            # 논문 arm 이 실제로 돌았다
        s.shutdown()

    def test_disabled_or_empty_returns_empty(self, monkeypatch):
        s = make_searcher(monkeypatch, gate=False)
        assert s.search_now("아무 발화") == []
        s2 = make_searcher(monkeypatch)
        inject_indexer(s2, [INDEX_HIT], {})
        assert s2.search_now("   ") == []
        s2.shutdown()

    def test_backend_failure_is_swallowed(self, monkeypatch):
        """검색 실패가 개입 생성 스레드로 예외를 던지면 안 된다."""
        s = make_searcher(monkeypatch)
        idx = inject_indexer(s, [], {})
        def boom(*a, **k):
            raise RuntimeError("index broken")
        idx.search = boom
        assert s.search_now("발화") == []
        s.shutdown()

    def test_lazy_init_is_serialized(self, monkeypatch):
        """두 스레드가 동시에 들어와도 '아직 안 붙은 백엔드'로 통과하지 않는다.

        `_init_done` 을 초기화 **앞에서** 세우면 뒤에 온 스레드가 인덱서 없이
        빠져나가 조용히 0건을 돌려준다 — 락을 넣은 이유 자체다."""
        s = make_searcher(monkeypatch)
        seen = []
        real = FakeIndexer([INDEX_HIT], {})

        class SlowIndexer:
            @staticmethod
            def from_config():
                time.sleep(0.05)
                return real

        real.load = lambda: True
        monkeypatch.setattr(
            "meeting_minutes_app.wiki_core.vault_indexer.VaultIndexer", SlowIndexer)

        def worker():
            s._lazy_init()
            seen.append(s._indexer)

        ts = [threading.Thread(target=worker) for _ in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        assert all(x is real for x in seen), "초기화 도중 통과한 스레드가 있다"
        s.shutdown()
