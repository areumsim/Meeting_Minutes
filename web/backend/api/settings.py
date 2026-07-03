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

# config.json에서 허용하는 최상위 섹션 목록
_ALLOWED_SECTIONS = {
    "api", "models", "realtime", "email", "obsidian",
    "indexing", "wiki", "notify", "ssl", "output",
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
        import config_loader
        config_loader.reload()
    except Exception:
        pass

    return {"success": True}
