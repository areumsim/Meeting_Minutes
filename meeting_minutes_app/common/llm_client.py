"""LLM client (GPT-4o / Claude fallback) — shared by wiki_core and meeting_pipeline."""

from __future__ import annotations

import logging
import os
import time
import traceback
import warnings
from typing import Any, Dict, List, Optional

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

try:
    from meeting_minutes_app.common import config_loader as _cfg
    _cfg_ok = True
except ImportError:
    _cfg = None  # type: ignore
    _cfg_ok = False


def _c(key: str, default: Any = None) -> Any:
    return _cfg.get(key, default) if _cfg_ok else default


# 기본 모델명은 한 곳에서만 정의(import 시점과 reload 훅 양쪽에서 재사용 — 중복 방지).
_GPT_MODEL_DEFAULT = "gpt-4o-mini"
_CLAUDE_MODEL_DEFAULT = "claude-opus-4-8"


def _eval_config_globals() -> Dict[str, Any]:
    """config에서 모델/키/SSL 전역값을 평가한다(import·reload 공용)."""
    return {
        "GPT_MODEL": _c("models.gpt_model", _GPT_MODEL_DEFAULT) or _GPT_MODEL_DEFAULT,
        "CLAUDE_MODEL": _c("models.claude_model", _CLAUDE_MODEL_DEFAULT) or _CLAUDE_MODEL_DEFAULT,
        "OPENAI_API_KEY": _c("api.openai_api_key", "") or "",
        "ANTHROPIC_API_KEY": _c("api.anthropic_api_key", "") or "",
        "GROQ_API_KEY": _c("api.groq_api_key", "") or "",
        "SSL_VERIFY": _c("ssl.verify", True),  # 안전 기본값: 키 누락 시 검증 켜짐
    }


_globals = _eval_config_globals()
GPT_MODEL = _globals["GPT_MODEL"]
CLAUDE_MODEL = _globals["CLAUDE_MODEL"]
OPENAI_API_KEY = _globals["OPENAI_API_KEY"]
ANTHROPIC_API_KEY = _globals["ANTHROPIC_API_KEY"]
GROQ_API_KEY = _globals["GROQ_API_KEY"]
SSL_VERIFY = _globals["SSL_VERIFY"]

# Groq OpenAI 호환 엔드포인트 — audio.transcriptions.create 를 OpenAI SDK 그대로 재사용.
GROQ_BASE_URL = "https://api.groq.com/openai/v1"


def _refresh_config_globals() -> None:
    """config_loader.reload() 훅 — 웹 UI에서 설정 저장 시 재시작 없이
    키/모델/SSL 전역을 재평가한다(위 상수들은 import 시점 값으로 고정되므로)."""
    global GPT_MODEL, CLAUDE_MODEL, OPENAI_API_KEY, ANTHROPIC_API_KEY, GROQ_API_KEY, SSL_VERIFY
    g = _eval_config_globals()
    GPT_MODEL = g["GPT_MODEL"]
    CLAUDE_MODEL = g["CLAUDE_MODEL"]
    OPENAI_API_KEY = g["OPENAI_API_KEY"]
    ANTHROPIC_API_KEY = g["ANTHROPIC_API_KEY"]
    GROQ_API_KEY = g["GROQ_API_KEY"]
    SSL_VERIFY = g["SSL_VERIFY"]


if _cfg_ok:
    _cfg.on_reload(_refresh_config_globals)

logger = logging.getLogger("meeting_minutes")


def info(msg: str) -> None:
    print(f"  {msg}")
    logger.info(msg)


def warn(msg: str) -> None:
    print(f"  ⚠  {msg}")
    logger.warning(msg)


def get_api_key(env_name: str, code_value: str = "") -> Optional[str]:
    key = os.environ.get(env_name) or code_value or None
    if key:
        masked = key[:8] + "..." + key[-4:]
        logger.debug(f"API Key [{env_name}]: {masked}")
    return key


def _sdk_limits(timeout: Optional[float], max_retries: Optional[int]) -> dict:
    """OpenAI SDK 생성 인자 중 timeout/max_retries만 골라 넘긴다.

    None 이면 인자를 아예 넣지 않아 SDK 기본값(요청 timeout 600초, 재시도 2회)을
    유지한다 — 기존 호출자(채팅·회의록 생성)의 동작을 바꾸지 않기 위함이다.
    폴백 체인이 있는 STT 경로만 짧은 값을 명시해 죽은 벤더에 오래 매달리지 않는다."""
    kw: dict = {}
    if timeout is not None:
        kw["timeout"] = timeout
    if max_retries is not None:
        kw["max_retries"] = max_retries
    return kw


def make_openai_client(api_key: str, timeout: Optional[float] = None,
                       max_retries: Optional[int] = None):
    """OpenAI 클라이언트 생성 (SSL 우회 지원)."""
    from openai import OpenAI
    limits = _sdk_limits(timeout, max_retries)
    if not SSL_VERIFY and HAS_HTTPX:
        warnings.filterwarnings("ignore", message="Unverified HTTPS request")
        http_client = httpx.Client(verify=False)
        logger.debug("OpenAI client: SSL 검증 비활성화")
        return OpenAI(api_key=api_key, http_client=http_client, **limits)
    return OpenAI(api_key=api_key, **limits)


def make_groq_client(api_key: str, timeout: Optional[float] = None,
                     max_retries: Optional[int] = None):
    """Groq STT 클라이언트 생성 — OpenAI SDK를 Groq 엔드포인트로 향하게 한다.

    Groq는 OpenAI 호환 `audio.transcriptions.create` 를 제공하므로(whisper-large-v3 /
    whisper-large-v3-turbo) 별도 SDK 없이 base_url 만 바꿔 재사용한다. OpenAI 장애·키
    문제 시 '다른 벤더' 폴백으로 동작(같은 OpenAI 키/엔드포인트가 아니므로 동시 장애를 피함)."""
    from openai import OpenAI
    limits = _sdk_limits(timeout, max_retries)
    if not SSL_VERIFY and HAS_HTTPX:
        warnings.filterwarnings("ignore", message="Unverified HTTPS request")
        http_client = httpx.Client(verify=False)
        logger.debug("Groq client: SSL 검증 비활성화")
        return OpenAI(api_key=api_key, base_url=GROQ_BASE_URL,
                      http_client=http_client, **limits)
    return OpenAI(api_key=api_key, base_url=GROQ_BASE_URL, **limits)


def make_anthropic_client(api_key: str):
    """Anthropic 클라이언트 생성 (SSL 우회 지원)."""
    import anthropic as ant
    if not SSL_VERIFY and HAS_HTTPX:
        warnings.filterwarnings("ignore", message="Unverified HTTPS request")
        http_client = httpx.Client(verify=False)
        logger.debug("Anthropic client: SSL 검증 비활성화")
        return ant.Anthropic(api_key=api_key, http_client=http_client)
    return ant.Anthropic(api_key=api_key)


_TRANSIENT_ERROR_NAMES = (
    "RateLimitError", "APIConnectionError", "APITimeoutError",
    "InternalServerError", "ServiceUnavailableError", "OverloadedError",
)


def _is_transient_error(e: Exception) -> bool:
    if type(e).__name__ in _TRANSIENT_ERROR_NAMES:
        return True
    status = getattr(e, "status_code", None)
    return isinstance(status, int) and (status == 429 or status >= 500)


def _call_with_retry(fn, label: str, retries: int = 2, base_delay: float = 2.0):
    """일시 오류(429/5xx/연결/타임아웃)에 한해 짧은 백오프 재시도.

    영구 오류(잘못된 요청 등)는 즉시 raise — 호출부의 provider 폴백 체인이 처리.
    """
    for attempt in range(retries + 1):
        try:
            return fn()
        except Exception as e:
            if attempt >= retries or not _is_transient_error(e):
                raise
            delay = base_delay * (2 ** attempt)
            warn(f"{label} 일시 오류({type(e).__name__}) → {delay:.0f}초 후 재시도 "
                 f"({attempt + 1}/{retries})")
            time.sleep(delay)


class LLMClient:
    def __init__(self, preferred: str = "gpt"):
        self.preferred     = preferred
        self.openai        = None
        self.anthropic     = None
        self._call_count   = 0
        self._total_tokens = 0
        #: 실제로 응답을 만든 모델 — 등장 순서 유지, 중복 없음.
        #: chat()은 GPT↔Claude 상호 폴백을 하므로 preferred 가 실제 모델이라는 보장이
        #: 없다. 회의록 frontmatter 의 녹취 출처 메타가 이 값을 쓴다.
        #: **인스턴스 속성이어야 한다** — 웹은 세션이 동시에 돌아 전역이면 섞인다.
        self.models_used: List[str] = []
        self._init()

    def _record_model(self, model: str) -> None:
        if model and model not in self.models_used:
            self.models_used.append(model)

    def _init(self):
        try:
            k = get_api_key("OPENAI_API_KEY", OPENAI_API_KEY)
            if k:
                self.openai = make_openai_client(k)
                info(f"OpenAI client ready{' (SSL 우회)' if not SSL_VERIFY else ''}")
            else:
                warn("OpenAI API 키 없음")
        except ImportError:
            warn("openai 미설치 → pip install openai")
        except Exception as e:
            warn(f"OpenAI 초기화 실패: {e}")

        try:
            k = get_api_key("ANTHROPIC_API_KEY", ANTHROPIC_API_KEY)
            if k:
                self.anthropic = make_anthropic_client(k)
                info(f"Anthropic client ready{' (SSL 우회)' if not SSL_VERIFY else ''}")
        except ImportError:
            pass
        except Exception as e:
            warn(f"Anthropic 초기화 실패: {e}")

    def _gpt(self, system: str, user: str, temp: float = 0.3,
             model: str = None, max_tokens: int = None) -> Optional[str]:
        if not self.openai:
            return None
        _model = model or GPT_MODEL
        try:
            logger.debug(f"[GPT] model={_model}, temp={temp}, max_tokens={max_tokens}")
            t0 = time.time()
            kwargs = dict(
                model=_model, temperature=temp,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user},
                ],
            )
            if max_tokens:
                kwargs["max_tokens"] = max_tokens
            r  = _call_with_retry(
                lambda: self.openai.chat.completions.create(**kwargs), "GPT")
            elapsed = time.time() - t0
            result  = r.choices[0].message.content
            if r.usage:
                self._total_tokens += r.usage.total_tokens
                logger.debug(f"[GPT USAGE] {r.usage.prompt_tokens}+{r.usage.completion_tokens} "
                             f"time={elapsed:.1f}s")
            self._call_count += 1
            self._record_model(_model)
            return result
        except Exception as e:
            logger.error(f"[GPT ERROR] {type(e).__name__}: {e}")
            logger.debug(traceback.format_exc())
            warn(f"GPT 호출 실패: {e}")
            return None

    def _claude(self, system: str, user: str, temp: float = 0.3,
                max_tokens: int = 16000) -> Optional[str]:
        if not self.anthropic:
            return None
        try:
            logger.debug(f"[CLAUDE] model={CLAUDE_MODEL}, temp={temp}, max_tokens={max_tokens}")
            t0 = time.time()
            r  = _call_with_retry(
                lambda: self.anthropic.messages.create(
                    model=CLAUDE_MODEL, max_tokens=max_tokens, temperature=temp,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                ), "Claude")
            elapsed = time.time() - t0
            result  = r.content[0].text
            self._total_tokens += r.usage.input_tokens + r.usage.output_tokens
            logger.debug(f"[CLAUDE USAGE] in={r.usage.input_tokens} "
                         f"out={r.usage.output_tokens} time={elapsed:.1f}s")
            self._call_count += 1
            self._record_model(CLAUDE_MODEL)
            return result
        except Exception as e:
            logger.error(f"[CLAUDE ERROR] {type(e).__name__}: {e}")
            logger.debug(traceback.format_exc())
            warn(f"Claude 호출 실패: {e}")
            return None

    def chat(self, system: str, user: str, temp: float = 0.3,
             model: str = None, max_tokens: int = None) -> str:
        if self.preferred == "claude":
            r = self._claude(system, user, temp, max_tokens or 16000)
            if r:
                return r
            warn("Claude 실패 → GPT 폴백")
            r = self._gpt(system, user, temp, model, max_tokens)
        else:
            r = self._gpt(system, user, temp, model, max_tokens)
            if r:
                return r
            warn("GPT 실패 → Claude 폴백")
            r = self._claude(system, user, temp, max_tokens or 16000)
        if r:
            return r
        tried = "Claude → GPT" if self.preferred == "claude" else "GPT → Claude"
        raise RuntimeError(
            f"모든 LLM API 호출 실패 ({tried} 모두 응답 없음).\n"
            "  → API 키를 확인하세요 (ANTHROPIC_API_KEY, OPENAI_API_KEY).\n"
            "  → SSL 에러라면: --ssl-no-verify 또는 config.json ssl.verify: false"
        )

    def web_research(self, query: str, max_uses: int = 3,
                     max_tokens: int = 1500) -> Dict[str, Any]:
        """Anthropic 웹 검색 도구로 외부 자료를 보완해 설명을 생성.
        반환: {"text": 설명, "sources": [{"title","url"}], "searched": bool}
        웹 검색 불가(키 없음/도구 미지원/회사망 차단) 시 모델 지식 기반으로 폴백(searched=False).
        """
        system = (
            "당신은 회의·세미나 기록을 보완하는 리서치 어시스턴트입니다.\n"
            "주어진 용어/기술/인물/기업에 대해 정확하고 간결한 한국어 설명(2~4문장)을 제공하세요.\n"
            "가능하면 웹 검색으로 최신·정확한 사실을 확인하고, 모르면 모른다고 하세요. 추측 금지."
        )
        # 1) Anthropic 웹 검색 도구 시도
        # (크레딧 소진/인증 실패 같은 영구 오류가 한 번 발생하면 이 프로세스에서는
        # 재시도하지 않는다 — 회의 1건당 십수 회 호출되므로 경고 스팸·지연 방지)
        if self.anthropic and not getattr(self, "_anthropic_web_disabled", False):
            try:
                r = self.anthropic.messages.create(
                    model=CLAUDE_MODEL, max_tokens=max_tokens, system=system,
                    messages=[{"role": "user", "content": query}],
                    tools=[{"type": "web_search_20250305",
                            "name": "web_search", "max_uses": max_uses}],
                )
                text_parts: List[str] = []
                sources: List[Dict[str, str]] = []
                for block in r.content:
                    btype = getattr(block, "type", "")
                    if btype == "text":
                        text_parts.append(getattr(block, "text", "") or "")
                    elif btype == "web_search_tool_result":
                        for item in (getattr(block, "content", None) or []):
                            url = getattr(item, "url", None)
                            if url:
                                sources.append({"title": getattr(item, "title", None) or url,
                                                "url": url})
                text = "\n".join(t for t in text_parts if t).strip()
                if text:
                    seen = set(); uniq = []
                    for s in sources:
                        if s["url"] not in seen:
                            seen.add(s["url"]); uniq.append(s)
                    self._call_count += 1
                    return {"text": text, "sources": uniq[:5], "searched": True}
            except Exception as e:
                logger.warning(f"[web_research] Anthropic 웹검색 실패 → GPT 폴백: {type(e).__name__}: {e}")
                msg = str(e).lower()
                if (not _is_transient_error(e)) or "credit balance" in msg:
                    self._anthropic_web_disabled = True
                    warn("Anthropic 웹검색 비활성화 (영구 오류) → 이후 GPT 폴백만 사용")

        # 2) GPT responses API 웹검색 폴백 (openai SDK 1.x/2.x responses 모듈 지원 시)
        # 실제 검색을 수행하지 않고 "찾아보겠습니다" 식 의도문만 내는 flaky 케이스가
        # 관찰돼(web_search_call 없이 message만 반환), 최대 2회 재시도한다 —
        # 품질을 낮춰 검증을 건너뛰지 않고 실제 검색을 얻어내는 것이 목표.
        if self.openai and hasattr(self.openai, "responses"):
            for attempt in range(2):
                try:
                    resp = self.openai.responses.create(
                        model=GPT_MODEL,
                        tools=[{"type": "web_search_preview"}],
                        input=query,
                    )
                    used_search = any(
                        getattr(item, "type", "") == "web_search_call"
                        for item in (resp.output or [])
                    )
                    text_parts: List[str] = []
                    sources: List[Dict[str, str]] = []
                    for item in (resp.output or []):
                        if getattr(item, "type", "") != "message":
                            continue
                        for c in (item.content or []):
                            if getattr(c, "type", "") != "output_text":
                                continue
                            text_parts.append(getattr(c, "text", "") or "")
                            for ann in (getattr(c, "annotations", None) or []):
                                url = getattr(ann, "url", None)
                                if url:
                                    sources.append({
                                        "title": getattr(ann, "title", None) or url,
                                        "url": url,
                                    })
                    text = "\n".join(t for t in text_parts if t).strip()
                    seen = set(); uniq_sources = []
                    for s in sources:
                        if s["url"] not in seen:
                            seen.add(s["url"]); uniq_sources.append(s)

                    if text and (used_search or uniq_sources or attempt == 1):
                        self._call_count += 1
                        result: Dict[str, Any] = {
                            "text": text, "sources": uniq_sources[:5], "searched": used_search,
                        }
                        if not uniq_sources:
                            result["source_status"] = "no_urls"
                            result["source_warning"] = "GPT 웹검색 폴백이 URL 출처를 반환하지 않았습니다."
                        return result

                    # 검색 미실행("찾아보겠습니다" 식 의도문만) → 1회 재시도
                    logger.warning(
                        f"[web_research] GPT 웹검색 미실행 감지(시도 {attempt + 1}/2) → 재시도")
                except Exception as e:
                    logger.warning(f"[web_research] GPT 웹검색 실패(시도 {attempt + 1}/2): {e}")

        # 3) 최종 폴백: 일반 LLM (라이브 검색 없음)
        try:
            text = self.chat(system, query, temp=0.2, max_tokens=max_tokens)
            return {
                "text": text or "",
                "sources": [],
                "searched": False,
                "source_status": "model_fallback",
                "source_warning": "라이브 웹검색 실패 후 일반 LLM 응답으로 대체되었습니다.",
            }
        except Exception:
            return {"text": "", "sources": [], "searched": False, "source_status": "failed"}

    def stats(self) -> str:
        return f"LLM 호출 {self._call_count}회  토큰 {self._total_tokens:,}개 (추정)"
