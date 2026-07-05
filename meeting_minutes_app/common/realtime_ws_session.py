#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""OpenAI Realtime WebSocket 전사 세션 설정(session_cfg) 빌더 — CLI/웹 공유."""

from typing import Any, Callable, Dict, Optional


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
