"""
api/sessions.py — 세션 CRUD API
"""

from fastapi import APIRouter, Query, HTTPException, Request
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
        # 모델 해석 규칙은 pricing.current_models 하나만 쓴다(여기에 복사돼 있던 같은
        # 분기가 two_pass 를 반영하지 않아 실시간 세션 비용이 과소 표시됐다).
        _m = pricing.current_models(cfg)
        stt_model = _m["stt_model"]
        llm = _m["llm"]
        minutes_model = _m["minutes_model"]
        # 2단계 보정 전사는 실시간 경로에만 있다 — 업로드 세션에 적용하면 과대 표시된다.
        _two_pass = _m["two_pass"] and pricing.is_two_pass_source(session.get("source"))
    except Exception as e:
        return {"ok": False, "message": f"가격 정보 로드 실패: {e}"}
    cost = pricing.estimate_session_cost(
        dur, stt_model,
        translate=bool(session.get("translate")),
        include_minutes=bool(segs),   # 문서가 생성된(=완료된) 세션만 회의록 비용 포함
        llm=llm, minutes_model=minutes_model,
        two_pass=_two_pass, revise_model=_m["revise_model"],
    )
    return {"ok": True, "stt_model": stt_model, "llm": llm, **cost}


@router.get("/sessions/{session_id}/related-notes")
def get_session_related_notes(session_id: str, cross: int = Query(8)):
    """이 회의에서 참조된 관련 노트 + 교차 회의 집계 (FR-5).

    실시간 검색이 근거(점수·섹션경로·snippet·발화·경과시각)와 함께 남긴 사이드카를
    읽는다. 회의가 없어도 404 대신 빈 목록 — 관련 노트는 부가 정보라 상세 화면이
    이것 때문에 실패하지 않아야 한다.
    """
    # 구버전 DB(테이블 없음)·조회 실패도 빈 목록으로 — 상세 화면은 계속 열려야 한다.
    try:
        notes = db.get_related_notes(session_id)
    except Exception:
        return {"notes": [], "cross": []}
    try:
        cross_rows = db.related_notes_cross_sessions(limit=max(0, cross))
    except Exception:
        cross_rows = []
    return {"notes": notes, "cross": cross_rows}


@router.get("/sessions/{session_id}/status")
def get_session_status(session_id: str):
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"id": session_id, "status": session["status"]}


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, request: Request):
    # 부수효과(삭제)가 있는 요청은 Origin 을 본다 — CORS 는 단순 요청의 전송을 막지
    # 않으므로, 예전에는 아무 웹페이지가 사용자의 회의 기록을 지울 수 있었다(SEC-009).
    from web.backend.security import require_local
    require_local(request)
    db.delete_session(session_id)
    return {"success": True}


@router.post("/sessions/clear")
def clear_sessions(request: Request):
    """모든 세션 기록 삭제. 확인은 프런트 confirm() 뿐이었고 서버는 무조건 실행했다.

    Origin 검증을 넣어 외부 페이지가 호출하지 못하게 한다(SEC-009). 되돌리기(soft
    delete)와 입력 확인은 FR-001 개정으로 Batch C 에서 다룬다 — 여기서는 원격
    트리거만 막는다.
    """
    from web.backend.security import require_local
    require_local(request)
    db.clear_all_sessions()
    return {"success": True}
