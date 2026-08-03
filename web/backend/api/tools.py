"""
api/tools.py — 텍스트 분석 · 회의록 재생성 · 볼트 인덱스 재빌드 · 준비 브리핑

CLI 전용이던 기능들을 web에 노출한다. 무거운 작업은 BackgroundTasks 로 비동기 처리하고
세션 상태(processing/completed/error)로 진행을 알린다.
"""

import os
import tempfile
import traceback
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from web.backend.security import require_client

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
def _regenerate_cost_usd() -> float:
    """재생성 1회의 예상 비용(USD) = 회의록 생성 LLM 비용.

    전사는 `args.resume` 으로 재사용하므로 STT 과금이 없다. 세션의 최초
    `cost_estimate` 도 같은 `minutes_cost()` 를 쓰므로 성질이 같은 추정치다.
    """
    try:
        from meeting_minutes_app.common import pricing
        from meeting_minutes_app.common import config_loader as cfg
        m = pricing.current_models(cfg)
        return float(pricing.minutes_cost(m["llm"], m["minutes_model"]))
    except Exception:
        return 0.0


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
        # 재생성 과금을 세션 비용에 누적한다. 이게 없어서 재생성 LLM 비용이
        # 어디에도 기록되지 않았고(월 합계에서 빠짐), 몇 번을 재생성해도 지출이
        # 0으로 보였다. STT 는 args.resume 으로 재사용하므로 과금이 없다 —
        # 회의록 생성 LLM 비용만 더한다.
        db.add_session_cost(session_id, _regenerate_cost_usd())
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
    # 재생성도 지출 한도를 지난다. 지금까지 이 경로는 검사를 받지 않아 한도를 넘긴
    # 뒤에도 무제한으로 LLM 을 부를 수 있었다(업로드만 막혀 있었다).
    # 1건당 한도는 적용하지 않는다 — 그 한도는 '오디오 파일 한 건'의 길이를 뜻한다.
    est = _regenerate_cost_usd()
    from meeting_minutes_app.common import spend_guard
    reason = spend_guard.blocked(est, check_per_item=False)
    if reason:
        raise HTTPException(
            status_code=400,
            detail=(f"{reason}. 회의록 재생성을 시작하지 않았습니다. "
                    f"[설정] → 지출 한도에서 한도를 조정하세요."),
        )
    background_tasks.add_task(_run_regenerate, session_id, payload.get("notes", ""))
    return {"sessionId": session_id, "status": "processing",
            "estimatedUsd": round(est, 4)}


# ── 3) 볼트 인덱스 + 지식 그래프 재빌드 ───────────
def _rebuild_graph_from_vault() -> str:
    """설정된 노트 폴더(+registry)에서 지식 그래프를 다시 파생한다.

    graph_sync 백필은 멱등(upsert)이며 wiki_graph.db에만 쓰고 원본 .md/registry는
    건드리지 않는다. graph_enabled가 꺼져 있으면 아무것도 하지 않는다. 실패는 호출부에서
    삼켜 인덱스 재빌드 자체를 실패시키지 않는다(폴더-only 위키의 부가 단계).

    백필 **전에** prune_shadow_note_nodes()를 돌린다. 과거 백필이 인덱서보다 넓게 긁어
    넣은 노드(그림자 사본·제외 폴더)는 재백필로는 사라지지 않고, 포터블 배포본에는
    scripts/ 가 들어가지 않아 이 버튼 말고는 정리할 경로가 없다. 이 순서가 안전성의
    핵심이다 — 판정이 잘못돼 지운 노드가 있어도 바로 뒤 백필이 같은 노트에서 다시 만든다
    (지우는 대상은 정의상 엣지 0건이라 잃을 관계도 없다). 깨끗한 DB에서는 0건 = no-op.
    """
    from meeting_minutes_app.common import config_loader as _cfg
    if not bool(_cfg.get("wiki_knowledge.graph_enabled", True)):
        return ""
    from meeting_minutes_app.wiki_core import graph_sync
    pruned = 0
    try:
        pruned = graph_sync.prune_shadow_note_nodes().get("pruned", 0)
    except Exception as _pe:      # 정리 실패가 재빌드를 막지는 않는다
        print(f"[graph] 그림자 노드 정리 건너뜀: {_pe}")
    graph_sync.backfill_from_registries()
    vc = graph_sync.backfill_from_vault()
    msg = (f", 그래프 노드 {vc.get('nodes_would_add', 0)}·엣지 "
           f"{vc.get('edges_would_add', 0)} 반영")
    if pruned:
        msg += f" (노트가 아닌 노드 {pruned}개 정리)"
    return msg


@router.post("/reindex")
def reindex(_guard: None = Depends(require_client)):
    """노트 폴더(.md) 검색 인덱스와 지식 그래프를 다시 만든다. 폴더-only 위키에 필수.

    임베딩 과금이 있는 경로라 관문을 지난다 — 본문 없는 POST 는 CORS 의 "단순 요청"이어서
    preflight 가 없고, 악성 페이지가 그대로 호출할 수 있었다(SEC-009 가 남긴 구멍).
    """
    try:
        from meeting_minutes_app.wiki_core.vault_indexer import VaultIndexer
        idx = VaultIndexer.from_config()
        if not idx:
            return {"ok": False, "message": "노트 폴더가 설정되지 않았습니다. 설정에서 노트 폴더(.md)를 지정하세요."}
        from meeting_minutes_app.common import usage_log
        _before = usage_log.month_to_date_by_kind().get("embedding", 0.0)
        n = idx.build(verbose=False)
        msg = f"인덱스 재빌드 완료 — 노트 {n}개"
        # 임베딩 과금은 사용자가 버튼을 눌러 일으킨 지출이다 — 사후에라도 보여 준다.
        # (사전 확인 다이얼로그는 두지 않는다: 볼트 전량이 보통 $0.02 수준이고,
        #  '폴더 연결 직후'·'앱 시작 시' 자동 인덱싱 경로에는 물어볼 자리가 없다.)
        _spent = usage_log.month_to_date_by_kind().get("embedding", 0.0) - _before
        if _spent > 0:
            msg += f" · 임베딩 비용 약 ${_spent:.4f}"
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
    """폴백 체인이 실제로 쓰는 모델명을 그대로 본다.

    config 를 여기서 또 읽으면(과거 동작) 체인과 갈라질 수 있어, 상태 배지가
    준비되지도 않은 모델을 '준비됨'으로 표시할 수 있다. stt 모듈 전역은
    config reload 훅이 갱신하므로 설정 저장 즉시 반영된다."""
    from meeting_minutes_app.meeting_pipeline import stt
    return str(stt.LOCAL_STT_MODEL or "base")


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


@router.get("/cost/summary")
def cost_summary(months: int = 6):
    """비용 대시보드용 집계 — 월별 · 유형별 · 상위 세션 + 이번 달 한도 대비.

    **모델별 집계는 넣지 않는다.** sessions.model 이 웹 업로드 경로에서 항상 빈
    값이고(batch.py 가 model=None 으로 만든다), cost_estimate 는 STT/번역/회의록이
    합쳐진 단일 숫자라 분해할 수 없다. 없는 축을 있는 척 보여주느니 뺀다.
    """
    from web.backend import database as db
    from meeting_minutes_app.common import config_loader as cfg
    from meeting_minutes_app.common import usage_log
    from meeting_minutes_app.common import spend_guard
    try:
        by_kind = usage_log.month_to_date_by_kind()
        # "자동 실행분 비용을 별도로 조회할 수 있다"(FR-011 수용 기준).
        # 어떤 kind 가 자동 실행인지는 spend_guard 가 정한다 — 그 목록을 프런트에
        # 복사하면 새 자동 경로를 추가할 때 한쪽만 갱신된다.
        automation = round(
            sum(v for k, v in by_kind.items() if k in spend_guard.AUTOMATION_KINDS), 4)
        return {
            "ok": True,
            "monthToDateUsd": round(db.month_to_date_spend(), 4),
            "monthlyCapUsd": float(cfg.get("cost.monthly_cap_usd", 0) or 0),
            "perFileCapUsd": float(cfg.get("cost.per_file_cap_usd", 0) or 0),
            "months": db.cost_by_month(months),
            "byType": db.cost_by_type(),
            "top": db.top_cost_sessions(5),
            # 세션에 속하지 않는 지출(위키 임베딩 등) — 월 합계에 포함돼 있다
            "otherUsd": round(sum(by_kind.values()), 4),
            "otherByKind": {k: round(v, 4) for k, v in by_kind.items()},
            # 그중 사용자가 화면을 보고 있지 않을 때 발생한 것(폴더 감시·계획 자동화)
            "automationUsd": automation,
        }
    except Exception as e:
        traceback.print_exc()
        return {"ok": False, "message": f"비용 집계 실패: {e}"}


# ── 4) 회의 준비 브리핑 ───────────────────────────
@router.get("/cost/rates")
def cost_rates():
    """현재 설정 기준 실시간 비용 요율(USD) — 녹음 중 러닝 비용 추정용."""
    try:
        from meeting_minutes_app.common import config_loader as cfg
        from meeting_minutes_app.common import pricing
        # 모델 해석 규칙은 pricing.current_models 하나만 쓴다 — 여기에 복사돼 있던
        # 같은 분기가 two_pass 를 반영하지 않아 러닝 미터가 실제의 1/3을 보여줬다.
        _m = pricing.current_models(cfg)
        # 실시간 녹음 화면의 러닝 미터용이므로 2단계 보정 전사를 반영한다.
        _est = pricing.estimate_session_cost(
            60.0, _m["stt_model"], include_minutes=False,
            llm=_m["llm"], minutes_model=_m["minutes_model"],
            two_pass=_m["two_pass"], revise_model=_m["revise_model"],
        )
        return {
            "stt_model": _m["stt_model"],
            # 1차(표시) 전사 단가 — 기존 필드 의미를 바꾸지 않는다.
            "stt_per_min": _est["stt_rate_per_min"],
            # 아래 4개가 신규. 러닝 미터는 stt_effective_per_min 을 써야 한다.
            "stt_effective_per_min": _est["stt_effective_per_min"],
            "revise_per_min": _est["revise_rate_per_min"],
            "revise_model": _est["revise_model"],
            "two_pass": _est["two_pass"],
            "translate_per_min": pricing.TRANSLATE_COST_PER_MIN,
            # 회의록 생성 요율은 실제 LLM(gpt/claude) 모델 단가를 반영 (과거 항상 gpt-4o 기준이었음)
            "minutes_flat": pricing.minutes_cost(_m["llm"], _m["minutes_model"]),
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
