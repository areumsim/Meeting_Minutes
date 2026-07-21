"""
config_loader.py — config.json 통합 로더
============================================================
우선순위:  환경변수  >  config.json  >  기본값

사용법:
    from meeting_minutes_app.common import config_loader as cfg
    key = cfg.get("api.openai_api_key")
    stt = cfg.get("models.stt", "gpt-4o-mini-transcribe")
============================================================
"""

import os
import json
import copy
import sys
from pathlib import Path
from typing import Any, Optional

from meeting_minutes_app.common import app_paths

# 경로 단일 소스(app_paths) 사용 — frozen 시 exe 옆 MeetingMinutesData/config.json,
# dev 시 저장소 루트/config.json. (과거의 __file__ 상위 탐색은 frozen에서 읽기전용
# _MEIPASS를 가리키는 버그가 있었다.)
# _PROJECT_ROOT 는 vault_indexer 등이 상대 경로(data/vault_index.json)를 해석하는
# 기준으로 참조하므로 데이터 베이스로 노출한다.
_PROJECT_ROOT = app_paths.get_base_dir()
_CONFIG_PATH = app_paths.get_config_path()
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
            print(f"[config] ⚠  config.json 파싱 오류: {e}", file=sys.stderr)
            _cache = {}
        except Exception as e:
            print(f"[config] ⚠  config.json 로드 실패: {e}", file=sys.stderr)
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


def set_nested(key_path: str, value: Any, persist: bool = True) -> None:
    """점(.) 구분 키에 값을 설정. persist=True면 config.json에도 즉시 반영한다
    (예: 회의 자동분류가 새 카테고리를 발견해 스스로 등록하는 경우).
    디스크에서 다시 읽어 병합 후 저장해 동시 실행 중인 다른 프로세스의
    무관한 키 변경을 최대한 덮어쓰지 않는다. 로컬 단일 사용자 도구 기준이며
    엄밀한 파일 잠금은 하지 않는다."""
    global _cache
    cfg = _load()
    node = cfg
    parts = key_path.split(".")
    for k in parts[:-1]:
        if not isinstance(node.get(k), dict):
            node[k] = {}
        node = node[k]
    node[parts[-1]] = value

    if not persist:
        return
    on_disk = cfg
    if _CONFIG_PATH.exists():
        try:
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                on_disk = json.load(f)
        except Exception:
            on_disk = cfg
    d_node = on_disk
    for k in parts[:-1]:
        if not isinstance(d_node.get(k), dict):
            d_node[k] = {}
        d_node = d_node[k]
    d_node[parts[-1]] = value
    try:
        with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(on_disk, f, ensure_ascii=False, indent=2)
        _cache = on_disk
    except Exception as e:
        print(f"[config] ⚠  config.json 저장 실패: {e}", file=sys.stderr)


# 사용자가 직접 채우는 맵/리스트 — 마이그레이션 시 내부까지 재귀 병합하지 않고
# 통째로 하나의 값으로 취급한다(삭제한 기본 항목이 되살아나지 않도록).
_OPAQUE_KEYS = {
    "entity_aliases", "entity_alias_patterns", "entity_query_hints",
    "project_domains", "ref_domains", "meeting_categories",
    "allowed_tokens", "watch_folders",
    "domain_keywords", "domain_relevance_keywords",
    "supported_extensions",
}


def _deep_merge_missing(dst: dict, src: dict) -> bool:
    """src 에 있으나 dst 에 없는 키만 dst 에 추가. 기존 dst 값은 절대 덮어쓰지 않는다.
    중첩 dict 는 재귀 병합하되 _OPAQUE_KEYS 는 리프처럼 통째로만 취급.
    변경이 있었으면 True."""
    changed = False
    for k, v in src.items():
        if k not in dst:
            dst[k] = copy.deepcopy(v)
            changed = True
        elif isinstance(v, dict) and isinstance(dst[k], dict) and k not in _OPAQUE_KEYS:
            if _deep_merge_missing(dst[k], v):
                changed = True
    return changed


def migrate() -> bool:
    """config.json 을 최신 스키마로 마이그레이션한다.

    - config.example.json(전체 기본값)을 기준으로 누락된 키만 주입(기존 사용자 값 보존).
    - config_schema 의 필드 기본값도 belt-and-suspenders 로 보장.
    - config_version 을 현재 코드 버전으로 승격.
    변경이 있어 디스크에 기록했으면 True. 실패해도 예외를 던지지 않는다(부팅 차단 방지)."""
    global _cache
    if not _CONFIG_PATH.exists():
        return False

    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            user_cfg = json.load(f)
    except Exception as e:
        print(f"[config] ⚠  마이그레이션: config.json 읽기 실패({e}) — 건너뜀", file=sys.stderr)
        return False
    if not isinstance(user_cfg, dict):
        return False

    changed = False

    # 1) config.example.json 의 전체 기본값에서 누락 키 주입
    try:
        example_path = app_paths.get_example_config_path()
        if example_path.exists():
            with open(example_path, "r", encoding="utf-8") as f:
                defaults = json.load(f)
            if isinstance(defaults, dict):
                if _deep_merge_missing(user_cfg, defaults):
                    changed = True
    except Exception as e:
        print(f"[config] ⚠  마이그레이션: 예시 기본값 병합 건너뜀({e})", file=sys.stderr)

    # 2) 스키마 필드 기본값 보장(예시에 없을 수 있는 UI 전용 키 대비)
    target_version = 1
    try:
        from meeting_minutes_app.common import config_schema
        target_version = config_schema.CONFIG_VERSION
        for field in config_schema.iter_fields():
            section, key = field["section"], field["key"]
            if not key:
                # 최상위 스칼라 필드(예: output_dir) — 중첩 dict가 아니다.
                # 과거 마이그레이션이 이 값을 {"": 값} dict로 변질시켰다면 복구하고,
                # 아예 없으면 기본값을 주입한다. (dict 취급 시 str(dict) 경로 오염 발생)
                cur = user_cfg.get(section)
                if isinstance(cur, dict) and "" in cur:
                    user_cfg[section] = cur[""]
                    changed = True
                elif section not in user_cfg:
                    user_cfg[section] = copy.deepcopy(field.get("default"))
                    changed = True
                continue
            node = user_cfg.get(section)
            if not isinstance(node, dict):
                node = {}
                user_cfg[section] = node
            if key not in node:
                node[key] = copy.deepcopy(field.get("default"))
                changed = True
    except Exception as e:
        print(f"[config] ⚠  마이그레이션: 스키마 기본값 보장 건너뜀({e})", file=sys.stderr)

    # 3) config_version 승격
    if user_cfg.get("config_version") != target_version:
        user_cfg["config_version"] = target_version
        changed = True

    if changed:
        try:
            with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(user_cfg, f, ensure_ascii=False, indent=2)
            _cache = user_cfg
            print(f"[config] config.json 마이그레이션 완료 (config_version={target_version})")
        except Exception as e:
            print(f"[config] ⚠  마이그레이션 저장 실패: {e}", file=sys.stderr)
            return False
    return changed


def reload():
    """config.json 재로드 (런타임 변경 반영용)"""
    global _cache
    _cache = None
    _load()


def exists() -> bool:
    """config.json 파일이 존재하는지 확인"""
    return _CONFIG_PATH.exists()
