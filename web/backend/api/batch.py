"""
api/batch.py — 파일 업로드 + 배치 처리 API
"""

import os
import time
import uuid
import argparse
import tempfile
import traceback
from pathlib import Path
from datetime import datetime

from fastapi import APIRouter, UploadFile, File, Form, BackgroundTasks, HTTPException

from web.backend import database as db
from web.backend.schemas import MODE_PRESETS
from web.backend.paths import EXE_DIR

router = APIRouter(tags=["batch"])

UPLOADS_DIR = Path(EXE_DIR) / "web" / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

# 세션별 처리 진행 상태(in-memory). BackgroundTasks가 같은 프로세스에서 돌아 공유된다.
# {session_id: {"percent": int, "stage": str, "started": float}}
_PROGRESS: dict = {}

# 취소 요청된 세션 id 집합. 진행 콜백(_progress)이 단계 경계마다 확인해 협조적으로 중단한다.
# (STT 등 개별 단계가 실행되는 도중에는 그 단계가 끝나야 취소가 반영된다.)
_CANCELLED: set = set()

# 비용 확인 대기 중인 업로드(in-memory). 사용자가 예상 비용을 보고 [계속]을 누를 때까지
# 세션을 만들지 않아 대시보드에 잔재가 남지 않는다. {pending_id: {...}}
# 파일은 이미 uploads/ 에 저장돼 있고, [계속] 시 세션 생성·처리 시작 / [취소] 시 파일 삭제.
_PENDING: dict = {}


class _BatchCancelled(BaseException):
    """사용자가 처리를 취소했을 때 파이프라인을 중단시키는 신호.

    Exception이 아닌 BaseException을 상속한다 — pipeline._p()가 progress_cb 예외를
    `except Exception: pass`로 삼키므로, Exception 하위였다면 취소 신호가 전파되지
    못하고 조용히 무시됐다. BaseException은 그 필터를 통과해 최상위까지 올라간다.
    """


def _build_args(
    mode: int = 2,
    title: str = "",
    topic: str = "",
    speakers: str = "",
    doc_type: str = "",
    language: str = "",
    translate: bool = False,
) -> argparse.Namespace:
    """CLI 모드 번호로부터 argparse.Namespace를 구성."""
    preset = MODE_PRESETS.get(mode, MODE_PRESETS[2])

    # 회의록 생성 LLM은 config.json(models.llm)을 따른다 (gpt 하드코딩 제거)
    try:
        from meeting_minutes_app.common import config_loader as _cfg
        _llm = _cfg.get("models.llm", "gpt") or "gpt"
    except Exception:
        _llm = "gpt"

    return argparse.Namespace(
        type=doc_type or preset["type"],
        language=language or preset["language"],
        translate=translate if translate else preset["translate"],
        # CLI의 --translate-script는 --translate와 독립적인 별도 opt-in 플래그로,
        # 지정하지 않으면 항상 False다. 여기서 preset["translate"]를 그대로 복제하면
        # translate=True인 프리셋(2/4/5번)마다 CLI에는 없는 script_ko.md가 추가로
        # 생성돼 동일 옵션으로 처리해도 산출물이 달라지므로, CLI 기본값과 맞춘다.
        translate_script=False,
        model=None,
        llm=_llm,
        speakers=speakers,
        topic=topic,
        title=title,
        resume=False,
        reuse_speakers=False,
        edit_speakers=False,
        custom_prompt="",
        debug=False,
        notify="",
        memo=None,
        ssl_no_verify=False,
        estimate_cost=False,
    )


def _run_batch_processing(session_id: str, file_path: str, args: argparse.Namespace,
                          title: str, output_dir: str | None = None):
    """백그라운드에서 pipeline.process_single() 실행.

    output_dir 를 주면(재시도) 그 폴더를 재사용한다 — 이전 실행이 이미 만든
    segments.json/transcript.md 가 있으면 process_single 이 STT 를 건너뛰어
    (가장 비싼 단계) 재과금 없이 이어서 처리한다.
    """
    try:
        from meeting_minutes_app.meeting_pipeline import meeting_minutes as mm
        from meeting_minutes_app.meeting_pipeline import pipeline

        if not args.model:
            args.model = mm.DEFAULT_STT_MODEL

        llm = mm.LLMClient(preferred=args.llm)

        if not output_dir:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_title = "".join(c for c in (title or "upload") if c.isalnum() or c in " _-").strip()[:50]
            # 상대 output_dir 은 CWD가 아닌 데이터 베이스 기준으로 해석(공용 로직) —
            # 엔트리포인트에 따라 CWD가 달라지면 산출물·스캐너 위치가 어긋난다.
            from meeting_minutes_app.common.app_paths import get_output_dir as _god
            output_dir = str(_god() / f"{ts}_{safe_title}")
        os.makedirs(output_dir, exist_ok=True)

        db.update_session_status(session_id, "processing", output_dir=output_dir)
        _PROGRESS[session_id] = {"percent": 0, "stage": "처리 준비 중", "started": time.time()}

        def _progress(pct: int, stage: str):
            # 단계 경계마다 취소 요청을 확인해 협조적으로 중단한다.
            if session_id in _CANCELLED:
                raise _BatchCancelled()
            prev = _PROGRESS.get(session_id, {})
            _PROGRESS[session_id] = {**prev, "percent": pct, "stage": stage}

        with tempfile.TemporaryDirectory() as work_dir:
            pipeline.process_single(
                input_path=file_path,
                args=args,
                llm=llm,
                output_dir=output_dir,
                title=title or "Upload",
                work_dir=work_dir,
                progress_cb=_progress,
            )

        _progress(95, "결과 저장 중")
        db.import_output_files(session_id, output_dir)
        db.update_session_status(session_id, "completed")
        _progress(100, "완료")

        # Wiki Knowledge Graph 동기화 (best-effort — 실패해도 배치 처리 결과에 영향 없음)
        try:
            from meeting_minutes_app.wiki_core import graph_sync, wiki_knowledge as wk

            docs = db.get_documents(session_id)
            actions_doc = next((d for d in docs if d.get("type") == "actions"), None)
            minutes_doc = next((d for d in docs if d.get("type") == "minutes"), None)
            decisions = wk.extract_decisions_from_minutes(minutes_doc["content"]) if minutes_doc else None

            graph_sync.sync_session_graph(
                session_id=session_id,
                title=title or "Upload",
                actions_json=actions_doc["content"] if actions_doc else None,
                decisions=decisions,
            )
        except Exception:
            pass

        # 처리 완료 알림 (email/slack/teams) — 웹 업로드도 CLI/실시간과 동일하게 발송.
        # (기존엔 이 경로에 알림 호출이 없어 업로드 완료 후 메일이 가지 않았다.)
        try:
            from meeting_minutes_app.common import config_loader as _cfg
            channel = (_cfg.get("notify.on_finish", "none") or "none").lower()
            if channel and channel != "none":
                from meeting_minutes_app.meeting_pipeline.publish import (
                    _send_notification, _collect_notification_artifacts,
                )
                files = _collect_notification_artifacts(output_dir, "", title or "Upload")
                summary_path = os.path.join(output_dir, "summary.md")
                if not os.path.isfile(summary_path):
                    summary_path = ""
                _send_notification(channel, title or "Upload", summary_path, files, doc_type=args.type)
        except Exception:
            traceback.print_exc()

    except _BatchCancelled:
        # 사용자가 취소 — 세션과 진행상태를 정리한다(대시보드에 잔여물 남기지 않음).
        print(f"[batch] 세션 {session_id} 처리 취소됨")
        try:
            db.delete_session(session_id)
        except Exception:
            db.update_session_status(session_id, "error")
        _PROGRESS.pop(session_id, None)
    except Exception as e:
        traceback.print_exc()
        # 실패 원인을 세션·진행상태에 보존 — 비개발자가 로그 파일을 열지 않아도
        # 대시보드/진행 표시에서 무엇이 문제였는지(키 누락, 결제 한도 등) 알 수 있게.
        reason = f"{type(e).__name__}: {e}".strip()
        db.update_session_status(session_id, "error", error_detail=reason[:500])
        _PROGRESS[session_id] = {**_PROGRESS.get(session_id, {}),
                                 "stage": f"오류로 중단됨 — {reason[:200]}"}
    finally:
        _CANCELLED.discard(session_id)


@router.get("/upload/progress/{session_id}")
def upload_progress(session_id: str):
    """업로드 처리 진행 상태(단계·퍼센트·경과초). 처리 중 폴링용."""
    p = _PROGRESS.get(session_id)
    if not p:
        return {"found": False}
    elapsed = int(time.time() - p.get("started", time.time()))
    return {
        "found": True,
        "percent": int(p.get("percent", 0)),
        "stage": p.get("stage", ""),
        "elapsed": elapsed,
    }


@router.post("/upload/cancel/{session_id}")
def cancel_upload(session_id: str):
    """진행 중인 업로드 처리를 취소 요청한다.

    실제 중단은 파이프라인이 다음 단계 경계에 도달할 때 일어난다(협조적 취소).
    STT 등 오래 걸리는 단계 실행 중이면 그 단계가 끝난 뒤 반영된다.
    """
    if session_id not in _PROGRESS:
        return {"ok": False, "message": "진행 중인 처리가 없습니다(이미 완료·취소되었을 수 있음)."}
    _CANCELLED.add(session_id)
    _PROGRESS[session_id] = {**_PROGRESS.get(session_id, {}), "stage": "취소 요청됨 — 현재 단계 완료 후 중단"}
    return {"ok": True, "message": "취소 요청됨. 현재 단계가 끝나면 중단됩니다."}


@router.post("/upload")
async def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    title: str = Form(""),
    topic: str = Form(""),
    type: str = Form("meeting"),
    language: str = Form(""),
    translate: str = Form("false"),
    speakers: str = Form(""),
    mode: int = Form(2),
    confirm: str = Form("false"),
):
    # 사전 점검: OpenAI 키(STT 필수)가 없으면 백그라운드에서 실패해 원인이 로그에만
    # 남는다. 시작 전에 명확한 한국어 오류로 거절해 설정 화면으로 안내한다.
    from meeting_minutes_app.common import config_loader as _cfg
    if not _cfg.get_api_key("api.openai_api_key", "OPENAI_API_KEY"):
        raise HTTPException(
            status_code=400,
            detail="OpenAI API 키가 설정되지 않았습니다. [설정] → API 키에서 입력한 뒤 다시 시도하세요.",
        )

    safe_name = file.filename or "upload.mp3"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    save_path = str(UPLOADS_DIR / f"{ts}_{safe_name}")

    # 스트리밍 저장 — 수백 MB 녹음 파일을 통째로 RAM에 올리지 않는다
    with open(save_path, "wb") as f:
        while True:
            chunk = await file.read(4 * 1024 * 1024)
            if not chunk:
                break
            f.write(chunk)

    do_translate = translate.lower() in ("true", "1", "yes")

    # ── 지출 한도 검사 ──────────────────────────────────────────────
    # 저장된 파일의 길이를 재 예상 비용을 산출하고, 파일당·월 한도(cost.*)를
    # 넘으면 처리 전에 거절한다(비개발자가 실수로 초장시간 녹음이나 여러 건을
    # 올려 공용 키로 큰 비용을 내는 것을 막는 유일한 서버측 방어선).
    # 예상치는 대략값이며, 길이를 못 재면(dur=0) 검사를 건너뛴다(정상 업로드 차단 방지).
    est_total = 0.0
    duration_sec = 0.0
    try:
        from meeting_minutes_app.meeting_pipeline import meeting_minutes as _mm
        duration_sec = _mm.audio_duration(save_path)
    except Exception:
        duration_sec = 0.0
    if duration_sec > 0:
        from meeting_minutes_app.common import pricing
        _m = pricing.current_models(_cfg)
        est = pricing.estimate_session_cost(
            duration_sec, _m["stt_model"], translate=do_translate,
            include_minutes=True, llm=_m["llm"], minutes_model=_m["minutes_model"],
            # two_pass 는 넘기지 않는다(기본 False) — 업로드/배치 파이프라인에는
            # 보정 전사 패스가 없어 STT 과금이 한 번뿐이다. realtime.two_pass 설정이
            # 켜져 있어도 이 경로에는 적용되지 않는다.
        )
        est_total = est["total"]
        per_file_cap = float(_cfg.get("cost.per_file_cap_usd", 0) or 0)
        monthly_cap = float(_cfg.get("cost.monthly_cap_usd", 0) or 0)
        if per_file_cap > 0 and est_total > per_file_cap:
            os.remove(save_path)
            raise HTTPException(
                status_code=400,
                detail=(f"이 파일의 예상 비용 ${est_total:.2f}(약 {duration_sec/60:.0f}분)이 "
                        f"파일당 한도 ${per_file_cap:.2f}를 초과합니다. "
                        f"[설정] → 지출 한도에서 한도를 조정하거나 더 짧은 파일을 올리세요."),
            )
        if monthly_cap > 0:
            mtd = db.month_to_date_spend()
            if mtd + est_total > monthly_cap:
                os.remove(save_path)
                raise HTTPException(
                    status_code=400,
                    detail=(f"이번 달 예상 지출 ${mtd:.2f} + 이 파일 ${est_total:.2f} = "
                            f"${mtd + est_total:.2f}가 월 한도 ${monthly_cap:.2f}를 초과합니다. "
                            f"[설정] → 지출 한도에서 한도를 조정하세요."),
                )

    args = _build_args(
        mode=mode, title=title, topic=topic, speakers=speakers,
        doc_type=type, language=language, translate=do_translate,
    )

    # 제목을 안 넣으면 원본 파일명(stem)을 사용 — CLI의 `title = args.title or
    # Path(fp).stem` 관례와 맞춰야 auto_process_vault.py의 already_processed(stem)
    # 토큰 매칭(파일명 기준 중복 처리 방지)이 web 업로드 폴더도 인식할 수 있다.
    # (기존엔 "upload" 고정값 또는 확장자가 안 떨어진 파일명을 써서 매칭이 깨졌음)
    effective_title = title or (Path(safe_name).stem or "upload")

    # ── 비용 확인 단계 ──────────────────────────────────────────────
    # confirm 이 아니면 아직 처리하지 않고, 예상 비용을 돌려줘 사용자가 확인하게 한다.
    # (세션은 만들지 않는다 — [계속] 시 confirm_upload 에서 생성·시작한다.)
    do_confirm = confirm.lower() in ("true", "1", "yes")
    if not do_confirm:
        pending_id = uuid.uuid4().hex
        _PENDING[pending_id] = {
            "file_path": save_path, "args": args, "title": effective_title,
            "topic": topic, "speakers": speakers, "mode": mode,
            "est_total": est_total, "duration_sec": duration_sec,
        }
        return {
            "status": "confirm_required",
            "pendingId": pending_id,
            "estimateUsd": round(est_total, 4),
            "durationSec": round(duration_sec),
            "monthToDateUsd": round(db.month_to_date_spend(), 4),
            "monthlyCapUsd": float(_cfg.get("cost.monthly_cap_usd", 0) or 0),
        }

    session_id = _start_pending(
        file_path=save_path, args=args, title=effective_title, topic=topic,
        speakers=speakers, mode=mode, est_total=est_total, duration_sec=duration_sec,
        background_tasks=background_tasks,
    )
    return {"sessionId": session_id, "status": "processing"}


def _start_pending(*, file_path, args, title, topic, speakers, mode,
                   est_total, duration_sec, background_tasks) -> str:
    """세션을 만들고 예상 비용을 기록한 뒤 배치 처리를 시작한다(업로드/확인 공통)."""
    # ── 월 지출 한도 재검사 ──────────────────────────────────────────
    # 한도 검사는 upload_file 에서도 하지만, 일반 UI 는 2단계(예상비용 확인 → confirm)라
    # 그 시점 검사는 '세션 생성 전' 값이라 대기 중 업로드가 서로 합산되지 않는다. 실제로
    # 세션을 만드는 이 지점에서 현재 월 지출 합계 기준으로 다시 검사해, 여러 건을 미리
    # 올려두고 한꺼번에 확인해 한도를 우회하는 것을 막는다(서버측 방어선 유지).
    if est_total > 0:
        from meeting_minutes_app.common import config_loader as _cfg
        monthly_cap = float(_cfg.get("cost.monthly_cap_usd", 0) or 0)
        if monthly_cap > 0:
            mtd = db.month_to_date_spend()
            if mtd + est_total > monthly_cap:
                try:
                    os.remove(file_path)
                except OSError:
                    pass
                raise HTTPException(
                    status_code=400,
                    detail=(f"이번 달 예상 지출 ${mtd:.2f} + 이 파일 ${est_total:.2f} = "
                            f"${mtd + est_total:.2f}가 월 한도 ${monthly_cap:.2f}를 초과합니다. "
                            f"[설정] → 지출 한도에서 한도를 조정하세요."),
                )
    session_id = db.create_session(
        title=title, topic=topic, doc_type=args.type, language=args.language,
        translate=args.translate, model=args.model or "", speakers=speakers,
        file_path=file_path, source="web", mode=str(mode),
    )
    # 예상 비용·길이를 세션에 기록 — 월 지출 합계(month_to_date_spend)에 즉시 반영돼
    # 동시 업로드도 한도 검사에 포함된다.
    if est_total > 0 or duration_sec > 0:
        db.update_session_status(
            session_id, "processing",
            cost_estimate=round(est_total, 4), duration_sec=round(duration_sec, 1),
        )
    background_tasks.add_task(_run_batch_processing, session_id, file_path, args, title)
    return session_id


@router.post("/upload/confirm/{pending_id}")
def confirm_upload(pending_id: str, background_tasks: BackgroundTasks):
    """예상 비용 확인 후 실제 처리를 시작한다."""
    p = _PENDING.pop(pending_id, None)
    if not p:
        raise HTTPException(
            status_code=404,
            detail="확인 대기 중인 업로드가 없습니다(만료되었거나 이미 처리됨). 다시 업로드하세요.",
        )
    session_id = _start_pending(
        file_path=p["file_path"], args=p["args"], title=p["title"], topic=p["topic"],
        speakers=p["speakers"], mode=p["mode"], est_total=p["est_total"],
        duration_sec=p["duration_sec"], background_tasks=background_tasks,
    )
    return {"sessionId": session_id, "status": "processing"}


@router.post("/upload/cancel-pending/{pending_id}")
def cancel_pending_upload(pending_id: str):
    """비용 확인 단계에서 사용자가 취소 — 대기 항목과 업로드된 파일을 정리한다."""
    p = _PENDING.pop(pending_id, None)
    if p:
        try:
            os.remove(p["file_path"])
        except OSError:
            pass
    return {"ok": True}


@router.post("/upload/retry/{session_id}")
def retry_session(session_id: str, background_tasks: BackgroundTasks):
    """실패한 세션을 같은 출력 폴더로 재시도한다.

    이전 실행이 STT까지 마친 뒤(회의록/번역 등 이후 단계에서) 실패했다면 그
    폴더의 segments.json 을 재사용해 STT를 건너뛴다 — 가장 비싼 단계를 다시
    결제하지 않는다. STT 이전에 실패했다면(중간 결과 없음) 원본 파일로 다시 처리한다.
    """
    s = db.get_session(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")
    if s.get("status") == "processing":
        raise HTTPException(status_code=400, detail="이미 처리 중인 세션입니다.")

    file_path = s.get("file_path") or ""
    output_dir = s.get("output_dir") or ""
    seg_cached = bool(output_dir) and (
        os.path.isfile(os.path.join(output_dir, "segments.json"))
        or os.path.isfile(os.path.join(output_dir, "transcript.md"))
    )
    # 중간 결과(STT)도 없고 원본 파일도 사라졌으면 재시도할 근거가 없다.
    if not seg_cached and (not file_path or not os.path.isfile(file_path)):
        raise HTTPException(
            status_code=400,
            detail="원본 파일과 중간 결과가 모두 없어 재시도할 수 없습니다. 파일을 다시 업로드하세요.",
        )

    args = _build_args(
        mode=int(s.get("mode") or 2), title=s.get("title", ""), topic=s.get("topic", ""),
        speakers=s.get("speakers", ""), doc_type=s.get("type", "meeting"),
        language=s.get("language", ""), translate=bool(s.get("translate")),
    )
    db.update_session_status(session_id, "processing")
    _PROGRESS[session_id] = {"percent": 0, "stage": "재시도 준비 중", "started": time.time()}
    background_tasks.add_task(
        _run_batch_processing, session_id, file_path, args, s.get("title", "Upload"),
        output_dir or None,
    )
    return {"sessionId": session_id, "status": "processing", "reusedStt": seg_cached}
