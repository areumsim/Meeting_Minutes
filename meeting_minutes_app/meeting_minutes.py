#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
============================================================
 Meeting / Seminar / Lecture Minutes Generator
 (회의록·세미나·강의 기록 자동 생성기)
============================================================

 주요 기능:
   - 음성/영상 파일 → STT → 스크립트 + 기록문서 + 요약본 자동 생성
   - 다중 파일 배치 처리  (glob 지원)
   - 영어→한국어 번역
   - 화자 분리 (diarize 모델)
   - 화자 캐시 재사용 (--reuse-speakers)
   - Named Profile 시스템 (--profile)
   - 완료 알림 — Email / Slack / Teams  (--notify)
   - 폴더 감시 자동 처리 (watcher.py)
   - GPT-4o ↔ Claude 자동 폴백  (MINUTES_MODEL / SUMMARY_MODEL / CLAUDE_MODEL)
   - 실패 시 이어서 처리 (--resume)
   - 비용 사전 추정 (--estimate-cost)
   - 디버그 모드 (--debug)
   - STT 교정 스크립트 생성 (refine_script — 전체 맥락+주제 기반 오탈자·고유명사 수정)

 사전 준비:
   pip install -r requirements.txt
   ffmpeg 설치 필요 (https://ffmpeg.org)
   config.json 에 API 키 설정

 사용법:
   python run_meeting.py batch meeting.mp4
   python run_meeting.py batch seminar.webm --type seminar --translate
   python run_meeting.py batch *.webm --title "Q1 세미나" --notify email
   python run_meeting.py batch meeting.mp4 --profile weekly_team
   python run_meeting.py batch meeting.mp4 --debug
   python run_meeting.py batch meeting.mp4 --estimate-cost
   python run_meeting.py batch meeting.mp4 --edit-speakers
   python run_meeting.py batch meeting.mp4 --ssl-no-verify
============================================================
"""

import os
import sys
import json
import shutil
import argparse
import subprocess
import tempfile
import math
import re
import time
import traceback
from difflib import SequenceMatcher

# Windows cp949 터미널에서 이모지 출력 시 UnicodeEncodeError 방지
if sys.stdout.encoding and sys.stdout.encoding.lower() in ("cp949", "euc-kr", "ansi"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr.encoding and sys.stderr.encoding.lower() in ("cp949", "euc-kr", "ansi"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
import logging
import glob
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Tuple, Any

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False


# ──────────────────────────────────────────────
#  config_loader (API 키, 모델, SSL 설정)
# ──────────────────────────────────────────────
try:
    import config_loader as _cfg
    _cfg_ok = True
except ImportError:
    _cfg = None  # type: ignore
    _cfg_ok = False


def _c(key: str, default: Any = None) -> Any:
    """config.json 조회 헬퍼"""
    return _cfg.get(key, default) if _cfg_ok else default


# ──────────────────────────────────────────────
#  상수 / 모델 설정
# ──────────────────────────────────────────────
DEFAULT_STT_MODEL  = _c("models.stt",          "gpt-4o-mini-transcribe") or "gpt-4o-mini-transcribe"
FALLBACK_STT_MODEL = _c("models.stt_fallback", "gpt-4o-transcribe") or "gpt-4o-transcribe"
GPT_MODEL          = _c("models.gpt_model",     "gpt-4o") or "gpt-4o"
MINUTES_MODEL      = _c("models.minutes_model", "gpt-4o") or "gpt-4o"
SUMMARY_MODEL      = _c("models.summary_model", "gpt-4o") or "gpt-4o"
CLAUDE_MODEL       = _c("models.claude_model", "claude-opus-4-6") or "claude-opus-4-6"
OPENAI_API_KEY     = _c("api.openai_api_key",    "") or ""
ANTHROPIC_API_KEY  = _c("api.anthropic_api_key", "") or ""
SSL_VERIFY         = _c("ssl.verify", False)

MAX_FILE_SIZE_MB = 25
MAX_CHUNK_DURATION_SEC = 1200  # gpt-4o-transcribe* 최대 1400s → 안전 마진 포함

MIN_STT_CHARS_PER_SEC   = 3.0  # 이보다 적으면 STT 결과 잘림 의심
MAX_STT_RETRY_SPLIT_DEPTH = 1  # 잘림 감지 시 청크 재분할 재시도 최대 깊이

# API 직접 업로드 가능 포맷
UPLOAD_FORMATS = {".flac", ".mp3", ".mp4", ".mpeg", ".mpga", ".m4a",
                  ".ogg", ".wav", ".webm"}
# ffmpeg 변환 필요 포맷
VIDEO_ONLY_EXT = {".mkv", ".avi", ".mov", ".wmv", ".flv", ".ts"}
ALL_SUPPORTED = UPLOAD_FORMATS | VIDEO_ONLY_EXT

# API 비용 (USD / 분)
COST_PER_MIN = {
    "gpt-4o-transcribe-diarize":         0.006,
    "gpt-4o-transcribe":                 0.006,
    "gpt-4o-mini-transcribe":            0.003,
    "gpt-4o-mini-transcribe-2025-12-15": 0.003,
    "whisper-1":                         0.006,
}
LLM_COST_PER_1K_TOKENS = {"gpt-4o": 0.005, "claude": 0.003}

TYPE_LABELS = {
    "meeting": {"title": "회의록",    "event": "회의",   "emoji": "🤝"},
    "seminar": {"title": "세미나 기록", "event": "세미나", "emoji": "🎓"},
    "lecture": {"title": "강의 노트",  "event": "강의",   "emoji": "📚"},
}

MAX_LLM_CHARS = 80_000
MAX_RETRIES   = 3
RETRY_DELAY   = 5


# ──────────────────────────────────────────────
#  Logging / Debug
# ──────────────────────────────────────────────
DEBUG = False
logger = logging.getLogger("meeting_minutes")


def setup_logging(debug: bool, output_dir: str = "./output"):
    global DEBUG
    DEBUG = debug
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter("[%(asctime)s] %(levelname)-7s %(message)s",
                            datefmt="%H:%M:%S")
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.DEBUG if debug else logging.WARNING)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    if debug:
        os.makedirs(output_dir, exist_ok=True)
        log_path = os.path.join(output_dir, "debug.log")
        fh = logging.FileHandler(log_path, encoding="utf-8", mode="a")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            "[%(asctime)s] %(levelname)-7s [%(funcName)s:%(lineno)d] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        logger.addHandler(fh)
        print(f"  디버그 로그 → {log_path}")


def debug_save(data: Any, filepath: str, label: str):
    if not DEBUG:
        return
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            if isinstance(data, (dict, list)):
                json.dump(data, f, ensure_ascii=False, indent=2)
            else:
                f.write(str(data))
        logger.debug(f"[DEBUG SAVE] {label} → {filepath}")
    except Exception as e:
        logger.debug(f"[DEBUG SAVE FAIL] {label}: {e}")


# ──────────────────────────────────────────────
#  출력 헬퍼
# ──────────────────────────────────────────────
def ts(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s   = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def step(msg: str):
    print(f"\n{'='*60}\n  {msg}\n{'='*60}")
    logger.info(f"STEP: {msg}")


def info(msg: str):
    print(f"  {msg}")
    logger.info(msg)


def ok(msg: str):
    print(f"  ✅ {msg}")
    logger.info(msg)


def warn(msg: str):
    print(f"  ⚠  {msg}")
    logger.warning(msg)


def err(msg: str):
    print(f"  ❌ {msg}", file=sys.stderr)
    logger.error(msg)


def file_mb(p: str) -> float:
    return os.path.getsize(p) / (1024 * 1024) if os.path.exists(p) else 0.0


# ──────────────────────────────────────────────
#  시스템 유틸
# ──────────────────────────────────────────────
def run_cmd(cmd: List[str], check: bool = True) -> subprocess.CompletedProcess:
    """subprocess 래퍼 — Windows cp949 인코딩 문제 방지."""
    logger.debug(f"[CMD] {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0 and check:
        logger.error(f"[CMD FAIL] exit={result.returncode}\nstderr: {result.stderr[:500]}")
        raise RuntimeError(f"명령 실패 (exit {result.returncode}): {cmd[0]}")
    return result


def audio_duration(p: str) -> float:
    try:
        r = run_cmd(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", p],
        )
        return float(json.loads(r.stdout)["format"]["duration"])
    except Exception:
        return 0.0


def check_ffmpeg() -> bool:
    try:
        run_cmd(["ffmpeg", "-version"])
        return True
    except Exception:
        return False


def read_file(p: str) -> str:
    with open(p, "r", encoding="utf-8") as f:
        return f.read()


def get_api_key(env_name: str, code_value: str = "") -> Optional[str]:
    key = os.environ.get(env_name) or code_value or None
    if key:
        masked = key[:8] + "..." + key[-4:]
        logger.debug(f"API Key [{env_name}]: {masked}")
    return key


def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', '_', name).strip()


def parse_session_dt_from_filename(filename: str) -> str:
    """파일명에서 날짜/시간 자동 파싱. 실패 시 '' 반환.

    지원 패턴:
      2026-06-29 14.10_...      → "2026년 06월 29일 14:10"  (하이픈 구분 날짜)
      realtime_20260303_145540  → "2026년 03월 03일 14:55"
      meeting_20260303          → "2026년 03월 03일"
      20260303_145540_whatever  → "2026년 03월 03일 14:55"
      260627_5                  → "2026년 06월 27일"
    """
    try:
        from date_utils import parse_session_dt_from_path
        return parse_session_dt_from_path(filename, default="")
    except Exception:
        return ""


def _date_key_local(s: str) -> str:
    s = str(s or "")
    m = re.search(r"(\d{4})\s*[-/.년]\s*(\d{1,2})\s*[-/.월]\s*(\d{1,2})", s)
    if not m:
        m = re.search(r"(\d{4})(\d{2})(\d{2})", s)
    if not m:
        return ""
    return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"


def segments_to_plain_text(segments: List[Dict], max_chars: int = 4000) -> str:
    return " ".join(str(s.get("text", "")).strip() for s in segments or [] if s.get("text"))[:max_chars]


def make_output_dir(base_dir: str, title: str) -> str:
    """출력 디렉토리 생성: {base_dir}/{날짜}_{제목}/"""
    date_str = datetime.now().strftime("%Y-%m-%d")
    folder   = sanitize_filename(f"{date_str}_{title}")
    out_dir  = os.path.join(base_dir, folder)
    os.makedirs(out_dir, exist_ok=True)
    return out_dir


def _norm_resume_key(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", text or "").lower()


def _has_reusable_stt(d: Path) -> bool:
    return any(d.glob("*segments.json")) or any(d.glob("segments.json")) or (d / "transcript.md").is_file()


def find_existing_output_dir(
    base_dir: str,
    title: str,
    *,
    include_transcript: bool = True,
) -> Optional[str]:
    """--resume / --edit-speakers 용: 가장 최근의 기존 STT/전사 폴더 반환."""
    safe = sanitize_filename(title)
    safe_norm = _norm_resume_key(safe)
    if not os.path.isdir(base_dir):
        return None
    candidates = []
    for d in Path(base_dir).iterdir():
        if not d.is_dir():
            continue
        if include_transcript:
            reusable = _has_reusable_stt(d)
        else:
            reusable = any(d.glob("*segments.json")) or any(d.glob("segments.json"))
        if not reusable:
            continue

        name_norm = _norm_resume_key(d.name)
        if safe and safe in d.name:
            score = 3.0
        elif safe_norm and safe_norm in name_norm:
            score = 2.0
        else:
            ratio = SequenceMatcher(None, safe_norm, name_norm).ratio() if safe_norm else 0.0
            if ratio < 0.78:
                continue
            score = ratio
        candidates.append((score, d.stat().st_mtime, str(d)))
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return candidates[0][2] if candidates else None


def _parse_ts_token(token: str) -> float:
    parts = [int(p) for p in token.split(":")]
    if len(parts) == 2:
        return float(parts[0] * 60 + parts[1])
    if len(parts) == 3:
        return float(parts[0] * 3600 + parts[1] * 60 + parts[2])
    return 0.0


def load_segments_from_transcript(transcript_path: str) -> List[Dict[str, Any]]:
    """transcript.md만 남은 이전 실행 결과를 STT 재실행 없이 재사용한다."""
    text = read_file(transcript_path)
    segments: List[Dict[str, Any]] = []
    last_start = 0.0
    for line in text.splitlines():
        line = line.strip()
        m = re.match(r"^\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*(.+)$", line)
        if not m:
            continue
        start = _parse_ts_token(m.group(1))
        body = m.group(2).strip()
        if not body:
            continue
        last_start = start
        segments.append({
            "start": start,
            "end": start,
            "speaker": "Speaker",
            "text": body,
        })
    if not segments:
        raise RuntimeError(f"전사 파일에서 세그먼트를 복원할 수 없음: {transcript_path}")

    for i, seg in enumerate(segments):
        if i + 1 < len(segments) and segments[i + 1]["start"] > seg["start"]:
            seg["end"] = segments[i + 1]["start"]
        else:
            seg["end"] = max(seg["start"] + 1.0, last_start + 1.0)
    return segments


def make_openai_client(api_key: str):
    """OpenAI 클라이언트 생성 (SSL 우회 지원)."""
    from openai import OpenAI
    if not SSL_VERIFY and HAS_HTTPX:
        import warnings
        warnings.filterwarnings("ignore", message="Unverified HTTPS request")
        http_client = httpx.Client(verify=False)
        logger.debug("OpenAI client: SSL 검증 비활성화")
        return OpenAI(api_key=api_key, http_client=http_client)
    return OpenAI(api_key=api_key)


def make_anthropic_client(api_key: str):
    """Anthropic 클라이언트 생성 (SSL 우회 지원)."""
    import anthropic as ant
    if not SSL_VERIFY and HAS_HTTPX:
        import warnings
        warnings.filterwarnings("ignore", message="Unverified HTTPS request")
        http_client = httpx.Client(verify=False)
        logger.debug("Anthropic client: SSL 검증 비활성화")
        return ant.Anthropic(api_key=api_key, http_client=http_client)
    return ant.Anthropic(api_key=api_key)


def retry_call(func, *args, retries: int = MAX_RETRIES, delay: int = RETRY_DELAY, **kwargs):
    """자동 재시도 래퍼."""
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_err = e
            if attempt < retries:
                warn(f"  재시도 {attempt}/{retries} ({delay}초 후)...")
                logger.warning(f"재시도 {attempt}/{retries}: {type(e).__name__}: {e}")
                time.sleep(delay)
            else:
                logger.error(f"최종 실패: {type(e).__name__}: {e}")
    raise last_err


def has_timestamps(segments: List[Dict]) -> bool:
    """세그먼트에 실제 타임스탬프가 있는지 확인 (start != end 이면 있음)."""
    return any(s.get("start", 0) != s.get("end", 0) for s in segments)


# ──────────────────────────────────────────────
#  LLM Client  (GPT-4o ↔ Claude 폴백)
# ──────────────────────────────────────────────
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
            if DEBUG:
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
            if DEBUG:
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


# ──────────────────────────────────────────────
#  비용 추정
# ──────────────────────────────────────────────
def estimate_cost(input_paths: List[str], model: str,
                  translate: bool, llm: str) -> dict:
    total_dur = 0.0
    for p in input_paths:
        d = audio_duration(p)
        total_dur += d if d > 0 else 60.0

    total_min   = total_dur / 60
    stt_cost    = total_min * COST_PER_MIN.get(model, 0.006)
    est_tokens  = total_min * 200
    llm_rate    = LLM_COST_PER_1K_TOKENS.get(llm, 0.005)
    llm_cost    = (est_tokens * 3 / 1000) * llm_rate
    trans_cost  = (est_tokens * 2 / 1000) * llm_rate if translate else 0.0

    return {
        "files":              len(input_paths),
        "total_duration_min": round(total_min, 1),
        "stt_cost":           round(stt_cost, 3),
        "llm_cost":           round(llm_cost, 3),
        "translate_cost":     round(trans_cost, 3),
        "total_cost":         round(stt_cost + llm_cost + trans_cost, 3),
    }


def print_cost_estimate(est: dict):
    print(f"\n  비용 추정")
    print(f"  {'─'*30}")
    print(f"  파일 수:       {est['files']}개")
    print(f"  총 길이:       ~{est['total_duration_min']}분")
    print(f"  STT 비용:      ~${est['stt_cost']:.3f}")
    print(f"  LLM 비용:      ~${est['llm_cost']:.3f}")
    if est["translate_cost"] > 0:
        print(f"  번역 비용:     ~${est['translate_cost']:.3f}")
    print(f"  {'─'*30}")
    print(f"  예상 합계:     ~${est['total_cost']:.3f}")
    print(f"  (실제 비용은 오디오 길이에 따라 다를 수 있습니다)\n")


# ──────────────────────────────────────────────
#  오디오 준비
# ──────────────────────────────────────────────
def prepare_audio(input_path: str, work_dir: str) -> str:
    step("오디오 준비 중...")
    ext  = Path(input_path).suffix.lower()
    size = file_mb(input_path)
    info(f"입력: {Path(input_path).name}  ({size:.1f} MB, {ext})")
    logger.debug(f"입력 파일: {input_path}, {size:.2f}MB")

    if size <= MAX_FILE_SIZE_MB and ext in UPLOAD_FORMATS:
        info(f"포맷 {ext}, {size:.1f}MB → 변환 없이 직접 업로드")
        return input_path

    info(f"mp3 변환 중... (원본 {size:.1f}MB)")
    out = os.path.join(work_dir, Path(input_path).stem + ".mp3")
    run_cmd([
        "ffmpeg", "-y", "-i", input_path,
        "-vn", "-ar", "16000", "-ac", "1", "-b:a", "48k", out,
    ])
    new_size = file_mb(out)
    ok(f"변환 완료: {size:.1f}MB → {new_size:.1f}MB  ({out})")
    return out


def _extract_audio_segment(audio_path: str, offset: float, duration: float, out_path: str) -> str:
    """audio_path의 [offset, offset+duration) 구간을 out_path(mp3)로 추출."""
    run_cmd([
        "ffmpeg", "-y", "-i", audio_path,
        "-ss", str(offset), "-t", str(duration),
        "-ar", "16000", "-ac", "1", "-b:a", "48k", out_path,
    ])
    return out_path


def split_audio(audio_path: str, work_dir: str) -> List[Tuple[str, float]]:
    """파일 크기(25MB) 또는 길이(1200s) 초과 시 청크 분할."""
    size = file_mb(audio_path)
    dur  = audio_duration(audio_path)
    logger.debug(f"오디오: {size:.2f}MB, {dur:.1f}s ({ts(dur)})")

    if size <= MAX_FILE_SIZE_MB and dur <= MAX_CHUNK_DURATION_SEC:
        return [(audio_path, 0.0)]

    # 크기 기준과 시간 기준 중 더 많은 청크 수 사용
    n_by_size = math.ceil(size / (MAX_FILE_SIZE_MB * 0.85)) if size > MAX_FILE_SIZE_MB else 1
    n_by_dur  = math.ceil(dur / MAX_CHUNK_DURATION_SEC) if dur > MAX_CHUNK_DURATION_SEC else 1
    n         = max(n_by_size, n_by_dur)

    info(f"파일 {size:.1f}MB, {dur:.0f}s → {n}개 청크 분할")
    chunk_dur = dur / n
    stem      = Path(audio_path).stem
    chunks    = []

    for i in range(n):
        offset = i * chunk_dur
        cp     = os.path.join(work_dir, f"{stem}_chunk{i:03d}.mp3")
        _extract_audio_segment(audio_path, offset, chunk_dur, cp)
        logger.debug(f"  청크 {i}: offset={ts(offset)}, {file_mb(cp):.2f}MB")
        chunks.append((cp, offset))

    info(f"{n}개 청크 생성")
    return chunks


# ──────────────────────────────────────────────
#  STT — OpenAI Transcription API
# ──────────────────────────────────────────────
def transcribe_chunk(
    client, audio_path: str, model: str,
    language: Optional[str] = None,
    speaker_names: Optional[List[str]] = None,
    offset: float = 0.0,
    debug_dir: Optional[str] = None,
    chunk_index: int = 0,
) -> List[Dict]:
    use_diarize = "diarize" in model
    use_whisper = model.startswith("whisper")
    logger.debug(f"[STT] model={model}, file={audio_path}, "
                 f"{file_mb(audio_path):.2f}MB, offset={offset:.1f}s")

    f = open(audio_path, "rb")
    try:
        params: Dict[str, Any] = {"model": model, "file": f}

        if use_diarize:
            params["response_format"]   = "diarized_json"
            params["chunking_strategy"] = "auto"
            if speaker_names:
                params["known_speaker_names"] = speaker_names[:4]
        elif use_whisper:
            params["response_format"]         = "verbose_json"
            params["timestamp_granularities"] = ["segment"]
        else:
            params["response_format"]   = "json"
            params["chunking_strategy"] = "auto"

        # language 가 "auto"/빈값이면 파라미터 생략 → 모델이 자동 감지(한국어·영어 모두 처리)
        if language and str(language).strip().lower() != "auto":
            params["language"] = language

        t0   = time.time()
        resp = client.audio.transcriptions.create(**params)
        logger.debug(f"[STT TIME] {time.time()-t0:.1f}s")
    finally:
        f.close()

    data = resp if isinstance(resp, dict) else (
        resp.model_dump() if hasattr(resp, "model_dump") else json.loads(resp)
    )

    if debug_dir:
        debug_save(data,
                   os.path.join(debug_dir, f"stt_raw_chunk{chunk_index:03d}.json"),
                   f"STT raw chunk {chunk_index}")

    logger.debug(f"[STT KEYS] {list(data.keys())}")

    if use_diarize:
        return _parse_diarized(data, offset)
    elif use_whisper:
        return _parse_verbose(data, offset)
    else:
        return _parse_json_simple(data, offset)


def _parse_diarized(data: dict, offset: float) -> List[Dict]:
    segments: List[Dict] = []

    if "speakers" in data and isinstance(data["speakers"], list):
        logger.debug("[PARSE] speakers 배열")
        for spk in data["speakers"]:
            label = spk.get("name") or spk.get("id", "Speaker")
            for seg in spk.get("segments", []):
                segments.append({
                    "start":   seg.get("start", 0) + offset,
                    "end":     seg.get("end",   0) + offset,
                    "text":    seg.get("text", "").strip(),
                    "speaker": label,
                })
        segments.sort(key=lambda x: x["start"])
        if segments:
            return segments

    if "segments" in data and isinstance(data["segments"], list):
        logger.debug("[PARSE] flat segments")
        for seg in data["segments"]:
            segments.append({
                "start":   seg.get("start", 0) + offset,
                "end":     seg.get("end",   0) + offset,
                "text":    seg.get("text", "").strip(),
                "speaker": seg.get("speaker", "Speaker"),
            })
        if segments:
            return segments

    if "words" in data and isinstance(data["words"], list):
        logger.debug("[PARSE] words → 문장 병합")
        cur: Dict = {"start": 0, "end": 0, "text": "", "speaker": ""}
        for w in data["words"]:
            spk  = w.get("speaker", "Speaker")
            word = w.get("word", w.get("text", ""))
            if spk != cur["speaker"] and cur["text"].strip():
                segments.append({"start": cur["start"], "end": cur["end"],
                                 "text": cur["text"].strip(), "speaker": cur["speaker"]})
                cur = {"start": w.get("start", 0) + offset,
                       "end":   w.get("end",   0) + offset,
                       "text":  word, "speaker": spk}
            else:
                if not cur["text"]:
                    cur["start"]   = w.get("start", 0) + offset
                    cur["speaker"] = spk
                cur["end"]  = w.get("end", 0) + offset
                cur["text"] += " " + word
        if cur["text"].strip():
            segments.append({"start": cur["start"], "end": cur["end"],
                             "text": cur["text"].strip(), "speaker": cur["speaker"]})
        if segments:
            return segments

    segments.append({"start": offset, "end": offset,
                     "text": data.get("text", ""), "speaker": "Speaker"})
    return segments


def _parse_verbose(data: dict, offset: float) -> List[Dict]:
    segments = []
    for seg in data.get("segments", []):
        segments.append({
            "start": seg["start"] + offset, "end": seg["end"] + offset,
            "text":  seg["text"].strip(), "speaker": "",
        })
    if not segments and data.get("text"):
        segments.append({"start": offset, "end": offset,
                         "text": data["text"], "speaker": ""})
    return segments


def _parse_json_simple(data: dict, offset: float) -> List[Dict]:
    """
    gpt-4o-transcribe / gpt-4o-mini-transcribe → {"text": "..."} 만 반환.
    타임스탬프 없음 → 문장 단위로 분할하여 세그먼트화.
    """
    text = data.get("text", "").strip()
    if not text:
        return [{"start": offset, "end": offset, "text": "", "speaker": ""}]

    sentences = re.split(r'(?<=[.!?。！？])\s+', text)
    merged, buf = [], ""
    for s in sentences:
        buf = (buf + " " + s).strip() if buf else s
        if len(buf) > 30:
            merged.append(buf)
            buf = ""
    if buf:
        if merged:
            merged[-1] = merged[-1] + " " + buf
        else:
            merged.append(buf)

    result = [{"start": offset, "end": offset,
               "text": sent.strip(), "speaker": ""}
              for sent in merged]
    logger.debug(f"[PARSE JSON] {len(text)}자 → {len(result)}개 세그먼트")
    return result


_CJK_RANGES = (
    r'\u3000-\u303F'   # CJK 기호
    r'\u3040-\u309F'   # 히라가나
    r'\u30A0-\u30FF'   # 가타카나
    r'\u4E00-\u9FFF'   # CJK 통합 한자
    r'\uF900-\uFAFF'   # CJK 호환 한자
)
_RE_CJK = re.compile(f'[{_CJK_RANGES}]')


def _is_cjk_hallucination(text: str, threshold: float = 0.3) -> bool:
    """텍스트 내 CJK(중국어/일본어) 문자 비율이 threshold 이상이면 True."""
    if not text or len(text.strip()) < 2:
        return False
    cjk_count = len(_RE_CJK.findall(text))
    return (cjk_count / len(text)) >= threshold


def _looks_truncated(segs: List[Dict], duration: float, has_timestamps: bool) -> bool:
    """청크 길이 대비 전사 결과 분량이 비정상적으로 적으면(중간에 잘렸으면) True."""
    if duration <= 0:
        return False
    total_chars = sum(len(s.get("text", "")) for s in segs)
    if (total_chars / duration) < MIN_STT_CHARS_PER_SEC:
        return True
    if has_timestamps and segs:
        last_end = max(s.get("end", 0) for s in segs)
        if last_end < duration * 0.7:
            return True
    return False


def _transcribe_chunk_checked(
    client, audio_path: str, model: str,
    language: Optional[str], speaker_names: Optional[List[str]],
    offset: float, debug_dir: Optional[str], chunk_index: int,
    work_dir: str, depth: int = 0,
) -> List[Dict]:
    """transcribe_chunk 결과가 잘린 것으로 보이면 청크를 반으로 나눠 재시도."""
    segs = transcribe_chunk(
        client, audio_path, model, language, speaker_names,
        offset, debug_dir, chunk_index,
    )

    dur      = audio_duration(audio_path)
    has_ts   = "diarize" in model or model.startswith("whisper")
    if depth < MAX_STT_RETRY_SPLIT_DEPTH and _looks_truncated(segs, dur, has_ts):
        warn(f"  청크 {chunk_index} 전사 결과가 {dur:.0f}s 길이 대비 비정상적으로 짧음 → 2분할 재시도")
        half = dur / 2
        retried: List[Dict] = []
        for j, sub_offset in enumerate((0.0, half)):
            sub_path = os.path.join(
                work_dir, f"{Path(audio_path).stem}_retry{depth}_{j}.mp3",
            )
            _extract_audio_segment(audio_path, sub_offset, half, sub_path)
            try:
                retried.extend(_transcribe_chunk_checked(
                    client, sub_path, model, language, speaker_names,
                    offset + sub_offset, debug_dir, chunk_index,
                    work_dir, depth + 1,
                ))
            finally:
                if os.path.exists(sub_path):
                    os.remove(sub_path)
        return retried

    if _looks_truncated(segs, dur, has_ts):
        warn(f"  청크 {chunk_index} 전사 결과가 여전히 짧지만 재시도 한도 도달 → 그대로 사용")

    return segs


def run_stt(
    audio_path: str, model: str = DEFAULT_STT_MODEL,
    language: Optional[str] = None,
    speaker_names: Optional[List[str]] = None,
    work_dir: Optional[str] = None,
    debug_dir: Optional[str] = None,
) -> List[Dict]:
    step(f"STT 수행 중  (model: {model})")
    work_dir = work_dir or tempfile.gettempdir()

    key    = get_api_key("OPENAI_API_KEY", OPENAI_API_KEY)
    client = make_openai_client(key)

    chunks       = split_audio(audio_path, work_dir)
    all_segments: List[Dict] = []
    total_time   = 0.0

    # 청크 분할 시 화자 연속성이 끊겨 diarize 결과가 무의미해짐 → fallback 모델 사용
    effective_model = model
    if len(chunks) > 1 and "diarize" in model:
        warn(f"  청크 분할 필요({len(chunks)}개): {model} → {FALLBACK_STT_MODEL} 자동 전환 (화자 연속성 불가)")
        effective_model = FALLBACK_STT_MODEL

    for i, (cp, chunk_offset) in enumerate(chunks):
        if len(chunks) > 1:
            info(f"  청크 {i+1}/{len(chunks)} 처리 중...")

        t0 = time.time()
        try:
            segs = _transcribe_chunk_checked(
                client, cp, effective_model, language, speaker_names,
                chunk_offset, debug_dir, i, work_dir,
            )
            all_segments.extend(segs)
        except Exception as e:
            logger.error(f"[STT FAIL] chunk {i}: {type(e).__name__}: {e}")
            if DEBUG:
                logger.debug(traceback.format_exc())
            if effective_model != FALLBACK_STT_MODEL:
                warn(f"  {model} 실패 ({e})")
                warn(f"  → {FALLBACK_STT_MODEL} 로 폴백")
                segs = _transcribe_chunk_checked(
                    client, cp, FALLBACK_STT_MODEL, language, None,
                    chunk_offset, debug_dir, i, work_dir,
                )
                all_segments.extend(segs)
            else:
                raise

        elapsed     = time.time() - t0
        total_time += elapsed
        logger.debug(f"  청크 {i}: {elapsed:.1f}s, 누적 {len(all_segments)} segs")

        if cp != audio_path and os.path.exists(cp):
            os.remove(cp)

    # CJK 환각 필터 — 중국어/일본어 텍스트 제거
    filtered = [s for s in all_segments if not _is_cjk_hallucination(s.get("text", ""))]
    if len(filtered) < len(all_segments):
        warn(f"  CJK 환각 필터: {len(all_segments) - len(filtered)}개 세그먼트 제거")
    ok(f"STT 완료: {len(filtered)}개 세그먼트 ({total_time:.1f}초)")
    return filtered


# ──────────────────────────────────────────────
#  번역 (영어 → 한국어)
# ──────────────────────────────────────────────
_TRANSLATE_CONTEXT_WINDOW = 5  # 이전 배치에서 가져올 컨텍스트 세그먼트 수


def translate_segments(
    segments: List[Dict], llm: LLMClient,
    batch_size: int = 30, debug_dir: Optional[str] = None,
) -> List[Dict]:
    step("영어 → 한국어 번역 중...")
    translated: List[Dict] = []
    total = math.ceil(len(segments) / batch_size)

    for bi in range(total):
        batch = segments[bi * batch_size : (bi + 1) * batch_size]
        info(f"  배치 {bi+1}/{total} ({len(batch)}개)")

        # 이전 배치의 마지막 N개를 컨텍스트 힌트로 제공 (용어 일관성 유지)
        context_hint = ""
        if bi > 0 and translated:
            prev_ctx = translated[-_TRANSLATE_CONTEXT_WINDOW:]
            ctx_lines = "\n".join(
                f"원문: {s.get('text_original', s['text'])} | 번역: {s['text']}"
                for s in prev_ctx
            )
            context_hint = (
                "[이전 문맥 참조 — 번역 대상 아님, 용어 일관성 유지용]\n"
                f"{ctx_lines}\n\n"
            )

        items = json.dumps(
            [{"i": i, "t": s["text"]} for i, s in enumerate(batch)],
            ensure_ascii=False,
        )
        system = (
            "전문 영한 번역가. 회의/세미나/강의 발화를 자연스러운 한국어로 번역.\n"
            "전문 용어는 원문 병기(예: 인공지능(AI)).\n"
            "동일 개념은 배치 전반에 걸쳐 일관된 용어로 번역.\n"
            "반드시 한국어로만 번역. 중국어·일본어·기타 언어로 절대 출력하지 마세요.\n"
            'JSON 배열로만 응답: [{"i":0,"t":"번역"},...]\n'
            "Markdown·설명 없이 순수 JSON만."
        )
        user = context_hint + items
        try:
            raw = llm.chat(system, user, temp=0.2)
            if debug_dir:
                debug_save(raw,
                           os.path.join(debug_dir, f"translate_batch{bi:03d}.txt"),
                           f"Translate {bi}")
            from json_utils import parse_json_loose
            arr = parse_json_loose(raw, expect="list")
            if arr is None:
                raise ValueError("번역 JSON 파싱 실패")
            tmap = {a["i"]: a["t"] for a in arr if isinstance(a, dict) and "i" in a}
            for i, s in enumerate(batch):
                ns = s.copy()
                ns["text_original"] = s["text"]
                ns["text"]          = tmap.get(i, s["text"])
                translated.append(ns)
        except Exception as e:
            warn(f"  배치 {bi+1} 번역 실패: {e} → 원문 유지")
            translated.extend(batch)
        if bi < total - 1:
            time.sleep(0.5)

    ok(f"번역 완료: {len(translated)}개")
    return translated


# ──────────────────────────────────────────────
#  스크립트 생성
# ──────────────────────────────────────────────
def build_script_md(segments: List[Dict], include_original: bool = False) -> str:
    use_ts = has_timestamps(segments)
    lines = [
        "# 스크립트 (Transcript)\n",
        f"> 생성: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"> 세그먼트: {len(segments)}개\n",
        "---\n",
    ]
    cur_spk = None
    for s in segments:
        spk = s.get("speaker", "")
        if spk and spk != cur_spk:
            lines.append(f"\n### {spk}\n")
            cur_spk = spk

        line = (f"`[{ts(s['start'])}]` {s['text']}" if use_ts else s["text"])
        if include_original and s.get("text_original"):
            line += f"\n> _{s['text_original']}_"
        lines.append(line)

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
#  프롬프트 템플릿 — 여기를 직접 편집하여 구조·규칙을 변경할 수 있습니다.
#  {prefix} 자리는 주제·일시·지시문이 자동 삽입됩니다 (수정 금지).
# ══════════════════════════════════════════════════════════════════

_MINUTES_MEETING = """\
{prefix}전문 회의록 작성자입니다.
스크립트의 모든 논의 내용을 주제별로 정리·종합하여, 빠짐없이 체계적으로 기록하는 것이 핵심 임무입니다.

## 핵심 원칙
1. 스크립트에 등장하는 모든 논의 주제·결정·수치·일정·고유명사를 누락 없이 반영
2. 개별 발언을 시간순으로 나열하지 말고, **주제별로 종합·정리** (타임스탬프 표기 금지)
3. 수치·일정·고유명사·제품명은 원문 그대로 유지 (의역 금지)
4. 핵심 사실·숫자·결정은 **굵게** 강조
5. 화자·조직·역할을 **추측하거나 지어내지 말 것**. 참석자 명단이 제공되면 그 이름만 사용하고, 특정할 수 없으면 귀속하지 않음. 스크립트에 명시되지 않은 소속·팀·직책·발언자(예: "발언자 A", 가상의 팀명)는 만들지 말 것
6. 메모(추가 메모)가 있으면 논의 내용과 적극 연결하여 반영
7. 전문적·격식 문체, 한국어
8. 인사·잡담·여담·진행상 군더더기 등 비중요 발언은 회의록에 싣지 말 것 — 핵심 논의·결정·액션만 정리
9. **스크립트·메모에 없는 사실/인물/조직/수치/기한은 절대 생성하지 말 것.** 불명확하면 "미정"으로 표기
10. 계약·교육·운영 회의는 배경, 일정, 계약 조건, 비용 청구 범위, 운영 프로세스, 이해관계자별 R&R을 별도 안건으로 반드시 분리
11. 지주사/그룹사/계열사/교육기관/외부업체처럼 운영 주체가 여럿이면 주체별 역할·권한·비용·의사결정 범위를 표나 액션으로 구체화

## 출력 형식 (이 구조를 정확히 따를 것)

## YYMMDD [회의 주제] 회의록

- **일시**: YYYY.MM.DD(요일) HH:MM ~
- **장소**: (언급된 경우 기재, 없으면 항목 생략)
- **참석자**: (제공된 참석자 명단을 그대로 기재. 명단이 없으면 "미정")
- **안건**
    1. 배경 및 진행 경과
    2. 세부 계약 조건 및 운영 방안
    3. 교육 체계/일정/R&R

---

### 주요 논의 내용

### A. 배경 및 진행 경과

- **협의 배경**
    - 세부 내용
- **선정/전환 경과**
    - 세부 내용

### B. [첫 번째 주요 안건 제목]

- **소주제/논점**
    - 세부 내용 (핵심 수치·사실은 **굵게**)
    - 세부 내용
- **소주제/논점**
    - 세부 내용
    - 개선안: 구체적 방안

### C. [두 번째 주요 안건 제목]

- **소주제/논점**
    - 세부 내용

(안건 수만큼 반복)

### [필요 시] 운영 주체별 R&R

| 주체 | 역할/책임 | 확인 필요 | 후속 액션 |
|---|---|---|---|
| 인재개발원/센터 | 스크립트·메모에 근거한 역할 | 미정/확인 필요 | 담당/기한 |
| 내부 수행사/그룹사 | 스크립트·메모에 근거한 역할 | 미정/확인 필요 | 담당/기한 |
| 지주사/그룹사 | 분리 운영 여부와 범위 | 미정/확인 필요 | 담당/기한 |

---

### 결정 사항(합의/정리된 방향)

1. **결정 요약**: 구체적 내용
2. **결정 요약**: 구체적 내용

---

### Action Item (담당/기한)

- 구체적 업무 내용 — 담당: (제공된 명단 내 인물, 특정 불가 시 "미정") · 기한: (스크립트에 있으면 명시, 없으면 생략)
- 구체적 업무 내용 — 담당: 미정
  ※ 담당자를 임의의 조직/팀명으로 지어내지 말 것

## 세부 작성 규칙

### 제목
- `## YYMMDD` 형식 (예: 260305), 뒤에 회의 주제와 "회의록"
- 주제는 스크립트 도입부·메모·topic 메타정보에서 추론

### 참석자
- **제공된 참석자 명단(메타데이터/메모)을 그대로 사용.** 명단에 없는 이름·조직·팀·역할을 새로 만들지 말 것
- 명단이 없거나 화자를 특정할 수 없으면 **"미정"** 으로 표기 ("발언자 A/B/C"나 가상의 팀명 생성 금지)
- 화자 분리(diarization) 정보가 없으면 발언별로 담당자를 추정하지 말 것

### 안건
- 스크립트 전체 흐름에서 주요 주제를 식별하여 번호 목록으로 정리

### 주요 논의 내용
- 안건별로 `### A.`, `### B.`, `### C.` … 알파벳 순서로 소제목 부여
- 각 안건 아래 `- **소주제**` → 들여쓰기 `- 세부 내용` 계층 구조 사용
- 동일 주제에 대한 여러 발언은 하나의 소주제 아래 종합
- 의견 대립이 있으면 양측 입장을 모두 기술
- 질문과 답변은 맥락에 녹여서 기술 (별도 Q:/A: 형식 사용 안 함)

### 결정 사항
- 명시적으로 합의·확정된 사항만 기재
- 번호 목록, 각 항목은 `**핵심 키워드**`: 상세 내용

### Action Item
- 담당 조직/팀/개인별로 그룹핑 (표 형식 사용 금지)
- `- **[담당자/조직]**` 아래 들여쓰기로 업무 나열
- 기한이 언급되었으면 포함, 없으면 생략

### 섹션 구분
- 주요 섹션 사이에 `---` 구분선 사용

## 길이 기준 (필수)
- 스크립트 전체 내용을 충실히 반영하되, 반복·중복 발언은 통합
- 각 안건의 소주제마다 구체적 세부 내용·근거·수치를 충분히 포함할 것
- 안건 하나를 1~2줄로 축약하는 것은 금지 — 소주제별로 세부 불릿을 충실히 작성
- 75분 회의 기준 최소 A4 2~3쪽 이상 분량이 되어야 함"""

_MINUTES_SEMINAR = """\
{prefix}전문 세미나 기록 작성자입니다.
발표 스크립트의 모든 내용을 섹션별로 정리·종합하여, 빠짐없이 체계적으로 기록하는 것이 핵심 임무입니다.

## 핵심 원칙
1. 발표에서 다룬 모든 주제·개념·수치·사례를 누락 없이 반영 — 내용이 풍부할수록 좋음
2. 개별 발언을 시간순으로 나열하지 말고, **섹션/주제별로 종합·정리** (타임스탬프 표기 금지)
3. 기술 용어·수치·고유명사·제품명은 원문 그대로 표기
4. 핵심 개념·수치·결론은 **굵게** 강조
5. 발표자의 중요 문구·설명은 직접 인용("")으로 최대한 많이 보존
6. 메모(추가 메모)가 있으면 해당 섹션과 적극 연결하여 반영
7. 전문적 문체, 한국어
8. **과도한 압축 절대 금지** — 발표자가 설명한 이유·맥락·예시·실험 결과를 모두 포함

## 출력 형식 (이 구조를 정확히 따를 것)

## YYMMDD [세미나 주제] 세미나 기록

- **일시**: YYYY.MM.DD(요일) HH:MM ~
- **장소**: (언급된 경우 기재, 없으면 항목 생략)
- **발표자**: 명단·스크립트에 명시된 이름만 기재 (불명확하면 "미정", 소속·역할 임의 생성 금지)
- **참석자**: 제공된 명단을 그대로 기재 (없으면 "미정")
- **주제**: 한줄 요약

---

### 발표 내용

### A. [첫 번째 섹션 제목]

- **소주제/개념**
    - 핵심 개념·주장 및 상세 설명
    - 데이터·수치·예시 (원문 그대로, **핵심 수치는 굵게**)
    - 발표자 주요 발언: "직접 인용"
- **소주제/개념**
    - 세부 내용
    - 중요 슬라이드/도식 내용 (언급된 경우)

### B. [두 번째 섹션 제목]

- **소주제/개념**
    - 세부 내용

(섹션 수만큼 반복)

---

### Q&A

- **질문 주제**
    - 질문 내용 및 발표자 답변 요약 (전사본에 있는 내용만 기록)
- **질문 주제**
    - 질문 내용 및 답변

⚠️ **Q&A 작성 규칙 (엄수)**: 전사본에 실제 Q&A 내용이 있을 때만 이 섹션을 작성. 전사본이 종료 전 끊겼거나 Q&A 내용이 없으면 → "⚠️ Q&A 미캡처 (녹음 종료)" 한 줄만 작성. 없는 내용을 추론하거나 만들어내는 것은 절대 금지.

---

### 핵심 인사이트

- 실무에 즉시 적용 가능한 포인트 (발표자가 강조한 내용 중심)
- 주요 시사점
- 기존 기술·연구와의 차별점

---

### 검토 권고사항

- **미해결 질문 / 후속 연구**: Q&A 또는 발표에서 제기됐으나 해결되지 않은 문제점·한계
- **검증 필요 항목**: 수치·사실이 명확히 확인되지 않은 주장 ([검증 필요] 표시)
- **실무·연구 적용 시 주의사항**: 한계점, 전제 조건, 기술 성숙도 주의점
- **다음 단계 제안**: 심화 학습에 필요한 논문, 시도해볼 실험, 추가로 공부할 개념

---

### 참고 자료

#### 📌 발표에서 언급된 자료
- (발표자가 직접 인용·소개한 논문·도구·링크. 형식: **저자 연도**: 제목 — 학술지/URL)
- 없으면 "없음"

#### 💡 관련 심화 자료 (LLM 내부 지식)
- (발표 주제를 더 깊이 이해하는 데 도움이 되는 핵심 논문·자료. 발표에서 언급 여부 무관)
- 형식: **저자 연도**: 제목 — 학술지 | 이 세미나와의 연결점 한 줄
- 실제 존재하는 논문만 최소 3~5개. 불확실하면 기재하지 말 것.

## 세부 작성 규칙

### 제목
- `## YYMMDD` 형식 (예: 260305), 뒤에 세미나 주제와 "세미나 기록"
- 주제는 스크립트 도입부·메모·topic 메타정보에서 추론

### 발표 내용
- 섹션별로 `### A.`, `### B.`, `### C.` … 알파벳 순서로 소제목 부여
- 각 섹션 아래 `- **소주제**` → 들여쓰기 `- 세부 내용` 계층 구조 사용
- 동일 주제에 대한 여러 설명은 하나의 소주제 아래 종합

### Q&A
- 질문-답변을 주제별로 정리 (맥락에 녹여서 기술)

### 섹션 구분
- 주요 섹션 사이에 `---` 구분선 사용

## 길이 기준 (필수)
- 발표 전체 내용을 충실히 반영하되, 반복·중복 설명은 통합
- **각 소주제마다 최소 3~5개 세부 불릿 작성** — 핵심 개념·이유·예시·결과를 모두 포함
- 소주제를 1~2줄로 축약하는 것은 절대 금지 — 교수님이 설명한 배경·맥락·의의를 충분히 기록
- 발표자의 말 중 중요한 설명·비유·강조는 직접 인용으로 최대한 보존
- 실험 결과·수치·데이터셋 이름·벤치마크 지표는 구체적으로 기록
- **30분 발표 기준 최소 A4 3~4쪽, 60분 기준 6~8쪽 이상 분량이 되어야 함**
- 이론적 배경이 있는 경우 수식·알고리즘·개념 간 관계도 충분히 설명"""

_MINUTES_LECTURE = """\
{prefix}전문 강의 노트 작성자입니다.
강의 스크립트의 모든 내용을 챕터/주제별로 정리·종합하여, 빠짐없이 체계적으로 기록하는 것이 핵심 임무입니다.

## 핵심 원칙
1. 강의에서 다룬 모든 개념·예시·공식·논리 흐름을 누락 없이 반영
2. 개별 발언을 시간순으로 나열하지 말고, **챕터/주제별로 종합·정리** (타임스탬프 표기 금지)
3. 수치·공식·코드·고유명사는 원문 그대로 표기
4. 핵심 개념·공식·결론은 **굵게** 강조
5. 강사의 중요 문구는 직접 인용("")으로 표기
6. 메모(추가 메모)가 있으면 해당 개념과 적극 연결하여 반영
7. 전문적이되 이해하기 쉬운 문체, 한국어

## 출력 형식 (이 구조를 정확히 따를 것)

## YYMMDD [강의 주제] 강의 노트

- **일시**: YYYY.MM.DD(요일) HH:MM ~
- **장소**: (언급된 경우 기재, 없으면 항목 생략)
- **강사**: 이름 (역할/소속)
- **과목/주제**: 과목명 또는 주제
- **학습 목표**: (강사가 언급한 경우 기재)

---

### 강의 내용

### A. [첫 번째 챕터/주제 제목]

- **핵심 개념**
    - 정의 및 상세 설명
    - 개념의 이유·배경·맥락
- **예시/사례**
    - 강사가 제시한 구체적 사례 (수치·데이터 포함)
    - 실무 적용 방법 (언급된 경우)
- **공식/코드**
    - 원문 그대로 (블록 형식 사용)
    - 강사의 부연 설명
- **강사 발언 인용**
    - "중요 설명 직접 인용"

### B. [두 번째 챕터/주제 제목]

- **핵심 개념**
    - 세부 내용

(챕터 수만큼 반복)

---

### Q&A (학생 질문 & 강사 답변)

- **질문 주제**
    - 질문 내용 및 강사 답변 요약
- **질문 주제**
    - 질문 내용 및 답변

(질문이 없었으면 섹션 생략)

---

### 핵심 정리

- 시험·실무에 중요하다고 강사가 강조한 내용
- 반복 언급된 핵심 포인트

---

### 과제 / 다음 강의 예고

- 언급된 과제 (기한 포함)
- 예습 내용 및 다음 주제

(언급이 없었으면 섹션 생략)

---

### 참고 자료

- 언급된 교재·논문·링크·도구 (원문 표기)

(언급이 없었으면 섹션 생략)

## 세부 작성 규칙

### 제목
- `## YYMMDD` 형식 (예: 260305), 뒤에 강의 주제와 "강의 노트"
- 주제는 스크립트 도입부·메모·topic 메타정보에서 추론

### 강의 내용
- 챕터별로 `### A.`, `### B.`, `### C.` … 알파벳 순서로 소제목 부여
- 각 챕터 아래 `- **소주제**` → 들여쓰기 `- 세부 내용` 계층 구조 사용
- 하나의 개념 설명에 "정의 + 이유/맥락 + 예시"를 모두 포함
- 강사가 반복 강조한 내용은 명시적으로 중요도 표시

### Q&A
- 질문-답변을 주제별로 정리

### 섹션 구분
- 주요 섹션 사이에 `---` 구분선 사용

## 길이 기준 (필수)
- 강의 전체 내용을 충실히 반영하되, 반복·중복 설명은 통합
- 각 챕터의 소주제마다 구체적 세부 내용·근거·예시를 충분히 포함할 것
- 챕터 하나를 1~2줄로 축약하는 것은 금지 — 소주제별로 세부 불릿을 충실히 작성
- 개념 설명을 요약할 때도 이유·예시·논리는 반드시 포함 (과도한 축약 금지)
- 60분 강의 기준 최소 A4 2~3쪽 이상 분량이 되어야 함"""

_SUMMARY_MEETING = """\
{prefix}회의 요약 전문가입니다.
회의록 전문을 읽기 전에 30~60초 안에 판단할 수 있는 executive brief를 작성합니다.
요약은 회의록을 대체하지 않습니다. 세부 논의·근거·발언 흐름은 회의록 본문에 남기고, 여기서는 결론·리스크·후속조치만 압축합니다.

【출력 형식】

### 한눈에 보는 결론
• 회의의 최종 의미를 2~4문장으로 요약
• 확정된 것과 아직 미정인 것을 분리해서 표현

### 결정/합의
• 명확히 확정된 사항만 3~5개 이내
• 없으면 `확정된 결정 없음`이라고 적음

### 리스크/주의
• 사실 확인, 이해관계자 확인, 상충 가능성이 있는 항목만 3~5개 이내
• 추론·참고 배경은 직접 논의된 사실과 구분

### 다음 액션
• 담당자·기한이 명시된 일만 적고, 없으면 담당: 미정
• 5개 이내로 제한

【작성 원칙】
- 전체 400~700자 내외로 압축
- 회의록 본문의 `주요 논의 내용`을 다시 풀어 쓰지 말 것
- 배경 설명·상세 근거·세부 논쟁은 회의록 본문에 맡기고 요약에서는 생략
- 수치·일정·고유명사·제품명은 원문 그대로 유지
- 결정되지 않은 사항은 "미결:" 접두어로 명확히 표시
- 확인되지 않은 참석자·소속·담당자를 지어내지 말 것"""

_SUMMARY_SEMINAR = """\
{prefix}세미나 요약 전문가입니다.
참석하지 않은 동료가 이 요약본 하나만으로 발표 전체를 완전히 파악할 수 있어야 합니다.
이메일로 전송될 내용이므로 **충분한 깊이와 구체성**이 필요합니다.

【출력 형식】

• 일시: / 장소: / 발표자: (기록에 명시된 값 사용)
• 주제 한줄 요약
• 주요 섹션: (번호 목록)

────────────────────────────────────────
배경 / 개요
• 세미나 목적·맥락 (발표자가 설명한 배경, 연구 동기, 문제 제기 포함)
• 발표자 소개 (언급된 경우)

[섹션 1 제목]
• 핵심 주장 및 내용 요약
  ○ 발표자가 설명한 개념의 정의와 배경
  ○ 데이터·수치·실험 결과 (원문 그대로, 구체적으로)
  ○ 핵심 개념 설명 (발표자 표현·인용 포함)
  ○ 왜 중요한지, 어떤 의의가 있는지

[섹션 2 제목]
• … (각 섹션마다 위 구조로 충분히 기록)

────────────────────────────────────────
Q&A 핵심 요약
• 주요 질문과 발표자 답변 (질문 배경·답변 근거 포함)
• 미해결 질문 또는 후속 연구 필요 사항

────────────────────────────────────────
핵심 인사이트 및 시사점
• 발표자가 강조한 핵심 메시지
• 기존 연구/기술과의 차별점
• 향후 방향성 또는 한계점

────────────────────────────────────────
실무/연구 적용 포인트
• 한빝 관련 비즈니스나 연구에 적용 가능한 포인트
• 검토가 필요한 사항 또는 주의점

────────────────────────────────────────
후속 학습 자료
• 발표에서 언급된 논문·도구·링크 (저자/연도 포함)
• 추가 조사 권장 주제

【작성 원칙】
- 각 섹션은 "무엇이 발표됐고, 어떤 근거·실험이 제시됐으며, 왜 중요한가"를 포함
- 수치·고유명사·논문명은 원문 그대로 유지
- **섹션 하나를 1~2줄로 줄이지 말 것** — 핵심 근거·예시·맥락을 반드시 포함
- 압축은 허용하되 개념의 핵심을 제거하는 압축은 금지
- 전체 분량: 발표 길이에 비례 (30분 발표 → 최소 A4 1.5~2쪽)"""

_SUMMARY_LECTURE = """\
{prefix}강의 요약 전문가입니다.
강의에 참석하지 않은 학생이 이 요약본만으로 핵심 개념을 충분히 파악할 수 있어야 합니다.

【출력 형식】

• 강의명: / 강사: / 일시: (기록에 명시된 값 사용)
• 이번 강의 핵심 한줄 요약
• 다룬 챕터: (번호 목록)

────────────────────────────────────────
[챕터/개념 1 제목]
• 핵심 개념 정의 및 설명
  ○ 공식·코드 (원문 그대로, 블록 형식)
  ○ 강사 제시 예시 (구체적으로)
  ○ 이해에 필요한 배경·맥락

[챕터/개념 2 제목]
• …

────────────────────────────────────────
시험/과제 대비 포인트
• 강사가 강조한 내용, 반복 언급 항목 (중요도 표시)

────────────────────────────────────────
질문 & 답변 핵심
• 학생 질문과 강사 답변 요약 (이해에 도움이 되는 것만)

────────────────────────────────────────
다음 강의 준비
• 예습 내용·과제 (기한 포함)

【작성 원칙】
- 각 개념은 "정의 + 이유/맥락 + 예시"를 모두 포함
- 수치·공식·코드·고유명사는 원문 그대로 유지
- 압축은 허용하되 개념의 이유와 예시를 제거하는 압축은 금지"""


# ──────────────────────────────────────────────
#  LLM 프롬프트 조립 (topic / session_dt / no_cut 삽입)
# ──────────────────────────────────────────────
_MINUTES_TEMPLATES = {
    "meeting": _MINUTES_MEETING,
    "seminar": _MINUTES_SEMINAR,
    "lecture": _MINUTES_LECTURE,
}
_SUMMARY_TEMPLATES = {
    "meeting": _SUMMARY_MEETING,
    "seminar": _SUMMARY_SEMINAR,
    "lecture": _SUMMARY_LECTURE,
}

_NO_CUT = ("⚠ 모든 주제·개념·수치·일정·고유명사를 빠짐없이 반영하세요. "
           "주제별로 종합하되, 내용 누락은 금지입니다. "
           "각 소주제마다 충분한 세부 내용을 포함하여 짧은 기록이 되지 않도록 하세요.\n\n")

_NO_CUT_MEETING = ("⚠ 논의된 모든 주제·결정·수치·일정·고유명사를 빠짐없이 반영하세요. "
                   "개별 발언을 나열하지 말고 주제별로 종합하되, 내용 누락은 금지입니다. "
                   "각 소주제마다 충분한 세부 내용을 포함하여 짧은 기록이 되지 않도록 하세요.\n\n")


def _get_minutes_prompt(doc_type: str, topic: str = "", session_dt: str = "",
                        title: str = "") -> str:
    tmpl = _MINUTES_TEMPLATES.get(doc_type, "")
    if not tmpl:
        return ""
    prefix = ""
    if title:      prefix += f"제목/발표자 힌트: {title}\n"
    if topic:      prefix += f"주제: {topic}\n"
    if session_dt: prefix += f"일시: {session_dt}\n"
    if prefix:     prefix += "\n"
    no_cut = _NO_CUT_MEETING if doc_type == "meeting" else _NO_CUT
    prefix += no_cut
    return tmpl.format(prefix=prefix)


def _get_summary_prompt(doc_type: str, topic: str = "", session_dt: str = "") -> str:
    tmpl = _SUMMARY_TEMPLATES.get(doc_type, "")
    if not tmpl:
        return ""
    prefix = ""
    if topic:      prefix += f"주제: {topic}\n"
    if session_dt: prefix += f"일시: {session_dt}\n"
    if prefix:     prefix += "\n"
    return tmpl.format(prefix=prefix)


# ──────────────────────────────────────────────
#  장시간 스크립트 청크 분할 헬퍼
# ──────────────────────────────────────────────
def _split_script_chunks(
    script: str, max_chars: int, overlap: int = 2000
) -> List[str]:
    """타임스탬프 줄 기준으로 스크립트를 max_chars 이하 청크로 분할.
    인접 청크 간 overlap 문자 중첩으로 문맥 연속성 유지.
    """
    lines = script.split('\n')
    chunks: List[str] = []
    current: List[str] = []
    current_len = 0

    for line in lines:
        line_len = len(line) + 1  # +1 for newline
        if current_len + line_len > max_chars and current:
            chunks.append('\n'.join(current))
            # overlap: 마지막 N자만큼을 다음 청크 시작에 포함
            overlap_lines: List[str] = []
            overlap_total = 0
            for prev_line in reversed(current):
                if overlap_total + len(prev_line) + 1 > overlap:
                    break
                overlap_lines.insert(0, prev_line)
                overlap_total += len(prev_line) + 1
            current = overlap_lines
            current_len = overlap_total
        current.append(line)
        current_len += line_len

    if current:
        chunks.append('\n'.join(current))
    return chunks


def _merge_partial_minutes(
    parts: List[str], llm: LLMClient, doc_type: str
) -> str:
    """복수 파트의 회의록을 하나의 완성된 회의록으로 통합.

    3개 이상이면 cascade 방식으로 2개씩 순차 병합해 압축 손실을 최소화.
    """
    system = (
        "동일 회의/세미나/강의의 여러 파트 기록문서를 하나의 완성된 문서로 통합하세요.\n"
        "규칙:\n"
        "- 중복 헤더·날짜·메타정보만 제거하고, 발표 내용(본문)은 파트별로 최대한 유지\n"
        "- 각 파트의 소제목·소항목·수치·인용 발언·예시 하나도 생략하지 말 것\n"
        "- 시간 순서 유지; 파트 번호 레이블(파트1, 파트2 등)은 최종본에서 제거\n"
        "- Q&A는 모든 파트에서 수집하여 하나의 Q&A 섹션으로 합칠 것\n"
        "- 핵심 인사이트·검토 권고사항·참고 자료도 모든 파트에서 수집·통합\n"
        "- 구성은 표준 세미나/회의록/강의 노트 형식 유지\n"
        "- 요약·압축 금지: 원본에 있는 내용이면 반드시 포함"
    )

    # 3개 이상이면 2개씩 cascade merge로 압축 손실 최소화
    remaining = list(parts)
    while len(remaining) > 2:
        pair_combined = "\n\n---\n\n".join(
            f"## 파트 {i+1}\n{p}" for i, p in enumerate(remaining[:2])
        )
        merged = llm.chat(system, pair_combined, temp=0.2, model=MINUTES_MODEL, max_tokens=16000)
        remaining = [merged] + remaining[2:]

    combined = "\n\n---\n\n".join(
        f"## 파트 {i+1}/{len(remaining)}\n{p}" for i, p in enumerate(remaining)
    )
    return llm.chat(system, combined, temp=0.2, model=MINUTES_MODEL, max_tokens=16000)


# ──────────────────────────────────────────────
#  회의록 / 요약 생성
# ──────────────────────────────────────────────
def generate_minutes(
    segments_or_script,   # List[Dict] 또는 교정된 str 텍스트 모두 허용
    llm: LLMClient,
    doc_type: str = "meeting",
    memo: Optional[str] = None,
    debug_dir: Optional[str] = None,
    topic: str = "",
    session_dt: str = "",
    title: str = "",
) -> str:
    labels = TYPE_LABELS[doc_type]
    step(f"{labels['title']} 생성 중...")

    # str이면 교정된 스크립트 텍스트, List[Dict]이면 기존 segments 처리
    if isinstance(segments_or_script, str):
        script = segments_or_script
    else:
        segments = segments_or_script
        use_ts = has_timestamps(segments)
        if use_ts:
            script = "\n".join(
                f"[{ts(s['start'])}] {s.get('speaker', 'Speaker')}: {s['text']}"
                for s in segments
            )
        else:
            script = "\n".join(
                f"{s.get('speaker', 'Speaker')}: {s['text']}"
                for s in segments
            )
    logger.debug(f"[MINUTES] 스크립트 {len(script)}자, 타입={doc_type}")

    memo_block = ""
    if memo:
        memo_block = (
            "\n### 내부 참고 메모 (최종 출력 금지)\n"
            "⚠️ 중요: 아래 메모는 발표자/주제에 대한 사전 배경 자료이며, 실제 세미나/회의 발언이 아닙니다.\n"
            "규칙:\n"
            "- 회의록/세미나 기록의 '발표 내용'은 오직 아래 스크립트에서만 파악할 것\n"
            "- 메모/웹리서치에 있는 정보를 발표자가 언급한 것처럼 회의록에 쓰지 말 것\n"
            "- 메모는 전문용어 이해, 발표 맥락 파악, 참고 자료 확인에만 활용\n"
            "- 메모 제목·원문·검색 결과를 회의록에 그대로 출력하지 말 것\n\n"
            f"{memo}\n"
        )
    system = _get_minutes_prompt(doc_type, topic, session_dt, title)
    meta_lines = ""
    if title:      meta_lines += f"### 제목/발표자: {title}\n"
    if session_dt: meta_lines += f"### 녹음 일시: {session_dt}\n"
    if topic:      meta_lines += f"### 주제: {topic}\n"

    if debug_dir:
        debug_save(
            f"{meta_lines}{memo_block}\n### 스크립트:\n{script}",
            os.path.join(debug_dir, "minutes_prompt.txt"),
            "Minutes prompt",
        )

    # MAX_LLM_CHARS 초과 시 청크 분할 처리
    if len(script) > MAX_LLM_CHARS:
        warn(f"스크립트 {len(script):,}자 > {MAX_LLM_CHARS:,}자 → 청크 분할 처리")
        chunks = _split_script_chunks(script, MAX_LLM_CHARS)
        partials: List[str] = []
        for idx, chunk in enumerate(chunks):
            info(f"  청크 {idx+1}/{len(chunks)} ({len(chunk):,}자) 처리 중...")
            chunk_user = (
                f"{meta_lines}{memo_block}\n"
                f"### 스크립트 (파트 {idx+1}/{len(chunks)}):\n{chunk}"
            )
            partials.append(
                llm.chat(system, chunk_user, temp=0.3, model=MINUTES_MODEL, max_tokens=16000)
            )
        result = _merge_partial_minutes(partials, llm, doc_type) if len(partials) > 1 else partials[0]
    else:
        user = f"{meta_lines}{memo_block}\n### 스크립트:\n{script}"
        result = llm.chat(system, user, temp=0.3, model=MINUTES_MODEL, max_tokens=16000)

    if debug_dir:
        debug_save(result, os.path.join(debug_dir, "minutes_raw.md"), "Minutes raw")

    ok(f"{labels['title']} 생성 완료")
    return result


def refine_script(
    segments: List[Dict], llm: LLMClient,
    doc_type: str = "meeting",
    topic: str = "",
    debug_dir: Optional[str] = None,
) -> str:
    """STT 원문 스크립트를 전체 맥락과 주제를 참고하여 교정한 스크립트를 생성.
    오탈자·잘못 인식된 고유명사·전문용어를 수정하고 문장을 자연스럽게 다듬는다.
    """
    step("스크립트 교정 중...")

    use_ts = has_timestamps(segments)
    if use_ts:
        raw_script = "\n".join(
            f"[{ts(s['start'])}] {s.get('speaker', 'Speaker')}: {s['text']}"
            for s in segments
        )
    else:
        raw_script = "\n".join(
            f"{s.get('speaker', 'Speaker')}: {s['text']}"
            for s in segments
        )

    topic_line = f"주제: {topic}\n\n" if topic else ""
    type_hint = {"meeting": "회의", "seminar": "세미나/발표", "lecture": "강의"}.get(doc_type, "회의")

    system = (
        f"{topic_line}전문 {type_hint} 스크립트 교정 전문가입니다.\n"
        "STT(음성인식)로 생성된 원문 스크립트를 전체 맥락을 참고하여 교정하세요.\n\n"
        "교정 기준:\n"
        "- 잘못 인식된 고유명사, 인명, 제품명, 기술 용어를 맥락에 맞게 수정\n"
        "- 명백한 오탈자·음운 오류 수정 (예: '에이아이' → 'AI')\n"
        "- 문장이 어색하게 잘린 경우 자연스럽게 연결\n"
        "- 발화 습관(어, 음, 그, 뭐 등) 과도한 반복은 제거하되 발화 스타일은 유지\n"
        "- 타임스탬프·화자 레이블·전체 발화 순서는 절대 변경하지 말 것\n"
        "- 내용상 의미 변경 금지 — 교정이 불확실한 경우 원문 그대로 유지\n"
        "- 출력 형식은 입력과 동일하게 유지 (타임스탬프 있으면 그대로)"
    )
    user = f"다음 스크립트를 교정하세요:\n\n{raw_script}"

    if debug_dir:
        debug_save(user, os.path.join(debug_dir, "refine_prompt.txt"), "Refine prompt")

    result = llm.chat(system, user, temp=0.1)

    if debug_dir:
        debug_save(result, os.path.join(debug_dir, "refined_script.txt"), "Refined script")

    ok("스크립트 교정 완료")
    return result


def _refined_script_is_usable(refined: Optional[str], segments: List[Dict]) -> Tuple[bool, str]:
    """LLM 교정본이 원문 대부분을 잃었는지 방어적으로 검증한다."""
    text = (refined or "").strip()
    if not text:
        return False, "교정 결과가 비어 있음"

    raw_text = "\n".join(str(s.get("text", "")) for s in segments or [])
    raw_len = len(raw_text.strip())
    refined_len = len(text)
    if raw_len >= 800 and refined_len < max(500, int(raw_len * 0.45)):
        return False, f"교정 결과가 원문 대비 과도하게 짧음 ({refined_len}/{raw_len}자)"

    if has_timestamps(segments):
        expected = len([s for s in segments if s.get("text")])
        found = len(re.findall(r"\[\d{1,2}:\d{2}(?::\d{2})?\]", text))
        if expected >= 10 and found < max(5, int(expected * 0.35)):
            return False, f"타임스탬프 보존율 부족 ({found}/{expected})"

    bad_tail_phrases = ("추가적인 질문", "언제든지 말씀", "도움이 되었기를")
    if raw_len >= 800 and any(p in text[-300:] for p in bad_tail_phrases) and refined_len < raw_len * 0.7:
        return False, "교정 결과가 답변형 요약으로 보임"

    return True, ""


def extract_action_items(
    minutes: str, llm: LLMClient,
    doc_type: str = "meeting",
    debug_dir: Optional[str] = None,
) -> Optional[str]:
    """회의록에서 액션 아이템을 추출하여 JSON 문자열로 반환.
    meeting 타입만 지원. 항목이 없거나 추출 실패 시 None 반환.
    """
    if doc_type != "meeting":
        return None
    step("액션 아이템 추출 중...")

    system = (
        "당신은 회의록 분석 전문가입니다.\n"
        "회의록에서 Action Item(다음 할 일, 후속 조치, 결정된 사항)을 "
        "추출해 JSON 배열로만 반환하세요.\n\n"
        "담당자(assignee) 규칙:\n"
        "- 실명 언급 시 → 해당 이름 그대로 사용\n"
        "- 실명 없어도 조직/역할이 명확하면 → 예: '코롱측', '메가존', '주관사', '발표자'\n"
        "- 발화자 정보 없을 때도 문맥에서 추론: '우리가 다음 회의 전에 하기로 했어요' → 발화 측 조직\n"
        "- 어떤 조직/역할도 특정 불가능할 때만 → null (단, 실제로 결정된 사항이면 포함)\n\n"
        "기타 규칙:\n"
        "- 불확실한 제안이나 논의 중 사항은 제외, 합의/결정된 것만\n"
        "- deadline이 언급되지 않으면 null\n"
        "- 설명 없이 순수 JSON 배열만 출력 (코드블록 금지)\n\n"
        '출력 형식: [{"assignee":"담당자 또는 null","task":"업무 내용","deadline":"YYYY-MM-DD 또는 null","context":"맥락"}]'
    )
    user = f"다음 회의록에서 Action Item을 추출하세요:\n\n{minutes[:6000]}"

    if debug_dir:
        debug_save(user, os.path.join(debug_dir, "actions_prompt.txt"), "Actions prompt")

    raw = llm.chat(system, user, temp=0.1)

    if debug_dir:
        debug_save(raw, os.path.join(debug_dir, "actions_raw.json"), "Actions raw")

    from json_utils import parse_json_loose
    items = parse_json_loose(raw, expect="list", default=[])

    if not items:
        ok("액션 아이템 없음")
        return None

    ok(f"액션 아이템 {len(items)}개 추출")
    return json.dumps(items, ensure_ascii=False, indent=2)


def format_actions_md(actions_json: str) -> str:
    """JSON 액션 아이템을 마크다운 테이블로 변환."""
    try:
        items = json.loads(actions_json)
    except Exception:
        return actions_json
    if not items:
        return "*(액션 아이템 없음)*"
    lines = [
        "# 액션 아이템\n",
        "| 담당자 | 업무 | 마감일 | 맥락 |",
        "| --- | --- | --- | --- |",
    ]
    for item in items:
        lines.append(
            f"| {item.get('assignee') or '-'} "
            f"| {item.get('task') or '-'} "
            f"| {item.get('deadline') or '-'} "
            f"| {item.get('context') or '-'} |"
        )
    return "\n".join(lines)


def generate_summary(
    minutes: str, llm: LLMClient,
    doc_type: str = "meeting",
    debug_dir: Optional[str] = None,
    topic: str = "",
    session_dt: str = "",
) -> str:
    labels = TYPE_LABELS[doc_type]
    step("요약본 생성 중...")

    system = _get_summary_prompt(doc_type, topic, session_dt)
    meta_lines = ""
    if session_dt: meta_lines += f"일시: {session_dt}\n"
    if topic:      meta_lines += f"주제: {topic}\n"
    result = llm.chat(system,
                      f"{meta_lines}다음 {labels['title']}을 요약하세요:\n\n{minutes}",
                      temp=0.2, model=SUMMARY_MODEL, max_tokens=8000)
    if debug_dir:
        debug_save(result, os.path.join(debug_dir, "summary_raw.md"), "Summary raw")

    ok("요약본 생성 완료")
    return result


# ──────────────────────────────────────────────
#  파일 저장
# ──────────────────────────────────────────────
def save(content: str, path: str, label: str):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    ok(f"{label} → {path}")


# ──────────────────────────────────────────────
#  화자 이름 LLM 추론
# ──────────────────────────────────────────────
def infer_speaker_names(
    segments: List[Dict],
    llm: LLMClient,
    known_names: Optional[List[str]] = None,
) -> Dict[str, str]:
    """diarize 모델이 반환한 'Speaker A/B/C' 레이블을 발화 패턴으로 실명·역할 추론.

    Returns:
        {"Speaker A": "추론된 이름/역할", ...} — 추론 불가 시 빈 dict
    """
    unique_speakers = list({s.get("speaker", "") for s in segments if s.get("speaker")})
    if not unique_speakers:
        return {}
    # 알려진 참석자 명단이 없으면 추론하지 않음 — 화자 레이블(화자1/2…) 그대로 유지(지어내기 방지)
    if not known_names:
        return {}

    # 각 화자별 대표 발언 최대 5개 샘플링
    samples: Dict[str, List[str]] = {}
    for spk in unique_speakers:
        spk_texts = [s["text"] for s in segments if s.get("speaker") == spk][:5]
        if spk_texts:
            samples[spk] = spk_texts

    if not samples:
        return {}

    system = (
        "회의 발화 분석가입니다. 각 화자 레이블이 '알려진 참석자 명단' 중 누구인지만 판단하세요.\n"
        "규칙:\n"
        "- 자기소개·명시적 호명 등으로 명단 속 인물과 **확실히** 일치할 때만 그 실명으로 매핑.\n"
        "- 불확실하거나 명단에 없으면 해당 키를 **출력에서 생략**(화자 레이블 그대로 유지).\n"
        "- 이름·역할·직책·소속을 **추측하거나 지어내지 말 것.** 명단에 없는 새 이름 생성 절대 금지.\n"
        '출력: {"Speaker A": "명단속이름", ...} 형식의 순수 JSON만. 설명 금지.'
    )
    known_hint = f"\n알려진 참석자 명단(이 안의 이름만 사용): {', '.join(known_names)}"
    user = json.dumps(samples, ensure_ascii=False) + known_hint

    try:
        raw = llm.chat(system, user, temp=0.1)
        from json_utils import parse_json_loose
        mapping = parse_json_loose(raw, expect="dict", default={})
        if not mapping:
            return {}
        kn = {n.strip() for n in known_names}
        # 명단에 있는 이름으로 매핑된 것만 인정(그 외는 화자 레이블 유지 → 지어내기 차단)
        return {k: v.strip() for k, v in mapping.items()
                if v and isinstance(v, str) and v.strip() in kn}
    except Exception as e:
        logger.debug(f"[infer_speaker_names] 실패: {e}")
        return {}


# ──────────────────────────────────────────────
#  알림 발송
# ──────────────────────────────────────────────
def _send_notification(
    notify_type: str,
    title: str,
    summary_path: str,
    files: List[str],
    obsidian_path: str = "",
    doc_type: str = "meeting",
):
    try:
        from notifier import Notifier
    except ImportError:
        warn("notifier.py 없음 → 알림 건너뜀")
        return

    # config.json 이메일 설정 읽기
    email_cfg = {
        "sender":     _c("email.sender",    ""),
        "password":   _c("email.password",  ""),
        "recipients": [r.strip() for r in
                       _c("email.recipient", "").split(",") if r.strip()],
        "smtp_host":  _c("email.smtp_host", ""),
        "smtp_port":  int(_c("email.smtp_port", 0) or 0),
    }
    slack_cfg = {"webhook_url": os.environ.get("SLACK_WEBHOOK_URL", "") or _c("notify.slack.webhook_url", "")}
    teams_cfg = {"webhook_url": os.environ.get("TEAMS_WEBHOOK_URL", "") or _c("notify.teams.webhook_url", "")}

    notify_dict: Dict[str, dict] = {}
    if notify_type in ("email", "all") and email_cfg["sender"] and email_cfg["password"]:
        notify_dict["email"] = email_cfg
    if notify_type in ("slack", "all") and slack_cfg["webhook_url"]:
        notify_dict["slack"] = slack_cfg
    if notify_type in ("teams", "all") and teams_cfg["webhook_url"]:
        notify_dict["teams"] = teams_cfg

    if not notify_dict:
        warn(f"알림 설정 없음 ({notify_type}) → config.json email 섹션 또는 환경변수 확인")
        return

    notifier = Notifier.from_config({"notify": notify_dict})
    if notifier.has_channels:
        results = notifier.send(
            title=title, summary_path=summary_path, files=files,
            obsidian_path=obsidian_path, doc_type=doc_type,
        )
        for r in results:
            status = "완료" if r["success"] else f"실패: {r.get('error', '')}"
            print(f"  알림 ({r['channel']}): {status}")


def _dedupe_existing(paths: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for p in paths:
        if not p:
            continue
        try:
            rp = str(Path(p).resolve())
        except Exception:
            rp = p
        if rp in seen or not os.path.isfile(p):
            continue
        seen.add(rp)
        out.append(p)
    return out


def _collect_wiki_proposal_files(title: str, out_dir: str) -> List[str]:
    """현재 회의 제목과 맞는 wiki_proposal 파일을 output 루트/세션 폴더에서 수집."""
    candidates: List[Path] = []
    roots = [Path(out_dir)]
    try:
        roots.append(Path(__file__).resolve().parent.parent / str(_c("output_dir", "output")))
    except Exception:
        pass

    title_norm = _norm_resume_key(title)
    for root in roots:
        if not root.exists():
            continue
        for p in root.glob("*wiki_proposal.*"):
            name_norm = _norm_resume_key(p.name)
            if not title_norm or title_norm in name_norm or name_norm in title_norm:
                candidates.append(p)

    candidates = sorted(set(candidates), key=lambda p: p.stat().st_mtime, reverse=True)
    # md/json 한 쌍이면 충분하다. 오래된 동명이 산출물 전체 첨부를 피한다.
    return [str(p) for p in candidates[:2]]


def _collect_notification_artifacts(out_dir: str, pfx: str, title: str) -> List[str]:
    """메일/알림 첨부 기본 세트.

    상세/요약 외에 STT 원본, 교정본, segments, LLM/Wiki context/proposal/fact_check까지
    같이 전달해 메일만으로 검토 가능하게 한다.
    """
    d = Path(out_dir)
    paths: List[str] = []

    for name in (
        f"{pfx}minutes.md",
        f"{pfx}summary.md",
        f"{pfx}summary.txt",
        f"{pfx}actions.md",
        f"{pfx}actions.json",
        f"{pfx}script_refined.txt",
        f"{pfx}script.md",
        f"{pfx}transcript.md",
        f"{pfx}transcript.txt",
        f"{pfx}segments.json",
        f"{pfx}wiki_context.json",
        "wiki_context.json",
    ):
        paths.append(str(d / name))

    for pattern in (
        f"{pfx}*fact_check*.md",
        f"{pfx}*fact_check*.json",
        "*fact_check*.md",
        "*fact_check*.json",
    ):
        paths.extend(str(p) for p in d.glob(pattern))

    paths.extend(_collect_wiki_proposal_files(title, out_dir))
    return _dedupe_existing(paths)


# ──────────────────────────────────────────────
#  후처리: 용어 보완 + Obsidian 기록 (+ 옵션 이메일)
# ──────────────────────────────────────────────
def _gather_attendees(segments: List[Dict]) -> List[str]:
    """세그먼트 화자에서 실명/역할만 추출(‘Speaker A’ 류 제외)."""
    names: List[str] = []
    seen = set()
    for s in segments or []:
        spk = (s.get("speaker") or "").strip()
        if not spk or re.match(r'(?i)^speaker(?:[\s_]*[A-Za-z0-9]+)?$', spk):
            continue
        if spk.lower() not in seen:
            seen.add(spk.lower())
            names.append(spk)
    return names[:10]


def _attendee_candidates(segments: List[Dict], planned_match: Optional[Dict[str, Any]] = None) -> List[str]:
    names = _gather_attendees(segments)
    if planned_match:
        for nm in _clean_attendee_names((planned_match.get("meta") or {}).get("attendees")):
            if nm and nm not in names:
                names.append(nm)
    # 발화 중 "저는 OOO입니다" 형태의 자기소개를 보수적으로 수집
    for seg in segments or []:
        txt = str(seg.get("text", ""))
        for m in re.finditer(r"저는\s+([가-힣]{2,4})(?:이라고|입니다|교수|박사)", txt):
            nm = m.group(1).strip()
            if nm and nm not in names:
                names.append(nm)
        if len(names) >= 10:
            break
    return names[:10]


def _strip_fact_verification_sections(markdown: str) -> str:
    """LLM이 생성한 중복 사실 검증 섹션을 제거한다.

    Vault 기반 검증은 후처리에서 별도로 붙이므로, 본문에 이미 같은 제목이 있으면
    다음 2레벨 섹션 전까지 삭제해 결과가 중복되지 않게 한다.
    """
    if not markdown:
        return ""
    return re.sub(
        r"(?ms)^##\s*사실\s*검증\b.*?(?=^##\s+|\Z)",
        "",
        markdown,
    ).strip()


def _stt_quality_meta(
    segments: List[Dict],
    refined_text: Optional[str],
    used_refined: bool,
    source: str,
) -> Dict[str, Any]:
    raw_chars = sum(len(str(s.get("text", ""))) for s in segments or [])
    refined_chars = len(refined_text or "")
    return {
        "stt_segment_count": len(segments or []),
        "stt_raw_chars": raw_chars,
        "refined_chars": refined_chars,
        "refined_ratio": round(refined_chars / raw_chars, 3) if raw_chars else 0,
        "used_refined_script": used_refined,
        "stt_source": source,
    }


def _detect_meeting_scope(title: str = "", topic: str = "") -> str:
    """내부/외부 회의 구분을 보수적으로 추론한다."""
    text = f"{title} {topic}".lower()
    external_terms = (
        "외부", "고객", "클라이언트", "파트너", "협력사", "벤더", "후원",
        "제안", "계약", "mou", "po", "견적", "미팅",
    )
    internal_terms = (
        "내부", "팀회의", "주간보고", "데일리", "사내", "1on1", "원온원",
    )
    if any(term in text for term in external_terms):
        return "external"
    if any(term in text for term in internal_terms):
        return "internal"
    return "unknown"


_PLAN_UNSET = object()   # enrich_and_publish planned_match 미지정 센티넬


def _confirm_plan_merge(match: Dict[str, Any], title: str) -> bool:
    """계획 회의 매칭 시, 회의록을 그 노트에 '병합'할지 사용자에게 확인.
    - config obsidian.auto_merge=true 면 묻지 않고 병합
    - 비대화형(웹/워처 등 TTY 아님)에서는 절대 자동 병합하지 않음(원칙: 합병 전 확인)
    - 그 외에는 대화형 프롬프트. 기본값 Y(병합)."""
    if _c("obsidian.auto_merge", False):
        return True
    meta = match.get("meta") or {}
    is_tty = bool(getattr(sys, "stdin", None)) and sys.stdin.isatty()
    print(f"\n  ── 계획된 회의와 일치하는 노트를 찾았습니다 ──")
    print(f"     노트   : {match.get('path')}")
    print(f"     제목   : {meta.get('title','')}  (녹음 제목: {title})")
    print(f"     날짜   : {meta.get('date','')} {meta.get('time','')}".rstrip())
    if match.get("reason"):
        print(f"     매칭사유: {match.get('reason')}")
    att = meta.get("attendees")
    if att:
        print(f"     참석자 : {', '.join(att) if isinstance(att, list) else att}")
    if not is_tty:
        warn("비대화형 환경 → 자동 병합하지 않고 새 노트로 생성합니다 "
             "(나중에 직접 확인 후 병합하세요).")
        return False
    try:
        ans = input("  이 계획 노트에 회의록을 병합할까요? [Y=병합 / n=새 노트] : ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return ans in ("", "y", "yes", "ㅛ")


def enrich_and_publish(
    *,
    title: str,
    doc_type: str,
    minutes_md: str,
    llm: "LLMClient",
    summary_md: str = "",
    actions_md: str = "",
    topic: str = "",
    session_dt: str = "",
    attendees: Optional[List[str]] = None,
    related_notes_extra: Optional[List[str]] = None,
    notify: Optional[str] = None,
    email_summary_path: str = "",
    email_files: Optional[List[str]] = None,
    planned_match: Any = _PLAN_UNSET,
    source_audio: str = "",
    source_file_date: str = "",
    stt_meta: Optional[Dict[str, Any]] = None,
    transcript_md: str = "",
    evidence: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """배치/실시간/화자수정 경로가 공유하는 후처리:
       1) 용어·인물·기업 외부검색 보완(enrichment)
       2) Obsidian 볼트에 회의록 노트 기록(+참고노트 백링크)
       3) (옵션) 이메일 발송
    반환: {"glossary_md","obsidian_path","related_notes","sources"}
    """
    result: Dict[str, Any] = {"glossary_md": "", "obsidian_path": None,
                              "related_notes": [], "sources": []}
    meeting_scope = _detect_meeting_scope(title, topic)
    result["meeting_scope"] = meeting_scope

    # Obsidian 클라이언트 (설정 없거나 연결 실패 시 None → 볼트 기록만 생략)
    obs = None
    try:
        from obsidian import ObsidianClient
        obs = ObsidianClient.from_config()
        if obs is not None and not obs.ping():
            warn("Obsidian 연결 실패 → 볼트 기록 건너뜀")
            obs.close(); obs = None
    except Exception as e:
        logger.warning(f"[publish] Obsidian 초기화 실패: {e}")
        obs = None

    # 1) 용어 보완
    enr = {"glossary_md": "", "related_notes": [], "sources": []}
    try:
        import enrichment
        enr = enrichment.enrich(minutes_md, llm, obs=obs, topic=topic or title,
                                presenter_name=title)
        if related_notes_extra:
            merged_related: List[str] = []
            for rn in list(enr.get("related_notes", []) or []) + list(related_notes_extra or []):
                if rn and rn not in merged_related:
                    merged_related.append(rn)
            enr["related_notes"] = merged_related
        result.update(enr)
    except Exception as e:
        warn(f"용어 보완 실패: {e}")
        if related_notes_extra:
            result["related_notes"] = list(dict.fromkeys(related_notes_extra))

    # 2) Obsidian 노트 기록 — 계획(planned) 노트와 매칭되면 '확인 후 병합'
    if obs is not None:
        try:
            # 2-1) 계획 회의 매칭 — 호출자가 이미 찾았으면 재사용, 아니면 직접 탐색
            if planned_match is not _PLAN_UNSET:
                match = planned_match
            else:
                match = None
                try:
                    match = obs.find_planned_note(title, session_dt)
                except Exception as e:
                    logger.warning(f"[publish] 계획 노트 탐색 실패: {e}")
            result["planned_match"] = match.get("path") if match else None

            # 2-2) 매칭 시 병합 여부 확인(합병 전 사용자 확인 원칙)
            do_merge = _confirm_plan_merge(match, title) if match else False
            result["merged"] = do_merge

            if match and do_merge:
                path = obs.update_planned_note(
                    match, title=title, body_md=minutes_md, doc_type=doc_type,
                    topic=topic, attendees=attendees or [], session_dt=session_dt,
                    glossary_md=enr.get("glossary_md", ""),
                    related_notes=result.get("related_notes", enr.get("related_notes", [])),
                    external_refs=enr.get("sources", []),
                    summary_md=summary_md, actions_md=actions_md,
                    meeting_scope=meeting_scope,
                    web_sources_md=enr.get("web_sources_md", ""),
                    source_audio=source_audio,
                    source_file_date=source_file_date,
                    processed_at=datetime.now().isoformat(timespec="seconds"),
                    stt_meta=stt_meta,
                    transcript_md=transcript_md,
                )
                result["obsidian_path"] = path
                if path:
                    ok(f"계획 회의에 병합 → {path}  (status: planned → done)")
            else:
                if match:
                    info(f"계획 회의 매칭됨(병합 보류): {match.get('path')} → 새 노트로 생성")
                path = obs.write_meeting_note(
                    title=title, body_md=minutes_md, doc_type=doc_type,
                    topic=topic, attendees=attendees or [], session_dt=session_dt,
                    glossary_md=enr.get("glossary_md", ""),
                    related_notes=result.get("related_notes", enr.get("related_notes", [])),
                    external_refs=enr.get("sources", []),
                    summary_md=summary_md, actions_md=actions_md,
                    meeting_scope=meeting_scope,
                    web_sources_md=enr.get("web_sources_md", ""),
                    source_audio=source_audio,
                    source_file_date=source_file_date,
                    processed_at=datetime.now().isoformat(timespec="seconds"),
                    stt_meta=stt_meta,
                    transcript_md=transcript_md,
                    evidence=evidence,
                    # 매칭됐지만 병합 보류 → 계획 경로 기록(대시보드 '병합 대기' 표시용)
                    extra_meta=({"matched_plan": match["path"]} if match else None),
                )
                result["obsidian_path"] = path
                if path:
                    ok(f"Obsidian 노트 기록 → {path}")
                    if match:
                        ok(f"→ 계획 '{match['path']}' 와(과) 매칭됨. 확인 후 병합하려면 Cowork에서 요청하세요.")
        except Exception as e:
            warn(f"Obsidian 노트 기록 실패: {e}")
        finally:
            obs.close()

    # 3) 이메일(옵션) — 배치는 main 루프가 일괄 발송하므로 보통 None.
    #    실시간 경로는 .md 파일이 없으므로(DB 저장), 회의록을 임시파일로 만들어 본문/첨부에 실어 보냄.
    if notify:
        tmp_dir = None
        try:
            summary_path = email_summary_path
            files = list(email_files or [])
            # Obsidian 노트 경로가 있으면 자동 첨부
            # obsidian_path는 vault 상대경로(예: "Inbox/note.md") → 풀 경로로 변환
            _obs_note = result.get("obsidian_path")
            if _obs_note:
                _vault_root = _c("obsidian.vault_path", "") or ""
                if not _vault_root:
                    try:
                        from obsidian import _detect_obsidian_config as _dOC
                        _vault_root = _dOC().get("vault_path", "")
                    except Exception:
                        pass
                _obs_full = (
                    os.path.join(_vault_root, str(_obs_note))
                    if _vault_root else str(_obs_note)
                )
                if os.path.isfile(_obs_full) and _obs_full not in files:
                    files.append(_obs_full)
            if not summary_path and (summary_md or minutes_md):
                body = minutes_md or summary_md
                glossary = result.get("glossary_md", "")
                if glossary and glossary not in body:
                    body = f"{body}\n\n## 용어·배경\n\n{glossary}\n"
                tmp_dir = tempfile.mkdtemp(prefix="mtg_mail_")
                safe = re.sub(r'[\\/:*?"<>|]', "_", title)[:40].strip() or "회의록"
                tmp_path = os.path.join(tmp_dir, f"{safe}_회의록.md")
                with open(tmp_path, "w", encoding="utf-8") as f:
                    f.write(body)
                summary_path = tmp_path
                files.append(tmp_path)
            _send_notification(notify, title, summary_path or "", files,
                              doc_type=doc_type)
        except Exception as e:
            warn(f"이메일 발송 실패: {e}")
        finally:
            if tmp_dir:
                shutil.rmtree(tmp_dir, ignore_errors=True)

    return result


# ──────────────────────────────────────────────
#  단일 파일 처리 파이프라인
# ──────────────────────────────────────────────
def _lookup_plan(title: str, session_dt: str):
    """계획(planned) 노트를 1회만 탐색해 match dict(or None) 반환. Obsidian 미연결시 None."""
    try:
        from obsidian import ObsidianClient
        obs = ObsidianClient.from_config()
        if obs is None or not obs.ping():
            if obs:
                obs.close()
            return None
        try:
            return obs.find_planned_note(title, session_dt)
        finally:
            obs.close()
    except Exception as e:
        logger.warning(f"[plan] 계획 노트 탐색 실패: {e}")
        return None


def _plan_context_text(match) -> str:
    """match 본문에서 회의록 정리에 참고할 '사전 자료'(병합 전 부분)를 추출.
    자동 리서치 내용은 참고용으로 유지하고 마커 주석만 제거한다."""
    if not match:
        return ""
    body = match.get("body") or ""
    cut = re.split(r"^##\s+회의 기록", body, maxsplit=1, flags=re.MULTILINE)[0]
    try:
        import plan_research
        cut = cut.replace(plan_research.MARKER_BEGIN, "").replace(plan_research.MARKER_END, "")
    except Exception:
        pass
    return cut.strip()


def _clean_attendee_names(attendees):
    """['최민석(팀장)','정하윤 수석','심아름 책임(나)'] → ['최민석','정하윤','심아름'] (화자 힌트용 이름만)."""
    out = []
    for a in (attendees or []):
        nm = re.sub(r"\(.*?\)", "", str(a)).strip()                 # 괄호 직책/메모 제거
        nm = re.sub(r"\s+(팀장|수석|책임|주임|선임|대표|이사|부장|과장|차장|사원|연구원|매니저)$", "", nm).strip()
        if nm and nm not in out:
            out.append(nm)
    return out


def plan_context_memo(title, session_dt, base_memo=None, match=_PLAN_UNSET):
    """[모든 진입점 공용] 계획 매칭(+사전 자료)을 회의록 생성용 memo 에 주입.
    match 를 넘기면 재탐색하지 않고 재사용한다. 반환: (match_or_None, memo_or_None)."""
    if match is _PLAN_UNSET:
        match = _lookup_plan(title, session_dt)
    ctx = _plan_context_text(match)
    directives = []
    if match:
        names = _clean_attendee_names((match.get("meta") or {}).get("attendees"))
        if names:
            directives.append(
                "[참석자 참고 명단] 아래는 계획상 참석 예정자입니다. 화자가 이 중 누구인지 "
                "분명한 경우에만 그 실명으로 표기하세요('발언자 A/B'보다 우선). 확실하지 않으면 "
                "억지로 맞추지 말고, 명단에 없어도 실제 발언자가 있으면 들은 대로 두세요: "
                + ", ".join(names))
    if ctx:
        directives.append(
            "[회의 전 사전 자료 \u2014 맥락 참고용. 실제 회의에서 다뤄진 경우에만 반영하고, "
            "다뤄지지 않은 항목을 억지로 넣지 말 것]:\n" + ctx)
    memo = base_memo or ""
    if directives:
        memo = ("\n\n".join(directives) + ("\n\n" + memo if memo else "")).strip()
    return match, (memo or None)


def process_single(
    input_path: str,
    args,
    llm: LLMClient,
    output_dir: str,
    title: str,
    work_dir: str,
    file_prefix: str = "",
    memo: Optional[str] = None,
    debug_dir: Optional[str] = None,
) -> Tuple[str, Optional[str]]:
    """
    단일 파일 처리 파이프라인.
    Returns: summary 텍스트 (알림 본문용)
    """
    labels = TYPE_LABELS[args.type]
    pfx    = file_prefix
    seg_path = os.path.join(output_dir, f"{pfx}segments.json")
    transcript_path = os.path.join(output_dir, f"{pfx}transcript.md")
    force_stt = bool(getattr(args, "force_stt", False))
    stt_source = "new_stt"

    # ── 기존 STT 결과 재사용 ──
    if not force_stt and os.path.isfile(seg_path):
        info(f"기존 세그먼트 로드 (--resume): {seg_path}")
        with open(seg_path, "r", encoding="utf-8") as f:
            segments = json.load(f)
        stt_source = "segments_json"
    elif not force_stt and os.path.isfile(transcript_path):
        info(f"기존 전사 로드 (--resume): {transcript_path}")
        segments = load_segments_from_transcript(transcript_path)
        with open(seg_path, "w", encoding="utf-8") as f:
            json.dump(segments, f, ensure_ascii=False, indent=2)
        info(f"전사에서 세그먼트 복원 → {seg_path}")
        stt_source = "transcript_md"
    elif getattr(args, "resume", False):
        raise RuntimeError(
            "--resume 지정됨: 기존 segments.json/transcript.md를 찾지 못해 STT를 중단합니다. "
            "기존 출력 폴더 제목을 --title로 지정하거나 --force-stt로 새 STT를 명시하세요."
        )
    else:
        # 1. 오디오 준비
        audio_path = prepare_audio(input_path, work_dir)

        # 2. STT
        speaker_names = (
            [n.strip() for n in args.speakers.split(",") if n.strip()]
            if getattr(args, "speakers", None) else None
        )
        segments = run_stt(
            audio_path, model=args.model,
            language=getattr(args, "language", None),
            speaker_names=speaker_names,
            work_dir=work_dir, debug_dir=debug_dir,
        )
        if not segments:
            raise RuntimeError(f"STT 결과 비어있음: {input_path}")

        with open(seg_path, "w", encoding="utf-8") as f:
            json.dump(segments, f, ensure_ascii=False, indent=2)
        info(f"세그먼트 → {seg_path}")

    # 3. 화자 매핑 재사용 (--reuse-speakers)
    if getattr(args, "reuse_speakers", False):
        try:
            from speaker_cache import SpeakerCache
            cache = SpeakerCache(
                os.path.join(os.path.dirname(output_dir), "speaker_map.json")
            )
            cached_key = cache.fuzzy_match(title)
            if cached_key:
                mapping = cache.get_mapping(cached_key)
                if mapping:
                    info(f"화자 매핑 재사용: [{cached_key}]")
                    for seg in segments:
                        orig = seg.get("speaker", "")
                        if orig in mapping:
                            seg["speaker"] = mapping[orig]
        except ImportError:
            pass

    # 3b. 화자 이름 LLM 추론 (diarize 모델 사용 시 'Speaker A' → 실명/역할)
    # 계획 매칭 1회 탐색 — 화자 추론(참석자 힌트)·사전자료·발행에 공통 사용
    session_dt = getattr(args, 'session_dt', '') or parse_session_dt_from_filename(input_path)
    _plan_match = None
    try:
        _plan_match = _lookup_plan(title, session_dt)
    except Exception as _e:
        logger.warning(f"[plan] 계획 노트 탐색 실패: {_e}")

    unique_spks = {s.get("speaker", "") for s in segments if s.get("speaker")}
    has_generic_labels = any(
        re.match(r'[Ss]peaker[\s_]?[A-Za-z0-9]', spk) for spk in unique_spks
    )
    if has_generic_labels:
        known_names_arg = ([n.strip() for n in args.speakers.split(",") if n.strip()]
                           if getattr(args, "speakers", None) else [])
        if _plan_match:  # 계획 노트 참석자(직책 제거)를 화자 추론 힌트로 자동 주입
            for nm in _clean_attendee_names((_plan_match.get("meta") or {}).get("attendees")):
                if nm not in known_names_arg:
                    known_names_arg.append(nm)
        if known_names_arg:
            info(f"화자 추론 힌트(참석자): {', '.join(known_names_arg)}")
        try:
            inferred = infer_speaker_names(segments, llm, known_names=known_names_arg or None)
            if inferred:
                info(f"화자 추론 결과: {inferred}")
                for seg in segments:
                    orig = seg.get("speaker", "")
                    if orig in inferred:
                        seg["speaker"] = inferred[orig]
        except Exception as e:
            warn(f"화자 이름 추론 실패 ({e}) → 원본 레이블 유지")

    # 4. 번역
    segments_for_doc = segments
    if getattr(args, "translate", False):
        seg_ko_path = os.path.join(output_dir, f"{pfx}segments_translated.json")
        if getattr(args, "resume", False) and os.path.isfile(seg_ko_path):
            info("기존 번역 세그먼트 로드 (--resume)")
            with open(seg_ko_path, "r", encoding="utf-8") as f:
                segments_for_doc = json.load(f)
        else:
            segments_for_doc = translate_segments(segments, llm, debug_dir=debug_dir)
            with open(seg_ko_path, "w", encoding="utf-8") as f:
                json.dump(segments_for_doc, f, ensure_ascii=False, indent=2)

    # 5. 스크립트 (원본 raw 보존)
    script_md = build_script_md(segments)
    save(script_md, os.path.join(output_dir, f"{pfx}script.md"), "스크립트")
    save(script_md, transcript_path, "전사")

    if getattr(args, "translate", False) and getattr(args, "translate_script", False):
        script_ko = build_script_md(segments_for_doc, include_original=True)
        save(script_ko, os.path.join(output_dir, f"{pfx}script_ko.md"), "스크립트 (한국어)")

    # 5b. STT 교정 — 회의록 생성 전에 실행하여 교정본을 입력으로 사용
    topic_str = getattr(args, 'topic', '') or ""
    refined_text: Optional[str] = None
    try:
        refined_text = refine_script(
            segments_for_doc, llm, args.type,
            topic=topic_str, debug_dir=debug_dir,
        )
        usable, reason = _refined_script_is_usable(refined_text, segments_for_doc)
        if usable:
            save(refined_text,
                 os.path.join(output_dir, f"{pfx}script_refined.txt"), "교정 스크립트")
        else:
            rejected = (
                "[REJECTED] 교정 결과가 회의록 입력 품질 기준을 통과하지 못해 원본 STT를 사용합니다.\n"
                f"사유: {reason}\n\n"
                "---- LLM 교정 결과 ----\n"
                f"{refined_text or ''}"
            )
            save(rejected,
                 os.path.join(output_dir, f"{pfx}script_refined.txt"), "교정 스크립트(미사용)")
            warn(f"교정본 품질 검증 실패 → 원본 STT로 회의록 생성 ({reason})")
            refined_text = None
    except Exception as e:
        warn(f"STT 교정 실패 ({e}) → 원본 스크립트로 회의록 생성")

    # 6. 회의록 — 교정본 우선, 실패 시 원본 segments 사용
    full_memo = memo or ""
    if getattr(args, "custom_prompt", None):
        full_memo = (full_memo + f"\n\n[추가 지시]: {args.custom_prompt}").strip()

    # 6-0. [공용] 사전 자료 주입 (계획 매칭은 위에서 1회 탐색해 화자 추론에도 사용)
    related_note_titles: List[str] = []
    context_flags: Dict[str, Any] = {}
    import meeting_workflow as mw
    try:
        _, full_memo = plan_context_memo(title, session_dt, full_memo, match=_plan_match)
        if _plan_match:
            info(f"계획 회의 매칭: {_plan_match.get('path')} (사유: {_plan_match.get('reason','')})")
    except Exception as e:
        warn(f"계획 매칭/사전자료 주입 실패: {e}")

    # 6-1. Obsidian Wiki/온라인 배경 컨텍스트 주입.
    # 실패해도 회의록 생성 자체는 계속 진행한다.
    try:
        full_memo, related_note_titles, context_flags = mw.build_generation_context_memo(
            llm=llm,
            title=title,
            topic=topic_str,
            segments_or_text=segments_for_doc,
            base_memo=full_memo,
        )
        if context_flags.get("wiki"):
            info(f"Obsidian Wiki 컨텍스트 주입: {len(related_note_titles)}개 노트")
        if context_flags.get("web"):
            info("웹 리서치 컨텍스트 주입")
    except Exception as e:
        warn(f"Obsidian Wiki 컨텍스트 주입 실패: {e}")

    # Wiki Context Package 저장 (wiki_context.json)
    try:
        from wiki_knowledge import build_wiki_context_package, save_wiki_context_package
        entities_for_context: List[str] = []
        _ctx_pkg = build_wiki_context_package(
            related_titles=related_note_titles,
            data_dir=Path(__file__).resolve().parent.parent / "data",
            metadata={
                "title": title,
                "session_dt": session_dt,
                "session_date": _date_key_local(session_dt),
                "source_file": Path(input_path).name,
                "source_file_date": _date_key_local(parse_session_dt_from_filename(input_path)),
                "doc_type": args.type,
                "stt_source": stt_source,
            },
            filter_query=" ".join([title, topic_str, segments_to_plain_text(segments_for_doc)[:1000]]),
            known_entities=entities_for_context,
            related_details=context_flags.get("evidence", []),
        )
        save_wiki_context_package(_ctx_pkg, Path(output_dir))
    except Exception as _cpe:
        warn(f"wiki_context.json 저장 실패 (무시): {_cpe}")

    minutes = generate_minutes(
        refined_text if refined_text else segments_for_doc,
        llm, args.type,
        full_memo or None, debug_dir,
        topic=topic_str,
        session_dt=session_dt,
        title=title,
    )

    verify_md = ""
    claim_results: List[Dict[str, Any]] = []
    if _c("wiki.claim_verify", False):
        obs_for_verify = None
        try:
            import meeting_workflow as mw
            info("사실 검증 중 (vault 비교)...")
            indexer_for_verify = mw.load_vault_indexer()
            obs_for_verify = mw.load_obsidian_client()
            verify_md, claim_results = mw.claim_verify(
                minutes,
                llm,
                indexer=indexer_for_verify,
                obs=obs_for_verify,
                topic=topic_str,
                max_claims=int(_c("wiki.claim_verify_max", 8) or 8),
                current_title=title,
            )
            if verify_md:
                conflicts = verify_md.count("- ⚠️")
                matches = verify_md.count("- ✅")
                unknowns = verify_md.count("- ❓") + verify_md.count("- 🔍")
                info(f"사실 검증 완료: 충돌 {conflicts}, 일치 {matches}, 확인불가 {unknowns}")
                minutes = _strip_fact_verification_sections(minutes).rstrip() + "\n\n" + verify_md
            else:
                warn("사실 검증 결과 없음: 검증 가능한 주장을 추출하지 못했습니다")
        except Exception as e:
            warn(f"사실 검증 실패 (무시): {e}")
        finally:
            if obs_for_verify:
                try:
                    obs_for_verify.close()
                except Exception:
                    pass

    header = (
        f"<!-- Generated: {datetime.now().isoformat()} -->\n"
        f"<!-- Source: {Path(input_path).name} | Type: {args.type} | "
        f"STT: {args.model} | LLM: {args.llm} -->\n\n"
    )
    save(header + minutes,
         os.path.join(output_dir, f"{pfx}minutes.md"), labels["title"])

    # 7. 요약
    summary = generate_summary(minutes, llm, args.type, debug_dir,
                                topic=topic_str, session_dt=session_dt)
    save(summary, os.path.join(output_dir, f"{pfx}summary.md"), "요약본")

    # 8. 액션 아이템 추출 (meeting 전용)
    actions_json = extract_action_items(minutes, llm, args.type, debug_dir)
    if actions_json:
        save(actions_json,
             os.path.join(output_dir, f"{pfx}actions.json"), "액션 아이템 (JSON)")
        save(format_actions_md(actions_json),
             os.path.join(output_dir, f"{pfx}actions.md"), "액션 아이템 (마크다운)")

    # 9. 후처리: 용어 보완 + Obsidian 기록 (이메일은 main 루프가 일괄 발송)
    _obs_path: Optional[str] = None
    enr: Dict[str, Any] = {"entities": {}, "glossary_md": "", "related_notes": [], "sources": []}
    try:
        actions_md = format_actions_md(actions_json) if actions_json else ""
        attendees_for_doc = _attendee_candidates(segments_for_doc, _plan_match)
        stt_meta = _stt_quality_meta(
            segments_for_doc,
            refined_text,
            bool(refined_text),
            stt_source,
        )
        enr = enrich_and_publish(
            title=title, doc_type=args.type, minutes_md=minutes, llm=llm,
            summary_md=summary, actions_md=actions_md,
            topic=topic_str, session_dt=session_dt,
            attendees=attendees_for_doc,
            related_notes_extra=related_note_titles,
            planned_match=_plan_match,   # 1회 탐색 결과 재사용(중복 탐색 방지)
            source_audio=input_path,
            source_file_date=_date_key_local(parse_session_dt_from_filename(input_path)),
            stt_meta=stt_meta,
            transcript_md=script_md,
            evidence=mw.evidence_to_wikilinks(context_flags.get("evidence", [])),
        )
        _obs_path = enr.get("obsidian_path") or None
        # 로컬 minutes.md 에도 용어·배경 + 웹 검색 추가 자료 append
        glossary = enr.get("glossary_md", "")
        web_sources = enr.get("web_sources_md", "")
        with open(os.path.join(output_dir, f"{pfx}minutes.md"),
                  "a", encoding="utf-8") as f:
            if glossary:
                f.write(f"\n\n## 용어·배경\n\n{glossary}\n")
            if web_sources:
                f.write(f"\n\n{web_sources}\n")
    except Exception as e:
        warn(f"후처리(용어/Obsidian) 실패 → 본문은 정상 저장됨: {e}")

    # Wiki Context Package 최종 저장: enrichment 엔티티와 정제된 registry 반영
    try:
        from wiki_knowledge import build_wiki_context_package, save_wiki_context_package
        entity_map = enr.get("entities") or {}
        known_entities = []
        for vals in entity_map.values():
            known_entities.extend(vals if isinstance(vals, list) else [])
        glossary_terms = list(entity_map.get("terms", []) or [])
        _ctx_pkg = build_wiki_context_package(
            related_titles=related_note_titles,
            data_dir=Path(__file__).resolve().parent.parent / "data",
            metadata={
                "title": title,
                "session_dt": session_dt,
                "session_date": _date_key_local(session_dt),
                "source_file": Path(input_path).name,
                "source_file_date": _date_key_local(parse_session_dt_from_filename(input_path)),
                "doc_type": args.type,
                "stt_source": stt_source,
                **_stt_quality_meta(segments_for_doc, refined_text, bool(refined_text), stt_source),
            },
            known_entities=known_entities,
            glossary_terms=glossary_terms,
            filter_query=" ".join([title, topic_str, minutes[:1500]]),
            related_details=context_flags.get("evidence", []),
        )
        save_wiki_context_package(_ctx_pkg, Path(output_dir))
    except Exception as _cpe:
        warn(f"wiki_context.json 최종 저장 실패 (무시): {_cpe}")

    # 10. Wiki Registry 갱신 (실패해도 회의록 결과에 영향 없음)
    if args.type == "meeting":
        try:
            from wiki_knowledge import (
                update_action_registry_from_actions,
                update_decision_registry_from_minutes,
                extract_decisions_from_minutes,
            )
            obs_note = _obs_path or ""
            if actions_json:
                update_action_registry_from_actions(
                    actions_json,
                    source_meeting=title,
                    source_note=obs_note,
                )
            decisions = extract_decisions_from_minutes(minutes)
            if decisions:
                update_decision_registry_from_minutes(
                    decisions,
                    source_meeting=title,
                    source_note=obs_note,
                )
        except Exception as _wke:
            warn(f"Wiki Registry 갱신 실패 (무시): {_wke}")

        # ── Wiki Update Proposal ──
        if related_note_titles:
            try:
                from wiki_knowledge import (
                    build_wiki_update_proposal,
                    save_wiki_update_proposal,
                )
                _proposal = build_wiki_update_proposal(
                    meeting_title=title,
                    minutes_text=minutes,
                    related_titles=related_note_titles,
                    llm=llm,
                    claim_results=claim_results,
                )
                import config_loader as _cfg
                _root_out = Path(__file__).resolve().parent.parent / str(_cfg.get("output_dir", "output"))
                save_wiki_update_proposal(_proposal, _root_out)
            except Exception as _wpe:
                warn(f"Wiki Update Proposal 생성 실패 (무시): {_wpe}")

    return summary, _obs_path


# ──────────────────────────────────────────────
#  Main
# ──────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Meeting/Seminar/Lecture Minutes Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python run_meeting.py batch meeting.mp4
  python run_meeting.py batch seminar.webm --type seminar --translate
  python run_meeting.py batch *.webm --title "Q1 세미나" --type seminar
  python run_meeting.py batch meeting.mp4 --profile weekly_team
  python run_meeting.py batch meeting.mp4 --edit-speakers
  python run_meeting.py batch meeting.mp4 --resume
  python run_meeting.py batch meeting.mp4 --notify email
  python run_meeting.py batch meeting.mp4 --debug
  python run_meeting.py batch meeting.mp4 --estimate-cost
  python run_meeting.py batch meeting.mp4 --ssl-no-verify

프로필 관리:
  python run_meeting.py profiles list
  python run_meeting.py profiles create

화자 캐시:
  python run_meeting.py speaker-cache list

폴더 감시:
  python run_meeting.py legacy-watcher ./recordings --profile weekly
""",
    )
    parser.add_argument("input", nargs="+",
                        help="음성/영상 파일 경로 (glob 지원, 예: *.webm)")
    parser.add_argument("--title",
                        help="출력 폴더 제목 (다중 파일 시 하나의 폴더로 묶음)")
    parser.add_argument("--type", default="meeting",
                        choices=["meeting", "seminar", "lecture"],
                        help="문서 유형 (기본: meeting)")
    parser.add_argument("--model", default=DEFAULT_STT_MODEL,
                        choices=["gpt-4o-transcribe-diarize", "gpt-4o-transcribe",
                                 "gpt-4o-mini-transcribe",
                                 "gpt-4o-mini-transcribe-2025-12-15", "whisper-1"],
                        help=f"STT 모델 (기본: {DEFAULT_STT_MODEL})")
    parser.add_argument("--llm", default=_c("models.llm", "gpt"), choices=["gpt", "claude"],
                        help="회의록 생성 LLM")
    parser.add_argument("--language", default="ko", choices=["ko", "en"],
                        help="STT 언어 (ko=한국어, en=영어)")
    parser.add_argument("--translate", action="store_true",
                        help="영→한 번역 후 문서 작성")
    parser.add_argument("--translate-script", action="store_true",
                        help="스크립트 한국어 번역본도 생성")
    parser.add_argument("--speakers",
                        help="화자 이름 쉼표 구분 (최대 4명, diarize 모델 전용)")
    parser.add_argument("--memo", help="메모 파일 경로 (회의록에 반영)")
    parser.add_argument("--topic",
                        help="회의 주제/맥락 (관련 노트 검색과 회의록 생성에 반영)")
    parser.add_argument("--custom-prompt",
                        help="LLM 추가 지시 (예: 'AI 용어 원문 병기')")
    parser.add_argument("--profile",
                        help="Named Profile 적용 (profiles.py 로 관리)")
    parser.add_argument("--reuse-speakers", action="store_true",
                        help="이전 화자 매핑 자동 재사용")
    parser.add_argument("--edit-speakers", action="store_true",
                        help="기존 결과의 화자명 수정 후 회의록 재생성")
    parser.add_argument("--resume", action="store_true",
                        help="이전 실행 이어서 (STT 건너뜀)")
    parser.add_argument("--force-stt", action="store_true",
                        help="기존 STT/전사 결과가 있어도 새로 STT 수행")
    parser.add_argument("--estimate-cost", action="store_true",
                        help="비용 추정만 수행 (실제 처리 안 함)")
    parser.add_argument("--notify", choices=["email", "slack", "teams"],
                        help="완료 알림 채널")
    parser.add_argument("--no-notify", action="store_true",
                        help="config notify.on_finish 자동 알림까지 포함해 이번 실행에서는 알림을 보내지 않음")
    parser.add_argument("--output-dir", default=_c("output_dir", "./output"),
                        help="출력 디렉토리 (기본: ./output)")
    parser.add_argument("--debug", action="store_true",
                        help="상세 로그 + 중간 파일 저장")
    parser.add_argument("--ssl-no-verify", action="store_true",
                        help="SSL 인증서 검증 비활성화 (회사/학교 네트워크 문제 시)")

    args = parser.parse_args()
    if args.resume and args.force_stt:
        parser.error("--resume 과 --force-stt 는 함께 사용할 수 없습니다.")

    # ── SSL ──────────────────────────────────────────────
    global SSL_VERIFY
    if args.ssl_no_verify:
        SSL_VERIFY = False

    # ── 프로필 적용 ──────────────────────────────────────
    if args.profile:
        try:
            from profiles import ProfileManager
            pm   = ProfileManager()
            args = pm.apply_profile(args.profile, args)
            print(f"  프로필 [{args.profile}] 적용됨")
        except ImportError:
            warn("profiles.py 없음 → 프로필 무시")
        except Exception as e:
            err(f"프로필 오류: {e}")
            sys.exit(1)

    # ── 완료 알림 기본값 (config.json notify.on_finish) ──
    # --notify(및 프로필) 미지정 시 config의 notify.on_finish(email/slack/teams) 자동 적용
    if args.no_notify:
        args.notify = None
    elif not getattr(args, "notify", None):
        args.notify = _c("notify.on_finish", None) or None

    # ── 로깅 ─────────────────────────────────────────────
    setup_logging(args.debug, args.output_dir)

    # ── 입력 파일 수집 ────────────────────────────────────
    input_files: List[str] = []
    for pattern in args.input:
        expanded = glob.glob(pattern)
        if expanded:
            input_files.extend(expanded)
        elif os.path.isfile(pattern):
            input_files.append(pattern)
        else:
            err(f"파일 없음: {pattern}")

    valid_files: List[str] = []
    for f in input_files:
        ext = Path(f).suffix.lower()
        if ext in ALL_SUPPORTED:
            valid_files.append(f)
        else:
            warn(f"미지원 포맷 건너뜀: {f} ({ext})")

    if not valid_files:
        if not input_files:
            parser.print_help()
        else:
            err("처리할 파일이 없습니다.")
        sys.exit(1)

    # ── 비용 추정 (처리 불필요) ───────────────────────────
    if args.estimate_cost:
        est = estimate_cost(valid_files, args.model, args.translate, args.llm)
        print_cost_estimate(est)
        return

    # ── 사전 검증 ─────────────────────────────────────────
    if not check_ffmpeg():
        err("ffmpeg 미설치. https://ffmpeg.org")
        sys.exit(1)
    if not get_api_key("OPENAI_API_KEY", OPENAI_API_KEY):
        err("OpenAI API 키 없음.\n  → config.json api.openai_api_key 또는 환경변수 OPENAI_API_KEY")
        sys.exit(1)
    if args.memo and not os.path.isfile(args.memo):
        err(f"메모 파일 없음: {args.memo}")
        sys.exit(1)

    labels = TYPE_LABELS[args.type]
    multi  = len(valid_files) > 1

    # ── 디버그 폴더 ───────────────────────────────────────
    debug_dir = None
    if args.debug:
        debug_dir = os.path.join(args.output_dir, "debug")
        os.makedirs(debug_dir, exist_ok=True)
        logger.debug(f"Python: {sys.version}")
        logger.debug(f"Args: {vars(args)}")

    # ── 헤더 출력 ─────────────────────────────────────────
    print(f"\n{'#'*60}")
    print(f"  {labels['emoji']}  {labels['title']} Generator")
    print(f"  입력:  {len(valid_files)}개 파일")
    print(f"  타입:  {args.type}")
    print(f"  STT:   {args.model}")
    print(f"  LLM:   {args.llm} (+ 자동 폴백)")
    print(f"  번역:  {'ON' if args.translate else 'OFF'}")
    if args.profile:    print(f"  프로필: {args.profile}")
    if args.notify:     print(f"  알림:  {args.notify}")
    if not SSL_VERIFY:  print(f"  SSL 검증 OFF")
    if args.debug:      print(f"  DEBUG ON → {debug_dir}")
    print(f"  출력:  {os.path.abspath(args.output_dir)}")
    print(f"{'#'*60}")

    # ── 메모 로드 ──────────────────────────────────────────
    memo: Optional[str] = None
    if args.memo:
        memo = read_file(args.memo)
        info(f"메모 로드 ({len(memo)}자)")

    llm            = LLMClient(preferred=args.llm)
    pipeline_start = time.time()
    success        = 0
    fail           = 0
    processed: List[Tuple[str, str, str, Optional[str]]] = []  # (filepath, out_dir, summary, obs_path)

    work_dir = tempfile.mkdtemp(prefix="mm_")
    try:
        # ── 화자 수정 모드 ──────────────────────────────────
        if args.edit_speakers:
            if len(valid_files) != 1:
                err("--edit-speakers 는 파일 하나만 지원합니다.")
                sys.exit(1)
            fp    = valid_files[0]
            title = args.title or Path(fp).stem
            found = find_existing_output_dir(args.output_dir, title)
            if not found:
                err(f"기존 출력 폴더를 찾을 수 없습니다 (제목: {title})")
                err(f"  먼저 일반 실행으로 STT를 수행하세요.")
                sys.exit(1)
            out_dir = found

            seg_files = list(Path(out_dir).glob("*segments.json"))
            if not seg_files:
                err(f"세그먼트 파일 없음: {out_dir}")
                sys.exit(1)
            seg_path = str(seg_files[0])

            with open(seg_path, "r", encoding="utf-8") as f:
                segments = json.load(f)

            # 화자 캐시 통합 수정
            speaker_mapping: Dict[str, str] = {}
            try:
                from speaker_cache import SpeakerCache
                cache          = SpeakerCache(
                    os.path.join(args.output_dir, "speaker_map.json")
                )
                speaker_mapping = cache.interactive_edit(segments, title=title)
            except ImportError:
                # speaker_cache.py 없으면 기본 대화형
                speakers = sorted({s.get("speaker", "") for s in segments
                                   if s.get("speaker")})
                for spk in speakers:
                    new_name = input(f"  {spk} → 새 이름 (Enter=유지): ").strip()
                    if new_name:
                        speaker_mapping[spk] = new_name

            if speaker_mapping:
                for seg in segments:
                    orig = seg.get("speaker", "")
                    if orig in speaker_mapping:
                        seg["speaker"] = speaker_mapping[orig]
                with open(seg_path, "w", encoding="utf-8") as f:
                    json.dump(segments, f, ensure_ascii=False, indent=2)

            step("화자 수정 후 문서 재생성")
            stem     = Path(seg_path).stem.replace("_segments", "")
            _edit_session_dt = parse_session_dt_from_filename(fp)
            minutes  = generate_minutes(segments, llm, args.type, memo, debug_dir,
                                        title=title, session_dt=_edit_session_dt)
            header   = (f"<!-- Regenerated: {datetime.now().isoformat()} -->\n"
                        f"<!-- Source: {Path(fp).name} | Speakers edited -->\n\n")
            save(header + minutes,
                 os.path.join(out_dir, f"{stem}_minutes.md"), labels["title"])
            summary = generate_summary(minutes, llm, args.type, debug_dir)
            save(summary, os.path.join(out_dir, f"{stem}_summary.md"), "요약본")
            ok("화자 수정 및 재생성 완료!")

            # 후처리: 용어 보완 + Obsidian 기록
            _enr_edit = {}
            try:
                _enr_edit = enrich_and_publish(
                    title=title, doc_type=args.type, minutes_md=minutes, llm=llm,
                    summary_md=summary,
                    topic=getattr(args, "topic", "") or "",
                    attendees=_gather_attendees(segments),
                )
            except Exception as e:
                warn(f"후처리(용어/Obsidian) 실패: {e}")

            if args.notify:
                _edit_attach = _collect_notification_artifacts(out_dir, f"{stem}_", title)
                _send_notification(
                    args.notify, title,
                    os.path.join(out_dir, f"{stem}_summary.md"),
                    _edit_attach,
                    doc_type=args.type,
                )
            return

        # ── 일반 처리 파이프라인 ─────────────────────────────
        if multi and args.title:
            # 모든 파일 → 하나의 출력 폴더
            out_dir = make_output_dir(args.output_dir, args.title)
            for i, fp in enumerate(valid_files):
                pfx = f"{i+1:02d}_{Path(fp).stem}_"
                step(f"[{i+1}/{len(valid_files)}] {Path(fp).name}")
                try:
                    summary, obs_path = process_single(
                        fp, args, llm, out_dir, args.title, work_dir, pfx, memo, debug_dir
                    )
                    success += 1
                    processed.append((fp, out_dir, summary, obs_path))
                except Exception as e:
                    err(f"{Path(fp).name}: {type(e).__name__}: {e}")
                    if args.debug:
                        logger.debug(traceback.format_exc())
                    fail += 1
        else:
            # 파일마다 개별 출력 폴더
            for fp in valid_files:
                title = args.title or Path(fp).stem
                if getattr(args, "force_stt", False):
                    out_dir = make_output_dir(args.output_dir, title)
                else:
                    found = find_existing_output_dir(args.output_dir, title)
                    if found:
                        out_dir = found
                        info(f"기존 STT/전사 결과 재사용 폴더: {out_dir}")
                    elif args.resume:
                        err(f"{Path(fp).name}: --resume 지정됨, 기존 STT/전사 결과 없음 (제목: {title})")
                        fail += 1
                        continue
                    else:
                        out_dir = make_output_dir(args.output_dir, title)
                if multi:
                    step(f"[{valid_files.index(fp)+1}/{len(valid_files)}] {Path(fp).name}")
                try:
                    summary, obs_path = process_single(
                        fp, args, llm, out_dir, title, work_dir, "", memo, debug_dir
                    )
                    success += 1
                    processed.append((fp, out_dir, summary, obs_path))
                except Exception as e:
                    err(f"{Path(fp).name}: {type(e).__name__}: {e}")
                    err_str = str(e)
                    if "SSL" in err_str or "CERTIFICATE" in err_str:
                        print("  SSL 문제: --ssl-no-verify 또는 config.json ssl.verify: false")
                    if args.debug:
                        logger.debug(traceback.format_exc())
                    else:
                        print("  --debug 로 재실행하면 상세 로그 확인 가능")
                    fail += 1

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    # ── 알림 발송 ──────────────────────────────────────────
    if args.notify and processed:
        for fp, out_dir, summary, obs_path in processed:
            title    = args.title or Path(fp).stem
            stem     = Path(fp).stem
            pfx      = ""
            if multi and args.title:
                pfx = f"{processed.index((fp, out_dir, summary, obs_path))+1:02d}_{stem}_"
            summary_path = os.path.join(out_dir, f"{pfx}summary.md")
            minutes_path = os.path.join(out_dir, f"{pfx}minutes.md")
            # 교정 전사본 우선, 없으면 원본 스크립트 폴백
            script_refined_path = os.path.join(out_dir, f"{pfx}script_refined.txt")
            script_raw_path     = os.path.join(out_dir, f"{pfx}script.md")
            print(f"\n  알림 발송 중 → {args.notify} ...")
            attach_files = _collect_notification_artifacts(out_dir, pfx, title)
            _send_notification(
                args.notify, title, summary_path,
                attach_files,
                obsidian_path=obs_path or "",
                doc_type=args.type,
            )

    # ── 완료 출력 ─────────────────────────────────────────
    total_time = time.time() - pipeline_start
    print(f"\n{'#'*60}")
    print(f"  완료!  ({total_time:.1f}초)")
    if multi:
        print(f"  성공: {success}개  |  실패: {fail}개")
    print(f"  {llm.stats()}")

    for fp, out_dir, _, _obs in processed:
        out_files = sorted(p for p in Path(out_dir).glob("*")
                           if p.is_file() and p.suffix in (".md", ".txt", ".json"))
        if out_files:
            print(f"\n  출력 폴더: {out_dir}/")
            for fp2 in out_files:
                print(f"    {fp2.name:<48s} {file_mb(str(fp2)):.2f} MB")

    if debug_dir and os.path.isdir(debug_dir):
        debug_files = sorted(Path(debug_dir).glob("*"))
        if debug_files:
            print(f"\n  디버그 파일 ({len(debug_files)}개): {debug_dir}/")

    if fail:
        print(f"\n  실패 파일은 --resume 으로 이어서 처리 가능")
    print(f"{'#'*60}\n")

    sys.exit(0 if not fail else 1)


if __name__ == "__main__":
    main()
