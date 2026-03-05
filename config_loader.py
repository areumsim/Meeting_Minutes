"""
config_loader.py — config.json 통합 로더
============================================================
우선순위:  환경변수  >  config.json  >  기본값

사용법:
    import config_loader as cfg
    key = cfg.get("api.openai_api_key")
    stt = cfg.get("models.stt", "gpt-4o-mini-transcribe")
============================================================
"""

import os
import json
from pathlib import Path
from typing import Any, Optional

# config.json 은 이 파일과 같은 폴더에 있어야 함
_CONFIG_PATH = Path(__file__).parent / "config.json"
_cache: Optional[dict] = None


# ── 내부 로드 ─────────────────────────────────
def _load() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    if _CONFIG_PATH.exists():
        try:
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                _cache = json.load(f)
        except json.JSONDecodeError as e:
            print(f"[config] ⚠  config.json 파싱 오류: {e}")
            _cache = {}
        except Exception as e:
            print(f"[config] ⚠  config.json 로드 실패: {e}")
            _cache = {}
    else:
        _cache = {}
    return _cache


# ── 공개 API ──────────────────────────────────
def get(key_path: str, default: Any = None) -> Any:
    """
    점(.) 구분 키로 값 조회.
      cfg.get("api.openai_api_key")
      cfg.get("models.stt", "gpt-4o-mini-transcribe")
    """
    cfg = _load()
    val: Any = cfg
    for k in key_path.split("."):
        if not isinstance(val, dict):
            return default
        val = val.get(k)
        if val is None:
            return default
    return val if val is not None else default


def get_api_key(config_key: str, env_var: str, fallback: str = "") -> str:
    """
    API 키 조회: 환경변수 > config.json > fallback
    예) get_api_key("api.openai_api_key", "OPENAI_API_KEY")
    """
    return (os.environ.get(env_var) or get(config_key) or fallback or "").strip()


def reload():
    """config.json 재로드 (런타임 변경 반영용)"""
    global _cache
    _cache = None
    _load()


def exists() -> bool:
    """config.json 파일이 존재하는지 확인"""
    return _CONFIG_PATH.exists()
