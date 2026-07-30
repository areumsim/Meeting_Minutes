"""
database.py — SQLite 세션/문서/세그먼트 데이터베이스
"""

import sys
import sqlite3
import os
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, List, Dict
from datetime import datetime

# DB 경로는 app_paths 단일 소스 사용 — frozen 시 exe 옆
# MeetingMinutesData/web/meeting_assistant.db, dev 시 web/meeting_assistant.db.
from web.backend.paths import EXE_DIR

DB_PATH = Path(EXE_DIR) / "web" / "meeting_assistant.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def _conn():
    """Context manager guaranteeing connection close."""
    # timeout: 동시 접근(실시간 finalize 스레드 + REST 조회 + revise/번역 워커) 시
    # 잠금 대기 — 5초는 부하 시 'database is locked'가 표면화돼 30초로 상향
    c = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=30.0)
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
                error_detail TEXT,
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
            CREATE TABLE IF NOT EXISTS related_notes (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                note_path TEXT,
                title TEXT,
                heading TEXT,
                section_path TEXT,
                source_type TEXT DEFAULT 'note',
                found_by TEXT,
                score REAL DEFAULT 0,
                rank_score REAL DEFAULT 0,
                hits INTEGER DEFAULT 1,
                snippet TEXT,
                segment_text TEXT,
                elapsed_sec REAL DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_segments_session ON segments(session_id);
            CREATE INDEX IF NOT EXISTS idx_documents_session ON documents(session_id);
            CREATE INDEX IF NOT EXISTS idx_sessions_created ON sessions(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_related_session ON related_notes(session_id);
            CREATE INDEX IF NOT EXISTS idx_related_path ON related_notes(note_path);
        """)
        # 기존 DB 마이그레이션: error_detail 컬럼(실패 원인 표시용)이 없으면 추가.
        try:
            c.execute("ALTER TABLE sessions ADD COLUMN error_detail TEXT")
        except sqlite3.OperationalError:
            pass  # 이미 존재
        # 서버가 (재)시작되는 시점엔 실제로 처리 중인 작업이 있을 수 없다.
        # 이전 실행이 비정상 종료(크래시·강제종료·키 누락 등)돼 'processing'
        # 상태로 고착된 세션을 error 로 정리해 대시보드가 지저분해지는 것을 막는다.
        c.execute("UPDATE sessions SET status='error', error_detail=COALESCE(error_detail, "
                  "'서버가 종료되어 처리가 중단되었습니다. 다시 시도하세요.') "
                  "WHERE status='processing'")
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
    # 새 시도 시작(processing)·성공(completed) 시 이전 실패 원인은 더 이상
    # 유효하지 않으므로 명시 값이 없으면 비운다.
    if status in ("processing", "completed") and "error_detail" not in kwargs:
        kwargs["error_detail"] = None
    with _conn() as c:
        sets = ["status = ?"]
        params: list = [status]
        for k, v in kwargs.items():
            sets.append(f"{k} = ?")
            params.append(v)
        params.append(sid)
        c.execute(f"UPDATE sessions SET {', '.join(sets)} WHERE id = ?", params)
        c.commit()


def month_to_date_spend(now: Optional[datetime] = None) -> float:
    """이번 달(로컬 시각 기준) 1일 00:00 이후의 예상 지출 합(USD).

    지출 한도(cost.monthly_cap_usd) 검사용. 실패(error) 세션은 제외하고 진행 중
    (processing)도 포함해, 동시에 여러 건을 올려 한도를 우회하지 못하게 한다.

    합계 정본은 common/usage_log.py 다 — 세션 비용(sessions.cost_estimate)에 더해
    **세션에 속하지 않는 사용량**(위키 임베딩 등 usage_log)까지 함께 센다. 예전엔
    세션 합계만 봐서, 세션 없이 도는 재빌드·reindex 의 임베딩 과금이 한도 밖에 있었다.
    core 쪽 CLI 도 같은 함수를 쓰므로 웹/CLI 판정 기준이 갈리지 않는다.
    """
    from meeting_minutes_app.common import usage_log
    return usage_log.month_to_date_spend(now, db_path=DB_PATH)


def delete_session(sid: str):
    with _conn() as c:
        c.execute("DELETE FROM segments WHERE session_id = ?", (sid,))
        c.execute("DELETE FROM documents WHERE session_id = ?", (sid,))
        c.execute("DELETE FROM related_notes WHERE session_id = ?", (sid,))
        c.execute("DELETE FROM sessions WHERE id = ?", (sid,))
        c.commit()


def clear_all_sessions():
    with _conn() as c:
        c.execute("DELETE FROM segments")
        c.execute("DELETE FROM documents")
        c.execute("DELETE FROM related_notes")
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
            # CLI 세그먼트(JSONL) 형식: 번역 세션은 text=번역, text_original=원문.
            # 과거엔 translated_text 자리에 text_original(원문)을 넣어, 번역 없는
            # 세션에서 '번역' 칸에 영어 원문이 그대로 들어갔다. 명시 translated_text 가
            # 없으면 text≠text_original 인 경우에만 (원문, 번역) 순으로 재배치한다.
            text = seg.get("text", "")
            orig = seg.get("text_original", "")
            translated = seg.get("translated_text", "")
            if not translated and orig and text != orig:
                text, translated = orig, text
            c.execute(
                """INSERT INTO segments (id, session_id, speaker, text, translated_text,
                   start_time, end_time) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (_new_id(), session_id,
                 seg.get("speaker", ""),
                 text,
                 translated,
                 seg.get("start", seg.get("start_time", 0)),
                 seg.get("end", seg.get("end_time", 0))),
            )
        c.commit()


def update_segment_translation(session_id: str, start_time: float, translated_text: str):
    """start_time으로 세그먼트를 찾아 번역만 갱신 — 실시간(WS) 비동기 번역 반영용.

    부동소수 오차 대비 ±5ms 허용. (과거 WS 경로는 번역을 메모리/화면에만 채워
    세션을 다시 열면 번역이 사라졌다.)
    """
    with _conn() as c:
        c.execute(
            """UPDATE segments SET translated_text = ?
               WHERE session_id = ? AND ABS(start_time - ?) < 0.005""",
            (translated_text, session_id, start_time),
        )
        c.commit()


def replace_segments(session_id: str, segments: List[Dict]):
    """세션의 기존 세그먼트를 전부 지우고 새 세그먼트로 교체 (화자분리 후처리용)."""
    with _conn() as c:
        c.execute("DELETE FROM segments WHERE session_id = ?", (session_id,))
        c.commit()
    add_segments_bulk(session_id, segments)


def replace_segments_range(session_id: str, t0: float, t1: float, segments: List[Dict]):
    """[t0, t1) 구간(start_time 기준)의 세그먼트를 삭제 후 새 세그먼트로 교체.

    실시간 2-pass 보정용 — 빠른 패스의 조각 세그먼트를 보정 패스의 문장 세그먼트로
    바꾼다. add_segments_bulk 를 재사용하지 않는 이유: 그쪽의
    translated_text ← text_original 폴백이 보정 세그먼트에서 원문(영어)을 번역
    칸에 넣어버리기 때문. 여기서는 translated_text 를 명시 값 그대로만 기록한다.
    """
    with _conn() as c:
        c.execute(
            "DELETE FROM segments WHERE session_id = ? AND start_time >= ? AND start_time < ?",
            (session_id, t0, t1),
        )
        for seg in segments:
            c.execute(
                """INSERT INTO segments (id, session_id, speaker, text, translated_text,
                   start_time, end_time) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (_new_id(), session_id,
                 seg.get("speaker", ""),
                 seg.get("text", ""),
                 seg.get("translated_text", ""),
                 seg.get("start", seg.get("start_time", 0)),
                 seg.get("end", seg.get("end_time", 0))),
            )
        c.commit()


# ── Documents ─────────────────────────────────────

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


# ── Related notes (실시간 관련 노트 누적 — 사이드카) ──────────
#
# 회의 중 실시간 검색이 찾아낸 관련 노트를 근거(점수·섹션·snippet·발화·경과시각)와
# 함께 영속화한다. 과거엔 프런트 React state에만 있어 정지/재시작 시 사라지고
# 회의록엔 제목만 남았다. vault 원본은 건드리지 않는다(사이드카 원칙).

def add_related_notes(session_id: str, rows: List[Dict]) -> int:
    """세션의 관련 노트 근거를 저장한다. 같은 노트가 이미 있으면 갱신(누적 재실행 대비).

    반환: 기록된 행 수. 실패는 호출자가 판단하도록 예외를 그대로 올린다
    (호출부는 finalize 부가 스테이지라 이미 try/except 로 감싼다).
    """
    if not session_id or not rows:
        return 0
    written = 0
    with _conn() as c:
        for r in rows:
            note_path = str(r.get("filename") or r.get("note_path") or "")
            title = str(r.get("title") or "")
            if not (note_path or title):
                continue
            existing = c.execute(
                "SELECT id FROM related_notes WHERE session_id = ? AND note_path = ?",
                (session_id, note_path),
            ).fetchone()
            vals = (
                title,
                str(r.get("heading") or ""),
                str(r.get("section_path") or ""),
                str(r.get("source_type") or "note"),
                str(r.get("found_by") or ""),
                float(r.get("score") or 0),
                float(r.get("rank_score") or 0),
                int(r.get("hits") or 1),
                str(r.get("snippet") or "")[:400],
                str(r.get("segment_text") or "")[:400],
                float(r.get("elapsed_sec") or 0),
            )
            if existing:
                c.execute(
                    """UPDATE related_notes SET title=?, heading=?, section_path=?,
                       source_type=?, found_by=?, score=?, rank_score=?, hits=?,
                       snippet=?, segment_text=?, elapsed_sec=? WHERE id=?""",
                    vals + (existing["id"],),
                )
            else:
                c.execute(
                    """INSERT INTO related_notes (id, session_id, note_path, title,
                       heading, section_path, source_type, found_by, score, rank_score,
                       hits, snippet, segment_text, elapsed_sec)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (_new_id(), session_id, note_path) + vals,
                )
            written += 1
        c.commit()
    return written


def get_related_notes(session_id: str) -> List[Dict]:
    """이 회의에서 참조된 관련 노트 — 관련도(rank_score) 내림차순."""
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM related_notes WHERE session_id = ? "
            "ORDER BY rank_score DESC, score DESC",
            (session_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def related_notes_cross_sessions(limit: int = 10,
                                 recent_sessions: int = 20) -> List[Dict]:
    """교차 회의 집계 — 최근 N개 회의에서 각 노트가 몇 번(몇 개 회의에서) 참조됐나.

    "이 노트가 계속 언급된다"를 드러내 위키 보강 우선순위를 잡는 데 쓴다.
    recent_sessions 로 시간창을 제한해 오래된 회의가 순위를 고정하지 않게 한다.
    """
    with _conn() as c:
        rows = c.execute(
            """SELECT r.note_path AS note_path,
                      MAX(r.title) AS title,
                      MAX(r.source_type) AS source_type,
                      COUNT(DISTINCT r.session_id) AS session_count,
                      SUM(r.hits) AS total_hits,
                      MAX(s.date) AS last_date
               FROM related_notes r
               JOIN (SELECT id, date FROM sessions
                     ORDER BY created_at DESC, rowid DESC LIMIT ?) s ON s.id = r.session_id
               GROUP BY r.note_path
               ORDER BY session_count DESC, total_hits DESC
               LIMIT ?""",
            (max(1, recent_sessions), max(1, limit)),
        ).fetchall()
    return [dict(r) for r in rows]


# ── File Import (공통 로직 — batch.py, session_scanner.py에서 사용) ──

DOC_TYPE_MAP = {
    "minutes": "minutes",
    "summary": "summary",
    "script": "script",
    "refined_script": "refined_script",
    "actions": "actions",
    "fact_check": "fact_check",
    "wiki_context": "wiki_context",
    "wiki_proposal": "wiki_proposal",
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

        # JSON 문서 임포트
        elif fname.endswith(".json") and any(key in fname.lower() for key in ("wiki_context", "wiki_proposal", "actions")):
            for key, doc_type in DOC_TYPE_MAP.items():
                if key in fname.lower():
                    try:
                        with open(fpath, "r", encoding="utf-8") as f:
                            content = f.read()
                        if content.strip():
                            upsert_document(session_id, doc_type, content, "json")
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
