#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""OpenAI Realtime WebSocket 전사 세션 설정(session_cfg) 빌더 — CLI/웹 공유."""

import re
from typing import Any, Callable, Dict, Optional, Tuple

#: WebSocket Realtime API가 지원하는 전사 모델 (base 이름 기준).
#: 과거 이 목록이 CLI(realtime_transcription)와 웹 백엔드(api/realtime.py)에
#: 서로 다른 방식으로 하드코딩돼 있었다.
WS_SUPPORTED_BASE = {"gpt-4o-transcribe", "gpt-4o-realtime-preview"}

_DATE_SUFFIX = re.compile(r"-\d{4}-\d{2}-\d{2}$")


def strip_model_date_suffix(model: str) -> str:
    """모델 ID의 날짜 접미사 제거 (예: gpt-4o-transcribe-2025-12-15 → gpt-4o-transcribe).

    과거 CLI는 `split("-2025")`로 처리해 2026년 이후 날짜 모델에서 조용히 실패했다.
    """
    return _DATE_SUFFIX.sub("", model or "")


def normalize_ws_model(stt_model: str) -> Tuple[str, Optional[str]]:
    """WS Realtime API에서 사용할 모델로 정규화.

    반환: (사용할 모델, 전환 사유 또는 None)
    diarize/mini 등 미지원 모델은 gpt-4o-transcribe로 전환하고 사유를 돌려준다 —
    호출자가 모델 전환(웹) 또는 HTTP 모드 폴백(CLI)을 결정한다.
    """
    base = strip_model_date_suffix(stt_model)
    if "diarize" in (stt_model or ""):
        return "gpt-4o-transcribe", "diarize 모델은 WebSocket 미지원"
    if base not in WS_SUPPORTED_BASE:
        return "gpt-4o-transcribe", f"{stt_model}은(는) WebSocket 미지원"
    return base, None


def build_ws_session_config(
    stt_model: str,
    language: Optional[str],
    cfg_get: Callable[..., Any],
) -> Dict[str, Any]:
    """`cfg_get(key, default)` 형태의 설정 조회 함수를 받아 session_cfg 딕셔너리를 구성한다."""
    session_cfg: Dict[str, Any] = {
        "input_audio_format": "pcm16",
        "input_audio_transcription": {"model": stt_model},
        "turn_detection": {
            "type": cfg_get("realtime.ws_vad_type", "server_vad") or "server_vad",
        },
    }

    if language and language != "auto":
        session_cfg["input_audio_transcription"]["language"] = language

    vad_type = session_cfg["turn_detection"]["type"]
    if vad_type == "semantic_vad":
        session_cfg["turn_detection"]["eagerness"] = (
            cfg_get("realtime.ws_vad_eagerness", "medium") or "medium"
        )

    nr_type = cfg_get("realtime.ws_noise_reduction", "near_field")
    if nr_type:
        session_cfg["input_audio_noise_reduction"] = {"type": nr_type}

    return session_cfg
