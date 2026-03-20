"""
schemas.py — Pydantic 모델
"""

from pydantic import BaseModel
from typing import Optional, List


class SessionCreate(BaseModel):
    title: str = ""
    topic: str = ""
    type: str = "meeting"
    language: str = "ko"
    translate: bool = False
    model: str = ""
    speakers: str = ""
    mode: int = 1  # CLI mode number (1-7)


class SessionResponse(BaseModel):
    id: str
    title: Optional[str] = ""
    topic: Optional[str] = ""
    date: Optional[str] = ""
    type: Optional[str] = "meeting"
    status: Optional[str] = "pending"
    language: Optional[str] = "ko"
    translate: Optional[int] = 0
    model: Optional[str] = ""
    speakers: Optional[str] = ""
    source: Optional[str] = "web"
    mode: Optional[str] = ""
    cost_estimate: Optional[float] = 0
    duration_sec: Optional[float] = 0
    created_at: Optional[str] = ""


class SegmentResponse(BaseModel):
    id: str
    session_id: str
    speaker: Optional[str] = ""
    text: Optional[str] = ""
    translated_text: Optional[str] = ""
    start_time: Optional[float] = 0
    end_time: Optional[float] = 0


class DocumentResponse(BaseModel):
    id: str
    session_id: str
    type: str
    content: Optional[str] = ""
    format: Optional[str] = "markdown"


class ProfileCreate(BaseModel):
    name: str
    description: str = ""
    type: str = "meeting"
    language: str = "ko"
    translate: bool = False
    model: str = "gpt-4o-mini-transcribe"
    llm: str = "gpt"
    speakers: str = ""


class ConfigUpdate(BaseModel):
    models: Optional[dict] = None
    realtime: Optional[dict] = None
    email: Optional[dict] = None


class RealtimeConfig(BaseModel):
    title: str = ""
    topic: str = ""
    type: str = "meeting"
    language: str = "en"
    translate: bool = False
    speakers: str = ""
    mode: int = 2  # CLI mode number
    recording_mode: str = "ws"  # ws or http


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
