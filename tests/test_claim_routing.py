# -*- coding: utf-8 -*-
"""도메인 라우팅 사실검증 (meeting_workflow.claim_verify) 테스트.

퀀텀(주 도메인) 주장 → vault/그래프 우선 + 논문 보강,
도메인 외 주장 → 웹 검증 직행 + fact 노트 축적. 전부 오프라인 (monkeypatch).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meeting_minutes_app.meeting_pipeline import meeting_workflow as mw


QUANTUM_KWS = ["양자", "퀀텀", "quantum", "큐비트"]


def _cfg(overrides):
    base = {
        "wiki.domain_keywords": QUANTUM_KWS,
        "wiki.claim_web_verify": False,
        "wiki.claim_paper_verify": False,
        "wiki.claim_web_verify_out_domain": True,
        "wiki.out_domain_fact_notes": False,
    }
    base.update(overrides)
    return lambda k, d=None: base.get(k, d)


class TestClaimInDomain:
    def test_empty_keywords_everything_in_domain(self, monkeypatch):
        monkeypatch.setattr(mw, "_c", _cfg({"wiki.domain_keywords": []}))
        assert mw._claim_in_domain("아무 주장", [])

    def test_claim_text_match(self, monkeypatch):
        monkeypatch.setattr(mw, "_c", _cfg({}))
        assert mw._claim_in_domain("큐비트 오류율이 0.1%다", [])
        assert not mw._claim_in_domain("참가팀은 30팀이다", [])

    def test_keyword_match_case_insensitive(self, monkeypatch):
        monkeypatch.setattr(mw, "_c", _cfg({}))
        assert mw._claim_in_domain("이 플랫폼은 회로를 자동화한다", ["Quantum", "회로"])


class FakeObs:
    def __init__(self):
        self.notes = []

    def create_reference_note(self, term, description, sources=None, category="", mentioned_by=""):
        self.notes.append((term, category))
        return term


@pytest.fixture
def routed(monkeypatch):
    """claim_verify 의존 함수를 기록형 fake로 대체."""
    calls = {"vault_fetch": [], "web": [], "paper": []}

    monkeypatch.setattr(mw, "_extract_claims", lambda *a, **kw: [
        {"claim": "큐비트 오류율이 0.1%다", "keywords": ["큐비트"]},
        {"claim": "참가팀 기념품 예산은 300만원이다", "keywords": ["예산"]},
    ])

    def fake_fetch(claim, keywords, indexer, obs, current_title=""):
        calls["vault_fetch"].append(claim)
        return [{"title": "양자오류정정", "content": "..."}]
    monkeypatch.setattr(mw, "_fetch_vault_notes_for_claim", fake_fetch)

    monkeypatch.setattr(mw, "_compare_claim_with_notes",
                        lambda claim, notes, llm, topic="": {
                            "claim": claim, "verdict": "unknown",
                            "summary": "판단 불가", "evidence": "", "sources": ["양자오류정정"],
                        })

    def fake_web(claim, llm, topic=""):
        calls["web"].append(claim)
        return {"text": "웹 검증 결과입니다", "sources": [{"title": "src", "url": "http://x"}]}
    monkeypatch.setattr(mw, "_web_verify_claim", fake_web)

    def fake_paper(claim, llm, topic=""):
        calls["paper"].append(claim)
        return {"text": "논문 근거입니다 (Kim et al. 2025)",
                "sources": [{"title": "arXiv:2501.00001", "url": "http://arxiv.org/x"}]}
    monkeypatch.setattr(mw, "_paper_verify_claim", fake_paper)

    return calls


class TestRouting:
    def test_out_domain_skips_vault_goes_web(self, routed, monkeypatch):
        monkeypatch.setattr(mw, "_c", _cfg({}))
        md, results = mw.claim_verify("회의록", llm=object())
        out = next(r for r in results if r["domain"] == "out")
        assert "예산" in out["claim"]
        # vault fetch는 in-domain 주장에만 수행됨
        assert routed["vault_fetch"] == ["큐비트 오류율이 0.1%다"]
        # out-domain은 웹 검증 직행
        assert out["claim"] in routed["web"]
        assert "🌍 **[도메인 외]**" in md

    def test_out_domain_web_gate_off(self, routed, monkeypatch):
        monkeypatch.setattr(mw, "_c", _cfg({"wiki.claim_web_verify_out_domain": False}))
        mw.claim_verify("회의록", llm=object())
        assert routed["web"] == []

    def test_in_domain_unknown_gets_paper_verify(self, routed, monkeypatch):
        monkeypatch.setattr(mw, "_c", _cfg({"wiki.claim_paper_verify": True,
                                            "wiki.claim_web_verify_out_domain": False}))
        md, results = mw.claim_verify("회의록", llm=object())
        assert routed["paper"] == ["큐비트 오류율이 0.1%다"]
        in_r = next(r for r in results if r["domain"] == "in")
        assert "논문 근거입니다" in in_r["paper_opinion"]
        assert "📄 논문 근거" in md
        assert "arXiv:2501.00001" in md

    def test_paper_verify_off_by_default(self, routed, monkeypatch):
        monkeypatch.setattr(mw, "_c", _cfg({}))
        mw.claim_verify("회의록", llm=object())
        assert routed["paper"] == []

    def test_out_domain_fact_note_accumulated(self, routed, monkeypatch):
        monkeypatch.setattr(mw, "_c", _cfg({"wiki.out_domain_fact_notes": True}))
        obs = FakeObs()
        mw.claim_verify("회의록", llm=object(), obs=obs)
        assert len(obs.notes) == 1
        term, category = obs.notes[0]
        assert category == "사실검증"
        assert "예산" in term

    def test_fact_note_gate_off(self, routed, monkeypatch):
        monkeypatch.setattr(mw, "_c", _cfg({}))
        obs = FakeObs()
        mw.claim_verify("회의록", llm=object(), obs=obs)
        assert obs.notes == []

    def test_no_domain_keywords_preserves_legacy_path(self, routed, monkeypatch):
        monkeypatch.setattr(mw, "_c", _cfg({"wiki.domain_keywords": []}))
        md, results = mw.claim_verify("회의록", llm=object())
        # 전부 in-domain → 모든 주장이 vault 경로
        assert len(routed["vault_fetch"]) == 2
        assert all(r["domain"] == "in" for r in results)


class FakeLLM:
    """.chat()에 규칙 기반 응답 — 실제 API 없이 LLM 재분류 로직 검증."""

    def __init__(self, verdict):
        self.verdict = verdict  # "in" | "out" | 예외 발생용 None
        self.calls = []

    def chat(self, system, user, temp=0.0, max_tokens=5):
        self.calls.append(user)
        if self.verdict is None:
            raise RuntimeError("모델 호출 실패")
        return self.verdict


class TestDomainClassifyLLM:
    def test_hard_match_skips_llm_call(self, monkeypatch):
        """키워드가 이미 매칭되면 LLM을 호출할 필요가 없다."""
        monkeypatch.setattr(mw, "_c", _cfg({}))
        llm = FakeLLM("out")  # 호출되면 오분류를 유발할 응답
        assert mw._claim_in_domain("양자컴퓨팅 큐비트 오류율", [], llm) is True
        assert llm.calls == []

    def test_adjacent_concept_reclassified_as_in_domain(self, monkeypatch):
        """'볼츠만 머신'은 키워드 매칭엔 안 걸리지만 LLM이 인접 개념으로 판단."""
        monkeypatch.setattr(mw, "_c", _cfg({}))
        llm = FakeLLM("in")
        result = mw._claim_in_domain("볼츠만 머신은 확률분포를 모델링하는 신경망이다", [], llm,
                                     topic="양자 머신러닝 세미나")
        assert result is True
        assert len(llm.calls) == 1
        assert "볼츠만 머신" in llm.calls[0]

    def test_truly_unrelated_stays_out_domain(self, monkeypatch):
        monkeypatch.setattr(mw, "_c", _cfg({}))
        llm = FakeLLM("out")
        assert mw._claim_in_domain("기념품 예산은 300만원이다", [], llm) is False

    def test_llm_failure_falls_back_to_out(self, monkeypatch):
        """LLM 호출이 실패하면 품질을 낮춰 넘기지 않고 보수적으로 도메인 외 유지."""
        monkeypatch.setattr(mw, "_c", _cfg({}))
        llm = FakeLLM(None)
        assert mw._claim_in_domain("볼츠만 머신 관련 주장", [], llm) is False

    def test_gate_off_skips_llm_entirely(self, monkeypatch):
        monkeypatch.setattr(mw, "_c", _cfg({"wiki.domain_classify_llm": False}))
        llm = FakeLLM("in")
        assert mw._claim_in_domain("볼츠만 머신 관련 주장", [], llm) is False
        assert llm.calls == []

    def test_no_domain_keywords_never_calls_llm(self, monkeypatch):
        monkeypatch.setattr(mw, "_c", _cfg({"wiki.domain_keywords": []}))
        llm = FakeLLM("out")
        assert mw._claim_in_domain("무엇이든", [], llm) is True
        assert llm.calls == []

    def test_malformed_llm_response_falls_back(self, monkeypatch):
        monkeypatch.setattr(mw, "_c", _cfg({}))
        llm = FakeLLM("잘 모르겠습니다")  # in/out 어느 쪽도 아닌 응답
        assert mw._claim_in_domain("볼츠만 머신", [], llm) is False


class TestExtractClaimsChunking:
    """_extract_claims 청크 분할 + 라운드로빈 병합 (트랙 B)."""

    def test_single_chunk_when_within_source_max(self, monkeypatch):
        monkeypatch.setattr(mw, "_c", _cfg({"wiki.claim_source_max_chars": 5000}))
        calls = []
        monkeypatch.setattr(
            mw, "_extract_claims_chunk",
            lambda chunk, llm, topic, cap: (calls.append(cap), [])[1])
        mw._extract_claims("짧은 회의록", llm=object(), max_claims=8)
        assert calls == [8]  # 청크 분할 없이 전체를 한 번에, cap=max_claims

    def test_chunks_and_round_robins_when_over_source_max(self, monkeypatch):
        monkeypatch.setattr(mw, "_c", _cfg({"wiki.claim_source_max_chars": 50}))
        monkeypatch.setattr(mw, "_split_script_chunks",
                            lambda text, max_chars: ["chunk1", "chunk2", "chunk3"])

        call_log = []

        def fake_extract(chunk, llm, topic, cap):
            call_log.append((chunk, cap))
            idx = chunk[-1]
            return [{"claim": f"claim{idx}-{i}"} for i in range(cap)]

        monkeypatch.setattr(mw, "_extract_claims_chunk", fake_extract)
        result = mw._extract_claims("x" * 60, llm=object(), max_claims=6)

        assert len(call_log) == 3
        assert all(cap == 2 for _, cap in call_log)  # ceil(6/3)
        assert len(result) == 6
        claims_text = [r["claim"] for r in result]
        # 라운드로빈이므로 세 청크 모두에서 최소 1개씩 포함되어야 함(앞쪽에만 몰리지 않음)
        assert any(c.startswith("claim1") for c in claims_text)
        assert any(c.startswith("claim2") for c in claims_text)
        assert any(c.startswith("claim3") for c in claims_text)

    def test_dedup_across_chunks(self, monkeypatch):
        monkeypatch.setattr(mw, "_c", _cfg({"wiki.claim_source_max_chars": 10}))
        monkeypatch.setattr(mw, "_split_script_chunks", lambda text, max_chars: ["c1", "c2"])
        monkeypatch.setattr(
            mw, "_extract_claims_chunk",
            lambda chunk, llm, topic, cap: [{"claim": "동일한 주장입니다"}])
        result = mw._extract_claims("x" * 20, llm=object(), max_claims=8)
        assert len(result) == 1  # 두 청크에서 같은 주장 → 중복 제거
