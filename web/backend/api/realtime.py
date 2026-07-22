"""
api/realtime.py — WebSocket 실시간 STT API

브라우저에서 PCM16 24kHz 오디오를 WebSocket으로 전송하면,
서버가 OpenAI Realtime WebSocket API로 포워딩하고
트랜스크립트 결과를 실시간으로 돌려보냄.
"""

import os
import json
import asyncio
import base64
import time
import threading
import traceback
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Any
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from web.backend import database as db
from web.backend.schemas import MODE_PRESETS
from web.backend.paths import AR_ROOT  # noqa: F401 — ensures sys.path setup

router = APIRouter(tags=["realtime"])

from meeting_minutes_app.common.text_filters import is_cjk_hallucination as _is_cjk_hallucination
from meeting_minutes_app.common.realtime_ws_session import build_ws_session_config


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
        # 실시간(WS) 세션이 OpenAI 오류로 죽었는지 / 사용자가 정지했는지 추적.
        # WS가 유효한 전사 없이 실패하면 같은 오디오 스트림으로 HTTP 청크 전사에 폴백한다.
        self._ws_failed = False
        self._user_stopped = False

        # 이벤트 상태 추적
        self._current_text: Dict[str, str] = {}
        self._speech_start: Dict[str, float] = {}
        self._delta_started: Dict[str, bool] = {}
        # _handle_event()는 별도 스레드에서 실행 → 상태 딕셔너리/리스트 보호
        self._event_lock = threading.Lock()

        # 브라우저 전송 큐 (스레드 → async 브릿지, maxsize로 메모리 무제한 성장 방지)
        self._send_queue: asyncio.Queue = asyncio.Queue(maxsize=100)

        # 번역 스레드풀
        self._translator_pool = ThreadPoolExecutor(max_workers=2)

        # Vault 검색 (실시간 관련 노트) — wiki_core.realtime_search 공유 모듈,
        # config.wiki.realtime_vault_search 로 활성화. run()에서 topic 확정 후 생성.
        self._searcher = None
        self._web_findings: List[Dict] = []
        self._notes_lock = threading.Lock()
        self._web_pool = ThreadPoolExecutor(max_workers=1)  # 웹 검색 보완용
        self._segment_counter = 0  # 웹 검색 throttle용

        # F2 화자분리 후처리(opt-in): 실시간 전사는 화자 라벨을 못 만들므로, 활성화 시
        # 스트리밍 PCM을 모아 두었다가 종료 후 diarize 모델로 재전사해 speaker를 채운다.
        # 메모리(약 173MB/시간)·STT 재호출 비용이 있어 기본 꺼짐.
        self._diarize_pp = False
        self._pcm = bytearray()

    async def run(self):
        """메인 실행 루프."""
        from meeting_minutes_app.common import config_loader as cfg

        mode_num = self.config.get("mode", 2)
        preset = MODE_PRESETS.get(mode_num, MODE_PRESETS[2])
        language = self.config.get("language") or preset["language"]
        translate = self.config.get("translate", preset["translate"])
        doc_type = self.config.get("type") or preset["type"]
        title = self.config.get("title", "")
        self._diarize_pp = bool(cfg.get("realtime.diarize_postprocess", False))
        topic = self.config.get("topic", "")
        speakers = self.config.get("speakers", "")

        # 실시간 vault 검색 (config.wiki.realtime_vault_search 게이트는 모듈 내부에서 검사)
        try:
            from meeting_minutes_app.wiki_core.realtime_search import RealtimeVaultSearcher
            self._searcher = RealtimeVaultSearcher(
                topic=topic, on_notes=self._emit_related_notes, allow_launch=True)
        except Exception:
            self._searcher = None

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
            # 방금 만든 빈 세션이 'processing'으로 영구 고착되지 않도록 삭제
            if self.session_id:
                try:
                    db.delete_session(self.session_id)
                except Exception:
                    db.update_session_status(self.session_id, "error")
                self.session_id = None
            return

        ssl_verify = cfg.get("ssl.verify", True)  # 안전 기본값: 키 누락 시 검증 켜짐

        try:
            from openai import OpenAI
            import httpx as _httpx

            http_client = None
            if not ssl_verify:
                http_client = _httpx.Client(verify=False)
            openai_client = OpenAI(api_key=openai_key, http_client=http_client)

            translate_model = cfg.get("models.translate_model", "gpt-4o-mini") or "gpt-4o-mini"

            # 전송 모드 — CLI(realtime_transcription.py)와 동일하게 config의
            # realtime.mode를 존중한다. "http"로 명시했으면(비용/지연/방화벽 등 이유로
            # WS를 쓰지 않으려는 의도) WS 시도 자체를 건너뛴다 — 과거엔 이 설정을
            # 무시하고 항상 WS부터 시도해 CLI와 다르게 동작했다.
            realtime_mode = cfg.get("realtime.mode", "auto")
            if realtime_mode == "http":
                await self._run_http_fallback(
                    openai_client, language, translate, translate_model,
                    doc_type, topic, title, speakers, cfg,
                )
            else:
                # "ws"/"auto"/그 외 — WebSocket Realtime API 연결 시도
                # (실패 시 _run_ws_realtime 내부에서 HTTP로 자동 폴백)
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
        from meeting_minutes_app.common.realtime_ws_session import normalize_ws_model
        # 기본값은 CLI(realtime_transcription.DEFAULT_STT_MODEL)·아래 HTTP 폴백 경로와
        # 동일하게 맞춘다(과거엔 여기만 "-mini"가 빠진 다른 기본값이라 config.json에
        # models.stt를 안 정하면 web WS 경로만 더 비싼 모델을 썼다).
        stt_model_cfg = cfg.get("models.stt", "gpt-4o-mini-transcribe") or "gpt-4o-mini-transcribe"
        # WS 미지원 모델(diarize/mini)은 공용 규칙으로 자동 전환
        stt_model, _ws_reason = normalize_ws_model(stt_model_cfg)
        if _ws_reason:
            print(f"[realtime] WS 모드: {stt_model_cfg} → {stt_model} 자동 전환 ({_ws_reason})")

        ws_opts: Dict[str, Any] = {}
        if not ssl_verify:
            import ssl as _ssl
            ctx = _ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = _ssl.CERT_NONE
            ws_opts["ssl"] = ctx

        stop_event = threading.Event()

        # GA Realtime API(client.realtime, openai>=1.107)를 사용한다. 구버전 SDK로
        # 빌드돼 client.realtime이 없으면 (사문화된) beta 경로 대신 곧바로 HTTP 폴백.
        if not hasattr(openai_client, "realtime"):
            print("[realtime] openai SDK가 GA realtime 미지원(<1.107) → HTTP 청크 전사로 폴백")
            await self._run_http_fallback(
                openai_client, language, translate, translate_model,
                doc_type, topic, title, speakers, cfg,
            )
            return

        try:
            conn_mgr = openai_client.realtime.connect(
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
                # 전사 세션 설정 (GA: session.type='transcription')
                session_cfg = build_ws_session_config(stt_model, language, cfg.get)

                conn.session.update(session=session_cfg)

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
                        # 이벤트 루프가 OpenAI 오류로 죽었으면(WS 실패) 즉시 탈출 → HTTP 폴백
                        if self._ws_failed:
                            break
                        try:
                            data = await asyncio.wait_for(self.ws.receive(), timeout=1.0)
                        except asyncio.TimeoutError:
                            continue
                        except WebSocketDisconnect:
                            break

                        if "bytes" in data and data["bytes"]:
                            # PCM16 바이너리 데이터
                            if self._ws_failed:
                                break
                            if self._diarize_pp:
                                self._pcm.extend(data["bytes"])
                            audio_b64 = base64.b64encode(data["bytes"]).decode("ascii")
                            try:
                                conn.input_audio_buffer.append(audio=audio_b64)
                            except Exception:
                                self._ws_failed = True
                                break
                        elif "text" in data and data["text"]:
                            msg = json.loads(data["text"])
                            if msg.get("type") == "stop":
                                self._user_stopped = True
                                break
                            elif msg.get("type") == "audio":
                                # base64 인코딩된 오디오
                                if self._diarize_pp:
                                    try: self._pcm.extend(base64.b64decode(msg["data"]))
                                    except Exception: pass
                                try:
                                    conn.input_audio_buffer.append(audio=msg["data"])
                                except Exception:
                                    self._ws_failed = True
                                    break
                except WebSocketDisconnect:
                    pass

                # WS 이벤트 루프/컨슈머 정리
                stop_event.set()
                consumer_task.cancel()
                event_thread.join(timeout=10)

        except Exception as e:
            traceback.print_exc()
            self._ws_failed = True

        # OpenAI 실시간(WS)이 유효한 전사를 만들지 못하고 실패했고(사내망/서버 측
        # beta_api_shape_disabled 등), 사용자가 정지한 것도 아니면 → 같은 브라우저
        # 오디오 스트림을 계속 읽어 HTTP 청크 전사로 자동 폴백한다(끊김 없이 이어감).
        if self._ws_failed and not self._user_stopped and not self.segments:
            print("[realtime] 실시간(WS) 실패 → HTTP 청크 전사로 자동 폴백")
            try:
                await self._run_http_fallback(
                    openai_client, language, translate, translate_model,
                    doc_type, topic, title, speakers, cfg,
                )
                return
            except Exception:
                traceback.print_exc()

        # 정상 종료: 풀/검색 정리 후 최종 처리
        self._stop = True
        self._translator_pool.shutdown(wait=True, cancel_futures=False)
        self._web_pool.shutdown(wait=True, cancel_futures=False)
        if self._searcher is not None:
            self._searcher.shutdown(wait=True)
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
                # OpenAI 실시간 WS가 오류로 끊김(beta_api_shape_disabled 등)
                # → 상위(run_ws_realtime)에서 HTTP 청크 전사로 폴백하도록 표시.
                self._ws_failed = True

    def _handle_event(self, event, language, translate, translate_model,
                      openai_client, topic):
        """서버 이벤트 처리."""
        etype = event.type

        if etype == "input_audio_buffer.speech_started":
            item_id = getattr(event, "item_id", "") or ""
            audio_start_ms = getattr(event, "audio_start_ms", 0) or 0
            with self._event_lock:
                self._speech_start[item_id] = audio_start_ms

        elif etype == "conversation.item.input_audio_transcription.delta":
            item_id = getattr(event, "item_id", "") or ""
            delta = getattr(event, "delta", "") or ""
            if not delta:
                return
            with self._event_lock:
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
            with self._event_lock:
                start_ms = self._speech_start.pop(item_id, 0)
            start_sec = start_ms / 1000.0 if start_ms > 0 else max(0, elapsed - 5)

            seg = {
                "start": start_sec,
                "end": elapsed,
                "text": final_text,
                "text_original": final_text,
                "speaker": "",
                "item_id": item_id,
            }
            with self._event_lock:
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
                    "itemId": item_id,
                    "text": final_text,
                    "speaker": "",
                    "start": start_sec,
                    "end": elapsed,
                })

            # 실시간 Vault/웹 검색 (설정된 경우, 비차단)
            # vault 검색 게이트/스로틀은 RealtimeVaultSearcher 내부에서 처리
            if self._searcher is not None:
                self._searcher.offer_segment(final_text)
            self._segment_counter += 1
            try:
                from meeting_minutes_app.common import config_loader as _rc
                online_search_on = bool(_rc.get("wiki.online_search_enabled", False))
                web_interval = int(_rc.get("wiki.realtime_web_search_interval", 0) or 0)
            except Exception:
                online_search_on = False
                web_interval = 0
            if online_search_on and web_interval > 0 and self._segment_counter % web_interval == 0:
                self._web_pool.submit(
                    self._web_research_segment,
                    final_text,
                )

            self._cleanup_item(item_id)

        elif etype == "error":
            error = getattr(event, "error", None)
            msg = getattr(error, "message", str(error)) if error else "Unknown error"
            self._send_to_browser({"type": "error", "message": msg})

    def _translate_text(self, text, openai_client, translate_model, topic) -> str:
        """발화 1건을 자연스러운 한국어로 번역해 반환(WS·HTTP 경로 공용)."""
        topic_hint = f"\n주제 맥락: {topic}" if topic else ""
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
        return r.choices[0].message.content.strip()

    def _translate_segment(self, text, seg, openai_client, translate_model, topic):
        """세그먼트 번역 (백그라운드 스레드).

        주의: 이는 실시간 '스트리밍' 번역(발화 1건씩 즉시)로, 배치용
        meeting_minutes.translate_segments(전체 세그먼트를 컨텍스트 윈도우로 일괄 번역)와는
        실행 맥락이 다른 **의도된 별도 구현**이다. 둘을 통합하면 실시간 지연·스트리밍이 깨지므로
        합치지 말 것. (회의록 본문 생성 LLM은 config.models.llm을 따름 — 번역만 OpenAI 고정)
        """
        try:
            ko_text = self._translate_text(text, openai_client, translate_model, topic)
            seg["translated_text"] = ko_text
            self._send_to_browser({
                "type": "segment",
                "itemId": seg.get("item_id", ""),
                "text": text,
                "translatedText": ko_text,
                "speaker": seg.get("speaker", ""),
                "start": seg["start"],
                "end": seg["end"],
            })
        except Exception as e:
            self._send_to_browser({
                "type": "segment",
                "itemId": seg.get("item_id", ""),
                "text": text,
                "speaker": seg.get("speaker", ""),
                "start": seg["start"],
                "end": seg["end"],
                "translateError": str(e),
            })

    def _emit_related_notes(self, notes: List[Dict]) -> None:
        """RealtimeVaultSearcher 검색 풀 스레드에서 호출 — 관련 노트를 브라우저로 push.

        페이로드는 기존 related_notes 이벤트의 superset (title/snippet 추가).
        """
        try:
            self._send_to_browser({
                "type": "related_notes",
                "notes": [
                    {
                        "filename": n.get("filename", ""),
                        "title": n.get("title", ""),
                        "score": round(float(n.get("score", 0) or 0), 3),
                        "matches": (n.get("matches") or [])[:2],
                        "snippet": n.get("snippet", ""),
                    }
                    for n in notes
                ],
                "elapsed": time.time() - self._session_start,
            })
        except Exception:
            pass  # 전송 실패는 무시 (실시간 스트림에 영향 없어야 함)

    def _web_research_segment(self, text: str) -> None:
        """세그먼트 텍스트로 웹 검색 보완 (백그라운드 스레드, 비차단)."""
        try:
            from meeting_minutes_app.common import config_loader as _rc
            from meeting_minutes_app.meeting_pipeline import meeting_minutes as mm
            llm = mm.LLMClient(preferred=_rc.get("models.llm", "gpt") or "gpt")
            result = llm.web_research(text[:60])
            if result and result.get("text"):
                with self._notes_lock:
                    self._web_findings.append({
                        "segment_text": text[:80],
                        "result": result.get("text", "")[:500],
                        "sources": result.get("sources", [])[:3],
                    })
        except Exception:
            pass

    def _send_to_browser(self, data: dict):
        """스레드 안전한 WebSocket 전송. 큐에 넣으면 메인 루프에서 처리."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._send_queue.put_nowait, data)
        except asyncio.QueueFull:
            pass  # 큐 포화 시 최신 데이터 드롭 (오래된 데이터 유지가 더 나쁨)
        except Exception as e:
            print(f"[realtime] _send_to_browser 실패: {e}")

    def _cleanup_item(self, item_id: str):
        with self._event_lock:
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
                    if self._diarize_pp:
                        self._pcm.extend(data["bytes"])
                elif "text" in data and data["text"]:
                    msg = json.loads(data["text"])
                    if msg.get("type") == "stop":
                        break
                    elif msg.get("type") == "audio":
                        _b = base64.b64decode(msg["data"])
                        audio_buffer.extend(_b)
                        if self._diarize_pp:
                            self._pcm.extend(_b)

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
                            # 실시간 번역(영→한): WS 경로와 동일 게이트. HTTP 청크 모드도
                            # 청크마다 번역해 한국어를 즉시 함께 보낸다(과거엔 번역이 최종
                            # 회의록에만 있고 실시간 화면엔 영어만 떴다).
                            translated = ""
                            if translate and language == "en":
                                try:
                                    translated = self._translate_text(
                                        text, openai_client, translate_model, topic)
                                    seg["translated_text"] = translated
                                except Exception as _te:
                                    print(f"[http-translate] error: {_te}")
                            await self.ws.send_json({
                                "type": "segment",
                                "text": text,
                                "translatedText": translated,
                                "speaker": "",
                                "start": seg["start"],
                                "end": seg["end"],
                            })
                            # 실시간 vault 검색 (WS 모드와 동일 게이트/스로틀)
                            if self._searcher is not None:
                                self._searcher.offer_segment(text)
                    except Exception as e:
                        print(f"[http-stt] error: {e}")

        except WebSocketDisconnect:
            pass

        # vault 검색 drain — _finalize()의 collected_notes() 완결성 보장 (WS 경로와 동일)
        if self._searcher is not None:
            self._searcher.shutdown(wait=True)

        await self._finalize(
            openai_client, language, translate, doc_type, topic, title,
        )

    def _diarize_postprocess(self, language):
        """모아둔 세션 PCM(24kHz mono s16le)을 WAV로 저장 후 diarize 모델로 재전사.

        반환: 화자 라벨이 채워진 세그먼트 리스트(start/end/text/speaker) 또는 None.
        blocking(ffmpeg+STT)이라 finalize에서 asyncio.to_thread로 호출한다.
        """
        import wave
        import tempfile
        import shutil
        from meeting_minutes_app.common import config_loader as cfg

        if not self._pcm:
            return None
        model_cfg = cfg.get("models.stt", "") or ""
        diar_model = model_cfg if "diarize" in model_cfg else "gpt-4o-transcribe-diarize"

        tmpdir = tempfile.mkdtemp(prefix="mm_diar_")
        try:
            wav_path = os.path.join(tmpdir, "session.wav")
            with wave.open(wav_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)       # int16
                wf.setframerate(24000)   # 브라우저 스트림과 동일
                wf.writeframes(bytes(self._pcm))

            from meeting_minutes_app.meeting_pipeline import stt
            segs = stt.run_stt(
                wav_path, diar_model,
                language=None if (language in (None, "auto")) else language,
                work_dir=tmpdir,
            )
            # 화자가 하나도 안 붙었으면(모델이 라벨 미제공) 후처리 이득이 없으니 원본 유지
            if not segs or not any((s.get("speaker") or "").strip() for s in segs):
                return None
            return segs
        finally:
            try:
                shutil.rmtree(tmpdir, ignore_errors=True)
            except Exception:
                pass

    async def _finalize(self, openai_client, language, translate, doc_type, topic, title):
        """세션 종료: 공유 오케스트레이터(finalize.run_post_session)로 회의록/요약 생성.

        과거 이 메서드에 복사돼 있던 refine→minutes→verify→publish→registry→graph
        흐름은 meeting_pipeline/finalize.py 로 통합됐다 — 여기서는 웹 고유 I/O만
        담당한다: DB upsert(on_document), WS 이벤트(on_status/fact_check), 세션 상태.
        """
        with self._event_lock:
            segments_snapshot = list(self.segments)
        if not segments_snapshot or not self.session_id:
            # 세그먼트가 하나도 없는 세션(연결만 하고 발화 없이 종료)은 문서·전사가
            # 전혀 없어 대시보드에 빈 행으로만 남는다 → 'completed'로 두지 말고 삭제.
            if self.session_id:
                try:
                    db.delete_session(self.session_id)
                except Exception:
                    db.update_session_status(self.session_id, "completed")
                self.session_id = None
            return
        # _finalize 전체에서 snapshot 사용 (스레드 안전)
        self.segments = segments_snapshot

        # F2: 화자분리 후처리(opt-in). 모아둔 PCM을 diarize 모델로 재전사해 speaker를
        # 채우고 세그먼트를 교체한다 — 이후 회의록/요약 생성이 화자 라벨을 반영한다.
        # 완전 best-effort: 어떤 실패든 원본 세그먼트를 그대로 유지한다.
        if self._diarize_pp and self._pcm and self.session_id:
            try:
                await self.ws.send_json({"type": "status", "message": "화자 분리 후처리 중..."})
            except Exception:
                pass
            try:
                diar_segs = await asyncio.to_thread(self._diarize_postprocess, language)
                if diar_segs:
                    db.replace_segments(self.session_id, diar_segs)
                    self.segments = diar_segs
                    segments_snapshot = diar_segs
                    print(f"[diarize-pp] 화자분리 완료: {len(diar_segs)}개 세그먼트")
            except Exception as e:
                print(f"[diarize-pp] 실패(원본 유지): {e}")

        await self.ws.send_json({"type": "generating", "message": "회의록 생성 중..."})

        # 본 큐 소비자는 stop 시 취소됨 — finalize 동안 상태 이벤트를 흘려보내기
        # 위해 소비자를 재가동한다 (run_post_session은 워커 스레드에서 실행).
        async def _queue_consumer():
            while True:
                try:
                    data = await asyncio.wait_for(self._send_queue.get(), timeout=0.5)
                    await self.ws.send_json(data)
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    raise
                except Exception:
                    break

        consumer = asyncio.create_task(_queue_consumer())
        session = self  # events 클로저용

        try:
            from meeting_minutes_app.meeting_pipeline import finalize as fz
            from meeting_minutes_app.meeting_pipeline import meeting_minutes as mm

            # 회의록 생성 LLM은 config.json(models.llm)을 따른다
            llm = mm.LLMClient(preferred=mm._c("models.llm", "gpt") or "gpt")
            session_dt = datetime.now().strftime("%Y년 %m월 %d일 %H:%M")

            # 실시간 수집분 — vault 관련 노트 + 웹 검색 보완
            with self._notes_lock:
                _web_findings = list(self._web_findings)
            _rt_titles = (self._searcher.collected_titles()[:10]
                          if self._searcher else [])
            extra_blocks = []
            if _web_findings:
                extra_blocks.append("[웹 검색 보완]:\n" + "\n".join(
                    f"- {f['result'][:200]}" for f in _web_findings[:3]))

            # 산출물 폴더: output/web_realtime_{session_id}
            from meeting_minutes_app.common import config_loader as _rc2
            out_root = Path(str(_rc2.get("output_dir", "output") or "output"))
            if not out_root.is_absolute():
                out_root = Path.cwd() / out_root
            session_out = out_root / f"web_realtime_{self.session_id}"

            class _WebEvents(fz.FinalizeEvents):
                """finalize 산출물 → SQLite documents + WS 이벤트."""

                def on_status(self, stage, message):
                    session._send_to_browser({"type": "status", "message": message})

                def on_document(self, dtype, content, fmt="markdown"):
                    try:
                        db.upsert_document(
                            session.session_id, dtype, content,
                            "json" if fmt == "json" else "markdown")
                    except Exception as _de:
                        print(f"[finalize] doc upsert 실패({dtype}): {_de}")
                    if dtype == "fact_check":
                        session._send_to_browser(
                            {"type": "fact_check", "content": content})

                def on_stage_error(self, stage, exc):
                    print(f"[finalize] {stage} 실패 (무시): {exc}")

            inputs = fz.SessionInputs(
                segments=self.segments,
                title=title or f"실시간 {session_dt}",
                topic=topic,
                doc_type=doc_type,
                session_dt=session_dt,
                source="web_realtime",
                session_id=self.session_id,
            )
            options = fz.FinalizeOptions(
                llm=llm,
                do_graph_sync=True,
                notify=("email" if mm._c("realtime.email_on_finish", False) else None),
                artifacts_dir=session_out,
                extra_related_titles=_rt_titles,
                extra_memo_blocks=extra_blocks,
            )

            # LLM/발행 작업은 워커 스레드로 — 이벤트 루프를 막지 않아
            # status 이벤트가 생성 중에도 스트리밍된다
            await asyncio.to_thread(fz.run_post_session, inputs, options, _WebEvents())

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
            try:
                await self.ws.send_json({"type": "error", "message": f"회의록 생성 실패: {e}"})
            except Exception:
                pass  # 소켓이 이미 죽었어도 DB에는 저장 완료
        finally:
            consumer.cancel()


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
