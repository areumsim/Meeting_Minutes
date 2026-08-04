# -*- coding: utf-8 -*-
"""실시간 관련 노트 — 웹 백엔드 배선 회귀 테스트 (네트워크 없이 실행).

방지하려는 재발 버그:
  1) 백엔드가 안 붙어도 조용히 no-op → 사용자는 "기능이 사라진" 줄 안다.
     → related_notes 이벤트에 status(사유)를 실어 배지로 표시한다.
  2) 근거(섹션경로·점수·snippet·출처유형) 없이 제목 칩만 전달 → 왜 떴는지 추적 불가.
  3) 웹 보완 검색이 내부자료를 찾은 구간에서도 매번 호출돼 비용·지연 발생.
     → wiki.realtime_web_only_if_no_vault_hit(기본 true) 로 내부 우선.
  4) HTTP 청크 경로(기본 모드)에 웹 보완 트리거가 아예 없어 설정을 켜도 무반응.

실행:
    python -m pytest tests/test_realtime_related_notes.py -q
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from web.backend.api import realtime as rt  # noqa: E402


@pytest.fixture(autouse=True)
def isolate_usage_db(tmp_path, monkeypatch):
    """과금 기록을 임시 DB 로 격리 — **사용자 실제 DB 를 오염시키지 않는다.**

    웹 검색 보완이 spend_guard.record() 를 지나게 된 뒤(회의 중 외부 유료 검색이
    무계량이던 결함 수정), 이 파일의 테스트가 개발 PC 의 web/meeting_assistant.db 에
    실제 과금 행을 남겼다 — 월 합계가 부풀면 **다른 경로의 한도 판정까지** 왜곡된다.
    autouse 로 걸어 새 테스트가 이 격리를 잊어도 안전하게 한다."""
    from meeting_minutes_app.common import usage_log
    monkeypatch.setattr(usage_log, "_resolve_db_path",
                        lambda p=None: tmp_path / "usage.db")


def make_session(monkeypatch):
    """WS/DB를 건드리지 않는 최소 세션 인스턴스 + 전송 캡처."""
    s = rt.BrowserRealtimeSession(ws=MagicMock(), config={})
    sent = []
    monkeypatch.setattr(s, "_send_to_browser", lambda payload: sent.append(payload))
    return s, sent


class _FakeSearcher:
    def __init__(self, notes=None, enabled=True, searchable=True):
        self._notes = list(notes or [])
        self.enabled = enabled
        #: 웹 검색도 vault 검색과 **같은 내용 문턱**을 쓴다 — 인사말·군더더기로 웹 API를
        #: 쏘지 않게. 실제 판정은 RealtimeVaultSearcher.has_searchable_content().
        self.searchable = searchable

    def collected_notes(self):
        return list(self._notes)

    def has_searchable_content(self, text):
        return self.searchable

    def add(self, n=1):
        self._notes.extend([{"title": f"n{i}"} for i in range(n)])


HIT = {
    "filename": "02_이론_학습/QAOA.md",
    "title": "QAOA",
    "score": 1.5,
    "rank_score": 0.02,
    "snippet": "QAOA 개요",
    "heading": "요약",
    "section_path": "QAOA › 요약",
    "source_type": "paper",
    "found_by": "section",
    "segment_text": "QAOA 관련 발화",
}


class TestRelatedNotesPayload:
    def test_evidence_fields_forwarded(self, monkeypatch):
        s, sent = make_session(monkeypatch)
        s._emit_related_notes([HIT])
        assert len(sent) == 1
        n = sent[0]["notes"][0]
        assert n["title"] == "QAOA"
        assert n["sectionPath"] == "QAOA › 요약"
        assert n["heading"] == "요약"
        assert n["sourceType"] == "paper"
        assert n["foundBy"] == "section"
        assert n["snippet"] == "QAOA 개요"
        assert n["segmentText"] == "QAOA 관련 발화"
        assert n["score"] == 1.5

    def test_emit_never_raises(self, monkeypatch):
        s, _ = make_session(monkeypatch)

        def boom(payload):
            raise RuntimeError("소켓 끊김")
        monkeypatch.setattr(s, "_send_to_browser", boom)
        s._emit_related_notes([HIT])          # 실시간 스트림 보호 — 예외 전파 금지
        s._emit_search_status({"enabled": False, "reason": "off"})


class TestStatusBadge:
    def test_status_event_carries_reason(self, monkeypatch):
        s, sent = make_session(monkeypatch)
        s._emit_search_status({
            "enabled": False, "gate": True, "backend": "",
            "reason": "index_missing", "reasonText": "검색 인덱스가 없습니다",
        })
        assert sent[0]["type"] == "related_notes"
        assert sent[0]["notes"] == []          # 기존 목록을 지우지 않는다
        assert sent[0]["status"]["reason"] == "index_missing"
        assert "인덱스" in sent[0]["status"]["reasonText"]

    def test_searcher_wired_with_status_and_warmup(self, monkeypatch):
        """세션이 status 콜백을 연결하고 warmup 으로 상태를 미리 확인하는지."""
        from meeting_minutes_app.wiki_core import realtime_search as rs
        created = {}

        class Spy(rs.RealtimeVaultSearcher):
            def __init__(self, **kw):
                created.update(kw)
                created["warmed"] = False
                super().__init__(**kw)

            def warmup(self):
                created["warmed"] = True

        monkeypatch.setattr(rs, "RealtimeVaultSearcher", Spy)
        s, _ = make_session(monkeypatch)
        searcher = s._create_searcher("주제")
        assert created["topic"] == "주제"
        assert created["on_notes"] == s._emit_related_notes
        assert created["on_status"] == s._emit_search_status
        assert created["warmed"] is True
        searcher.shutdown()

    def test_searcher_creation_failure_does_not_block_recording(self, monkeypatch):
        from meeting_minutes_app.wiki_core import realtime_search as rs

        def boom(**kw):
            raise RuntimeError("인덱스 폭발")
        monkeypatch.setattr(rs, "RealtimeVaultSearcher", boom)
        s, _ = make_session(monkeypatch)
        assert s._create_searcher("주제") is None


class TestWebIsSupplement:
    def _cfg(self, monkeypatch, **over):
        cfg = {
            "wiki.online_search_enabled": True,
            "wiki.realtime_web_search_interval": 1,
            "wiki.realtime_web_only_if_no_vault_hit": True,
        }
        cfg.update(over)
        import meeting_minutes_app.common.config_loader as cl
        monkeypatch.setattr(cl, "get", lambda k, d=None: cfg.get(k, d))

    def test_skipped_when_vault_found_notes(self, monkeypatch):
        self._cfg(monkeypatch)
        s, _ = make_session(monkeypatch)
        submitted = []
        monkeypatch.setattr(s._web_pool, "submit",
                            lambda fn, *a: submitted.append(a))
        searcher = _FakeSearcher()
        searcher.add(3)                 # 내부에서 후보 3건 회수됨
        s._searcher = searcher
        s._segment_counter = 1
        s._maybe_web_research("발화")
        assert submitted == []           # 웹 호출 생략
        assert s._internal_seen_count == 3

    def test_runs_when_vault_found_nothing(self, monkeypatch):
        self._cfg(monkeypatch)
        s, _ = make_session(monkeypatch)
        submitted = []
        monkeypatch.setattr(s._web_pool, "submit",
                            lambda fn, *a: submitted.append(a))
        s._searcher = _FakeSearcher()    # 후보 0건
        s._segment_counter = 1
        s._maybe_web_research("발화")
        assert submitted == [("발화",)]

    def test_always_when_policy_disabled(self, monkeypatch):
        self._cfg(monkeypatch, **{"wiki.realtime_web_only_if_no_vault_hit": False})
        s, _ = make_session(monkeypatch)
        submitted = []
        monkeypatch.setattr(s._web_pool, "submit",
                            lambda fn, *a: submitted.append(a))
        searcher = _FakeSearcher()
        searcher.add(5)
        s._searcher = searcher
        s._segment_counter = 1
        s._maybe_web_research("발화")
        assert submitted == [("발화",)]

    def test_gate_off_never_calls_web(self, monkeypatch):
        self._cfg(monkeypatch, **{"wiki.online_search_enabled": False})
        s, _ = make_session(monkeypatch)
        submitted = []
        monkeypatch.setattr(s._web_pool, "submit",
                            lambda fn, *a: submitted.append(a))
        s._searcher = _FakeSearcher()
        s._segment_counter = 1
        s._maybe_web_research("발화")
        assert submitted == []

    def test_interval_throttles(self, monkeypatch):
        self._cfg(monkeypatch, **{"wiki.realtime_web_search_interval": 3})
        s, _ = make_session(monkeypatch)
        submitted = []
        monkeypatch.setattr(s._web_pool, "submit",
                            lambda fn, *a: submitted.append(a))
        s._searcher = _FakeSearcher()
        for i in range(1, 7):
            s._segment_counter = i
            s._maybe_web_research(f"발화{i}")
        assert len(submitted) == 2       # 3, 6번째만

class TestWebResearchIsMetered:
    """회의 중 웹 검색은 외부 유료 호출인데 계량이 아예 없었다(PRD §10 — realtime.py 에
    spend_guard 참조 0건). 안 보이는 지출은 다른 경로의 한도 판정까지 왜곡한다."""

    def _fake_llm(self, monkeypatch, searched=True):
        fake = MagicMock()
        fake.web_research.return_value = {
            "text": "외부 자료 요약", "sources": ["https://example.com/a"],
            "searched": searched}
        from meeting_minutes_app.meeting_pipeline import meeting_minutes as mm
        monkeypatch.setattr(mm, "LLMClient", lambda **kw: fake)
        return fake

    def test_call_is_recorded_under_its_own_kind(self, monkeypatch):
        from meeting_minutes_app.common import spend_guard, usage_log
        import meeting_minutes_app.common.config_loader as cl
        monkeypatch.setattr(cl, "get", lambda k, d=None: d)
        monkeypatch.setattr(spend_guard, "_c", lambda k, d=None: d)
        s, _ = make_session(monkeypatch)
        self._fake_llm(monkeypatch)
        s._web_research_segment("발화")
        by_kind = usage_log.month_to_date_by_kind()
        assert by_kind.get(spend_guard.KIND_WEB_RESEARCH, 0.0) > 0.0
        assert usage_log.month_to_date_spend() > 0.0

    def test_degraded_call_costs_less_than_live_search(self, monkeypatch):
        """searched=False(라이브 검색 실패 후 모델 지식 폴백)에 검색 요금을 물리지 않는다."""
        from meeting_minutes_app.common import pricing, spend_guard, usage_log
        import meeting_minutes_app.common.config_loader as cl
        monkeypatch.setattr(cl, "get", lambda k, d=None: d)
        monkeypatch.setattr(spend_guard, "_c", lambda k, d=None: d)
        s, _ = make_session(monkeypatch)
        self._fake_llm(monkeypatch, searched=False)
        s._web_research_segment("발화")
        spent = usage_log.month_to_date_by_kind()[spend_guard.KIND_WEB_RESEARCH]
        # 강등된 회차는 최종 폴백 chat() 경로 = models.llm(기본 gpt) 기준.
        assert spent == pytest.approx(
            pricing.web_research_call_cost(None, searched=False, llm="gpt"), abs=1e-9)
        # 라이브 검색 회차(1순위 Anthropic + 검색 요금)보다 싸다.
        assert spent < pricing.web_research_call_cost(None, searched=True,
                                                     llm="claude")

    def test_spend_cap_blocks_the_call_with_a_reason(self, monkeypatch, capsys):
        from meeting_minutes_app.common import spend_guard, usage_log
        import meeting_minutes_app.common.config_loader as cl
        monkeypatch.setattr(cl, "get", lambda k, d=None: d)
        monkeypatch.setattr(spend_guard, "blocked",
                            lambda est, **kw: "월 한도 초과(테스트)")
        s, _ = make_session(monkeypatch)
        fake = self._fake_llm(monkeypatch)
        s._web_research_segment("발화")
        assert fake.web_research.call_count == 0        # 호출 자체가 없다
        assert usage_log.month_to_date_spend() == 0.0
        # 조용히 건너뛰지 않는다 — 사유를 남긴다
        assert "월 한도" in capsys.readouterr().out

    def test_automation_pause_stops_in_meeting_web_search(self, monkeypatch):
        from meeting_minutes_app.common import spend_guard, usage_log
        import meeting_minutes_app.common.config_loader as cl
        monkeypatch.setattr(cl, "get", lambda k, d=None: d)
        monkeypatch.setattr(spend_guard, "automation_paused", lambda: True)
        s, _ = make_session(monkeypatch)
        fake = self._fake_llm(monkeypatch)
        s._web_research_segment("발화")
        assert fake.web_research.call_count == 0
        assert usage_log.month_to_date_spend() == 0.0

    def test_skip_reason_is_logged_once_not_per_segment(self, monkeypatch, capsys):
        """웹 검색은 interval 마다 돈다 — 같은 사유를 매번 찍으면 로그가 도배된다."""
        from meeting_minutes_app.common import spend_guard
        import meeting_minutes_app.common.config_loader as cl
        monkeypatch.setattr(cl, "get", lambda k, d=None: d)
        monkeypatch.setattr(spend_guard, "automation_paused", lambda: True)
        s, _ = make_session(monkeypatch)
        self._fake_llm(monkeypatch)
        for _ in range(5):
            s._web_research_segment("발화")
        assert capsys.readouterr().out.count("일시정지") == 1

    def test_web_finding_emitted_as_web_source(self, monkeypatch):
        """웹 결과도 같은 바에 표시되지만 출처유형이 web(🌐) 이어야 한다."""
        s, sent = make_session(monkeypatch)
        import meeting_minutes_app.common.config_loader as cl
        monkeypatch.setattr(cl, "get", lambda k, d=None: d)
        fake_llm = MagicMock()
        fake_llm.web_research.return_value = {
            "text": "외부 자료 요약", "sources": ["https://example.com/a"]}
        from meeting_minutes_app.meeting_pipeline import meeting_minutes as mm
        monkeypatch.setattr(mm, "LLMClient", lambda **kw: fake_llm)
        s._web_research_segment("발화")
        assert s._web_findings and s._web_findings[0]["result"] == "외부 자료 요약"
        note = sent[0]["notes"][0]
        assert note["sourceType"] == "web"
        assert note["snippet"] == "외부 자료 요약"
