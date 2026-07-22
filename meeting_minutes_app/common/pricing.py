"""API 비용 단가 — 단일 소스.

과거 이 표가 4곳(meeting_minutes.COST_PER_MIN, run_realtime._STT_PRICE_PER_MIN,
realtime_transcription._PRICING / RealtimeSession._STT_PRICE)에 복사돼 있어
단가 변경 시 서로 어긋날 수 있었다. 모든 비용 추정은 이 모듈을 import한다.
"""

# STT 단가 ($/min)
STT_PRICE_PER_MIN = {
    "gpt-4o-transcribe-diarize":         0.006,
    "gpt-4o-transcribe":                 0.006,
    "gpt-4o-mini-transcribe":            0.003,
    "gpt-4o-mini-transcribe-2025-12-15": 0.003,
    "whisper-1":                         0.006,
}
DEFAULT_STT_PRICE_PER_MIN = 0.006

# LLM 토큰 단가 ($/1M tokens) — 회의록/요약 생성 비용 추정용.
# Claude 단가가 과거 이 표에 없어 LLM_TOKEN=claude 세션도 항상 gpt-4o 가격으로
# 계산돼 비용이 왜곡됐다. Anthropic 공개 단가(2026-06 기준)를 추가한다.
LLM_TOKEN_PRICE = {
    # OpenAI
    "gpt-4o":            {"in": 2.50, "out": 10.00},
    "gpt-4o-mini":       {"in": 0.15, "out": 0.60},
    # Anthropic (Claude)
    "claude-opus-4-8":   {"in": 5.00, "out": 25.00},
    "claude-opus-4-7":   {"in": 5.00, "out": 25.00},
    "claude-opus-4-6":   {"in": 5.00, "out": 25.00},
    "claude-sonnet-5":   {"in": 3.00, "out": 15.00},
    "claude-sonnet-4-6": {"in": 3.00, "out": 15.00},
    "claude-haiku-4-5":  {"in": 1.00, "out": 5.00},
}
DEFAULT_LLM_TOKEN_PRICE = {"in": 2.50, "out": 10.00}  # 미지원 모델 폴백(gpt-4o 기준)

# 배치 비용 추정용 대략치 ($/1K tokens)
LLM_COST_PER_1K_TOKENS = {"gpt-4o": 0.005, "claude": 0.003}

# 회의록 1회 생성 시 대략적 토큰 사용량 (입력 컨텍스트 + 생성)
MINUTES_INPUT_TOKENS  = 20_000
MINUTES_OUTPUT_TOKENS = 3_000

TRANSLATE_COST_PER_MIN = 0.0002  # gpt-4o-mini 실시간 번역 (~173 tok/min × 2방향)


def _resolve_llm_key(llm: str = "gpt", model: str | None = None) -> str:
    """config models.llm('gpt'|'claude') + 구체 모델명 → LLM_TOKEN_PRICE 키."""
    if model and model in LLM_TOKEN_PRICE:
        return model
    if (llm or "").lower().startswith("claude"):
        return "claude-opus-4-8"   # claude 기본값(config.example의 claude_model)
    return "gpt-4o"


def minutes_cost(llm: str = "gpt", model: str | None = None) -> float:
    """회의록 생성 1회 대략 비용(USD) — 실제 LLM 모델 단가 반영."""
    price = LLM_TOKEN_PRICE.get(_resolve_llm_key(llm, model), DEFAULT_LLM_TOKEN_PRICE)
    return (MINUTES_INPUT_TOKENS / 1_000_000) * price["in"] + \
           (MINUTES_OUTPUT_TOKENS / 1_000_000) * price["out"]


# 하위호환: 기존 import 유지 (gpt-4o 기준 = 0.08)
MINUTES_COST_PER_SESSION = minutes_cost("gpt", "gpt-4o")


def stt_rate_per_min(stt_model: str) -> float:
    return STT_PRICE_PER_MIN.get(stt_model, DEFAULT_STT_PRICE_PER_MIN)


def estimate_session_cost(duration_sec: float, stt_model: str,
                          translate: bool = False,
                          include_minutes: bool = True,
                          llm: str = "gpt",
                          minutes_model: str | None = None) -> dict:
    """오디오 길이(초) 기반 세션 비용 추정(USD).

    stt = 길이(분) × 모델 분당단가, translate = 길이(분) × 번역단가(옵션),
    minutes = 회의록 생성 대략 비용(include_minutes, 실제 LLM 모델 단가 반영).
    정확한 청구액이 아니라 대략치.
    """
    minutes_dur = max(0.0, float(duration_sec)) / 60.0
    stt = minutes_dur * stt_rate_per_min(stt_model)
    tr = minutes_dur * TRANSLATE_COST_PER_MIN if translate else 0.0
    mins = minutes_cost(llm, minutes_model) if include_minutes else 0.0
    return {
        "duration_sec": round(float(duration_sec)),
        "stt": round(stt, 4),
        "translate": round(tr, 4),
        "minutes": round(mins, 4),
        "total": round(stt + tr + mins, 4),
        "stt_rate_per_min": stt_rate_per_min(stt_model),
    }
