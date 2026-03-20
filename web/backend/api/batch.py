"""
api/batch.py — 파일 업로드 + 배치 처리 API
"""

import os
import sys
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

    return argparse.Namespace(
        type=doc_type or preset["type"],
        language=language or preset["language"],
        translate=translate if translate else preset["translate"],
        translate_script=preset.get("translate", False),
        model=None,
        llm="gpt",
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
    """백그라운드에서 meeting_minutes.process_single() 실행."""
    try:
        import meeting_minutes as mm

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
            mm.process_single(
                input_path=file_path,
                args=args,
                llm=llm,
                output_dir=output_dir,
                title=title or "Upload",
                work_dir=work_dir,
            )

        db.import_output_files(session_id, output_dir)
        db.update_session_status(session_id, "completed")

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

    session_id = db.create_session(
        title=title or safe_name,
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

    background_tasks.add_task(_run_batch_processing, session_id, save_path, args, title or safe_name)

    return {"sessionId": session_id, "status": "processing"}
