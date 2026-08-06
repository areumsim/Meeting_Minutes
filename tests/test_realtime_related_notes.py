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


class TestCancelMakesNoNewCharge:
    """'저장하지 않고 취소' 가 **실제로** 아무것도 만들지 않는가(동작 확인).

    배선만 보는 소스 스캔과 달리 여기서는 `_cancel_session()` 을 돌려 결과를 본다.
    이 경로가 WS 수신 루프에 없던 동안, 실시간 모드의 [취소]는 '연결 끊김'으로 보여
    **정상 종료** 경로로 갔다 — 마지막 정리(LLM 1회)와 회의록 생성이 그대로 돌았다.
    """

    def _session(self, monkeypatch, tmp_path):
        s, sent = make_session(monkeypatch)
        s.session_id = "cx1"
        s.ws.send_json = _AsyncNoop()
        deleted = []
        monkeypatch.setattr(rt.db, "delete_session", lambda sid: deleted.append(sid))
        monkeypatch.setattr(rt.db, "DB_PATH", tmp_path / "web.db")
        return s, sent, deleted

    def test_no_final_brief_and_no_minutes(self, monkeypatch, tmp_path):
        """마지막 정리(LLM)·회의록 생성을 부르지 않고, 세션은 버린다."""
        import asyncio
        s, _sent, deleted = self._session(monkeypatch, tmp_path)
        calls = []
        fac = MagicMock()
        fac.finalize_brief.side_effect = lambda *a, **k: calls.append("brief") or ""
        s._facilitator = fac
        monkeypatch.setattr(s, "_finalize",
                            lambda *a, **k: calls.append("finalize"))

        asyncio.run(s._cancel_session())

        assert calls == []                 # 새 과금이 될 호출이 하나도 없다
        assert deleted == ["cx1"]          # 세션은 버려진다
        assert s.session_id is None
        fac.shutdown.assert_called_once_with(wait=False)   # 기다리지 않는다(즉시 종료)

    def test_observations_are_deleted(self, monkeypatch, tmp_path):
        """관찰 로그(발화 인용 ≤500자)도 지운다 — 저장하지 않기로 한 회의다."""
        import asyncio
        from meeting_minutes_app.wiki_core import facilitation
        db_path = tmp_path / "web.db"
        facilitation.record_observation(
            "cx1", "critic", trigger_type="fact", confidence=0.9,
            span="회의에서 실제로 나온 발화 인용", level=1, db_path=db_path)
        assert len(facilitation.observations("cx1", db_path=db_path)) == 1

        s, _sent, _deleted = self._session(monkeypatch, tmp_path)
        asyncio.run(s._cancel_session())

        assert facilitation.observations("cx1", db_path=db_path) == []

    def test_tells_the_browser_it_was_cancelled(self, monkeypatch, tmp_path):
        """조용히 끝내지 않는다 — 화면이 '저장하지 않고 종료'를 알아야 한다."""
        import asyncio
        s, _sent, _deleted = self._session(monkeypatch, tmp_path)
        asyncio.run(s._cancel_session())
        assert s.ws.send_json.payloads[-1]["type"] == "cancelled"


class _AsyncNoop:
    """await 가능한 더미 — MagicMock 은 코루틴이 아니라 await 에서 터진다."""

    def __init__(self):
        self.payloads = []

    async def __call__(self, payload):
        self.payloads.append(payload)


class TestBrowserTransport:
    """워커 스레드가 만든 산출물이 **회의 중에** 브라우저에 닿는가.

    회귀(2026-08-06 실사용 신고 "관련 노트가 준비중에서 멈춘다"): 관련 노트·페르소나
    카드·finalize 진행 상태는 모두 워커 스레드에서 `_send_to_browser()` 로 나가는데,
    그 브릿지의 두 부품이 **WS 경로 안에만** 있었다.
      1) 목적지 루프(`self._loop`)를 `_run_ws_realtime` 에서 세웠다 → 기본 설정
         (`realtime.mode="http"`)에서는 None 이라 모든 이벤트가 조용히 버려졌다.
      2) 큐 소비자(`_send_queue` → ws)를 WS 경로에서만 띄웠다 → 루프를 세워도 큐에
         쌓인 채 회의가 끝날 때까지 나가지 않았다.
    두 결함 모두 검색기 자체는 정상이라 로그·테스트에 아무 흔적이 없었고, 기존
    테스트는 `_send_to_browser` 를 monkeypatch 해서 전송 자체를 검증하지 않았다.
    """

    def test_loop_is_wired_before_transport_is_chosen(self, monkeypatch):
        """`run()` 이 전송 경로(WS/HTTP)를 고르기 **전에** 루프를 세운다.

        검색기 warmup 은 생성 직후 워커 스레드에서 상태를 보내므로, 루프가 그보다
        늦게 세워지면 첫 상태 배지가 사라진다(화면은 "대기 중…"에 머문다)."""
        import asyncio
        import threading
        from meeting_minutes_app.common import config_loader as cfg

        monkeypatch.setattr(cfg, "get", lambda k, d=None: {
            "api.openai_api_key": "sk-test", "realtime.mode": "http"}.get(k, d))
        monkeypatch.setattr(rt.db, "create_session", lambda **kw: "sid-http")

        s = rt.BrowserRealtimeSession(MagicMock(), {})
        s.ws.send_json = _AsyncNoop()
        monkeypatch.setattr(s, "_create_facilitator", lambda *a, **k: None)
        monkeypatch.setattr(s, "_announce_facilitation", lambda: None)

        seen = {}

        def _searcher(topic):
            # 실제 RealtimeVaultSearcher.warmup() 과 같은 타이밍 — 생성 직후,
            # 전송 경로가 정해지기 전에 워커 스레드에서 상태를 보낸다.
            seen["loop_at_warmup"] = s._loop
            t = threading.Thread(target=lambda: s._emit_search_status(
                {"enabled": True, "gate": True, "backend": "index"}))
            t.start()
            t.join()
            return None

        monkeypatch.setattr(s, "_create_searcher", _searcher)

        async def _fake_http(*a, **k):
            # call_soon_threadsafe 는 예약만 한다 — 루프에 한 번 양보해야 큐에 들어온다
            await asyncio.sleep(0)
            seen["queued"] = s._send_queue.qsize()

        monkeypatch.setattr(s, "_run_http_fallback", _fake_http)
        asyncio.run(s.run())

        assert seen["loop_at_warmup"] is not None      # 버려지지 않았다
        assert seen["queued"] == 1                     # 큐에 실제로 들어왔다

    def test_http_path_flushes_queue_during_recording(self, monkeypatch):
        """HTTP 청크 경로도 녹음 중 큐 소비자를 띄운다(WS 경로와 같은 계약).

        finalize 가 자기 소비자를 띄우기 **전에** 나가야 한다 — 회의 중에 보여야
        의미가 있는 산출물이다."""
        import asyncio
        import json as _json
        import threading
        from unittest.mock import AsyncMock

        cfg = MagicMock()
        cfg.get.side_effect = lambda k, d=None: d          # 전부 기본값
        ws = MagicMock()
        ws.send_json = _AsyncNoop()
        # 첫 수신에서 즉시 정지 → 오디오·STT 없이 경로의 배선만 통과시킨다
        ws.receive = AsyncMock(return_value={"text": _json.dumps({"type": "stop"})})

        s = rt.BrowserRealtimeSession(ws, {})
        s.session_id = None
        finalized = []
        monkeypatch.setattr(s, "_finalize",
                            lambda *a, **k: _done(finalized))

        async def _drive():
            s._loop = asyncio.get_running_loop()          # run() 이 하는 일
            task = asyncio.create_task(s._run_http_fallback(
                MagicMock(), "ko", False, "gpt-4o-mini",
                "meeting", "", "", "", cfg))
            # 워커 스레드(검색 풀)가 관련 노트를 보내는 순간을 재현
            await asyncio.sleep(0)
            t = threading.Thread(target=lambda: s._emit_related_notes([HIT]))
            t.start()
            t.join()
            await asyncio.wait_for(task, timeout=10)

        asyncio.run(_drive())

        types = [p.get("type") for p in ws.send_json.payloads]
        assert "related_notes" in types, types      # 회의 중에 실제로 나갔다
        assert finalized == ["ok"]                  # 종료 경로도 그대로 지난다


def _done(bucket):
    """monkeypatch 한 _finalize 대체 — await 가능해야 한다."""
    async def _noop():
        bucket.append("ok")
    return _noop()
