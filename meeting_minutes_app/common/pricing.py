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

# LLM 토큰 단가 ($/1M tokens)
LLM_TOKEN_PRICE = {
    "gpt-4o":      {"in": 2.50, "out": 10.00},
    "gpt-4o-mini": {"in": 0.15, "out": 0.60},
}

# 배치 비용 추정용 대략치 ($/1K tokens)
LLM_COST_PER_1K_TOKENS = {"gpt-4o": 0.005, "claude": 0.003}

# 세션당 고정 추정치
MINUTES_COST_PER_SESSION = 0.08    # gpt-4o 회의록 생성 1회 (~20K in + 3K out)
TRANSLATE_COST_PER_MIN   = 0.0002  # gpt-4o-mini 실시간 번역 (~173 tok/min × 2방향)
