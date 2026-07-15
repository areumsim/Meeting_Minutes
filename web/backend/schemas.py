"""
schemas.py — Pydantic 모델
"""

from pydantic import BaseModel


class ProfileCreate(BaseModel):
    name: str
    description: str = ""
    type: str = "meeting"
    language: str = "ko"
    translate: bool = False
    model: str = "gpt-4o-mini-transcribe"
    llm: str = "gpt"
    speakers: str = ""


# CLI 모드 번호 → 파라미터 매핑
MODE_PRESETS = {
    1: {"language": "ko", "translate": False, "type": "meeting",  "doc_label": "한국어 회의"},
    2: {"language": "en", "translate": True,  "type": "meeting",  "doc_label": "영어->한국어 회의"},
    3: {"language": "en", "translate": False, "type": "meeting",  "doc_label": "영어 회의"},
    4: {"language": "en", "translate": True,  "type": "seminar",  "doc_label": "세미나"},
    5: {"language": "en", "translate": True,  "type": "lecture",  "doc_label": "강의"},
    6: {"language": "ko", "translate": False, "type": "seminar",  "doc_label": "한국어 세미나"},
    7: {"language": "ko", "translate": False, "type": "lecture",  "doc_label": "한국어 강의"},
}
