"""회의 진행 페르소나(M0 관찰모드) 회귀 테스트.

PRD(docs/prd/PRD_회의진행_페르소나에이전트_20260803.md) §14 수용 기준 중 M0 몫을
고정한다. 이 리포에서 반복적으로 깨진 지점(§10 — 실시간·자동 경로가 비용 계량을
통째로 우회)이 이 기능의 최대 리스크라, 다음 네 가지를 회귀로 못박는다:

  1. 기본값(facilitation.enabled=false)에서 LLM 호출이 **한 번도** 없다.
  2. 참견도 0(금지) 페르소나는 트리아지 입력에 등장하지 않는다(진짜 0 비용).
  3. 트리아지 1건 후 usage_log.month_to_date_by_kind()['facilitation'] 이 증가한다
     (무계량 회귀 방지 — 워처 과금이 월 합계에서 영구히 안 보였던 그 결함의 재발 방지).
  4. 참견도 1(관찰)에서 화면 채널(on_intervention) 호출 0건 — 판정은 DB 로그에만.
"""

import json

import pytest

from meeting_minutes_app.common import spend_guard, usage_log
from meeting_minutes_app.wiki_core import facilitation, personas
from meeting_minutes_app.wiki_core.facilitation import FacilitationOrchestrator


@pytest.fixture
def fac_db(tmp_path, monkeypatch):
    """usage_log(과금 집계)와 facilitation_log(관찰 로그)를 임시 DB 로 돌린다."""
    dbp = tmp_path / "meeting_assistant.db"
    monkeypatch.setattr(usage_log, "_resolve_db_path", lambda p=None: dbp)
    monkeypatch.setattr(facilitation, "_resolve_db_path", lambda p=None: dbp)
    return dbp


@pytest.fixture
def cfg(monkeypatch):
    """facilitation._c 와 spend_guard._c 를 테스트 값으로 갈아끼운다.

    spend_guard 쪽도 함께 바꾸는 이유: 트리아지가 blocked()/automation_paused() 를
    지나는데, 그게 개발 PC 의 실제 config.json 을 읽으면 테스트가 환경에 좌우된다."""
    def _apply(values):
        monkeypatch.setattr(facilitation, "_c",
                            lambda k, d=None: values.get(k, d))
        monkeypatch.setattr(spend_guard, "_c",
                            lambda k, d=None: values.get(k, d))
    return _apply


@pytest.fixture
def llm_calls(monkeypatch):
    """트리아지 LLM 호출을 가로챈다 — 실제 API 로 나가면 안 된다."""
    calls = []

    def _fake(model, system, user, max_tokens=800):
        calls.append({"model": model, "system": system, "user": user})
        return "[]"

    monkeypatch.setattr(facilitation, "_call_llm", _fake)
    return calls


def _offer_and_drain(orch, texts):
    for t in texts:
        orch.offer_segment(t)
    orch.shutdown(wait=True)


class TestDisabledByDefault:
    """수용 기준 1 — 기본 설정에서 이 기능은 한 번도 호출되지 않는다."""

    def test_default_off_zero_llm_calls(self, cfg, llm_calls, fac_db):
        cfg({})                              # facilitation.enabled 기본 false
        orch = FacilitationOrchestrator(session_id="s1")
        assert orch.enabled is False
        _offer_and_drain(orch, ["이번 분기 매출 목표는 30억입니다."] * 5)
        assert llm_calls == []
        assert usage_log.month_to_date_spend() == 0.0
        assert facilitation.observations() == []

    def test_empty_text_never_triages(self, cfg, llm_calls, fac_db):
        """침묵(빈/공백 세그먼트)은 트리아지를 만들지 않는다 — 내용 게이트."""
        cfg({"facilitation.enabled": True})
        orch = FacilitationOrchestrator(session_id="s1")
        _offer_and_drain(orch, ["", "   ", None])
        assert llm_calls == []


class TestLevelZeroExcluded:
    """수용 기준 2 — 참견도 0 은 트리아지 입력에서 제외돼 비용이 진짜 0 이다."""

    def _values(self, **overrides):
        values = {"facilitation.enabled": True}
        for k in personas.PERSONAS:
            values[f"facilitation.personas.{k}.level"] = 1
        values.update(overrides)
        return values

    def test_level0_not_in_active_personas(self, cfg, fac_db):
        cfg(self._values(**{"facilitation.personas.critic.level": 0}))
        orch = FacilitationOrchestrator(session_id="s2")
        keys = [p.key for p in orch.active_personas()]
        assert "critic" not in keys
        assert "scribe" in keys

    def test_level0_not_in_triage_prompt(self, cfg, llm_calls, fac_db):
        cfg(self._values(**{"facilitation.personas.critic.level": 0}))
        orch = FacilitationOrchestrator(session_id="s2")
        _offer_and_drain(orch, ["이 방식이 항상 더 빠르니까 그걸로 결정하시죠."])
        assert len(llm_calls) == 1
        prompt = llm_calls[0]["system"] + llm_calls[0]["user"]
        assert "critic" not in prompt        # 금지 페르소나는 LLM 입력에 미등장
        assert "scribe" in llm_calls[0]["user"]

    def test_all_zero_means_no_triage_at_all(self, cfg, llm_calls, fac_db):
        """전원 0 이면 활성 페르소나가 없다 — LLM 호출 자체가 없어야 한다."""
        cfg(self._values(**{f"facilitation.personas.{k}.level": 0
                            for k in personas.PERSONAS}))
        orch = FacilitationOrchestrator(session_id="s2")
        _offer_and_drain(orch, ["판교 데이터센터 전력 계약은 2027년 만료입니다."])
        assert llm_calls == []

    def test_hard_cap_clamps_risky_personas(self, cfg, fac_db):
        """위험 페르소나(팩트체커·비판자)는 설정만으로 hard_cap(2)을 못 넘는다."""
        cfg(self._values(**{
            "facilitation.personas.fact_checker.level": 5,
            "facilitation.personas.critic.level": 5,
            "facilitation.max_level": 5,
        }))
        orch = FacilitationOrchestrator(session_id="s2")
        assert orch.persona_level("fact_checker") == 2
        assert orch.persona_level("critic") == 2
        # 전역 상한도 함께 동작한다.
        cfg(self._values(**{"facilitation.personas.scribe.level": 5}))
        orch = FacilitationOrchestrator(session_id="s2")
        assert orch.persona_level("scribe") == 3   # max_level 기본 3 에 클램프


class TestCostMetering:
    """수용 기준 3 — facilitation 과금이 usage_log 에 kind 로 분리 집계된다."""

    def test_by_kind_increases_after_one_triage(self, cfg, llm_calls, fac_db):
        cfg({"facilitation.enabled": True})
        orch = FacilitationOrchestrator(session_id="s3")
        _offer_and_drain(orch, ["판교 데이터센터 전력 계약은 2027년 만료입니다."])
        assert len(llm_calls) == 1
        by_kind = usage_log.month_to_date_by_kind()
        assert by_kind.get(spend_guard.KIND_FACILITATION, 0.0) > 0.0
        # 월 합계(한도 판정 정본)에도 잡힌다 — 무계량 회귀 방지의 핵심.
        assert usage_log.month_to_date_spend() > 0.0

    def test_estimate_matches_pricing_single_source(self):
        """한도 판정 입력과 세션 추정의 facilitation 항이 같은 함수에서 나온다."""
        from meeting_minutes_app.common import pricing
        per_call = pricing.facilitation_triage_call_cost("gpt-4o-mini")
        assert per_call > 0.0
        est = pricing.estimate_session_cost(
            3600, "gpt-4o-mini-transcribe", include_minutes=False,
            facilitation=True, facilitation_triage_model="gpt-4o-mini",
            facilitation_period_sec=25.0)
        assert est["facilitation"] == pytest.approx(3600 / 25.0 * per_call, abs=1e-4)
        # 기본값(꺼짐)에서는 기존 호출부의 추정 총액이 변하지 않는다.
        off = pricing.estimate_session_cost(
            3600, "gpt-4o-mini-transcribe", include_minutes=False)
        assert off["facilitation"] == 0.0

    def test_blocked_prevents_call_and_charge(self, cfg, llm_calls, fac_db,
                                              monkeypatch):
        """지출 한도에 걸리면 LLM 을 부르지도, 과금을 남기지도 않는다."""
        cfg({"facilitation.enabled": True})
        monkeypatch.setattr(spend_guard, "blocked",
                            lambda est, **kw: "월 한도 초과(테스트)")
        orch = FacilitationOrchestrator(session_id="s3")
        _offer_and_drain(orch, ["판교 데이터센터 전력 계약은 2027년 만료입니다."])
        assert llm_calls == []
        assert usage_log.month_to_date_spend() == 0.0
        assert "한도" in orch.status()["skip_reason"]

    def test_automation_pause_gates_triage(self, cfg, llm_calls, fac_db,
                                           monkeypatch):
        """전역 일시정지는 상시 트리아지도 멈춘다(PRD §10 — 사실상 자동 실행)."""
        cfg({"facilitation.enabled": True})
        monkeypatch.setattr(spend_guard, "automation_paused", lambda: True)
        orch = FacilitationOrchestrator(session_id="s3")
        _offer_and_drain(orch, ["판교 데이터센터 전력 계약은 2027년 만료입니다."])
        assert llm_calls == []

    def test_meeting_cap_stops_further_triage(self, cfg, llm_calls, fac_db):
        """월 한도가 0(무제한)이어도 회의당 캡이 동작한다 — 기본 설치의 유일한 방벽."""
        cfg({"facilitation.enabled": True,
             "facilitation.max_cost_usd_per_meeting": 0.0001,
             "facilitation.triage_period_sec": 1})
        orch = FacilitationOrchestrator(session_id="s3")
        orch._session_cost = 0.0002          # 이미 캡 초과 상태
        _offer_and_drain(orch, ["판교 데이터센터 전력 계약은 2027년 만료입니다."])
        assert llm_calls == []
        assert "캡" in orch.status()["skip_reason"]


class TestObserveModeIsSilent:
    """수용 기준 4 — 참견도 1(관찰)은 DB 로그만 남기고 화면 채널을 부르지 않는다."""

    def test_level1_no_screen_events_but_logged(self, cfg, fac_db, monkeypatch):
        cfg({"facilitation.enabled": True})   # 전원 기본 1(관찰)
        candidate = [{"persona": "scribe", "trigger_type": "missing",
                      "confidence": 0.9, "span": "결정은 났는데 담당자가 없다",
                      "need_search": False}]
        monkeypatch.setattr(
            facilitation, "_call_llm",
            lambda *a, **k: json.dumps(candidate, ensure_ascii=False))
        shown = []
        orch = FacilitationOrchestrator(session_id="s4",
                                        on_intervention=shown.append)
        _offer_and_drain(orch, ["그럼 그렇게 하시죠. 다음 안건으로 넘어가겠습니다."])
        assert shown == []                    # 화면(WS) 채널 호출 0건
        rows = facilitation.observations(session_id="s4")
        assert len(rows) == 1
        assert rows[0]["persona"] == "scribe"
        assert rows[0]["level"] == 1

    def test_hallucinated_persona_key_is_dropped(self, cfg, fac_db, monkeypatch):
        """트리아지가 목록에 없는 키를 지어내면 버린다 — 금지(0) 우회 차단."""
        cfg({"facilitation.enabled": True,
             "facilitation.personas.critic.level": 0})
        candidate = [{"persona": "critic", "confidence": 0.9, "span": "…"},
                     {"persona": "made_up", "confidence": 0.9, "span": "…"}]
        monkeypatch.setattr(
            facilitation, "_call_llm",
            lambda *a, **k: json.dumps(candidate, ensure_ascii=False))
        orch = FacilitationOrchestrator(session_id="s5")
        _offer_and_drain(orch, ["이 방식이 항상 더 빠릅니다."])
        assert facilitation.observations(session_id="s5") == []


class TestRegistryData:
    """personas.py 는 데이터 전용 — PRD §3 로스터와 어긋나지 않는지 고정."""

    def test_eight_personas_registered(self):
        assert len(personas.PERSONAS) == 8
        assert set(personas.PERSONAS) == {
            "facilitator", "scribe", "domain_expert", "fact_checker",
            "devils_advocate", "junior", "senior", "critic"}

    def test_risky_personas_have_hard_cap(self):
        assert personas.get_persona("fact_checker").hard_cap == 2
        assert personas.get_persona("critic").hard_cap == 2
        assert personas.get_persona("scribe").hard_cap is None

    def test_prompts_carry_common_rules(self):
        """화자 비귀속·판정 문구 금지는 전 페르소나 공통 제약이다(§8)."""
        for p in personas.all_personas():
            assert personas.COMMON_RULES in p.system_prompt
