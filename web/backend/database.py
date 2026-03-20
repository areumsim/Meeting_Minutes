"""
database.py — SQLite 세션/문서/세그먼트 데이터베이스
"""

import sys
import sqlite3
import os
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime

if getattr(sys, 'frozen', False):
    DB_PATH = Path(sys.executable).parent / "web" / "meeting_assistant.db"
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
else:
    DB_PATH = Path(__file__).parent.parent / "meeting_assistant.db"


@contextmanager
def _conn():
    """Context manager guaranteeing connection close."""
    c = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    try:
        yield c
    finally:
        c.close()


def init_db():
    with _conn() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                title TEXT,
                topic TEXT,
                date TEXT,
                type TEXT DEFAULT 'meeting',
                status TEXT DEFAULT 'pending',
                language TEXT DEFAULT 'ko',
                translate INTEGER DEFAULT 0,
                model TEXT,
                speakers TEXT,
                file_path TEXT,
                output_dir TEXT,
                source TEXT DEFAULT 'web',
                mode TEXT,
                cost_estimate REAL DEFAULT 0,
                duration_sec REAL DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS segments (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                speaker TEXT,
                text TEXT,
                translated_text TEXT,
                start_time REAL,
                end_time REAL,
                FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                type TEXT,
                content TEXT,
                format TEXT DEFAULT 'markdown',
                FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_segments_session ON segments(session_id);
            CREATE INDEX IF NOT EXISTS idx_documents_session ON documents(session_id);
            CREATE INDEX IF NOT EXISTS idx_sessions_created ON sessions(created_at DESC);
        """)
        c.commit()


def _new_id() -> str:
    return str(uuid.uuid4())


# ── Sessions ───────────────────────────────────────

def create_session(
    title: str,
    topic: str = "",
    doc_type: str = "meeting",
    language: str = "ko",
    translate: bool = False,
    model: str = "",
    speakers: str = "",
    file_path: str = "",
    source: str = "web",
    mode: str = "",
) -> str:
    sid = _new_id()
    with _conn() as c:
        c.execute(
            """INSERT INTO sessions (id, title, topic, date, type, status, language,
               translate, model, speakers, file_path, source, mode)
               VALUES (?, ?, ?, ?, ?, 'processing', ?, ?, ?, ?, ?, ?, ?)""",
            (sid, title, topic, datetime.now().isoformat(), doc_type, language,
             int(translate), model, speakers, file_path, source, mode),
        )
        c.commit()
    return sid


def get_session(sid: str) -> Optional[Dict]:
    with _conn() as c:
        row = c.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
    return dict(row) if row else None


def list_sessions(search: str = "", type_filter: str = "") -> List[Dict]:
    with _conn() as c:
        q = "SELECT * FROM sessions WHERE 1=1"
        params: list = []
        if search:
            q += " AND (title LIKE ? OR topic LIKE ?)"
            params += [f"%{search}%", f"%{search}%"]
        if type_filter:
            q += " AND type = ?"
            params.append(type_filter)
        q += " ORDER BY created_at DESC"
        rows = c.execute(q, params).fetchall()
    return [dict(r) for r in rows]


def update_session_status(sid: str, status: str, **kwargs):
    with _conn() as c:
        sets = ["status = ?"]
        params: list = [status]
        for k, v in kwargs.items():
            sets.append(f"{k} = ?")
            params.append(v)
        params.append(sid)
        c.execute(f"UPDATE sessions SET {', '.join(sets)} WHERE id = ?", params)
        c.commit()


def delete_session(sid: str):
    with _conn() as c:
        c.execute("DELETE FROM segments WHERE session_id = ?", (sid,))
        c.execute("DELETE FROM documents WHERE session_id = ?", (sid,))
        c.execute("DELETE FROM sessions WHERE id = ?", (sid,))
        c.commit()


def clear_all_sessions():
    with _conn() as c:
        c.execute("DELETE FROM segments")
        c.execute("DELETE FROM documents")
        c.execute("DELETE FROM sessions")
        c.commit()


# ── Segments ──────────────────────────────────────

def add_segment(session_id: str, speaker: str, text: str,
                start_time: float, end_time: float,
                translated_text: str = "") -> str:
    seg_id = _new_id()
    with _conn() as c:
        c.execute(
            """INSERT INTO segments (id, session_id, speaker, text, translated_text,
               start_time, end_time) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (seg_id, session_id, speaker, text, translated_text, start_time, end_time),
        )
        c.commit()
    return seg_id


def get_segments(session_id: str) -> List[Dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM segments WHERE session_id = ? ORDER BY start_time",
            (session_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def add_segments_bulk(session_id: str, segments: List[Dict]):
    with _conn() as c:
        for seg in segments:
            c.execute(
                """INSERT INTO segments (id, session_id, speaker, text, translated_text,
                   start_time, end_time) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (_new_id(), session_id,
                 seg.get("speaker", ""),
                 seg.get("text", ""),
                 seg.get("translated_text", seg.get("text_original", "")),
                 seg.get("start", seg.get("start_time", 0)),
                 seg.get("end", seg.get("end_time", 0))),
            )
        c.commit()


# ── Documents ─────────────────────────────────────

def add_document(session_id: str, doc_type: str, content: str,
                 fmt: str = "markdown") -> str:
    doc_id = _new_id()
    with _conn() as c:
        c.execute(
            "INSERT INTO documents (id, session_id, type, content, format) VALUES (?, ?, ?, ?, ?)",
            (doc_id, session_id, doc_type, content, fmt),
        )
        c.commit()
    return doc_id


def get_documents(session_id: str) -> List[Dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM documents WHERE session_id = ?", (session_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def upsert_document(session_id: str, doc_type: str, content: str,
                    fmt: str = "markdown"):
    with _conn() as c:
        existing = c.execute(
            "SELECT id FROM documents WHERE session_id = ? AND type = ?",
            (session_id, doc_type),
        ).fetchone()
        if existing:
            c.execute(
                "UPDATE documents SET content = ?, format = ? WHERE id = ?",
                (content, fmt, existing["id"]),
            )
        else:
            c.execute(
                "INSERT INTO documents (id, session_id, type, content, format) VALUES (?, ?, ?, ?, ?)",
                (_new_id(), session_id, doc_type, content, fmt),
            )
        c.commit()


# ── File Import (공통 로직 — batch.py, session_scanner.py에서 사용) ──

DOC_TYPE_MAP = {
    "minutes": "minutes",
    "summary": "summary",
    "script": "script",
    "refined_script": "refined_script",
    "actions": "actions",
    "transcript": "transcript",
}


def import_output_files(session_id: str, output_dir: str):
    """output 디렉토리의 결과 파일을 DB에 임포트 (공통 로직)."""
    import json as _json

    if not os.path.isdir(output_dir):
        return

    for fname in os.listdir(output_dir):
        fpath = os.path.join(output_dir, fname)
        if not os.path.isfile(fpath):
            continue

        # 텍스트 문서 임포트
        if fname.endswith((".md", ".txt")):
            for key, doc_type in DOC_TYPE_MAP.items():
                if key in fname.lower():
                    try:
                        with open(fpath, "r", encoding="utf-8") as f:
                            content = f.read()
                        if content.strip():
                            upsert_document(session_id, doc_type, content)
                    except Exception:
                        pass
                    break

        # segments.json 임포트
        elif fname.endswith("segments.json") and "translated" not in fname:
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    segments = _json.load(f)
                add_segments_bulk(session_id, segments)
            except Exception:
                pass
