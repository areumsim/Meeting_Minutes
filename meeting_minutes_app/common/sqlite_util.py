"""sqlite_util.py — core 가 web DB(sqlite)를 **직접** 열 때의 공통 접속 정책.

`usage_log`(과금 집계)와 `wiki_core.facilitation`(관찰 로그)은 `web.backend.database`
를 import 하지 않고 같은 sqlite 파일을 직접 연다 — core → web 단방향 의존을 유지하려는
의도적 구조다(각 모듈 독스트링 참조). 그 대가로 접속 정책이 두 모듈에 **문자 단위로
복제**돼 있었다. 이 리포가 반복해서 대가를 치른 "같은 규칙이 복사돼 갈라진다"(단가 표
4곳, 노트 판정 2곳, CLAUDE.md) 바로 그 패턴이라 한 곳으로 모은다.

정책은 복제본과 동일하게 유지한다(행동 변화 없음):
  - `timeout=30.0` — 실시간 finalize 스레드·REST 조회가 같은 파일을 공유한다
    (`web/backend/database.py` 와 같은 값).
  - `check_same_thread=False` — 스레드풀에서 기록한다.
  - `journal_mode=WAL` — 읽기와 쓰기가 서로를 막지 않는다.
  - **실패 시 예외가 아니라 `None`** — 이 경로들의 기록·집계는 부수 효과이지 본 작업이
    아니다. 기록 실패가 전사·인덱싱을 멈추게 하면 안 된다.

**`wiki_core/graph_db.py` 는 여기로 합치지 않는다.** 다른 파일(`data/wiki_graph.db`)을
열고 정책이 실제로 다르다 — `timeout=5.0`, `row_factory`, `foreign_keys=ON`, 그리고
실패 시 **예외를 올린다**(그래프 쓰기는 조용히 누락되면 안 되는 본 작업이다).
겉모습이 비슷하다는 이유로 합치면 실패 정책이 뒤집힌다.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional, Union


def resolve_db_path(db_path: Optional[Union[str, Path]] = None) -> Optional[Path]:
    """명시 경로 > `app_paths.get_db_path()` > None.

    `usage_log` 와 `wiki_core.facilitation` 에 **문자 단위로 같은 함수**가 있던 자리다
    (테스트가 이 이름을 monkeypatch 하므로 각 모듈은 얇은 래퍼를 유지한다 — 래퍼를
    없애면 기존 테스트의 주입 지점이 사라진다).
    """
    if db_path:
        return Path(db_path)
    try:
        from meeting_minutes_app.common.app_paths import get_db_path
        return get_db_path()
    except Exception:
        return None


def connect(path: Optional[Union[str, Path]]) -> Optional[sqlite3.Connection]:
    """WAL·timeout 정책을 적용해 연결. 경로가 없거나 열지 못하면 None."""
    if path is None:
        return None
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(str(p), check_same_thread=False, timeout=30.0)
        c.execute("PRAGMA journal_mode=WAL")
        return c
    except (sqlite3.Error, OSError):
        # OSError: 복제본은 mkdir 실패를 잡지 않아 호출부로 예외가 샜다
        # (읽기 전용 폴더·경로 길이 초과). 여기서 None 으로 수렴시킨다.
        return None


def ensure_columns(conn: sqlite3.Connection, table: str,
                   columns: dict) -> None:
    """없는 컬럼만 ALTER TABLE 로 추가(구버전 DB 승급). 실패는 무시.

    `columns` = {"컬럼명": "TEXT DEFAULT ''"} 형태. sqlite 는 ADD COLUMN 만
    지원하므로 타입 변경·삭제는 대상이 아니다 — 새 컬럼 추가만 쓴다.
    """
    try:
        have = {str(r[1]) for r in conn.execute(
            f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.Error:
        return
    if not have:
        return                      # 테이블 자체가 없다 — CREATE 가 할 일이다
    for name, decl in columns.items():
        if name in have:
            continue
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
        except sqlite3.Error:
            pass
