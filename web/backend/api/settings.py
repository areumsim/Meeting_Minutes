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

# config.json에서 허용하는 최상위 섹션 목록 — config.example.json의 실제 최상위
# 키(_readme 제외)와 반드시 일치시킬 것. 예전엔 "output"이 들어 있었지만 실제 키는
# "output_dir"이라 어차피 무의미했고, wiki_knowledge/vault_watcher/mcp/supermemory/
# analysis는 아예 빠져 있어서 그 섹션들은 이 엔드포인트로 저장이 불가능했다(422).
_ALLOWED_SECTIONS = {
    "api", "models", "realtime", "email", "obsidian",
    "indexing", "wiki", "wiki_knowledge", "notify", "ssl",
    "output_dir", "vault_watcher", "mcp", "supermemory", "analysis",
}


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

    unknown = [s for s in data if s not in _ALLOWED_SECTIONS]
    if unknown:
        raise HTTPException(status_code=422, detail=f"허용되지 않는 섹션: {unknown}")

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    sensitive = set(_sensitive_paths())

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
            cfg[section][k] = v

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
