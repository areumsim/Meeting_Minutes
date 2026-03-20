"""
api/profiles.py — 프로파일 관리 API
"""

from pathlib import Path

from fastapi import APIRouter

from web.backend.paths import EXE_DIR
from web.backend.schemas import ProfileCreate

router = APIRouter(tags=["profiles"])


def _get_manager():
    from profiles import ProfileManager
    return ProfileManager(str(Path(EXE_DIR) / "profiles.json"))


@router.get("/profiles")
def list_profiles():
    pm = _get_manager()
    result = []
    for name, desc, source in pm.list_profiles():
        profile = pm.get_profile(name)
        result.append({
            "name": name,
            "description": desc,
            "source": source,
            **(profile or {}),
        })
    return result


@router.post("/profiles")
def create_profile(data: ProfileCreate):
    pm = _get_manager()
    config = {
        "description": data.description or data.name,
        "type": data.type,
        "language": data.language,
        "translate": data.translate,
        "model": data.model,
        "llm": data.llm,
    }
    if data.speakers:
        config["speakers"] = data.speakers
    pm.create_profile(data.name, config, overwrite=True)
    return {"success": True, "name": data.name}


@router.delete("/profiles/{name}")
def delete_profile(name: str):
    pm = _get_manager()
    deleted = pm.delete_profile(name)
    return {"success": deleted}
