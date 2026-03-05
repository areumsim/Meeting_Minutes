#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
============================================================
 WebSocket 실시간 스트리밍 전사 모듈
============================================================
 OpenAI Realtime Transcription API (WebSocket) 를 사용하여
 마이크 오디오를 연속 스트리밍하고 서버 VAD 기반으로
 즉시 전사합니다.

 기존 HTTP 청크 방식(3-6초 지연) 대비 ~1초 이내 응답.

 의존성:
   pip install openai[realtime]   # websockets 포함
============================================================
"""

import base64
import io
import queue
import sys
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, List, Dict, Any

import numpy as np

try:
    import sounddevice as sd
except ImportError:
    sd = None  # type: ignore

# ── 색상 코드 (realtime_transcription.py 와 동일) ──
C_CYAN   = "\033[36m"
C_YELLOW = "\033[33m"
C_GREEN  = "\033[32m"
C_RED    = "\033[31m"
C_GRAY   = "\033[90m"
C_RESET  = "\033[0m"

# ── 상수 ──
WS_SAMPLE_RATE = 24000   # Realtime API: 24kHz PCM16 mono 필수
CHANNELS       = 1


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  공유 번역 유틸리티
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def translate_and_log(
    text: str,
    seg: dict,
    openai_client,
    translate_model: str,
    logger,
    indicator,
    topic: str = "",
):
    """
    백그라운드 스레드용 번역 + 로깅 함수.
    RealtimeTranscriber 와 WebSocketTranscriber 양쪽에서 공유.
    """
    topic_hint = f"\n주제 맥락: {topic}" if topic else ""
    try:
        stream = openai_client.chat.completions.create(
            model=translate_model,
            stream=True,
            temperature=0.2,
            messages=[
                {"role": "system",
                 "content": (f"전문 영한 번역가. 회의/세미나 발화를 자연스러운 한국어로 번역.{topic_hint}\n"
                             "번역문만 출력. Markdown·설명 없이.")},
                {"role": "user", "content": text},
            ],
        )
    except Exception as e:
        print(f"\n  {C_YELLOW}[번역 오류]{C_RESET} {e}", file=sys.stderr)
        if indicator:
            indicator.unsuppress_draw()   # suppress 상태 해제 (claim 없이 복구)
        if logger:
            logger.append(seg)
        return

    if indicator:
        indicator.claim()   # suppress 자동 해제 + stdout 소유권 획득
    ko_text: Optional[str] = None
    try:
        print(f"  {C_YELLOW}→ ", end="", flush=True)
        parts: List[str] = []
        for chunk in stream:
            token = chunk.choices[0].delta.content or ""
            if token:
                print(token, end="", flush=True)
                parts.append(token)
        print(C_RESET, flush=True)
        ko_text = "".join(parts).strip() or None
    except Exception as e:
        print(f"\n  {C_YELLOW}[번역 오류]{C_RESET} {e}", file=sys.stderr)
    finally:
        if indicator:
            indicator.release()

    if ko_text:
        seg["text"] = ko_text
    if logger:
        logger.append(seg)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  WebSocketAudioStreamer
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class WebSocketAudioStreamer:
    """
    마이크 오디오를 24kHz PCM16 으로 캡처하여
    WebSocket RealtimeConnection 에 연속 스트리밍합니다.

    오디오 콜백(실시간 스레드) → queue → 송신 스레드 → connection.input_audio_buffer.append()
    """

    FRAME_MS = 100   # 100ms 마다 프레임 전송

    def __init__(self, connection, backup=None, level_cb=None):
        self.connection  = connection
        self._backup     = backup
        self._level_cb   = level_cb
        self._send_queue: queue.Queue = queue.Queue()
        self._stop_ev    = threading.Event()
        self._stream     = None
        self._sender_thread: Optional[threading.Thread] = None
        self._frame_samples = int(WS_SAMPLE_RATE * self.FRAME_MS / 1000)  # 2400

    def _callback(self, indata, frames, time_info, status):
        """sounddevice 오디오 콜백 — 실시간 스레드에서 호출."""
        if status:
            print(f"\n  [마이크] {status}", file=sys.stderr, end="")

        samples = indata[:, 0]

        # base64 인코딩하여 큐에 추가
        int16 = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
        b64 = base64.b64encode(int16.tobytes()).decode("ascii")
        self._send_queue.put(b64)

        # 오디오 백업
        if self._backup:
            self._backup.write(samples)

        # 레벨 미터
        if self._level_cb:
            self._level_cb(float(np.sqrt(np.mean(samples ** 2))))

    def _sender_loop(self):
        """큐에서 base64 오디오를 꺼내 WebSocket으로 전송."""
        while not self._stop_ev.is_set():
            try:
                b64 = self._send_queue.get(timeout=0.1)
                try:
                    self.connection.input_audio_buffer.append(audio=b64)
                except Exception as e:
                    if not self._stop_ev.is_set():
                        print(f"\n  {C_YELLOW}[WS 전송 오류]{C_RESET} {e}",
                              file=sys.stderr, end="")
                    break
            except queue.Empty:
                continue

    def start(self):
        """마이크 캡처 + WebSocket 송신 시작."""
        self._stop_ev.clear()

        self._sender_thread = threading.Thread(
            target=self._sender_loop, daemon=True, name="ws-audio-sender"
        )
        self._sender_thread.start()

        self._stream = sd.InputStream(
            samplerate=WS_SAMPLE_RATE,
            channels=CHANNELS,
            dtype="float32",
            callback=self._callback,
            blocksize=self._frame_samples,
        )
        self._stream.start()

    def pause(self):
        """마이크 캡처 일시정지."""
        if self._stream:
            self._stream.stop()

    def resume(self):
        """마이크 캡처 재개."""
        if self._stream:
            self._stream.start()

    def stop(self):
        """마이크 + 송신 스레드 종료."""
        self._stop_ev.set()
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if self._sender_thread:
            self._sender_thread.join(timeout=5)
            self._sender_thread = None

    @staticmethod
    def to_wav_bytes(float_audio: np.ndarray) -> bytes:
        """float32 오디오 → WAV bytes (24kHz)."""
        int16 = (np.clip(float_audio, -1.0, 1.0) * 32767).astype(np.int16)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)
            wf.setframerate(WS_SAMPLE_RATE)
            wf.writeframes(int16.tobytes())
        buf.seek(0)
        return buf.read()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  WebSocketTranscriber
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class WebSocketTranscriber:
    """
    RealtimeConnection 이벤트 루프를 실행하여
    서버 VAD 기반 전사 이벤트를 처리합니다.

    이벤트 흐름:
      input_audio_buffer.speech_started   → 발화 시작
      ...transcription.delta              → 스트리밍 부분 텍스트
      ...transcription.completed          → 최종 텍스트
      input_audio_buffer.speech_stopped   → 발화 종료
    """

    MAX_RECONNECT = 5
    RECONNECT_BASE = 2  # seconds

    def __init__(
        self,
        connection,
        language: str         = "en",
        translate: bool       = False,
        translate_model: str  = "gpt-4o-mini",
        openai_client         = None,
        logger                = None,
        indicator             = None,
        topic: str            = "",
    ):
        self.connection      = connection
        self.language        = language
        self.translate       = translate and (language == "en")
        self.translate_model = translate_model
        self.client          = openai_client
        self.logger          = logger
        self._indicator      = indicator
        self.topic           = topic
        self.segments: List[Dict] = []
        self._session_start  = time.time()
        self._translator_pool = ThreadPoolExecutor(max_workers=2)

        # 이벤트 상태 추적
        self._current_text: Dict[str, str] = {}       # item_id → 누적 delta
        self._speech_start: Dict[str, float] = {}     # item_id → audio_start_ms
        self._delta_started: Dict[str, bool] = {}     # item_id → 첫 delta 출력 여부

    def run_event_loop(self, stop_event: threading.Event):
        """
        이벤트 루프 — 별도 스레드에서 실행.
        stop_event 설정 시 종료.
        """
        try:
            for event in self.connection:
                if stop_event.is_set():
                    break
                self._handle_event(event)
        except Exception as e:
            if not stop_event.is_set():
                print(f"\n  {C_RED}[WS 이벤트 루프 오류]{C_RESET} {e}",
                      file=sys.stderr)

    def _handle_event(self, event):
        """서버 이벤트 디스패치."""
        etype = event.type

        if etype == "input_audio_buffer.speech_started":
            self._on_speech_started(event)
        elif etype == "conversation.item.input_audio_transcription.delta":
            self._on_transcription_delta(event)
        elif etype == "conversation.item.input_audio_transcription.completed":
            self._on_transcription_completed(event)
        elif etype == "input_audio_buffer.speech_stopped":
            self._on_speech_stopped(event)
        elif etype == "error":
            self._on_error(event)
        elif etype == "transcription_session.created":
            pass  # 세션 생성 확인
        elif etype == "transcription_session.updated":
            pass  # 세션 업데이트 확인

    def _on_speech_started(self, event):
        """발화 시작 — 시작 시간 기록."""
        item_id = getattr(event, "item_id", None) or ""
        audio_start_ms = getattr(event, "audio_start_ms", 0) or 0
        self._speech_start[item_id] = audio_start_ms

    def _on_speech_stopped(self, event):
        """발화 종료."""
        # completed 이벤트에서 처리하므로 여기서는 패스
        pass

    def _on_transcription_delta(self, event):
        """스트리밍 부분 전사 — 실시간 텍스트 출력."""
        item_id = getattr(event, "item_id", None) or ""
        delta = getattr(event, "delta", "") or ""

        if not delta:
            return

        if item_id not in self._delta_started:
            # 첫 delta — 타임스탬프 출력
            self._delta_started[item_id] = True
            self._current_text[item_id] = ""
            elapsed = time.time() - self._session_start
            mm, ss = divmod(int(elapsed), 60)
            if self._indicator:
                self._indicator.claim()
            print(f"\n{C_CYAN}[{mm:02d}:{ss:02d}]{C_RESET} ", end="", flush=True)

        print(delta, end="", flush=True)
        self._current_text[item_id] = self._current_text.get(item_id, "") + delta

    def _on_transcription_completed(self, event):
        """최종 전사 완료 — 세그먼트 생성 및 로깅."""
        item_id = getattr(event, "item_id", None) or ""
        final_text = (getattr(event, "transcript", "") or "").strip()

        if not final_text:
            # 빈 전사 — delta 중이었으면 indicator release 후 정리
            if item_id in self._delta_started and self._indicator:
                print(flush=True)  # 줄바꿈으로 라인 마무리
                self._indicator.release()
            self._cleanup_item(item_id)
            return

        had_delta = item_id in self._delta_started

        # 번역 예정 여부 (영어→한국어 인라인 번역)
        will_translate = self.translate and final_text.strip()

        if had_delta:
            # delta 스트리밍 중이었음 → 줄바꿈으로 라인 확정 후 release
            # ★ 핵심: print()의 줄바꿈이 스크롤 영역에서 텍스트를 위로 밀어
            #   다음 claim()이 rows-1 col 1로 이동해도 이전 텍스트가 보존됨
            print(flush=True)
            if self._indicator:
                # 번역 예정이면 인디케이터 그리기 억제 → 영어↔한국어 사이 끼임 방지
                self._indicator.release(suppress_draw=will_translate)
        else:
            # delta 없이 바로 completed — 전체 텍스트 한번에 출력
            # print()의 기본 end="\n" 으로 자동 줄바꿈
            elapsed = time.time() - self._session_start
            mm, ss = divmod(int(elapsed), 60)
            if self._indicator:
                self._indicator.claim()
            print(f"\n{C_CYAN}[{mm:02d}:{ss:02d}]{C_RESET} {final_text}", flush=True)
            if self._indicator:
                self._indicator.release(suppress_draw=will_translate)

        # 세그먼트 생성
        elapsed_now = time.time() - self._session_start
        start_ms = self._speech_start.pop(item_id, 0)
        start_sec = start_ms / 1000.0 if start_ms > 0 else max(0, elapsed_now - 5)

        seg = {
            "start":         start_sec,
            "end":           elapsed_now,
            "text":          final_text,
            "text_original": final_text,
            "speaker":       "",
        }
        self.segments.append(seg)

        # 번역 또는 로깅
        if self.translate and final_text.strip():
            self._translator_pool.submit(
                translate_and_log,
                final_text, seg, self.client,
                self.translate_model, self.logger, self._indicator,
                self.topic,
            )
        else:
            if self.logger:
                self.logger.append(seg)

        self._cleanup_item(item_id)

    def _on_error(self, event):
        """서버 오류 이벤트."""
        error = getattr(event, "error", None)
        msg = ""
        if error:
            msg = getattr(error, "message", str(error))
        print(f"\n  {C_RED}[WS 서버 오류]{C_RESET} {msg}", file=sys.stderr)

    def _cleanup_item(self, item_id: str):
        """이벤트 상태 정리."""
        self._current_text.pop(item_id, None)
        self._delta_started.pop(item_id, None)
        self._speech_start.pop(item_id, None)

    def shutdown(self):
        """번역 스레드풀 종료 대기."""
        self._translator_pool.shutdown(wait=True)
