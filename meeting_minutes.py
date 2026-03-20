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
   python meeting_minutes.py meeting.mp4
   python meeting_minutes.py seminar.webm --type seminar --translate
   python meeting_minutes.py *.webm --title "Q1 세미나" --notify email
   python meeting_minutes.py meeting.mp4 --profile weekly_team
   python meeting_minutes.py meeting.mp4 --debug
   python meeting_minutes.py meeting.mp4 --estimate-cost
   python meeting_minutes.py meeting.mp4 --edit-speakers
   python meeting_minutes.py meeting.mp4 --ssl-no-verify
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
FALLBACK_STT_MODEL = "gpt-4o-transcribe"
GPT_MODEL          = _c("models.gpt_model",     "gpt-4o") or "gpt-4o"
MINUTES_MODEL      = _c("models.minutes_model", "gpt-4o") or "gpt-4o"
SUMMARY_MODEL      = _c("models.summary_model", "gpt-4o") or "gpt-4o"
CLAUDE_MODEL       = _c("models.claude_model", "claude-opus-4-6") or "claude-opus-4-6"
OPENAI_API_KEY     = _c("api.openai_api_key",    "") or ""
ANTHROPIC_API_KEY  = _c("api.anthropic_api_key", "") or ""
SSL_VERIFY         = _c("ssl.verify", False)

MAX_FILE_SIZE_MB = 25

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
      realtime_20260303_145540  → "2026년 03월 03일 14:55"
      meeting_20260303          → "2026년 03월 03일"
      20260303_145540_whatever  → "2026년 03월 03일 14:55"
    """
    stem = Path(filename).stem
    # 패턴 1: YYYYMMDD_HHMMSS (또는 YYYYMMDD-HHMMSS)
    m = re.search(r'(\d{4})(\d{2})(\d{2})[_\-](\d{2})(\d{2})(\d{2})', stem)
    if m:
        y, mo, d, h, mi, _ = m.groups()
        return f"{y}년 {mo}월 {d}일 {h}:{mi}"
    # 패턴 2: YYYYMMDD 만
    m = re.search(r'(\d{4})(\d{2})(\d{2})', stem)
    if m:
        y, mo, d = m.groups()
        return f"{y}년 {mo}월 {d}일"
    return ""


def make_output_dir(base_dir: str, title: str) -> str:
    """출력 디렉토리 생성: {base_dir}/{날짜}_{제목}/"""
    date_str = datetime.now().strftime("%Y-%m-%d")
    folder   = sanitize_filename(f"{date_str}_{title}")
    out_dir  = os.path.join(base_dir, folder)
    os.makedirs(out_dir, exist_ok=True)
    return out_dir


def find_existing_output_dir(base_dir: str, title: str) -> Optional[str]:
    """--resume / --edit-speakers 용: 가장 최근의 기존 출력 폴더 반환."""
    safe = sanitize_filename(title)
    if not os.path.isdir(base_dir):
        return None
    candidates = []
    for d in sorted(Path(base_dir).iterdir(), reverse=True):
        if d.is_dir() and safe in d.name:
            if any(d.glob("*segments.json")) or any(d.glob("segments.json")):
                candidates.append(str(d))
    return candidates[0] if candidates else None


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
        raise RuntimeError(
            "모든 LLM API 호출 실패.\n"
            "  → API 키를 확인하세요.\n"
            "  → SSL 에러라면: --ssl-no-verify 또는 config.json ssl.verify: false"
        )

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


def split_audio(audio_path: str, work_dir: str) -> List[Tuple[str, float]]:
    """25MB 초과 시 청크 분할."""
    size = file_mb(audio_path)
    dur  = audio_duration(audio_path)
    logger.debug(f"오디오: {size:.2f}MB, {dur:.1f}s ({ts(dur)})")

    if size <= MAX_FILE_SIZE_MB:
        return [(audio_path, 0.0)]

    info(f"파일 {size:.1f}MB > {MAX_FILE_SIZE_MB}MB → 분할")
    n         = math.ceil(size / (MAX_FILE_SIZE_MB * 0.85))
    chunk_dur = dur / n
    stem      = Path(audio_path).stem
    chunks    = []

    for i in range(n):
        offset = i * chunk_dur
        cp     = os.path.join(work_dir, f"{stem}_chunk{i:03d}.mp3")
        run_cmd([
            "ffmpeg", "-y", "-i", audio_path,
            "-ss", str(offset), "-t", str(chunk_dur),
            "-ar", "16000", "-ac", "1", "-b:a", "48k", cp,
        ])
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
            params["response_format"] = "json"

        if language:
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

    for i, (cp, chunk_offset) in enumerate(chunks):
        if len(chunks) > 1:
            info(f"  청크 {i+1}/{len(chunks)} 처리 중...")

        t0 = time.time()
        try:
            segs = transcribe_chunk(
                client, cp, model, language, speaker_names,
                chunk_offset, debug_dir, i,
            )
            all_segments.extend(segs)
        except Exception as e:
            logger.error(f"[STT FAIL] chunk {i}: {type(e).__name__}: {e}")
            if DEBUG:
                logger.debug(traceback.format_exc())
            if model != FALLBACK_STT_MODEL:
                warn(f"  {model} 실패 ({e})")
                warn(f"  → {FALLBACK_STT_MODEL} 로 폴백")
                segs = transcribe_chunk(
                    client, cp, FALLBACK_STT_MODEL, language, None,
                    chunk_offset, debug_dir, i,
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
            arr  = json.loads(re.search(r"\[[\s\S]*\]", raw).group())
            tmap = {a["i"]: a["t"] for a in arr}
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
5. 화자별 발언 귀속(attribution)은 하지 않음 — 조직명·역할 단위로 맥락상 필요할 때만 언급
6. 메모(추가 메모)가 있으면 논의 내용과 적극 연결하여 반영
7. 전문적·격식 문체, 한국어

## 출력 형식 (이 구조를 정확히 따를 것)

## YYMMDD [회의 주제] 회의록

- **일시**: YYYY.MM.DD(요일) HH:MM ~
- **장소**: (언급된 경우 기재, 없으면 항목 생략)
- **참석자**
    - [조직/팀명]: 이름1, 이름2(역할)
    - [조직/팀명]: 이름3, 이름4
- **안건**
    1. 안건 제목
    2. 안건 제목

---

### 주요 논의 내용

### A. [첫 번째 안건 제목]

- **소주제/논점**
    - 세부 내용 (핵심 수치·사실은 **굵게**)
    - 세부 내용
- **소주제/논점**
    - 세부 내용
    - 개선안: 구체적 방안

### B. [두 번째 안건 제목]

- **소주제/논점**
    - 세부 내용

(안건 수만큼 반복)

---

### 결정 사항(합의/정리된 방향)

1. **결정 요약**: 구체적 내용
2. **결정 요약**: 구체적 내용

---

### Action Item (담당/기한)

- **[담당 조직/팀명]**
    - 구체적 업무 내용 (기한 있으면 명시)
    - 구체적 업무 내용

- **[담당 조직/팀명]**
    - 구체적 업무 내용

## 세부 작성 규칙

### 제목
- `## YYMMDD` 형식 (예: 260305), 뒤에 회의 주제와 "회의록"
- 주제는 스크립트 도입부·메모·topic 메타정보에서 추론

### 참석자
- 스크립트의 자기소개·호칭·맥락에서 조직/팀 소속을 추론하여 그룹핑
- 조직을 추론할 수 없으면 "참석자: 발언자 A, B, C" 형식으로 단순 나열
- 이름을 알 수 없으면 "발언자 A" 등 사용

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
1. 발표에서 다룬 모든 주제·개념·수치·사례를 누락 없이 반영
2. 개별 발언을 시간순으로 나열하지 말고, **섹션/주제별로 종합·정리** (타임스탬프 표기 금지)
3. 기술 용어·수치·고유명사·제품명은 원문 그대로 표기
4. 핵심 개념·수치·결론은 **굵게** 강조
5. 발표자의 중요 문구는 직접 인용("")으로 표기
6. 메모(추가 메모)가 있으면 해당 섹션과 적극 연결하여 반영
7. 전문적 문체, 한국어

## 출력 형식 (이 구조를 정확히 따를 것)

## YYMMDD [세미나 주제] 세미나 기록

- **일시**: YYYY.MM.DD(요일) HH:MM ~
- **장소**: (언급된 경우 기재, 없으면 항목 생략)
- **발표자**: 이름 (역할/소속)
- **참석자**: (파악 가능한 경우 기재)
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
    - 질문 내용 및 발표자 답변 요약
- **질문 주제**
    - 질문 내용 및 답변

(질문이 없었으면 섹션 생략)

---

### 핵심 인사이트

- 실무에 즉시 적용 가능한 포인트 (발표자가 강조한 내용 중심)
- 주요 시사점

---

### 참고 자료

- 언급된 도구·링크·논문·제품명 (원문 표기)

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
- 각 섹션의 소주제마다 구체적 세부 내용·근거·수치를 충분히 포함할 것
- 섹션 하나를 1~2줄로 축약하는 것은 금지 — 소주제별로 세부 불릿을 충실히 작성
- 60분 발표 기준 최소 A4 2~3쪽 이상 분량이 되어야 함"""

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
회의에 참석하지 않은 임원이 이 요약본 하나만으로 회의 전체를 완전히 파악할 수 있어야 합니다.

【출력 형식】

• 일시: (회의록에 명시된 값 사용)
• 장소: (언급된 경우, 없으면 생략)
• 참석자: (역할·소속 포함, 확인 불가 시 "발언자 A/B/C" 형식)
• 주요 논의 항목: (번호 목록)

────────────────────────────────────────
배경 (논의 맥락 이해에 필요한 경우만)
• 관련 배경·경위 정리 (불필요 시 섹션 생략)

[논의 항목 1 제목]
• 핵심 논의 내용 및 결론
  ○ 주요 발언·주장 및 그 근거 (수치·사례 포함)
  ○ 반론 또는 대안 의견이 있었다면 포함
  ○ 최종 결론 또는 미결 여부 명시 ("미결:" 접두어 사용)

[논의 항목 2 제목]
• …

────────────────────────────────────────
결정 사항 (명확히 확정된 것만)
• 결정 내용 — 결정 근거·배경 포함

────────────────────────────────────────
R&R / 역할 분담 (해당 시)
[주체]
• 담당 역할 및 범위

────────────────────────────────────────
To-do / 후속 조치
• **[담당자]** 구체적 업무 내용 (기한 명시, 없으면 "미정")
  ○ 업무의 목적·맥락

【작성 원칙】
- 각 논의 항목은 "무엇이 논의됐고, 어떤 근거가 제시됐으며, 결론이 무엇인가"를 모두 포함
- 단순 나열보다 인과관계·근거가 드러나는 서술 선호
- 수치·일정·고유명사·제품명은 원문 그대로 유지
- 결정되지 않은 사항은 "미결:" 접두어로 명확히 표시
- 각 섹션은 ──── 선으로 구분
- 압축은 허용하되 근거와 맥락을 제거하는 압축은 금지"""

_SUMMARY_SEMINAR = """\
{prefix}세미나 요약 전문가입니다.
참석하지 않은 동료가 이 요약본 하나만으로 발표 전체를 완전히 파악할 수 있어야 합니다.

【출력 형식】

• 일시: / 장소: / 발표자: (기록에 명시된 값 사용)
• 주제 한줄 요약
• 주요 섹션: (번호 목록)

────────────────────────────────────────
배경 / 개요
• 세미나 목적·맥락 (발표자가 설명한 배경 포함)

[섹션 1 제목]
• 핵심 주장 및 내용 요약
  ○ 데이터·수치·예시 (원문 그대로)
  ○ 핵심 개념 설명 (발표자 표현 기준)
  ○ 실무적 시사점 (발표자가 강조한 경우)

[섹션 2 제목]
• …

────────────────────────────────────────
Q&A 핵심 요약
• 주요 질문과 발표자 답변 요약

────────────────────────────────────────
실무 적용 포인트
• 바로 활용 가능한 인사이트 (발표자가 명시적으로 강조한 것 중심)

────────────────────────────────────────
후속 학습 자료
• 발표에서 언급된 도구·논문·링크·제품

【작성 원칙】
- 각 섹션은 "무엇이 발표됐고, 어떤 근거가 제시됐으며, 실무에 어떻게 적용되는가"를 포함
- 수치·고유명사·제품명은 원문 그대로 유지
- 압축은 허용하되 핵심 근거와 예시를 제거하는 압축은 금지"""

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


def _get_minutes_prompt(doc_type: str, topic: str = "", session_dt: str = "") -> str:
    tmpl = _MINUTES_TEMPLATES.get(doc_type, "")
    if not tmpl:
        return ""
    prefix = ""
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
    """복수 파트의 회의록을 하나의 완성된 회의록으로 통합."""
    combined = "\n\n---\n\n".join(
        f"## 파트 {i+1}/{len(parts)}\n{p}" for i, p in enumerate(parts)
    )
    system = (
        "동일 회의/세미나/강의의 여러 파트 기록문서를 하나의 완성된 문서로 통합하세요.\n"
        "규칙:\n"
        "- 중복 내용은 제거하되 어느 파트에서도 누락되지 않도록 할 것\n"
        "- 시간 순서 유지\n"
        "- 구성은 표준 회의록/세미나/강의 노트 형식 유지\n"
        "- 통합 후에도 세부 내용·수치·발언은 생략하지 말 것"
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

    memo_block = f"\n### 추가 메모 (반드시 반영):\n{memo}\n" if memo else ""
    system = _get_minutes_prompt(doc_type, topic, session_dt)
    meta_lines = ""
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
        "회의록에서 Action Item(다음 할 일, 후속 조치, 담당자가 있는 결정 사항)을 "
        "추출해 JSON 배열로만 반환하세요.\n\n"
        "규칙:\n"
        "- 담당자가 명시되거나 문맥상 명확한 항목만 포함\n"
        "- 불확실한 제안이나 논의 중인 사항은 제외\n"
        "- deadline이 언급되지 않으면 null 사용\n"
        "- 설명 없이 순수 JSON 배열만 출력 (코드블록 금지)\n\n"
        '출력 형식: [{"assignee":"담당자","task":"업무 내용","deadline":"YYYY-MM-DD 또는 null","context":"맥락"}]'
    )
    user = f"다음 회의록에서 Action Item을 추출하세요:\n\n{minutes[:6000]}"

    if debug_dir:
        debug_save(user, os.path.join(debug_dir, "actions_prompt.txt"), "Actions prompt")

    raw = llm.chat(system, user, temp=0.1)

    if debug_dir:
        debug_save(raw, os.path.join(debug_dir, "actions_raw.json"), "Actions raw")

    try:
        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-z]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text.strip())
        items = json.loads(text)
        if not isinstance(items, list):
            items = []
    except json.JSONDecodeError:
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        items = []
        if m:
            try:
                items = json.loads(m.group())
            except json.JSONDecodeError:
                pass

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

    # 각 화자별 대표 발언 최대 5개 샘플링
    samples: Dict[str, List[str]] = {}
    for spk in unique_speakers:
        spk_texts = [s["text"] for s in segments if s.get("speaker") == spk][:5]
        if spk_texts:
            samples[spk] = spk_texts

    if not samples:
        return {}

    system = (
        "회의 발화 분석 전문가입니다.\n"
        "각 화자 레이블에 대한 대표 발언을 분석하여 가능한 실명·역할·직책을 추론하세요.\n"
        "힌트: 호칭(님, 씨, 대리, 팀장, 선생님 등), 자기소개 문구, 발화 스타일을 활용.\n"
        '출력 형식: {"Speaker A": "추론된 이름 또는 역할", ...}\n'
        "추론 불가 시 해당 키를 출력에서 생략. 설명 없이 순수 JSON만."
    )
    known_hint = f"\n알려진 이름 힌트: {', '.join(known_names)}" if known_names else ""
    user = json.dumps(samples, ensure_ascii=False) + known_hint

    try:
        raw = llm.chat(system, user, temp=0.1)
        m = re.search(r'\{[\s\S]*\}', raw)
        if not m:
            return {}
        mapping = json.loads(m.group())
        return {k: v for k, v in mapping.items() if v and isinstance(v, str)}
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
        results = notifier.send(title=title, summary_path=summary_path, files=files)
        for r in results:
            status = "완료" if r["success"] else f"실패: {r.get('error', '')}"
            print(f"  알림 ({r['channel']}): {status}")


# ──────────────────────────────────────────────
#  단일 파일 처리 파이프라인
# ──────────────────────────────────────────────
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
) -> str:
    """
    단일 파일 처리 파이프라인.
    Returns: summary 텍스트 (알림 본문용)
    """
    labels = TYPE_LABELS[args.type]
    pfx    = file_prefix
    seg_path = os.path.join(output_dir, f"{pfx}segments.json")

    # ── Resume: 기존 STT 결과 재사용 ──
    if getattr(args, "resume", False) and os.path.isfile(seg_path):
        info(f"기존 세그먼트 로드 (--resume): {seg_path}")
        with open(seg_path, "r", encoding="utf-8") as f:
            segments = json.load(f)
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
    unique_spks = {s.get("speaker", "") for s in segments if s.get("speaker")}
    has_generic_labels = any(
        re.match(r'[Ss]peaker[\s_]?[A-Za-z0-9]', spk) for spk in unique_spks
    )
    if has_generic_labels:
        known_names_arg = (
            [n.strip() for n in args.speakers.split(",") if n.strip()]
            if getattr(args, "speakers", None) else None
        )
        try:
            inferred = infer_speaker_names(segments, llm, known_names=known_names_arg)
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
        save(refined_text,
             os.path.join(output_dir, f"{pfx}script_refined.txt"), "교정 스크립트")
    except Exception as e:
        warn(f"STT 교정 실패 ({e}) → 원본 스크립트로 회의록 생성")

    # 6. 회의록 — 교정본 우선, 실패 시 원본 segments 사용
    full_memo = memo or ""
    if getattr(args, "custom_prompt", None):
        full_memo = (full_memo + f"\n\n[추가 지시]: {args.custom_prompt}").strip()

    # 날짜 자동 파싱 (CLI에서 지정하지 않은 경우 파일명에서 추출)
    session_dt = getattr(args, 'session_dt', '') or \
                 parse_session_dt_from_filename(input_path)

    minutes = generate_minutes(
        refined_text if refined_text else segments_for_doc,
        llm, args.type,
        full_memo or None, debug_dir,
        topic=topic_str,
        session_dt=session_dt,
    )
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

    return summary


# ──────────────────────────────────────────────
#  Main
# ──────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Meeting/Seminar/Lecture Minutes Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python meeting_minutes.py meeting.mp4
  python meeting_minutes.py seminar.webm --type seminar --translate
  python meeting_minutes.py *.webm --title "Q1 세미나" --type seminar
  python meeting_minutes.py meeting.mp4 --profile weekly_team
  python meeting_minutes.py meeting.mp4 --edit-speakers
  python meeting_minutes.py meeting.mp4 --resume
  python meeting_minutes.py meeting.mp4 --notify email
  python meeting_minutes.py meeting.mp4 --debug
  python meeting_minutes.py meeting.mp4 --estimate-cost
  python meeting_minutes.py meeting.mp4 --ssl-no-verify

프로필 관리:
  python profiles.py list
  python profiles.py create

화자 캐시:
  python speaker_cache.py list

폴더 감시:
  python watcher.py ./recordings --profile weekly
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
    parser.add_argument("--estimate-cost", action="store_true",
                        help="비용 추정만 수행 (실제 처리 안 함)")
    parser.add_argument("--notify", choices=["email", "slack", "teams"],
                        help="완료 알림 채널")
    parser.add_argument("--output-dir", default=_c("output_dir", "./output"),
                        help="출력 디렉토리 (기본: ./output)")
    parser.add_argument("--debug", action="store_true",
                        help="상세 로그 + 중간 파일 저장")
    parser.add_argument("--ssl-no-verify", action="store_true",
                        help="SSL 인증서 검증 비활성화 (회사/학교 네트워크 문제 시)")

    args = parser.parse_args()

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
    processed: List[Tuple[str, str, str]] = []  # (filepath, out_dir, summary)

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
            minutes  = generate_minutes(segments, llm, args.type, memo, debug_dir)
            header   = (f"<!-- Regenerated: {datetime.now().isoformat()} -->\n"
                        f"<!-- Source: {Path(fp).name} | Speakers edited -->\n\n")
            save(header + minutes,
                 os.path.join(out_dir, f"{stem}_minutes.md"), labels["title"])
            summary = generate_summary(minutes, llm, args.type, debug_dir)
            save(summary, os.path.join(out_dir, f"{stem}_summary.md"), "요약본")
            ok("화자 수정 및 재생성 완료!")

            if args.notify:
                _send_notification(
                    args.notify, title,
                    os.path.join(out_dir, f"{stem}_summary.md"),
                    [os.path.join(out_dir, f"{stem}_minutes.md"),
                     os.path.join(out_dir, f"{stem}_summary.md")],
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
                    summary = process_single(
                        fp, args, llm, out_dir, args.title, work_dir, pfx, memo, debug_dir
                    )
                    success += 1
                    processed.append((fp, out_dir, summary))
                except Exception as e:
                    err(f"{Path(fp).name}: {type(e).__name__}: {e}")
                    if args.debug:
                        logger.debug(traceback.format_exc())
                    fail += 1
        else:
            # 파일마다 개별 출력 폴더
            for fp in valid_files:
                title = args.title or Path(fp).stem
                if args.resume:
                    found   = find_existing_output_dir(args.output_dir, title)
                    out_dir = found or make_output_dir(args.output_dir, title)
                else:
                    out_dir = make_output_dir(args.output_dir, title)
                if multi:
                    step(f"[{valid_files.index(fp)+1}/{len(valid_files)}] {Path(fp).name}")
                try:
                    summary = process_single(
                        fp, args, llm, out_dir, title, work_dir, "", memo, debug_dir
                    )
                    success += 1
                    processed.append((fp, out_dir, summary))
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
        for fp, out_dir, summary in processed:
            title    = args.title or Path(fp).stem
            stem     = Path(fp).stem
            pfx      = ""
            if multi and args.title:
                pfx = f"{processed.index((fp, out_dir, summary))+1:02d}_{stem}_"
            summary_path = os.path.join(out_dir, f"{pfx}summary.md")
            minutes_path = os.path.join(out_dir, f"{pfx}minutes.md")
            script_path  = os.path.join(out_dir, f"{pfx}script.md")
            print(f"\n  알림 발송 중 → {args.notify} ...")
            attach_files = [p for p in [minutes_path, summary_path, script_path]
                            if os.path.isfile(p)]
            _send_notification(
                args.notify, title, summary_path,
                attach_files,
            )

    # ── 완료 출력 ─────────────────────────────────────────
    total_time = time.time() - pipeline_start
    print(f"\n{'#'*60}")
    print(f"  완료!  ({total_time:.1f}초)")
    if multi:
        print(f"  성공: {success}개  |  실패: {fail}개")
    print(f"  {llm.stats()}")

    for fp, out_dir, _ in processed:
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
