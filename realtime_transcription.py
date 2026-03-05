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
   python realtime_transcription.py               # 영어 STT
   python realtime_transcription.py --translate   # 영어 → 한국어 번역
   python realtime_transcription.py --language ko # 한국어 STT
   python realtime_transcription.py --topic "Q1 정기회의"  # 주제 지정
   python realtime_transcription.py --recover output/session_20250220_143022.jsonl
   python realtime_transcription.py --ssl-no-verify

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
from typing import Optional, List, Dict, Any

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
    import config_loader as _cfg_mod
    _cfg_ok = True
except ImportError:
    _cfg_mod = None  # type: ignore
    _cfg_ok = False


def _c(key: str, default=None):
    return _cfg_mod.get(key, default) if _cfg_ok else default


# ── meeting_minutes 모듈 임포트 ──────────────
_this_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _this_dir)
try:
    from meeting_minutes import (
        OPENAI_API_KEY, SSL_VERIFY,
        make_openai_client, get_api_key,
        LLMClient, generate_minutes, generate_summary, refine_script,
        save, TYPE_LABELS,
    )
    import meeting_minutes as _mm
except ImportError as e:
    print(f"❌ meeting_minutes.py 임포트 실패: {e}")
    print("   meeting_minutes.py 와 같은 폴더에서 실행하세요.")
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

_PRICING = {
    "whisper-1":                         0.006,
    "gpt-4o-mini-transcribe":            0.003,
    "gpt-4o-mini-transcribe-2025-12-15": 0.003,
    "gpt-4o-transcribe":                 0.006,
    "gpt-4o-transcribe-diarize":         0.006,
    "gpt-4o":      {"in": 2.50,  "out": 10.00},
    "gpt-4o-mini": {"in": 0.15,  "out": 0.60},
}

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
    stt_cost = _PRICING.get(stt_model, 0.006) * 60
    translate_cost = 0.0
    if translate and translate_model in _PRICING:
        tpm = _PRICING[translate_model]
        tokens_hr = int(130 * 60 * 1.33)
        translate_cost = (tokens_hr / 1_000_000) * (tpm["in"] + tpm["out"])
    gpt4o = _PRICING["gpt-4o"]
    minutes_cost = (20_000 / 1_000_000) * gpt4o["in"] + (3_000 / 1_000_000) * gpt4o["out"]
    return {
        "stt":       round(stt_cost, 4),
        "translate": round(translate_cost, 4),
        "minutes":   round(minutes_cost, 4),
        "total":     round(stt_cost + translate_cost + minutes_cost, 4),
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  이메일 발송
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
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
        if not os.path.isfile(fpath):
            continue
        with open(fpath, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        fname = Path(fpath).name
        part.add_header("Content-Disposition", f'attachment; filename="{fname}"')
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
                try:
                    self._file.flush()
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
    language  = "en"
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
                language  = entry.get("language", "en")
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

    minutes = generate_minutes(segments, llm, doc_type, memo=memo,
                               topic=topic, session_dt=session_dt)
    header  = (f"<!-- Recovered: {datetime.now().isoformat()} -->\n"
               f"<!-- Source: {log_path} | Type: {doc_type} -->\n\n")
    minutes_path = os.path.join(output_dir, f"{stem}_minutes.md")
    save(header + minutes, minutes_path, labels["title"])

    summary = generate_summary(minutes, llm, doc_type, topic=topic, session_dt=session_dt)
    summary_path     = os.path.join(output_dir, f"{stem}_summary.md")
    summary_txt_path = os.path.join(output_dir, f"{stem}_summary.txt")
    save(summary, summary_path, "요약본(md)")
    save(summary, summary_txt_path, "요약본(txt)")

    # 전사 원문
    lines = []
    for s in segments:
        sm, ss2 = divmod(int(s["start"]), 60)
        orig = s.get("text_original", s["text"])
        ko   = s["text"] if s["text"] != orig else None
        lines.append(f"[{sm:02d}:{ss2:02d}] {orig}")
        if ko:
            lines.append(f"         → {ko}")
    transcript_path = os.path.join(output_dir, f"{stem}_transcript.txt")
    save("\n".join(lines), transcript_path, "전사 원문")

    if send_email:
        attach = [p for p in [minutes_path, summary_txt_path, transcript_path]
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
    FRAME_MS      = 30       # webrtcvad 지원: 10 / 20 / 30 ms
    MAX_CHUNK_SEC = 6.0      # 안전 상한 (긴 발화 강제 분할)
    SILENCE_SEC   = 0.5      # 침묵 판단 기준 (초)

    def __init__(self, vad_aggressiveness: int = 2,
                 backup: Optional["AudioBackup"] = None,
                 level_cb=None):
        import webrtcvad as _wv   # ImportError → 호출자가 처리
        self.audio_queue: queue.Queue = queue.Queue()
        self._backup     = backup
        self._vad        = _wv.Vad(vad_aggressiveness)  # 0~3, 2 권장
        self._frame_samp = int(SAMPLE_RATE * self.FRAME_MS / 1000)  # 480 samples
        self._max_samp   = int(SAMPLE_RATE * self.MAX_CHUNK_SEC)
        self._sil_limit  = int(self.SILENCE_SEC * 1000 / self.FRAME_MS)  # 프레임 수

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
    ):
        self.client          = openai_client
        self.stt_model       = stt_model
        self.language        = language
        self.translate       = translate and (language == "en")  # 영어일 때만 실시간 번역
        self.translate_model = translate_model
        self.logger          = logger
        self._indicator      = indicator
        self.topic           = topic
        self.segments: List[Dict] = []
        self._session_start = time.time()
        self._use_diarize   = "diarize" in stt_model
        self._use_whisper   = stt_model.startswith("whisper")
        # 번역을 STT와 병렬 실행하기 위한 스레드 풀
        self._translator_pool = ThreadPoolExecutor(max_workers=2)

    def _run_stt(self, wav_bytes: bytes) -> str:
        params: Dict[str, Any] = {"model": self.stt_model}

        if self._use_diarize:
            params["response_format"]   = "diarized_json"
            params["chunking_strategy"] = "auto"
        elif self._use_whisper:
            params["response_format"]         = "verbose_json"
            params["timestamp_granularities"] = ["segment"]
        else:
            params["response_format"] = "json"

        if self.language and self.language != "auto":
            params["language"] = self.language

        last_err: Optional[Exception] = None
        for attempt in range(3):
            try:
                audio_file = io.BytesIO(wav_bytes)
                audio_file.name = "chunk.wav"
                resp = self.client.audio.transcriptions.create(
                    file=audio_file, **params
                )
                if isinstance(resp, dict):
                    return resp.get("text", "").strip()
                if hasattr(resp, "text"):
                    return resp.text.strip()
                try:
                    return json.loads(resp).get("text", "").strip()
                except Exception:
                    return ""
            except Exception as e:
                last_err = e
                if attempt < 2:
                    wait = 2 ** attempt   # 1초, 2초
                    print(f"\n  {C_YELLOW}[STT 재시도 {attempt + 1}/3]{C_RESET} {e}",
                          file=sys.stderr)
                    time.sleep(wait)

        raise last_err  # type: ignore

    def process(self, float_audio: np.ndarray) -> Optional[str]:
        wav = AudioRecorder.to_wav_bytes(float_audio)
        try:
            text = self._run_stt(wav)
        except Exception as e:
            print(f"\n  {C_RED}[STT 오류 - 청크 폐기]{C_RESET} {e}", file=sys.stderr)
            return None

        if not text:
            return None

        elapsed = time.time() - self._session_start
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
            "end":           elapsed + len(float_audio) / SAMPLE_RATE,
            "text":          text,
            "text_original": text,
            "speaker":       "",
        }
        self.segments.append(seg)

        if self.translate:
            # ② 번역을 백그라운드 스레드에 제출하고 즉시 리턴
            self._translator_pool.submit(self._translate_and_log, text, seg)
        else:
            if self.logger:
                self.logger.append(seg)

        return text

    def _translate_and_log(self, text: str, seg: dict):
        """백그라운드 스레드: 공유 번역 함수 호출."""
        from ws_transcriber import translate_and_log
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

    # ── 비용 단가 ──────────────────────────────
    _STT_PRICE = {   # $/min
        "gpt-4o-mini-transcribe":            0.003,
        "gpt-4o-mini-transcribe-2025-12-15": 0.003,
        "gpt-4o-transcribe":                 0.006,
        "gpt-4o-transcribe-diarize":         0.006,
        "whisper-1":                         0.006,
    }
    _TRANS_PRICE_PER_MIN   = 0.0002  # gpt-4o-mini 번역
    _MINUTES_COST_FIXED    = 0.08    # gpt-4o 회의록 생성 1회

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
                import websockets  # noqa: F401
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
        _backup_rate = WS_SAMPLE_RATE if self.mode == "ws" else SAMPLE_RATE
        if _c("realtime.audio_backup", True):
            self._backup = AudioBackup(self.output_dir, self.logger.session_ts,
                                       sample_rate=_backup_rate)

        # indicator 먼저 생성 → recorder에 level_cb 전달
        self.indicator = RecordingIndicator()
        self._stop_ev  = threading.Event()
        self._worker: Optional[threading.Thread] = None

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
        )

    def _init_ws_mode(self):
        """WebSocket 스트리밍 모드 초기화."""
        try:
            import websockets  # noqa: F401
        except ImportError:
            print(f"  {C_YELLOW}[WS] websockets 미설치 → HTTP 모드로 전환{C_RESET}")
            print(f"  {C_GRAY}  설치: pip install websockets{C_RESET}")
            self.mode = "http"
            self._init_http_mode()
            return

        # Realtime API 지원 모델 목록 (mini-transcribe 및 diarize는 미지원)
        _WS_SUPPORTED = {"gpt-4o-transcribe", "gpt-4o-realtime-preview"}
        _base_model = self.stt_model.split("-2025")[0]  # 날짜 접미사 제거
        if "diarize" in self.stt_model or _base_model not in _WS_SUPPORTED:
            reason = "diarize 모델" if "diarize" in self.stt_model else f"{self.stt_model}"
            print(f"  {C_YELLOW}[WS] {reason}은(는) WebSocket 미지원 → HTTP 모드로 전환{C_RESET}")
            self.mode = "http"
            self._init_http_mode()
            return

        print(f"  {C_GREEN}[WebSocket 모드]{C_RESET} 실시간 스트리밍 활성화 (24kHz)")
        # 실제 연결은 run()에서 컨텍스트 매니저로 열림
        self.recorder = None      # WS 모드에서는 WebSocketAudioStreamer 사용
        self.transcriber = None   # WS 모드에서는 WebSocketTranscriber 사용

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
        from ws_transcriber import WebSocketAudioStreamer, WebSocketTranscriber

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

        # WS 모델: diarize 미지원이므로 기본 모델 사용
        ws_model = self.stt_model
        if ws_model.endswith("-2025-12-15"):
            ws_model = ws_model.replace("-2025-12-15", "")

        try:
            conn_mgr = self.openai.beta.realtime.connect(
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
                session_cfg: Dict[str, Any] = {
                    "input_audio_format": "pcm16",
                    "input_audio_transcription": {
                        "model": ws_model,
                    },
                    "turn_detection": {
                        "type": _c("realtime.ws_vad_type", "server_vad") or "server_vad",
                    },
                }

                # 언어 설정
                if self.language and self.language != "auto":
                    session_cfg["input_audio_transcription"]["language"] = self.language

                # VAD eagerness (semantic_vad 전용)
                vad_type = session_cfg["turn_detection"]["type"]
                if vad_type == "semantic_vad":
                    session_cfg["turn_detection"]["eagerness"] = (
                        _c("realtime.ws_vad_eagerness", "medium") or "medium"
                    )

                # 노이즈 리덕션
                nr_type = _c("realtime.ws_noise_reduction", "near_field")
                if nr_type:
                    session_cfg["input_audio_noise_reduction"] = {"type": nr_type}

                conn.transcription_session.update(session=session_cfg)

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
                )
                # _finalize()에서 접근하기 위해 저장
                self.transcriber = ws_transcriber

                ws_streamer.start()
                event_thread = threading.Thread(
                    target=ws_transcriber.run_event_loop,
                    args=(self._stop_ev,),
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

        except Exception as e:
            print(f"\n  {C_RED}[WS 오류]{C_RESET} {e}")
            print(f"  {C_YELLOW}HTTP 모드로 전환합니다.{C_RESET}")
            self.mode = "http"
            self._init_http_mode()
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
        # 오디오 백업 WAV 변환 (정상 종료 시)
        if self._backup:
            audio_path = self._backup.close(convert_to_wav=True)
            if audio_path:
                kb = os.path.getsize(audio_path) / 1024
                print(f"  오디오 저장: {Path(audio_path).name}  ({kb:.0f} KB)")

        self.logger.close(completed=True)
        self._generate_output()

    def _save_meta(self, meta_path: str, segment_count: int, duration_sec: float):
        """세션 메타데이터 + 비용 추정을 JSON으로 저장."""
        end_dt  = self._session_end_dt or datetime.now()
        dur_min = duration_sec / 60
        stt_cost   = self._STT_PRICE.get(self.stt_model, 0.003) * dur_min
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
            print("\n  전사된 내용이 없습니다. 마이크 및 음량을 확인하세요.")
            print(f"  세션 로그 보존: {self.logger.log_path}")
            return

        total_s = segments[-1]["end"] - segments[0]["start"]
        mm, ss  = divmod(int(total_s), 60)
        print(f"\n  총 {len(segments)}개 세그먼트 / {mm}분 {ss}초")

        stem = f"realtime_{self.logger.session_ts}"

        print(f"\n{'═'*60}")
        print(f"  {self.labels['title']} 생성 중...")

        minutes_path        = os.path.join(self.output_dir, f"{stem}_minutes.md")
        summary_path        = os.path.join(self.output_dir, f"{stem}_summary.md")
        summary_txt_path    = os.path.join(self.output_dir, f"{stem}_summary.txt")
        transcript_path     = os.path.join(self.output_dir, f"{stem}_transcript.txt")
        refined_script_path = os.path.join(self.output_dir, f"{stem}_refined_script.txt")
        summary_text        = ""

        # 세션 타임스탬프 → 한국어 날짜 문자열
        try:
            _parsed = datetime.strptime(self.logger.session_ts, "%Y%m%d_%H%M%S")
            session_dt = _parsed.strftime("%Y년 %m월 %d일 %H:%M")
        except Exception:
            session_dt = ""

        try:
            # STT 교정 — 회의록 생성 전에 실행하여 교정본을 입력으로 사용
            refined_text = None
            try:
                refined_text = refine_script(
                    segments, self.llm, self.doc_type, topic=self.topic
                )
                save(refined_text, refined_script_path, "교정 스크립트")
            except Exception as re_err:
                print(f"  STT 교정 실패 ({re_err}) → 원본 스크립트로 회의록 생성")

            # 회의록 생성 — 교정본 우선, 실패 시 원본 segments 사용
            minutes = generate_minutes(
                refined_text if refined_text else segments,
                self.llm, self.doc_type,
                memo=self.memo, topic=self.topic, session_dt=session_dt,
            )
            header  = (f"<!-- Generated: {datetime.now().isoformat()} -->\n"
                       f"<!-- Mode: realtime | Type: {self.doc_type} | "
                       f"STT: {self.stt_model} | Lang: {self.language}"
                       + (f" | Topic: {self.topic}" if self.topic else "")
                       + " -->\n\n")
            save(header + minutes, minutes_path, self.labels["title"])

            summary_text = generate_summary(
                minutes, self.llm, self.doc_type,
                topic=self.topic, session_dt=session_dt,
            )
            save(summary_text, summary_path, "요약본(md)")
            save(summary_text, summary_txt_path, "요약본(txt)")

        except Exception as e:
            print(f"  회의록 생성 실패: {e}")
            import traceback
            traceback.print_exc()
            print(f"\n  나중에 복구 가능:")
            print(f"    python realtime_transcription.py --recover {self.logger.log_path}")

        # 전사 원문
        lines = []
        for s in segments:
            sm, ss2 = divmod(int(s["start"]), 60)
            orig = s.get("text_original", s["text"])
            ko   = s["text"] if s["text"] != orig else None
            lines.append(f"[{sm:02d}:{ss2:02d}] {orig}")
            if ko:
                lines.append(f"         → {ko}")
        save("\n".join(lines), transcript_path, "전사 원문")

        # 메타데이터 저장
        meta_path = os.path.join(self.output_dir, f"{stem}_meta.json")
        self._save_meta(meta_path, len(segments), total_s)

        # 이메일 발송 — minutes.md + summary.txt + transcript.txt 첨부
        if self.do_email and summary_text:
            attach = [p for p in [minutes_path, summary_txt_path, transcript_path]
                      if os.path.isfile(p)]
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
  영어 회의  : python realtime_transcription.py --language en
  영어→한국어: python realtime_transcription.py --language en --translate
  한국어 회의: python realtime_transcription.py --language ko

세션 복구 (JSONL 로그에서 회의록 재생성):
  python realtime_transcription.py --recover output/session_20250220_143022.jsonl

세션 이어붙이기 (이전 세션 + 현재 세션 → 하나의 회의록):
  python realtime_transcription.py --prev-session output/session_20250220_143022.jsonl

오디오 백업 복원 (크래시 후 PCM → WAV):
  ffmpeg -f s16le -ar 16000 -ac 1 -i output/session_TS_audio.pcm output.wav
""",
    )
    parser.add_argument("--type", default=_c("realtime.type", "meeting"),
                        choices=["meeting", "seminar", "lecture"])
    parser.add_argument("--language", default=_c("realtime.language", "en"),
                        choices=["en", "ko", "auto"],
                        help="입력 언어 (en=영어, ko=한국어, auto=자동)")
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
