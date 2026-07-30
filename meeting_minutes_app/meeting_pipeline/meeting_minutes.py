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

# ──────────────────────────────────────────────
#  config_loader (API 키, 모델, SSL 설정)
# ──────────────────────────────────────────────
try:
    from meeting_minutes_app.common import config_loader as _cfg
    _cfg_ok = True
except ImportError:
    _cfg = None  # type: ignore
    _cfg_ok = False


def _c(key: str, default: Any = None) -> Any:
    """config.json 조회 헬퍼"""
    return _cfg.get(key, default) if _cfg_ok else default


from meeting_minutes_app.common.llm_client import (  # noqa: F401 — 하위 모듈 재노출
    LLMClient, OPENAI_API_KEY, GROQ_API_KEY, SSL_VERIFY, get_api_key,
)


# ──────────────────────────────────────────────
#  상수 / 모델 설정
# ──────────────────────────────────────────────
DEFAULT_STT_MODEL  = _c("models.stt",          "gpt-4o-mini-transcribe") or "gpt-4o-mini-transcribe"
FALLBACK_STT_MODEL = _c("models.stt_fallback", "gpt-4o-transcribe") or "gpt-4o-transcribe"
# STT 폴백 체인 — OpenAI(위 2개) 실패 시 다른 벤더/로컬로 이어진다.
GROQ_STT_MODEL     = _c("models.stt_groq",     "whisper-large-v3-turbo") or "whisper-large-v3-turbo"
LOCAL_STT_ENABLED  = bool(_c("stt.local_fallback", False))
LOCAL_STT_MODEL    = _c("models.stt_local",    "base") or "base"
MINUTES_MODEL      = _c("models.minutes_model", "gpt-4o") or "gpt-4o"
SUMMARY_MODEL      = _c("models.summary_model", "gpt-4o") or "gpt-4o"


def _refresh_config_globals() -> None:
    """config_loader.reload() 훅 — 웹 UI 설정 저장 시 재시작 없이 반영.
    llm_client 훅이 먼저 등록·실행되므로 키/SSL은 갱신된 값을 그대로 복사한다."""
    global DEFAULT_STT_MODEL, FALLBACK_STT_MODEL, MINUTES_MODEL, SUMMARY_MODEL
    global GROQ_STT_MODEL, LOCAL_STT_ENABLED, LOCAL_STT_MODEL
    global OPENAI_API_KEY, GROQ_API_KEY, SSL_VERIFY
    DEFAULT_STT_MODEL  = _c("models.stt",          "gpt-4o-mini-transcribe") or "gpt-4o-mini-transcribe"
    FALLBACK_STT_MODEL = _c("models.stt_fallback", "gpt-4o-transcribe") or "gpt-4o-transcribe"
    GROQ_STT_MODEL     = _c("models.stt_groq",     "whisper-large-v3-turbo") or "whisper-large-v3-turbo"
    LOCAL_STT_ENABLED  = bool(_c("stt.local_fallback", False))
    LOCAL_STT_MODEL    = _c("models.stt_local",    "base") or "base"
    MINUTES_MODEL      = _c("models.minutes_model", "gpt-4o") or "gpt-4o"
    SUMMARY_MODEL      = _c("models.summary_model", "gpt-4o") or "gpt-4o"
    from meeting_minutes_app.common import llm_client as _llm
    OPENAI_API_KEY = _llm.OPENAI_API_KEY
    GROQ_API_KEY = _llm.GROQ_API_KEY
    SSL_VERIFY = _llm.SSL_VERIFY


if _cfg_ok:
    _cfg.on_reload(_refresh_config_globals)

# ffmpeg/ffprobe 경로 — 번들(vendor/ffmpeg) 우선, 없으면 PATH fallback
from meeting_minutes_app.common import app_paths as _app_paths
FFMPEG = _app_paths.get_ffmpeg_path()
FFPROBE = _app_paths.get_ffprobe_path()

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

# API 비용 — common/pricing.py 단일 소스 사용
from meeting_minutes_app.common.pricing import (  # noqa: E402
    STT_PRICE_PER_MIN as COST_PER_MIN,
    LLM_COST_PER_1K_TOKENS,
)

TYPE_LABELS = {
    "meeting": {"title": "회의록",    "event": "회의",   "emoji": "🤝"},
    "seminar": {"title": "세미나 기록", "event": "세미나", "emoji": "🎓"},
    "lecture": {"title": "강의 노트",  "event": "강의",   "emoji": "📚"},
    "memo":    {"title": "메모 정리",  "event": "메모",   "emoji": "📝"},
}

MAX_LLM_CHARS = 80_000


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
            [FFPROBE, "-v", "quiet", "-print_format", "json", "-show_format", p],
        )
        return float(json.loads(r.stdout)["format"]["duration"])
    except Exception:
        return 0.0


def check_ffmpeg() -> bool:
    try:
        run_cmd([FFMPEG, "-version"])
        return True
    except Exception:
        return False


def read_file(p: str) -> str:
    with open(p, "r", encoding="utf-8") as f:
        return f.read()


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
        from meeting_minutes_app.meeting_pipeline.date_utils import parse_session_dt_from_path
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
        # segments.json 보유 폴더 우선 — transcript.md만 있는 폴더는 타임스탬프
        # 없는 형식이면 세그먼트 복원이 실패할 수 있다 (최신 mtime보다 완전한
        # STT 산출물이 우선)
        has_segments = any(d.glob("*segments.json")) or (d / "segments.json").is_file()
        candidates.append((score, 1 if has_segments else 0, d.stat().st_mtime, str(d)))
    candidates.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
    return candidates[0][3] if candidates else None


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


def has_timestamps(segments: List[Dict]) -> bool:
    """세그먼트에 실제 타임스탬프가 있는지 확인 (start != end 이면 있음)."""
    return any(s.get("start", 0) != s.get("end", 0) for s in segments)


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
#  Main
# ──────────────────────────────────────────────
def main():
    # 지연 임포트: 아래 모듈들이 core(meeting_minutes.py)의 로깅/설정 헬퍼를
    # 임포트하므로, 모듈 최상단에서 임포트하면 순환 임포트가 발생한다.
    from meeting_minutes_app.meeting_pipeline.minutes_generation import (
        generate_minutes, generate_summary, save,
    )
    from meeting_minutes_app.meeting_pipeline.publish import (
        enrich_and_publish, _gather_attendees, _collect_notification_artifacts,
        _send_notification,
    )
    from meeting_minutes_app.meeting_pipeline.pipeline import process_single

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
    parser.add_argument("--force-republish", action="store_true",
                        help="같은 녹음의 회의록이 볼트에 이미 있어도 다시 발행(덮어쓰기). "
                             "기본은 중복 생성을 막기 위해 볼트 기록을 건너뛴다")
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
            from meeting_minutes_app.meeting_pipeline.profiles import ProfileManager
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

    # ── 시작 시 vault 인덱스 재빌드 (indexing.auto_reindex_on_start) ──
    try:
        from meeting_minutes_app.wiki_core.wiki_knowledge import reindex_on_start_if_configured
        reindex_on_start_if_configured()
    except Exception as _rie:
        warn(f"시작 시 인덱스 재빌드 실패 (무시): {_rie}")

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
                from meeting_minutes_app.meeting_pipeline.speaker_cache import SpeakerCache
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
            # transcript_md을 원본 발행 때와 동일하게 넘겨야 한다 — classify_meeting_route()가
            # title/topic/script_excerpt로 저장 폴더를 정하는데, 이걸 빠뜨리면 화자 수정
            # 재발행이 원본과 다른 폴더로 분류돼 노트가 두 곳에 중복 생성될 수 있다.
            try:
                from meeting_minutes_app.meeting_pipeline.script_formatting import build_script_md
                enrich_and_publish(
                    title=title, doc_type=args.type, minutes_md=minutes, llm=llm,
                    summary_md=summary,
                    topic=getattr(args, "topic", "") or "",
                    attendees=_gather_attendees(segments),
                    transcript_md=build_script_md(segments),
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
