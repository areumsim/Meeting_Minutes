"""
api/tools.py — 텍스트 분석 · 회의록 재생성 · 볼트 인덱스 재빌드 · 준비 브리핑

CLI 전용이던 기능들을 web에 노출한다. 무거운 작업은 BackgroundTasks 로 비동기 처리하고
세션 상태(processing/completed/error)로 진행을 알린다.
"""

import os
import tempfile
import traceback
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, HTTPException

from web.backend import database as db

router = APIRouter(tags=["tools"])


# ── 공용 헬퍼 ─────────────────────────────────────
def _llm(preferred: str | None = None):
    from meeting_minutes_app.meeting_pipeline import meeting_minutes as mm
    from meeting_minutes_app.common import config_loader as cfg
    return mm.LLMClient(preferred=preferred or cfg.get("models.llm", "gpt") or "gpt")


def _make_output_dir(title: str) -> str:
    from meeting_minutes_app.meeting_pipeline import meeting_minutes as mm
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = "".join(c for c in (title or "text") if c.isalnum() or c in " _-").strip()[:50] or "text"
    out = os.path.join(mm._c("output_dir", "./output") or "./output", f"{ts}_{safe}")
    os.makedirs(out, exist_ok=True)
    return out


# ── 1) 텍스트 → 회의록 (STT 건너뜀) ───────────────
def _run_text(session_id: str, text: str, title: str, topic: str, doc_type: str):
    try:
        from meeting_minutes_app.meeting_pipeline import minutes_generation as mg
        llm = _llm()
        out = _make_output_dir(title)
        db.update_session_status(session_id, "processing", output_dir=out)

        mg.save(text, os.path.join(out, "transcript.md"), "전사")
        minutes = mg.generate_minutes(text, llm, doc_type=doc_type, topic=topic, title=title)
        mg.save(minutes, os.path.join(out, "minutes.md"), "회의록")
        summary = mg.generate_summary(minutes, llm, doc_type=doc_type, topic=topic)
        mg.save(summary, os.path.join(out, "summary.md"), "요약")
        actions = mg.extract_action_items(minutes, llm, doc_type=doc_type)
        if actions:
            mg.save(mg.format_actions_md(actions), os.path.join(out, "actions.md"), "액션")

        db.import_output_files(session_id, out)
        db.update_session_status(session_id, "completed")
    except Exception:
        traceback.print_exc()
        db.update_session_status(session_id, "error")


@router.post("/process-text")
def process_text(payload: dict, background_tasks: BackgroundTasks):
    """붙여넣은 텍스트를 회의록/요약/액션으로 변환(서버 처리, 키 미노출)."""
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="텍스트가 비어 있습니다.")
    title = payload.get("title") or "텍스트 입력"
    topic = payload.get("topic") or ""
    doc_type = payload.get("type") or "meeting"
    session_id = db.create_session(
        title=title, topic=topic, doc_type=doc_type,
        language="", translate=False, source="web", mode="text",
    )
    background_tasks.add_task(_run_text, session_id, text, title, topic, doc_type)
    return {"sessionId": session_id, "status": "processing"}


# ── 2) 회의록 재생성(노트 반영) ───────────────────
def _run_regenerate(session_id: str, notes: str):
    try:
        from meeting_minutes_app.meeting_pipeline import meeting_minutes as mm, pipeline
        from web.backend.api.batch import _build_args
        sess = db.get_session(session_id) or {}
        out = sess.get("output_dir")
        if not out or not os.path.isdir(out):
            db.update_session_status(session_id, "error")
            return
        db.update_session_status(session_id, "processing")

        args = _build_args(title=sess.get("title", ""), topic=sess.get("topic", ""),
                           doc_type=sess.get("type", "meeting"))
        args.resume = True               # STT 건너뛰고 기존 전사(segments.json/transcript.md) 재사용
        args.custom_prompt = notes or ""
        if not args.model:
            args.model = mm.DEFAULT_STT_MODEL
        llm = mm.LLMClient(preferred=args.llm)

        with tempfile.TemporaryDirectory() as work:
            pipeline.process_single(
                input_path=sess.get("file_path") or out, args=args, llm=llm,
                output_dir=out, title=sess.get("title") or "회의", work_dir=work,
                memo=notes or None,
            )
        db.import_output_files(session_id, out)
        db.update_session_status(session_id, "completed")
    except Exception:
        traceback.print_exc()
        db.update_session_status(session_id, "error")


@router.post("/sessions/{session_id}/regenerate")
def regenerate(session_id: str, payload: dict, background_tasks: BackgroundTasks):
    """기존 세션의 전사를 재사용해 노트를 반영, 회의록을 다시 생성."""
    sess = db.get_session(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")
    if not sess.get("output_dir"):
        raise HTTPException(status_code=400, detail="재생성할 전사 데이터가 없습니다.")
    background_tasks.add_task(_run_regenerate, session_id, payload.get("notes", ""))
    return {"sessionId": session_id, "status": "processing"}


# ── 3) 볼트 인덱스 재빌드 ─────────────────────────
@router.post("/reindex")
def reindex():
    """Obsidian 볼트(.md 폴더) 검색 인덱스를 다시 만든다. 폴더-only 위키에 필수."""
    try:
        from meeting_minutes_app.wiki_core.vault_indexer import VaultIndexer
        idx = VaultIndexer.from_config()
        if not idx:
            return {"ok": False, "message": "볼트 폴더가 설정되지 않았습니다. 설정에서 Obsidian 볼트 폴더를 지정하세요."}
        n = idx.build(verbose=False)
        return {"ok": True, "message": f"인덱스 재빌드 완료 — 노트 {n}개"}
    except Exception as e:
        traceback.print_exc()
        return {"ok": False, "message": f"재빌드 실패: {e}"}


# ── 4) 회의 준비 브리핑 ───────────────────────────
@router.post("/prep-brief")
def prep_brief(payload: dict):
    """제목/주제로 볼트·레지스트리를 검색해 준비 브리핑 마크다운을 생성."""
    title = (payload.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=422, detail="제목을 입력하세요.")
    topic = payload.get("topic") or ""
    try:
        from meeting_minutes_app.wiki_core import wiki_knowledge as wk
        from meeting_minutes_app.wiki_core import vault_retrieval as vr

        indexer = vr.load_vault_indexer()
        try:
            obs = vr.load_obsidian_client()
        except Exception:
            obs = None

        regular, papers = wk._get_brief_related_notes(title, topic, indexer, obs, limit=5, memo="")
        action_reg = wk.load_action_registry(wk.DATA_DIR / "action_registry.json")
        decision_reg = wk.load_decision_registry(wk.DATA_DIR / "decision_registry.json")
        # 필터 함수는 registry dict가 아니라 내부 리스트를 받고, 2번째 인자는 topic 문자열.
        open_actions = wk._filter_actions_by_topic(action_reg.get("actions", []), topic, limit=10)
        recent_decisions = wk._filter_decisions_by_topic(decision_reg.get("decisions", []), topic, limit=10)

        now = datetime.now()
        brief = wk.build_prep_brief(
            title, topic, now.strftime("%y%m%d"), now.strftime("%Y-%m-%d"),
            regular, papers, open_actions, recent_decisions,
        )
        return {"ok": True, "brief": brief}
    except Exception as e:
        traceback.print_exc()
        return {"ok": False, "message": f"브리핑 생성 실패: {e}"}
