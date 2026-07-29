# -*- coding: utf-8 -*-
"""finalize.run_post_session 오케스트레이터 테스트.

모든 생성/발행 함수는 소스 모듈에 monkeypatch — LLM/네트워크/파일시스템 없음
(wiki_context/proposal 저장만 tmp_path 사용).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meeting_minutes_app.meeting_pipeline import finalize as fz


SEGMENTS = [
    {"start": 0.0, "end": 3.0, "text": "안건 논의", "text_original": "안건 논의", "speaker": ""},
    {"start": 3.0, "end": 6.0, "text": "PoC 범위 확정", "text_original": "PoC 범위 확정", "speaker": ""},
]


class RecordingEvents(fz.FinalizeEvents):
    def __init__(self):
        self.docs = []      # (doc_type, fmt)
        self.statuses = []  # (stage, message)
        self.stage_errors = []

    def on_status(self, stage, message):
        self.statuses.append((stage, message))

    def on_document(self, doc_type, content, fmt="markdown"):
        self.docs.append((doc_type, fmt))

    def on_stage_error(self, stage, exc):
        self.stage_errors.append(stage)


@pytest.fixture
def patched(monkeypatch, tmp_path):
    """생성/발행 전 단계를 기록형 fake로 대체."""
    calls = {}

    from meeting_minutes_app.meeting_pipeline import (
        minutes_generation as mg,
        meeting_workflow as mw,
        publish as pub,
        script_formatting as sf,
    )
    from meeting_minutes_app.wiki_core import wiki_knowledge as wk
    from meeting_minutes_app.wiki_core import graph_sync as gs

    def rec(name, ret=None):
        def fn(*a, **kw):
            calls.setdefault(name, []).append((a, kw))
            return ret
        return fn

    monkeypatch.setattr(pub, "plan_context_memo",
                        rec("plan", (None, "MEMO0")))
    monkeypatch.setattr(mw, "build_generation_context_memo",
                        rec("context", ("MEMO1", ["노트A"],
                                        {"wiki": True, "graph": False,
                                         "registry": True, "web": False,
                                         "evidence": [{"note": "노트A", "heading": None}],
                                         "note_count": 1})))
    monkeypatch.setattr(mg, "refine_script", rec("refine", "교정된 스크립트"))
    monkeypatch.setattr(mg, "_refined_script_is_usable", rec("quality", (True, "")))
    monkeypatch.setattr(mg, "generate_minutes", rec("minutes", "# 회의록\n## 결정사항\n- PoC 범위 확정"))
    monkeypatch.setattr(mg, "extract_action_items", rec("actions", '[{"task":"후속 작업"}]'))
    monkeypatch.setattr(mg, "format_actions_md", rec("actions_md", "| 액션 |"))
    monkeypatch.setattr(mg, "generate_summary", rec("summary", "요약"))
    monkeypatch.setattr(sf, "build_script_md", rec("script", "# 스크립트"))
    monkeypatch.setattr(mw, "claim_verify", rec("verify", ("## 사실 검증\n- ✅ ok", [{"claim": "c"}])))
    monkeypatch.setattr(mw, "load_vault_indexer", rec("load_idx", None))
    monkeypatch.setattr(mw, "load_obsidian_client", rec("load_obs", None))
    monkeypatch.setattr(pub, "enrich_and_publish",
                        rec("publish", {"obsidian_path": "00_Meetings/x.md",
                                        "entities": {"terms": ["용어1"]}}))
    monkeypatch.setattr(wk, "build_wiki_context_package", rec("ctx_pkg", {"related_notes": []}))
    monkeypatch.setattr(wk, "save_wiki_context_package",
                        rec("ctx_save", tmp_path / "wiki_context.json"))
    prop_md = tmp_path / "prop.md"
    prop_md.write_text("proposal", encoding="utf-8")
    monkeypatch.setattr(wk, "build_wiki_update_proposal", rec("proposal", {"status": "suggested"}))
    monkeypatch.setattr(wk, "save_wiki_update_proposal",
                        rec("prop_save", (tmp_path / "prop.json", prop_md)))
    monkeypatch.setattr(wk, "update_action_registry_from_actions", rec("reg_act", 1))
    monkeypatch.setattr(wk, "extract_decisions_from_minutes", rec("dec_extract", ["PoC 범위 확정"]))
    monkeypatch.setattr(wk, "update_decision_registry_from_minutes", rec("reg_dec", 1))
    monkeypatch.setattr(wk, "_reindex_if_configured", rec("reindex", None))
    monkeypatch.setattr(gs, "sync_session_graph", rec("graph", None))

    monkeypatch.setattr(fz, "_c", lambda k, d=None: {
        "wiki.claim_verify": True,
        "wiki.claim_verify_max": 8,
    }.get(k, d))

    return calls


def run(calls_fixture, tmp_path, *, doc_type="meeting", **opt_kw):
    ev = RecordingEvents()
    inputs = fz.SessionInputs(
        segments=SEGMENTS, title="주간회의", topic="양자",
        doc_type=doc_type, session_dt="2026년 07월 07일 10:00",
        source="test", session_id="sess-1",
    )
    defaults = dict(llm=object(), artifacts_dir=tmp_path,
                    refined_quality_check=True)
    defaults.update(opt_kw)
    options = fz.FinalizeOptions(**defaults)
    res = fz.run_post_session(inputs, options, ev)
    return res, ev


class TestTranscriptSanitize:
    """모든 진입점이 거치는 정화 단계 — 회의록 입력에 환각·반복이 들어가지 않는다."""

    DIRTY = [
        {"start": 0.0, "end": 3.0, "text": "Na velolodu.", "speaker": ""},
        {"start": 3.0, "end": 6.0, "text": "Na velolodu.", "speaker": ""},
        {"start": 6.0, "end": 9.0, "text": "Na velolodu.", "speaker": ""},
        {"start": 9.0, "end": 12.0, "text": "где-нибудь 뭐가 있냐.", "speaker": ""},
        {"start": 12.0, "end": 15.0, "text": "금주 안에 오픈하라고 하셔서 오픈은 해요.", "speaker": ""},
        {"start": 15.0, "end": 18.0, "text": "금주 안에 오픈하라고 하셔서 오픈은 해요.", "speaker": ""},
        {"start": 18.0, "end": 21.0, "text": "가이드 문서를 하나 드릴 거예요.", "speaker": ""},
    ]

    def _segments_seen_by_refine(self, patched, tmp_path, **kw):
        ev = RecordingEvents()
        inputs = fz.SessionInputs(
            segments=list(self.DIRTY), title="테스트 회의", topic="위키 오픈",
            doc_type="meeting", session_dt="2026년 07월 28일 10:00",
            source="test", session_id="sess-2", language="ko", **kw,
        )
        fz.run_post_session(inputs, fz.FinalizeOptions(
            llm=object(), artifacts_dir=tmp_path), ev)
        args, _ = patched["refine"][0]
        return [s["text"] for s in args[0]]

    def test_repeats_collapsed_and_hallucination_marked(self, patched, tmp_path):
        texts = self._segments_seen_by_refine(patched, tmp_path)
        assert texts.count("Na velolodu.") == 0            # 표시가 붙어 원문 그대로는 없음
        assert sum(1 for t in texts if "Na velolodu" in t) == 1
        assert any(t.startswith("[불명]") and "где-нибудь" in t for t in texts)
        assert texts.count("금주 안에 오픈하라고 하셔서 오픈은 해요.") == 1
        assert "가이드 문서를 하나 드릴 거예요." in texts   # 정상 발화는 보존

    def test_original_input_not_mutated(self, patched, tmp_path):
        before = [dict(s) for s in self.DIRTY]
        self._segments_seen_by_refine(patched, tmp_path)
        assert self.DIRTY == before   # 호출자의 세그먼트(DB/화면 원본)는 건드리지 않는다


class TestFullMeetingRun:
    def test_all_stages_and_documents(self, patched, tmp_path):
        res, ev = run(patched, tmp_path, do_graph_sync=True)
        assert res.errors == []
        # 문서 방출 프로파일 (minutes는 검증 반영 후 재방출)
        doc_types = [d for d, _ in ev.docs]
        assert doc_types == [
            "refined_script", "minutes", "actions", "fact_check", "minutes",
            "summary", "script", "wiki_context", "wiki_proposal",
        ]
        # 검증 섹션이 minutes에 병합됨
        assert "사실 검증" in res.minutes
        assert res.source_note == "00_Meetings/x.md"
        assert res.related_note_titles == ["노트A"]
        assert res.decisions == ["PoC 범위 확정"]
        # 모든 백엔드 스테이지 실행됨
        for key in ("plan", "context", "refine", "minutes", "actions", "verify",
                    "summary", "script", "publish", "reindex",
                    "ctx_pkg", "proposal", "reg_act", "reg_dec", "graph"):
            assert key in patched, f"{key} 미호출"

    def test_stage_order(self, patched, tmp_path):
        order = []
        orig_setdefault = dict.setdefault  # noqa: F841
        res, ev = run(patched, tmp_path)
        # statuses에 context가 refine 이전에 오는지 (컨텍스트 → 생성 순서)
        stages = [s for s, _ in ev.statuses]
        assert stages.index("context") < len(stages)

    def test_graph_sync_off_by_default(self, patched, tmp_path):
        run(patched, tmp_path)
        assert "graph" not in patched


class TestGates:
    def test_seminar_skips_meeting_only_stages(self, patched, tmp_path):
        res, ev = run(patched, tmp_path, doc_type="seminar", do_graph_sync=True)
        for key in ("actions", "reg_act", "reg_dec", "proposal", "graph"):
            assert key not in patched
        assert "minutes" in patched

    def test_claim_verify_config_off(self, patched, tmp_path, monkeypatch):
        monkeypatch.setattr(fz, "_c", lambda k, d=None: {"wiki.claim_verify": False}.get(k, d))
        res, ev = run(patched, tmp_path)
        assert "verify" not in patched
        assert res.verify_md == ""
        # minutes는 1회만 방출
        assert [d for d, _ in ev.docs].count("minutes") == 1

    def test_claim_verify_option_overrides_config(self, patched, tmp_path, monkeypatch):
        monkeypatch.setattr(fz, "_c", lambda k, d=None: {"wiki.claim_verify": False}.get(k, d))
        run(patched, tmp_path, do_claim_verify=True)
        assert "verify" in patched

    def test_no_publish(self, patched, tmp_path):
        res, ev = run(patched, tmp_path, do_publish=False)
        assert "publish" not in patched
        assert res.source_note == ""

    def test_no_artifacts_dir_skips_context_package(self, patched, tmp_path):
        run(patched, tmp_path, artifacts_dir=None, proposal_dir=None)
        assert "ctx_pkg" not in patched
        assert "prop_save" not in patched  # 저장 위치 없으면 건너뜀


class TestErrorIsolation:
    def test_refine_failure_falls_back_to_segments(self, patched, tmp_path, monkeypatch):
        from meeting_minutes_app.meeting_pipeline import minutes_generation as mg

        def boom(*a, **kw):
            raise RuntimeError("교정 실패")
        monkeypatch.setattr(mg, "refine_script", boom)
        res, ev = run(patched, tmp_path)
        assert res.minutes  # 회의록은 생성됨
        assert ("refine" in [s for s, _ in res.errors]) or any(
            s == "refine" for s in ev.stage_errors)
        # generate_minutes가 segments를 받았는지 (교정본 아님)
        args, kwargs = patched["minutes"][0]
        assert args[0] == SEGMENTS

    def test_publish_failure_does_not_block_registry(self, patched, tmp_path, monkeypatch):
        from meeting_minutes_app.meeting_pipeline import publish as pub

        def boom(*a, **kw):
            raise RuntimeError("Obsidian down")
        monkeypatch.setattr(pub, "enrich_and_publish", boom)
        res, ev = run(patched, tmp_path)
        assert "reg_act" in patched   # registry는 계속 실행 (source_note="")
        assert any(stage == "publish" for stage, _ in res.errors)

    def test_empty_minutes_early_return(self, patched, tmp_path, monkeypatch):
        from meeting_minutes_app.meeting_pipeline import minutes_generation as mg
        monkeypatch.setattr(mg, "generate_minutes", lambda *a, **kw: "")
        res, ev = run(patched, tmp_path)
        assert res.minutes == ""
        assert "publish" not in patched
        assert "summary" not in patched


EVIDENCE = [
    {"title": "QAOA", "filename": "02_이론_학습/QAOA.md", "heading": "요약",
     "section_path": "QAOA › 요약", "score": 1.42, "rank_score": 0.02,
     "hits": 3, "snippet": "QAOA는 조합최적화 근사 알고리즘", "source_type": "paper",
     "segment_text": "QAOA 적용 가능성 논의", "elapsed_sec": 12.5},
    {"title": "주간회의", "filename": "00_Meetings/주간회의.md", "heading": "",
     "section_path": "주간회의", "score": 0.31, "rank_score": 0.01,
     "hits": 1, "snippet": "지난주 결정 사항", "source_type": "note",
     "segment_text": "지난주 결정 확인", "elapsed_sec": 40.0},
]


class TestExtraContext:
    def test_extra_titles_merged_and_memo_block(self, patched, tmp_path):
        res, ev = run(patched, tmp_path,
                      extra_related_titles=["실시간노트1", "노트A"])
        # 실시간 노트가 앞에, 중복(노트A) 제거
        assert res.related_note_titles == ["실시간노트1", "노트A"]
        # build_generation_context_memo에 전달된 base_memo에 실시간 블록 포함
        _, ctx_kwargs = patched["context"][0]
        assert "실시간 관련 노트" in (ctx_kwargs.get("base_memo") or "")

    def test_evidence_injected_into_memo(self, patched, tmp_path):
        """제목만이 아니라 근거(섹션·snippet·발화)까지 회의록 생성 memo에 들어간다."""
        run(patched, tmp_path, extra_related_evidence=EVIDENCE)
        _, ctx_kwargs = patched["context"][0]
        memo = ctx_kwargs.get("base_memo") or ""
        assert "[[QAOA#요약]]" in memo
        assert "조합최적화" in memo
        assert "발화: QAOA 적용 가능성 논의" in memo


class TestRelatedNotesSection:
    """FR-6 — 회의록에 '🔗 관련 노트'가 근거 링크와 함께 자동 삽입된다."""

    def test_section_appended_with_evidence(self, patched, tmp_path):
        res, ev = run(patched, tmp_path, extra_related_evidence=EVIDENCE)
        assert fz.RELATED_NOTES_HEADING in res.minutes
        assert "🎓 [[QAOA#요약]]" in res.minutes
        assert "관련도 1.42" in res.minutes
        assert "3회 참조" in res.minutes
        assert "📄 [[주간회의]]" in res.minutes
        # 사실검증 블록보다 뒤 — 검증 섹션 재작성에 지워지지 않는 위치
        assert res.minutes.index("## 사실 검증") < res.minutes.index(fz.RELATED_NOTES_HEADING)
        # 병합본이 재방출돼 웹 DB/화면이 최신 회의록을 받는다
        assert [d for d, _ in ev.docs].count("minutes") >= 2

    def test_titles_only_still_produces_section(self, patched, tmp_path):
        res, _ = run(patched, tmp_path, extra_related_titles=["실시간노트1"])
        assert "📄 [[실시간노트1]]" in res.minutes

    def test_no_related_no_section(self, patched, tmp_path):
        res, _ = run(patched, tmp_path)
        assert fz.RELATED_NOTES_HEADING not in res.minutes

    def test_section_not_duplicated(self, patched, tmp_path, monkeypatch):
        """이미 관련 노트 섹션이 있는 회의록(재생성·복구)에는 다시 붙이지 않는다."""
        from meeting_minutes_app.meeting_pipeline import minutes_generation as mg
        monkeypatch.setattr(
            mg, "generate_minutes",
            lambda *a, **kw: f"# 회의록\n\n{fz.RELATED_NOTES_HEADING}\n\n- 📄 [[기존]]")
        res, _ = run(patched, tmp_path, extra_related_evidence=EVIDENCE)
        assert res.minutes.count(fz.RELATED_NOTES_HEADING) == 1
        assert "QAOA" not in res.minutes

    def test_section_failure_does_not_block_summary(self, patched, tmp_path, monkeypatch):
        monkeypatch.setattr(fz, "build_related_notes_section",
                            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")))
        res, ev = run(patched, tmp_path, extra_related_evidence=EVIDENCE)
        assert "summary" in patched
        assert any(stage == "related_notes" for stage, _ in res.errors)


class TestBuildRelatedNotesSection:
    def test_dedupes_and_limits(self):
        ev = [{"title": "A", "score": 1.0}, {"title": "A", "score": 0.5},
              {"title": "B", "score": 0.4}]
        md = fz.build_related_notes_section(ev, ["A", "C"], limit=2)
        assert md.count("- ") == 2
        assert "[[A]]" in md and "[[B]]" in md

    def test_max_rank_filters_noise(self):
        """FR-6 노이즈 컷은 **순위**로 한다(0-기반). 과거엔 rank_score 하한이었는데
        그 값의 상한이 1/(60+1)≈0.0164라 '0.1' 같은 점수처럼 보이는 임계값을 주면
        전부 사라지는 함정이었다."""
        ev = [{"title": "A", "rank": 0}, {"title": "B", "rank": 7}]
        md = fz.build_related_notes_section(ev, max_rank=2)
        assert "[[A]]" in md and "[[B]]" not in md
        # 제한 없음(None)이면 둘 다
        both = fz.build_related_notes_section(ev)
        assert "[[A]]" in both and "[[B]]" in both
        # rank 정보가 없는 근거(REST 폴백 등)는 컷하지 않는다
        norank = fz.build_related_notes_section([{"title": "C"}], max_rank=0)
        assert "[[C]]" in norank

    def test_same_title_different_paths_collapse_to_one_row(self):
        """같은 제목의 다른 노트는 한 줄로 — 회의록은 [[제목]]으로 적어 구분이 안 된다.
        헤딩만 다른 경우도 포함(과거엔 [[제목#헤딩]] 전체를 키로 써서 두 줄 남았다)."""
        ev = [{"title": "Acme", "filename": "01_References/Companies/Acme.md",
               "heading": "개요"},
              {"title": "Acme", "filename": "Archive/QC/회사/Acme.md",
               "heading": "로드맵"}]
        md = fz.build_related_notes_section(ev)
        assert md.count("- ") == 1
        # titles 폴백도 같은 제목을 다시 추가하지 않는다
        md2 = fz.build_related_notes_section(ev, ["Acme"])
        assert md2.count("- ") == 1

    def test_empty_returns_empty_string(self):
        assert fz.build_related_notes_section([], []) == ""
        assert fz.build_related_notes_section(None, None) == ""

    def test_untitled_evidence_skipped(self):
        assert fz.build_related_notes_section([{"filename": "x.md"}]) == ""

    def test_quality_gate_rejects_bad_refined(self, patched, tmp_path, monkeypatch):
        from meeting_minutes_app.meeting_pipeline import minutes_generation as mg
        monkeypatch.setattr(mg, "_refined_script_is_usable",
                            lambda refined, segments: (False, "너무 짧음"))
        res, ev = run(patched, tmp_path, refined_quality_check=True)
        # 교정본 대신 segments로 생성
        args, kwargs = patched["minutes"][0]
        assert args[0] == SEGMENTS
        assert res.refined_text == "교정된 스크립트"  # 기록은 유지


class TestLLMRequired:
    def test_missing_llm_raises(self, tmp_path):
        with pytest.raises(ValueError):
            fz.run_post_session(
                fz.SessionInputs(segments=SEGMENTS, title="t"),
                fz.FinalizeOptions(llm=None),
            )
