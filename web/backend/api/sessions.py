"""
api/sessions.py — 세션 CRUD API
"""

from fastapi import APIRouter, Query, HTTPException
from typing import Optional

from web.backend import database as db

router = APIRouter(tags=["sessions"])


@router.get("/sessions")
def list_sessions(
    search: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
):
    return db.list_sessions(search=search or "", type_filter=type or "")


@router.get("/sessions/{session_id}")
def get_session(session_id: str):
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    segments = db.get_segments(session_id)
    documents = db.get_documents(session_id)
    return {"session": session, "segments": segments, "documents": documents}


@router.get("/sessions/{session_id}/cost")
def get_session_cost(session_id: str):
    """세션 비용 추정(USD). 오디오 길이(초)×STT 단가 + 번역 + 회의록 생성 고정치."""
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    segs = db.get_segments(session_id)
    dur = session.get("duration_sec") or 0
    if not dur and segs:
        dur = max((s.get("end_time") or 0) for s in segs)
    try:
        from meeting_minutes_app.common import config_loader as cfg
        from meeting_minutes_app.common import pricing
        stt_model = cfg.get("models.stt", "gpt-4o-mini-transcribe") or "gpt-4o-mini-transcribe"
        llm = cfg.get("models.llm", "gpt") or "gpt"
        # 회의록 생성 모델(minutes_model) 우선, 없으면 gpt_model/claude_model
        if str(llm).lower().startswith("claude"):
            minutes_model = cfg.get("models.claude_model", None)
        else:
            minutes_model = cfg.get("models.minutes_model", None) or cfg.get("models.gpt_model", None)
    except Exception as e:
        return {"ok": False, "message": f"가격 정보 로드 실패: {e}"}
    cost = pricing.estimate_session_cost(
        dur, stt_model,
        translate=bool(session.get("translate")),
        include_minutes=bool(segs),   # 문서가 생성된(=완료된) 세션만 회의록 비용 포함
        llm=llm, minutes_model=minutes_model,
    )
    return {"ok": True, "stt_model": stt_model, "llm": llm, **cost}


@router.get("/sessions/{session_id}/status")
def get_session_status(session_id: str):
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"id": session_id, "status": session["status"]}


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str):
    db.delete_session(session_id)
    return {"success": True}


@router.post("/sessions/clear")
def clear_sessions():
    db.clear_all_sessions()
    return {"success": True}
