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
    # 상대 output_dir 은 CWD가 아닌 데이터 베이스 기준으로 해석(공용 로직)
    from meeting_minutes_app.common.app_paths import get_output_dir as _god
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = "".join(c for c in (title or "text") if c.isalnum() or c in " _-").strip()[:50] or "text"
    out = str(_god() / f"{ts}_{safe}")
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
    except Exception as e:
        traceback.print_exc()
        db.update_session_status(session_id, "error",
                                 error_detail=f"{type(e).__name__}: {e}"[:500])


def _require_llm_key():
    """LLM 호출 전 사전 점검 — 키가 하나도 없으면 백그라운드 실패 대신
    시작 시점에 명확한 한국어 오류로 거절해 설정 화면으로 안내한다."""
    from meeting_minutes_app.common import config_loader as _cfg
    if not (_cfg.get_api_key("api.openai_api_key", "OPENAI_API_KEY")
            or _cfg.get_api_key("api.anthropic_api_key", "ANTHROPIC_API_KEY")):
        raise HTTPException(
            status_code=400,
            detail="AI API 키가 설정되지 않았습니다. [설정] → API 키에서 입력한 뒤 다시 시도하세요.",
        )


@router.post("/process-text")
def process_text(payload: dict, background_tasks: BackgroundTasks):
    """붙여넣은 텍스트를 회의록/요약/액션으로 변환(서버 처리, 키 미노출)."""
    _require_llm_key()
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
            db.update_session_status(
                session_id, "error",
                error_detail="원본 결과 폴더를 찾을 수 없어 재생성할 수 없습니다.")
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
    except Exception as e:
        traceback.print_exc()
        db.update_session_status(session_id, "error",
                                 error_detail=f"{type(e).__name__}: {e}"[:500])


@router.post("/sessions/{session_id}/regenerate")
def regenerate(session_id: str, payload: dict, background_tasks: BackgroundTasks):
    """기존 세션의 전사를 재사용해 노트를 반영, 회의록을 다시 생성."""
    _require_llm_key()
    sess = db.get_session(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")
    if not sess.get("output_dir"):
        raise HTTPException(status_code=400, detail="재생성할 전사 데이터가 없습니다.")
    background_tasks.add_task(_run_regenerate, session_id, payload.get("notes", ""))
    return {"sessionId": session_id, "status": "processing"}


# ── 3) 볼트 인덱스 + 지식 그래프 재빌드 ───────────
def _rebuild_graph_from_vault() -> str:
    """설정된 노트 폴더(+registry)에서 지식 그래프를 다시 파생한다.

    graph_sync 백필은 멱등(upsert)이며 wiki_graph.db에만 쓰고 원본 .md/registry는
    건드리지 않는다. graph_enabled가 꺼져 있으면 아무것도 하지 않는다. 실패는 호출부에서
    삼켜 인덱스 재빌드 자체를 실패시키지 않는다(폴더-only 위키의 부가 단계).
    """
    from meeting_minutes_app.common import config_loader as _cfg
    if not bool(_cfg.get("wiki_knowledge.graph_enabled", True)):
        return ""
    from meeting_minutes_app.wiki_core import graph_sync
    graph_sync.backfill_from_registries()
    vc = graph_sync.backfill_from_vault()
    return (f", 그래프 노드 {vc.get('nodes_would_add', 0)}·엣지 "
            f"{vc.get('edges_would_add', 0)} 반영")


@router.post("/reindex")
def reindex():
    """노트 폴더(.md) 검색 인덱스와 지식 그래프를 다시 만든다. 폴더-only 위키에 필수."""
    try:
        from meeting_minutes_app.wiki_core.vault_indexer import VaultIndexer
        idx = VaultIndexer.from_config()
        if not idx:
            return {"ok": False, "message": "노트 폴더가 설정되지 않았습니다. 설정에서 노트 폴더(.md)를 지정하세요."}
        n = idx.build(verbose=False)
        msg = f"인덱스 재빌드 완료 — 노트 {n}개"
        # 같은 .md 폴더에서 지식 그래프도 함께 최신화(부가 단계 — 실패해도 인덱스는 성공).
        try:
            msg += _rebuild_graph_from_vault()
        except Exception as ge:
            traceback.print_exc()
            msg += f" (그래프 갱신 건너뜀: {ge})"
        return {"ok": True, "message": msg}
    except Exception as e:
        traceback.print_exc()
        return {"ok": False, "message": f"재빌드 실패: {e}"}


# ── 3.5) 로컬 STT 백업 모델 준비 ──────────────────
def _local_stt_model() -> str:
    from meeting_minutes_app.common import config_loader as _cfg
    return str(_cfg.get("models.stt_local", "base") or "base")


@router.get("/local-stt/status")
def local_stt_status():
    """로컬 백업 STT(faster-whisper) 준비 상태 — 라이브러리·가중치 유무."""
    try:
        from meeting_minutes_app.meeting_pipeline import stt
        return {"ok": True, **stt.local_model_status(_local_stt_model())}
    except Exception as e:
        traceback.print_exc()
        return {"ok": False, "lib_available": False, "installed": False,
                "model": "", "path": "", "size_mb": 0.0, "message": str(e)}


@router.post("/local-stt/prepare")
def local_stt_prepare():
    """로컬 백업 모델 가중치를 미리 내려받는다(수백 MB·수 분 걸릴 수 있음).

    전사 경로는 다운로드를 하지 않으므로(stt._get_local_model), 장애 전에 여기서 한 번
    준비해 두는 것이 전제다. 실패는 예외 대신 {ok:false} 로 돌려 설정 화면에 표시한다."""
    try:
        from meeting_minutes_app.meeting_pipeline import stt
        model = _local_stt_model()
        st = stt.prepare_local_model(model)
        return {"ok": True, "message":
                f"로컬 백업 모델 준비 완료 — {model} ({st['size_mb']}MB, "
                f"{st['elapsed_sec']}초)", **st}
    except Exception as e:
        traceback.print_exc()
        return {"ok": False, "message": f"준비 실패: {e}"}


# ── 4) 회의 준비 브리핑 ───────────────────────────
@router.get("/cost/rates")
def cost_rates():
    """현재 설정 기준 실시간 비용 요율(USD) — 녹음 중 러닝 비용 추정용."""
    try:
        from meeting_minutes_app.common import config_loader as cfg
        from meeting_minutes_app.common import pricing
        stt_model = cfg.get("models.stt", "gpt-4o-mini-transcribe") or "gpt-4o-mini-transcribe"
        llm = cfg.get("models.llm", "gpt") or "gpt"
        if str(llm).lower().startswith("claude"):
            minutes_model = cfg.get("models.claude_model", None)
        else:
            minutes_model = cfg.get("models.minutes_model", None) or cfg.get("models.gpt_model", None)
        return {
            "stt_model": stt_model,
            "stt_per_min": pricing.stt_rate_per_min(stt_model),
            "translate_per_min": pricing.TRANSLATE_COST_PER_MIN,
            # 회의록 생성 요율은 실제 LLM(gpt/claude) 모델 단가를 반영 (과거 항상 gpt-4o 기준이었음)
            "minutes_flat": pricing.minutes_cost(llm, minutes_model),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"요율 로드 실패: {e}")


def _note_ref(n) -> dict:
    """검색 결과 노트를 {title, path, score} 로 정규화(형태가 dict/obj 어느 쪽이든)."""
    def g(k, *alts):
        if isinstance(n, dict):
            for kk in (k, *alts):
                if n.get(kk) not in (None, ""):
                    return n.get(kk)
            return ""
        for kk in (k, *alts):
            v = getattr(n, kk, None)
            if v not in (None, ""):
                return v
        return ""
    path = g("path", "filename", "file")
    title = g("title", "name") or (str(path).replace("\\", "/").split("/")[-1].replace(".md", "") if path else "")
    return {"title": title, "path": str(path), "score": g("score")}


@router.post("/prep-brief")
def prep_brief(payload: dict):
    """제목/주제(+참석자·추가노트)로 볼트·레지스트리를 검색해 준비 브리핑 생성.

    반환에 related(찾은 관련 노트 목록)·vault_connected 를 포함해, 저장 전에
    '무엇이 연결됐는지' 화면에서 확인·추천할 수 있게 한다.
    """
    title = (payload.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=422, detail="제목을 입력하세요.")
    topic = payload.get("topic") or ""
    attendees = (payload.get("attendees") or "").strip()
    notes = (payload.get("notes") or "").strip()
    # 참석자·추가노트를 검색 힌트로 반영(관련 노트 매칭 향상) + 브리핑 memo 로 전달
    search_topic = " ".join(x for x in (topic, attendees, notes) if x).strip() or topic
    memo_parts = []
    if attendees:
        memo_parts.append(f"참석자: {attendees}")
    if notes:
        memo_parts.append(notes)
    memo = "\n".join(memo_parts)
    try:
        from meeting_minutes_app.wiki_core import wiki_knowledge as wk
        from meeting_minutes_app.wiki_core import vault_retrieval as vr

        indexer = vr.load_vault_indexer()
        try:
            obs = vr.load_obsidian_client()
        except Exception:
            obs = None
        vault_connected = bool(indexer or obs)

        regular, papers = wk._get_brief_related_notes(
            title, search_topic, indexer, obs, limit=5, memo=memo)
        action_reg = wk.load_action_registry(wk.DATA_DIR / "action_registry.json")
        decision_reg = wk.load_decision_registry(wk.DATA_DIR / "decision_registry.json")
        open_actions = wk._filter_actions_by_topic(action_reg.get("actions", []), search_topic, limit=10)
        recent_decisions = wk._filter_decisions_by_topic(decision_reg.get("decisions", []), search_topic, limit=10)

        now = datetime.now()
        brief = wk.build_prep_brief(
            title, topic, now.strftime("%y%m%d"), now.strftime("%Y-%m-%d"),
            regular, papers, open_actions, recent_decisions,
        )
        related = [_note_ref(n) for n in (list(regular or []) + list(papers or []))]
        return {
            "ok": True,
            "brief": brief,
            "vault_connected": vault_connected,
            "related": related,
            "related_count": len(related),
            "open_actions": len(open_actions or []),
            "recent_decisions": len(recent_decisions or []),
        }
    except Exception as e:
        traceback.print_exc()
        return {"ok": False, "message": f"브리핑 생성 실패: {e}"}


@router.post("/prep-brief/save")
def prep_brief_save(payload: dict):
    """생성된 회의 준비 브리핑을 세션으로 저장 → 대시보드에 표시.

    payload: { title, brief, topic?, date?, attendees? }
    """
    title = (payload.get("title") or "").strip()
    brief = payload.get("brief") or ""
    if not title or not brief:
        raise HTTPException(status_code=422, detail="제목과 브리핑 내용이 필요합니다.")
    try:
        from web.backend import database as db
        sid = db.create_session(
            title=title,
            topic=payload.get("topic") or "",
            doc_type="prep",
            speakers=payload.get("attendees") or "",
            source="web",
            mode="prep_brief",
        )
        db.upsert_document(sid, "minutes", brief, fmt="markdown")
        kw = {}
        d = (payload.get("date") or "").strip()
        if d:
            kw["date"] = d
        db.update_session_status(sid, "completed", **kw)
        return {"ok": True, "sessionId": sid}
    except Exception as e:
        traceback.print_exc()
        return {"ok": False, "message": f"저장 실패: {e}"}
