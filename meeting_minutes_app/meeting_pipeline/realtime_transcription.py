#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
============================================================
 실시간 회의 녹취 + 회의록 자동 생성
============================================================
 기능:
   - 마이크 실시간 캡처 → OpenAI STT → 단어별 스트리밍 출력
   - 영어(en): 실시간 STT + 선택적 한국어 번역
   - 한국어(ko): 실시간 STT → 종료 후 한국어 회의록 생성
   - 회의 주제 입력 (--topic) → 번역·회의록·요약 프롬프트에 맥락 반영
   - JSONL 세션 로그 → 비정상 종료 시 데이터 보존
   - 오디오 백업 PCM (크래시 시 ffmpeg 로 WAV 복원 가능)
   - 이전 세션 이어붙이기 (--prev-session)
   - 완료 후 회의록·요약본 이메일 자동 발송

 출력 파일 (세션 종료 후):
   *_minutes.md          — 상세 회의록
   *_summary.md/.txt     — 요약본 (md + txt 이중 저장, 이메일에 txt 첨부)
   *_transcript.txt      — 타임스탬프 전사 원문
   *_refined_script.txt  — 맥락 기반 교정 스크립트 (오탈자·고유명사 수정)

 사전 준비:
   pip install sounddevice numpy

 사용법:
   python run_meeting.py realtime-raw               # 영어 STT
   python run_meeting.py realtime-raw --translate   # 영어 → 한국어 번역
   python run_meeting.py realtime-raw --language ko # 한국어 STT
   python run_meeting.py realtime-raw --topic "Q1 정기회의"  # 주제 지정
   python run_meeting.py realtime-raw --recover output/session_20250220_143022.jsonl
   python run_meeting.py realtime-raw --ssl-no-verify

 오디오 백업 복원 (크래시 후):
   ffmpeg -f s16le -ar 16000 -ac 1 -i session_TIMESTAMP_audio.pcm output.wav
============================================================
"""

import atexit
import os
import sys
import io
import json
import queue
import smtplib
import threading
import argparse
from concurrent.futures import ThreadPoolExecutor
import time
import wave
from datetime import datetime
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

# ── 의존성 체크 ──────────────────────────────
try:
    import numpy as np
except ImportError:
    print("❌ numpy 미설치: pip install numpy")
    sys.exit(1)

try:
    import sounddevice as sd
except ImportError:
    print("❌ sounddevice 미설치: pip install sounddevice")
    sys.exit(1)

# ── config_loader ─────────────────────────────
try:
    from meeting_minutes_app.common import config_loader as _cfg_mod
    _cfg_ok = True
except ImportError:
    _cfg_mod = None  # type: ignore
    _cfg_ok = False


def _c(key: str, default=None):
    return _cfg_mod.get(key, default) if _cfg_ok else default


from meeting_minutes_app.common.text_filters import (
    collapse_repetitions as _collapse_repetitions,
    is_cjk_hallucination as _is_cjk_hallucination,
    is_near_duplicate as _is_near_duplicate,
    is_script_mismatch as _is_script_mismatch,
    mark_suspect as _mark_suspect,
)
from meeting_minutes_app.common.realtime_ws_session import (
    build_ws_session_config,
    resolve_session_language as _resolve_session_language,
)


def _make_cli_finalize_events(output_dir: str, stem: str, labels: Dict[str, str],
                              header):
    """finalize.run_post_session 산출물 → output 폴더 파일 저장.

    _generate_output(실시간 종료)과 cmd_recover(세션 복구)가 공용으로 사용.
    (과거 두 함수 + _apply_wiki_quality_loop에 복사돼 있던 저장/후처리 로직을
    meeting_pipeline/finalize.py 오케스트레이터로 통합하면서 도입.)

    header: 문자열 또는 **무인자 함수**. 녹취 출처 주석은 저장 시점에 평가해야
    실제 사용 모델(llm.models_used)이 담긴다 — 미리 만들면 비어 있다.
    """
    from meeting_minutes_app.meeting_pipeline.finalize import FinalizeEvents

    def _hdr() -> str:
        return header() if callable(header) else (header or "")

    class _CliEvents(FinalizeEvents):
        def on_status(self, stage, message):
            print(f"  {message}")

        def on_document(self, dtype, content, fmt="markdown"):
            try:
                if dtype == "refined_script":
                    save(content, os.path.join(output_dir, f"{stem}_refined_script.txt"),
                         "교정 스크립트")
                elif dtype == "minutes":
                    save(_hdr() + content, os.path.join(output_dir, f"{stem}_minutes.md"),
                         labels["title"])
                elif dtype == "actions":
                    save(content, os.path.join(output_dir, f"{stem}_actions.json"),
                         "액션 아이템(JSON)")
                elif dtype == "fact_check":
                    save(content, os.path.join(output_dir, f"{stem}_fact_check.md"),
                         "사실 검증")
                elif dtype == "summary":
                    # 요약본에도 붙인다 — 메일 첨부로 이것만 받아 보는 경우가 있는데
                    # 예전엔 minutes.md 에만 있어 출처를 확인할 수 없었다.
                    save(_hdr() + content,
                         os.path.join(output_dir, f"{stem}_summary.md"), "요약본(md)")
                    save(content, os.path.join(output_dir, f"{stem}_summary.txt"), "요약본(txt)")
                # script/transcript는 호출자가 직접 저장 (회의록 실패 시에도 보존),
                # wiki_context/wiki_proposal은 finalize가 artifacts_dir에 직접 저장
            except Exception as e:
                print(f"  {C_YELLOW}{dtype} 저장 실패 (무시): {e}{C_RESET}")

        def on_stage_error(self, stage, exc):
            print(f"  {C_YELLOW}[{stage}] 실패 (무시): {exc}{C_RESET}")

    return _CliEvents()


# ── meeting_minutes 모듈 임포트 ──────────────
_this_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _this_dir)
try:
    from meeting_minutes_app.meeting_pipeline.meeting_minutes import (
        OPENAI_API_KEY, SSL_VERIFY, get_api_key, LLMClient, TYPE_LABELS,
    )
    from meeting_minutes_app.common.llm_client import make_openai_client
    from meeting_minutes_app.meeting_pipeline.minutes_generation import (
        save, infer_speaker_names,
    )
    from meeting_minutes_app.meeting_pipeline.script_formatting import build_script_md
    from meeting_minutes_app.meeting_pipeline.stt import _parse_diarized
    from meeting_minutes_app.meeting_pipeline import meeting_minutes as _mm
    from meeting_minutes_app.meeting_pipeline import publish as _publish
except (ImportError, Exception) as e:
    import traceback as _tb
    print(f"❌ meeting_minutes.py 임포트 실패: {e}")
    _tb.print_exc()
    print("   meeting_minutes.py 와 같은 폴더에서 실행하세요.")
    print("   또는: python -m pip install -r requirements.txt")
    sys.exit(1)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  상수 / 설정 (실제 값은 config.json 에서 로드)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SAMPLE_RATE  = 16000
WS_SAMPLE_RATE = 24000     # Realtime API: 24kHz PCM16 필수
CHANNELS     = 1
WORD_DELAY   = 0.0        # 단어별 출력 딜레이 (0 = 즉시 출력)

ACTIVE_SESSION_FILENAME = ".active_session"   # output 폴더 내 상태 파일

STT_MODELS = [
    "gpt-4o-mini-transcribe",            # 기본: 저렴·빠름  $0.003/min
    "gpt-4o-mini-transcribe-2025-12-15",
    "gpt-4o-transcribe",                 # 고품질           $0.006/min
    "gpt-4o-transcribe-diarize",         # 화자분리          $0.006/min
    "whisper-1",                         # 구버전            $0.006/min
]

DEFAULT_STT_MODEL       = _c("models.stt",            "gpt-4o-mini-transcribe") or "gpt-4o-mini-transcribe"
DEFAULT_TRANSLATE_MODEL = _c("models.translate_model", "gpt-4o-mini") or "gpt-4o-mini"


def _refresh_config_globals() -> None:
    """config_loader.reload() 훅 — 웹 UI 설정 저장 시 재시작 없이
    모델/키/SSL 전역을 갱신한다(meeting_minutes 훅이 먼저 실행됨)."""
    global DEFAULT_STT_MODEL, DEFAULT_TRANSLATE_MODEL, OPENAI_API_KEY, SSL_VERIFY
    DEFAULT_STT_MODEL       = _c("models.stt",            "gpt-4o-mini-transcribe") or "gpt-4o-mini-transcribe"
    DEFAULT_TRANSLATE_MODEL = _c("models.translate_model", "gpt-4o-mini") or "gpt-4o-mini"
    OPENAI_API_KEY = _mm.OPENAI_API_KEY
    SSL_VERIFY = _mm.SSL_VERIFY


if _cfg_ok:
    _cfg_mod.on_reload(_refresh_config_globals)

# 비용 단가 — common/pricing.py 단일 소스 사용 (과거 이 파일에만 2벌 복사돼 있었음)
from meeting_minutes_app.common.pricing import (
    LLM_TOKEN_PRICE as _LLM_TOKEN_PRICE,
    stt_rate_per_min as _stt_rate_per_min,
    MINUTES_COST_PER_SESSION as _MINUTES_COST_PER_SESSION,
    TRANSLATE_COST_PER_MIN as _TRANSLATE_COST_PER_MIN,
)

C_CYAN   = "\033[36m"
C_YELLOW = "\033[33m"
C_GREEN  = "\033[32m"
C_RED    = "\033[31m"
C_GRAY   = "\033[90m"
C_RESET  = "\033[0m"
C_BOLD   = "\033[1m"

# 상단 고정 헤더 줄 수 (row 1: 상태, row 2: 구분선)
_HEADER_LINES = 2

# Windows 터미널 ANSI 가상 시퀀스 처리 활성화
if sys.platform == "win32":
    try:
        import ctypes as _ctypes
        _k32 = _ctypes.windll.kernel32
        _stdout_handle = _k32.GetStdHandle(-11)   # STD_OUTPUT_HANDLE
        _mode = _ctypes.c_ulong()
        _k32.GetConsoleMode(_stdout_handle, _ctypes.byref(_mode))
        _k32.SetConsoleMode(_stdout_handle, _mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  atexit 크래시 안전망 — 열린 로거/백업을 정리
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_loggers_to_cleanup: List["SessionLogger"] = []
_backups_to_cleanup: List["AudioBackup"] = []


def _atexit_handler():
    """비정상 종료 시 JSONL footer 기록 + 활성 세션 마커 보존."""
    for logger in _loggers_to_cleanup[:]:
        try:
            if logger._file is not None:
                # completed=False → .active_session 유지 (복구 감지용)
                logger.close(completed=False)
        except Exception:
            pass
    for backup in _backups_to_cleanup[:]:
        try:
            # 크래시: PCM 그대로 보존 (WAV 변환 X)
            backup.close(convert_to_wav=False)
        except Exception:
            pass


atexit.register(_atexit_handler)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  비용 추정
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def estimate_cost(stt_model: str, translate: bool, translate_model: str) -> Dict[str, float]:
    """시간당 대략 비용($) — 세션 헤더·인디케이터 표시 전용(한도 판정에는 쓰지 않는다).

    단가 조회는 common/pricing 로 수렴한다. 과거엔 STT($/분)와 LLM($/1M토큰) 표를 하나로
    합친 dict 를 써서, 단위가 다른 두 공간이 섞여 있었다(모델명이 양쪽에 다 있으면 float 를
    구독하려다 터진다). 회의록 단가도 gpt-4o 를 하드코딩해 Claude 설정 사용자에게는 늘
    gpt-4o 값을 보여줬다 — 웹(batch/realtime API)은 이미 실제 모델 단가를 쓰므로 같은
    세션에서 CLI 와 웹이 다른 값을 표시했다.
    """
    from meeting_minutes_app.common import pricing as _pricing
    stt_cost = _stt_rate_per_min(stt_model) * 60
    translate_cost = 0.0
    if translate and translate_model in _LLM_TOKEN_PRICE:
        tpm = _LLM_TOKEN_PRICE[translate_model]
        tokens_hr = int(130 * 60 * 1.33)
        # 주의: 이 토큰 기반 계산과 파일 내 _TRANS_PRICE_PER_MIN($/분, 세션 메타 기록용)은
        # 서로 다른 두 벌이다. 어느 쪽이 실제 청구액에 가까운지 실측 근거가 없어
        # 통일하지 않는다(근거 없이 상수·계산식을 바꾸지 않는다는 기존 원칙).
        translate_cost = (tokens_hr / 1_000_000) * (tpm["in"] + tpm["out"])
    # 회의록 생성 단가는 실제 설정 모델(gpt/claude)을 반영 — pricing 이 단일 소스다.
    # 어떤 모델이 회의록을 쓰는지 해석하는 규칙도 pricing.current_models 하나만 쓴다
    # (그 해석이 이 파일에 또 복사되면 웹과 다시 갈라진다).
    if _cfg_ok:
        _m = _pricing.current_models(_cfg_mod)
        minutes_cost = _pricing.minutes_cost(_m["llm"], _m["minutes_model"])
    else:
        minutes_cost = _MINUTES_COST_PER_SESSION
    return {
        "stt":       round(stt_cost, 4),
        "translate": round(translate_cost, 4),
        "minutes":   round(minutes_cost, 4),
        "total":     round(stt_cost + translate_cost + minutes_cost, 4),
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  이메일 발송
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _build_email_attachment(fpath: str):
    """메일 클라이언트 한글 깨짐을 줄이기 위해 .md는 UTF-8 .txt로 첨부."""
    if not fpath or not os.path.isfile(fpath):
        return None
    path = Path(fpath)
    if path.suffix.lower() == ".md":
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        payload = ("\ufeff" + text).encode("utf-8")
        part = MIMEBase("text", "plain", charset="utf-8")
        part.set_payload(payload)
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", "attachment", filename=path.with_suffix(".txt").name)
        return part
    with open(path, "rb") as f:
        part = MIMEBase("application", "octet-stream")
        part.set_payload(f.read())
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", "attachment", filename=path.name)
    return part


def send_email_report(
    recipient: str,
    sender: str,
    password: str,
    subject: str,
    body: str,
    attachments: Optional[List[str]] = None,
) -> bool:
    """회의록·요약본 이메일 발송. 성공 시 True 반환."""
    if not password:
        print(f"  {C_YELLOW}이메일 비밀번호 미설정 → 발송 건너뜀{C_RESET}")
        print(f"  {C_GRAY}config.json 의 email.password 에 앱 비밀번호를 입력하세요.{C_RESET}")
        return False

    domain = sender.split("@")[-1] if "@" in sender else ""
    if "gmail" in domain:
        smtp_host, smtp_port = "smtp.gmail.com", 587
    elif "naver" in domain:
        smtp_host, smtp_port = "smtp.naver.com", 587
    else:
        smtp_host, smtp_port = f"smtp.{domain}", 587

    msg = MIMEMultipart()
    msg["From"]    = sender
    msg["To"]      = recipient
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    for fpath in (attachments or []):
        part = _build_email_attachment(fpath)
        if part is not None:
            msg.attach(part)

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
            server.ehlo()
            server.starttls()
            server.login(sender, password)
            server.sendmail(sender, recipient, msg.as_string())
        return True
    except Exception as e:
        print(f"  {C_RED}이메일 발송 실패: {e}{C_RESET}")
        return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  이메일 설정 로더 (환경변수 > config.json)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _get_email_cfg(args=None) -> tuple:
    """(sender, password, recipient) — 환경변수 > config.json 순"""
    sender    = os.environ.get("EMAIL_SENDER")    or _c("email.sender",    "")
    password  = os.environ.get("EMAIL_PASSWORD")  or _c("email.password",  "")
    recipient = os.environ.get("EMAIL_RECIPIENT") or _c("email.recipient", "")
    return (sender or ""), (password or ""), (recipient or "")


def _send_report_email(stem: str, summary_text: str, attach_paths: List[str],
                       args=None):
    sender, password, recipient = _get_email_cfg(args)
    if not sender or not recipient:
        return
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    subject  = f"[회의록] {date_str}"
    body     = f"회의록이 생성되었습니다.\n\n---\n{summary_text}\n---\n\n첨부 파일을 확인하세요."
    print(f"\n  이메일 발송 중 → {recipient} ...", end="", flush=True)
    ok = send_email_report(recipient, sender, password, subject, body, attach_paths)
    if ok:
        print(f" {C_GREEN}완료{C_RESET}")
    else:
        print(f" {C_YELLOW}건너뜀{C_RESET}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  SessionLogger  — JSONL 크래시 세이프 로그
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class SessionLogger:
    """
    청크 처리 직후 JSONL 로그에 즉시 기록 (os.fsync).
    비정상 종료 후 → .active_session 파일이 남아있으면 복구 가능.
    """

    def __init__(self, output_dir: str, doc_type: str, translate: bool,
                 stt_model: str, language: str,
                 base_dir: Optional[str] = None,
                 session_ts: Optional[str] = None):
        self.output_dir  = output_dir
        self.doc_type    = doc_type
        self.translate   = translate
        self.stt_model   = stt_model
        self.language    = language
        self.session_ts  = session_ts or datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_path    = os.path.join(output_dir, f"session_{self.session_ts}.jsonl")
        # .active_session 은 베이스 output 폴더에 저장 (bat 파일이 항상 찾을 수 있도록)
        self._active_path = os.path.join(base_dir or output_dir, ACTIVE_SESSION_FILENAME)
        self._file: Optional[Any] = None
        self._lock = threading.Lock()
        self._write_count = 0  # fsync 빈도 제어용

    def open(self):
        os.makedirs(self.output_dir, exist_ok=True)
        self._file = open(self.log_path, "w", encoding="utf-8", buffering=1)
        self._write({
            "type": "header",
            "session_start": datetime.now().isoformat(),
            "doc_type": self.doc_type,
            "translate": self.translate,
            "stt_model": self.stt_model,
            "language": self.language,
        })
        # 활성 세션 마커 (배치파일 크래시 감지용) — fsync 로 전원 차단 대비
        with open(self._active_path, "w", encoding="utf-8") as f:
            f.write(self.log_path + "\n")
            f.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "\n")
            f.flush()
            os.fsync(f.fileno())
        _loggers_to_cleanup.append(self)
        print(f"  {C_GRAY}세션 로그: {self.log_path}{C_RESET}")

    def append(self, segment: Dict):
        self._write({"type": "segment", **segment})

    def close(self, completed: bool = True):
        self._write({
            "type": "footer",
            "session_end": datetime.now().isoformat(),
            "completed": completed,
        })
        if self._file:
            self._file.close()
            self._file = None
        try:
            _loggers_to_cleanup.remove(self)
        except ValueError:
            pass
        if completed:
            try:
                os.remove(self._active_path)
            except OSError:
                pass

    def _write(self, obj: Dict):
        with self._lock:
            if self._file:
                self._file.write(json.dumps(obj, ensure_ascii=False) + "\n")
                self._write_count += 1
                try:
                    self._file.flush()
                    # 매 5번마다 한 번만 fsync — 디스크 I/O로 인한 lock 장시간 보유 방지
                    if self._write_count % 5 == 0:
                        os.fsync(self._file.fileno())
                except Exception:
                    pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  AudioBackup  — 연속 PCM 오디오 백업
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class AudioBackup:
    """
    녹음 중 원시 PCM(int16, 16 kHz, mono)을 별도 파일에 연속 기록.

    STT 오류·크래시 시 남은 PCM 파일을 WAV 로 복원:
      ffmpeg -f s16le -ar 16000 -ac 1 -i session_TS_audio.pcm output.wav

    정상 종료 시 자동으로 WAV 변환 후 PCM 삭제.
    """

    BYTES_PER_SEC = SAMPLE_RATE * 2  # int16 = 2 bytes/sample

    def __init__(self, output_dir: str, session_ts: str,
                 sample_rate: int = SAMPLE_RATE):
        self._pcm_path = os.path.join(output_dir, f"session_{session_ts}_audio.pcm")
        self._sample_rate = sample_rate
        self._queue: queue.Queue = queue.Queue()
        self._stop_ev = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._file: Optional[Any] = None

    @property
    def pcm_path(self) -> str:
        return self._pcm_path

    def open(self):
        self._file = open(self._pcm_path, "wb")
        self._thread = threading.Thread(target=self._writer_loop, daemon=True)
        self._thread.start()
        _backups_to_cleanup.append(self)

    def write(self, float_audio: np.ndarray):
        """오디오 콜백에서 호출 (thread-safe). float32 → int16 변환 후 큐에 추가."""
        int16 = (np.clip(float_audio, -1.0, 1.0) * 32767).astype(np.int16)
        self._queue.put(int16.tobytes())

    def _writer_loop(self):
        while not self._stop_ev.is_set() or not self._queue.empty():
            try:
                data = self._queue.get(timeout=0.5)
                if self._file:
                    self._file.write(data)
            except queue.Empty:
                continue
            except Exception:
                break

    def close(self, convert_to_wav: bool = True) -> Optional[str]:
        """Writer 스레드 종료. convert_to_wav=True 시 PCM → WAV, PCM 삭제."""
        self._stop_ev.set()
        if self._thread:
            self._thread.join(timeout=10)
        if self._file:
            self._file.close()
            self._file = None
        try:
            _backups_to_cleanup.remove(self)
        except ValueError:
            pass

        if not os.path.isfile(self._pcm_path):
            return None
        if os.path.getsize(self._pcm_path) == 0:
            os.remove(self._pcm_path)
            return None

        if convert_to_wav:
            wav_path = self._pcm_path.replace(".pcm", ".wav")
            try:
                with open(self._pcm_path, "rb") as f:
                    pcm_data = f.read()
                with wave.open(wav_path, "wb") as wf:
                    wf.setnchannels(CHANNELS)
                    wf.setsampwidth(2)
                    wf.setframerate(self._sample_rate)
                    wf.writeframes(pcm_data)
                os.remove(self._pcm_path)
                return wav_path
            except Exception as e:
                print(f"  {C_YELLOW}오디오 WAV 변환 실패: {e}{C_RESET}")
                return self._pcm_path

        return self._pcm_path


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  세션 로그 파싱
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def load_session_log(log_path: str):
    """JSONL 로그 파싱 → (doc_type, translate, language, segments)"""
    doc_type  = "meeting"
    translate = False
    language  = "ko"   # 헤더에 언어가 없는 옛 로그의 폴백 — 사내 기본은 한국어
    segments: List[Dict] = []

    with open(log_path, "r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                continue
            t = entry.get("type")
            if t == "header":
                doc_type  = entry.get("doc_type", "meeting")
                translate = entry.get("translate", False)
                language  = entry.get("language", "ko")
            elif t == "segment":
                segments.append({k: v for k, v in entry.items() if k != "type"})

    return doc_type, translate, language, segments


def _merge_segment_lists(base_segs: List[Dict], new_segs: List[Dict]) -> List[Dict]:
    """두 세션 세그먼트를 이어붙이기 (타임스탬프 연속 조정)"""
    if not base_segs:
        return new_segs
    if not new_segs:
        return base_segs
    offset = base_segs[-1]["end"]
    shifted = []
    for s in new_segs:
        ns = s.copy()
        ns["start"] = round(s["start"] + offset, 3)
        ns["end"]   = round(s["end"]   + offset, 3)
        shifted.append(ns)
    return base_segs + shifted


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  복구 명령어
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def cmd_recover(log_path: str, output_dir: str, llm_preferred: str,
                send_email: bool = False, memo_path: Optional[str] = None,
                topic: str = ""):
    if not os.path.isfile(log_path):
        print(f"❌ 파일 없음: {log_path}")
        sys.exit(1)

    print(f"\n{'─'*60}")
    print(f"  세션 복구: {Path(log_path).name}")
    doc_type, translate, language, segments = load_session_log(log_path)
    labels = TYPE_LABELS[doc_type]

    if not segments:
        print("  복구할 세그먼트가 없습니다.")
        sys.exit(1)

    total_s = segments[-1]["end"] - segments[0]["start"]
    mm, ss  = divmod(int(total_s), 60)
    print(f"  타입: {labels['title']} | 세그먼트: {len(segments)}개 | {mm}분 {ss}초")
    print(f"{'─'*60}")

    # 메모 로드
    memo: Optional[str] = None
    if memo_path:
        try:
            memo = Path(memo_path).read_text(encoding="utf-8").strip() or None
            if memo:
                print(f"  메모 반영: {Path(memo_path).name} ({len(memo)}자)")
        except Exception as e:
            print(f"  {C_YELLOW}[메모 로드 실패]{C_RESET} {e}")

    os.makedirs(output_dir, exist_ok=True)
    stem = Path(log_path).stem.replace("session_", "recovered_")
    llm  = LLMClient(preferred=llm_preferred)

    # 파일명에서 타임스탬프 파싱 (session_20260303_145540.jsonl)
    try:
        _ts = Path(log_path).stem.replace("session_", "")
        _parsed = datetime.strptime(_ts, "%Y%m%d_%H%M%S")
        session_dt = _parsed.strftime("%Y년 %m월 %d일 %H:%M")
    except Exception:
        session_dt = ""

    # [공용] 종료 후 파이프라인 — finalize.run_post_session
    # (컨텍스트 주입 → 교정 → 회의록 → 액션 → 사실검증 → 요약 → 발행 → wiki 산출물/registry)
    from meeting_minutes_app.meeting_pipeline import finalize as fz

    _session_inputs = fz.SessionInputs(
        segments=segments,
        title=(topic or f"복구 {session_dt}"),
        topic=topic,
        doc_type=doc_type,
        session_dt=session_dt,
        base_memo=memo,
        source="recover",
        language=language or "",
    )

    def header() -> str:
        """복구 산출물 헤더 — 다른 경로와 같은 렌더러를 쓴다.

        capture_note: recovered 가 provenance 에 들어가므로 '복구본'임이 저절로 드러난다
        (예전에는 이 경로만 `<!-- Recovered: -->` 라는 별도 문구를 썼다)."""
        from meeting_minutes_app.wiki_core.note_builder import render_provenance_comment
        return render_provenance_comment(
            fz._build_provenance(_session_inputs, None, llm),
            generated_at=datetime.now().isoformat(timespec="seconds"),
            extra={"복구원본": log_path},
        )

    events = _make_cli_finalize_events(output_dir, stem, labels, header)
    res = fz.run_post_session(
        _session_inputs,
        fz.FinalizeOptions(
            llm=llm,
            do_refine=False,   # recover는 기존에도 교정 없이 원본 세그먼트로 생성
            artifacts_dir=Path(output_dir),
        ),
        events,
    )
    summary = res.summary
    if res.actions_md:
        save(res.actions_md, os.path.join(output_dir, f"{stem}_actions.md"),
             "액션 아이템(마크다운)")
    minutes_path     = os.path.join(output_dir, f"{stem}_minutes.md")
    summary_txt_path = os.path.join(output_dir, f"{stem}_summary.txt")

    # 화자 구분 포함 스크립트 (script.md)
    script_md = build_script_md(segments)
    script_path = os.path.join(output_dir, f"{stem}_script.md")
    save(script_md, script_path, "스크립트")

    # 번역된 스크립트 (번역 세그먼트가 있을 때)
    has_translation = any(
        s.get("text") != s.get("text_original") and s.get("text_original")
        for s in segments
    )
    if has_translation:
        script_ko_path = os.path.join(output_dir, f"{stem}_script_ko.md")
        script_ko = build_script_md(segments, include_original=True)
        save(script_ko, script_ko_path, "스크립트 (한국어)")

    # 전사 원문 (화자 포함)
    lines = []
    for s in segments:
        sm, ss2 = divmod(int(s["start"]), 60)
        spk = s.get("speaker", "")
        spk_prefix = f" {spk}:" if spk else ""
        orig = s.get("text_original", s["text"])
        ko   = s["text"] if s["text"] != orig else None
        lines.append(f"[{sm:02d}:{ss2:02d}]{spk_prefix} {orig}")
        if ko:
            pad = " " * (8 + len(spk_prefix))
            lines.append(f"{pad}→ {ko}")
    transcript_path = os.path.join(output_dir, f"{stem}_transcript.txt")
    save("\n".join(lines), transcript_path, "전사 원문")

    if send_email:
        attach = [p for p in [minutes_path, summary_txt_path, script_path, transcript_path]
                  if os.path.isfile(p)]
        _send_report_email(stem, summary, attach)

    print(f"\n  완료! → {os.path.abspath(output_dir)}\n")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  AudioRecorder
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class AudioRecorder:
    def __init__(self, chunk_duration: float = 5.0,
                 backup: Optional[AudioBackup] = None,
                 level_cb=None):
        self.chunk_duration = chunk_duration
        self.chunk_samples  = int(SAMPLE_RATE * chunk_duration)
        self.audio_queue: queue.Queue = queue.Queue()
        self._buffer    = np.array([], dtype=np.float32)
        self._lock      = threading.Lock()
        self._stream    = None
        self._backup    = backup
        self._level_cb  = level_cb

    def _callback(self, indata, frames, time_info, status):
        if status:
            print(f"\n  [마이크] {status}", file=sys.stderr, end="")
        with self._lock:
            self._buffer = np.concatenate([self._buffer, indata[:, 0]])
            while len(self._buffer) >= self.chunk_samples:
                chunk = self._buffer[:self.chunk_samples].copy()
                self.audio_queue.put(chunk)
                if self._backup:
                    self._backup.write(chunk)
                self._buffer = self._buffer[self.chunk_samples:]
        if self._level_cb:
            self._level_cb(float(np.sqrt(np.mean(indata[:, 0] ** 2))))

    def start(self):
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="float32",
            callback=self._callback, blocksize=int(SAMPLE_RATE * 0.1),
        )
        self._stream.start()

    def pause(self):
        """마이크 캡처 일시정지. 버퍼 잔여 데이터는 버린다."""
        if self._stream:
            self._stream.stop()
            with self._lock:
                self._buffer = np.array([], dtype=np.float32)

    def resume(self):
        """마이크 캡처 재개."""
        if self._stream:
            with self._lock:
                self._buffer = np.array([], dtype=np.float32)
            self._stream.start()

    def stop(self):
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        with self._lock:
            if len(self._buffer) > int(SAMPLE_RATE * 0.5):
                chunk = self._buffer.copy()
                self.audio_queue.put(chunk)
                if self._backup:
                    self._backup.write(chunk)
            self._buffer = np.array([], dtype=np.float32)

    @staticmethod
    def to_wav_bytes(float_audio: np.ndarray) -> bytes:
        int16 = (np.clip(float_audio, -1.0, 1.0) * 32767).astype(np.int16)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(int16.tobytes())
        buf.seek(0)
        return buf.read()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  VADAudioRecorder  — 침묵 감지 동적 청크
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class VADAudioRecorder:
    """
    webrtcvad 기반 동적 청크 레코더.
    - 발화 중 오디오를 누적
    - 침묵 SILENCE_SEC 초 감지 시 즉시 큐에 전송
    - MAX_CHUNK_SEC 초 초과 시 강제 분할
    - AudioRecorder와 동일한 인터페이스 (audio_queue, start, stop, to_wav_bytes)

    설치: pip install webrtcvad-wheels   (Windows 사전 빌드)
          pip install webrtcvad          (Mac/Linux)
    """
    # 기본값 — config realtime.vad_* 키로 오버라이드 가능
    FRAME_MS      = 30       # webrtcvad 지원: 10 / 20 / 30 ms
    MAX_CHUNK_SEC = 6.0      # 안전 상한 (긴 발화 강제 분할)
    SILENCE_SEC   = 0.5      # 침묵 판단 기준 (초)

    def __init__(self, vad_aggressiveness: Optional[int] = None,
                 backup: Optional["AudioBackup"] = None,
                 level_cb=None):
        import webrtcvad as _wv   # ImportError → 호출자가 처리
        # config 오버라이드 (없으면 클래스 기본값)
        frame_ms = int(_c("realtime.vad_frame_ms", self.FRAME_MS) or self.FRAME_MS)
        if frame_ms not in (10, 20, 30):
            frame_ms = self.FRAME_MS  # webrtcvad 제약
        max_chunk = float(_c("realtime.vad_max_chunk_sec", self.MAX_CHUNK_SEC)
                          or self.MAX_CHUNK_SEC)
        silence = float(_c("realtime.vad_silence_sec", self.SILENCE_SEC)
                        or self.SILENCE_SEC)
        if vad_aggressiveness is None:
            vad_aggressiveness = int(_c("realtime.vad_aggressiveness", 2) or 2)
        vad_aggressiveness = min(max(vad_aggressiveness, 0), 3)

        self.audio_queue: queue.Queue = queue.Queue()
        self._backup     = backup
        self._vad        = _wv.Vad(vad_aggressiveness)  # 0~3, 2 권장
        self._frame_samp = int(SAMPLE_RATE * frame_ms / 1000)  # 480 samples @30ms
        self._max_samp   = int(SAMPLE_RATE * max_chunk)
        self._sil_limit  = int(silence * 1000 / frame_ms)  # 프레임 수

        self._buf: List[np.ndarray] = []   # 누적 float32 프레임
        self._residual  = np.array([], dtype=np.float32)  # 미처리 잔여 샘플
        self._sil_count = 0
        self._has_sp    = False            # 현재 청크에 발화가 있었는지
        self._lock      = threading.Lock()
        self._stream    = None
        self._level_cb  = level_cb

    # ── 내부 ──────────────────────────────────────────
    def _callback(self, indata, frames, time_info, status):
        if status:
            print(f"\n  [마이크] {status}", file=sys.stderr, end="")
        samples = indata[:, 0].copy()
        with self._lock:
            combined = np.concatenate([self._residual, samples])
            offset   = 0
            while offset + self._frame_samp <= len(combined):
                frame = combined[offset: offset + self._frame_samp]
                self._process_frame(frame)
                offset += self._frame_samp
            self._residual = combined[offset:]
        if self._level_cb:
            self._level_cb(float(np.sqrt(np.mean(indata[:, 0] ** 2))))

    def _process_frame(self, frame: np.ndarray):
        """30 ms 프레임을 VAD로 분석 → 침묵/발화 상태 업데이트 → 필요 시 emit."""
        pcm16 = (np.clip(frame, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
        try:
            is_speech = self._vad.is_speech(pcm16, SAMPLE_RATE)
        except Exception:
            is_speech = True   # VAD 실패 시 발화로 간주

        self._buf.append(frame)
        buf_samp = sum(len(f) for f in self._buf)

        if is_speech:
            self._has_sp    = True
            self._sil_count = 0
        elif self._has_sp:
            self._sil_count += 1

        should_emit = (
            (self._has_sp and self._sil_count >= self._sil_limit)  # 침묵 0.5초
            or buf_samp >= self._max_samp                           # 6초 상한
        )
        if should_emit and self._has_sp:
            chunk = np.concatenate(self._buf)
            self.audio_queue.put(chunk)
            if self._backup:
                self._backup.write(chunk)
            self._buf       = []
            self._sil_count = 0
            self._has_sp    = False
        elif not self._has_sp and buf_samp > self._max_samp:
            # 발화 없이 너무 오래됨 → 버퍼 정리
            self._buf = []

    # ── 공개 ──────────────────────────────────────────
    def start(self):
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="float32",
            callback=self._callback, blocksize=self._frame_samp,
        )
        self._stream.start()

    def pause(self):
        """마이크 캡처 일시정지. 버퍼 잔여 데이터는 버린다."""
        if self._stream:
            self._stream.stop()
            with self._lock:
                self._buf       = []
                self._residual  = np.array([], dtype=np.float32)
                self._has_sp    = False
                self._sil_count = 0

    def resume(self):
        """마이크 캡처 재개."""
        if self._stream:
            with self._lock:
                self._buf       = []
                self._residual  = np.array([], dtype=np.float32)
                self._has_sp    = False
                self._sil_count = 0
            self._stream.start()

    def stop(self):
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        with self._lock:
            if self._has_sp and self._buf:
                chunk = np.concatenate(self._buf)
                if len(chunk) > int(SAMPLE_RATE * 0.5):
                    self.audio_queue.put(chunk)
                    if self._backup:
                        self._backup.write(chunk)
            self._buf      = []
            self._residual = np.array([], dtype=np.float32)

    @staticmethod
    def to_wav_bytes(float_audio: np.ndarray) -> bytes:
        return AudioRecorder.to_wav_bytes(float_audio)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  RealtimeTranscriber
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class RealtimeTranscriber:
    """
    오디오 청크 → STT → 단어별 스트리밍 출력 → 번역(en only) → 로그 기록.

    언어별 동작:
      ko: STT만 (한국어 회의록은 종료 후 생성)
      en + translate=False: 영어 STT → 영어 출력
      en + translate=True : 영어 STT → 실시간 한국어 번역 출력

    STT 오류 시 최대 3회 재시도 (1초, 2초 간격).
    """

    def __init__(
        self,
        openai_client,
        stt_model: str       = DEFAULT_STT_MODEL,
        language: str        = "en",
        translate: bool      = False,
        translate_model: str = DEFAULT_TRANSLATE_MODEL,
        logger: Optional[SessionLogger] = None,
        indicator: Optional["RecordingIndicator"] = None,
        topic: str           = "",
        vault_searcher       = None,
    ):
        self.client          = openai_client
        self.stt_model       = stt_model
        # 언어 고정 — auto 면 청크마다 언어가 재판정돼 무음·잡음 구간이 엉뚱한
        # 언어로 환각된다(공유 정책: realtime_ws_session.resolve_session_language).
        self.language        = _resolve_session_language(language, _c)
        self.translate       = translate and (language == "en")  # 영어일 때만 실시간 번역
        self.translate_model = translate_model
        self.logger          = logger
        self._indicator      = indicator
        self.topic           = topic
        self.vault_searcher  = vault_searcher  # 실시간 vault 검색 (논블로킹, 선택)
        self.segments: List[Dict] = []
        self._session_start = time.time()
        self._use_diarize   = "diarize" in stt_model
        self._groq_cached   = None   # (client, model) 지연 생성 캐시 — _groq_fallback()
        self._stt_client_cached = None   # STT 전용 클라이언트 캐시 — _stt_client()
        # STT 호출이 실패해 폐기한 청크 수 — 종료 시 "전사된 내용이 없다"의 원인을
        # 마이크 문제와 구분해 안내하기 위해 센다(_generate_output 참조).
        self._stt_error_chunks = 0
        # 예외 없이 빈 텍스트만 돌아온 청크 수. VAD가 발화로 판정한 청크만 STT로 가지만
        # (AudioRecorder._process_frame) VAD도 잡음을 발화로 볼 수 있어, 이 수만으로
        # 원인을 단정하지 않는다 — 세그먼트 0으로 끝난 세션의 안내에만 쓴다.
        self._stt_empty_chunks = 0
        # 실제로 전사를 만든 (제공자, 모델) — 등장 순서 유지. 청크마다 다른 제공자로
        # 폴백될 수 있어 단일 값이 아니다. 회의록의 녹취 출처 메타가 이 값을 쓴다:
        # 설정값 모델을 적으면 폴백이 일어난 회의에 틀린 감사 기록이 남는다.
        # **인스턴스 속성** — 전역이면 동시 세션에서 섞인다.
        self._stt_models_used: List[Tuple[str, str]] = []
        # 번역을 STT와 병렬 실행하기 위한 스레드 풀
        self._translator_pool = ThreadPoolExecutor(max_workers=2)

    def _note_stt_model(self, provider: str, model: str) -> None:
        if provider and (provider, model) not in self._stt_models_used:
            self._stt_models_used.append((provider, model))

    def stt_usage(self) -> Dict[str, Any]:
        """finalize.SessionInputs 에 넣을 실측 STT 메타."""
        used = list(self._stt_models_used)
        primary = ("OpenAI", self.stt_model)
        return {
            "stt_providers": [p for p, _ in used],
            "stt_models": [m for _, m in used],
            "stt_fallback_used": bool(used) and any(u != primary for u in used),
        }

    def _run_stt(self, wav_bytes: bytes):
        """STT API 호출. diarize 모델이면 List[Dict] (화자+텍스트), 아니면 str 반환.

        요청 파라미터는 배치·웹과 같은 단일 소스(stt.stt_request_params)를 쓴다 —
        과거엔 이 메서드가 response_format 을 따로 정해, 같은 Groq 폴백을 웹은
        verbose_json 으로 CLI 는 json 으로 부르는 이원화가 있었다.
        재시도 정책만 라이브 고유다(같은 모델 3회 → OpenAI 폴백모델 → Groq): 라이브는
        순간적인 오류에서 빠르게 회복하는 것이 모델을 바꾸는 것보다 낫기 때문에 같은
        모델을 먼저 3회 두드린다. 로컬은 라이브에 쓰지 않는다(아래 주석 참고)."""
        from meeting_minutes_app.meeting_pipeline import stt as _stt
        params, kind = _stt.stt_request_params(
            "OpenAI", self.stt_model, self.language)

        last_err: Optional[Exception] = None
        for attempt in range(3):
            try:
                out = self._call_stt(self._stt_client(), params, wav_bytes,
                                     parse_diarized=(kind == "diarized"))
                self._note_stt_model("OpenAI", self.stt_model)
                return out
            except Exception as e:
                last_err = e
                if attempt < 2:
                    wait = 2 ** attempt   # 1초, 2초
                    print(f"\n  {C_YELLOW}[STT 재시도 {attempt + 1}/3]{C_RESET} {e}",
                          file=sys.stderr)
                    time.sleep(wait)

        # 같은 모델 3회가 모두 실패했으면 모델 자체가 문제일 수 있다(계정에서 사용 불가·
        # 폐기 등). 웹 라이브(realtime.py)와 같이 OpenAI 폴백 모델을 1회 시도한다 —
        # 이 단계가 없어서 Groq 키가 없는 사용자(대부분)는 청크가 그대로 폐기됐다.
        fb = _stt.FALLBACK_STT_MODEL
        if fb and fb != self.stt_model:
            print(f"\n  {C_YELLOW}[STT 폴백]{C_RESET} {self.stt_model} 실패 → OpenAI/{fb}",
                  file=sys.stderr)
            try:
                fparams, fkind = _stt.stt_request_params("OpenAI", fb, self.language)
                out = self._call_stt(self._stt_client(), fparams, wav_bytes,
                                     parse_diarized=(fkind == "diarized"))
                self._note_stt_model("OpenAI", fb)
                return out
            except Exception as fe:
                print(f"\n  {C_RED}[STT 폴백 실패]{C_RESET} OpenAI/{fb}: {fe}",
                      file=sys.stderr)

        # OpenAI 가 모두 실패 = 벤더 장애일 수 있으므로 다른 벤더(Groq)로 1회 재시도.
        # 로컬(faster-whisper)은 라이브 청크에 쓰지 않는다 — CPU 전사가 실시간을 못
        # 따라간다. **CLI 실시간 경로는 종료 후 재전사를 하지 않는다**: Groq 까지 실패한
        # 청크는 그대로 폐기되고, 그 세션은 저장된 백업 WAV 를 `batch` 로 다시 돌려야
        # 로컬까지 포함한 체인(stt.run_stt)을 쓴다(_generate_output 이 그 경로를 안내한다).
        gclient, gmodel = self._groq_fallback()
        if gclient is not None:
            print(f"\n  {C_YELLOW}[STT 폴백]{C_RESET} OpenAI 실패 → Groq/{gmodel}",
                  file=sys.stderr)
            try:
                # Groq 전용 파라미터도 같은 단일 소스가 만든다 — diarize·OpenAI 전용
                # 옵션·prompt 는 거기서 자동으로 빠진다. 화자분리가 없으니 평문만
                # 돌아오고, process() 가 일반 경로로 처리한다.
                gparams, gkind = _stt.stt_request_params("Groq", gmodel, self.language)
                out = self._call_stt(gclient, gparams, wav_bytes,
                                     parse_diarized=(gkind == "diarized"))
                self._note_stt_model("Groq", gmodel)
                return out
            except Exception as ge:
                print(f"\n  {C_RED}[STT 폴백 실패]{C_RESET} Groq: {ge}", file=sys.stderr)

        raise last_err  # type: ignore

    def _stt_client(self):
        """STT 전용 클라이언트 — 세션당 1회 만들어 캐시.

        폴백(폴백모델·Groq)이 있으므로 한 벤더에 오래 매달릴 이유가 없다. SDK 기본값
        (요청 600초 × 재시도 2회)을 그대로 쓰면, _run_stt 의 3회 루프와 곱해져 응답 없이
        매달리는 장애에서 청크 하나가 Groq 에 닿기까지 몇 시간 규모로 막힌다.
        같은 함수의 Groq 클라이언트는 stt.groq_fallback() 이 이미 한도를 넣어 주므로
        여기만 비어 있던 비대칭이었다.

        self.client 자체를 좁히지 않는 이유: 그 객체는 번역과 WS realtime.connect 도
        공유한다 → with_options 로 **사본만** 좁힌다(하위 httpx 클라이언트는 공유)."""
        if getattr(self, "_stt_client_cached", None) is None:
            from meeting_minutes_app.meeting_pipeline import stt as _stt
            try:
                self._stt_client_cached = self.client.with_options(
                    timeout=_stt.STT_REQUEST_TIMEOUT_SEC,
                    max_retries=_stt.STT_MAX_RETRIES,
                )
            except Exception as e:   # 구버전 SDK 등 — 한도 없이라도 동작은 유지
                print(f"\n  {C_YELLOW}[STT]{C_RESET} 요청 한도 적용 실패"
                      f"(SDK 기본값 사용): {e}", file=sys.stderr)
                self._stt_client_cached = self.client
        return self._stt_client_cached

    def _groq_fallback(self):
        """Groq 폴백 (클라이언트, 모델) — 세션당 1회 생성해 캐시. 키 없으면 (None, "")."""
        if getattr(self, "_groq_cached", None) is None:
            from meeting_minutes_app.meeting_pipeline import stt as _stt
            self._groq_cached = _stt.groq_fallback()
        return self._groq_cached

    def _call_stt(self, client, params: Dict[str, Any], wav_bytes: bytes,
                  parse_diarized: bool):
        """전사 API 1회 호출 + 응답 파싱(OpenAI/Groq 공통 — 둘 다 OpenAI 호환 SDK)."""
        audio_file = io.BytesIO(wav_bytes)
        audio_file.name = "chunk.wav"
        resp = client.audio.transcriptions.create(file=audio_file, **params)
        # diarize 모델: 화자 정보 포함 세그먼트 리스트 반환
        if parse_diarized:
            data = resp if isinstance(resp, dict) else (
                resp.model_dump() if hasattr(resp, "model_dump") else json.loads(resp)
            )
            return _parse_diarized(data, 0)  # offset은 process()에서 보정

        if isinstance(resp, dict):
            return resp.get("text", "").strip()
        if hasattr(resp, "text"):
            return resp.text.strip()
        try:
            return json.loads(resp).get("text", "").strip()
        except Exception:
            return ""

    def _clean_text(self, text: str) -> str:
        """STT 환각·반복 방어 (웹 경로와 동일 정책).

        조각 안의 되풀이를 축약하고, 직전 세그먼트와 같은 말이면 버리고(모델이
        문맥을 되풀이한 경우), 회의 언어에 없는 이질 문자는 [불명] 표시만 붙인다.
        """
        if not _c("realtime.hallucination_filter", True):
            return text
        t = _collapse_repetitions(text)
        if not t:
            return ""
        prev = self.segments[-1].get("text", "") if self.segments else ""
        if prev and _is_near_duplicate(t, prev):
            return ""
        if _is_script_mismatch(t, self.language or "ko"):
            t = _mark_suspect(t)
        return t

    def process(self, float_audio: np.ndarray) -> Optional[str]:
        wav = AudioRecorder.to_wav_bytes(float_audio)
        try:
            result = self._run_stt(wav)
        except Exception as e:
            self._stt_error_chunks += 1
            print(f"\n  {C_RED}[STT 오류 - 청크 폐기]{C_RESET} {e}", file=sys.stderr)
            return None

        elapsed = time.time() - self._session_start
        chunk_end = elapsed + len(float_audio) / SAMPLE_RATE

        # diarize 모델: result가 List[Dict] (화자별 세그먼트)
        if self._use_diarize and isinstance(result, list):
            if not result or all(not s.get("text", "").strip() for s in result):
                self._stt_empty_chunks += 1
                return None
            combined_text = ""
            for ds in result:
                txt = ds.get("text", "").strip()
                if not txt or _is_cjk_hallucination(txt):
                    continue
                txt = self._clean_text(txt)
                if not txt:
                    continue
                spk = ds.get("speaker", "")
                mm, ss = divmod(int(elapsed), 60)
                spk_label = f" {spk}:" if spk else ""
                line = f"\n{C_CYAN}[{mm:02d}:{ss:02d}]{C_RESET}{spk_label} {txt}"
                if self._indicator and self._indicator._scroll_locked:
                    self._indicator.buffer_line(line)
                else:
                    if self._indicator:
                        self._indicator.claim()
                    print(line, flush=True)
                    if self._indicator:
                        self._indicator.release(suppress_draw=bool(self.translate))

                seg = {
                    "start":         elapsed,
                    "end":           chunk_end,
                    "text":          txt,
                    "text_original": txt,
                    "speaker":       spk,
                }
                self.segments.append(seg)
                if self.vault_searcher:
                    self.vault_searcher.offer_segment(txt)
                if self._indicator:
                    self._indicator.increment_seg()

                if self.translate:
                    self._translator_pool.submit(self._translate_and_log, txt, seg)
                else:
                    if self.logger:
                        self.logger.append(seg)
                combined_text += (" " if combined_text else "") + txt
            return combined_text or None

        # 일반 모델: result가 str
        text = result if isinstance(result, str) else ""
        if not text.strip():
            # 호출은 성공했는데 내용이 비어 있다. VAD가 발화로 판정한 청크이므로
            # 원인은 저음량이거나 제공자의 조용한 실패다 — 어느 쪽인지는 여기서
            # 단정하지 않고, 세그먼트가 하나도 없이 끝났을 때만 안내에 쓴다.
            self._stt_empty_chunks += 1
            return None
        if _is_cjk_hallucination(text):
            # 환각 필터가 지운 것은 '빈 인식 결과'가 아니다(위 카운터에 넣지 않는다).
            return None
        text = self._clean_text(text)
        if not text:
            return None

        mm, ss  = divmod(int(elapsed), 60)

        # ① 영어 즉시 출력 — indicator 하단 고정 영역과 충돌 방지
        line = f"\n{C_CYAN}[{mm:02d}:{ss:02d}]{C_RESET} {text}"
        if self._indicator and self._indicator._scroll_locked:
            # 스크롤 잠금 중이면 버퍼에 저장 (화면 출력 안 함)
            self._indicator.buffer_line(line)
        else:
            if self._indicator:
                self._indicator.claim()
            print(line, flush=True)
            if self._indicator:
                # 번역 예정이면 인디케이터 그리기 억제 → 영어↔한국어 사이에 인디케이터 끼임 방지
                self._indicator.release(suppress_draw=bool(self.translate))

        if self._indicator:
            self._indicator.increment_seg()

        seg = {
            "start":         elapsed,
            "end":           chunk_end,
            "text":          text,
            "text_original": text,
            "speaker":       "",
        }
        self.segments.append(seg)
        if self.vault_searcher:
            self.vault_searcher.offer_segment(text)

        if self.translate:
            # ② 번역을 백그라운드 스레드에 제출하고 즉시 리턴
            self._translator_pool.submit(self._translate_and_log, text, seg)
        else:
            if self.logger:
                self.logger.append(seg)

        return text

    def _translate_and_log(self, text: str, seg: dict):
        """백그라운드 스레드: 공유 번역 함수 호출."""
        from meeting_minutes_app.meeting_pipeline.ws_transcriber import translate_and_log
        translate_and_log(
            text, seg, self.client,
            self.translate_model, self.logger, self._indicator,
            self.topic,
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  RecordingIndicator
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class RecordingIndicator:
    """
    터미널 UI 레이아웃 관리자.

    Row 1      [고정 헤더]: 제목 · 녹음 시간 · 예상 비용 (매초 갱신)
    Row 2      [고정 구분선]: ─────────────────────
    Row 3~N-1  [스크롤 영역]: 실시간 전사 텍스트
    Row N      [고정 인디케이터]: 녹음 상태 · 명령어 안내

    ANSI 스크롤 영역 (\033[top;botr) 을 이용해 콘텐츠가 위쪽에서만 스크롤되고
    헤더와 인디케이터 줄은 화면에 고정되도록 합니다.

    start() → 헤더 렌더 + 스크롤 영역 설정 → 인디케이터 스레드 기동
    stop()  → 스크롤 영역 복원 → 정리
    claim() → 전사/번역 출력 스레드가 stdout 소유권 획득 (인디케이터 일시 정지)
    release()→ 소유권 반환 (인디케이터 자동 재드로우)

    스크롤 잠금 (s 명령어):
      toggle_scroll_lock() 호출 시 새 전사 텍스트를 버퍼에 쌓고 화면 출력 중단.
      사용자가 위로 자유롭게 스크롤하여 이전 대화를 확인할 수 있음.
      잠금 해제 시 버퍼에 쌓인 내용을 한 번에 출력.
    """
    _FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self):
        self._ev       = threading.Event()
        self._out_lock = threading.Lock()   # stdout 직렬화 (인디케이터 ↔ 콘텐츠)
        self._thread: Optional[threading.Thread] = None
        self._rows     = 24   # 터미널 높이 캐시
        self._paused   = False
        self._level: float = 0.0   # 최신 오디오 RMS (callback 스레드가 갱신)
        self._draw_suppressed = False  # 번역 대기 중 인디케이터 그리기 억제
        # 헤더 정보
        self._title:         str   = ""
        self._emoji:         str   = ""
        self._stt_model:     str   = ""
        self._cost_per_hour: float = 0.0
        self._session_start: float = 0.0
        self._seg_count:     int   = 0
        # 스크롤 잠금
        self._scroll_locked: bool      = False
        self._pending_lines: List[str] = []
        # 헤더 갱신 카운터 (매 5프레임 ≈ 0.6초마다 갱신)
        self._header_tick: int = 0

    def _get_rows(self) -> int:
        try:
            return os.get_terminal_size().lines
        except OSError:
            return 24

    def _get_cols(self) -> int:
        try:
            return os.get_terminal_size().columns
        except OSError:
            return 80

    def update_level(self, rms: float):
        """오디오 콜백 스레드에서 호출 — float 할당은 GIL 하에서 원자적."""
        self._level = rms

    def set_paused(self, paused: bool):
        self._paused = paused

    def increment_seg(self):
        """발화 건수 카운터 증가 (헤더 표시용)."""
        self._seg_count += 1

    def _level_bar(self) -> str:
        filled = min(8, int(self._level * 40))  # 0.2 RMS = 만바 (정상 발화)
        return "▐" + "█" * filled + "░" * (8 - filled) + "▌"

    def _build_header(self, cols: int = 80) -> str:
        """상단 고정 헤더 1줄 텍스트 생성."""
        elapsed = time.time() - self._session_start if self._session_start else 0
        mm, ss  = divmod(int(elapsed), 60)
        hh, mm2 = divmod(mm, 60)
        time_str = f"{hh:02d}:{mm2:02d}:{ss:02d}" if hh else f"{mm2:02d}:{ss:02d}"
        cost_est = elapsed / 3600 * self._cost_per_hour

        scroll_badge = f"  {C_YELLOW}🔒{C_RESET}" if self._scroll_locked else ""
        seg_badge    = f"  {C_GRAY}({self._seg_count}건){C_RESET}" if self._seg_count else ""

        return (
            f" {self._emoji} {C_BOLD}{self._title}{C_RESET}"
            f"  {C_CYAN}⬤ {time_str}{C_RESET}"
            f"  │  ~${cost_est:.3f}"
            f"  │  {C_GRAY}{self._stt_model}{C_RESET}"
            f"{seg_badge}{scroll_badge}"
        )

    def _status_str(self, frame: str) -> str:
        if self._scroll_locked:
            buf_cnt = len(self._pending_lines)
            pending_info = f"(+{buf_cnt}건 대기)  " if buf_cnt else ""
            return (
                f"  {C_YELLOW}🔒 스크롤 잠금{C_RESET}  {pending_info}"
                f"{C_CYAN}s{C_RESET}+Enter → 해제   "
                f"{C_CYAN}q{C_RESET}+Enter → 종료"
            )
        if self._paused:
            return (f"  {C_YELLOW}⏸  일시정지{C_RESET}  "
                    f"{C_CYAN}r{C_RESET}+Enter → 재개   "
                    f"{C_CYAN}q{C_RESET}+Enter → 종료")
        bar = self._level_bar()
        return (f"  {C_GREEN}{frame}{C_RESET} 녹음 중...  {bar}  "
                f"{C_CYAN}q{C_RESET}+Enter → 종료   "
                f"{C_CYAN}p{C_RESET}+Enter → 일시정지   "
                f"{C_YELLOW}s{C_RESET}+Enter → 스크롤잠금")

    def start(self, title: str = "", emoji: str = "",
              stt_model: str = "", cost_per_hour: float = 0.0):
        """UI 시작.

        Args:
            title: 세션 제목 (예: "실시간 회의록 녹취")
            emoji: 타입 이모지 (예: "🤝")
            stt_model: STT 모델명 (헤더 표시용)
            cost_per_hour: 1시간 예상 비용 (헤더 표시용, USD)
        """
        self._title         = title
        self._emoji         = emoji
        self._stt_model     = stt_model
        self._cost_per_hour = cost_per_hour
        self._session_start = time.time()
        self._ev.clear()
        self._rows = self._get_rows()
        cols = self._get_cols()

        # ① 헤더 2줄 초기 렌더
        sys.stdout.write("\033[1;1H\033[2K")        # row 1 이동 + 지우기
        sys.stdout.write(self._build_header(cols))
        sys.stdout.write(f"\033[2;1H\033[2K")       # row 2 이동 + 지우기
        sys.stdout.write("─" * min(cols, 80))
        # ② 스크롤 영역: row (_HEADER_LINES+1) ~ (rows-1)
        #    헤더 2줄 + 인디케이터 1줄이 고정, 나머지가 스크롤
        sys.stdout.write(f"\033[{_HEADER_LINES + 1};{self._rows - 1}r")
        # ③ 커서를 스크롤 영역 하단으로 이동
        sys.stdout.write(f"\033[{self._rows - 1};1H")
        sys.stdout.flush()

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._ev.set()
        if self._thread:
            self._thread.join(timeout=1)
        # 헤더 2줄 + 인디케이터 줄 지우기 + 스크롤 영역 전체 복원
        sys.stdout.write("\033[1;1H\033[2K")              # row 1 지우기
        sys.stdout.write("\033[2;1H\033[2K")              # row 2 지우기
        sys.stdout.write(f"\033[{self._rows};1H\033[2K")  # 인디케이터 줄 클리어
        sys.stdout.write("\033[r")                         # 스크롤 영역 전체 복원
        sys.stdout.write(f"\033[{self._rows};1H\n")       # 커서 아래로
        sys.stdout.flush()

    def claim(self):
        """콘텐츠 출력 스레드가 stdout 소유권 획득.
        인디케이터가 다시 쓰지 않도록 lock을 잡고,
        인디케이터 줄을 비운 뒤 커서를 스크롤 영역 하단으로 옮긴다."""
        self._draw_suppressed = False   # claim 시 suppress 해제
        self._out_lock.acquire()
        # 인디케이터 줄 클리어, 커서를 스크롤 영역 하단으로 이동
        sys.stdout.write(f"\033[{self._rows};1H\033[2K\033[{self._rows - 1};1H")
        sys.stdout.flush()

    def release(self, suppress_draw: bool = False):
        """stdout 소유권 반환.
        suppress_draw=True: 다음 claim() 까지 인디케이터 그리기 억제.
        영어 출력 후 번역이 이어질 때 인디케이터가 사이에 끼는 것을 방지."""
        if suppress_draw:
            self._draw_suppressed = True
        self._out_lock.release()

    def unsuppress_draw(self):
        """번역 실패 등으로 claim() 없이 suppress 해제가 필요할 때 사용."""
        self._draw_suppressed = False

    def buffer_line(self, text: str):
        """스크롤 잠금 중 출력 버퍼에 텍스트 저장."""
        self._pending_lines.append(text)

    def toggle_scroll_lock(self):
        """스크롤 잠금 토글. 잠금 해제 시 버퍼된 내용 일괄 출력."""
        self._scroll_locked = not self._scroll_locked
        if not self._scroll_locked and self._pending_lines:
            with self._out_lock:
                # 인디케이터 줄 비우고 스크롤 영역 하단으로 이동
                sys.stdout.write(
                    f"\033[{self._rows};1H\033[2K\033[{self._rows - 1};1H"
                )
                for line in self._pending_lines:
                    sys.stdout.write(line)
                sys.stdout.flush()
            self._pending_lines.clear()

    def _run(self):
        idx = 0
        while not self._ev.is_set():
            if self._out_lock.acquire(blocking=False):
                if not self._ev.is_set() and not self._draw_suppressed:
                    self._rows = self._get_rows()
                    cols = self._get_cols()
                    f = self._FRAMES[idx % len(self._FRAMES)]

                    # 매 5프레임(약 0.6초)마다 헤더 갱신
                    if self._title and self._header_tick % 5 == 0:
                        sys.stdout.write("\033[s")            # 커서 저장
                        sys.stdout.write("\033[1;1H\033[2K")  # row 1 이동 + 지우기
                        sys.stdout.write(self._build_header(cols))
                        sys.stdout.write("\033[u")            # 커서 복원
                        self._header_tick = 0

                    # 하단 인디케이터 갱신
                    sys.stdout.write(
                        f"\033[{self._rows};1H\033[2K" + self._status_str(f)
                    )
                    sys.stdout.flush()
                    idx += 1
                    self._header_tick += 1
                self._out_lock.release()
            time.sleep(0.12)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  RealtimeSession
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class RealtimeSession:

    # ── 비용 단가 (common/pricing.py 단일 소스) ──
    _TRANS_PRICE_PER_MIN   = _TRANSLATE_COST_PER_MIN
    _MINUTES_COST_FIXED    = _MINUTES_COST_PER_SESSION

    def __init__(self, args):
        self.args          = args
        self.doc_type      = getattr(args, "type", "meeting")
        self.labels        = TYPE_LABELS[self.doc_type]
        self.stt_model     = getattr(args, "model", DEFAULT_STT_MODEL)
        self.language      = getattr(args, "language", "en")
        self.translate     = getattr(args, "translate", False)
        self.translate_model = getattr(args, "translate_model", DEFAULT_TRANSLATE_MODEL)
        self.chunk_dur     = getattr(args, "chunk_duration", 3.0)
        self.use_vad       = getattr(args, "vad", False)
        self.prev_session  = getattr(args, "prev_session", None)
        self.do_email      = getattr(args, "email", False)
        self.topic         = getattr(args, "topic", "")

        # 메모/노트 파일 로드
        self.memo: Optional[str] = None
        _memo_path = getattr(args, "memo", None)
        if _memo_path:
            try:
                _memo_text = Path(_memo_path).read_text(encoding="utf-8").strip()
                self.memo = _memo_text or None
                if self.memo:
                    print(f"  메모 로드: {Path(_memo_path).name} ({len(self.memo)}자)")
            except Exception as e:
                print(f"  {C_YELLOW}[메모 로드 실패]{C_RESET} {e}")
        self._session_start_dt: datetime = datetime.now()
        self._session_end_dt:   Optional[datetime] = None

        # ── 전송 모드 결정 ──
        self.mode = getattr(args, "mode", "http")
        if self.mode == "auto":
            try:
                import websockets  # noqa: F401 — 설치 여부 프로브
                self.mode = "ws"
            except ImportError:
                self.mode = "http"

        key = get_api_key("OPENAI_API_KEY", OPENAI_API_KEY)
        if not key:
            raise RuntimeError(
                "OPENAI_API_KEY 없음.\n"
                "  → config.json 의 api.openai_api_key 또는 환경변수 OPENAI_API_KEY 설정"
            )
        self.openai = make_openai_client(key)
        self.llm    = LLMClient(preferred=getattr(args, "llm", "gpt"))

        # 세션 타임스탬프 먼저 생성 → 세션 서브폴더 경로 결정
        _session_ts           = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._base_output_dir = args.output_dir
        self.output_dir       = os.path.join(self._base_output_dir, f"realtime_{_session_ts}")

        self.logger = SessionLogger(self.output_dir, self.doc_type,
                                    self.translate, self.stt_model, self.language,
                                    base_dir=self._base_output_dir,
                                    session_ts=_session_ts)

        # 오디오 백업 — WS 모드는 24kHz, HTTP 모드는 16kHz
        self._backup: Optional[AudioBackup] = None
        self._audio_backup_path: Optional[str] = None   # 종료 시 변환된 WAV 경로
        _backup_rate = WS_SAMPLE_RATE if self.mode == "ws" else SAMPLE_RATE
        if _c("realtime.audio_backup", True):
            self._backup = AudioBackup(self.output_dir, self.logger.session_ts,
                                       sample_rate=_backup_rate)

        # indicator 먼저 생성 → recorder에 level_cb 전달
        self.indicator = RecordingIndicator()
        self._stop_ev  = threading.Event()
        self._worker: Optional[threading.Thread] = None

        # 실시간 vault 검색 (wiki.realtime_vault_search 게이트, 논블로킹) —
        # 회의 중 관련 노트를 📎 줄로 표시하고 종료 후 회의록 컨텍스트에 병합.
        # 실시간 '웹' 보완은 웹 UI 전용이며 CLI는 내부 노트 검색만 수행한다(FR-12).
        self.vault_searcher = None
        try:
            from meeting_minutes_app.wiki_core.realtime_search import RealtimeVaultSearcher
            _searcher = RealtimeVaultSearcher(
                topic=self.topic,
                on_notes=self._display_related_notes,
                on_status=self._display_search_status)
            # `enabled` 는 이 시점에 게이트만 반영한다(백엔드 판정은 warmup 이후) →
            # 여기서 결과를 단정하지 않고 warmup 의 on_status 콜백이 한 줄로 알린다.
            if _searcher.status().get("gate"):
                self.vault_searcher = _searcher
                _searcher.warmup()   # 인덱스 연결을 미리 확인(논블로킹) → 사유 즉시 안내
            else:
                _st = _searcher.status()
                if _st.get("reasonText"):
                    print(f"  {C_YELLOW}[Wiki]{C_RESET} 실시간 관련 노트 검색 꺼짐 — "
                          f"{C_GRAY}{_st['reasonText']}{C_RESET}")
        except Exception:
            self.vault_searcher = None

        if self.mode == "ws":
            self._init_ws_mode()
        else:
            self._init_http_mode()

    def _init_http_mode(self):
        """HTTP 청크 전송 모드 초기화 (기존 동작)."""
        if self.use_vad:
            try:
                self.recorder = VADAudioRecorder(backup=self._backup,
                                                 level_cb=self.indicator.update_level)
                print(f"  {C_GREEN}[VAD 모드]{C_RESET} 침묵 감지 동적 청크 활성화")
            except ImportError:
                print(f"  {C_YELLOW}[VAD] webrtcvad 미설치 → Standard 모드로 전환{C_RESET}")
                print(f"  {C_GRAY}  설치: pip install webrtcvad-wheels{C_RESET}")
                self.recorder = AudioRecorder(chunk_duration=self.chunk_dur,
                                             backup=self._backup,
                                             level_cb=self.indicator.update_level)
        else:
            self.recorder = AudioRecorder(chunk_duration=self.chunk_dur,
                                         backup=self._backup,
                                         level_cb=self.indicator.update_level)
        self.transcriber = RealtimeTranscriber(
            openai_client=self.openai,
            stt_model=self.stt_model,
            language=self.language,
            translate=self.translate,
            translate_model=self.translate_model,
            logger=self.logger,
            indicator=self.indicator,
            topic=self.topic,
            vault_searcher=self.vault_searcher,
        )

    def _init_ws_mode(self):
        """WebSocket 스트리밍 모드 초기화."""
        try:
            import websockets  # noqa: F401 — 설치 여부 프로브
        except ImportError:
            print(f"  {C_YELLOW}[WS] websockets 미설치 → HTTP 모드로 전환{C_RESET}")
            print(f"  {C_GRAY}  설치: pip install websockets{C_RESET}")
            self.mode = "http"
            self._init_http_mode()
            return

        # WS 지원 모델 판정 — common.realtime_ws_session 공용 규칙
        # (과거 split("-2025") 방식은 2026년 이후 날짜 모델에서 오동작)
        from meeting_minutes_app.common.realtime_ws_session import normalize_ws_model
        _, _ws_reason = normalize_ws_model(self.stt_model)
        if _ws_reason:
            print(f"  {C_YELLOW}[WS] {_ws_reason} → HTTP 모드로 전환{C_RESET}")
            self.mode = "http"
            self._init_http_mode()
            return

        print(f"  {C_GREEN}[WebSocket 모드]{C_RESET} 실시간 스트리밍 활성화 (24kHz)")
        # 실제 연결은 run()에서 컨텍스트 매니저로 열림
        self.recorder = None      # WS 모드에서는 WebSocketAudioStreamer 사용
        self.transcriber = None   # WS 모드에서는 WebSocketTranscriber 사용

    def _print_indicator_safe(self, line: str) -> None:
        """인디케이터와 충돌하지 않게 한 줄 출력 (검색 풀 스레드에서 호출됨)."""
        if self.indicator and self.indicator._scroll_locked:
            self.indicator.buffer_line(line)
            return
        if self.indicator:
            self.indicator.claim()
        print(line, flush=True)
        if self.indicator:
            self.indicator.release()

    def _display_search_status(self, status) -> None:
        """실시간 검색 백엔드 상태를 1회 안내 (FR-1).

        성공/실패 **양쪽 모두** 여기서 알린다. 세션 시작 시점에는 백엔드 연결 여부를
        아직 알 수 없으므로(`_lazy_init` 은 warmup 스레드에서 처리) 시작 로그가
        "활성"을 단정하면 곧이어 "비활성 — 사유" 가 뒤따르는 모순이 생겼다."""
        try:
            if status.get("enabled"):
                backend = {"index": "로컬 인덱스", "rest": "Obsidian REST"}.get(
                    status.get("backend") or "", status.get("backend") or "")
                self._print_indicator_safe(
                    f"\n  {C_GREEN}[Wiki]{C_RESET} 실시간 관련 노트 검색 준비 완료"
                    f"{f' ({backend})' if backend else ''} "
                    f"{C_GRAY}— 내부자료 우선(섹션·논문 노트 포함){C_RESET}")
                return
            reason = status.get("reasonText") or ""
            if not reason:
                return
            self._print_indicator_safe(
                f"\n  {C_YELLOW}[Wiki]{C_RESET} {C_GRAY}관련 노트 검색 비활성 — "
                f"{reason}{C_RESET}")
        except Exception:
            pass

    def _display_related_notes(self, notes):
        """실시간 vault 검색 결과를 터미널에 한 줄로 표시.

        RealtimeVaultSearcher의 검색 풀 스레드에서 호출된다 —
        전사 출력과 같은 claim/release/buffer_line 규율로 직렬화.
        근거 추적(FR-3)을 위해 섹션경로·점수·경로를 함께 보여준다."""
        try:
            parts = []
            for n in notes[:3]:
                title = n.get("title", "")
                if not title:
                    continue
                from meeting_minutes_app.wiki_core.realtime_search import SOURCE_ICON
                icon = SOURCE_ICON.get(n.get("source_type") or "note", "📄")
                heading = n.get("heading") or ""
                label = f"[[{title}#{heading}]]" if heading else f"[[{title}]]"
                score = float(n.get("score", 0) or 0)
                parts.append(f"{icon} {label} ({score:.2f})")
            if not parts:
                return
            titles = " · ".join(parts)
            paths = " | ".join(str(n.get("filename", "")) for n in notes[:3]
                               if n.get("filename"))
            line = f"\n  {C_GRAY}📎 관련: {titles}{C_RESET}"
            if paths:
                line += f"\n     {C_GRAY}↳ {paths}{C_RESET}"
            self._print_indicator_safe(line)
        except Exception:
            pass

    def _worker_loop(self):
        """청크를 순서대로 꺼내 STT → 번역 파이프라인 실행.
        인디케이터는 항상 켜진 상태를 유지하고,
        출력 타이밍은 indicator.claim()/release() 로 직렬화한다."""
        while not self._stop_ev.is_set() or not self.recorder.audio_queue.empty():
            try:
                chunk = self.recorder.audio_queue.get(timeout=0.5)
                self.transcriber.process(chunk)
            except queue.Empty:
                continue
            except Exception as e:
                print(f"\n  [처리 오류] {e}", file=sys.stderr)

    def _print_session_header(self):
        """세션 시작 헤더 출력 (HTTP/WS 공용)."""
        cost = estimate_cost(self.stt_model, self.translate and (self.language == "en"),
                             self.translate_model)
        lang_label = {"en": "영어 (English)", "ko": "한국어", "auto": "자동 감지"}.get(
            self.language, self.language)
        trans_label = (f"ON  → 실시간 한국어 번역 ({self.translate_model})"
                       if (self.translate and self.language == "en")
                       else ("OFF (종료 후 한국어로 회의록 생성)" if self.language == "ko"
                             else "OFF"))
        prev_label = (f"\n  이어붙이기: {Path(self.prev_session).name}"
                      if self.prev_session else "")

        mode_label = {"http": "HTTP 청크", "ws": "WebSocket 스트리밍"}.get(self.mode, self.mode)

        print(f"\n{'═'*60}")
        print(f"  {self.labels['emoji']}  실시간 {self.labels['title']} 녹취")
        print(f"  {'─'*56}")
        print(f"  STT 모델  : {self.stt_model}")
        print(f"  전송 모드 : {mode_label}")
        print(f"  입력 언어 : {lang_label}")
        print(f"  번역      : {trans_label}")
        if self.mode == "http":
            print(f"  청크 간격 : {self.chunk_dur:.0f}초")
        print(f"  오디오 백업: {'ON' if self._backup else 'OFF'}")
        if prev_label:
            print(f"  {prev_label.strip()}")
        print(f"  {'─'*56}")
        print(f"  예상 비용 (1시간): STT ${cost['stt']:.3f}", end="")
        if self.translate and self.language == "en":
            print(f"  번역 ${cost['translate']:.4f}", end="")
        print(f"  회의록 ${cost['minutes']:.3f}  합계 ${cost['total']:.3f}")
        print(f"{'═'*60}")

        try:
            dev = sd.query_devices(kind="input")
            print(f"\n  마이크: {dev['name']}")
        except Exception:
            pass

        print(f"\n  말씀하세요.  q+Enter → 종료  |  p+Enter → 일시정지  |  s+Enter → 스크롤잠금\n")

    def _input_loop(self, streamer):
        """사용자 입력 루프 (HTTP/WS 공용). streamer는 pause()/resume() 을 가진 객체."""
        _paused = False
        try:
            while True:
                cmd = input().strip().lower()
                if cmd in ("q", "quit", "stop", "종료"):
                    break
                elif cmd in ("p", "pause", "일시정지") and not _paused:
                    _paused = True
                    streamer.pause()
                    self.indicator.set_paused(True)
                    self.indicator.claim()
                    print(f"  {C_YELLOW}⏸  일시정지됨.  r + Enter 로 재개하세요.{C_RESET}",
                          flush=True)
                    self.indicator.release()
                elif cmd in ("r", "resume", "재개") and _paused:
                    _paused = False
                    streamer.resume()
                    self.indicator.set_paused(False)
                    self.indicator.claim()
                    print(f"  {C_GREEN}●  녹취 재개.{C_RESET}", flush=True)
                    self.indicator.release()
                elif cmd in ("s", "scroll", "스크롤", "잠금"):
                    self.indicator.toggle_scroll_lock()
                    self.indicator.claim()
                    if self.indicator._scroll_locked:
                        print(
                            f"  {C_YELLOW}🔒 스크롤 잠금.  "
                            f"위로 스크롤하여 이전 대화 확인.  "
                            f"s+Enter → 해제{C_RESET}",
                            flush=True,
                        )
                    else:
                        print(
                            f"  {C_GREEN}🔓 스크롤 잠금 해제. 실시간 표시 재개.{C_RESET}",
                            flush=True,
                        )
                    self.indicator.release()
                elif cmd:
                    self.indicator.claim()
                    if _paused:
                        print(f"  {C_YELLOW}일시정지 중.  r+Enter 재개  |  q+Enter 종료{C_RESET}",
                              flush=True)
                    else:
                        print(
                            f"  {C_YELLOW}종료: q+Enter  |  일시정지: p+Enter  |  "
                            f"스크롤잠금: s+Enter  |  계속 녹음 중...{C_RESET}",
                            flush=True,
                        )
                    self.indicator.release()
                # 빈 Enter는 무시 (실수 방지)
        except KeyboardInterrupt:
            pass

    def run(self):
        # 시작 시 vault 인덱스 재빌드 (indexing.auto_reindex_on_start) —
        # 실시간 vault 검색·종료 후 컨텍스트가 최신 노트를 보도록
        try:
            from meeting_minutes_app.wiki_core.wiki_knowledge import reindex_on_start_if_configured
            reindex_on_start_if_configured()
        except Exception:
            pass
        if self.mode == "ws":
            self._run_ws()
        else:
            self._run_http()

    def _run_http(self):
        """HTTP 청크 전송 모드 실행 (기존 동작)."""
        self._print_session_header()

        os.makedirs(self.output_dir, exist_ok=True)
        self.logger.open()
        if self._backup:
            self._backup.open()
            print(f"  {C_GRAY}오디오 백업: {self._backup.pcm_path}{C_RESET}")

        self.recorder.start()
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()
        _cost_http = estimate_cost(self.stt_model,
                                   self.translate and (self.language == "en"),
                                   self.translate_model)
        self.indicator.start(
            title=f"실시간 {self.labels['title']} 녹취",
            emoji=self.labels["emoji"],
            stt_model=self.stt_model,
            cost_per_hour=_cost_http["total"],
        )

        self._input_loop(self.recorder)
        self._finalize()

    def _run_ws(self):
        """WebSocket 스트리밍 모드 실행."""
        from meeting_minutes_app.meeting_pipeline.ws_transcriber import WebSocketAudioStreamer, WebSocketTranscriber

        self._print_session_header()

        os.makedirs(self.output_dir, exist_ok=True)
        self.logger.open()
        if self._backup:
            self._backup.open()
            print(f"  {C_GRAY}오디오 백업: {self._backup.pcm_path}{C_RESET}")

        # SSL 미검증 시 WebSocket 옵션 설정
        # 주의: additional_headers 는 SDK가 내부에서 설정하므로 중복 전달 금지
        ws_opts: Dict[str, Any] = {}
        if not SSL_VERIFY:
            import ssl as _ssl
            ctx = _ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = _ssl.CERT_NONE
            ws_opts["ssl"] = ctx

        # WS 모델: 날짜 접미사 제거 (공용 규칙)
        from meeting_minutes_app.common.realtime_ws_session import strip_model_date_suffix
        ws_model = strip_model_date_suffix(self.stt_model)

        try:
            conn_mgr = self.openai.realtime.connect(
                model=ws_model,
                websocket_connection_options=ws_opts,
            )
        except Exception as e:
            print(f"  {C_RED}[WS] 연결 생성 실패: {e}{C_RESET}")
            print(f"  {C_YELLOW}HTTP 모드로 전환합니다.{C_RESET}")
            self.mode = "http"
            self._init_http_mode()
            self._run_http()
            return

        try:
            with conn_mgr as conn:
                # 전사 세션 설정
                session_cfg = build_ws_session_config(ws_model, self.language, _c)

                conn.session.update(session=session_cfg)

                # 스트리머 + 트랜스크라이버 생성
                ws_streamer = WebSocketAudioStreamer(
                    connection=conn,
                    backup=self._backup,
                    level_cb=self.indicator.update_level,
                )
                ws_transcriber = WebSocketTranscriber(
                    connection=conn,
                    language=self.language,
                    translate=self.translate,
                    translate_model=self.translate_model,
                    openai_client=self.openai,
                    logger=self.logger,
                    indicator=self.indicator,
                    topic=self.topic,
                    vault_searcher=self.vault_searcher,
                )
                # _finalize()에서 접근하기 위해 저장
                self.transcriber = ws_transcriber

                # 연결 끊김 시 재연결 콜백 — 새 연결 생성 + 세션 설정 + 스트리머 재부착.
                # 교체된 연결/매니저는 홀더에 보관해 종료 시 정리한다.
                _conn_holder: Dict[str, Any] = {"mgr": None, "conn": conn}

                def _ws_reconnect():
                    try:
                        _conn_holder["conn"].close()
                    except Exception:
                        pass
                    mgr2 = self.openai.realtime.connect(
                        model=ws_model, websocket_connection_options=ws_opts)
                    conn2 = mgr2.__enter__()
                    conn2.session.update(session=session_cfg)
                    _conn_holder["mgr"], _conn_holder["conn"] = mgr2, conn2
                    ws_streamer.reattach(conn2)
                    return conn2

                ws_streamer.start()
                event_thread = threading.Thread(
                    target=ws_transcriber.run_event_loop,
                    args=(self._stop_ev, _ws_reconnect),
                    daemon=True,
                    name="ws-event-loop",
                )
                event_thread.start()
                _cost_ws = estimate_cost(self.stt_model,
                                         self.translate and (self.language == "en"),
                                         self.translate_model)
                self.indicator.start(
                    title=f"실시간 {self.labels['title']} 녹취",
                    emoji=self.labels["emoji"],
                    stt_model=self.stt_model,
                    cost_per_hour=_cost_ws["total"],
                )

                self._input_loop(ws_streamer)

                # 종료 처리
                self._stop_ev.set()
                ws_streamer.stop()
                event_thread.join(timeout=30)
                ws_transcriber.shutdown()
                # 재연결로 교체된 연결 정리 (원 연결은 with 블록이 닫음)
                if _conn_holder["mgr"] is not None:
                    try:
                        _conn_holder["mgr"].__exit__(None, None, None)
                    except Exception:
                        pass

        except Exception as e:
            print(f"\n  {C_RED}[WS 오류]{C_RESET} {e}")
            print(f"  {C_YELLOW}HTTP 모드로 전환합니다.{C_RESET}")
            # WS 세션에서 이미 확보한 세그먼트 보존 (과거엔 transcriber 교체로 유실됨)
            _prev_segments = list(self.transcriber.segments) if self.transcriber else []
            self.mode = "http"
            self._init_http_mode()
            if _prev_segments:
                self.transcriber.segments = _prev_segments
                print(f"  {C_GREEN}WS 세그먼트 {len(_prev_segments)}개 보존됨{C_RESET}")
            self._run_http()
            return

        self._finalize_ws()

    def _finalize(self):
        """HTTP 모드 종료 처리."""
        self._session_end_dt = datetime.now()
        self.indicator.stop()
        print(f"\n\n  {'─'*56}")
        print(f"  녹음 종료. 남은 청크 처리 중...", end="", flush=True)
        self._stop_ev.set()
        self.recorder.stop()
        if self._worker:
            self._worker.join(timeout=120)
        # 번역 완료 대기 (pool.shutdown이 회의록 생성 전에 모든 번역 종료를 보장)
        self.transcriber._translator_pool.shutdown(wait=True)
        print(" 완료")

        self._finalize_common()

    def _finalize_ws(self):
        """WebSocket 모드 종료 처리."""
        self._session_end_dt = datetime.now()
        self.indicator.stop()
        print(f"\n\n  {'─'*56}")
        print(f"  녹음 종료.", flush=True)

        self._finalize_common()

    def _finalize_common(self):
        """HTTP/WS 공통 종료 처리 (오디오 백업, 로거, 출력 생성)."""
        # vault 검색 drain — _generate_output()의 collected_titles() 완결성 보장
        if self.vault_searcher is not None:
            self.vault_searcher.shutdown(wait=True)

        # 오디오 백업 WAV 변환 (정상 종료 시)
        if self._backup:
            audio_path = self._backup.close(convert_to_wav=True)
            if audio_path:
                kb = os.path.getsize(audio_path) / 1024
                print(f"  오디오 저장: {Path(audio_path).name}  ({kb:.0f} KB)")
                # 전사가 하나도 안 남았을 때 재처리 경로를 안내하려면 경로가 필요하다.
                self._audio_backup_path = audio_path

        self.logger.close(completed=True)
        self._generate_output()

    def _save_meta(self, meta_path: str, segment_count: int, duration_sec: float):
        """세션 메타데이터 + 비용 추정을 JSON으로 저장."""
        end_dt  = self._session_end_dt or datetime.now()
        dur_min = duration_sec / 60
        stt_cost   = _stt_rate_per_min(self.stt_model) * dur_min
        trans_cost = (self._TRANS_PRICE_PER_MIN * dur_min
                      if (self.translate and self.language == "en") else 0.0)
        total_cost = stt_cost + trans_cost + self._MINUTES_COST_FIXED

        mm, ss = divmod(int(duration_sec), 60)
        meta = {
            "session_ts":       self.logger.session_ts,
            "start_time":       self._session_start_dt.isoformat(timespec="seconds"),
            "end_time":         end_dt.isoformat(timespec="seconds"),
            "duration":         f"{mm}분 {ss}초",
            "duration_sec":     round(duration_sec, 1),
            "language":         self.language,
            "translate":        self.translate and (self.language == "en"),
            "doc_type":         self.doc_type,
            "stt_model":        self.stt_model,
            "translate_model":  self.translate_model if self.translate else None,
            "transport_mode":   self.mode,
            "recording_mode":   "vad" if self.use_vad else ("ws" if self.mode == "ws" else "standard"),
            "chunk_duration_sec": None if self.use_vad else self.chunk_dur,
            "segment_count":    segment_count,
            "cost_estimate_usd": {
                "note":      "추정치 (실제 토큰 기반 청구와 다를 수 있음)",
                "stt":       round(stt_cost, 5),
                "translate": round(trans_cost, 5),
                "minutes_llm": round(self._MINUTES_COST_FIXED, 4),
                "total":     round(total_cost, 4),
            },
        }
        try:
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"  {C_YELLOW}메타데이터 저장 실패: {e}{C_RESET}")

    def _generate_output(self):
        segments = self.transcriber.segments

        # ── 이전 세션 이어붙이기 ──
        if self.prev_session and os.path.isfile(self.prev_session):
            _, _, _, prev_segs = load_session_log(self.prev_session)
            if prev_segs:
                n_prev = len(prev_segs)
                segments = _merge_segment_lists(prev_segs, segments)
                print(f"\n  이전 세션 병합: {n_prev}개 + 현재 {len(self.transcriber.segments)}개"
                      f" = 총 {len(segments)}개 세그먼트")
                prev_active = os.path.join(os.path.dirname(self.prev_session),
                                           ACTIVE_SESSION_FILENAME)
                try:
                    os.remove(prev_active)
                except OSError:
                    pass

        if not segments:
            # 원인이 셋(무발화 / STT 호출 실패 / 호출은 됐지만 빈 결과)인데 과거엔 늘
            # 마이크 문제로 안내해 오진을 유발했다. 폐기된 청크가 있으면 마이크가 아니라
            # 음성 인식 쪽이고, 빈 결과뿐이면 둘 다 가능하므로 단정하지 않는다.
            failed = getattr(self.transcriber, "_stt_error_chunks", 0)
            empty  = getattr(self.transcriber, "_stt_empty_chunks", 0)
            if failed or empty:
                if failed:
                    print(f"\n  전사된 내용이 없습니다 — 마이크 문제가 아니라 음성 인식 호출이"
                          f" {failed}회 실패했습니다.")
                else:
                    # 호출은 성공했는데 내용이 비어서 돌아왔다 — 저음량일 수도, 제공자가
                    # 내용을 주지 않은 것일 수도 있어 한쪽으로 단정하지 않는다.
                    print(f"\n  전사된 내용이 없습니다 — 발화로 감지된 구간 {empty}개가 모두"
                          f" 빈 인식 결과로 돌아왔습니다.")
                    print("  마이크 음량이 매우 낮거나, 음성 인식이 내용을 돌려주지 못한"
                          " 경우입니다(둘 다 확인해 보세요).")
                if self._audio_backup_path:
                    print(f"  녹음은 저장돼 있습니다: {self._audio_backup_path}")
                    print(f"  아래 명령으로 다시 처리하면 Groq·로컬 백업까지 쓰는 전체"
                          f" 폴백 체인이 적용됩니다:")
                    print(f"    python run_meeting.py batch \"{self._audio_backup_path}\"")
                else:
                    print("  오디오 백업이 꺼져 있어 다시 처리할 원본이 없습니다"
                          " (realtime.audio_backup).")
            else:
                print("\n  전사된 내용이 없습니다. 마이크 및 음량을 확인하세요.")
            print(f"  세션 로그 보존: {self.logger.log_path}")
            return

        total_s = segments[-1]["end"] - segments[0]["start"]
        mm, ss  = divmod(int(total_s), 60)
        print(f"\n  총 {len(segments)}개 세그먼트 / {mm}분 {ss}초")

        # 세션 날짜 + 계획 매칭(화자 힌트·사전자료 공통, 1회 탐색)
        try:
            _p0 = datetime.strptime(self.logger.session_ts, "%Y%m%d_%H%M%S")
            session_dt = _p0.strftime("%Y년 %m월 %d일 %H:%M")
        except Exception:
            session_dt = ""
        _plan_match = None
        try:
            _plan_match = _publish._lookup_plan(self.topic or f"realtime_{self.logger.session_ts}", session_dt)
        except Exception:
            _plan_match = None
        _known = (_publish._clean_attendee_names((_plan_match.get("meta") or {}).get("attendees"))
                  if _plan_match else None)
        if _known:
            print(f"  화자 추론 힌트(참석자): {', '.join(_known)}")

        # ── 화자 이름 LLM 추론 (diarize 모델 사용 시) ──
        import re as _re
        unique_spks = {s.get("speaker", "") for s in segments if s.get("speaker")}
        has_generic = any(_re.match(r'[Ss]peaker[\s_]?[A-Za-z0-9]', spk) for spk in unique_spks)
        if has_generic:
            try:
                inferred = infer_speaker_names(segments, self.llm, known_names=_known)
                if inferred:
                    print(f"  화자 추론 결과: {inferred}")
                    for seg in segments:
                        orig = seg.get("speaker", "")
                        if orig in inferred:
                            seg["speaker"] = inferred[orig]
            except Exception as e:
                print(f"  {C_YELLOW}화자 이름 추론 실패 ({e}) → 원본 레이블 유지{C_RESET}")

        stem = f"realtime_{self.logger.session_ts}"

        print(f"\n{'═'*60}")
        print(f"  {self.labels['title']} 생성 중...")

        minutes_path        = os.path.join(self.output_dir, f"{stem}_minutes.md")
        summary_txt_path    = os.path.join(self.output_dir, f"{stem}_summary.txt")
        transcript_path     = os.path.join(self.output_dir, f"{stem}_transcript.txt")
        script_path         = os.path.join(self.output_dir, f"{stem}_script.md")
        summary_text        = ""


        # [공용] 종료 후 파이프라인 — finalize.run_post_session
        # (컨텍스트 주입 → 교정 → 회의록 → 액션 → 사실검증 → 요약 → 발행 → wiki 산출물/registry)
        # 과거 이 자리에 복사돼 있던 개별 스테이지들은 meeting_pipeline/finalize.py로 통합됐다.
        from meeting_minutes_app.meeting_pipeline import finalize as fz

        _session_inputs = fz.SessionInputs(
            segments=segments,
            title=(self.topic or f"실시간 {session_dt}"),
            topic=self.topic,
            doc_type=self.doc_type,
            session_dt=session_dt,
            base_memo=self.memo,
            source="realtime",
            language=self.language or "",
            # WS 모드는 WebSocketTranscriber 라 stt_usage() 가 없다 — 그 경우
            # 빈 값으로 두어 '모르는 것을 적지 않는다'.
            **(self.transcriber.stt_usage()
               if hasattr(self.transcriber, "stt_usage") else {}),
        )

        def header() -> str:
            """볼트 노트 frontmatter 와 **같은 dict** 에서 렌더한다(리터럴 중복 제거).

            저장 시점 평가 — llm.models_used 는 회의록 생성 중에 채워진다."""
            from meeting_minutes_app.wiki_core.note_builder import render_provenance_comment
            extra = {"주제": self.topic} if self.topic else None
            return render_provenance_comment(
                fz._build_provenance(_session_inputs, None, self.llm),
                generated_at=datetime.now().isoformat(timespec="seconds"),
                extra=extra,
            )

        events = _make_cli_finalize_events(self.output_dir, stem, self.labels, header)

        # 실시간 vault 검색 수집분 — 회의록 컨텍스트 + "🔗 관련 노트" 섹션에 병합.
        # (CLI는 웹 SQLite 사이드카를 쓰지 않으므로 누적 저장은 회의록·wiki_context에
        #  남는 것으로 갈음한다 — 회의별 재열람은 웹 UI 상세 화면 담당)
        _rt_titles = (self.vault_searcher.collected_titles()[:10]
                      if self.vault_searcher else [])
        _rt_evidence = (self.vault_searcher.collected_evidence(limit=30)
                        if self.vault_searcher else [])
        if _rt_titles:
            print(f"  실시간 관련 노트 병합: {len(_rt_titles)}개"
                  f" (근거 {len(_rt_evidence)}건)")

        res = fz.run_post_session(
            _session_inputs,
            fz.FinalizeOptions(
                llm=self.llm,
                plan_match=_plan_match,   # 화자추론 단계에서 1회 탐색한 결과 재사용
                artifacts_dir=Path(self.output_dir),
                extra_related_titles=_rt_titles,
                extra_related_evidence=_rt_evidence,
            ),
            events,
        )
        minutes = res.minutes
        summary_text = res.summary
        _pub = res.publish_result
        if res.actions_md:
            save(res.actions_md, os.path.join(self.output_dir, f"{stem}_actions.md"),
                 "액션 아이템(마크다운)")
        if not minutes:
            print(f"  회의록 생성 실패")
            print(f"\n  나중에 복구 가능:")
            print(f"    python run_meeting.py realtime-raw --recover {self.logger.log_path}")

        # 화자 구분 포함 스크립트 (script.md)
        script_md = build_script_md(segments)
        save(script_md, script_path, "스크립트")

        # 번역된 스크립트 (translate 모드일 때)
        has_translation = any(
            s.get("text") != s.get("text_original") and s.get("text_original")
            for s in segments
        )
        if has_translation:
            script_ko_path = os.path.join(self.output_dir, f"{stem}_script_ko.md")
            script_ko = build_script_md(segments, include_original=True)
            save(script_ko, script_ko_path, "스크립트 (한국어)")

        # 전사 원문 (화자 포함)
        lines = []
        for s in segments:
            sm, ss2 = divmod(int(s["start"]), 60)
            spk = s.get("speaker", "")
            spk_prefix = f" {spk}:" if spk else ""
            orig = s.get("text_original", s["text"])
            ko   = s["text"] if s["text"] != orig else None
            lines.append(f"[{sm:02d}:{ss2:02d}]{spk_prefix} {orig}")
            if ko:
                pad = " " * (8 + len(spk_prefix))
                lines.append(f"{pad}→ {ko}")
        save("\n".join(lines), transcript_path, "전사 원문")

        # 메타데이터 저장
        meta_path = os.path.join(self.output_dir, f"{stem}_meta.json")
        self._save_meta(meta_path, len(segments), total_s)

        # 이메일 발송 — minutes.md + summary.txt + script.md + transcript.txt + Obsidian 노트 첨부
        if self.do_email and summary_text:
            attach = [p for p in [minutes_path, summary_txt_path, script_path, transcript_path]
                      if os.path.isfile(p)]
            # Obsidian 노트가 있으면 추가 (vault-relative → 풀 경로 변환)
            _obs_rel = (_pub or {}).get("obsidian_path")
            if _obs_rel:
                _vault_root = _c("obsidian.vault_path", "") or ""
                if not _vault_root:
                    try:
                        from meeting_minutes_app.wiki_core.obsidian import _detect_obsidian_config as _dOC2
                        _vault_root = _dOC2().get("vault_path", "")
                    except Exception:
                        pass
                _obs_abs = os.path.join(_vault_root, str(_obs_rel)) if _vault_root else str(_obs_rel)
                if os.path.isfile(_obs_abs) and _obs_abs not in attach:
                    attach.append(_obs_abs)
            _send_report_email(stem, summary_text, attach, self.args)

        # 완료 요약
        print(f"\n{'═'*60}")
        print(f"  {C_GREEN}{C_BOLD}완료!{C_RESET}  생성된 파일:")
        for fp in sorted(Path(self.output_dir).glob(f"{stem}_*")):
            kb = fp.stat().st_size / 1024
            print(f"    {fp.name:<48s} {kb:5.1f} KB")
        print(f"  세션 로그: session_{self.logger.session_ts}.jsonl")
        print(f"  {C_GRAY}출력 폴더: {os.path.abspath(self.output_dir)}{C_RESET}")
        print(f"{'═'*60}\n")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  main
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def main():
    parser = argparse.ArgumentParser(
        description="실시간 회의 녹취 + 회의록 자동 생성",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
언어별 권장 설정:
  영어 회의  : python run_meeting.py realtime-raw --language en
  영어→한국어: python run_meeting.py realtime-raw --language en --translate
  한국어 회의: python run_meeting.py realtime-raw --language ko

세션 복구 (JSONL 로그에서 회의록 재생성):
  python run_meeting.py realtime-raw --recover output/session_20250220_143022.jsonl

세션 이어붙이기 (이전 세션 + 현재 세션 → 하나의 회의록):
  python run_meeting.py realtime-raw --prev-session output/session_20250220_143022.jsonl

오디오 백업 복원 (크래시 후 PCM → WAV):
  ffmpeg -f s16le -ar 16000 -ac 1 -i output/session_TS_audio.pcm output.wav
""",
    )
    parser.add_argument("--type", default=_c("realtime.type", "meeting"),
                        choices=["meeting", "seminar", "lecture"])
    parser.add_argument("--language", default=_c("realtime.language", "ko"),
                        choices=["en", "ko"],
                        help="입력 언어 (en=영어, ko=한국어)")
    parser.add_argument("--model", default=DEFAULT_STT_MODEL, choices=STT_MODELS)
    parser.add_argument("--translate", action="store_true",
                        default=_c("realtime.translate", False),
                        help="영→한 실시간 번역 (--language en 일 때만 동작)")
    parser.add_argument("--translate-model", default=DEFAULT_TRANSLATE_MODEL,
                        choices=["gpt-4o-mini", "gpt-4o"])
    parser.add_argument("--llm", default=_c("models.llm", "gpt"),
                        choices=["gpt", "claude"])
    parser.add_argument("--chunk-duration", type=float,
                        default=_c("realtime.chunk_duration", 3.0), metavar="SEC")
    parser.add_argument("--vad", action="store_true",
                        help="VAD 동적 청크 (침묵 감지 즉시 전송, webrtcvad 필요)")
    parser.add_argument("--mode", default=_c("realtime.mode", "http"),
                        choices=["http", "ws", "auto"],
                        help="전송 모드 (http=기존 청크, ws=WebSocket 스트리밍, auto=자동)")
    parser.add_argument("--output-dir", default=_c("output_dir", "./output"))
    parser.add_argument("--recover", metavar="LOG_FILE",
                        help="비정상 종료 세션 로그에서 회의록 재생성")
    parser.add_argument("--prev-session", metavar="LOG_FILE",
                        help="이전 세션과 이어붙여 하나의 회의록 생성")
    parser.add_argument("--topic", default="",
                        help="회의/세미나/강의 주제 (번역·회의록·요약 프롬프트에 반영)")
    parser.add_argument("--memo", metavar="FILE",
                        help="메모/노트 파일 경로 (txt, md). 회의록·요약 생성 시 LLM에 반영.")
    parser.add_argument("--email", action="store_true",
                        help="완료 후 회의록·요약본 이메일 발송")
    parser.add_argument("--ssl-no-verify", action="store_true")

    args = parser.parse_args()

    if args.ssl_no_verify:
        _mm.SSL_VERIFY = False

    if args.recover:
        cmd_recover(args.recover, args.output_dir, args.llm,
                    send_email=args.email,
                    memo_path=args.memo,
                    topic=args.topic)
        return

    try:
        RealtimeSession(args).run()
    except KeyboardInterrupt:
        print("\n  중단됨.")
    except Exception as e:
        print(f"\n  오류: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
