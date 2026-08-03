"""API 비용 단가 — 단일 소스.

과거 이 표가 4곳(meeting_minutes.COST_PER_MIN, run_realtime._STT_PRICE_PER_MIN,
realtime_transcription._PRICING / RealtimeSession._STT_PRICE)에 복사돼 있어
단가 변경 시 서로 어긋날 수 있었다. 그 복사본은 모두 제거됐고(위 이름들은 이제 없다),
모든 비용 추정은 이 모듈을 import한다 — 단가는 `stt_rate_per_min()`,
회의록 생성 단가는 `minutes_cost()`, 어떤 모델이 쓰이는지는 `current_models()`.
"""

# STT 단가 ($/min)
STT_PRICE_PER_MIN = {
    "gpt-4o-transcribe-diarize":         0.006,
    "gpt-4o-transcribe":                 0.006,
    "gpt-4o-mini-transcribe":            0.003,
    "gpt-4o-mini-transcribe-2025-12-15": 0.003,
    "whisper-1":                         0.006,
    # Groq STT 폴백(OpenAI 장애 시) — 공개 단가는 시간당이라 /60 환산.
    # whisper-large-v3-turbo $0.04/시간, whisper-large-v3 $0.111/시간 (2026-07 기준).
    "whisper-large-v3-turbo":            0.000667,
    "whisper-large-v3":                  0.00185,
    # 로컬 faster-whisper 최종 백업 — API 호출이 없어 과금 0.
    # (키는 models.stt_local 선택지와 동일한 모델 크기 이름)
    "tiny":                              0.0,
    "base":                              0.0,
    "small":                             0.0,
    "medium":                            0.0,
    "large-v3":                          0.0,
}
DEFAULT_STT_PRICE_PER_MIN = 0.006

# 주의: 비용 추정은 **기본 STT 모델 기준**이다. 실제 세션에서 폴백(OpenAI 폴백모델·
# Groq·로컬)이 걸리면 청구액이 추정과 달라진다(대개 더 싸다 — Groq/로컬이 더 저렴).
# 위 Groq/로컬 단가는 아직 추정에만 쓰인다 — 세션이 어떤 제공자로 전사됐는지 기록해
# 사후 재계산하는 경로는 없다(폴백이 걸린 세션의 추정치는 과대평가된다).
# 단가 조회는 반드시 stt_rate_per_min() 을 쓴다 — 표를 직접 .get 하면 미등록 모델의
# 기본 단가가 호출부마다 갈린다(과거 0.003 vs 0.006 불일치).

# LLM 토큰 단가 ($/1M tokens) — 회의록/요약 생성 비용 추정용.
# Claude 단가가 과거 이 표에 없어 LLM_TOKEN=claude 세션도 항상 gpt-4o 가격으로
# 계산돼 비용이 왜곡됐다. Anthropic 공개 단가(2026-06 기준)를 추가한다.
LLM_TOKEN_PRICE = {
    # OpenAI — config_schema models.gpt_model / minutes_model / summary_model 선택지와 일치.
    # (선택 가능한 모델이 이 표에 없으면 estimate 가 gpt-4o 폴백 단가로 잘못 계산된다.)
    "gpt-4o":            {"in": 2.50, "out": 10.00},
    "gpt-4o-mini":       {"in": 0.15, "out": 0.60},
    "o1":                {"in": 15.00, "out": 60.00},
    "o3-mini":           {"in": 1.10, "out": 4.40},
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

# 임베딩 단가 ($/1M tokens) — 위키 인덱스(vault_indexer.build_embeddings)가 쓴다.
# 이 항목이 없어서 임베딩 과금이 월 지출한도(cost.monthly_cap_usd) 계산에 아예
# 잡히지 않았다: 한도는 업로드 경로에서만 강제되고 합계는 sessions.cost_estimate
# 뿐이었는데, [검색 인덱스·그래프 재빌드]나 reindex 는 그 어느 쪽도 거치지 않는다.
EMBEDDING_PRICE_PER_1M = {
    "text-embedding-3-small": 0.02,
    "text-embedding-3-large": 0.13,
    "text-embedding-ada-002": 0.10,
}
DEFAULT_EMBEDDING_PRICE_PER_1M = 0.02

#: 사전 추정용 문자→토큰 환산. 한국어 혼합 텍스트 기준으로 **보수적으로 크게** 잡는다
#: — 한도 판정에서 과소평가는 한도를 넘겨 버리고, 과대평가는 재빌드가 한 번 미뤄질 뿐이다.
#: 실제 기록은 추정이 아니라 응답의 usage.total_tokens 를 쓴다(아래 참조).
EMBEDDING_CHARS_PER_TOKEN = 2.0


def embedding_rate_per_1m(model: str) -> float:
    """임베딩 모델의 100만 토큰당 단가($). 미등록 모델은 기본 단가.

    표를 직접 .get 하지 말 것 — 호출부마다 기본값이 갈린다(STT 단가에서 실제로
    0.003 vs 0.006 로 갈렸던 전례)."""
    return EMBEDDING_PRICE_PER_1M.get(model, DEFAULT_EMBEDDING_PRICE_PER_1M)


def embedding_cost_from_tokens(tokens: float, model: str) -> float:
    """실사용 토큰 수 → 비용(USD). 임베딩 API 는 응답에 usage 를 주므로
    STT/LLM 과 달리 **정확한** 기록이 공짜로 가능하다."""
    return max(0.0, float(tokens)) / 1_000_000 * embedding_rate_per_1m(model)


def embedding_cost_from_chars(chars: float, model: str) -> float:
    """문자 수 → 예상 비용(USD). 호출 **전** 한도 판정용 추정."""
    return embedding_cost_from_tokens(
        max(0.0, float(chars)) / EMBEDDING_CHARS_PER_TOKEN, model)


def _resolve_llm_key(llm: str = "gpt", model: str | None = None) -> str:
    """config models.llm('gpt'|'claude') + 구체 모델명 → LLM_TOKEN_PRICE 키."""
    if model and model in LLM_TOKEN_PRICE:
        return model
    if (llm or "").lower().startswith("claude"):
        return "claude-opus-4-8"   # claude 기본값(config.example의 claude_model)
    return "gpt-4o"


def llm_token_price(model: str | None = None, llm: str = "gpt") -> dict:
    """LLM 토큰 단가({"in","out"}, $/1M) 조회. 미등록 모델은 기본 단가.

    표를 직접 .get 하지 말 것 — 호출부마다 기본값이 갈린다(STT 단가에서 실제로
    0.003 vs 0.006 로 갈렸던 전례, embedding_rate_per_1m 과 같은 규칙)."""
    return LLM_TOKEN_PRICE.get(_resolve_llm_key(llm, model), DEFAULT_LLM_TOKEN_PRICE)


def minutes_cost(llm: str = "gpt", model: str | None = None) -> float:
    """회의록 생성 1회 대략 비용(USD) — 실제 LLM 모델 단가 반영."""
    price = llm_token_price(model, llm)
    return (MINUTES_INPUT_TOKENS / 1_000_000) * price["in"] + \
           (MINUTES_OUTPUT_TOKENS / 1_000_000) * price["out"]


# 회의 진행 페르소나(facilitation) 트리아지 1회 대략 토큰 — PRD §10 추정치
# (최근 발화 창 ~2000자 + 페르소나 목록 ≈ 입력 1.5k, 후보 JSON 출력 ≈ 150).
# 창 길이(facilitation.TRIAGE_WINDOW_CHARS)를 바꾸면 이 값도 같이 조정한다.
FACILITATION_TRIAGE_INPUT_TOKENS  = 1_500
FACILITATION_TRIAGE_OUTPUT_TOKENS = 150


def facilitation_triage_call_cost(model: str | None) -> float:
    """트리아지 1회 예상 비용(USD).

    한도 판정(spend_guard.blocked 의 입력)과 세션 추정(estimate_session_cost 의
    facilitation 항)이 **같은 함수**를 써야 표시 금액과 판정이 안 갈라진다(CLAUDE.md)."""
    price = llm_token_price(model)
    return (FACILITATION_TRIAGE_INPUT_TOKENS / 1_000_000) * price["in"] + \
           (FACILITATION_TRIAGE_OUTPUT_TOKENS / 1_000_000) * price["out"]


# 하위호환: 기존 import 유지 (gpt-4o 기준 = 0.08)
MINUTES_COST_PER_SESSION = minutes_cost("gpt", "gpt-4o")


def stt_rate_per_min(stt_model: str) -> float:
    return STT_PRICE_PER_MIN.get(stt_model, DEFAULT_STT_PRICE_PER_MIN)


#: 2단계 보정 전사(realtime.two_pass)가 적용되는 세션 출처(sessions.source).
#: 실시간 경로만 조각 전사를 다시 전사해 문장으로 교체한다 — 배치/업로드 파이프라인에는
#: 보정 패스가 없다. 이 판정을 호출부에 복사하면 웹과 CLI가 갈라진다(그 전례가 이 파일
#: 맨 위 주석의 '단가 표 4곳 복사'다). 판정이 필요하면 is_two_pass_source() 를 쓴다.
REALTIME_SOURCES = frozenset({"web_realtime", "realtime", "recover"})


def is_two_pass_source(source: str | None) -> bool:
    """이 세션 출처가 2단계 보정 전사를 거치는가(= STT 과금이 두 번 발생하는가)."""
    return (source or "") in REALTIME_SOURCES


def current_models(cfg) -> dict:
    """현재 config 기준으로 비용 추정에 쓸 모델을 해석한다.

    cfg 는 config_loader 모듈(또는 .get(path, default) 를 제공하는 객체).
    stt / llm / 회의록 생성 모델을 한 곳에서 뽑아 estimate_session_cost 에 넘긴다.

    two_pass / revise_model 도 함께 돌려준다. 이 둘이 빠져 있어서 실시간 세션의
    STT 과금이 실제의 1/3로 추정됐다(기본 설정에서 표시 모델 $0.003/분 +
    보정 모델 $0.006/분 = $0.009/분인데 앞의 것만 계산했다). 월 지출 한도가 같은
    추정치를 쓰기 때문에 이건 표시 문제가 아니라 지출 통제가 헐거워지는 문제였다.
    """
    stt_model = cfg.get("models.stt", "gpt-4o-mini-transcribe") or "gpt-4o-mini-transcribe"
    llm = cfg.get("models.llm", "gpt") or "gpt"
    if str(llm).lower().startswith("claude"):
        minutes_model = cfg.get("models.claude_model", None)
    else:
        minutes_model = cfg.get("models.minutes_model", None) or cfg.get("models.gpt_model", None)
    # config_schema 기본값과 일치시킨다(realtime.two_pass=True, revise_model=gpt-4o-transcribe).
    two_pass = bool(cfg.get("realtime.two_pass", True))
    revise_model = cfg.get("realtime.revise_model", "gpt-4o-transcribe") or "gpt-4o-transcribe"
    return {
        "stt_model": stt_model,
        "llm": llm,
        "minutes_model": minutes_model,
        "two_pass": two_pass,
        "revise_model": revise_model,
    }


def estimate_session_cost(duration_sec: float, stt_model: str,
                          translate: bool = False,
                          include_minutes: bool = True,
                          llm: str = "gpt",
                          minutes_model: str | None = None,
                          two_pass: bool = False,
                          revise_model: str | None = None,
                          facilitation: bool = False,
                          facilitation_triage_model: str | None = None,
                          facilitation_period_sec: float = 25.0) -> dict:
    """오디오 길이(초) 기반 세션 비용 추정(USD).

    stt = 길이(분) × 모델 분당단가, translate = 길이(분) × 번역단가(옵션),
    minutes = 회의록 생성 대략 비용(include_minutes, 실제 LLM 모델 단가 반영).
    정확한 청구액이 아니라 대략치.

    two_pass=True 면 **STT 과금이 두 번** 발생한다(빠른 조각 전사 + 확정 문장 보정).
    보정 단가는 revise_model 기준이며, 기본 설정에서는 보정 모델이 표시 모델보다
    비싸다(gpt-4o-transcribe $0.006 vs gpt-4o-mini-transcribe $0.003).
    기본값을 False 로 둔 것은 의도적이다 — 배치/업로드 경로에는 보정 패스가 없으므로
    호출부가 실시간 경로임을 명시할 때만 켜진다(is_two_pass_source 참조).

    facilitation=True 면 회의 진행 페르소나 트리아지(시간 기반, 기본 25초에 1회)
    비용을 더한다 — 실시간 경로에서 config `facilitation.enabled` 가 켜져 있을 때만
    호출부가 켠다(two_pass 와 같은 규칙). 단가는 facilitation_triage_call_cost()
    한 곳에서 나온다 — 오케스트레이터의 한도 판정과 같은 함수다.
    """
    minutes_dur = max(0.0, float(duration_sec)) / 60.0
    stt_rate = stt_rate_per_min(stt_model)
    stt = minutes_dur * stt_rate
    revise_rate = stt_rate_per_min(revise_model or stt_model) if two_pass else 0.0
    stt_revise = minutes_dur * revise_rate
    tr = minutes_dur * TRANSLATE_COST_PER_MIN if translate else 0.0
    mins = minutes_cost(llm, minutes_model) if include_minutes else 0.0
    fac = 0.0
    if facilitation and facilitation_period_sec > 0:
        triage_calls = max(0.0, float(duration_sec)) / float(facilitation_period_sec)
        fac = triage_calls * facilitation_triage_call_cost(facilitation_triage_model)
    return {
        "duration_sec": round(float(duration_sec)),
        "stt": round(stt, 4),
        # 2단계 보정 전사의 추가 STT 과금. two_pass=False 면 0.0.
        "stt_revise": round(stt_revise, 4),
        "translate": round(tr, 4),
        "minutes": round(mins, 4),
        # 회의 진행 페르소나 트리아지(상시 경량 호출). facilitation=False 면 0.0.
        "facilitation": round(fac, 4),
        "total": round(stt + stt_revise + tr + mins + fac, 4),
        "stt_rate_per_min": stt_rate,
        "revise_rate_per_min": revise_rate,
        # 실측 분당 STT 단가(두 패스 합계) — 러닝 미터·한도 검사가 이 값을 써야 한다.
        "stt_effective_per_min": round(stt_rate + revise_rate, 6),
        "two_pass": bool(two_pass),
        "revise_model": (revise_model or stt_model) if two_pass else None,
    }
