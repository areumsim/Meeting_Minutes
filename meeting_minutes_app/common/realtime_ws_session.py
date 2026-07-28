#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""OpenAI Realtime WebSocket 전사 세션 설정(session_cfg) 빌더 — CLI/웹 공유."""

import re
from typing import Any, Callable, Dict, Optional, Tuple

#: GA Realtime 전사(transcription) 세션이 지원하는 모델 (base 이름 기준).
#: GA(/v1/realtime, openai>=1.107)에서는 과거 beta에서 미지원이던 mini·whisper 계열도
#: 전사 세션에 쓸 수 있다(gpt-realtime-whisper가 최저지연 스트리밍 STT).
WS_SUPPORTED_BASE = {
    "gpt-4o-transcribe",
    "gpt-4o-mini-transcribe",
    "gpt-realtime-whisper",
    "whisper-1",
}

_DATE_SUFFIX = re.compile(r"-\d{4}-\d{2}-\d{2}$")


def strip_model_date_suffix(model: str) -> str:
    """모델 ID의 날짜 접미사 제거 (예: gpt-4o-transcribe-2025-12-15 → gpt-4o-transcribe).

    과거 CLI는 `split("-2025")`로 처리해 2026년 이후 날짜 모델에서 조용히 실패했다.
    """
    return _DATE_SUFFIX.sub("", model or "")


def normalize_ws_model(stt_model: str) -> Tuple[str, Optional[str]]:
    """실시간(WS) 전사에서 사용할 모델로 정규화.

    반환: (사용할 모델, 전환 사유 또는 None)
    diarize 모델은 GA 스키마상 허용되나 실시간 화자분리가 신뢰할 수 없어(접근 게이트·
    빈 speaker) gpt-4o-transcribe로 전환한다 — 화자분리는 F2(녹음본 배치 diarize
    후처리)에서 처리한다. 그 외 미지원 모델은 gpt-4o-transcribe로 폴백.
    """
    base = strip_model_date_suffix(stt_model)
    if "diarize" in (stt_model or ""):
        return "gpt-4o-transcribe", "실시간 화자분리 불안정 — 배치 후처리로 처리(전사는 gpt-4o-transcribe)"
    if base not in WS_SUPPORTED_BASE:
        return "gpt-4o-transcribe", f"{stt_model}은(는) 실시간 전사 미지원"
    return base, None


def resolve_session_language(language: Optional[str],
                             cfg_get: Optional[Callable[..., Any]] = None,
                             fallback: str = "ko") -> str:
    """실시간 세션 STT 언어를 하나로 확정한다 (CLI·웹 공유).

    "auto"/빈값이면 STT 호출에서 language 파라미터가 생략돼 짧은 조각마다 언어가
    재판정된다. 그 결과 무음·잡음 구간이 엉뚱한 언어(러시아어 등)로 환각되는 문제가
    있었으므로, 세션 전체가 같은 언어 값을 쓰도록 고정한다(사내 기본 한국어).
    """
    lang = (language or "").strip().lower()
    if lang and lang != "auto":
        return lang
    if cfg_get is not None:
        try:
            cand = str(cfg_get("realtime.language", fallback) or "").strip().lower()
        except Exception:
            cand = ""
        if cand and cand != "auto":
            return cand
    return fallback


def build_ws_session_config(
    stt_model: str,
    language: Optional[str],
    cfg_get: Callable[..., Any],
) -> Dict[str, Any]:
    """GA Realtime 전사 세션 설정(session)을 구성한다.

    GA(/v1/realtime)에서는 beta의 평면 구조(input_audio_format/input_audio_transcription)
    대신 session.type='transcription' + audio.input.{format,transcription,turn_detection,
    noise_reduction} 중첩 구조를 사용한다.
    """
    input_cfg: Dict[str, Any] = {
        "format": {"type": "audio/pcm", "rate": 24000},
        "transcription": {"model": stt_model},
        "turn_detection": {
            "type": cfg_get("realtime.ws_vad_type", "server_vad") or "server_vad",
        },
    }

    if language and language != "auto":
        input_cfg["transcription"]["language"] = language

    if input_cfg["turn_detection"]["type"] == "semantic_vad":
        input_cfg["turn_detection"]["eagerness"] = (
            cfg_get("realtime.ws_vad_eagerness", "medium") or "medium"
        )

    nr_type = cfg_get("realtime.ws_noise_reduction", "near_field")
    if nr_type:
        input_cfg["noise_reduction"] = {"type": nr_type}

    return {"type": "transcription", "audio": {"input": input_cfg}}
