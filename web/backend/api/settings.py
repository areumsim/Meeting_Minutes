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


@router.get("/config")
def get_config():
    if not CONFIG_PATH.exists():
        return {"error": "config.json not found"}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    safe = copy.deepcopy(cfg)
    if "api" in safe:
        for k in safe["api"]:
            if "key" in k.lower():
                safe["api"][k] = _mask_key(safe["api"][k])
    if "email" in safe and "password" in safe["email"]:
        safe["email"]["password"] = "***"
    return safe


@router.put("/config")
def update_config(data: dict):
    if not CONFIG_PATH.exists():
        return {"error": "config.json not found"}

    unknown = [s for s in data if s not in _ALLOWED_SECTIONS]
    if unknown:
        raise HTTPException(status_code=422, detail=f"허용되지 않는 섹션: {unknown}")

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)

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
            if isinstance(v, str) and ("***" in v or v.endswith("...")):
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
