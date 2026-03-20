"""
api/realtime.py — WebSocket 실시간 STT API

브라우저에서 PCM16 24kHz 오디오를 WebSocket으로 전송하면,
서버가 OpenAI Realtime WebSocket API로 포워딩하고
트랜스크립트 결과를 실시간으로 돌려보냄.
"""

import sys
import os
import json
import asyncio
import base64
import time
import threading
import traceback
import re
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Any
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from web.backend import database as db
from web.backend.schemas import MODE_PRESETS
from web.backend.paths import AR_ROOT  # noqa: F401 — ensures sys.path setup

router = APIRouter(tags=["realtime"])

# CJK 환각 필터
_CJK_RANGES = (
    r'\u3000-\u303F\u3040-\u309F\u30A0-\u30FF'
    r'\u4E00-\u9FFF\uF900-\uFAFF'
)
_RE_CJK = re.compile(f'[{_CJK_RANGES}]')


def _is_cjk_hallucination(text: str, threshold: float = 0.3) -> bool:
    if not text or len(text.strip()) < 2:
        return False
    return (len(_RE_CJK.findall(text)) / len(text)) >= threshold


class BrowserRealtimeSession:
    """브라우저 오디오 → OpenAI Realtime API → 트랜스크립트를 관리하는 세션."""

    def __init__(self, ws: WebSocket, config: dict):
        self.ws = ws
        self.config = config
        self.session_id: Optional[str] = None
        self.segments: List[Dict] = []
        self._session_start = time.time()
        self._stop = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # 이벤트 상태 추적 (ws_transcriber.py와 동일)
        self._current_text: Dict[str, str] = {}
        self._speech_start: Dict[str, float] = {}
        self._delta_started: Dict[str, bool] = {}

        # 브라우저 전송 큐 (스레드 → async 브릿지)
        self._send_queue: asyncio.Queue = asyncio.Queue()

        # 번역 스레드풀
        self._translator_pool = ThreadPoolExecutor(max_workers=2)

    async def run(self):
        """메인 실행 루프."""
        import config_loader as cfg

        mode_num = self.config.get("mode", 2)
        preset = MODE_PRESETS.get(mode_num, MODE_PRESETS[2])
        language = self.config.get("language") or preset["language"]
        translate = self.config.get("translate", preset["translate"])
        doc_type = self.config.get("type") or preset["type"]
        title = self.config.get("title", "")
        topic = self.config.get("topic", "")
        speakers = self.config.get("speakers", "")

        # DB 세션 생성
        self.session_id = db.create_session(
            title=title or f"실시간 녹음 {datetime.now().strftime('%H:%M')}",
            topic=topic,
            doc_type=doc_type,
            language=language,
            translate=translate,
            source="web",
            mode=f"realtime_{mode_num}",
            speakers=speakers,
        )

        await self.ws.send_json({
            "type": "session_created",
            "sessionId": self.session_id,
            "config": {"language": language, "translate": translate, "doc_type": doc_type},
        })

        # OpenAI 클라이언트 생성
        openai_key = cfg.get("api.openai_api_key", "")
        if not openai_key:
            openai_key = os.environ.get("OPENAI_API_KEY", "")
        if not openai_key:
            await self.ws.send_json({"type": "error", "message": "OpenAI API 키가 설정되지 않았습니다."})
            return

        ssl_verify = cfg.get("ssl.verify", False)

        try:
            from openai import OpenAI
            import httpx as _httpx

            http_client = None
            if not ssl_verify:
                http_client = _httpx.Client(verify=False)
            openai_client = OpenAI(api_key=openai_key, http_client=http_client)

            translate_model = cfg.get("models.translate_model", "gpt-4o-mini") or "gpt-4o-mini"

            # WebSocket Realtime API 연결
            await self._run_ws_realtime(
                openai_client, language, translate, translate_model,
                doc_type, topic, title, speakers, ssl_verify, cfg,
            )
        except Exception as e:
            traceback.print_exc()
            await self.ws.send_json({"type": "error", "message": str(e)})
            if self.session_id:
                db.update_session_status(self.session_id, "error")

    async def _run_ws_realtime(
        self, openai_client, language, translate, translate_model,
        doc_type, topic, title, speakers, ssl_verify, cfg,
    ):
        """OpenAI Realtime WebSocket API 연결 및 이벤트 루프."""
        stt_model = cfg.get("models.stt", "gpt-4o-transcribe") or "gpt-4o-transcribe"
        # WS는 diarize, mini-transcribe 미지원
        if "diarize" in stt_model or "mini" in stt_model:
            stt_model = "gpt-4o-transcribe"
        # 날짜 접미사 제거
        if stt_model.endswith("-2025-12-15"):
            stt_model = stt_model.replace("-2025-12-15", "")

        ws_opts: Dict[str, Any] = {}
        if not ssl_verify:
            import ssl as _ssl
            ctx = _ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = _ssl.CERT_NONE
            ws_opts["ssl"] = ctx

        stop_event = threading.Event()

        try:
            conn_mgr = openai_client.beta.realtime.connect(
                model=stt_model,
                websocket_connection_options=ws_opts,
            )
        except Exception as e:
            await self.ws.send_json({"type": "error", "message": f"OpenAI Realtime 연결 실패: {e}"})
            # HTTP 폴백 시도
            await self._run_http_fallback(
                openai_client, language, translate, translate_model,
                doc_type, topic, title, speakers, cfg,
            )
            return

        try:
            with conn_mgr as conn:
                # 전사 세션 설정
                session_cfg: Dict[str, Any] = {
                    "input_audio_format": "pcm16",
                    "input_audio_transcription": {"model": stt_model},
                    "turn_detection": {
                        "type": cfg.get("realtime.ws_vad_type", "server_vad") or "server_vad",
                    },
                }
                if language and language != "auto":
                    session_cfg["input_audio_transcription"]["language"] = language
                vad_type = session_cfg["turn_detection"]["type"]
                if vad_type == "semantic_vad":
                    session_cfg["turn_detection"]["eagerness"] = (
                        cfg.get("realtime.ws_vad_eagerness", "medium") or "medium"
                    )
                nr_type = cfg.get("realtime.ws_noise_reduction", "near_field")
                if nr_type:
                    session_cfg["input_audio_noise_reduction"] = {"type": nr_type}

                conn.transcription_session.update(session=session_cfg)

                await self.ws.send_json({"type": "ready", "model": stt_model})

                # 현재 이벤트 루프 저장 (스레드→async 브릿지용)
                self._loop = asyncio.get_event_loop()

                # 이벤트 루프를 별도 스레드에서 실행
                event_thread = threading.Thread(
                    target=self._event_loop,
                    args=(conn, stop_event, language, translate, translate_model,
                          openai_client, topic),
                    daemon=True,
                )
                event_thread.start()

                # 큐 consumer: 스레드에서 넣은 데이터를 WebSocket으로 전송
                async def send_queue_consumer():
                    while not self._stop:
                        try:
                            data = await asyncio.wait_for(self._send_queue.get(), timeout=0.5)
                            await self.ws.send_json(data)
                        except asyncio.TimeoutError:
                            continue
                        except Exception:
                            break

                consumer_task = asyncio.create_task(send_queue_consumer())

                # 브라우저로부터 오디오 수신
                try:
                    while not self._stop:
                        try:
                            data = await asyncio.wait_for(self.ws.receive(), timeout=1.0)
                        except asyncio.TimeoutError:
                            continue
                        except WebSocketDisconnect:
                            break

                        if "bytes" in data and data["bytes"]:
                            # PCM16 바이너리 데이터
                            audio_b64 = base64.b64encode(data["bytes"]).decode("ascii")
                            try:
                                conn.input_audio_buffer.append(audio=audio_b64)
                            except Exception:
                                break
                        elif "text" in data and data["text"]:
                            msg = json.loads(data["text"])
                            if msg.get("type") == "stop":
                                break
                            elif msg.get("type") == "audio":
                                # base64 인코딩된 오디오
                                try:
                                    conn.input_audio_buffer.append(audio=msg["data"])
                                except Exception:
                                    break
                except WebSocketDisconnect:
                    pass

                # 종료
                self._stop = True
                stop_event.set()
                consumer_task.cancel()
                event_thread.join(timeout=10)
                self._translator_pool.shutdown(wait=True)

        except Exception as e:
            traceback.print_exc()
            await self.ws.send_json({"type": "error", "message": str(e)})

        # 최종 처리
        await self._finalize(
            openai_client, language, translate, doc_type, topic, title,
        )

    def _event_loop(self, conn, stop_event, language, translate, translate_model,
                    openai_client, topic):
        """OpenAI Realtime 이벤트 루프 (별도 스레드)."""
        try:
            for event in conn:
                if stop_event.is_set():
                    break
                self._handle_event(
                    event, language, translate, translate_model,
                    openai_client, topic,
                )
        except Exception as e:
            if not stop_event.is_set():
                print(f"[realtime] event loop error: {e}")

    def _handle_event(self, event, language, translate, translate_model,
                      openai_client, topic):
        """서버 이벤트 처리."""
        etype = event.type

        if etype == "input_audio_buffer.speech_started":
            item_id = getattr(event, "item_id", "") or ""
            audio_start_ms = getattr(event, "audio_start_ms", 0) or 0
            self._speech_start[item_id] = audio_start_ms

        elif etype == "conversation.item.input_audio_transcription.delta":
            item_id = getattr(event, "item_id", "") or ""
            delta = getattr(event, "delta", "") or ""
            if not delta:
                return
            if item_id not in self._delta_started:
                self._delta_started[item_id] = True
                self._current_text[item_id] = ""
            self._current_text[item_id] = self._current_text.get(item_id, "") + delta
            # 실시간 delta를 브라우저로 전송
            self._send_to_browser({
                "type": "delta",
                "itemId": item_id,
                "delta": delta,
                "elapsed": time.time() - self._session_start,
            })

        elif etype == "conversation.item.input_audio_transcription.completed":
            item_id = getattr(event, "item_id", "") or ""
            final_text = (getattr(event, "transcript", "") or "").strip()

            if not final_text or _is_cjk_hallucination(final_text):
                self._cleanup_item(item_id)
                return

            elapsed = time.time() - self._session_start
            start_ms = self._speech_start.pop(item_id, 0)
            start_sec = start_ms / 1000.0 if start_ms > 0 else max(0, elapsed - 5)

            seg = {
                "start": start_sec,
                "end": elapsed,
                "text": final_text,
                "text_original": final_text,
                "speaker": "",
            }
            self.segments.append(seg)

            # 세그먼트를 DB에 저장
            if self.session_id:
                db.add_segment(
                    self.session_id, "", final_text,
                    start_sec, elapsed,
                )

            # 번역
            if translate and language == "en" and final_text.strip():
                self._translator_pool.submit(
                    self._translate_segment,
                    final_text, seg, openai_client, translate_model, topic,
                )
            else:
                self._send_to_browser({
                    "type": "segment",
                    "text": final_text,
                    "speaker": "",
                    "start": start_sec,
                    "end": elapsed,
                })

            self._cleanup_item(item_id)

        elif etype == "error":
            error = getattr(event, "error", None)
            msg = getattr(error, "message", str(error)) if error else "Unknown error"
            self._send_to_browser({"type": "error", "message": msg})

    def _translate_segment(self, text, seg, openai_client, translate_model, topic):
        """세그먼트 번역 (백그라운드 스레드)."""
        topic_hint = f"\n주제 맥락: {topic}" if topic else ""
        try:
            r = openai_client.chat.completions.create(
                model=translate_model,
                temperature=0.2,
                messages=[
                    {"role": "system",
                     "content": (f"전문 영한 번역가. 회의/세미나 발화를 자연스러운 한국어로 번역.{topic_hint}\n"
                                 "번역문만 출력. Markdown/설명 없이.\n"
                                 "반드시 한국어로만 출력.")},
                    {"role": "user", "content": text},
                ],
            )
            ko_text = r.choices[0].message.content.strip()
            seg["translated_text"] = ko_text
            self._send_to_browser({
                "type": "segment",
                "text": text,
                "translatedText": ko_text,
                "speaker": seg.get("speaker", ""),
                "start": seg["start"],
                "end": seg["end"],
            })
        except Exception as e:
            self._send_to_browser({
                "type": "segment",
                "text": text,
                "speaker": seg.get("speaker", ""),
                "start": seg["start"],
                "end": seg["end"],
                "translateError": str(e),
            })

    def _send_to_browser(self, data: dict):
        """스레드 안전한 WebSocket 전송. 큐에 넣으면 메인 루프에서 처리."""
        try:
            if self._loop and not self._loop.is_closed():
                self._loop.call_soon_threadsafe(self._send_queue.put_nowait, data)
        except Exception:
            pass

    def _cleanup_item(self, item_id: str):
        self._current_text.pop(item_id, None)
        self._delta_started.pop(item_id, None)
        self._speech_start.pop(item_id, None)

    async def _run_http_fallback(
        self, openai_client, language, translate, translate_model,
        doc_type, topic, title, speakers, cfg,
    ):
        """WebSocket 연결 실패 시 HTTP 청크 방식 폴백."""
        stt_model = cfg.get("models.stt", "gpt-4o-mini-transcribe") or "gpt-4o-mini-transcribe"
        await self.ws.send_json({"type": "fallback_http", "model": stt_model})

        import io
        import wave
        import numpy as np

        audio_buffer = bytearray()
        CHUNK_SAMPLES = 24000 * 5  # 5초 분량 (24kHz)
        CHUNK_BYTES = CHUNK_SAMPLES * 2  # int16 = 2 bytes

        try:
            while not self._stop:
                try:
                    data = await asyncio.wait_for(self.ws.receive(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                except WebSocketDisconnect:
                    break

                if "bytes" in data and data["bytes"]:
                    audio_buffer.extend(data["bytes"])
                elif "text" in data and data["text"]:
                    msg = json.loads(data["text"])
                    if msg.get("type") == "stop":
                        break
                    elif msg.get("type") == "audio":
                        audio_buffer.extend(base64.b64decode(msg["data"]))

                # 충분한 오디오가 모이면 STT 호출
                if len(audio_buffer) >= CHUNK_BYTES:
                    chunk = bytes(audio_buffer[:CHUNK_BYTES])
                    audio_buffer = audio_buffer[CHUNK_BYTES:]

                    # PCM16 → WAV 변환
                    wav_buf = io.BytesIO()
                    with wave.open(wav_buf, "wb") as wf:
                        wf.setnchannels(1)
                        wf.setsampwidth(2)
                        wf.setframerate(24000)
                        wf.writeframes(chunk)
                    wav_buf.seek(0)
                    wav_buf.name = "chunk.wav"

                    try:
                        result = openai_client.audio.transcriptions.create(
                            model=stt_model,
                            file=wav_buf,
                            language=language if language != "auto" else None,
                            response_format="text",
                        )
                        text = result.strip() if isinstance(result, str) else result.text.strip()
                        if text and not _is_cjk_hallucination(text):
                            elapsed = time.time() - self._session_start
                            seg = {
                                "start": max(0, elapsed - 5),
                                "end": elapsed,
                                "text": text,
                                "text_original": text,
                                "speaker": "",
                            }
                            self.segments.append(seg)
                            if self.session_id:
                                db.add_segment(self.session_id, "", text, seg["start"], seg["end"])
                            await self.ws.send_json({
                                "type": "segment",
                                "text": text,
                                "speaker": "",
                                "start": seg["start"],
                                "end": seg["end"],
                            })
                    except Exception as e:
                        print(f"[http-stt] error: {e}")

        except WebSocketDisconnect:
            pass

        await self._finalize(
            openai_client, language, translate, doc_type, topic, title,
        )

    async def _finalize(self, openai_client, language, translate, doc_type, topic, title):
        """세션 종료: 회의록/요약 생성."""
        if not self.segments or not self.session_id:
            if self.session_id:
                db.update_session_status(self.session_id, "completed")
            return

        await self.ws.send_json({"type": "generating", "message": "회의록 생성 중..."})

        try:
            import meeting_minutes as mm

            llm = mm.LLMClient(preferred="gpt")

            # 교정 스크립트
            refined_text = None
            try:
                refined_text = mm.refine_script(
                    self.segments, llm, doc_type, topic=topic
                )
                if refined_text:
                    db.upsert_document(self.session_id, "refined_script", refined_text)
            except Exception:
                pass

            # 세션 날짜 문자열
            session_dt = datetime.now().strftime("%Y년 %m월 %d일 %H:%M")

            # 회의록
            try:
                minutes = mm.generate_minutes(
                    refined_text if refined_text else self.segments,
                    llm, doc_type, topic=topic, session_dt=session_dt,
                )
                if minutes:
                    db.upsert_document(self.session_id, "minutes", minutes)
            except Exception as e:
                print(f"[finalize] minutes error: {e}")

            # 요약
            try:
                summary = mm.generate_summary(
                    minutes if minutes else refined_text if refined_text else "",
                    llm, doc_type, topic=topic, session_dt=session_dt,
                )
                if summary:
                    db.upsert_document(self.session_id, "summary", summary)
            except Exception as e:
                print(f"[finalize] summary error: {e}")

            # 스크립트
            try:
                script = mm.build_script_md(self.segments)
                if script:
                    db.upsert_document(self.session_id, "script", script)
            except Exception:
                pass

            # 액션 아이템 (회의 타입만)
            if doc_type == "meeting":
                try:
                    actions = mm.extract_action_items(
                        minutes if minutes else "",
                        llm,
                        doc_type=doc_type,
                    )
                    if actions:
                        db.upsert_document(self.session_id, "actions", actions, "json")
                except Exception:
                    pass

            duration = self.segments[-1]["end"] - self.segments[0]["start"] if self.segments else 0
            db.update_session_status(
                self.session_id, "completed",
                duration_sec=duration,
            )

            await self.ws.send_json({
                "type": "completed",
                "sessionId": self.session_id,
                "segmentCount": len(self.segments),
                "duration": duration,
            })

        except Exception as e:
            traceback.print_exc()
            db.update_session_status(self.session_id, "error")
            await self.ws.send_json({"type": "error", "message": f"회의록 생성 실패: {e}"})


@router.websocket("/ws/realtime")
async def websocket_realtime(ws: WebSocket):
    await ws.accept()

    try:
        # 첫 메시지로 설정 수신
        init_data = await ws.receive_json()
        config = init_data.get("config", init_data)

        session = BrowserRealtimeSession(ws, config)
        await session.run()

    except WebSocketDisconnect:
        pass
    except Exception as e:
        traceback.print_exc()
        try:
            await ws.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
