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

#: 파일이 있는데 읽지 못했을 때의 사유. None 이면 정상(파일 없음도 정상으로 본다).
#:
#: 이 플래그가 왜 필요한가 — 예전에는 파싱 실패 시 `_cache = {}` 로 폴백했다. 그러면
#: 이후 어떤 `set_nested(persist=True)` 든 **모든 설정이 사라진 config.json 을 기록**했다.
#: 사용자 입장에서는 "설정 하나 바꿨는데 API 키까지 전부 날아갔다"가 된다.
#: 빈 dict 는 "설정이 없다"와 "읽지 못했다"를 구분하지 못하므로 별도로 기억한다.
_load_error: Optional[str] = None


class ConfigCorrupted(RuntimeError):
    """config.json 을 읽지 못한 상태에서 저장을 시도했다.

    덮어쓰면 사용자 설정 전체가 사라지므로 저장하지 않고 이 예외를 올린다.
    """


# ── 내부 로드 ─────────────────────────────────
def _load() -> dict:
    global _cache, _load_error
    if _cache is not None:
        return _cache
    _load_error = None
    if _CONFIG_PATH.exists():
        try:
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                _cache = json.load(f)
        except json.JSONDecodeError as e:
            # 읽기는 계속 진행한다(기본값으로 앱은 떠야 한다). 다만 **저장은 막는다** —
            # 아래 _load_error 를 set_nested/migrate 가 확인한다.
            print(f"[config] ⚠  config.json 파싱 오류: {e}", file=sys.stderr)
            print(f"[config] ⚠  설정을 읽지 못해 기본값으로 동작합니다. "
                  f"이 상태에서는 설정을 저장하지 않습니다(기존 파일 보호).",
                  file=sys.stderr)
            _load_error = f"JSON 파싱 오류: {e}"
            _cache = {}
        except Exception as e:
            print(f"[config] ⚠  config.json 로드 실패: {e}", file=sys.stderr)
            _load_error = f"파일 읽기 실패: {e}"
            _cache = {}
    else:
        _cache = {}          # 파일 없음 = 첫 실행. 저장해도 잃을 것이 없다.
    return _cache


def load_error() -> Optional[str]:
    """config 를 읽지 못했으면 사유, 정상이면 None. 웹 진단·설정 화면이 쓴다."""
    _load()
    return _load_error


def _quarantine_corrupt_file() -> Optional[Path]:
    """손상된 config.json 을 `.corrupt-<타임스탬프>` 로 옮겨 사용자가 복구할 수 있게 한다.

    지우지 않는다 — 그 안에 사용자가 손으로 넣은 키가 들어 있을 수 있고, 부분 손상이면
    사람이 보고 살릴 수 있다.
    """
    from datetime import datetime
    try:
        dst = _CONFIG_PATH.with_suffix(
            _CONFIG_PATH.suffix + f".corrupt-{datetime.now():%Y%m%d_%H%M%S}")
        _CONFIG_PATH.replace(dst)
        print(f"[config] 손상된 설정을 {dst.name} 로 보관했습니다.", file=sys.stderr)
        return dst
    except Exception as e:
        print(f"[config] ⚠  손상 파일 보관 실패: {e}", file=sys.stderr)
        return None


def _atomic_write(data: dict) -> None:
    """config.json 을 원자적으로 교체한다. 저장은 전부 `save()` 를 지나 여기로 모인다.

    예전에는 **다섯 곳**(set_nested / migrate / web settings.update_config /
    cli_init 의 init·mcp-token)이 각자 `open(path, "w")` 로 **제자리 덮어쓰기**를 했다.
    (PRD FR-004 개정은 이를 3곳으로 셌는데, cli_init 의 두 곳이 빠진 오산이었다.)
    쓰는 중에 프로세스가 죽으면 config.json 이 잘린 JSON 으로 남아 다음 실행에서
    파싱 실패한다 — 그리고 그때 빈 dict 폴백이 나머지 설정을 지웠다(두 결함이 연쇄했다).

    순서: 같은 디렉터리에 tmp 작성 → flush+fsync → 기존 파일을 .bak 로 보존 →
    os.replace(원자적) → 디렉터리 fsync.
    tmp 를 같은 디렉터리에 두는 이유는 os.replace 가 같은 볼륨에서만 원자적이기 때문이다.
    """
    import tempfile

    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=".config.", suffix=".tmp", dir=str(_CONFIG_PATH.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")               # cli_init 이 쓰던 형식과 동일하게 유지
            f.flush()
            os.fsync(f.fileno())
        # 마지막 정상 설정을 한 벌 남긴다(FR-004). 실패해도 저장은 계속한다.
        if _CONFIG_PATH.exists():
            try:
                import shutil
                shutil.copy2(_CONFIG_PATH, _CONFIG_PATH.with_suffix(
                    _CONFIG_PATH.suffix + ".bak"))
            except Exception as e:
                print(f"[config] 백업 생성 실패(저장은 계속): {e}", file=sys.stderr)
        os.replace(tmp, _CONFIG_PATH)
        tmp = None                      # replace 성공 → 정리 대상 아님
        # 디렉터리 엔트리까지 내려써야 크래시 후에도 교체가 남는다(POSIX). Windows 에서는
        # 디렉터리 fd 를 열 수 없어 조용히 건너뛴다.
        try:
            dfd = os.open(str(_CONFIG_PATH.parent), os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except (OSError, AttributeError):
            pass
    finally:
        if tmp is not None and tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _guard_save(op: str) -> None:
    """손상 상태에서의 저장을 막는다. 저장 경로 전부가 이 관문을 지난다."""
    _load()
    if _load_error:
        raise ConfigCorrupted(
            f"config.json 을 읽지 못한 상태({_load_error})에서 {op} 를 시도했습니다. "
            f"덮어쓰면 기존 설정이 모두 사라지므로 저장하지 않았습니다. "
            f"파일을 고치거나 삭제한 뒤 다시 시도하세요."
        )


# ── 공개 API ──────────────────────────────────
def save(cfg: dict, *, force: bool = False) -> None:
    """config.json 전체를 원자적으로 저장하는 **유일한 공개 경로**.

    이 함수를 두는 이유 — 예전에는 저장하려는 쪽이 각자 `open(path, "w")` 를 했고,
    원자성 수정이 들어온 뒤에도 `web.backend.api.settings` 가 사설 함수
    `_atomic_write` 를 크로스 모듈로 부르고 `cli_init` 은 제자리 덮어쓰기를 유지했다.
    "지나야 하는 관문"이 사설 함수면 우회가 정상처럼 보인다.

    `force=True` 는 `init --force` 전용이다 — 손상된 config 를 **의도적으로 재작성**하는
    경로이므로 손상 상태 저장 차단(_guard_save)을 적용하면 복구 수단이 사라진다.
    """
    global _cache
    if not force:
        _guard_save("설정 저장")
    _atomic_write(cfg)
    _cache = cfg


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
    # 읽지 못한 상태면 저장하지 않는다 — 덮어쓰면 사용자 설정 전체가 사라진다.
    _guard_save(f"'{key_path}' 저장")

    on_disk = cfg
    if _CONFIG_PATH.exists():
        try:
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                on_disk = json.load(f)
        except Exception as e:
            # 로드 시점엔 정상이었는데 지금 깨졌다 = 다른 프로세스가 쓰는 중이거나
            # 손상. 메모리 캐시로 덮어쓰면 그 사이 남이 저장한 키를 날린다.
            raise ConfigCorrupted(
                f"저장 직전 config.json 을 읽지 못했습니다({e}). 덮어쓰면 설정이 "
                f"사라질 수 있어 중단했습니다. 잠시 후 다시 시도하세요."
            ) from e
    d_node = on_disk
    for k in parts[:-1]:
        if not isinstance(d_node.get(k), dict):
            d_node[k] = {}
        d_node = d_node[k]
    d_node[parts[-1]] = value
    save(on_disk)


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

    # 0) 알려진 예시 플레이스홀더 값 정리.
    #    과거 config.example.json 이 가짜 키(sk-proj-... 등)를 시드해서, 첫 실행 시
    #    그 값이 config.json 에 그대로 저장됐다. 그 상태로 연결 테스트하면 OpenAI 가
    #    401(키 무효)로 응답한다. 정확히 일치하는 플레이스홀더만 비워 실제 키 입력을 유도.
    _PLACEHOLDERS = {
        "api.openai_api_key": ("sk-proj-...",),
        "api.anthropic_api_key": ("sk-ant-...",),
        "email.sender": ("sender@naver.com",),
        "email.recipient": ("recipient@company.com",),
    }
    for _path, _bad in _PLACEHOLDERS.items():
        _sec, _, _k = _path.partition(".")
        _node = user_cfg.get(_sec)
        if isinstance(_node, dict) and _node.get(_k) in _bad:
            _node[_k] = ""
            changed = True

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
            save(user_cfg)
            print(f"[config] config.json 마이그레이션 완료 (config_version={target_version})")
        except Exception as e:
            # 마이그레이션 실패로 부팅을 막지 않는다 — 기존 파일은 원자적 교체가
            # 실패했으므로 그대로 남아 있다(제자리 덮어쓰기였던 예전과 다르다).
            print(f"[config] ⚠  마이그레이션 저장 실패: {e}", file=sys.stderr)
            return False
    return changed


# reload() 시 함께 호출되는 콜백들. llm_client 등 일부 모듈은 키/모델/SSL을
# import 시점에 모듈 전역으로 고정하는데, 웹 UI에서 설정을 저장해도 그 전역은
# 낡은 값으로 남아 재시작 전까지 반영되지 않았다. 해당 모듈이 여기 훅을 등록해
# reload 때 자신의 전역을 재평가한다.
_RELOAD_HOOKS: list = []


def on_reload(fn) -> None:
    """reload() 후 호출될 콜백 등록(중복 등록 무시). 등록 순서대로 호출된다."""
    if fn not in _RELOAD_HOOKS:
        _RELOAD_HOOKS.append(fn)


def reload():
    """config.json 재로드 (런타임 변경 반영용)"""
    global _cache
    _cache = None
    _load()
    for fn in list(_RELOAD_HOOKS):
        try:
            fn()
        except Exception as e:
            print(f"[config] ⚠  reload 훅 실패({getattr(fn, '__module__', '?')}): {e}",
                  file=sys.stderr)


def exists() -> bool:
    """config.json 파일이 존재하는지 확인"""
    return _CONFIG_PATH.exists()
