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


GPT_MODEL = _c("models.gpt_model", "gpt-4o") or "gpt-4o"
CLAUDE_MODEL = _c("models.claude_model", "claude-opus-4-6") or "claude-opus-4-6"
OPENAI_API_KEY = _c("api.openai_api_key", "") or ""
ANTHROPIC_API_KEY = _c("api.anthropic_api_key", "") or ""
SSL_VERIFY = _c("ssl.verify", False)

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


def make_openai_client(api_key: str):
    """OpenAI 클라이언트 생성 (SSL 우회 지원)."""
    from openai import OpenAI
    if not SSL_VERIFY and HAS_HTTPX:
        warnings.filterwarnings("ignore", message="Unverified HTTPS request")
        http_client = httpx.Client(verify=False)
        logger.debug("OpenAI client: SSL 검증 비활성화")
        return OpenAI(api_key=api_key, http_client=http_client)
    return OpenAI(api_key=api_key)


def make_anthropic_client(api_key: str):
    """Anthropic 클라이언트 생성 (SSL 우회 지원)."""
    import anthropic as ant
    if not SSL_VERIFY and HAS_HTTPX:
        warnings.filterwarnings("ignore", message="Unverified HTTPS request")
        http_client = httpx.Client(verify=False)
        logger.debug("Anthropic client: SSL 검증 비활성화")
        return ant.Anthropic(api_key=api_key, http_client=http_client)
    return ant.Anthropic(api_key=api_key)


class LLMClient:
    def __init__(self, preferred: str = "gpt"):
        self.preferred     = preferred
        self.openai        = None
        self.anthropic     = None
        self._call_count   = 0
        self._total_tokens = 0
        self._init()

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
            r  = self.openai.chat.completions.create(**kwargs)
            elapsed = time.time() - t0
            result  = r.choices[0].message.content
            if r.usage:
                self._total_tokens += r.usage.total_tokens
                logger.debug(f"[GPT USAGE] {r.usage.prompt_tokens}+{r.usage.completion_tokens} "
                             f"time={elapsed:.1f}s")
            self._call_count += 1
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
            r  = self.anthropic.messages.create(
                model=CLAUDE_MODEL, max_tokens=max_tokens, temperature=temp,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            elapsed = time.time() - t0
            result  = r.content[0].text
            self._total_tokens += r.usage.input_tokens + r.usage.output_tokens
            logger.debug(f"[CLAUDE USAGE] in={r.usage.input_tokens} "
                         f"out={r.usage.output_tokens} time={elapsed:.1f}s")
            self._call_count += 1
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
        if self.anthropic:
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

        # 2) GPT responses API 웹검색 폴백 (openai SDK 1.x/2.x responses 모듈 지원 시)
        if self.openai and hasattr(self.openai, "responses"):
            try:
                resp = self.openai.responses.create(
                    model=GPT_MODEL,
                    tools=[{"type": "web_search_preview"}],
                    input=query,
                )
                text_parts: List[str] = []
                for item in (resp.output or []):
                    if getattr(item, "type", "") == "message":
                        for c in (item.content or []):
                            if getattr(c, "type", "") == "output_text":
                                text_parts.append(getattr(c, "text", "") or "")
                text = "\n".join(t for t in text_parts if t).strip()
                if text:
                    self._call_count += 1
                    return {
                        "text": text,
                        "sources": [],
                        "searched": True,
                        "source_status": "no_urls",
                        "source_warning": "GPT 웹검색 폴백이 URL 출처를 반환하지 않았습니다.",
                    }
            except Exception as e:
                logger.warning(f"[web_research] GPT 웹검색 실패 → 최종 폴백: {e}")

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
