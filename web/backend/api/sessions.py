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


@router.get("/sessions/trash")
def list_trash():
    """휴지통 목록 — 되돌리기·완전 삭제 화면이 쓴다.

    **`/sessions/{session_id}` 보다 먼저 등록해야 한다** — FastAPI 는 등록 순서로
    매칭하므로 뒤에 두면 `session_id="trash"` 로 잡혀 404 가 된다.
    """
    return db.list_sessions(deleted=True)


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
        # 판정은 녹음 시점에 기록된 런타임 값이 우선이다. `sessions.source` 로 추정하던
        # 과거에는 웹 실시간이 웹 업로드와 같은 "web" 이라 항상 False 로 떨어져,
        # 같은 회의를 대시보드는 $0.009/분(cost_estimate) · 상세는 $0.003/분으로
        # 보여줬다(기본 설정 기준 STT 를 1/3로 과소 표시).
        _two_pass = pricing.resolve_two_pass(
            session.get("stt_two_pass"), session.get("source"), session.get("mode"),
            config_two_pass=_m["two_pass"])
    except Exception as e:
        return {"ok": False, "message": f"가격 정보 로드 실패: {e}"}
    cost = pricing.estimate_session_cost(
        dur, stt_model,
        translate=bool(session.get("translate")),
        include_minutes=bool(segs),   # 문서가 생성된(=완료된) 세션만 회의록 비용 포함
        llm=llm, minutes_model=minutes_model,
        two_pass=_two_pass, revise_model=_m["revise_model"],
        # facilitation 은 추정하지 않는다 — 아래에서 **실제 발생액**으로 채운다.
    )
    # 회의 중에 발생하지만 sessions.cost_estimate 에는 넣지 않는 과금(이중 집계 방지,
    # pricing 독스트링)을 usage_log 에서 되찾는다. 그래서 이 회의가 쓴 돈인데도 상세
    # 화면에서 빠져 보였다 — 추정이 아니라 기록된 실제 금액을 더한다.
    #
    # **kind 를 하나씩 적지 않는다.** 예전에는 facilitation 만 되찾아서, 나중에 추가된
    # web_research(회의 중 웹 검색 보완)가 같은 규약으로 기록되는데도 상세에서 빠졌다.
    # 세션 키가 붙은 모든 kind 를 합산하면 새 과금 경로가 자동으로 포함된다.
    try:
        from meeting_minutes_app.common import usage_log
        actual = usage_log.session_spend_by_kind(session_id)
    except Exception:
        actual = {}
    for kind, usd in actual.items():
        cost[kind] = round(float(usd), 6)
    if actual:
        cost["total"] = round(
            float(cost.get("total") or 0.0) + sum(actual.values()), 6)
    return {"ok": True, "stt_model": stt_model, "llm": llm,
            # 아래 kind 들은 추정이 아니라 기록된 실측이라는 표시(화면 문구 구분용)
            "actual_kinds": sorted(actual), "facilitation_actual": True, **cost}


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
    """세션을 **휴지통으로** 보낸다(soft delete). 파일은 손대지 않는다.

    하드 DELETE 였을 때 두 가지가 잘못됐다(FR-001 개정 · N-13).
      ① 결과 폴더가 남아 다음 시작에 `session_scanner` 가 되살렸다 — 지운 회의가 돌아온다.
      ② 되돌릴 방법이 없는데 확인은 프런트 `confirm()` 뿐이고 서버는 무조건 성공을 돌려줬다.
    폴더 정리는 사용자가 '완전 삭제'를 누를 때만 하고, 그때도 OS 휴지통으로 보낸다.

    부수효과가 있는 요청이라 Origin 을 본다 — CORS 는 단순 요청의 전송을 막지 않으므로,
    예전에는 아무 웹페이지가 사용자의 회의 기록을 지울 수 있었다(SEC-009).
    """
    from web.backend.security import require_client
    require_client(request)
    if not db.delete_session(session_id):
        # 없는 세션·이미 삭제된 세션에 무조건 success 를 돌려주면 화면이 거짓말을 한다.
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없거나 이미 삭제됐습니다.")
    return {"success": True, "restorable": True}


@router.post("/sessions/{session_id}/restore")
def restore_session(session_id: str, request: Request):
    """휴지통에서 되돌린다."""
    from web.backend.security import require_client
    require_client(request)
    if not db.restore_session(session_id):
        raise HTTPException(status_code=404, detail="휴지통에 그 세션이 없습니다.")
    return {"success": True}


@router.delete("/sessions/{session_id}/purge")
def purge_session(session_id: str, request: Request):
    """완전 삭제 — DB 행을 지우고 결과 폴더를 **OS 휴지통으로** 보낸다.

    `rmtree` 를 쓰지 않는 이유는 폴더 안에 회의록·전사·오디오가 들어 있기 때문이다.
    '완전 삭제'라도 회복 불가일 이유는 없고, PRD §17 이 "삭제는 휴지통 기본"을 확정했다.
    """
    from web.backend.security import require_client
    require_client(request)
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")

    # **순서가 중요하다**(FR-001 수용 기준): 폴더를 먼저 옮기고, 성공한 뒤에 DB 행을 지운다.
    # 반대로 하면 폴더 이동이 실패했을 때 파일만 남고 그것을 가리키는 기록이 사라져
    # 아무도 모르는 고아 폴더가 된다(원래 결함이 바로 고아 파일이었다).
    output_dir = (session.get("output_dir") or "").strip()
    moved, message = True, "결과 폴더가 없습니다."
    if output_dir:
        from web.backend.trash import move_to_trash
        moved, message = move_to_trash(output_dir)
        if not moved:
            raise HTTPException(
                status_code=500,
                detail=(f"{message} 기록은 그대로 두었습니다(휴지통에 남아 있습니다) — "
                        f"폴더를 직접 정리한 뒤 다시 시도하세요."))

    db.purge_session(session_id)
    return {"success": True, "folder_removed": moved, "message": message}


@router.post("/sessions/clear")
def clear_sessions(request: Request):
    """모든 세션을 **휴지통으로** 보낸다.

    확인은 프런트 `confirm()` 뿐이었고 서버는 무조건 실행했다. Origin 검증(SEC-009)에
    이어 이제 되돌릴 수 있게 됐다 — 전량 삭제가 회복 불가인 것이 가장 위험했다.
    """
    from web.backend.security import require_client
    require_client(request)
    moved = db.clear_all_sessions()
    return {"success": True, "moved": moved, "restorable": True}
