"""
api/batch.py — 파일 업로드 + 배치 처리 API
"""

import os
import argparse
import tempfile
import traceback
from pathlib import Path
from datetime import datetime

from fastapi import APIRouter, UploadFile, File, Form, BackgroundTasks

from web.backend import database as db
from web.backend.schemas import MODE_PRESETS
from web.backend.paths import EXE_DIR

router = APIRouter(tags=["batch"])

UPLOADS_DIR = Path(EXE_DIR) / "web" / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


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
        output_dir = os.path.join(mm._c("output_dir", "./output") or "./output",
                                  f"{ts}_{safe_title}")
        os.makedirs(output_dir, exist_ok=True)

        db.update_session_status(session_id, "processing", output_dir=output_dir)

        with tempfile.TemporaryDirectory() as work_dir:
            pipeline.process_single(
                input_path=file_path,
                args=args,
                llm=llm,
                output_dir=output_dir,
                title=title or "Upload",
                work_dir=work_dir,
            )

        db.import_output_files(session_id, output_dir)
        db.update_session_status(session_id, "completed")

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

    except Exception:
        traceback.print_exc()
        db.update_session_status(session_id, "error")


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
    safe_name = file.filename or "upload.mp3"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    save_path = str(UPLOADS_DIR / f"{ts}_{safe_name}")

    with open(save_path, "wb") as f:
        content = await file.read()
        f.write(content)

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
