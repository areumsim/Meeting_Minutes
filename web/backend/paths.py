"""
paths.py — 공통 경로 설정 (AR_ROOT, frozen 모드 지원)
"""

import sys
from pathlib import Path


def _get_ar_root() -> str:
    """ar_transcription 루트 디렉토리 반환."""
    if getattr(sys, 'frozen', False):
        return str(Path(sys._MEIPASS))
    return str(Path(__file__).parent.parent.parent)


def _get_exe_dir() -> str:
    """실행 파일(exe) 또는 프로젝트 루트 디렉토리 반환."""
    if getattr(sys, 'frozen', False):
        return str(Path(sys.executable).parent)
    return str(Path(__file__).parent.parent.parent)


AR_ROOT = _get_ar_root()
EXE_DIR = _get_exe_dir()

if AR_ROOT not in sys.path:
    sys.path.insert(0, AR_ROOT)
