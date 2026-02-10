#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
============================================================
 Meeting / Seminar / Lecture Minutes Generator
 (회의록·세미나·강의 기록 자동 생성기)
============================================================

 ◆ 주요 기능
   - 음성/영상 파일 → STT → 스크립트 + 기록문서 + 요약본 자동 생성
   - 다중 파일 배치 처리
   - 영어→한국어 번역
   - 화자 분리 (diarize 모델)
   - 화자 사후 수정 → 재생성
   - GPT-4o ↔ Claude 자동 폴백
   - 실패 시 이어서 처리 (--resume)
   - 비용 사전 추정 (--estimate-cost)
   - 항상 로그 파일 기록

 ◆ 설치
   pip install -r requirements.txt
   ffmpeg 필요 (https://ffmpeg.org)

 ◆ 사용법
   # 기본
   python meeting_minutes.py meeting.mp4

   # 제목 지정
   python meeting_minutes.py meeting.mp4 --title "2025 Q1 정기회의"

   # 다중 파일
   python meeting_minutes.py file1.mp4 file2.webm file3.mp3
   python meeting_minutes.py *.webm --type seminar --translate

   # 세미나 / 강의
   python meeting_minutes.py seminar.webm --type seminar
   python meeting_minutes.py lecture.mp4 --type lecture

   # 영어→한국어
   python meeting_minutes.py talk.mp4 --translate --translate-script

   # 비용 추정만
   python meeting_minutes.py big_file.mp4 --estimate-cost

   # 이전 실행 이어서 (STT 건너뜀)
   python meeting_minutes.py meeting.mp4 --resume

   # 화자 수정
   python meeting_minutes.py meeting.mp4 --edit-speakers

   # SSL 문제 (회사/학교 망)
   python meeting_minutes.py meeting.mp4 --ssl-no-verify

   # 설정 파일 사용
   python meeting_minutes.py meeting.mp4 --config config.json
============================================================
"""

import os
import sys
import json
import argparse
import subprocess
import tempfile
import math
import re
import time
import traceback
import logging
import warnings
import glob
import ssl
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Tuple, Any

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

# ──────────────────────────────────────────────
#  Configuration
# ──────────────────────────────────────────────

DEFAULT_STT_MODEL = "gpt-4o-mini-transcribe-2025-12-15"
FALLBACK_STT_MODEL = "gpt-4o-transcribe" #"whisper-1"
GPT_MODEL = "gpt-4o"
CLAUDE_MODEL = "claude-sonnet-4-20250514"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  🔑 API KEY 설정 (여기에 직접 입력)
#     환경변수가 있으면 환경변수 우선, 없으면 아래 값 사용
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OPENAI_API_KEY = ""
ANTHROPIC_API_KEY = ""
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  🔒 SSL 설정
#     회사/학교 네트워크에서 SSL 인증서 에러 발생 시
#     아래를 True로 변경하거나 --ssl-no-verify 옵션 사용
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SSL_VERIFY = False   # False로 바꾸면 SSL 검증 건너뜀
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

MAX_FILE_SIZE_MB = 25

# API가 직접 받을 수 있는 포맷
UPLOAD_FORMATS = {".flac", ".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".ogg", ".wav", ".webm"}
# ffmpeg 변환 필요 포맷
VIDEO_ONLY_EXT = {".mkv", ".avi", ".mov", ".wmv", ".flv", ".ts"}
ALL_SUPPORTED = UPLOAD_FORMATS | VIDEO_ONLY_EXT

# API 비용 (USD / 분, 2025 기준 추정치)
COST_PER_MIN = {
    "gpt-4o-transcribe-diarize": 0.006,
    "gpt-4o-transcribe": 0.006,
    "gpt-4o-mini-transcribe": 0.003,
    "gpt-4o-mini-transcribe-2025-12-15": 0.003,
    "whisper-1": 0.006,
}
LLM_COST_PER_1K_TOKENS = {"gpt-4o": 0.005, "claude": 0.003}

TYPE_LABELS = {
    "meeting":  {"title": "회의록",    "event": "회의",   "emoji": "🤝"},
    "seminar":  {"title": "세미나 기록", "event": "세미나", "emoji": "🎓"},
    "lecture":  {"title": "강의 노트",  "event": "강의",   "emoji": "📚"},
}

MAX_LLM_CHARS = 80000  # LLM 한 번에 보낼 최대 글자 수
MAX_RETRIES = 3
RETRY_DELAY = 5


# ──────────────────────────────────────────────
#  Logging  (항상 파일 기록, --debug 시 콘솔에도)
# ──────────────────────────────────────────────

DEBUG = False
logger = logging.getLogger("mm")


def setup_logging(output_dir: str, debug: bool = False):
    global DEBUG
    DEBUG = debug
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    os.makedirs(output_dir, exist_ok=True)
    log_path = os.path.join(output_dir, "run.log")

    # 파일 — 항상 DEBUG 레벨
    fh = logging.FileHandler(log_path, encoding="utf-8", mode="a")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "[%(asctime)s] %(levelname)-7s [%(funcName)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(fh)

    # 콘솔 — debug 시 DEBUG, 아니면 WARNING
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.DEBUG if debug else logging.WARNING)
    ch.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)-7s %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(ch)

    logger.info(f"{'='*60}")
    logger.info(f"  세션 시작: {datetime.now().isoformat()}")
    logger.info(f"  Python: {sys.version}")
    logger.info(f"  로그: {log_path}")
    logger.info(f"{'='*60}")

    return log_path


# ──────────────────────────────────────────────
#  Utilities
# ──────────────────────────────────────────────

def ts(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

def step(msg):  print(f"\n{'='*60}\n  ▶ {msg}\n{'='*60}"); logger.info(f"STEP: {msg}")
def info(msg):  print(f"  ℹ  {msg}"); logger.info(msg)
def ok(msg):    print(f"  ✅ {msg}"); logger.info(msg)
def warn(msg):  print(f"  ⚠️  {msg}"); logger.warning(msg)
def err(msg):   print(f"  ❌ {msg}", file=sys.stderr); logger.error(msg)

def file_mb(p: str) -> float:
    return os.path.getsize(p) / (1024 * 1024) if os.path.exists(p) else 0


def run_cmd(cmd: List[str], check=True) -> subprocess.CompletedProcess:
    logger.debug(f"CMD: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    if result.returncode != 0:
        logger.error(f"CMD FAIL (exit {result.returncode}): {result.stderr[:500]}")
        if check:
            raise RuntimeError(f"명령 실패: {cmd[0]} (exit {result.returncode})")
    return result


def audio_duration(p: str) -> float:
    try:
        r = run_cmd(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", p])
        return float(json.loads(r.stdout)["format"]["duration"])
    except Exception:
        return 0.0


def check_ffmpeg():
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
        logger.debug(f"API Key [{env_name}]: {key[:8]}...{key[-4:]}")
    return key


def make_openai_client(api_key: str):
    from openai import OpenAI
    if not SSL_VERIFY and HAS_HTTPX:
        warnings.filterwarnings("ignore", message="Unverified HTTPS request")
        return OpenAI(api_key=api_key, http_client=httpx.Client(verify=False))
    return OpenAI(api_key=api_key)


def make_anthropic_client(api_key: str):
    import anthropic as ant
    if not SSL_VERIFY and HAS_HTTPX:
        warnings.filterwarnings("ignore", message="Unverified HTTPS request")
        return ant.Anthropic(api_key=api_key, http_client=httpx.Client(verify=False))
    return ant.Anthropic(api_key=api_key)


def sanitize_filename(name: str) -> str:
    """파일명에 사용할 수 없는 문자 제거"""
    return re.sub(r'[\\/*?:"<>|]', '_', name).strip()


def make_output_dir(base_dir: str, title: str) -> str:
    """
    출력 디렉토리 생성.
    구조: {base_dir}/{날짜}_{제목}/
    예:   output/2025-02-10_NVIDIA세미나/
    """
    date_str = datetime.now().strftime("%Y-%m-%d")
    folder_name = sanitize_filename(f"{date_str}_{title}")
    out_dir = os.path.join(base_dir, folder_name)
    os.makedirs(out_dir, exist_ok=True)
    return out_dir


def find_existing_output_dir(base_dir: str, title: str) -> Optional[str]:
    """
    --resume, --edit-speakers 용: 기존 출력 폴더 찾기.
    가장 최근 {날짜}_{제목} 폴더를 반환.
    """
    safe_title = sanitize_filename(title)
    if not os.path.isdir(base_dir):
        return None

    candidates = []
    for d in sorted(Path(base_dir).iterdir(), reverse=True):
        if d.is_dir() and safe_title in d.name:
            # segments.json 이 있는 폴더만
            if any(d.glob("*_segments.json")) or any(d.glob("segments.json")):
                candidates.append(str(d))

    if candidates:
        return candidates[0]  # 가장 최근
    return None


def retry_call(func, *args, retries=MAX_RETRIES, delay=RETRY_DELAY, **kwargs):
    """자동 재시도 래퍼"""
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_err = e
            if attempt < retries:
                logger.warning(f"재시도 {attempt}/{retries}: {type(e).__name__}: {e}")
                warn(f"  재시도 {attempt}/{retries} ({delay}초 후)...")
                time.sleep(delay)
            else:
                logger.error(f"최종 실패: {type(e).__name__}: {e}")
    raise last_err


# ──────────────────────────────────────────────
#  Config File Support
# ──────────────────────────────────────────────

DEFAULT_CONFIG = {
    "type": "meeting",
    "model": DEFAULT_STT_MODEL,
    "llm": "gpt",
    "language": None,
    "translate": False,
    "translate_script": False,
    "speakers": None,
    "memo": None,
    "ssl_no_verify": False,
    "debug": False,
    "output_dir": "./output",
    "openai_api_key": "",
    "anthropic_api_key": "",
}


def load_config(config_path: str) -> dict:
    """JSON 설정 파일 로드"""
    if not os.path.isfile(config_path):
        return {}
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    logger.info(f"설정 파일 로드: {config_path}")
    return cfg


def save_default_config(path: str):
    """기본 설정 파일 생성"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
    ok(f"기본 설정 파일 생성 → {path}")


# ──────────────────────────────────────────────
#  LLM Client  (GPT-4o ↔ Claude 폴백 + 재시도)
# ──────────────────────────────────────────────

class LLMClient:
    def __init__(self, preferred: str = "gpt"):
        self.preferred = preferred
        self.openai = None
        self.anthropic = None
        self._call_count = 0
        self._total_tokens = 0
        self._init()

    def _init(self):
        try:
            k = get_api_key("OPENAI_API_KEY", OPENAI_API_KEY)
            if k:
                self.openai = make_openai_client(k)
                info("OpenAI client ready" + (" (SSL우회)" if not SSL_VERIFY else ""))
        except ImportError:
            warn("openai 미설치 → pip install openai")
        except Exception as e:
            warn(f"OpenAI 초기화 실패: {e}")

        try:
            k = get_api_key("ANTHROPIC_API_KEY", ANTHROPIC_API_KEY)
            if k:
                self.anthropic = make_anthropic_client(k)
                info("Anthropic client ready" + (" (SSL우회)" if not SSL_VERIFY else ""))
        except ImportError:
            pass
        except Exception as e:
            warn(f"Anthropic 초기화 실패: {e}")

    def _gpt(self, system, user, temp=0.3):
        if not self.openai: return None
        try:
            t0 = time.time()
            r = self.openai.chat.completions.create(
                model=GPT_MODEL, temperature=temp,
                messages=[{"role":"system","content":system}, {"role":"user","content":user}],
            )
            elapsed = time.time() - t0
            result = r.choices[0].message.content
            if r.usage:
                self._total_tokens += r.usage.total_tokens
                logger.debug(f"GPT: {r.usage.total_tokens} tokens, {elapsed:.1f}s")
            return result
        except Exception as e:
            logger.error(f"GPT: {type(e).__name__}: {e}")
            return None

    def _claude(self, system, user, temp=0.3):
        if not self.anthropic: return None
        try:
            t0 = time.time()
            r = self.anthropic.messages.create(
                model=CLAUDE_MODEL, max_tokens=8192, temperature=temp,
                system=system, messages=[{"role":"user","content":user}],
            )
            elapsed = time.time() - t0
            result = r.content[0].text
            total = r.usage.input_tokens + r.usage.output_tokens
            self._total_tokens += total
            logger.debug(f"Claude: {total} tokens, {elapsed:.1f}s")
            return result
        except Exception as e:
            logger.error(f"Claude: {type(e).__name__}: {e}")
            return None

    def chat(self, system: str, user: str, temp: float = 0.3) -> str:
        self._call_count += 1
        if self.preferred == "claude":
            r = self._claude(system, user, temp)
            if r: return r
            warn("Claude 실패 → GPT 폴백")
            r = self._gpt(system, user, temp)
        else:
            r = self._gpt(system, user, temp)
            if r: return r
            warn("GPT 실패 → Claude 폴백")
            r = self._claude(system, user, temp)
        if r: return r
        raise RuntimeError(
            "LLM 호출 모두 실패.\n"
            "  → API 키 확인 / --ssl-no-verify 시도"
        )

    def stats(self) -> str:
        return f"LLM 호출 {self._call_count}회, 총 ~{self._total_tokens:,} tokens"


# ──────────────────────────────────────────────
#  Audio Preparation
# ──────────────────────────────────────────────

def prepare_audio(input_path: str, work_dir: str) -> str:
    step("오디오 준비 중...")
    ext = Path(input_path).suffix.lower()
    size = file_mb(input_path)
    info(f"입력: {Path(input_path).name}  ({size:.1f} MB, {ext})")

    if size <= MAX_FILE_SIZE_MB and ext in UPLOAD_FORMATS:
        info("변환 없이 직접 업로드")
        return input_path

    info(f"mp3 변환 중... (원본 {size:.1f}MB)")
    out = os.path.join(work_dir, Path(input_path).stem + ".mp3")
    run_cmd(["ffmpeg", "-y", "-i", input_path,
             "-vn", "-ar", "16000", "-ac", "1", "-b:a", "48k", out])
    ok(f"변환 완료: {size:.1f}MB → {file_mb(out):.1f}MB")
    return out


def split_audio(audio_path: str, work_dir: str) -> List[Tuple[str, float]]:
    size = file_mb(audio_path)
    dur = audio_duration(audio_path)
    logger.debug(f"오디오: {size:.2f}MB, {dur:.1f}s")
    if size <= MAX_FILE_SIZE_MB:
        return [(audio_path, 0.0)]

    info(f"파일 {size:.1f}MB > {MAX_FILE_SIZE_MB}MB → 분할")
    n = math.ceil(size / (MAX_FILE_SIZE_MB * 0.85))
    chunk_dur = dur / n
    stem = Path(audio_path).stem
    chunks = []
    for i in range(n):
        offset = i * chunk_dur
        cp = os.path.join(work_dir, f"{stem}_chunk{i:03d}.mp3")
        run_cmd(["ffmpeg", "-y", "-i", audio_path,
                 "-ss", str(offset), "-t", str(chunk_dur),
                 "-ar", "16000", "-ac", "1", "-b:a", "48k", cp])
        chunks.append((cp, offset))
    info(f"{n}개 청크 생성")
    return chunks


# ──────────────────────────────────────────────
#  Cost Estimation
# ──────────────────────────────────────────────

def estimate_cost(input_paths: List[str], model: str, translate: bool, llm: str) -> dict:
    """API 비용 사전 추정"""
    total_dur = 0
    for p in input_paths:
        d = audio_duration(p)
        if d == 0:
            # 영상이면 ffprobe로
            total_dur += 60  # 기본 1분 가정
        else:
            total_dur += d

    total_min = total_dur / 60

    stt_cost = total_min * COST_PER_MIN.get(model, 0.006)

    # LLM 비용 추정: 1분당 약 150 단어 → ~200 토큰
    est_tokens = total_min * 200
    llm_rate = LLM_COST_PER_1K_TOKENS.get(llm, 0.005)
    # 회의록 + 요약 = 입력 + 출력 ~3x
    llm_cost = (est_tokens * 3 / 1000) * llm_rate

    translate_cost = 0
    if translate:
        translate_cost = (est_tokens * 2 / 1000) * llm_rate

    total = stt_cost + llm_cost + translate_cost

    return {
        "files": len(input_paths),
        "total_duration_min": round(total_min, 1),
        "stt_cost": round(stt_cost, 3),
        "llm_cost": round(llm_cost, 3),
        "translate_cost": round(translate_cost, 3),
        "total_cost": round(total, 3),
    }


def print_cost_estimate(est: dict):
    print(f"\n  💰 비용 추정")
    print(f"  ─────────────────────────────")
    print(f"  파일 수:       {est['files']}개")
    print(f"  총 길이:       ~{est['total_duration_min']}분")
    print(f"  STT 비용:      ~${est['stt_cost']:.3f}")
    print(f"  LLM 비용:      ~${est['llm_cost']:.3f}")
    if est['translate_cost'] > 0:
        print(f"  번역 비용:     ~${est['translate_cost']:.3f}")
    print(f"  ─────────────────────────────")
    print(f"  예상 합계:     ~${est['total_cost']:.3f}")
    print(f"  (실제 비용은 다를 수 있습니다)\n")


# ──────────────────────────────────────────────
#  STT
# ──────────────────────────────────────────────

def transcribe_chunk(
    client, audio_path: str, model: str,
    language: Optional[str] = None,
    speaker_names: Optional[List[str]] = None,
    offset: float = 0.0,
) -> List[Dict]:
    use_diarize = "diarize" in model
    use_whisper = model.startswith("whisper")

    f = open(audio_path, "rb")
    try:
        params: Dict[str, Any] = {"model": model, "file": f}
        if use_diarize:
            params["response_format"] = "diarized_json"
            params["chunking_strategy"] = "auto"
            if speaker_names:
                params["known_speaker_names"] = speaker_names[:4]
        elif use_whisper:
            params["response_format"] = "verbose_json"
            params["timestamp_granularities"] = ["segment"]
        else:
            params["response_format"] = "json"
        if language:
            params["language"] = language

        logger.debug(f"STT: model={model}, file={Path(audio_path).name}, "
                     f"{file_mb(audio_path):.1f}MB, offset={offset:.1f}s")

        t0 = time.time()
        resp = retry_call(client.audio.transcriptions.create, **params)
        logger.debug(f"STT: {time.time()-t0:.1f}s")
    finally:
        f.close()

    data = resp if isinstance(resp, dict) else (
        resp.model_dump() if hasattr(resp, "model_dump") else json.loads(resp))

    logger.debug(f"STT keys: {list(data.keys())}")

    if use_diarize:
        return _parse_diarized(data, offset)
    elif use_whisper:
        return _parse_verbose(data, offset)
    else:
        return _parse_json_simple(data, offset)


def _parse_diarized(data: dict, offset: float) -> List[Dict]:
    segments = []
    if "speakers" in data and isinstance(data["speakers"], list):
        for spk in data["speakers"]:
            label = spk.get("name") or spk.get("id", "Speaker")
            for seg in spk.get("segments", []):
                segments.append({"start": seg.get("start",0)+offset, "end": seg.get("end",0)+offset,
                                 "text": seg.get("text","").strip(), "speaker": label})
        segments.sort(key=lambda x: x["start"])
        if segments: return segments

    if "segments" in data and isinstance(data["segments"], list):
        for seg in data["segments"]:
            segments.append({"start": seg.get("start",0)+offset, "end": seg.get("end",0)+offset,
                             "text": seg.get("text","").strip(), "speaker": seg.get("speaker","Speaker")})
        if segments: return segments

    if "words" in data and isinstance(data["words"], list):
        cur = {"start":0, "end":0, "text":"", "speaker":""}
        for w in data["words"]:
            spk = w.get("speaker","Speaker")
            word = w.get("word", w.get("text",""))
            if spk != cur["speaker"] and cur["text"].strip():
                segments.append({"start":cur["start"], "end":cur["end"],
                                 "text":cur["text"].strip(), "speaker":cur["speaker"]})
                cur = {"start":w.get("start",0)+offset, "end":w.get("end",0)+offset, "text":word, "speaker":spk}
            else:
                if not cur["text"]: cur["start"]=w.get("start",0)+offset; cur["speaker"]=spk
                cur["end"]=w.get("end",0)+offset; cur["text"]+=" "+word
        if cur["text"].strip():
            segments.append({"start":cur["start"], "end":cur["end"],
                             "text":cur["text"].strip(), "speaker":cur["speaker"]})
        if segments: return segments

    segments.append({"start":offset, "end":offset, "text":data.get("text",""), "speaker":"Speaker"})
    return segments


def _parse_verbose(data: dict, offset: float) -> List[Dict]:
    segments = []
    for seg in data.get("segments", []):
        segments.append({"start": seg["start"]+offset, "end": seg["end"]+offset,
                         "text": seg["text"].strip(), "speaker": ""})
    if not segments and data.get("text"):
        segments.append({"start":offset, "end":offset, "text":data["text"], "speaker":""})
    return segments


def _parse_json_simple(data: dict, offset: float) -> List[Dict]:
    text = data.get("text", "").strip()
    if not text:
        return [{"start":offset, "end":offset, "text":"", "speaker":""}]

    sentences = re.split(r'(?<=[.!?。！？])\s+', text)
    merged, buf = [], ""
    for s in sentences:
        buf = (buf+" "+s).strip() if buf else s
        if len(buf) > 30:
            merged.append(buf); buf = ""
    if buf:
        if merged: merged[-1] += " " + buf
        else: merged.append(buf)

    return [{"start":offset, "end":offset, "text":s.strip(), "speaker":""} for s in merged]


def run_stt(
    audio_path: str, model: str, language: Optional[str] = None,
    speaker_names: Optional[List[str]] = None, work_dir: str = "/tmp",
) -> List[Dict]:
    step(f"STT 수행 중  (model: {model})")
    key = get_api_key("OPENAI_API_KEY", OPENAI_API_KEY)
    client = make_openai_client(key)

    chunks = split_audio(audio_path, work_dir)
    all_segments, total_time = [], 0

    for i, (cp, chunk_offset) in enumerate(chunks):
        if len(chunks) > 1:
            info(f"  청크 {i+1}/{len(chunks)} 처리 중...")
        t0 = time.time()
        try:
            segs = transcribe_chunk(client, cp, model, language, speaker_names, chunk_offset)
            all_segments.extend(segs)
        except Exception as e:
            logger.error(f"STT chunk {i}: {type(e).__name__}: {e}\n{traceback.format_exc()}")
            if model != FALLBACK_STT_MODEL:
                warn(f"  {model} 실패 → {FALLBACK_STT_MODEL} 폴백")
                segs = transcribe_chunk(client, cp, FALLBACK_STT_MODEL, language, None, chunk_offset)
                all_segments.extend(segs)
            else:
                raise
        total_time += time.time() - t0
        if cp != audio_path and os.path.exists(cp):
            os.remove(cp)

    ok(f"STT 완료: {len(all_segments)}개 세그먼트 ({total_time:.1f}초)")
    return all_segments


# ──────────────────────────────────────────────
#  Translation
# ──────────────────────────────────────────────

def translate_segments(segments: List[Dict], llm: LLMClient, batch_size: int = 30) -> List[Dict]:
    step("영어 → 한국어 번역 중...")
    translated, total = [], math.ceil(len(segments)/batch_size)

    for bi in range(total):
        batch = segments[bi*batch_size:(bi+1)*batch_size]
        info(f"  번역 배치 {bi+1}/{total} ({len(batch)}개)")

        items = json.dumps([{"i":i,"t":s["text"]} for i,s in enumerate(batch)], ensure_ascii=False)
        system = ("전문 영한 번역가. 회의/세미나/강의 발화를 자연스러운 한국어로 번역.\n"
                  "전문 용어는 원문 병기(예: 인공지능(AI)).\n"
                  'JSON 배열로만: [{"i":0,"t":"번역"},...]  설명 없이.')
        try:
            raw = retry_call(llm.chat, system, items, temp=0.2)
            arr = json.loads(re.search(r"\[[\s\S]*\]", raw).group())
            tmap = {a["i"]: a["t"] for a in arr}
            for i, s in enumerate(batch):
                ns = s.copy(); ns["text_original"] = s["text"]; ns["text"] = tmap.get(i, s["text"])
                translated.append(ns)
        except Exception as e:
            warn(f"  배치 {bi+1} 실패: {e} → 원문 유지")
            logger.error(traceback.format_exc())
            translated.extend(batch)
        if bi < total-1: time.sleep(0.5)

    ok(f"번역 완료: {len(translated)}개")
    return translated


# ──────────────────────────────────────────────
#  Speaker Editing
# ──────────────────────────────────────────────

def edit_speakers_interactive(segments: List[Dict]) -> List[Dict]:
    step("화자 수정 모드")
    speakers = sorted(set(s.get("speaker","") for s in segments if s.get("speaker")))
    if not speakers:
        warn("화자 정보 없음"); return segments

    print(f"\n  현재 화자 ({len(speakers)}명):")
    for i, spk in enumerate(speakers):
        examples = [s["text"][:60] for s in segments if s.get("speaker")==spk][:3]
        print(f"    [{i+1}] {spk}  ({sum(1 for s in segments if s.get('speaker')==spk)}발화)")
        for ex in examples:
            print(f"        > {ex}...")

    print(f"\n  이름 변경 (Enter=유지, 'done'=완료)\n")
    rename_map = {}
    for spk in speakers:
        new = input(f"  {spk} → ").strip()
        if new.lower() == "done": break
        if new: rename_map[spk] = new; ok(f"  {spk} → {new}")

    if rename_map:
        for seg in segments:
            old = seg.get("speaker","")
            if old in rename_map: seg["speaker"] = rename_map[old]
        ok(f"화자 수정 적용 완료")
    return segments


# ──────────────────────────────────────────────
#  Output Generation
# ──────────────────────────────────────────────

def has_timestamps(segments: List[Dict]) -> bool:
    return any(s.get("start",0) != s.get("end",0) for s in segments)


def build_script_md(segments: List[Dict], title: str = "", include_original: bool = False) -> str:
    has_ts = has_timestamps(segments)
    lines = [
        f"# 📝 {title + ' — ' if title else ''}스크립트\n",
        f"> 생성: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"> 세그먼트: {len(segments)}개"
            + (f" | 타임스탬프: 포함" if has_ts else "") + "\n",
        "---\n",
    ]
    cur_spk = None
    for s in segments:
        spk = s.get("speaker","")
        if spk and spk != cur_spk:
            lines.append(f"\n### 🎤 {spk}\n"); cur_spk = spk
        line = f"`[{ts(s['start'])}]` {s['text']}" if has_ts else s['text']
        if include_original and s.get("text_original"):
            line += f"\n> 🇺🇸 _{s['text_original']}_"
        lines.append(line)
    return "\n".join(lines)


def _get_minutes_prompt(doc_type: str) -> str:
    if doc_type == "meeting":
        return """전문 회의록 작성자. 스크립트+메모 기반 체계적 회의록을 Markdown으로.

구성:
1. **회의 개요** — 일시, 참석자(추론), 목적
2. **주요 안건**
3. **논의 내용** — 안건별 상세, 발언자 인용
4. **결정 사항**
5. **Action Items** — 담당자, 마감, 업무
6. **다음 단계**

규칙: 메모 적극 반영, 화자 역할 추론, 불명확=[확인 필요], 격식 한국어"""

    elif doc_type == "seminar":
        return """전문 세미나 기록 작성자. 발표 스크립트+메모 기반 세미나 기록을 Markdown으로.

구성:
1. **세미나 개요** — 제목, 발표자, 주제
2. **핵심 내용** — 주요 포인트 (섹션별)
3. **상세 내용** — 개념, 기술, 사례, 데모
4. **Q&A 요약** (있으면)
5. **핵심 인사이트** — 실무 적용 포인트
6. **참고 자료** — 도구, 링크, 논문

규칙: 발표 흐름순, 기술 용어 설명, 메모 반영, 격식 한국어"""

    elif doc_type == "lecture":
        return """전문 강의 노트 작성자. 강의 스크립트+메모 기반 강의 노트를 Markdown으로.

구성:
1. **강의 개요** — 과목/주제, 강사, 학습 목표
2. **강의 목차**
3. **강의 내용** — 주제별 상세 (개념, 예시, 수식/코드, ⭐강조점)
4. **핵심 정리** — 시험/실무 대비
5. **과제/다음 강의**
6. **추가 학습 자료**

규칙: 학습 최적화, 어려운 개념 쉽게, 메모 반영, 격식 한국어"""
    return ""


def _get_summary_prompt(doc_type: str) -> str:
    prompts = {
        "meeting": "한줄 요약 → 핵심 결정 3~5개 → Action Items → 리스크. A4 1p 이내. 경영진이 1분내 읽을 수준.",
        "seminar": "한줄 요약 → 핵심 내용 3~5개 → 실무 적용 포인트 → 후속 학습. 참석 못한 동료에게 공유 수준.",
        "lecture": "한줄 요약 → 핵심 개념 3~5개 → 시험/과제 대비 → 다음 강의 준비. 시험 직전 복습 수준.",
    }
    labels = TYPE_LABELS[doc_type]
    return f"{labels['event']} 요약 전문가.\n\n{prompts.get(doc_type,'')} 한국어."


def _chunk_script(segments: List[Dict], max_chars: int = MAX_LLM_CHARS) -> List[str]:
    """긴 스크립트를 LLM 컨텍스트에 맞게 분할"""
    has_ts = has_timestamps(segments)
    blocks, current = [], ""
    for s in segments:
        if has_ts:
            line = f"[{ts(s['start'])}] {s.get('speaker','')}: {s['text']}\n"
        else:
            line = f"{s.get('speaker','')}: {s['text']}\n"
        if len(current) + len(line) > max_chars:
            blocks.append(current); current = line
        else:
            current += line
    if current: blocks.append(current)
    return blocks


def generate_minutes(
    segments: List[Dict], llm: LLMClient,
    doc_type: str = "meeting", memo: Optional[str] = None, title: str = "",
) -> str:
    labels = TYPE_LABELS[doc_type]
    step(f"{labels['title']} 생성 중...")

    blocks = _chunk_script(segments)
    system = _get_minutes_prompt(doc_type)

    if len(blocks) <= 1:
        memo_block = f"\n### 📌 메모 (반드시 반영):\n{memo}\n" if memo else ""
        title_block = f"\n### 제목: {title}\n" if title else ""
        user = f"{title_block}{memo_block}\n### 스크립트:\n{blocks[0] if blocks else ''}"
        result = retry_call(llm.chat, system, user, temp=0.3)
    else:
        # 여러 블록 → 블록별 요약 → 최종 통합
        info(f"  긴 스크립트: {len(blocks)}블록으로 분할 처리")
        partial_summaries = []
        for i, block in enumerate(blocks):
            info(f"  블록 {i+1}/{len(blocks)} 처리 중...")
            p_sys = f"스크립트 일부를 정리하세요. 핵심 내용, 결정사항, 발언자를 포함. 한국어."
            p_result = retry_call(llm.chat, p_sys, block, temp=0.3)
            partial_summaries.append(p_result)

        combined = "\n\n---\n\n".join(partial_summaries)
        memo_block = f"\n### 📌 메모:\n{memo}\n" if memo else ""
        title_block = f"\n### 제목: {title}\n" if title else ""
        final_user = (f"{title_block}{memo_block}\n"
                      f"### 정리된 내용 (여러 파트를 통합하여 하나의 {labels['title']}로):\n{combined}")
        result = retry_call(llm.chat, system, final_user, temp=0.3)

    ok(f"{labels['title']} 생성 완료")
    return result


def generate_summary(minutes: str, llm: LLMClient, doc_type: str = "meeting") -> str:
    labels = TYPE_LABELS[doc_type]
    step("요약본 생성 중...")
    system = _get_summary_prompt(doc_type)
    result = retry_call(llm.chat, system, f"다음 {labels['title']}을 요약:\n\n{minutes}", temp=0.2)
    ok("요약본 생성 완료")
    return result


# ──────────────────────────────────────────────
#  Save
# ──────────────────────────────────────────────

def save(content: str, path: str, label: str):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    ok(f"{label} → {path}")


# ──────────────────────────────────────────────
#  Single File Pipeline
# ──────────────────────────────────────────────

def process_single(
    input_path: str, args, llm: LLMClient,
    output_dir: str, title: str, work_dir: str,
    file_prefix: str = "",
):
    """
    단일 파일 처리 파이프라인.
    file_prefix: 다중파일 시 "01_파일명_" 같은 접두사, 단일파일이면 ""
    """
    labels = TYPE_LABELS[args.type]
    pfx = file_prefix  # 예: "" (단일) 또는 "01_filename_" (다중)
    seg_path = os.path.join(output_dir, f"{pfx}segments.json")

    # ── Resume: 기존 STT 결과 재사용 ──
    if args.resume and os.path.isfile(seg_path):
        info(f"기존 세그먼트 로드 (--resume): {seg_path}")
        with open(seg_path, "r", encoding="utf-8") as f:
            segments = json.load(f)
    else:
        # 1. 오디오 준비
        audio_path = prepare_audio(input_path, work_dir)

        # 2. STT
        speaker_names = [n.strip() for n in args.speakers.split(",") if n.strip()] if args.speakers else None
        segments = run_stt(audio_path, model=args.model, language=args.language,
                           speaker_names=speaker_names, work_dir=work_dir)
        if not segments:
            err(f"STT 결과 비어있음: {input_path}"); return

        # 세그먼트 저장
        with open(seg_path, "w", encoding="utf-8") as f:
            json.dump(segments, f, ensure_ascii=False, indent=2)
        info(f"세그먼트 → {seg_path}")

    # 3. 번역
    segments_for_doc = segments
    if args.translate:
        seg_ko_path = os.path.join(output_dir, f"{pfx}segments_translated.json")
        if args.resume and os.path.isfile(seg_ko_path):
            info(f"기존 번역 세그먼트 로드 (--resume)")
            with open(seg_ko_path, "r", encoding="utf-8") as f:
                segments_for_doc = json.load(f)
        else:
            segments_for_doc = translate_segments(segments, llm)
            with open(seg_ko_path, "w", encoding="utf-8") as f:
                json.dump(segments_for_doc, f, ensure_ascii=False, indent=2)

    # 4. 스크립트
    script_md = build_script_md(segments, title)
    save(script_md, os.path.join(output_dir, f"{pfx}script.md"), "스크립트")

    if args.translate and args.translate_script:
        script_ko = build_script_md(segments_for_doc, title, include_original=True)
        save(script_ko, os.path.join(output_dir, f"{pfx}script_ko.md"), "스크립트 (한국어)")

    # 5. 기록 문서
    memo = read_file(args.memo) if args.memo and os.path.isfile(args.memo) else None
    if args.custom_prompt:
        memo = (memo or "") + f"\n\n[추가 지시]: {args.custom_prompt}"

    minutes = generate_minutes(segments_for_doc, llm, args.type, memo, title)
    header = (f"<!-- Generated: {datetime.now().isoformat()} -->\n"
              f"<!-- Source: {Path(input_path).name} | Type: {args.type} -->\n\n")
    save(header + minutes, os.path.join(output_dir, f"{pfx}minutes.md"), labels['title'])

    # 6. 요약
    summary = generate_summary(minutes, llm, args.type)
    save(summary, os.path.join(output_dir, f"{pfx}summary.md"), "요약본")


# ──────────────────────────────────────────────
#  Main
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="🎙️ Meeting/Seminar/Lecture Minutes Generator v2.0",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python meeting_minutes.py meeting.mp4
  python meeting_minutes.py meeting.mp4 --title "Q1 정기회의"
  python meeting_minutes.py *.webm --type seminar --translate
  python meeting_minutes.py a.mp4 b.mp4 --title "프로젝트 킥오프"
  python meeting_minutes.py big.webm --estimate-cost
  python meeting_minutes.py meeting.mp4 --resume
  python meeting_minutes.py meeting.mp4 --edit-speakers
  python meeting_minutes.py meeting.mp4 --ssl-no-verify
  python meeting_minutes.py --init-config

출력 구조:
  output/2025-02-10_NVIDIA세미나/
    ├── script.md
    ├── minutes.md
    ├── summary.md
    ├── segments.json
    └── run.log
""",
    )

    parser.add_argument("input", nargs="*", help="음성/영상 파일 (여러 개, glob 가능)")
    parser.add_argument("--title", help="제목 (출력 폴더명·문서 제목. 미지정 시 파일명)")
    parser.add_argument("--type", default="meeting", choices=["meeting","seminar","lecture"],
                        help="문서 유형")
    parser.add_argument("--memo", help="메모 파일 경로")
    parser.add_argument("--speakers", help="화자 이름 쉼표 구분 (최대 4명)")
    parser.add_argument("--language", help="STT 언어 힌트 (ko, en, ja ...)")
    parser.add_argument("--model", default=DEFAULT_STT_MODEL,
                        choices=["gpt-4o-transcribe-diarize","gpt-4o-transcribe",
                                 "gpt-4o-mini-transcribe","gpt-4o-mini-transcribe-2025-12-15","whisper-1"],
                        help=f"STT 모델 (기본: {DEFAULT_STT_MODEL})")
    parser.add_argument("--llm", default="gpt", choices=["gpt","claude"], help="LLM 선택")
    parser.add_argument("--translate", action="store_true", help="영→한 번역")
    parser.add_argument("--translate-script", action="store_true", help="스크립트 번역본도 생성")
    parser.add_argument("--output-dir", default="./output", help="출력 베이스 디렉토리")
    parser.add_argument("--custom-prompt", help="LLM에 추가 지시 (예: '기술 용어 중심으로 정리')")

    parser.add_argument("--resume", action="store_true", help="기존 STT 결과 재사용 (이어서 처리)")
    parser.add_argument("--edit-speakers", action="store_true", help="화자명 수정 후 재생성")
    parser.add_argument("--estimate-cost", action="store_true", help="비용 추정만 (실행 안 함)")

    parser.add_argument("--config", help="JSON 설정 파일 경로")
    parser.add_argument("--init-config", action="store_true", help="기본 설정 파일(config.json) 생성")

    parser.add_argument("--ssl-no-verify", action="store_true", help="SSL 검증 비활성화")
    parser.add_argument("--debug", action="store_true", help="콘솔에도 상세 로그 출력")

    args = parser.parse_args()

    # ── init-config ──
    if args.init_config:
        save_default_config("config.json")
        return

    # ── config 파일 로드 ──
    if args.config:
        cfg = load_config(args.config)
        for k, v in cfg.items():
            arg_key = k.replace("-", "_")
            if hasattr(args, arg_key) and getattr(args, arg_key) in (None, False, "meeting", DEFAULT_STT_MODEL, "gpt", "./output"):
                setattr(args, arg_key, v)
            if k == "openai_api_key" and v:
                global OPENAI_API_KEY; OPENAI_API_KEY = v
            if k == "anthropic_api_key" and v:
                global ANTHROPIC_API_KEY; ANTHROPIC_API_KEY = v

    # ── SSL ──
    global SSL_VERIFY
    if args.ssl_no_verify:
        SSL_VERIFY = False

    # ── 입력 파일 수집 (glob 지원) ──
    input_files = []
    for pattern in (args.input or []):
        expanded = glob.glob(pattern)
        if expanded:
            input_files.extend(expanded)
        elif os.path.isfile(pattern):
            input_files.append(pattern)
        else:
            err(f"파일 없음: {pattern}")

    if not input_files:
        parser.print_help()
        return

    valid_files = []
    for f in input_files:
        ext = Path(f).suffix.lower()
        if ext in ALL_SUPPORTED:
            valid_files.append(f)
        else:
            warn(f"미지원 포맷 건너뜀: {f} ({ext})")

    if not valid_files:
        err("처리할 파일이 없습니다.")
        sys.exit(1)

    # ── Validation (로깅 전 기본 검사) ──
    if not check_ffmpeg():
        err("ffmpeg 미설치. https://ffmpeg.org"); sys.exit(1)
    if not get_api_key("OPENAI_API_KEY", OPENAI_API_KEY):
        err("OpenAI API 키 없음 (코드 상단 또는 환경변수)"); sys.exit(1)

    # ── 비용 추정 ──
    if args.estimate_cost:
        est = estimate_cost(valid_files, args.model, args.translate, args.llm)
        print_cost_estimate(est)
        return

    # ══════════════════════════════════════════
    #  출력 폴더 결정
    #
    #  단일 파일:
    #    output/2025-02-10_NVIDIA세미나/
    #      ├── script.md
    #      ├── minutes.md
    #      ├── summary.md
    #      ├── segments.json
    #      └── run.log
    #
    #  다중 파일 + --title:
    #    output/2025-02-10_프로젝트킥오프/
    #      ├── 01_file1_script.md
    #      ├── 01_file1_minutes.md
    #      ├── 02_file2_script.md
    #      └── run.log
    #
    #  다중 파일 (title 없음):
    #    output/2025-02-10_file1/ , output/2025-02-10_file2/ ...
    # ══════════════════════════════════════════
    multi = len(valid_files) > 1
    base_title = args.title or (Path(valid_files[0]).stem if not multi else None)

    # 다중 파일 + 제목 → 하나의 폴더
    # 다중 파일 - 제목 → 파일마다 폴더
    # 단일 파일 → 하나의 폴더

    # ── resume / edit-speakers: 기존 폴더 찾기 ──
    if args.resume or args.edit_speakers:
        if base_title:
            found = find_existing_output_dir(args.output_dir, base_title)
            if found:
                info(f"기존 출력 폴더 발견: {found}")
            else:
                err(f"기존 출력 폴더를 찾을 수 없습니다 (제목: {base_title})")
                err(f"  {args.output_dir}/ 아래 폴더를 확인하세요.")
                sys.exit(1)

    # ── 화자 수정 모드 ──
    if args.edit_speakers:
        for fp in valid_files:
            file_title = base_title or Path(fp).stem
            out_dir = find_existing_output_dir(args.output_dir, file_title)
            if not out_dir:
                err(f"기존 폴더 없음 ({file_title}) → 건너뜀"); continue

            # 세그먼트 파일 찾기
            seg_files = sorted(Path(out_dir).glob("*segments.json"))
            seg_files = [f for f in seg_files if "translated" not in f.name]
            if not seg_files:
                err(f"세그먼트 없음: {out_dir}"); continue

            log_path = setup_logging(out_dir, args.debug)
            llm = LLMClient(preferred=args.llm)
            labels = TYPE_LABELS[args.type]
            memo = read_file(args.memo) if args.memo and os.path.isfile(args.memo) else None

            for seg_path in seg_files:
                with open(seg_path, "r", encoding="utf-8") as f:
                    segments = json.load(f)
                segments = edit_speakers_interactive(segments)
                with open(seg_path, "w", encoding="utf-8") as f:
                    json.dump(segments, f, ensure_ascii=False, indent=2)
                ok(f"세그먼트 업데이트 → {seg_path}")

                # prefix 추출
                pfx = seg_path.name.replace("segments.json", "")

                # 번역 세그먼트 화자도 업데이트
                seg_ko_path = os.path.join(out_dir, f"{pfx}segments_translated.json")
                segs_for_doc = segments
                if os.path.isfile(seg_ko_path):
                    with open(seg_ko_path, "r", encoding="utf-8") as f:
                        segs_ko = json.load(f)
                    for orig, upd in zip(segments, segs_ko):
                        upd["speaker"] = orig.get("speaker","")
                    segs_for_doc = segs_ko
                    with open(seg_ko_path, "w", encoding="utf-8") as f:
                        json.dump(segs_ko, f, ensure_ascii=False, indent=2)

                doc_title = file_title
                script_md = build_script_md(segments, doc_title)
                save(script_md, os.path.join(out_dir, f"{pfx}script.md"), "스크립트")

                minutes = generate_minutes(segs_for_doc, llm, args.type, memo, doc_title)
                header = f"<!-- Edited: {datetime.now().isoformat()} -->\n\n"
                save(header+minutes, os.path.join(out_dir, f"{pfx}minutes.md"), labels['title'])

                summary = generate_summary(minutes, llm, args.type)
                save(summary, os.path.join(out_dir, f"{pfx}summary.md"), "요약본")

            ok(f"화자 수정 및 재생성 완료 → {out_dir}")
        return

    # ══════════════════════════════════════════
    #  메인 파이프라인
    # ══════════════════════════════════════════
    labels = TYPE_LABELS[args.type]
    work_dir = tempfile.mkdtemp(prefix="mm_")
    pipeline_start = time.time()

    # 다중 파일 + 제목 있음 → 공용 폴더 하나
    if multi and base_title:
        shared_out_dir = make_output_dir(args.output_dir, base_title)
        log_path = setup_logging(shared_out_dir, args.debug)
    else:
        # 단일 or 다중(제목 없음) → 파일별 폴더, 로그는 base에
        shared_out_dir = None
        os.makedirs(args.output_dir, exist_ok=True)
        log_path = setup_logging(args.output_dir, args.debug)

    logger.info(f"입력: {valid_files}")
    logger.info(f"옵션: {vars(args)}")

    print(f"\n{'#'*60}")
    print(f"  {labels['emoji']}  {labels['title']} Generator v2.0")
    print(f"  파일:  {len(valid_files)}개")
    if base_title:
        print(f"  제목:  {base_title}")
    print(f"  타입:  {args.type}   STT: {args.model}   LLM: {args.llm}")
    print(f"  번역:  {'ON' if args.translate else 'OFF'}")
    if args.resume: print(f"  ♻️  Resume 모드")
    if not SSL_VERIFY: print(f"  🔓 SSL 검증 OFF")
    print(f"  로그:  {log_path}")
    print(f"{'#'*60}")

    llm = LLMClient(preferred=args.llm)
    success, fail = 0, 0
    all_out_dirs = set()

    for idx, fp in enumerate(valid_files):
        if multi:
            print(f"\n{'━'*60}")
            print(f"  📁 [{idx+1}/{len(valid_files)}] {Path(fp).name}")
            print(f"{'━'*60}")

        # 폴더 + 제목 + 파일 prefix 결정
        if multi and base_title:
            # 다중+제목 → 공용 폴더, prefix로 구분
            out_dir = shared_out_dir
            file_prefix = f"{idx+1:02d}_{sanitize_filename(Path(fp).stem)}_"
            doc_title = f"{base_title} ({idx+1})"
        elif multi:
            # 다중+제목없음 → 파일별 별도 폴더
            out_dir = make_output_dir(args.output_dir, Path(fp).stem)
            file_prefix = ""
            doc_title = Path(fp).stem
        else:
            # 단일 파일
            out_dir = make_output_dir(args.output_dir, base_title)
            # resume 시 기존 폴더 사용
            if args.resume:
                found = find_existing_output_dir(args.output_dir, base_title)
                if found:
                    out_dir = found
                    info(f"기존 폴더 재사용: {out_dir}")
            file_prefix = ""
            doc_title = base_title

        all_out_dirs.add(out_dir)
        # 로그가 아직 이 폴더에 없으면 설정
        if out_dir != shared_out_dir and not shared_out_dir:
            log_path = setup_logging(out_dir, args.debug)

        try:
            process_single(fp, args, llm, out_dir, doc_title, work_dir, file_prefix)
            success += 1
        except Exception as e:
            fail += 1
            err(f"실패: {Path(fp).name} — {type(e).__name__}: {e}")
            logger.error(f"파일 실패: {fp}\n{traceback.format_exc()}")
            err_str = str(e)
            if "SSL" in err_str or "CERTIFICATE" in err_str or "Connection error" in err_str:
                print(f"  🔒 SSL 문제 → --ssl-no-verify 시도")
            print(f"  💡 로그: {log_path}")

    # ── 완료 ──
    total_time = time.time() - pipeline_start
    print(f"\n{'#'*60}")
    print(f"  🎉 완료!  ({total_time:.1f}초)")
    if multi:
        print(f"  ✅ 성공: {success}개  |  ❌ 실패: {fail}개")
    print(f"  📊 {llm.stats()}")
    print(f"{'#'*60}")

    for out_dir in sorted(all_out_dirs):
        out_files = sorted(Path(out_dir).glob("*"))
        out_files = [f for f in out_files if f.is_file() and f.name != "run.log"]
        if out_files:
            print(f"\n  📂 {out_dir}/")
            for fp in out_files:
                print(f"    {fp.name:<45s} ({file_mb(str(fp)):.2f} MB)")

    if fail:
        print(f"\n  ⚠️  실패 파일은 --resume 으로 이어서 처리 가능")
    print()


if __name__ == "__main__":
    main()
