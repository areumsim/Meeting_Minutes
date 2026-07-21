"""
api/settings.py — 설정 읽기/쓰기 API
"""

import json
import copy
from pathlib import Path

from fastapi import APIRouter, HTTPException

from web.backend.paths import EXE_DIR

router = APIRouter(tags=["settings"])

CONFIG_PATH = Path(EXE_DIR) / "config.json"

# 허용 최상위 섹션은 config.example.json 의 실제 최상위 키에서 자동 도출한다.
# (과거엔 이 목록을 손으로 유지하다 "output" 오타·wiki_knowledge/vault_watcher 누락으로
#  해당 섹션 저장이 422로 막히는 버그가 반복됐다. 새 기능 섹션은 example 에만 추가하면 됨.)
_ALLOWED_FALLBACK = {
    "api", "models", "realtime", "email", "obsidian",
    "indexing", "wiki", "wiki_knowledge", "notify", "ssl",
    "output_dir", "vault_watcher", "mcp", "supermemory", "analysis",
}


def _allowed_sections() -> set:
    """config.example.json 최상위 키(_접두·config_version 제외)에서 허용 섹션 도출."""
    try:
        from meeting_minutes_app.common import app_paths
        p = app_paths.get_example_config_path()
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            derived = {k for k in data if not k.startswith("_") and k != "config_version"}
            if derived:
                return derived
    except Exception:
        pass
    return set(_ALLOWED_FALLBACK)


def _coerce_value(field: dict, value):
    """스키마 필드 타입에 맞춰 값 변환/검증. 실패 시 ValueError(한국어 메시지).

    프론트 폼은 이미 올바른 타입을 보내므로 이 함수는 주로 '고급: 전체 설정(JSON)'
    편집기·API 직접 호출에 대한 안전망이다. 스키마에 없는 키는 이 함수를 거치지 않는다.
    """
    ftype = field.get("type", "text")
    label = field.get("label", field.get("key", ""))
    if value is None:
        return value
    if ftype == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1", "yes", "on", "y")
        raise ValueError(f"'{label}' 값은 참/거짓이어야 합니다.")
    if ftype == "number":
        if isinstance(value, bool):
            raise ValueError(f"'{label}' 값은 숫자여야 합니다.")
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            s = value.strip()
            if s == "":
                raise ValueError(f"'{label}' 값은 숫자여야 합니다(비어 있음).")
            try:
                return int(s)
            except ValueError:
                try:
                    return float(s)
                except ValueError:
                    raise ValueError(f"'{label}' 값은 숫자여야 합니다: {value!r}")
        raise ValueError(f"'{label}' 값은 숫자여야 합니다.")
    if ftype == "select":
        valid = set()
        for o in field.get("options") or []:
            valid.add(o["value"] if isinstance(o, dict) else o)
        if valid and value not in valid:
            raise ValueError(f"'{label}' 에 허용되지 않는 값: {value!r} (허용: {sorted(valid)})")
        return value
    return value


def _mask_key(key: str) -> str:
    if not key or len(key) < 8:
        return "***"
    return key[:8] + "..." + key[-4:]


def _sensitive_paths() -> list:
    """스키마가 표시한 비밀 값 경로 목록. 스키마 로드 실패 시 최소 기본값으로 폴백."""
    try:
        from meeting_minutes_app.common import config_schema
        return config_schema.sensitive_paths()
    except Exception:
        return [
            "api.openai_api_key", "api.anthropic_api_key",
            "obsidian.api_key", "supermemory.api_key", "email.password",
        ]


@router.get("/config")
def get_config():
    if not CONFIG_PATH.exists():
        return {"error": "config.json not found"}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    safe = copy.deepcopy(cfg)

    # 스키마가 지정한 모든 비밀 값 마스킹(키/비밀번호가 브라우저로 평문 전송되지 않도록)
    for path in _sensitive_paths():
        section, _, key = path.partition(".")
        node = safe.get(section)
        if isinstance(node, dict) and isinstance(node.get(key), str) and node[key]:
            node[key] = _mask_key(node[key])

    # 방어적: api.* 안의 'key' 포함 필드 + email.password 는 스키마와 무관하게 항상 마스킹
    if isinstance(safe.get("api"), dict):
        for k, v in safe["api"].items():
            if "key" in k.lower() and isinstance(v, str) and v:
                safe["api"][k] = _mask_key(v)
    if isinstance(safe.get("email"), dict) and safe["email"].get("password"):
        safe["email"]["password"] = "***"

    return safe


@router.get("/config/schema")
def get_config_schema():
    """웹 Settings 자동 렌더링용 스키마(그룹/필드) 반환."""
    try:
        from meeting_minutes_app.common import config_schema
        return {"version": config_schema.CONFIG_VERSION, "groups": config_schema.get_schema()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"스키마 로드 실패: {e}")


@router.put("/config")
def update_config(data: dict):
    if not CONFIG_PATH.exists():
        return {"error": "config.json not found"}

    # 주석(_readme 등)·config_version 은 전체 설정 저장(고급 JSON 편집) 시 함께 넘어올 수 있으므로
    # 검증 대상에서 제외하고 무시한다.
    data = {k: v for k, v in data.items() if not k.startswith("_") and k != "config_version"}
    allowed = _allowed_sections()
    unknown = [s for s in data if s not in allowed]
    if unknown:
        raise HTTPException(status_code=422, detail=f"허용되지 않는 섹션: {unknown}")

    try:
        from meeting_minutes_app.common import config_schema
    except Exception:
        config_schema = None

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    sensitive = set(_sensitive_paths())

    def _set(sec: str, key: str, val):
        """섹션/키에 값 기록. 최상위 스칼라(output_dir 등)는 key='' 로 처리."""
        if key:
            if not isinstance(cfg.get(sec), dict):
                cfg[sec] = {}
            cfg[sec][key] = val
        else:
            cfg[sec] = val

    for section, values in data.items():
        # output_dir은 config.json에서 유일하게 중첩 dict가 아닌 스칼라(문자열)
        # 최상위 키라서, 나머지 섹션과 같은 "dict여야 함" 규칙을 적용할 수 없다.
        if section == "output_dir":
            if not isinstance(values, str):
                raise HTTPException(status_code=422, detail="output_dir 값은 문자열이어야 합니다.")
            cfg["output_dir"] = values
            continue
        if not isinstance(values, dict):
            raise HTTPException(status_code=422, detail=f"섹션 '{section}'의 값은 dict여야 합니다.")
        if section not in cfg:
            cfg[section] = {}
        for k, v in values.items():
            # 마스킹된 비밀값(GET에서 가려져 온 값)이 되돌아오면 실제 값을 덮지 않는다.
            # 마스크 형식은 'xxxxxxxx...yyyy' 또는 '***' — '...' 가 중간에 있으므로
            # endswith 가 아니라 포함 여부로 판별하되, 비밀 필드에만 적용한다.
            if f"{section}.{k}" in sensitive and isinstance(v, str) and ("***" in v or "..." in v):
                continue
            # 스키마 필드가 있으면 타입 검증/변환(안전망). 없는 키는 그대로 통과.
            field = config_schema.field_for(section, k) if config_schema else None
            if field is not None:
                try:
                    v = _coerce_value(field, v)
                except ValueError as e:
                    raise HTTPException(status_code=422, detail=str(e))
            cfg[section][k] = v
            # 서버측 mirror: obsidian.vault_path → indexing.vault_path 등 동시 반영.
            for mt in (field.get("mirror") if field else None) or []:
                if isinstance(mt, (list, tuple)) and len(mt) == 2:
                    _set(mt[0], mt[1], v)

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

    try:
        from meeting_minutes_app.common import config_loader
        config_loader.reload()
    except Exception:
        pass

    return {"success": True}


@router.post("/config/test/openai")
def test_openai():
    """저장된 OpenAI 키의 유효성을 가볍게 확인(모델 목록 조회). 키는 응답에 포함하지 않음."""
    try:
        from meeting_minutes_app.common import config_loader
        key = config_loader.get_api_key("api.openai_api_key", "OPENAI_API_KEY")
        verify = config_loader.get("ssl.verify", True)
    except Exception as e:
        return {"ok": False, "message": f"설정 로드 실패: {e}"}

    if not key:
        return {"ok": False, "message": "OpenAI API 키가 설정되지 않았습니다."}

    try:
        import httpx
        resp = httpx.get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=10.0,
            verify=bool(verify),
        )
        if resp.status_code == 200:
            return {"ok": True, "message": "OpenAI 연결 성공 — 키가 유효합니다."}
        if resp.status_code == 401:
            return {"ok": False, "message": "API 키가 유효하지 않습니다 (401 인증 실패)."}
        return {"ok": False, "message": f"OpenAI 응답 오류 ({resp.status_code})."}
    except Exception as e:
        return {"ok": False, "message": f"연결 실패: {e}"}


@router.post("/config/test/anthropic")
def test_anthropic():
    """저장된 Anthropic(Claude) 키의 유효성을 가볍게 확인. 키는 응답에 포함하지 않음."""
    try:
        from meeting_minutes_app.common import config_loader
        key = config_loader.get_api_key("api.anthropic_api_key", "ANTHROPIC_API_KEY")
        verify = config_loader.get("ssl.verify", True)
    except Exception as e:
        return {"ok": False, "message": f"설정 로드 실패: {e}"}

    if not key:
        return {"ok": False, "message": "Anthropic API 키가 설정되지 않았습니다."}

    try:
        import httpx
        # /v1/models 는 x-api-key + anthropic-version 헤더만으로 조회 가능(과금 없음)
        resp = httpx.get(
            "https://api.anthropic.com/v1/models",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
            timeout=10.0,
            verify=bool(verify),
        )
        if resp.status_code == 200:
            return {"ok": True, "message": "Anthropic 연결 성공 — 키가 유효합니다."}
        if resp.status_code == 401:
            return {"ok": False, "message": "API 키가 유효하지 않습니다 (401 인증 실패)."}
        return {"ok": False, "message": f"Anthropic 응답 오류 ({resp.status_code})."}
    except Exception as e:
        return {"ok": False, "message": f"연결 실패: {e}"}


@router.post("/config/test/obsidian")
def test_obsidian():
    """Obsidian 볼트 경로 존재/디렉터리 여부 확인."""
    try:
        from meeting_minutes_app.common import config_loader
        vault = (config_loader.get("obsidian.vault_path", "")
                 or config_loader.get("indexing.vault_path", ""))
    except Exception as e:
        return {"ok": False, "message": f"설정 로드 실패: {e}"}

    if not vault:
        return {"ok": False, "message": "Obsidian 볼트 경로가 설정되지 않았습니다."}
    p = Path(vault)
    if not p.exists():
        return {"ok": False, "message": f"경로가 존재하지 않습니다: {vault}"}
    if not p.is_dir():
        return {"ok": False, "message": f"폴더가 아닙니다: {vault}"}
    return {"ok": True, "message": f"볼트 폴더 확인됨: {vault}"}
