"""
api/batch.py — 파일 업로드 + 배치 처리 API
"""

import os
import time
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


def _run_batch_processing(session_id: str, file_path: str, args: argparse.Namespace, title: str):
    """백그라운드에서 pipeline.process_single() 실행."""
    try:
        from meeting_minutes_app.meeting_pipeline import meeting_minutes as mm
        from meeting_minutes_app.meeting_pipeline import pipeline

        if not args.model:
            args.model = mm.DEFAULT_STT_MODEL

        llm = mm.LLMClient(preferred=args.llm)

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
    args = _build_args(
        mode=mode, title=title, topic=topic, speakers=speakers,
        doc_type=type, language=language, translate=do_translate,
    )

    # 제목을 안 넣으면 원본 파일명(stem)을 사용 — CLI의 `title = args.title or
    # Path(fp).stem` 관례와 맞춰야 auto_process_vault.py의 already_processed(stem)
    # 토큰 매칭(파일명 기준 중복 처리 방지)이 web 업로드 폴더도 인식할 수 있다.
    # (기존엔 "upload" 고정값 또는 확장자가 안 떨어진 파일명을 써서 매칭이 깨졌음)
    effective_title = title or (Path(safe_name).stem or "upload")

    session_id = db.create_session(
        title=effective_title,
        topic=topic,
        doc_type=args.type,
        language=args.language,
        translate=args.translate,
        model=args.model or "",
        speakers=speakers,
        file_path=save_path,
        source="web",
        mode=str(mode),
    )

    background_tasks.add_task(_run_batch_processing, session_id, save_path, args, effective_title)

    return {"sessionId": session_id, "status": "processing"}
