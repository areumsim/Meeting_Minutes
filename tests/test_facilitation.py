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

    def test_span_key_normalizes_whitespace_and_case(self):
        a = facilitation.span_key("critic", "이 방식이  항상 더 빠릅니다")
        b = facilitation.span_key("critic", "이 방식이 항상 더 빠릅니다 ")
        assert a == b
        assert a != facilitation.span_key("junior", "이 방식이 항상 더 빠릅니다")
        # 인용이 비면 트리거 유형으로 대체 — 빈 인용끼리 같은 것으로 본다
        assert (facilitation.span_key("critic", "", "논리 비약")
                == facilitation.span_key("critic", "   ", "논리 비약"))


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
