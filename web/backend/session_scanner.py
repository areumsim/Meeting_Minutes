"""
session_scanner.py — ./output/ 폴더 스캔 → CLI 세션 DB 동기화
"""

import os
import json
import re
from pathlib import Path
from datetime import datetime
from typing import Optional

from web.backend import database as db
from web.backend.paths import EXE_DIR


def scan_output_dir(output_dir: Optional[str] = None):
    """output/ 디렉토리를 스캔하여 CLI로 생성된 세션을 DB에 임포트."""
    if not output_dir:
        try:
            import config_loader as cfg
            output_dir = cfg.get("output_dir", "./output")
        except Exception:
            output_dir = "./output"

    output_path = Path(EXE_DIR) / output_dir if not os.path.isabs(output_dir) else Path(output_dir)
    if not output_path.exists():
        return

    existing_sessions = {
        os.path.normcase(os.path.normpath(s.get("output_dir", ""))): s
        for s in db.list_sessions() if s.get("output_dir")
    }

    for item in output_path.iterdir():
        if not item.is_dir():
            continue

        dir_path = str(item)
        normalized = os.path.normcase(os.path.normpath(dir_path))
        if normalized in existing_sessions:
            continue

        meta = _find_meta(item)
        title = item.name

        ts_match = re.match(r'(\d{8}_\d{6})', item.name)
        date_str = ""
        if ts_match:
            try:
                dt = datetime.strptime(ts_match.group(1), "%Y%m%d_%H%M%S")
                date_str = dt.isoformat()
                title = item.name[len(ts_match.group(0)):].strip("_ ")
            except ValueError:
                pass

        is_realtime = item.name.startswith("realtime_")
        doc_type = meta.get("doc_type", "meeting") if meta else "meeting"
        language = meta.get("language", "ko") if meta else "ko"
        translate = meta.get("translate", False) if meta else False
        duration = meta.get("duration_sec", 0) if meta else 0

        session_id = db.create_session(
            title=title or item.name,
            topic=meta.get("topic", "") if meta else "",
            doc_type=doc_type,
            language=language,
            translate=translate,
            model=meta.get("stt_model", "") if meta else "",
            source="cli",
            mode="realtime" if is_realtime else "batch",
        )
        db.update_session_status(
            session_id, "completed",
            output_dir=dir_path,
            duration_sec=duration,
            date=date_str or datetime.now().isoformat(),
        )

        # 공통 파일 임포트 로직 사용
        db.import_output_files(session_id, dir_path)


def _find_meta(dir_path: Path) -> Optional[dict]:
    """디렉토리에서 메타데이터 JSON 파일 검색."""
    for f in dir_path.iterdir():
        if f.name.endswith("_meta.json") or f.name == "meta.json":
            try:
                return json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                pass
    return None
