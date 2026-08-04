"""테스트 전역 안전장치.

**테스트는 사용자의 실제 DB에 과금을 기록하지 않는다.**

이 파일이 없던 동안 실제로 이런 일이 있었다: `tests/test_spend_guard.py` 의
`TestFirstScanQueue` 는 `usage_db` 픽스처를 쓰지 않아서, 워처가 파일을 처리하는 경로가
`spend_guard.record()` → `usage_log`(= `app_paths.get_db_path()` = 개발 리포의
`web/meeting_assistant.db`) 로 **진짜 과금 행을 남겼다**. 전체 스위트를 한 번 돌릴 때마다
가짜 '폴더 자동 처리' 지출이 몇 달러씩 쌓였고(발견 시점 usage_log 361행 ≈ $112),
`spend_guard.blocked()` 는 그 합계를 보고 한도를 판정하므로 **다른 경로의 한도 판정까지
왜곡**됐다. 사용자에게는 쓰지도 않은 돈이 비용 대시보드에 보인다.

개별 테스트마다 격리를 기억하게 하는 대신 여기서 한 번에 막는다 — 새 테스트가 과금
경로를 지나도 자동으로 안전하다. 자기 DB 경로가 필요한 테스트는 지금처럼
`usage_log._resolve_db_path` 를 직접 monkeypatch 하면 된다(그 패치가 뒤에 적용돼 이긴다).

`web.backend.database.DB_PATH` 는 여기서 건드리지 않는다 — 세션 테이블은 각 테스트가
이미 스스로 격리하고 있고(실측: 실제 DB 의 sessions 5건 전부 사용자 데이터),
전역으로 빈 임시 DB 를 물리면 `init_db()` 를 부르지 않는 테스트가 "no such table" 로
깨진다. 즉 여기 있는 것은 **실제로 유출이 확인된 경로**뿐이다.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_usage_log(tmp_path_factory, monkeypatch):
    """모든 테스트의 usage_log 기록을 임시 DB 로 돌린다(과금 오염 차단)."""
    from pathlib import Path

    from meeting_minutes_app.common import usage_log
    dbp = tmp_path_factory.mktemp("usage") / "meeting_assistant.db"
    # 명시적으로 넘어온 db_path 는 그대로 존중한다 — `db.month_to_date_spend()` 는
    # 세션 DB 경로를 직접 넘기므로(자기 격리), 그걸 무시하면 그 테스트들이 빈 DB 를
    # 읽는다. 기본 경로(=사용자 실제 DB)만 임시 파일로 돌린다.
    monkeypatch.setattr(usage_log, "_resolve_db_path",
                        lambda p=None: Path(p) if p else dbp)
