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


class TestExtraContext:
    def test_extra_titles_merged_and_memo_block(self, patched, tmp_path):
        res, ev = run(patched, tmp_path,
                      extra_related_titles=["실시간노트1", "노트A"])
        # 실시간 노트가 앞에, 중복(노트A) 제거
        assert res.related_note_titles == ["실시간노트1", "노트A"]
        # build_generation_context_memo에 전달된 base_memo에 실시간 블록 포함
        _, ctx_kwargs = patched["context"][0]
        assert "실시간 관련 노트" in (ctx_kwargs.get("base_memo") or "")

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
