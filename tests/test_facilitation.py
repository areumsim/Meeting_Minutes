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
import time

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

    def _fake(model, system, user, max_tokens=None):
        calls.append({"model": model, "system": system, "user": user,
                      "max_tokens": max_tokens})
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


class TestChargedModelIsThePricedModel:
    """표시 금액 = 실제 과금. 고른 모델과 호출되는 모델이 다를 수 있다(§2-B)."""

    def test_claude_triage_model_resolves_to_the_model_actually_called(self, cfg):
        cfg({"facilitation.triage_model": "claude-haiku-4-5",
             "models.claude_model": "claude-opus-4-8"})
        # llm_client 는 claude 경로에서 model 오버라이드를 무시하고 models.claude_model
        # 로 호출한다 → 추정도 그 모델 단가여야 한다.
        assert facilitation.effective_triage_model("claude-haiku-4-5") \
            == "claude-opus-4-8"
        assert facilitation.effective_triage_model("gpt-4o-mini") == "gpt-4o-mini"

    def test_recorded_cost_uses_effective_model(self, cfg, llm_calls, fac_db):
        from meeting_minutes_app.common import pricing
        cfg({"facilitation.enabled": True,
             "facilitation.triage_model": "claude-haiku-4-5",
             "models.claude_model": "claude-opus-4-8"})
        orch = FacilitationOrchestrator(session_id="c1")
        assert orch._triage_model == "claude-opus-4-8"
        _offer_and_drain(orch, ["판교 데이터센터 전력 계약은 2027년 만료입니다."])
        spent = usage_log.month_to_date_by_kind()[spend_guard.KIND_FACILITATION]
        assert spent == pytest.approx(
            pricing.facilitation_triage_call_cost("claude-opus-4-8"), abs=1e-9)
        # haiku 단가로 계산했다면 1/12 로 과소 기록됐다(초기 구현의 결함).
        assert spent > pricing.facilitation_triage_call_cost("claude-haiku-4-5")
        # 관찰 로그에도 실제 호출 모델이 남는다
        assert facilitation.triages(session_id="c1")[0]["model"] \
            == "claude-opus-4-8"

    def test_call_max_tokens_equals_pricing_output_assumption(self, cfg, fac_db,
                                                             monkeypatch):
        """호출 상한과 추정 출력 토큰이 같은 상수에서 나온다(갈라지면 비용이 추정 초과)."""
        from meeting_minutes_app.common import llm_client as _lc
        from meeting_minutes_app.common import pricing
        seen = {}

        class _FakeLLM:
            def __init__(self, preferred="gpt"):
                seen["preferred"] = preferred

            def chat(self, system, user, temp=0.3, model=None, max_tokens=None):
                seen["max_tokens"] = max_tokens
                seen["model"] = model
                return "[]"

        monkeypatch.setattr(_lc, "LLMClient", _FakeLLM)
        facilitation._call_llm("gpt-4o-mini", "sys", "user")
        assert seen["max_tokens"] == pricing.FACILITATION_TRIAGE_MAX_OUTPUT_TOKENS
        assert pricing.FACILITATION_TRIAGE_OUTPUT_TOKENS \
            == pricing.FACILITATION_TRIAGE_MAX_OUTPUT_TOKENS
        assert seen["model"] == "gpt-4o-mini"

    def test_failed_triage_refunds_the_meeting_cap(self, cfg, fac_db, monkeypatch):
        """실패한 호출이 캡을 소진시키면 남은 회의 내내 트리아지가 막힌다."""
        cfg({"facilitation.enabled": True,
             "facilitation.max_cost_usd_per_meeting": 0.001})
        calls = []

        def _boom(*a, **k):
            calls.append(1)
            raise RuntimeError("모든 LLM API 호출 실패")
        monkeypatch.setattr(facilitation, "_call_llm", _boom)
        orch = FacilitationOrchestrator(session_id="c2")
        active = orch.active_personas()
        w = [facilitation.Utterance("발화", 0.0, 1.0, False)]
        orch._triage_task(w, active)
        assert orch._session_cost == 0.0        # 환불됨
        orch._triage_task(w, active)
        assert len(calls) == 2                  # 두 번째도 시도된다
        assert usage_log.month_to_date_spend() == 0.0   # 과금은 없다


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


class TestDisplayChannelM1:
    """M1 화면 채널 — 참견도 2·3 이 카드로 나가고, 그 경로도 비용 3관문을 지난다.

    M0 는 "화면에 아무것도 내지 않는다"를 고정했다. M1 은 반대 방향을 고정한다:
    **낼 때 무엇이 나가고, 언제 내지 않는지**. 특히 개입 생성(Tier 1)은 트리아지보다
    비싼 모델을 쓰므로(§5) 여기서 관문이 빠지면 회의당 캡이 무의미해진다.
    """

    #: 개입 문장(생성 호출의 응답). 트리아지 응답(JSON)과 구분되게 둔다.
    TEXT = "이 결정의 담당자와 기한이 아직 비어 있습니다. 지금 정하고 넘어가시겠어요?"

    def _values(self, level=3, **overrides):
        """전원 관찰(1) + 지정 페르소나만 level — 실제 M1 기본값 모양과 같다."""
        values = {"facilitation.enabled": True,
                  "facilitation.triage_period_sec": 600,
                  "facilitation.max_cost_usd_per_meeting": 0.0,
                  "facilitation.min_confidence": 0.6,
                  "facilitation.max_interventions_per_session": 12}
        for k in personas.PERSONAS:
            values[f"facilitation.personas.{k}.level"] = 1
        values["facilitation.personas.scribe.level"] = level
        values.update(overrides)
        return values

    def _llm(self, monkeypatch, cands, text=None, boom=False):
        """트리아지와 개입 생성이 **같은 `_call_llm`** 을 쓴다 — system 으로 갈라 준다."""
        calls = []

        def _fake(model, system, user, max_tokens=None):
            triage = "트리아지" in system
            calls.append({"model": model, "user": user, "triage": triage,
                          "max_tokens": max_tokens})
            if triage:
                return json.dumps(cands, ensure_ascii=False)
            if boom:
                raise RuntimeError("생성 실패")
            return self.TEXT if text is None else text

        monkeypatch.setattr(facilitation, "_call_llm", _fake)
        return calls

    def _cand(self, persona="scribe", conf=0.9, span="결정은 났는데 담당자가 없다"):
        return {"persona": persona, "trigger_type": "missing",
                "confidence": conf, "span": span, "need_search": False}

    def _run(self, orch, text="그럼 그렇게 하시죠. 다음 안건으로 넘어가겠습니다."):
        _offer_and_drain(orch, [text])

    def test_level3_emits_card_and_still_logs_the_observation(
            self, cfg, fac_db, monkeypatch):
        """화면에 뜬 개입도 관찰 로그에 남는다 — 분자에서 빠지면 오탐률이 왜곡된다."""
        cfg(self._values())
        calls = self._llm(monkeypatch, [self._cand()])
        shown = []
        orch = FacilitationOrchestrator(session_id="d1",
                                        on_intervention=shown.append)
        self._run(orch)

        assert len(shown) == 1
        item = shown[0]
        assert item["type"] == "facilitation" and item["persona"] == "scribe"
        assert item["personaLabel"] == personas.PERSONAS["scribe"].label
        assert item["kind"] == "missing"          # 카드 색·문구가 이 값으로 갈린다
        assert item["level"] == 3
        assert item["draft"] is True              # 항상 '초안' — 판정이 아니다
        assert item["searched"] is False          # 라이브 웹검색은 M2
        assert item["text"] == self.TEXT
        assert item["quote"] == "결정은 났는데 담당자가 없다"
        assert item["id"]                          # 프런트 dedup 키
        # 트리아지 1 + 생성 1
        assert [c["triage"] for c in calls] == [True, False]
        rows = facilitation.observations(session_id="d1")
        assert len(rows) == 1 and rows[0]["level"] == 3

    def test_intervention_is_metered_on_top_of_triage(
            self, cfg, fac_db, monkeypatch):
        """개입 과금이 집계에 남는다 — 무계량 회귀(이 리포 반복 결함) 방지."""
        from meeting_minutes_app.common import pricing
        cfg(self._values())
        self._llm(monkeypatch, [self._cand()])
        shown = []
        orch = FacilitationOrchestrator(session_id="d2",
                                        on_intervention=shown.append)
        self._run(orch)

        expected = (pricing.facilitation_triage_call_cost("gpt-4o-mini")
                    + pricing.facilitation_intervention_cost("gpt-4o-mini"))
        by_kind = usage_log.month_to_date_by_kind()
        assert by_kind[spend_guard.KIND_FACILITATION] == pytest.approx(
            expected, abs=1e-9)
        # 러닝 미터가 합산할 금액이 개입 이벤트에 함께 실려 나간다(분당 요율로는
        # 표현할 수 없는 건수 기반 비용 — pricing.estimate_session_cost 주석 참조)
        assert shown[0]["costUsd"] == pytest.approx(
            pricing.facilitation_intervention_cost("gpt-4o-mini"), abs=1e-9)
        # 회의별 실측 금액도 되찾을 수 있어야 한다(세션 note 규약)
        assert usage_log.session_spend("d2") == pytest.approx(expected, abs=1e-9)

    def test_intervention_uses_effective_model_for_price_and_call(
            self, cfg, fac_db, monkeypatch):
        """claude 를 고르면 실제로는 models.claude_model 이 호출된다 — 추정도 그 단가."""
        from meeting_minutes_app.common import pricing
        cfg(self._values(**{
            "facilitation.personas.scribe.model": "claude-sonnet-5",
            "models.claude_model": "claude-opus-4-8"}))
        calls = self._llm(monkeypatch, [self._cand()])
        orch = FacilitationOrchestrator(session_id="d3",
                                        on_intervention=lambda _i: None)
        self._run(orch)

        gen = [c for c in calls if not c["triage"]]
        assert len(gen) == 1 and gen[0]["model"] == "claude-opus-4-8"
        expected = (pricing.facilitation_triage_call_cost("gpt-4o-mini")
                    + pricing.facilitation_intervention_cost("claude-opus-4-8"))
        assert usage_log.month_to_date_by_kind()[
            spend_guard.KIND_FACILITATION] == pytest.approx(expected, abs=1e-9)

    def test_low_confidence_is_recorded_but_never_generated(
            self, cfg, fac_db, monkeypatch):
        """임계 미달은 **생성조차 하지 않는다** — 화면에 못 낼 개입에 돈을 쓰지 않는다."""
        cfg(self._values(**{"facilitation.min_confidence": 0.8}))
        calls = self._llm(monkeypatch, [self._cand(conf=0.5)])
        shown = []
        orch = FacilitationOrchestrator(session_id="d4",
                                        on_intervention=shown.append)
        self._run(orch)

        assert shown == []
        assert [c["triage"] for c in calls] == [True]        # 생성 호출 0건
        assert len(facilitation.observations(session_id="d4")) == 1   # 기록은 남는다

    def test_level2_waits_for_check_now_without_new_llm_call(
            self, cfg, fac_db, monkeypatch):
        """참견도 2(소극)는 모아 두고 [지금 점검]에서 방출 — 버튼이 과금을 만들지 않는다."""
        cfg(self._values(level=2))
        calls = self._llm(monkeypatch, [self._cand()])
        shown, status = [], []
        orch = FacilitationOrchestrator(session_id="d5",
                                        on_intervention=shown.append,
                                        on_status=status.append)
        self._run(orch)

        assert shown == []                       # 자동으로 뜨지 않는다
        assert orch.pending_count() == 1
        assert [s["kind"] for s in status] == ["pending"]
        before = len(calls)
        out = orch.check_now()
        assert len(out) == 1 and len(shown) == 1
        assert shown[0]["level"] == 2
        assert len(calls) == before               # 새 LLM 호출 없음(추가 과금 0)
        assert orch.pending_count() == 0
        assert orch.check_now() == []             # 두 번 눌러도 빈 목록

    def test_budget_exhaustion_degrades_to_record_only(
            self, cfg, fac_db, monkeypatch):
        """예산을 다 쓰면 생성을 멈추고 사유를 알린다(조용히 꺼지면 '기능 없음'으로 읽힌다)."""
        cfg(self._values(**{"facilitation.max_interventions_per_session": 1,
                            "facilitation.personas.facilitator.level": 3}))
        calls = self._llm(monkeypatch, [self._cand(),
                                        self._cand(persona="facilitator",
                                                   span="주제가 샜다")])
        shown, status = [], []
        orch = FacilitationOrchestrator(session_id="d6",
                                        on_intervention=shown.append,
                                        on_status=status.append)
        self._run(orch)

        assert len(shown) == 1                   # 예산 1건
        assert len([c for c in calls if not c["triage"]]) == 1   # 생성도 1회뿐
        assert any(s["kind"] == "budget" for s in status)
        assert len(facilitation.observations(session_id="d6")) == 2  # 기록은 2건
        assert orch.budget_remaining() == 0
        assert orch.status()["shown_count"] == 1

    def test_pending_items_consume_the_budget_at_generation_time(
            self, cfg, fac_db, monkeypatch):
        """참견도 2(대기)도 생성 시점에 예산을 쓴다.

        방출 시점에 세면 참견도 2 만 쓰는 회의에서는 예산이 영원히 남아 "회의당 N건"
        상한이 사라진다 — 대기 항목은 이미 생성됐으므로 돈은 이미 나갔다."""
        cfg(self._values(level=2, **{
            "facilitation.max_interventions_per_session": 1,
            "facilitation.personas.facilitator.level": 2}))
        calls = self._llm(monkeypatch, [self._cand(),
                                        self._cand(persona="facilitator",
                                                   span="주제가 샜다")])
        shown, status = [], []
        orch = FacilitationOrchestrator(session_id="d19",
                                        on_intervention=shown.append,
                                        on_status=status.append)
        self._run(orch)

        assert orch.pending_count() == 1                  # 예산 1건까지만 생성
        assert len([c for c in calls if not c["triage"]]) == 1
        assert orch.budget_remaining() == 0
        assert any(s["kind"] == "budget" for s in status)
        # 방출이 예산을 두 번 깎지 않는다
        orch.check_now()
        assert len(shown) == 1 and orch.status()["shown_count"] == 1

    def test_at_most_two_cards_per_triage_round(self, cfg, fac_db, monkeypatch):
        """한 회차에 여러 장이 쏟아지면 '흘깃 보고 넘긴다'(§19.1)가 성립하지 않는다."""
        cands = [self._cand(span="담당자 없음"),
                 self._cand(persona="facilitator", span="주제 이탈"),
                 self._cand(persona="junior", span="약어 미정의")]
        cfg(self._values(**{"facilitation.personas.facilitator.level": 3,
                            "facilitation.personas.junior.level": 3}))
        self._llm(monkeypatch, cands)
        shown = []
        orch = FacilitationOrchestrator(session_id="d7",
                                        on_intervention=shown.append)
        self._run(orch)

        assert len(shown) == facilitation.MAX_INTERVENTIONS_PER_TRIAGE == 2
        assert len(facilitation.observations(session_id="d7")) == 3   # 기록은 전부

    def test_repeated_candidate_is_not_shown_again(self, cfg, fac_db, monkeypatch):
        """창이 겹쳐 같은 발화가 재판정돼도 같은 카드를 다시 띄우지 않는다(dedup)."""
        cfg(self._values())
        self._llm(monkeypatch, [self._cand()])
        shown = []
        orch = FacilitationOrchestrator(session_id="d8",
                                        on_intervention=shown.append)
        active = orch.active_personas()
        w = [facilitation.Utterance("결정은 났는데 담당자가 없다", 0.0, 3.0, False)]
        orch._triage_task(w, active)
        orch._triage_task(w, active)              # 같은 span → repeat
        orch.shutdown(wait=True)

        assert len(shown) == 1
        rows = facilitation.observations(session_id="d8")
        assert len(rows) == 1 and rows[0]["repeats"] == 1

    def test_vault_personas_need_evidence_to_intervene(
            self, cfg, fac_db, monkeypatch):
        """근거 필수 페르소나(도메인·팩트체커)는 근거 없이 개입하지 않는다 — 추측 금지."""
        cfg(self._values(**{"facilitation.personas.domain_expert.level": 3}))
        calls = self._llm(monkeypatch, [self._cand(persona="domain_expert",
                                                   span="TSMC 3nm 수율")])
        shown = []
        orch = FacilitationOrchestrator(session_id="d9",
                                        on_intervention=shown.append)
        self._run(orch)
        assert shown == []
        assert [c["triage"] for c in calls] == [True]     # 생성 호출도 없다
        assert len(facilitation.observations(session_id="d9")) == 1

    def test_evidence_provider_output_is_reused_not_researched(
            self, cfg, fac_db, monkeypatch):
        """근거는 이미 상시 수집 중인 결과를 가져다 쓴다(추가 검색 0회, §6·§7)."""
        cfg(self._values(**{"facilitation.personas.domain_expert.level": 3}))
        calls = self._llm(monkeypatch, [self._cand(persona="domain_expert",
                                                   span="TSMC 3nm 수율")])
        hits = [0]

        def _provider():
            hits[0] += 1
            return [{"title": "반도체 공정 노트", "filename": "notes/fab.md",
                     "snippet": "3nm 수율은 …", "rank_score": 0.83}]

        shown = []
        orch = FacilitationOrchestrator(session_id="d10",
                                        on_intervention=shown.append,
                                        evidence_provider=_provider)
        self._run(orch)

        assert len(shown) == 1
        ev = shown[0]["evidence"]
        assert len(ev) == 1 and ev[0]["title"] == "반도체 공정 노트"
        assert ev[0]["source"] == "note" and ev[0]["score"] == pytest.approx(0.83)
        assert hits[0] == 1
        gen = [c for c in calls if not c["triage"]][0]
        assert "반도체 공정 노트" in gen["user"]     # 근거가 프롬프트에 들어간다

    def test_broken_evidence_provider_does_not_break_the_stream(
            self, cfg, fac_db, monkeypatch):
        """근거 공급자가 터져도 실시간 스트림은 살아 있어야 한다(대화만 보는 개입은 계속)."""
        cfg(self._values())

        def _boom():
            raise RuntimeError("검색기 없음")

        self._llm(monkeypatch, [self._cand()])
        shown = []
        orch = FacilitationOrchestrator(session_id="d11",
                                        on_intervention=shown.append,
                                        evidence_provider=_boom)
        self._run(orch)
        assert len(shown) == 1 and shown[0]["evidence"] == []

    def test_meeting_cap_blocks_intervention_with_a_visible_reason(
            self, cfg, fac_db, monkeypatch):
        """트리아지는 통과하고 개입에서 캡에 걸리는 구간 — 사유가 화면으로 나가야 한다."""
        from meeting_minutes_app.common import pricing
        cap = (pricing.facilitation_triage_call_cost("gpt-4o-mini")
               + pricing.facilitation_intervention_cost("gpt-4o-mini") / 2)
        cfg(self._values(**{"facilitation.max_cost_usd_per_meeting": cap}))
        calls = self._llm(monkeypatch, [self._cand()])
        shown, status = [], []
        orch = FacilitationOrchestrator(session_id="d12",
                                        on_intervention=shown.append,
                                        on_status=status.append)
        self._run(orch)

        assert shown == []
        assert [c["triage"] for c in calls] == [True]     # 캡 검사가 호출 전이다
        assert any(s["kind"] == "capped" for s in status)
        assert "캡" in orch.status()["skip_reason"]

    def test_monthly_cap_blocks_intervention(self, cfg, fac_db, monkeypatch):
        """월 한도는 개입 경로에도 걸린다 — 새 과금 경로가 관문을 우회하면 안 된다."""
        cfg(self._values())
        self._llm(monkeypatch, [self._cand()])
        seen = {"n": 0}

        def _blocked(est, **kw):
            # 1회차 = 트리아지(통과), 2회차 = 개입 생성(막는다)
            seen["n"] += 1
            return "" if seen["n"] == 1 else "월 한도 초과(테스트)"

        monkeypatch.setattr(spend_guard, "blocked", _blocked)
        shown, status = [], []
        orch = FacilitationOrchestrator(session_id="d13",
                                        on_intervention=shown.append,
                                        on_status=status.append)
        self._run(orch)
        assert seen["n"] == 2                    # 개입 경로도 한도 검사를 지난다
        assert shown == []
        assert any(s["kind"] == "blocked" for s in status)

    def test_failed_generation_refunds_the_meeting_cap(
            self, cfg, fac_db, monkeypatch):
        """생성 실패분이 캡을 소진하면 남은 회의의 개입이 전부 막힌다."""
        from meeting_minutes_app.common import pricing
        cfg(self._values(**{"facilitation.max_cost_usd_per_meeting": 1.0}))
        self._llm(monkeypatch, [self._cand()], boom=True)
        shown = []
        orch = FacilitationOrchestrator(session_id="d14",
                                        on_intervention=shown.append)
        self._run(orch)

        assert shown == []
        triage_only = pricing.facilitation_triage_call_cost("gpt-4o-mini")
        assert orch._session_cost == pytest.approx(triage_only, abs=1e-9)
        # 실패한 생성은 집계에도 없다(record() 를 지나지 않았다)
        assert usage_log.month_to_date_spend() == pytest.approx(
            triage_only, abs=1e-9)

    def test_intervention_callback_exception_does_not_kill_the_stream(
            self, cfg, fac_db, monkeypatch):
        """화면 콜백(WS send)이 터져도 트리아지 루프는 계속 돈다."""
        cfg(self._values())
        self._llm(monkeypatch, [self._cand()])

        def _boom(_item):
            raise RuntimeError("WS 끊김")

        orch = FacilitationOrchestrator(session_id="d15", on_intervention=_boom)
        self._run(orch)
        assert orch.status()["shown_count"] == 1
        assert facilitation.triages(session_id="d15")[0]["ok"] == 1

    def test_status_exposes_display_state_for_the_screen(
            self, cfg, fac_db, monkeypatch):
        """프런트 배지가 읽는 값 — 없으면 예산·대기 상태를 화면에 못 쓴다."""
        cfg(self._values())
        st = FacilitationOrchestrator(session_id="d16").status()
        assert st["shown_count"] == 0 and st["pending_count"] == 0
        assert st["budget"] == 12 and st["budget_remaining"] == 12
        assert st["display_personas"] == ["scribe"]      # 참견도 3 이상만

    def test_no_screen_channel_means_no_generation(self, cfg, fac_db, monkeypatch):
        """화면 채널이 없는 호출자(리플레이·헤드리스)는 참견도 3 이어도 생성하지 않는다.

        아무도 볼 수 없는 개입에 Tier 1 모델 비용을 쓰는 것은 순손실이다 — 리플레이의
        계약("트리아지 비용만 든다")이 M1 으로 조용히 깨질 자리였다."""
        cfg(self._values())
        calls = self._llm(monkeypatch, [self._cand()])
        orch = FacilitationOrchestrator(session_id="d18")   # on_intervention 없음
        self._run(orch)
        assert [c["triage"] for c in calls] == [True]
        assert orch.status()["shown_count"] == 0
        assert len(facilitation.observations(session_id="d18")) == 1   # 기록은 남는다

    def test_observe_level_never_generates_even_with_high_confidence(
            self, cfg, fac_db, monkeypatch):
        """M0 계약 유지 — 참견도 1 은 confidence 1.0 이어도 생성·표시가 없다."""
        cfg(self._values(level=1))
        calls = self._llm(monkeypatch, [self._cand(conf=1.0)])
        shown = []
        orch = FacilitationOrchestrator(session_id="d17",
                                        on_intervention=shown.append)
        self._run(orch)
        assert shown == [] and [c["triage"] for c in calls] == [True]


class TestTimeGate:
    """비용 상한의 핵심 메커니즘 — 시간 기반 게이트(§5). 없으면 발화량이 비용을 정한다."""

    def test_one_triage_per_period_regardless_of_segment_count(
            self, cfg, llm_calls, fac_db):
        cfg({"facilitation.enabled": True, "facilitation.triage_period_sec": 600})
        orch = FacilitationOrchestrator(session_id="g1")
        _offer_and_drain(orch, [f"세그먼트 {i} 입니다. 논의를 계속합니다." for i in range(20)])
        assert len(llm_calls) == 1          # 첫 발화 1회, 그 뒤 600초 안은 전부 억제
        rows = facilitation.triages(session_id="g1")
        assert len(rows) == 1               # 분모도 1회만 늘어난다

    def test_window_carries_only_last_utterance(self, cfg, llm_calls, fac_db):
        """트리아지 후 창을 비운다 — 안 비우면 같은 발화가 회차마다 다시 판정된다."""
        cfg({"facilitation.enabled": True, "facilitation.triage_period_sec": 600})
        orch = FacilitationOrchestrator(session_id="g2")
        orch.offer_segment("첫 발화입니다.")           # 여기서 트리아지 1회
        orch.offer_segment("두 번째 발화입니다.")
        orch.shutdown(wait=True)
        assert llm_calls[0]["user"].endswith("첫 발화입니다.")
        # 창에는 마지막 1건(문맥 다리) + 새 발화만 남는다
        assert [u.text for u in orch._window] == ["첫 발화입니다.", "두 번째 발화입니다."]


class TestObservationIsMeasurable:
    """M0 의 산출물은 '측정 가능한 데이터' 하나다 — 분모·중복·좌표가 남는지 고정."""

    def _one_candidate(self, monkeypatch, span="결정은 났는데 담당자가 없다"):
        candidate = [{"persona": "scribe", "trigger_type": "missing",
                      "confidence": 0.8, "span": span, "need_search": False}]
        monkeypatch.setattr(
            facilitation, "_call_llm",
            lambda *a, **k: json.dumps(candidate, ensure_ascii=False))

    def test_triage_row_is_recorded_even_with_zero_candidates(
            self, cfg, llm_calls, fac_db):
        """후보 0건도 분모에 남는다 — 없으면 '안 돌았다'와 구분이 안 된다."""
        cfg({"facilitation.enabled": True})
        orch = FacilitationOrchestrator(session_id="m1")
        _offer_and_drain(orch, ["평범한 잡담입니다."])
        rows = facilitation.triages(session_id="m1")
        assert len(rows) == 1
        assert rows[0]["ok"] == 1 and rows[0]["candidates"] == 0
        assert rows[0]["skip_reason"] == ""
        assert rows[0]["cost_usd"] > 0.0
        assert rows[0]["personas"] == 8
        assert facilitation.observations(session_id="m1") == []

    def test_skip_reason_is_persisted_not_only_in_memory(
            self, cfg, llm_calls, fac_db, monkeypatch):
        """한도로 건너뛴 회차도 사유가 DB 에 남는다(조용히 실패 금지)."""
        cfg({"facilitation.enabled": True})
        monkeypatch.setattr(spend_guard, "blocked",
                            lambda est, **kw: "월 한도 초과(테스트)")
        orch = FacilitationOrchestrator(session_id="m2")
        _offer_and_drain(orch, ["판교 데이터센터 전력 계약은 2027년 만료입니다."])
        rows = facilitation.triages(session_id="m2")
        assert len(rows) == 1
        assert rows[0]["skip_reason"] == "월 한도 초과(테스트)"
        assert rows[0]["ok"] == 0 and rows[0]["cost_usd"] == 0.0
        assert usage_log.month_to_date_spend() == 0.0

    def test_llm_failure_is_recorded_as_attempt(self, cfg, fac_db, monkeypatch):
        """양 벤더 모두 실패(llm_client 가 raise)해도 시도는 분모에 남는다."""
        cfg({"facilitation.enabled": True})

        def _boom(*a, **k):
            raise RuntimeError("모든 LLM API 호출 실패")
        monkeypatch.setattr(facilitation, "_call_llm", _boom)
        orch = FacilitationOrchestrator(session_id="m3")
        _offer_and_drain(orch, ["판교 데이터센터 전력 계약은 2027년 만료입니다."])
        rows = facilitation.triages(session_id="m3")
        assert len(rows) == 1 and rows[0]["ok"] == 0
        assert rows[0]["skip_reason"] == ""      # 관문에 막힌 게 아니라 호출 실패
        assert "실패" in orch.status()["skip_reason"]

    def test_duplicate_candidate_bumps_repeats_instead_of_new_row(
            self, cfg, fac_db, monkeypatch):
        """겹치는 창에서 같은 후보가 다시 잡히면 행이 아니라 repeats 가 는다."""
        cfg({"facilitation.enabled": True})
        self._one_candidate(monkeypatch)
        orch = FacilitationOrchestrator(session_id="m4")
        active = orch.active_personas()
        window = [facilitation.Utterance("그럼 그렇게 하시죠.", 10.0, 15.0, False)]
        orch._triage_task(window, active)
        orch._triage_task(window, active)
        rows = facilitation.observations(session_id="m4")
        assert len(rows) == 1
        assert rows[0]["repeats"] == 1
        assert len(facilitation.triages(session_id="m4")) == 2   # 분모는 2회
        assert orch.status()["observed_count"] == 1
        assert orch.status()["repeat_count"] == 1

    def test_span_time_and_provisional_are_recorded(self, cfg, fac_db, monkeypatch):
        """조각 전사 기반 판정과 확정 기반 판정을 섞어 세지 않도록 좌표를 남긴다."""
        cfg({"facilitation.enabled": True})
        self._one_candidate(monkeypatch)
        orch = FacilitationOrchestrator(session_id="m5")
        orch.offer_segment("결정은 났는데 담당자가 없습니다.", t0=12.5, t1=18.0,
                           provisional=True)
        orch.shutdown(wait=True)
        row = facilitation.observations(session_id="m5")[0]
        assert row["t0"] == 12.5 and row["t1"] == 18.0
        assert row["provisional"] == 1
        assert facilitation.triages(session_id="m5")[0]["provisional"] == 1

    def test_report_aggregates_numerator_and_denominator(
            self, cfg, fac_db, monkeypatch):
        cfg({"facilitation.enabled": True})
        self._one_candidate(monkeypatch)
        orch = FacilitationOrchestrator(session_id="m6")
        active = orch.active_personas()
        w = [facilitation.Utterance("결정은 났는데 담당자가 없다", 1.0, 2.0, True)]
        orch._triage_task(w, active)
        orch._triage_task(w, active)
        r = facilitation.report(session_id="m6")
        assert r["triage_attempts"] == 2 and r["triage_called"] == 2
        assert r["triage_skipped"] == 0 and r["triage_empty"] == 0
        assert r["candidates"] == 1 and r["candidate_repeats"] == 1
        assert r["provisional_candidates"] == 1
        assert r["by_persona"]["scribe"]["candidates"] == 1
        assert r["cost_usd"] > 0.0

    def test_report_and_cli_survive_empty_db(self, fac_db, capsys):
        r = facilitation.report()
        assert r["triage_attempts"] == 0 and r["candidates"] == 0
        assert facilitation.main([]) == 0
        assert "기록이 없습니다" in capsys.readouterr().out

    def test_delete_session_observations_clears_both_tables(
            self, cfg, fac_db, monkeypatch):
        """완전 삭제가 발화 인용을 남기지 않는다(related_notes 정리 규칙과 동일)."""
        cfg({"facilitation.enabled": True})
        self._one_candidate(monkeypatch)
        orch = FacilitationOrchestrator(session_id="m7")
        orch._triage_task([facilitation.Utterance("인용될 발화", 0.0, 1.0, False)],
                          orch.active_personas())
        assert facilitation.observations(session_id="m7")
        assert facilitation.delete_session_observations("m7") == 2
        assert facilitation.observations(session_id="m7") == []
        assert facilitation.triages(session_id="m7") == []

    def test_db_failure_never_breaks_the_stream(self, cfg, fac_db, monkeypatch):
        """기록은 부수 효과다 — DB 를 못 열어도 전사·트리아지는 계속 돈다."""
        cfg({"facilitation.enabled": True})
        self._one_candidate(monkeypatch)
        monkeypatch.setattr(facilitation, "_connect", lambda p=None: None)
        orch = FacilitationOrchestrator(session_id="m8")
        orch.offer_segment("발화입니다.")          # 예외가 새면 실시간 스트림이 깨진다
        orch.shutdown(wait=True)
        assert orch.status()["observed_count"] == 0   # 기록은 실패했지만 조용히 넘어간다
        assert orch.status()["triage_count"] == 1     # 트리아지 자체는 성공

    def test_span_key_normalizes_whitespace_and_case(self):
        a = facilitation.span_key("critic", "이 방식이  항상 더 빠릅니다")
        b = facilitation.span_key("critic", "이 방식이 항상 더 빠릅니다 ")
        assert a == b
        assert a != facilitation.span_key("junior", "이 방식이 항상 더 빠릅니다")
        # 인용이 비면 트리거 유형으로 대체 — 빈 인용끼리 같은 것으로 본다
        assert (facilitation.span_key("critic", "", "논리 비약")
                == facilitation.span_key("critic", "   ", "논리 비약"))


class TestCostIsVisibleWhereItHappens:
    """세션 중에 쓴 돈이 화면에서 사라지지 않는다 — 단, 이중 집계도 하지 않는다(§2-C·E)."""

    def test_session_note_roundtrip(self, fac_db):
        """쓰는 쪽(record)과 읽는 쪽(session_spend)이 같은 규약을 쓴다."""
        spend_guard.record(spend_guard.KIND_FACILITATION, 0.002,
                           model="gpt-4o-mini", units=1, unit_kind="triage_call",
                           note=spend_guard.session_note("sX"))
        assert usage_log.session_spend("sX") == pytest.approx(0.002)
        assert usage_log.session_spend(
            "sX", spend_guard.KIND_FACILITATION) == pytest.approx(0.002)
        assert usage_log.session_spend("다른세션") == 0.0
        assert usage_log.session_spend("") == 0.0
        # 다른 kind 로 필터하면 0 — 세션별·kind별 분리가 유지된다
        assert usage_log.session_spend("sX", spend_guard.KIND_WATCHER) == 0.0

    def test_session_cost_endpoint_includes_actual_facilitation(self, tmp_path,
                                                                monkeypatch):
        """회의 상세 금액에 트리아지 실측분이 더해진다(추정이 아니라 기록된 값)."""
        from fastapi.testclient import TestClient
        from web.backend import database as db
        from web.backend.app import app

        dbp = tmp_path / "t.db"
        monkeypatch.setattr(db, "DB_PATH", dbp)
        monkeypatch.setattr(usage_log, "_resolve_db_path", lambda p=None: dbp)
        db.init_db()
        sid = db.create_session(title="회의", source="web")
        db.update_session_status(sid, "completed", duration_sec=600,
                                 cost_estimate=0.05)
        spend_guard.record(spend_guard.KIND_FACILITATION, 0.0123,
                           note=spend_guard.session_note(sid))

        body = TestClient(app).get(f"/api/sessions/{sid}/cost").json()
        assert body["ok"] is True
        assert body["facilitation"] == pytest.approx(0.0123)
        # 총액에 포함된다 — 이 항목이 빠져 상세 금액이 실제보다 적게 보였다
        assert body["total"] >= 0.0123
        assert body["facilitation_actual"] is True

    def test_finalize_estimate_never_includes_facilitation(self):
        """sessions.cost_estimate 기록 경로가 facilitation 을 켜면 이중 집계된다.

        realtime.py 의 finalize 추정에 이 인자가 들어가지 않는 것을 코드로 고정한다 —
        pricing 독스트링의 경고를 다음 사람이 반대로 읽고 '자연스럽게' 켤 수 있다."""
        import re
        from pathlib import Path
        src = Path("web/backend/api/realtime.py").read_text(encoding="utf-8")
        m = re.search(r"_est = pricing\.estimate_session_cost\((.*?)\)\[",
                      src, re.S)
        assert m, "finalize 의 추정 호출을 찾지 못했다(테스트를 갱신할 것)"
        assert "facilitation" not in m.group(1).replace("# ", "").split("\n")[0]
        # 인자로 넘기는 코드가 없다(주석의 설명 문구는 허용)
        assert not re.search(r"^\s*facilitation\s*=", m.group(1), re.M)

    def test_cost_rates_reports_facilitation_per_min_when_on(self, tmp_path,
                                                            monkeypatch):
        """녹음 화면 러닝 미터가 상시 트리아지 요율을 반영한다."""
        import json as _json
        from fastapi.testclient import TestClient
        from meeting_minutes_app.common import config_loader, pricing
        from web.backend.app import app

        cfg_path = tmp_path / "config.json"
        cfg_path.write_text(_json.dumps({
            "models": {"stt": "gpt-4o-mini-transcribe"},
            "facilitation": {"enabled": True, "triage_model": "gpt-4o-mini",
                             "triage_period_sec": 25},
        }), encoding="utf-8")
        monkeypatch.setattr(config_loader, "_CONFIG_PATH", cfg_path)
        config_loader.reload()
        try:
            body = TestClient(app).get("/api/cost/rates").json()
            assert body["facilitation_on"] is True
            assert body["facilitation_model"] == "gpt-4o-mini"
            # 60초 기준 = 분당 요율. 단가는 오케스트레이터 한도 판정과 같은 함수.
            assert body["facilitation_per_min"] == pytest.approx(
                round(60 / 25 * pricing.facilitation_triage_call_cost("gpt-4o-mini"), 4),
                abs=1e-4)
        finally:
            config_loader._cache = None      # 다른 테스트가 실제 config 를 보도록

    def test_cost_rates_is_zero_when_feature_off(self, tmp_path, monkeypatch):
        import json as _json
        from fastapi.testclient import TestClient
        from meeting_minutes_app.common import config_loader
        from web.backend.app import app

        cfg_path = tmp_path / "config.json"
        cfg_path.write_text(_json.dumps({"models": {"stt": "gpt-4o-mini-transcribe"}}),
                            encoding="utf-8")
        monkeypatch.setattr(config_loader, "_CONFIG_PATH", cfg_path)
        config_loader.reload()
        try:
            body = TestClient(app).get("/api/cost/rates").json()
            assert body["facilitation_on"] is False
            assert body["facilitation_per_min"] == 0.0
        finally:
            config_loader._cache = None


class TestMidwayBrief:
    """중간 요약(주기 페르소나 summarizer) — 음성브리핑 PRD 트랙 A 를 오케스트레이터에
    1종으로 합친 것. 트랙 A 의 게이트(경과 시간 + 새 발화량)와 '요약은 후보가 아니다'가
    이 클래스의 고정 대상이다."""

    BRIEF = {"points": ["출시 일정 논의"], "decisions": ["9월 1일로 확정"],
             "actions": ["일정표 갱신"], "open_questions": ["예산 승인?"]}

    def _values(self, triage=False, **overrides):
        """기본은 요약만 켠 설정 — 트리아지 페르소나를 전원 0(금지)으로 두면 트리아지
        LLM 호출 자체가 없어(수용 기준 §14) 요약 경로만 남는다. 시간 게이트는 첫
        세그먼트에서 즉시 1회 도므로 주기를 크게 잡는 것으로는 격리되지 않는다."""
        values = {"facilitation.enabled": True,
                  "facilitation.triage_period_sec": 100000,
                  "facilitation.brief_period_sec": 600,
                  "facilitation.brief_min_new_chars": 600,
                  "facilitation.max_cost_usd_per_meeting": 0.0}
        for k in personas.PERSONAS:
            values[f"facilitation.personas.{k}.level"] = 1 if triage else 0
        values["facilitation.personas.summarizer.level"] = 3
        values.update(overrides)
        return values

    def _llm(self, monkeypatch, payload=None, boom=False):
        """요약 호출만 가려낸다 — 트리아지와 같은 `_call_llm` 을 쓴다."""
        calls = []

        def _fake(model, system, user, max_tokens=None):
            brief = "중간 요약" in system
            calls.append({"model": model, "user": user, "brief": brief,
                          "max_tokens": max_tokens})
            if not brief:
                return "[]"
            if boom:
                raise RuntimeError("요약 실패")
            return json.dumps(self.BRIEF if payload is None else payload,
                              ensure_ascii=False)

        monkeypatch.setattr(facilitation, "_call_llm", _fake)
        return calls

    def _orch(self, fac_db, session, now, shown, status=None):
        return FacilitationOrchestrator(
            session_id=session, clock=lambda: now["t"],
            on_intervention=shown.append,
            on_status=(status.append if status is not None else None),
            db_path=fac_db)

    def _drive(self, orch, now, chars=700, period=600.0):
        """첫 발화(주기 시작점) → 주기 경과 후 충분한 발화 → 요약 1회."""
        orch.offer_segment("회의를 시작합니다.")
        now["t"] += period + 1
        orch.offer_segment("가" * chars)
        orch.shutdown(wait=True)

    def test_periodic_brief_emits_a_structured_card(self, cfg, fac_db, monkeypatch):
        cfg(self._values())
        calls = self._llm(monkeypatch)
        now, shown = {"t": 0.0}, []
        orch = self._orch(fac_db, "b1", now, shown)
        self._drive(orch, now)

        assert len(shown) == 1
        item = shown[0]
        assert item["persona"] == "summarizer" and item["kind"] == "brief"
        assert item["brief"]["decisions"] == ["9월 1일로 확정"]
        assert item["onDemand"] is False and item["draft"] is True
        # 카드 접힘 줄·로그·다음 요약 입력이 쓰는 텍스트가 절 제목을 갖는다
        assert "[결정] 9월 1일로 확정" in item["text"]
        assert [c["brief"] for c in calls] == [True]     # 트리아지는 안 돌았다
        assert orch.status()["brief_count"] == 1

    def test_first_brief_waits_one_period(self, cfg, fac_db, monkeypatch):
        """녹음 시작 직후에는 정리할 내용이 없다 — 첫 요약은 1주기 뒤다."""
        cfg(self._values())
        calls = self._llm(monkeypatch)
        now, shown = {"t": 0.0}, []
        orch = self._orch(fac_db, "b2", now, shown)
        orch.offer_segment("가" * 900)          # 내용은 충분하지만 주기 전이다
        orch.shutdown(wait=True)
        assert shown == [] and calls == []

    def test_silence_skips_the_period(self, cfg, fac_db, monkeypatch):
        """주기가 됐어도 새 발화가 적으면 요약하지 않는다(빈 요약에 돈을 쓰지 않는다)."""
        cfg(self._values())
        calls = self._llm(monkeypatch)
        now, shown = {"t": 0.0}, []
        orch = self._orch(fac_db, "b3", now, shown)
        self._drive(orch, now, chars=50)        # 600자 미달
        assert shown == [] and calls == []

    def test_summarizer_never_enters_the_triage_prompt(self, cfg, fac_db,
                                                      monkeypatch):
        """요약은 후보 판정 대상이 아니다 — 넣으면 상시 프롬프트가 길어지고 분모가 오염된다."""
        cfg(self._values(triage=True, **{"facilitation.triage_period_sec": 1}))
        calls = self._llm(monkeypatch)
        now, shown = {"t": 0.0}, []
        orch = self._orch(fac_db, "b4", now, shown)
        orch.offer_segment("이 방식이 항상 더 빠릅니다.")
        orch.shutdown(wait=True)
        triage = [c for c in calls if not c["brief"]]
        assert len(triage) == 1
        assert "summarizer" not in triage[0]["user"]
        assert "중간 요약" not in triage[0]["user"]
        assert "summarizer" not in [p.key for p in orch.active_personas()]
        rows = facilitation.triages(session_id="b4", db_path=fac_db)
        assert rows[0]["personas"] == 8          # 주기 페르소나는 세지 않는다

    def test_observe_level_makes_no_brief(self, cfg, fac_db, monkeypatch):
        """참견도 1(관찰)에 '요약을 기록만' 상태는 없다 — 아무도 안 볼 요약에 돈을 안 쓴다."""
        cfg(self._values(**{"facilitation.personas.summarizer.level": 1}))
        calls = self._llm(monkeypatch)
        now, shown = {"t": 0.0}, []
        orch = self._orch(fac_db, "b5", now, shown)
        self._drive(orch, now)
        assert shown == [] and calls == []
        assert orch.brief_enabled() is False

    def test_period_zero_disables_the_automatic_brief(self, cfg, fac_db, monkeypatch):
        cfg(self._values(**{"facilitation.brief_period_sec": 0}))
        calls = self._llm(monkeypatch)
        now, shown = {"t": 0.0}, []
        orch = self._orch(fac_db, "b6", now, shown)
        self._drive(orch, now)
        assert shown == [] and calls == []

    def test_brief_is_metered_and_recorded_as_a_brief_not_a_candidate(
            self, cfg, fac_db, monkeypatch):
        from meeting_minutes_app.common import pricing
        cfg(self._values())
        self._llm(monkeypatch)
        now, shown = {"t": 0.0}, []
        orch = self._orch(fac_db, "b7", now, shown)
        self._drive(orch, now)

        expected = pricing.facilitation_brief_cost("gpt-4o-mini")
        assert usage_log.month_to_date_by_kind()[
            spend_guard.KIND_FACILITATION] == pytest.approx(expected, abs=1e-9)
        assert shown[0]["costUsd"] == pytest.approx(expected, abs=1e-9)
        rows = facilitation.observations(session_id="b7", db_path=fac_db)
        assert len(rows) == 1 and rows[0]["persona"] == "summarizer"
        assert rows[0]["trigger_type"] == facilitation.TRIGGER_BRIEF_PERIODIC
        # 오탐률 분자에 섞이지 않는다
        r = facilitation.report("b7", db_path=fac_db)
        assert r["briefs"] == 1 and r["candidates"] == 0
        assert "summarizer" not in r["by_persona"]

    def test_brief_does_not_consume_the_intervention_budget(
            self, cfg, fac_db, monkeypatch):
        """주기 산출물이라 횟수가 이미 시간으로 묶여 있다 — 기회주의적 개입 카드가
        사용자가 기대하는 요약을 굶기지 않게 한다(상한은 주기와 비용 캡)."""
        cfg(self._values(**{"facilitation.max_interventions_per_session": 1}))
        self._llm(monkeypatch)
        now, shown = {"t": 0.0}, []
        orch = self._orch(fac_db, "b8", now, shown)
        self._drive(orch, now)
        assert len(shown) == 1
        assert orch.budget_remaining() == 1          # 예산은 그대로다
        assert orch.status()["shown_count"] == 0

    def test_meeting_cap_holds_the_brief_with_a_reason(self, cfg, fac_db,
                                                      monkeypatch):
        from meeting_minutes_app.common import pricing
        cap = pricing.facilitation_brief_cost("gpt-4o-mini") / 2
        cfg(self._values(**{"facilitation.max_cost_usd_per_meeting": cap}))
        calls = self._llm(monkeypatch)
        now, shown, status = {"t": 0.0}, [], []
        orch = self._orch(fac_db, "b9", now, shown, status)
        self._drive(orch, now)
        assert shown == [] and calls == []           # 캡 검사가 호출 전이다
        assert any(s["kind"] == "capped" for s in status)

    def test_failed_brief_refunds_and_stays_silent(self, cfg, fac_db, monkeypatch):
        cfg(self._values(**{"facilitation.max_cost_usd_per_meeting": 1.0}))
        self._llm(monkeypatch, boom=True)
        now, shown = {"t": 0.0}, []
        orch = self._orch(fac_db, "b10", now, shown)
        self._drive(orch, now)
        assert shown == []
        assert orch._session_cost == 0.0             # 환불됨
        assert usage_log.month_to_date_spend() == 0.0

    def test_empty_summary_is_not_shown(self, cfg, fac_db, monkeypatch):
        """모든 절이 비면 카드를 내지 않는다 — 빈 카드는 소음이다."""
        cfg(self._values())
        self._llm(monkeypatch, payload={"points": [], "decisions": [],
                                        "actions": [], "open_questions": []})
        now, shown = {"t": 0.0}, []
        orch = self._orch(fac_db, "b11", now, shown)
        self._drive(orch, now)
        assert shown == []

    def test_brief_now_runs_immediately_and_is_labeled_on_demand(
            self, cfg, fac_db, monkeypatch):
        cfg(self._values(**{"facilitation.brief_period_sec": 100000}))
        calls = self._llm(monkeypatch)
        now, shown = {"t": 0.0}, []
        orch = self._orch(fac_db, "b12", now, shown)
        orch.offer_segment("가" * 200)               # 주기는 멀지만 내용은 있다
        now["t"] += 100                              # 연타 가드(20초) 밖
        assert orch.brief_now() == ""
        orch.shutdown(wait=True)

        assert len(shown) == 1 and shown[0]["onDemand"] is True
        assert [c["brief"] for c in calls] == [True]
        rows = facilitation.observations(session_id="b12", db_path=fac_db)
        assert rows[0]["trigger_type"] == facilitation.TRIGGER_BRIEF_ON_DEMAND
        r = facilitation.report("b12", db_path=fac_db)
        assert r["briefs"] == 1 and r["briefs_on_demand"] == 1

    def test_brief_now_is_debounced_and_needs_new_speech(self, cfg, fac_db,
                                                        monkeypatch):
        """이 버튼은 새 과금을 만든다 — 연타·빈 내용은 사유를 돌려주고 돌지 않는다."""
        cfg(self._values(**{"facilitation.brief_period_sec": 100000}))
        calls = self._llm(monkeypatch)
        now, shown = {"t": 0.0}, []
        orch = self._orch(fac_db, "b13", now, shown)
        orch.offer_segment("가" * 200)
        now["t"] += 100
        assert orch.brief_now() == ""
        reason = orch.brief_now()                    # 곧바로 다시 누름
        assert "방금 정리했습니다" in reason
        now["t"] += 100
        assert "새로 쌓인 발화가 없습니다" in orch.brief_now()   # 그 뒤 발화 없음
        orch.shutdown(wait=True)
        assert len([c for c in calls if c["brief"]]) == 1

    def test_brief_now_shows_even_at_collect_level(self, cfg, fac_db, monkeypatch):
        """눌렀는데 [지금 점검] 대기열로 들어가면 버튼이 고장난 것처럼 보인다."""
        cfg(self._values(**{"facilitation.personas.summarizer.level": 2,
                            "facilitation.brief_period_sec": 100000}))
        self._llm(monkeypatch)
        now, shown = {"t": 0.0}, []
        orch = self._orch(fac_db, "b14", now, shown)
        orch.offer_segment("가" * 200)
        now["t"] += 100
        assert orch.brief_now() == ""
        orch.shutdown(wait=True)
        assert len(shown) == 1 and orch.pending_count() == 0

    def test_brief_now_is_refused_when_summaries_are_off(self, cfg, fac_db,
                                                        monkeypatch):
        cfg(self._values(**{"facilitation.personas.summarizer.level": 1}))
        self._llm(monkeypatch)
        now, shown = {"t": 0.0}, []
        orch = self._orch(fac_db, "b15", now, shown)
        orch.offer_segment("가" * 200)
        assert "참견도" in orch.brief_now()
        orch.shutdown(wait=True)
        assert shown == []

    def test_previous_summary_is_carried_into_the_next_prompt(
            self, cfg, fac_db, monkeypatch):
        """전체 전사를 매번 넣지 않는 대신 이전 요약을 이어받는다(FR-A2)."""
        cfg(self._values())
        calls = self._llm(monkeypatch)
        now, shown = {"t": 0.0}, []
        orch = self._orch(fac_db, "b16", now, shown)
        orch.offer_segment("회의를 시작합니다.")
        now["t"] += 601
        orch.offer_segment("가" * 700)
        # 첫 요약이 끝난 뒤에 두 번째를 낸다 — 풀이 2워커라 동시에 제출하면 두 번째가
        # 이전 요약이 저장되기 전에 프롬프트를 만든다(실사용에서는 600초 간격이라
        # 겹치지 않는다).
        for _ in range(200):
            if orch.status()["brief_count"] >= 1:
                break
            time.sleep(0.01)
        now["t"] += 601
        orch.offer_segment("나" * 700)
        orch.shutdown(wait=True)

        briefs = [c for c in calls if c["brief"]]
        assert len(briefs) == 2
        assert "이전 요약" not in briefs[0]["user"]
        assert "이전 요약" in briefs[1]["user"]
        assert "9월 1일로 확정" in briefs[1]["user"]
        # 두 번째 요약 입력에 첫 구간(가…)이 다시 들어가지 않는다(버퍼를 비운다)
        assert "가" * 50 not in briefs[1]["user"]

    def test_brief_now_ws_message_is_wired_in_both_loops(self):
        from pathlib import Path
        src = Path("web/backend/api/realtime.py").read_text(encoding="utf-8")
        assert src.count('"facilitation_brief_now"') == 2
        assert "def _facilitation_brief_now" in src
        # 시작 시 [지금 정리] 표시 조건(실효값)을 프런트에 알린다
        assert "def _announce_facilitation" in src


class TestHumanFeedbackLabels:
    """카드의 [✓ 확인]/[✕ 닫기] 가 §15 오탐률 실측의 **사람 라벨**로 남는지 고정.

    이 채널이 없으면 M2 진입 게이트(오탐률)는 회의가 끝난 뒤 사람이 따로 라벨링해야만
    나온다 — 실무에서 그 작업은 하지 않게 되고, 게이트는 영구히 열리지 않는다."""

    def _shown_item(self, cfg, monkeypatch, fac_db, session="f1"):
        """참견도 3 으로 개입 1건을 실제로 만들어 그 이벤트를 돌려준다."""
        values = {"facilitation.enabled": True,
                  "facilitation.triage_period_sec": 600,
                  "facilitation.personas.scribe.level": 3}
        for k in personas.PERSONAS:
            values.setdefault(f"facilitation.personas.{k}.level", 1)
        values["facilitation.personas.scribe.level"] = 3
        cfg(values)
        cands = [{"persona": "scribe", "trigger_type": "missing",
                  "confidence": 0.9, "span": "결정은 났는데 담당자가 없다"}]

        def _fake(model, system, user, max_tokens=None):
            if "트리아지" in system:
                return json.dumps(cands, ensure_ascii=False)
            return "담당자와 기한을 정하고 넘어가시겠어요?"

        monkeypatch.setattr(facilitation, "_call_llm", _fake)
        shown = []
        orch = FacilitationOrchestrator(session_id=session,
                                        on_intervention=shown.append,
                                        db_path=fac_db)
        _offer_and_drain(orch, ["그럼 그렇게 하시죠."])
        assert len(shown) == 1
        return orch, shown[0]

    def test_ack_and_dismiss_land_on_the_candidate_row(
            self, cfg, fac_db, monkeypatch):
        orch, item = self._shown_item(cfg, monkeypatch, fac_db)
        assert item["spanHash"]                  # 라벨을 붙일 좌표가 이벤트에 있다

        assert orch.feedback("scribe", item["spanHash"], "ack") is True
        rows = facilitation.observations(session_id="f1", db_path=fac_db)
        assert rows[0]["feedback"] == "ack" and rows[0]["feedback_ts"]

        # 같은 카드를 다시 누르면 마지막 값이 남는다(새 행을 만들지 않는다)
        assert orch.feedback("scribe", item["spanHash"], "dismiss") is True
        rows = facilitation.observations(session_id="f1", db_path=fac_db)
        assert len(rows) == 1 and rows[0]["feedback"] == "dismiss"

    def test_unknown_label_is_rejected(self, cfg, fac_db, monkeypatch):
        """자유 문자열을 그대로 받으면 집계가 무의미해진다."""
        orch, item = self._shown_item(cfg, monkeypatch, fac_db, session="f2")
        assert orch.feedback("scribe", item["spanHash"], "좋아요") is False
        rows = facilitation.observations(session_id="f2", db_path=fac_db)
        assert not rows[0]["feedback"]

    def test_feedback_for_missing_row_is_not_an_error(self, fac_db):
        """이미 완전 삭제된 회의의 카드를 눌러도 조용히 무시한다(스트림 보호)."""
        assert facilitation.record_feedback("없는세션", "scribe", "deadbeef",
                                            "ack", db_path=fac_db) is False

    def test_report_counts_labels_overall_and_per_persona(
            self, cfg, fac_db, monkeypatch):
        orch, item = self._shown_item(cfg, monkeypatch, fac_db, session="f3")
        orch.feedback("scribe", item["spanHash"], "dismiss")
        r = facilitation.report("f3", db_path=fac_db)
        assert r["feedback_dismiss"] == 1 and r["feedback_ack"] == 0
        assert r["by_persona"]["scribe"]["dismiss"] == 1
        assert r["by_persona"]["scribe"]["ack"] == 0

    def test_purge_removes_labels_with_the_candidate(self, cfg, fac_db, monkeypatch):
        """라벨을 별도 테이블에 두지 않은 이유 — 완전 삭제에서 빠지는 사이드카가
        하나 더 생기면 안 된다(발화 인용이 남았던 전례)."""
        orch, item = self._shown_item(cfg, monkeypatch, fac_db, session="f4")
        orch.feedback("scribe", item["spanHash"], "ack")
        facilitation.delete_session_observations("f4", db_path=fac_db)
        assert facilitation.observations(session_id="f4", db_path=fac_db) == []

    def test_ws_message_routes_to_the_orchestrator(self):
        """프런트가 보내는 메시지 타입이 두 수신 루프 모두에 배선돼 있는지 고정 —
        한쪽만 있으면 HTTP 청크 모드에서 라벨이 조용히 사라진다(전례 있음)."""
        from pathlib import Path
        src = Path("web/backend/api/realtime.py").read_text(encoding="utf-8")
        # 수신 루프가 2개다(WS 오디오 경로 / HTTP 청크 경로) — 분기도 2벌 있어야 한다
        assert src.count('"facilitation_feedback"') == 2
        assert src.count('"facilitation_check"') == 2
        assert "def _facilitation_feedback" in src


class TestPersonaMatrixApi:
    """설정 화면의 참견도 매트릭스가 읽는 API — 목록·상한·실효값의 단일 소스.

    M0 부터 쓰던 config.json 은 전원 참견도 1(관찰)이라, 이 화면 없이는 기능을 켜도
    화면에 아무것도 뜨지 않는다("켰는데 아무 일도 안 일어나는 토글" — 이 리포가 반복해서
    없애온 함정). 그래서 목록을 프런트에 복사하지 않고 서버 레지스트리를 내려준다."""

    def _client(self, tmp_path, monkeypatch, cfg_values):
        import json as _json
        from fastapi.testclient import TestClient
        from meeting_minutes_app.common import config_loader
        from web.backend.app import app

        cfg_path = tmp_path / "config.json"
        cfg_path.write_text(_json.dumps(cfg_values), encoding="utf-8")
        monkeypatch.setattr(config_loader, "_CONFIG_PATH", cfg_path)
        config_loader.reload()
        return TestClient(app), cfg_path

    def test_lists_every_persona_with_current_level(self, tmp_path, monkeypatch):
        client, _ = self._client(tmp_path, monkeypatch, {
            "facilitation": {"enabled": True, "max_level": 3,
                             "personas": {"scribe": {"level": 3}}}})
        try:
            body = client.get("/api/facilitation/personas").json()
            assert body["ok"] is True and body["enabled"] is True
            keys = [p["key"] for p in body["personas"]]
            assert keys == list(personas.PERSONAS)      # 8종 전부, 레지스트리 순서
            row = {p["key"]: p for p in body["personas"]}
            assert row["scribe"]["level"] == 3
            assert row["facilitator"]["level"] == 1     # 미설정 = 관찰
            # 프런트가 채널 경계를 상수로 복사하지 않게 함께 내려준다
            assert body["displayLevel"] == facilitation.DISPLAY_LEVEL
            assert body["collectLevel"] == facilitation.COLLECT_LEVEL
        finally:
            from meeting_minutes_app.common import config_loader
            config_loader._cache = None

    def test_risky_persona_reports_its_hard_cap_and_clamped_level(
            self, tmp_path, monkeypatch):
        """설정으로 5 를 적어도 실효값은 hard_cap 이다 — 화면이 그 차이를 보여줄 수 있어야
        한다(적어둔 값과 적용값을 함께 준다)."""
        client, _ = self._client(tmp_path, monkeypatch, {
            "facilitation": {"enabled": True, "max_level": 5,
                             "personas": {"fact_checker": {"level": 5}}}})
        try:
            row = {p["key"]: p for p in
                   client.get("/api/facilitation/personas").json()["personas"]}
            assert row["fact_checker"]["hardCap"] == 2
            assert row["fact_checker"]["configuredLevel"] == 5
            assert row["fact_checker"]["level"] == 2       # 코어 클램프와 같은 값
            assert row["scribe"]["hardCap"] is None
        finally:
            from meeting_minutes_app.common import config_loader
            config_loader._cache = None

    def test_global_max_level_clamps_the_reported_level(self, tmp_path, monkeypatch):
        client, _ = self._client(tmp_path, monkeypatch, {
            "facilitation": {"enabled": True, "max_level": 1,
                             "personas": {"scribe": {"level": 3}}}})
        try:
            body = client.get("/api/facilitation/personas").json()
            row = {p["key"]: p for p in body["personas"]}
            assert body["maxLevel"] == 1
            assert row["scribe"]["configuredLevel"] == 3 and row["scribe"]["level"] == 1
        finally:
            from meeting_minutes_app.common import config_loader
            config_loader._cache = None

    def test_levels_round_trip_through_config_save(self, tmp_path, monkeypatch):
        """화면은 점 있는 키로 저장한다 — 서버가 중첩 경로로 풀어 주는지 고정."""
        import json as _json
        client, cfg_path = self._client(tmp_path, monkeypatch, {
            "facilitation": {"enabled": True, "max_level": 3}})
        monkeypatch.setattr("web.backend.api.settings.CONFIG_PATH", cfg_path)
        try:
            res = client.put("/api/config", json={"facilitation": {
                "personas.scribe.level": 3, "personas.critic.level": 0}})
            assert res.status_code == 200, res.text
            saved = _json.loads(cfg_path.read_text(encoding="utf-8"))
            assert saved["facilitation"]["personas"]["scribe"]["level"] == 3
            assert saved["facilitation"]["personas"]["critic"]["level"] == 0
            # 저장 직후 조회가 같은 값을 보여야 한다(reload 훅)
            row = {p["key"]: p for p in
                   client.get("/api/facilitation/personas").json()["personas"]}
            assert row["scribe"]["level"] == 3 and row["critic"]["level"] == 0
        finally:
            from meeting_minutes_app.common import config_loader
            config_loader._cache = None


class TestReplayPastMeetings:
    """지난 회의 전사로 관찰 데이터를 만든다 — 새 녹음 5건을 기다리지 않아도 M2 게이트를
    측정할 수 있다(지난 회의에는 대조 정답이 이미 있다)."""

    @pytest.fixture
    def session_with_segments(self, fac_db):
        """같은 DB 파일에 segments 를 심는다 — 리플레이는 web.backend 를 import 하지
        않고 이 테이블을 직접 읽는다."""
        import sqlite3
        c = sqlite3.connect(str(fac_db))
        c.executescript("""
            CREATE TABLE IF NOT EXISTS segments (
                id TEXT PRIMARY KEY, session_id TEXT, speaker TEXT, text TEXT,
                translated_text TEXT, start_time REAL, end_time REAL);
        """)
        rows = [
            ("1", "past-1", "", "지난 회의 첫 발화입니다.", "", 0.0, 20.0),
            ("2", "past-1", "", "결정은 났는데 담당자가 없습니다.", "", 20.0, 40.0),
            ("3", "past-1", "", "  ", "", 40.0, 41.0),          # 빈 발화 — 무시된다
            ("4", "past-1", "", "다음 안건으로 넘어가겠습니다.", "", 41.0, 70.0),
        ]
        c.executemany("INSERT INTO segments VALUES (?,?,?,?,?,?,?)", rows)
        c.commit()
        c.close()
        return fac_db

    def test_segments_are_read_without_web_backend(self, session_with_segments):
        segs = facilitation.session_segments("past-1", db_path=session_with_segments)
        assert [s.text for s in segs] == [
            "지난 회의 첫 발화입니다.", "결정은 났는데 담당자가 없습니다.",
            "다음 안건으로 넘어가겠습니다."]
        # 지난 회의 전사는 보정이 끝난 확정 텍스트다
        assert all(s.provisional is False for s in segs)
        assert segs[0].t0 == 0.0 and segs[0].t1 == 20.0

    def test_replay_uses_segment_clock_not_wall_clock(self, cfg, llm_calls,
                                                      session_with_segments):
        """세그먼트가 즉시 도착하므로 실제 시계로 게이트를 재면 트리아지가 1회만 돈다."""
        cfg({"facilitation.enabled": True, "facilitation.triage_period_sec": 25})
        res = facilitation.replay_session("past-1", db_path=session_with_segments)
        assert res["ok"] is True and res["segments"] == 3
        # 세그먼트 끝 시각이 시계다: 20s(첫 발화 → 즉시 1회) → 40s(20s 경과, 게이트 미달)
        # → 70s(50s 경과 → 2회). 실제 시계로 재면 전부 즉시라 1회로 끝난다.
        assert res["triages"] == 2
        assert len(llm_calls) == res["triages"]
        rows = facilitation.triages("past-1", db_path=session_with_segments)
        assert len(rows) == res["triages"]
        assert all(r["note"] == facilitation.NOTE_REPLAY for r in rows)
        assert all(r["provisional"] == 0 for r in rows)

    def test_replay_runs_even_when_feature_is_off(self, cfg, llm_calls,
                                                  session_with_segments):
        """사용자가 명시적으로 부른 측정 명령이다 — 라이브 토글과 무관하게 돌아야 한다."""
        cfg({})                                   # facilitation.enabled 기본 false
        assert FacilitationOrchestrator(session_id="x").enabled is False
        res = facilitation.replay_session("past-1", db_path=session_with_segments)
        assert res["ok"] is True and res["triages"] >= 1

    def test_replay_costs_triage_only_even_with_display_levels(
            self, cfg, session_with_segments, monkeypatch):
        """리플레이 계약: 트리아지 비용만 든다. 참견도 3(옆 카드)로 둔 설정에서도
        개입 생성(Tier 1, 상위 모델)이 돌면 아무도 못 보는 카드에 돈을 쓰는 것이다."""
        cfg({"facilitation.enabled": True, "facilitation.triage_period_sec": 25,
             "facilitation.personas.scribe.level": 3,
             "facilitation.personas.senior.level": 3})
        cands = [{"persona": "scribe", "trigger_type": "missing",
                  "confidence": 0.95, "span": "결정은 났는데 담당자가 없습니다."}]
        calls = []

        def _fake(model, system, user, max_tokens=None):
            calls.append("트리아지" in system)
            return json.dumps(cands, ensure_ascii=False)

        monkeypatch.setattr(facilitation, "_call_llm", _fake)
        res = facilitation.replay_session("past-1", db_path=session_with_segments)
        assert res["ok"] is True
        assert all(calls), "리플레이가 개입 생성까지 호출했다(비용 순손실)"
        assert facilitation.observations("past-1",
                                         db_path=session_with_segments)   # 기록은 남는다

    def test_enabled_override_does_not_leak_into_live_path(self, cfg):
        """오버라이드는 리플레이 전용 — 기본 경로의 '꺼져 있으면 LLM 0회'를 깨지 않는다."""
        cfg({})
        assert FacilitationOrchestrator(session_id="x").enabled is False
        assert FacilitationOrchestrator(session_id="x",
                                        enabled_override=True).enabled is True

    def test_replay_rows_are_separated_in_report(self, cfg, fac_db, monkeypatch,
                                                 session_with_segments):
        """라이브(조각)와 리플레이(확정) 판정을 섞어 세면 실측이 무의미해진다."""
        cfg({"facilitation.enabled": True})
        monkeypatch.setattr(facilitation, "_call_llm", lambda *a, **k: json.dumps(
            [{"persona": "scribe", "trigger_type": "missing", "confidence": 0.8,
              "span": "담당자가 없다", "need_search": False}], ensure_ascii=False))
        # 라이브 1건
        orch = FacilitationOrchestrator(session_id="past-1")
        orch.offer_segment("결정은 났는데 담당자가 없습니다.", t0=1.0, t1=2.0,
                           provisional=True)
        orch.shutdown(wait=True)
        # 리플레이
        facilitation.replay_session("past-1", db_path=session_with_segments)
        r = facilitation.report("past-1", db_path=session_with_segments)
        assert r["live"]["triage_attempts"] == 1
        assert r["replay"]["triage_attempts"] >= 1
        assert r["live"]["candidates"] == 1
        assert r["replay"]["candidates"] == 0     # 같은 후보는 dedup(repeats)으로 흡수

    def test_reset_removes_only_replay_rows(self, cfg, llm_calls, fac_db,
                                            session_with_segments):
        cfg({"facilitation.enabled": True})
        facilitation.record_observation("past-1", "critic", span="라이브 판정",
                                        db_path=session_with_segments)
        facilitation.replay_session("past-1", db_path=session_with_segments)
        assert facilitation.delete_replay_rows(
            "past-1", db_path=session_with_segments) > 0
        left = facilitation.observations("past-1", db_path=session_with_segments)
        assert [o["persona"] for o in left] == ["critic"]      # 라이브는 남는다

    def test_cli_realtime_transcript_file_is_read_when_db_has_no_segments(
            self, fac_db, tmp_path):
        """폴더 스캐너가 임포트한 세션은 DB 에 세그먼트가 없다(실측: 실볼트 5세션 전부 0건).
        전사는 CLI 산출물 `session_*.jsonl` 에만 있으므로 그쪽도 읽어야 리플레이가 성립한다."""
        import json as _json
        import sqlite3
        out = tmp_path / "realtime_20260715_092241"
        out.mkdir()
        (out / "session_20260715_092241.jsonl").write_text("\n".join([
            _json.dumps({"type": "header", "translate": True}),
            _json.dumps({"type": "segment", "start": 24.9, "end": 29.9,
                         "text": "안녕하세요, 잘 지내시죠?",
                         "text_original": "Hi, how are you doing?"},
                        ensure_ascii=False),
            "",                                            # 빈 줄 — 무시
            "{깨진 json",                                   # 파싱 실패 — 무시
            _json.dumps({"type": "revise", "text": "무시되는 타입"},
                        ensure_ascii=False),
            _json.dumps({"type": "segment", "start": 29.2, "end": 34.2,
                         "text": "", "text_original": "we need communication"},
                        ensure_ascii=False),
        ]), encoding="utf-8")
        c = sqlite3.connect(str(fac_db))
        c.executescript("CREATE TABLE IF NOT EXISTS sessions "
                        "(id TEXT PRIMARY KEY, output_dir TEXT);")
        c.execute("INSERT INTO sessions VALUES (?, ?)", ("cli-1", str(out)))
        c.commit()
        c.close()

        segs = facilitation.session_segments("cli-1", db_path=fac_db)
        # 번역된 회의는 text(한국어)를 쓰고, 비어 있으면 원문으로 폴백한다
        assert [s.text for s in segs] == ["안녕하세요, 잘 지내시죠?",
                                          "we need communication"]
        assert segs[0].t0 == 24.9 and segs[0].t1 == 29.9
        assert all(s.provisional is False for s in segs)

    def test_missing_output_dir_is_not_a_crash(self, fac_db):
        import sqlite3
        c = sqlite3.connect(str(fac_db))
        c.executescript("CREATE TABLE IF NOT EXISTS sessions "
                        "(id TEXT PRIMARY KEY, output_dir TEXT);")
        c.execute("INSERT INTO sessions VALUES (?, ?)", ("gone", "C:/없는/폴더"))
        c.commit()
        c.close()
        assert facilitation.session_segments("gone", db_path=fac_db) == []

    def test_estimate_uses_the_same_pricing_function(self, cfg):
        from meeting_minutes_app.common import pricing
        segs = [facilitation.Utterance("a", 0.0, 100.0, False)]
        est = facilitation.replay_estimate(segs, 25.0, "gpt-4o-mini")
        assert est["triages"] == 5          # 1 + 100//25
        assert est["cost_usd"] == pytest.approx(
            5 * pricing.facilitation_triage_call_cost("gpt-4o-mini"), abs=1e-9)

    def test_missing_session_is_reported_not_crashed(self, cfg, fac_db):
        res = facilitation.replay_session("없는세션", db_path=fac_db)
        assert res["ok"] is False and "세그먼트" in res["message"]


class TestSettingsExposeOnlyWhatWorks:
    """켰는데 아무 일도 안 일어나는 토글을 설정 화면에 올리지 않는다(§3)."""

    #: 값을 읽는 코드가 아직 없는 키 → 구현하는 마일스톤에서 이 목록에서 빼고
    #: config_schema 에 필드를 올린다(그때 이 테스트가 그 짝을 강제한다).
    UNIMPLEMENTED = ("voice_enabled", "web_search_enabled",
                     "web_search_interval")

    def test_unimplemented_keys_are_not_in_settings_ui(self):
        from meeting_minutes_app.common import config_schema
        keys = {f["key"] for f in config_schema.iter_fields()
                if f["section"] == "facilitation"}
        for k in self.UNIMPLEMENTED:
            assert k not in keys, (
                f"{k} 를 설정 화면에 올렸다면 이 값을 읽는 코드가 있어야 한다")

    def test_unimplemented_keys_are_still_documented_in_example(self):
        """UI 에서 내렸다고 사라지면 안 된다 — 기본값·의미는 example 주석에 남는다."""
        import json
        from pathlib import Path
        ex = json.loads(Path("config.example.json").read_text(encoding="utf-8"))
        for k in self.UNIMPLEMENTED:
            assert k in ex["facilitation"]
        assert "_unimplemented_comment" in ex["facilitation"]

    def test_implemented_keys_are_in_settings_ui(self):
        """반대 방향 — 오케스트레이터가 읽는 키는 화면에 있어야 한다."""
        from meeting_minutes_app.common import config_schema
        keys = {f["key"] for f in config_schema.iter_fields()
                if f["section"] == "facilitation"}
        assert {"enabled", "max_level", "triage_model", "triage_period_sec",
                "max_cost_usd_per_meeting",
                # M1 에서 읽는 코드가 생긴 둘 — 짝이 맞아야 한다
                "max_interventions_per_session", "min_confidence"} <= keys


class TestRegistryData:
    """personas.py 는 데이터 전용 — PRD §3 로스터와 어긋나지 않는지 고정."""

    def test_eight_triage_personas_plus_one_periodic(self):
        """트리아지 후보 8종 + 주기 페르소나 1종(중간 요약). 둘은 섞이지 않는다."""
        assert set(personas.PERSONAS) == {
            "facilitator", "scribe", "domain_expert", "fact_checker",
            "devils_advocate", "junior", "senior", "critic", "summarizer"}
        assert [p.key for p in personas.triage_personas()] == [
            "facilitator", "scribe", "domain_expert", "fact_checker",
            "devils_advocate", "junior", "senior", "critic"]
        assert personas.PERSONAS[personas.BRIEF_PERSONA].periodic is True
        assert all(not p.periodic for p in personas.triage_personas())

    def test_risky_personas_have_hard_cap(self):
        assert personas.get_persona("fact_checker").hard_cap == 2
        assert personas.get_persona("critic").hard_cap == 2
        assert personas.get_persona("scribe").hard_cap is None

    def test_prompts_carry_common_rules(self):
        """화자 비귀속·판정 문구 금지는 전 페르소나 공통 제약이다(§8)."""
        for p in personas.all_personas():
            assert personas.COMMON_RULES in p.system_prompt
