"""usage_log.py — 세션에 속하지 않는 API 사용량·비용 기록.

월 지출한도(`cost.monthly_cap_usd`)는 지금까지 `sessions.cost_estimate` 합계만
봤다. 그런데 위키 임베딩은 세션이 아니다 — 웹 [검색 인덱스·그래프 재빌드],
폴더 연결 직후 자동 인덱싱, 앱 시작 시 자동 인덱싱, CLI `reindex` 가 전부
세션 없이 OpenAI 임베딩 API 를 부른다. 그래서 그 과금이 **한도 밖에서** 일어났다.

`sessions` 에 합성 행을 넣는 방법은 쓰지 않는다 — 그러면 대시보드 목록·
session_scanner·clear_all_sessions 등 sessions 를 읽는 모든 곳이 오염된다.
별도 테이블이면 합산 지점 한 곳만 고치면 된다.

core 가 `web.backend` 를 import 하지 않는 현 구조는 유지한다. 같은 sqlite 파일을
`app_paths.get_db_path()` 로 직접 열 뿐이다(wiki_core/graph_db.py 와 같은 방식).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    kind      TEXT NOT NULL,
    model     TEXT,
    units     REAL,
    unit_kind TEXT,
    cost_usd  REAL NOT NULL DEFAULT 0,
    note      TEXT
);
CREATE INDEX IF NOT EXISTS idx_usage_log_ts ON usage_log(ts);
"""


def _resolve_db_path(db_path: Optional[Union[str, Path]] = None) -> Optional[Path]:
    if db_path:
        return Path(db_path)
    try:
        from meeting_minutes_app.common.app_paths import get_db_path
        return get_db_path()
    except Exception:
        return None


def _connect(db_path: Optional[Union[str, Path]] = None) -> Optional[sqlite3.Connection]:
    """접속 정책(WAL·timeout·실패 시 None)은 common/sqlite_util 하나만 쓴다 —
    facilitation.py 에 같은 코드가 복제돼 있던 자리다."""
    from meeting_minutes_app.common import sqlite_util
    return sqlite_util.connect(_resolve_db_path(db_path))


def record(kind: str, model: str = "", units: float = 0.0, unit_kind: str = "",
           cost_usd: float = 0.0, note: str = "",
           db_path: Optional[Union[str, Path]] = None) -> bool:
    """사용량 1건 기록. 실패해도 예외를 올리지 않는다 — 기록 실패가 인덱싱을
    멈추게 하면 안 된다(비용 기록은 부수 효과이지 본 작업이 아니다)."""
    c = _connect(db_path)
    if c is None:
        return False
    try:
        with c:
            c.executescript(_SCHEMA)
            c.execute(
                "INSERT INTO usage_log (ts, kind, model, units, unit_kind, cost_usd, note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (datetime.now().isoformat(timespec="seconds"), kind, model,
                 float(units or 0), unit_kind, float(cost_usd or 0), note),
            )
        return True
    except sqlite3.Error:
        return False
    finally:
        c.close()


def month_to_date_spend(now: Optional[datetime] = None,
                        db_path: Optional[Union[str, Path]] = None) -> float:
    """이번 달 세션 비용 + 세션 밖 사용량(임베딩 등)의 합(USD) — **한도 판정 정본**.

    구버전 DB 에는 usage_log 테이블이 없다(포터블 배포본을 덮어쓴 사용자). 그 경우
    세션 합계만 돌려주고 조용히 넘어간다 — 업그레이드가 한도 검사를 깨면 안 된다.
    """
    now = now or datetime.now()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    c = _connect(db_path)
    if c is None:
        return 0.0
    total = 0.0
    try:
        row = c.execute(
            "SELECT COALESCE(SUM(cost_estimate), 0) FROM sessions "
            "WHERE date >= ? AND status != 'error'", (start,),
        ).fetchone()
        total += float(row[0] or 0.0)
    except sqlite3.Error:
        pass                              # sessions 가 아직 없는 새 DB
    try:
        row = c.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM usage_log WHERE ts >= ?",
            (start,),
        ).fetchone()
        total += float(row[0] or 0.0)
    except sqlite3.Error:
        pass                              # usage_log 없는 구버전 DB
    finally:
        c.close()
    return total


def session_spend(session_id: str, kind: Optional[str] = None,
                  db_path: Optional[Union[str, Path]] = None) -> float:
    """특정 세션에 딸린 usage_log 지출 합(USD). 기간 제한 없음.

    이 테이블은 원래 '세션 없는 사용량'을 위한 것이라 session_id 컬럼이 없다. 그런데
    회의 진행 페르소나처럼 **세션 중에 발생하지만 sessions.cost_estimate 에는 넣지
    않는** 과금이 생겼다 — 넣으면 month_to_date_spend() 가 둘을 더해 이중 집계된다.
    그래서 note 에 세션 키를 남기고(`spend_guard.session_note()`) 여기서 되찾는다.
    이 함수가 없으면 회의 상세의 비용이 실제보다 적게 보인다(그 회의가 쓴 돈인데
    '회의 외 지출'에만 잡힌다).
    """
    sid = str(session_id or "").strip()
    if not sid:
        return 0.0
    from meeting_minutes_app.common import spend_guard
    note = spend_guard.session_note(sid)
    c = _connect(db_path)
    if c is None:
        return 0.0
    try:
        sql = "SELECT COALESCE(SUM(cost_usd), 0) FROM usage_log WHERE note = ?"
        params: tuple = (note,)
        if kind:
            sql += " AND kind = ?"
            params = (note, kind)
        row = c.execute(sql, params).fetchone()
        return float(row[0] or 0.0)
    except sqlite3.Error:
        return 0.0                        # usage_log 없는 구버전 DB
    finally:
        c.close()


def session_spend_by_kind(session_id: str,
                          db_path: Optional[Union[str, Path]] = None) -> dict:
    """특정 세션에 딸린 usage_log 지출을 kind 별로 합산.

    `session_spend()` 를 kind 마다 부르는 대신 이것을 쓴다. 호출부가 kind 를 하나씩
    적으면 새 kind 가 생길 때마다 빠뜨리기 때문이다 — 실제로 회의 상세 화면이
    `facilitation` 만 되찾고 `web_research`(회의 중 웹 검색 보완)를 빠뜨려서, 그
    회의가 쓴 돈인데도 '세션에 잡히지 않는 지출'에만 보였다.
    """
    sid = str(session_id or "").strip()
    if not sid:
        return {}
    from meeting_minutes_app.common import spend_guard
    note = spend_guard.session_note(sid)
    c = _connect(db_path)
    if c is None:
        return {}
    try:
        rows = c.execute(
            "SELECT kind, COALESCE(SUM(cost_usd), 0) FROM usage_log "
            "WHERE note = ? GROUP BY kind", (note,),
        ).fetchall()
        return {str(k): float(v or 0.0) for k, v in rows if float(v or 0.0)}
    except sqlite3.Error:
        return {}                         # usage_log 없는 구버전 DB
    finally:
        c.close()


def month_to_date_by_kind(now: Optional[datetime] = None,
                          db_path: Optional[Union[str, Path]] = None) -> dict:
    """이번 달 usage_log 를 kind 별로 합산(대시보드 표시용)."""
    now = now or datetime.now()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    c = _connect(db_path)
    if c is None:
        return {}
    try:
        rows = c.execute(
            "SELECT kind, COALESCE(SUM(cost_usd), 0) FROM usage_log "
            "WHERE ts >= ? GROUP BY kind", (start,),
        ).fetchall()
        return {str(k): float(v or 0.0) for k, v in rows}
    except sqlite3.Error:
        return {}
    finally:
        c.close()
