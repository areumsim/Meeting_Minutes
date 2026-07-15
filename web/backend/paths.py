"""
paths.py — 공통 경로 설정 (AR_ROOT, frozen 모드 지원)

AR_ROOT : 소스/번들 리소스 루트 (_MEIPASS 또는 저장소 루트) — sys.path 설정용.
EXE_DIR : 쓰기 가능한 데이터 베이스 (frozen=exe 옆 MeetingMinutesData/, dev=저장소 루트).
          meeting_minutes_app.common.app_paths.get_base_dir()에 위임한다 —
          웹 백엔드/파이프라인이 같은 데이터 폴더를 보도록 단일화.
"""

import sys
from pathlib import Path


def _get_ar_root() -> str:
    """소스/번들 리소스 루트 반환."""
    if getattr(sys, 'frozen', False):
        return str(Path(sys._MEIPASS))
    return str(Path(__file__).parent.parent.parent)


AR_ROOT = _get_ar_root()
APP_DIR = str(Path(AR_ROOT) / "meeting_minutes_app")

# meeting_minutes_app import가 가능하도록 sys.path를 먼저 설정한 뒤 app_paths를 로드한다.
if AR_ROOT not in sys.path:
    sys.path.insert(0, AR_ROOT)
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from meeting_minutes_app.common import app_paths as _app_paths

# 쓰기 가능한 데이터 베이스 (config.json, output/, data/, web/uploads, DB 등이 이 아래).
EXE_DIR = str(_app_paths.get_base_dir())
