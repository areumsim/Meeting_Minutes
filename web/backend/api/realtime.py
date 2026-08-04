"""
api/realtime.py — 실시간 STT API (WebSocket 오디오 입력)

브라우저가 PCM16 24kHz 오디오를 WebSocket(/ws/realtime)으로 보내면 서버가 전사해
결과를 실시간으로 돌려준다. 전사 경로는 config realtime.mode 로 결정된다:
  - "http"(기본): 오디오를 청크로 잘라 stt.transcribe_chunk 로 전사(_run_http_fallback).
      + 2단계 보정(two_pass): 빠른 패스로 조각을 즉시 표시하고, revise 워커가 윈도
        단위로 재전사해 문장으로 교체. 빠른 패스는 stt_concurrency 만큼 병렬(순서 보장).
  - "auto"/"ws": OpenAI Realtime WebSocket API로 포워딩(_run_ws_realtime).
      실패 시 같은 오디오 스트림으로 http 청크 전사에 자동 폴백.
안정·저비용이 기본(http)이며, 저지연이 필요하면 auto/ws 를 쓴다.
"""

import os
import re
import json
import array
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

from meeting_minutes_app.common.text_filters import (
    SUSPECT_MARKER,
    collapse_repetitions as _collapse_repetitions,
    is_cjk_hallucination as _is_cjk_hallucination,
    is_near_duplicate as _is_near_duplicate,
    is_script_mismatch as _is_script_mismatch,
    mark_suspect as _mark_suspect,
    unique_ratio as _unique_ratio,
)
from meeting_minutes_app.common.realtime_ws_session import (
    build_ws_session_config,
    resolve_session_language as _resolve_stt_language,
)


def _pcm_rms(b: bytes, max_samples: int = 8000) -> float:
    """int16 mono PCM 의 RMS. 긴 버퍼는 균일 서브샘플링으로 근사한다.

    보정 윈도(25초×24kHz=60만 샘플)를 매번 전부 제곱합하면 이벤트 루프가 눈에
    띄게 멈춘다. audioop 은 3.13 에서 제거돼 쓰지 않는다.
    """
    n = (len(b) // 2) * 2
    if n <= 0:
        return 0.0
    a = array.array("h")
    a.frombytes(bytes(b[:n]))
    if not a:
        return 0.0
    step = max(1, len(a) // max_samples)
    vals = a[::step] if step > 1 else a
    if not vals:
        return 0.0
    return (sum(v * v for v in vals) / len(vals)) ** 0.5


def _c_get(key: str, default: Any = None) -> Any:
    """config 조회 — cfg 핸들이 없는 콜백(WS 이벤트 스레드)에서 사용."""
    try:
        from meeting_minutes_app.common import config_loader as cfg
        return cfg.get(key, default)
    except Exception:
        return default


def _norm_tokens(s: str) -> List[str]:
    """에코 비교용 정규화 토큰: 소문자 + 단어문자만(구두점 무시)."""
    out = []
    for w in (s or "").split():
        t = re.sub(r"[^\w']+", "", w).lower()
        if t:
            out.append(t)
    return out


def _strip_prompt_echo(text: str, prompt: str, min_tokens: int = 3) -> str:
    """전사 결과 앞머리가 문맥 prompt 꼬리를 그대로 반복(에코)하면 잘라낸다.

    gpt-4o-transcribe 계열은 prompt 로 준 직전 문장을 출력에 되풀이하는 경우가
    있어, 청크/보정 윈도마다 직전 내용이 중복 표시되는 원인이 된다. 구두점·
    대소문자를 무시한 토큰열로 text 접두부와 prompt 접미부의 최장 일치를 찾아
    제거한다(min_tokens 미만의 짧은 우연 일치는 무시). 전체가 에코면 "" 반환.
    """
    if not text or not prompt:
        return text
    t_words = text.split()
    t_norm = _norm_tokens(text)
    p_norm = _norm_tokens(prompt)
    if len(t_norm) != len([w for w in t_words if _norm_tokens(w)]):
        # 정규화로 사라지는 토큰(순수 구두점 단어)이 있으면 인덱스 매핑이 틀어짐 —
        # 안전하게 원문 단어 기준으로 재구성한다.
        t_words = [w for w in t_words if _norm_tokens(w)]
    best = 0
    for k in range(min(len(t_norm), len(p_norm)), min_tokens - 1, -1):
        if t_norm[:k] == p_norm[-k:]:
            best = k
            break
    if best == 0:
        return text
    return " ".join(t_words[best:]).strip()


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。！？])\s+")


def _split_sentences(text: str) -> List[str]:
    """보정 윈도 텍스트를 문장 단위로 분할(에코 제거 후 재분할용)."""
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(text or "") if s.strip()]


def _allocate_timestamps(segs: List[Dict], t0: float, t1: float) -> None:
    """보정 패스 문장들에 [t0, t1) 구간을 문자수 비례로 단조 배분(in-place).

    _parse_json_simple 은 start=end=offset 만 주므로, 화면 정렬·구간 교체(revise)에
    필요한 타임스탬프를 근사로 만든다. 정밀할 필요는 없고 단조 증가 + 경계 일치만
    보장하면 된다(프런트 정렬·다음 윈도와의 경계 계산 용도).
    """
    if not segs:
        return
    if t1 <= t0:
        for s in segs:
            s["start"], s["end"] = t0, t0
        return
    total = sum(max(1, len(s.get("text") or "")) for s in segs)
    pos = t0
    span = t1 - t0
    for s in segs:
        w = max(1, len(s.get("text") or "")) / total
        end = min(t1, pos + span * w)
        s["start"], s["end"] = round(pos, 3), round(end, 3)
        pos = end
    segs[-1]["end"] = round(t1, 3)


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
        self._web_skip_notified = ""   # 웹검색 건너뜀 사유 중복 출력 방지(_note_web_skip)
        self._notes_lock = threading.Lock()
        self._web_pool = ThreadPoolExecutor(max_workers=1)  # 웹 검색 보완용
        # 회의 진행 페르소나 오케스트레이터(M0 관찰모드) — wiki_core.facilitation.
        # config.facilitation.enabled(기본 꺼짐) 게이트·전용 스레드풀·비용 관문은
        # 전부 모듈 내부에 있다. run()에서 세션 생성 후 만든다(관찰 로그가 session_id
        # 로 남아야 종료 후 finalize 사실검증과 대조할 수 있다).
        self._facilitator = None
        self._segment_counter = 0  # 웹 검색 throttle용
        # 웹은 보완재 — 내부(vault) 검색이 후보를 찾아낸 구간에서는 웹 검색을 건너뛴다
        # (wiki.realtime_web_only_if_no_vault_hit). 내부 후보 누적 개수의 증가로 판정.
        self._internal_seen_count = 0

        # F2 화자분리 후처리(opt-in): 실시간 전사는 화자 라벨을 못 만들므로, 활성화 시
        # 스트리밍 PCM을 모아 두었다가 종료 후 diarize 모델로 재전사해 speaker를 채운다.
        # 메모리(약 173MB/시간)·STT 재호출 비용이 있어 기본 꺼짐.
        self._diarize_pp = False
        self._pcm = bytearray()
        # 2-pass 보정(HTTP 청크 모드): 빠른 패스 조각을 윈도 단위로 재전사해 문장으로
        # 교체한다. _pcm_base_sec 는 _pcm[0]이 세션 타임라인에서 갖는 시각(보정 완료
        # 구간을 폐기하며 전진).
        self._two_pass = False
        self._pcm_base_sec = 0.0
        # STT 호출이 실패해 폐기한 청크 수 — 종료 시 "저장할 내용이 없다"의 원인을
        # 무발화와 구분해 안내하기 위해 센다(_finalize 참조).
        self._stt_failed_chunks = 0
        # 예외 없이 빈 텍스트만 돌아온 청크 수. 이것만으로는 원인을 단정할 수 없다 —
        # 무음 청크 드롭(realtime.drop_silent_chunks)을 끄면 무음도 STT 로 가고, 켜져
        # 있어도 발화 판정은 RMS 임계값 한 번 넘김이라 잡음 구간이 통과한다. 그래서
        # 세그먼트가 0인 종료 분기에서 "가능한 원인 두 가지"를 함께 알리는 데만 쓴다.
        self._stt_empty_chunks = 0
        # 실제로 전사를 만든 (제공자, 모델) — 등장 순서 유지. WS/HTTP·폴백 단계에 따라
        # 달라지므로 단일 값이 아니다. 회의록의 녹취 출처 메타가 이 값을 쓴다:
        # 설정값 모델을 적으면 폴백이 일어난 회의에 틀린 감사 기록이 남는다.
        # **인스턴스 속성** — 웹은 세션이 동시에 돌아 전역이면 섞인다.
        self._stt_models_used: List[tuple] = []

    def _note_stt_model(self, provider: str, model: str) -> None:
        if provider and (provider, model) not in self._stt_models_used:
            self._stt_models_used.append((provider, model))

    def stt_usage(self) -> Dict[str, Any]:
        """finalize.SessionInputs 에 넣을 실측 STT 메타."""
        used = list(self._stt_models_used)
        configured = str(self.config.get("stt_model") or "").strip()
        primary = ("OpenAI", configured) if configured else None
        return {
            "stt_providers": [p for p, _ in used],
            "stt_models": [m for _, m in used],
            "stt_fallback_used": bool(used) and (
                any(p != "OpenAI" for p, _ in used)
                or (primary is not None and any(u != primary for u in used))
            ),
        }

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

        self._searcher = self._create_searcher(topic)

        # DB 세션 생성
        self.session_id = db.create_session(
            title=title or f"실시간 녹음 {datetime.now().strftime('%y%m%d-%H%M')}",
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

        # API 키 확인을 통과한 뒤에 만든다 — 위의 조기 return 경로에서 유휴
        # 스레드풀이 남지 않게 한다(게이트가 꺼져 있으면 어차피 no-op).
        self._facilitator = self._create_facilitator(topic)

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
            realtime_mode = cfg.get("realtime.mode", "http")
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
        # 녹음별 임시 모델 오버라이드: 프런트(녹음 화면)가 config에 stt_model을 실어 보내면
        # config.json의 기본값 대신 이번 세션에만 그 모델을 쓴다(설정은 그대로 유지).
        _stt_ov = str(self.config.get("stt_model") or "").strip()
        stt_model_cfg = _stt_ov or (cfg.get("models.stt", "gpt-4o-mini-transcribe") or "gpt-4o-mini-transcribe")
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
                # 언어 고정(auto 금지) — HTTP 경로와 동일 정책
                session_cfg = build_ws_session_config(
                    stt_model, _resolve_stt_language(language, cfg.get), cfg.get)

                conn.session.update(session=session_cfg)

                await self.ws.send_json({"type": "ready", "model": stt_model})
                self._note_stt_model("OpenAI", stt_model)

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
        if self._facilitator is not None:
            self._facilitator.shutdown(wait=True)
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
            # HTTP 경로와 동일한 환각 방어(WS 경로는 서버 VAD 덕에 발생이 드물지만
            # 파리티를 맞춘다): 문장 내부 되풀이 축약 → 직전 확정문과 중복이면 폐기
            # → 이질 문자는 삭제하지 않고 [불명] 표시.
            if _c_get("realtime.hallucination_filter", True):
                final_text = _collapse_repetitions(final_text)
                prev = ""
                with self._event_lock:
                    if self.segments:
                        prev = self.segments[-1].get("text") or ""
                if not final_text or (prev and _is_near_duplicate(final_text, prev)):
                    self._cleanup_item(item_id)
                    return
                if _is_script_mismatch(final_text, _resolve_stt_language(language, _c_get)):
                    final_text = _mark_suspect(final_text)

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

            # 영어(원문)를 번역 대기 없이 즉시 전송 — HTTP 경로와 동일하게 확정 문장을
            # 먼저 보여주고 번역은 translation 이벤트로 뒤따라 붙는다.
            # (과거엔 번역 완료까지 확정 세그먼트 전송을 미뤄 표시가 LLM 왕복만큼 늦었다)
            self._send_to_browser({
                "type": "segment",
                "itemId": item_id,
                "text": final_text,
                "speaker": "",
                "start": start_sec,
                "end": elapsed,
            })
            # 번역 게이트: `language == "en"` 정확 일치는 auto 등에서 번역을 통째로
            # 건너뛰었다 — HTTP 경로와 동일하게 '한국어만 아니면' 시도한다.
            if translate and (language or "").strip().lower() != "ko" and final_text.strip():
                self._translator_pool.submit(
                    self._translate_segment,
                    final_text, seg, openai_client, translate_model, topic,
                )

            # 실시간 Vault/웹 검색 (설정된 경우, 비차단)
            # vault 검색 게이트/스로틀은 RealtimeVaultSearcher 내부에서 처리
            if self._searcher is not None:
                self._searcher.offer_segment(final_text)
            # 회의 진행 페르소나 트리아지 — 같은 계약(논블로킹, 시간 게이트는 내부).
            # 구간 시각을 함께 넘긴다: 관찰 로그가 전사와 대조 가능한 좌표를 갖는다.
            # 이 경로(Realtime WS 의 completed)는 확정 전사라 provisional=False.
            if self._facilitator is not None:
                self._facilitator.offer_segment(
                    final_text, t0=start_sec, t1=elapsed, provisional=False)
            self._segment_counter += 1
            self._maybe_web_research(final_text)

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

    def _apply_revision(self, t0: float, t1: float, new_segs: List[Dict]) -> None:
        """[t0, t1) 구간(start 기준)의 세그먼트를 보정본으로 교체(메모리).

        빠른 패스 조각과 보정 문장의 경계가 정확히 일치하지 않아도 되도록
        구간 시간 기반으로 걸러낸다. finalize 는 self.segments 를 그대로 쓰므로
        여기서 교체해 두면 회의록도 보정본 기준이 된다.
        """
        with self._event_lock:
            kept = [s for s in self.segments if not (t0 <= s.get("start", 0) < t1)]
            self.segments = sorted(kept + list(new_segs), key=lambda s: s.get("start", 0))

    def _translate_batch(self, texts: List[str], openai_client, translate_model, topic) -> List[str]:
        """보정 윈도의 문장들을 chat 1회로 일괄 번역(문맥 일관·저비용).

        반환 리스트 길이는 입력과 동일하게 맞춘다(부족분은 "" 패딩).
        조각별 _translate_text N회 호출 대비 문장 간 문맥이 유지되고 호출 수가 준다.
        """
        from meeting_minutes_app.meeting_pipeline.json_utils import parse_json_loose
        topic_hint = f"\n주제 맥락: {topic}" if topic else ""
        numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts))
        r = openai_client.chat.completions.create(
            model=translate_model,
            temperature=0.2,
            messages=[
                {"role": "system",
                 "content": (f"전문 영한 번역가. 아래 번호 매긴 발화 각각을 자연스러운 한국어로 번역.{topic_hint}\n"
                             "JSON 배열로만 출력: [\"번역1\", \"번역2\", ...] — 입력과 같은 개수·순서.\n"
                             "반드시 한국어로만 출력.")},
                {"role": "user", "content": numbered},
            ],
        )
        raw = (r.choices[0].message.content or "").strip()
        arr = parse_json_loose(raw, expect="list", default=None)
        if not isinstance(arr, list):
            raise ValueError("번역 응답 파싱 실패")
        out = [str(x).strip() for x in arr][:len(texts)]
        out += [""] * (len(texts) - len(out))
        # LLM이 개수를 못 맞춰 비는 항목은 건별 번역으로 보충 — 과거엔 "" 패딩으로
        # 일부 문장만 번역이 조용히 누락됐다. (이 함수는 워커 스레드에서 실행됨)
        for i, (src, ko) in enumerate(zip(texts, out)):
            if not ko and src.strip():
                try:
                    out[i] = self._translate_text(src, openai_client, translate_model, topic)
                except Exception as _e:
                    print(f"[translate-batch] 건별 보충 실패(영문 유지): {_e}")
        return out

    def _translate_segment(self, text, seg, openai_client, translate_model, topic):
        """세그먼트 번역 (백그라운드 스레드).

        주의: 이는 실시간 '스트리밍' 번역(발화 1건씩 즉시)로, 배치용
        stt.translate_segments(전체 세그먼트를 컨텍스트 윈도우로 일괄 번역)와는
        실행 맥락이 다른 **의도된 별도 구현**이다. 둘을 통합하면 실시간 지연·스트리밍이 깨지므로
        합치지 말 것. (회의록 본문 생성 LLM은 config.models.llm을 따름 — 번역만 OpenAI 고정)
        """
        try:
            ko_text = self._translate_text(text, openai_client, translate_model, topic)
            seg["translated_text"] = ko_text
            # DB에도 반영 — 과거엔 메모리/화면에만 채워져 세션을 다시 열면 번역이 사라졌다
            if self.session_id:
                try:
                    db.update_segment_translation(self.session_id, seg["start"], ko_text)
                except Exception as _de:
                    print(f"[realtime] 번역 DB 반영 실패(표시는 유지): {_de}")
            # 영어 세그먼트는 이미 전송됨 — 번역만 뒤따라 채운다(HTTP 경로와 동일 이벤트)
            self._send_to_browser({
                "type": "translation",
                "start": seg["start"],
                "end": seg["end"],
                "translatedText": ko_text,
            })
        except Exception as e:
            self._send_to_browser({
                "type": "translation",
                "start": seg["start"],
                "end": seg["end"],
                "translatedText": "",
                "translateError": str(e),
            })

    def _create_searcher(self, topic: str):
        """실시간 관련 노트 검색기 생성 (config.wiki.realtime_vault_search 게이트는
        모듈 내부에서 검사). warmup()으로 백엔드 연결을 미리 확인해 첫 발화를
        기다리지 않고 상태 배지를 띄운다 — 비활성이면 사유까지(과거엔 조용히
        no-op이라 원인 불명이었다). 생성 실패는 녹음을 막지 않는다."""
        try:
            from meeting_minutes_app.wiki_core.realtime_search import RealtimeVaultSearcher
            searcher = RealtimeVaultSearcher(
                topic=topic, on_notes=self._emit_related_notes,
                on_status=self._emit_search_status, allow_launch=True)
            searcher.warmup()
            return searcher
        except Exception:
            return None

    def _create_facilitator(self, topic: str):
        """회의 진행 페르소나 오케스트레이터(M0 관찰모드) 생성.

        게이트(config.facilitation.enabled, 기본 꺼짐)는 모듈 내부에서 검사한다 —
        꺼져 있으면 스레드풀도 만들지 않는 no-op 이라 LLM 호출이 0회다.
        관찰모드라 화면(WS) 이벤트는 보내지 않는다 — 판정은 facilitation_log 에만
        남는다. 생성 실패는 녹음을 막지 않는다(_create_searcher 와 같은 규칙)."""
        try:
            from meeting_minutes_app.wiki_core.facilitation import FacilitationOrchestrator
            return FacilitationOrchestrator(
                session_id=self.session_id or "", topic=topic)
        except Exception:
            return None

    def _emit_related_notes(self, notes: List[Dict]) -> None:
        """RealtimeVaultSearcher 검색 풀 스레드에서 호출 — 관련 노트를 브라우저로 push.

        페이로드는 기존 related_notes 이벤트의 superset — 근거 추적(FR-3)을 위해
        섹션경로·heading·score·출처유형을 함께 싣는다. 표시 정책(비방해·내부 우선)은
        프런트(Recorder)가 담당한다.
        """
        try:
            self._send_to_browser({
                "type": "related_notes",
                "notes": [
                    {
                        "filename": n.get("filename", ""),
                        "title": n.get("title", ""),
                        "score": round(float(n.get("score", 0) or 0), 3),
                        "rankScore": round(float(n.get("rank_score", 0) or 0), 6),
                        "matches": (n.get("matches") or [])[:2],
                        "snippet": n.get("snippet", ""),
                        "heading": n.get("heading", ""),
                        "sectionPath": n.get("section_path", ""),
                        "sourceType": n.get("source_type", "note"),
                        "foundBy": n.get("found_by", ""),
                        "segmentText": n.get("segment_text", ""),
                    }
                    for n in notes
                ],
                "elapsed": time.time() - self._session_start,
            })
        except Exception:
            pass  # 전송 실패는 무시 (실시간 스트림에 영향 없어야 함)

    def _emit_search_status(self, status: Dict) -> None:
        """실시간 검색 백엔드 연결 상태/비활성 사유를 배지용으로 push (FR-1).

        notes 는 비우고 status 만 실어 보낸다 — 프런트는 기존 목록을 유지한다.
        """
        try:
            self._send_to_browser({
                "type": "related_notes",
                "notes": [],
                "status": {
                    "enabled": bool(status.get("enabled")),
                    "gate": bool(status.get("gate")),
                    "backend": status.get("backend", ""),
                    "reason": status.get("reason", ""),
                    "reasonText": status.get("reasonText", ""),
                },
                "elapsed": time.time() - self._session_start,
            })
        except Exception:
            pass

    def _note_web_skip(self, message: str) -> None:
        """웹 검색 보완을 건너뛴 사유를 한 세션에 한 번만 알린다.

        매 세그먼트 출력하면 로그·화면이 같은 줄로 도배된다(웹 검색은 interval 마다
        돈다). 그러나 아예 조용하면 "기능이 없는 것처럼" 보인다 — 이 리포의 반복 규칙."""
        if getattr(self, "_web_skip_notified", "") == message:
            return
        self._web_skip_notified = message
        print(f"[realtime] {message}")

    def _maybe_web_research(self, text: str) -> None:
        """웹 보완 검색 트리거 — 게이트/스로틀 + '내부에서 못 찾았을 때만' 정책(FR-11).

        내부(vault) 후보가 새로 잡힌 구간에서는 웹을 건너뛴다. 항상 웹도 함께 보려면
        wiki.realtime_web_only_if_no_vault_hit=false.
        """
        try:
            from meeting_minutes_app.common import config_loader as _rc
            online_search_on = bool(_rc.get("wiki.online_search_enabled", False))
            web_interval = int(_rc.get("wiki.realtime_web_search_interval", 0) or 0)
            vault_first = bool(_rc.get("wiki.realtime_web_only_if_no_vault_hit", True))
        except Exception:
            return
        if not online_search_on or web_interval <= 0:
            return
        if self._segment_counter % web_interval != 0:
            return
        # 내용 없는 발화로 웹 검색을 쏘지 않는다 — vault 검색과 **같은 문턱**을 쓴다.
        # 웹은 vault 와 달리 실제 API 호출·비용·지연이 붙으므로 이 낭비가 더 크다
        # (예전엔 "다음 회의는 다음 주 화요일입니다" 로도 웹 리서치가 나갔다).
        # 판정 실패가 실시간 스트림을 깨지 않게 감싼다 — 이 파일의 규칙(예외 무전파).
        try:
            if (self._searcher is not None
                    and not self._searcher.has_searchable_content(text)):
                return
        except Exception:
            pass
        if vault_first and self._searcher is not None and self._searcher.enabled:
            found = len(self._searcher.collected_notes())
            if found > self._internal_seen_count:
                self._internal_seen_count = found
                return  # 내부자료로 충분 — 웹 호출(비용·지연) 생략
        self._web_pool.submit(self._web_research_segment, text)

    def _web_research_segment(self, text: str) -> None:
        """세그먼트 텍스트로 웹 검색 보완 (백그라운드 스레드, 비차단).

        결과는 회의록 memo 병합용으로 누적하고, 화면에는 내부 결과와 같은 바에
        웹(🌐) 출처로 뒤이어 표시한다(FR-10 — 내부가 앞줄).

        **비용 3종을 지난다.** 이 호출은 회의 중 자동으로 나가는 외부 유료 검색인데
        (Anthropic web_search 는 검색 1,000회당 $10) 지금까지 한도 검사도 기록도 없었다 —
        PRD §10 감사가 "realtime.py 에 spend_guard 참조 0건"으로 지적한 그 자리다.
        그래서 회의 중 웹검색 비용은 월 합계에서 보이지 않았고, 안 보이는 만큼
        **다른 경로의 한도 판정까지 왜곡**했다(합계가 실제보다 작게 나온다).
        """
        try:
            from meeting_minutes_app.common import config_loader as _rc
            from meeting_minutes_app.common import pricing, spend_guard
            from meeting_minutes_app.meeting_pipeline import meeting_minutes as mm

            # 회의 중 자동 실행 — facilitation 트리아지와 같은 관문을 지난다.
            if spend_guard.automation_paused():
                self._note_web_skip("자동 실행 일시정지로 웹 검색 보완을 건너뜁니다")
                return
            # 단가 기준을 llm_client.web_research 의 **실제 경로**에 맞춘다:
            #  · 라이브 검색이 됐다면 1순위인 Anthropic web_search 를 지났을 가능성이
            #    높다(models.llm 과 무관하게 먼저 시도한다). GPT responses 폴백도
            #    searched=True 를 낼 수 있으나 그쪽이 더 싸므로 claude 기준이 보수적이다.
            #  · 검색 없이 강등됐다면 최종 폴백이 chat() 이므로 models.llm 기준이다.
            _pref = _rc.get("models.llm", "gpt") or "gpt"
            _claude_model = _rc.get("models.claude_model") or None
            _pref_model = (_claude_model if str(_pref).startswith("claude")
                           else _rc.get("models.gpt_model")) or None
            # 한도 판정에는 상한(라이브 검색 성공 가정)을 넣고, 기록은 실제 결과의
            # searched 로 다시 계산한다 — 강등된 회차를 검색 요금까지 물릴 이유가 없다.
            _est = pricing.web_research_call_cost(_claude_model, searched=True,
                                                  llm="claude")
            _reason = spend_guard.blocked(_est, check_per_item=False)
            if _reason:
                # 조용히 건너뛰지 않는다 — 사유를 남긴다(CLAUDE.md).
                self._note_web_skip(f"지출 한도로 웹 검색 보완 보류: {_reason}")
                return
            llm = mm.LLMClient(preferred=_pref)
            # 쿼리 길이는 내부 검색과 같은 설정을 쓴다 — 60자 하드코딩이던 과거엔
            # 실측(60→180자에서 R@3 +0.17)이 웹 경로에만 반영되지 않았다.
            _qchars = max(int(_rc.get("wiki.realtime_query_chars", 180) or 180), 20)
            result = llm.web_research(text[:_qchars])
            # 호출은 이미 나갔다 — 결과가 비어도 과금은 발생했으므로 먼저 기록한다.
            _searched = bool((result or {}).get("searched"))
            spend_guard.record(
                spend_guard.KIND_WEB_RESEARCH,
                pricing.web_research_call_cost(
                    _claude_model if _searched else _pref_model,
                    searched=_searched, llm=("claude" if _searched else _pref)),
                model=str((_claude_model if _searched else _pref_model) or ""),
                units=1, unit_kind="web_research_call",
                note=spend_guard.session_note(self.session_id or ""))
            if result and result.get("text"):
                sources = result.get("sources", [])[:3]
                with self._notes_lock:
                    self._web_findings.append({
                        "segment_text": text[:80],
                        "result": result.get("text", "")[:500],
                        "sources": sources,
                    })
                self._emit_related_notes([{
                    "filename": (sources[0] if sources else ""),
                    "title": (str(sources[0])[:60] if sources else "웹 검색 결과"),
                    "score": 0.0,
                    "rank_score": 0.0,
                    "snippet": result.get("text", "")[:200],
                    "section_path": "",
                    "source_type": "web",
                    "found_by": "web",
                    "segment_text": text[:200],
                }])
        except Exception:
            pass

    def _send_to_browser(self, data: dict):
        """스레드 안전한 WebSocket 전송. 큐에 넣으면 메인 루프에서 처리."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return

        def _put():
            # QueueFull은 콜백 안(루프 스레드)에서 발생한다 — call_soon_threadsafe 는
            # 예약만 하므로 바깥 except 로는 절대 잡히지 않는다(과거 사문 코드 + 포화
            # 시 이벤트마다 'Exception in callback' 트레이스백 스팸).
            try:
                self._send_queue.put_nowait(data)
            except asyncio.QueueFull:
                pass  # 큐 포화 시 최신 데이터 드롭 (오래된 데이터 유지가 더 나쁨)

        try:
            loop.call_soon_threadsafe(_put)
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
        """WebSocket 연결 실패 시(또는 config realtime.mode='http') HTTP 청크 방식 폴백.

        전사는 배치와 동일한 공유 경로(stt.transcribe_chunk)를 재사용해 모델별로 올바른
        response_format/파싱을 자동 적용한다 — 과거엔 여기만 별도 ad-hoc 호출
        (response_format='text')이라 diarize 등 일부 모델에서 매 청크가 조용히 실패해
        화면에 아무것도 안 뜨는 버그가 반복됐다. 실시간 청크에는 스트리밍 적합 평문
        모델을 쓰고(화자분리는 종료 후 _diarize_postprocess가 담당), WS 경로와 동일한
        normalize_ws_model 정책으로 정규화한다.
        """
        from meeting_minutes_app.common.realtime_ws_session import normalize_ws_model
        from meeting_minutes_app.meeting_pipeline import stt

        # STT 전용 클라이언트 — 폴백(폴백모델·Groq)이 있으므로 한 벤더에 오래 매달릴
        # 이유가 없다. SDK 기본값(요청 600초 × 재시도 2회)을 그대로 쓰면 응답 없이
        # 매달리는 장애에서 청크 하나가 수십 분 막혀 라이브가 멈춘 것처럼 보인다.
        # openai_client 자체를 좁히지 않는 이유: 이 객체는 WS realtime.connect 와
        # 번역도 공유한다 → with_options 로 **사본만** 좁힌다(하위 httpx 는 공유).
        try:
            stt_client = openai_client.with_options(
                timeout=stt.STT_REQUEST_TIMEOUT_SEC,
                max_retries=stt.STT_MAX_RETRIES,
            )
        except Exception as _woe:   # 구버전 SDK 등 — 한도 없이라도 동작은 유지
            print(f"[http-stt] STT 클라이언트 한도 적용 실패(기본값 사용): {_woe}")
            stt_client = openai_client

        # 녹음별 임시 모델 오버라이드(config에 stt_model이 실려오면 우선) — 설정 기본값은 유지
        _stt_ov = str(self.config.get("stt_model") or "").strip()
        raw_model = _stt_ov or (cfg.get("models.stt", "gpt-4o-mini-transcribe") or "gpt-4o-mini-transcribe")
        stt_model, _norm_reason = normalize_ws_model(raw_model)
        if _norm_reason:
            print(f"[http-stt] 모델 정규화: {raw_model} → {stt_model} ({_norm_reason})")
        await self.ws.send_json({"type": "fallback_http", "model": stt_model})

        import wave
        import tempfile
        import array

        SR = 24000
        BYTES_PER_SEC = SR * 2          # int16 mono
        MIN_CHUNK_SEC = 1.5             # 너무 잘게 자르지 않기 위한 하한
        try:                            # 무음이 없어도 이 길이에서 강제 분할(지연 상한)
            MAX_CHUNK_SEC = float(cfg.get("realtime.fast_max_chunk_sec", 5.0) or 5.0)
        except (TypeError, ValueError):
            MAX_CHUNK_SEC = 5.0
        MAX_CHUNK_SEC = min(max(MAX_CHUNK_SEC, MIN_CHUNK_SEC), 30.0)
        SILENCE_HOLD_SEC = 0.5          # 이만큼 조용하면 발화 경계로 보고 분할
        try:                            # int16 RMS 임계값(이하=무음) — 마이크 게인이 낮으면
            SILENCE_RMS = float(cfg.get("realtime.silence_rms", 300) or 300)
        except (TypeError, ValueError):  # 발화도 무음 판정돼 1.5초 조각으로 잘게 잘리므로 조정 가능
            SILENCE_RMS = 300.0
        # 빠른 패스 STT 동시 실행 상한 — 1이면 완전 직렬(과거 동작). STT 왕복이 청크
        # 길이보다 느린 환경(프록시 등)에서 직렬 처리는 지연이 세션 내내 누적된다.
        # 전사는 병렬로 돌리되 화면 발행(emit)은 이벤트 체인으로 순서를 보장한다.
        try:
            STT_CONCURRENCY = int(cfg.get("realtime.stt_concurrency", 2) or 2)
        except (TypeError, ValueError):
            STT_CONCURRENCY = 2
        STT_CONCURRENCY = min(max(STT_CONCURRENCY, 1), 4)
        # 무음 청크를 STT 로 보내지 않는다 — 무음/잡음을 전사시키면 모델이 없는 말을
        # 만들어내고(외국어 조각), 그것이 문맥으로 되먹여져 반복까지 유발한다.
        DROP_SILENT = bool(cfg.get("realtime.drop_silent_chunks", True))
        FILTER_ON = bool(cfg.get("realtime.hallucination_filter", True))
        # 언어 고정(auto 금지) — 청크별 언어 재판정이 러시아어 환각의 직접 원인이었다.
        stt_language = _resolve_stt_language(language, cfg.get)

        audio_buffer = bytearray()
        audio_pos_sec = 0.0             # 큐로 보낸 오디오 누적 길이(세그먼트 타임스탬프 기준)
        silence_sec = 0.0               # 현재 버퍼 끝의 연속 무음 길이
        chunk_has_speech = False        # 현재 버퍼에 발화 에너지가 한 번이라도 있었는지
        silent_chunks = 0               # STT 를 건너뛴 무음 청크 수 (종료 시 1회 로그)
        silent_secs = 0.0
        dropped_dup = 0                 # 직전과 동일해서 발행하지 않은 조각 수
        marked_suspect = 0              # 환각 의심으로 표시한 조각 수
        stt_fail_streak = 0             # 연속 STT 실패 카운트 (조용한 실패 방지)
        stt_notified = False            # 사용자 통지는 1회만
        # 다른 벤더(Groq) 폴백 — OpenAI 기본·폴백 모델이 모두 실패한 청크에만 쓴다.
        # 키가 없으면 (None, "") 이라 이 단계는 자동으로 건너뛰어진다.
        groq_client, groq_model = stt.groq_fallback()
        groq_notified = False           # 벤더 전환 알림도 1회만
        last_emitted_text = ""          # 직전 발행 텍스트(연속 중복·prompt 에코 억제)
        prompt_tail = ""                # 직전 전사 꼬리 (prompt_context="tail" 일 때만 사용)

        # STT는 단일 소비자 태스크에서만 수행한다 → 수신 루프가 STT로 절대 막히지 않아
        # 연속 발화 중에도 오디오를 계속 받아들인다(과거엔 수신 루프에서 STT를 await해
        # 처리 중 들어온 소리가 밀리고 5초 하드 컷으로 경계 단어가 누락됐다).
        stt_queue: asyncio.Queue = asyncio.Queue(maxsize=32)
        emitted_pos_sec = 0.0           # 빠른 패스가 emit 완료한 오디오 시각(보정 순서 보장용)
        consumer_done = False

        # ── 2-pass 보정 설정 ──
        # 빠른 패스는 조각을 즉시 표시하고(provisional), 보정 패스가 윈도 단위로
        # 재전사해 문장으로 교체한다("최종 STT는 수정 한 번").
        self._two_pass = bool(cfg.get("realtime.two_pass", True))
        try:
            REVISE_WINDOW_SEC = float(cfg.get("realtime.revise_window_sec", 25.0) or 25.0)
        except (TypeError, ValueError):
            REVISE_WINDOW_SEC = 25.0
        REVISE_WINDOW_SEC = min(max(REVISE_WINDOW_SEC, 10.0), 120.0)
        _revise_raw = cfg.get("realtime.revise_model", "gpt-4o-transcribe") or "gpt-4o-transcribe"
        revise_model, _rv_reason = normalize_ws_model(_revise_raw)  # diarize 등 부적합 모델 방지
        revise_pos_sec = 0.0            # 여기까지 보정 윈도 발행됨
        revise_queue: asyncio.Queue = asyncio.Queue(maxsize=4)
        # 번역 게이트: 기존 `language == "en"` 정확 일치는 auto 등에서 번역을 통째로
        # 건너뛰었다. 한국어 발화만 아니면 시도한다(_translate_text 는 한국어만 출력).
        translate_enabled = bool(translate) and (language or "").strip().lower() != "ko"
        translate_sem = asyncio.Semaphore(2)  # 빠른 패스 번역 동시 실행 상한
        fast_tr_tasks: set = set()            # 진행 중 번역 태스크(종료 시 드레인)

        # ── prompt 문맥 정책 ──
        # 과거엔 직전 전사 꼬리를 다음 청크·윈도의 prompt 로 되먹였다. 모델이 그
        # 문장을 되풀이하고 그 출력이 다시 꼬리가 되면서 같은 문장이 세션 내내
        # 반복되는 자기강화 루프가 생겼다(에코 제거는 접두부만 잡아 한계).
        # 기본은 세션 내내 불변인 "정적 힌트" — 루프가 원리적으로 불가능하다.
        PROMPT_MODE = str(cfg.get("realtime.prompt_context", "static") or "static").strip().lower()
        if PROMPT_MODE not in ("static", "tail", "off"):
            PROMPT_MODE = "static"
        _static_bits: List[str] = []
        if topic:
            _static_bits.append(f"주제: {topic}")
        if speakers:
            _static_bits.append(f"참석자: {speakers}")
        if stt_language.startswith("ko"):
            _static_bits.append("한국어 회의 녹음입니다.")
        static_prompt = " ".join(_static_bits)[:400]

        def _chunk_prompt() -> str:
            if PROMPT_MODE == "off":
                return ""
            if PROMPT_MODE == "static":
                return static_prompt
            return f"{static_prompt} {prompt_tail}".strip()

        def _write_wav(chunk_bytes: bytes) -> str:
            tmp_wav = tempfile.NamedTemporaryFile(
                prefix="mm_rt_chunk_", suffix=".wav", delete=False)
            tmp_path = tmp_wav.name
            tmp_wav.close()
            with wave.open(tmp_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(SR)
                wf.writeframes(chunk_bytes)
            return tmp_path

        async def _translate_fast(seg: dict):
            """빠른 패스 세그먼트의 비동기 번역 — 완료되면 translation 이벤트로 갱신.

            과거엔 번역을 이벤트 루프에서 동기 호출해 번역이 도는 동안 수신·STT까지
            전부 멈췄고, 영어 표시도 번역 완료까지 지연됐다. 이제 영어를 먼저 보내고
            번역은 뒤따라 붙인다. 실패해도 영어 표시는 유지(보정 패스가 재번역).
            """
            async with translate_sem:
                for attempt in (1, 2):
                    try:
                        ko = await asyncio.to_thread(
                            self._translate_text, seg["text"],
                            openai_client, translate_model, topic)
                        seg["translated_text"] = ko
                        await self.ws.send_json({
                            "type": "translation",
                            "start": seg["start"], "end": seg["end"],
                            "translatedText": ko,
                        })
                        return
                    except Exception as _te:
                        if attempt == 2:
                            print(f"[fast-translate] 포기(영어 표시 유지): {_te}")

        async def _transcribe_chunk_bytes(chunk_bytes: bytes, c_start: float) -> str:
            """청크 STT — 병렬 실행 가능 구간(emit 은 별도 순서 보장).

            배치와 동일한 model-aware 전사 경로 재사용(blocking → to_thread).
            직전 전사 꼬리를 prompt 로 전달해 경계 오인식·언어 환각을 줄인다.
            (동시 실행 중엔 문맥이 한 청크 전 것일 수 있다 — 힌트 용도라 무해.)
            """
            nonlocal groq_notified
            tmp_path = _write_wav(chunk_bytes)
            used_prompt = _chunk_prompt()
            try:
                try:
                    segs = await asyncio.to_thread(
                        stt.transcribe_chunk,
                        stt_client, tmp_path, stt_model,
                        stt_language,
                        None, c_start, prompt=used_prompt or None,
                    )
                    self._note_stt_model("OpenAI", stt_model)
                except Exception as _e1:
                    # 폴백 모델 1회 재시도 — 과거엔 청크 예외 시 텍스트가 조용히
                    # 소실됐다(run_stt 의 폴백 로직을 우회하는 경로라서).
                    fb = stt.FALLBACK_STT_MODEL
                    try:
                        if not fb or fb == stt_model:
                            raise _e1
                        print(f"[http-stt] {stt_model} 실패 → {fb} 재시도: {_e1}")
                        segs = await asyncio.to_thread(
                            stt.transcribe_chunk,
                            stt_client, tmp_path, fb,
                            stt_language,
                            None, c_start, prompt=used_prompt or None,
                        )
                        self._note_stt_model("OpenAI", fb)
                    except Exception as _e2:
                        # OpenAI 두 모델이 모두 실패 = 벤더 장애 가능성 → 다른 벤더(Groq).
                        # 로컬(faster-whisper)은 라이브 청크에 쓰지 않는다(CPU 전사가
                        # 실시간을 못 따라감). 주의: 종료 후 재전사(로컬까지 포함한
                        # stt.run_stt)는 realtime.diarize_postprocess 가 켜진 세션에서만
                        # 돈다(_diarize_postprocess). 기본값은 꺼짐이므로 Groq 까지 실패한
                        # 청크는 그대로 폐기된다 — 그 경우 _finalize 가 원인을 안내한다.
                        if groq_client is None:
                            raise
                        print(f"[http-stt] OpenAI 실패 → Groq/{groq_model} 폴백: {_e2}")
                        segs = await asyncio.to_thread(
                            stt.transcribe_chunk,
                            groq_client, tmp_path, groq_model,
                            stt_language,
                            None, c_start, prompt=used_prompt or None,
                            # provider 를 넘기면 벤더 전용 파라미터(chunking_strategy
                            # 등)와 prompt 정책(Groq/whisper 는 224토큰 상한이라 생략)이
                            # stt.stt_request_params 한 곳에서 걸러진다.
                            provider="Groq",
                        )
                        self._note_stt_model("Groq", groq_model)
                        if not groq_notified:
                            groq_notified = True
                            await self.ws.send_json({
                                "type": "fallback_provider", "provider": "Groq",
                                "model": groq_model,
                            })
                text = " ".join(
                    (s.get("text") or "").strip() for s in segs
                    if (s.get("text") or "").strip()
                ).strip()
                # 모델이 prompt(직전 문장·정적 힌트)를 출력에 되풀이하면 그 부분을
                # 제거하고, 조각 안에서 되풀이되는 문장·구절도 1회로 축약한다 —
                # 화면에 같은 문장이 청크마다 중복되는 원인.
                text = _strip_prompt_echo(text, used_prompt)
                return _collapse_repetitions(text) if FILTER_ON else text
            finally:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

        async def _emit_text(text: str, c_start: float, c_end: float):
            """전사 결과 발행 — 메모리/DB 기록, 화면 전송, 번역·vault 검색 트리거."""
            nonlocal prompt_tail, last_emitted_text, dropped_dup, marked_suspect
            if not text or _is_cjk_hallucination(text):
                return
            if FILTER_ON:
                # 직전 조각과 같은 말이면 발행하지 않는다 — 사람이 이어 말한 것이
                # 아니라 모델이 prompt/직전 출력을 되풀이한 경우다.
                if last_emitted_text and _is_near_duplicate(text, last_emitted_text):
                    dropped_dup += 1
                    return
                # 이질 문자(키릴 등)는 삭제하지 않고 표시만 — 오삭제 방지.
                if _is_script_mismatch(text, stt_language):
                    text = _mark_suspect(text)
                    marked_suspect += 1
            seg = {
                "start": c_start, "end": c_end, "text": text,
                "text_original": text, "speaker": "",
            }
            with self._event_lock:
                self.segments.append(seg)
            if self.session_id:
                db.add_segment(self.session_id, "", text, c_start, c_end)
            last_emitted_text = text
            # 꼬리 문맥은 (a) tail 모드에서만, (b) 환각 표시가 붙지 않은 깨끗한
            # 텍스트만, (c) 짧게(120자) 유지한다 — 루프 재발 방지.
            if PROMPT_MODE == "tail" and not text.startswith(SUSPECT_MARKER):
                prompt_tail = f"{prompt_tail} {text}"[-120:]
            # 영어(원문)를 번역 대기 없이 즉시 전송 — provisional 은 보정 패스가
            # 나중에 문장으로 교체할 수 있음을 프런트에 알린다(흐린 표시).
            await self.ws.send_json({
                "type": "segment", "text": text, "translatedText": "",
                "speaker": "", "start": c_start, "end": c_end,
                "provisional": bool(self._two_pass),
            })
            if translate_enabled:
                _t = asyncio.create_task(_translate_fast(seg))
                fast_tr_tasks.add(_t)
                _t.add_done_callback(fast_tr_tasks.discard)
            # 실시간 관련정보: 내부(vault) 검색은 항상, 웹 보완은 게이트+내부 미발견 시만.
            # (과거엔 HTTP 청크 경로에 웹 보완 트리거가 아예 없어, 기본 모드에서
            #  realtime_web_search_interval 을 켜도 아무 일도 일어나지 않았다.)
            if self._searcher is not None:
                self._searcher.offer_segment(text)
            # 회의 진행 페르소나 트리아지 — 같은 계약(논블로킹, 시간 게이트는 내부).
            # 이 경로의 텍스트는 2단계 보정이 켜져 있으면 **보정 전 조각**이고
            # (위 segment 이벤트의 provisional 과 같은 값), 나중 revise 교체분은
            # 다시 offer 되지 않는다 — 관찰 로그가 그 사실을 기록해야 실측에서
            # 조각 기반 판정과 확정 기반 판정을 섞어 세지 않는다(PRD §17).
            if self._facilitator is not None:
                self._facilitator.offer_segment(
                    text, t0=c_start, t1=c_end, provisional=bool(self._two_pass))
            self._segment_counter += 1
            self._maybe_web_research(text)

        stt_sem = asyncio.Semaphore(STT_CONCURRENCY)
        stt_workers: set = set()

        async def _stt_worker(chunk_bytes: bytes, c_start: float, c_end: float,
                              prev_ev: Optional[asyncio.Event], my_ev: asyncio.Event):
            """전사는 즉시(병렬), emit 은 직전 청크 완료 후(이벤트 체인) 수행.

            과거엔 소비자 1개가 전사→emit 을 직렬 수행해, STT 왕복이 청크 길이보다
            느린 환경에선 큐가 계속 쌓여 화면 표시가 세션 내내 뒤로 밀렸다.
            """
            nonlocal stt_fail_streak, stt_notified, emitted_pos_sec
            text: Optional[str] = None
            err: Optional[Exception] = None
            # 직전 청크가 곧 끝나면 잠깐 기다려 그 문맥(prompt)을 그대로 잇는다 —
            # STT가 빠른 평시엔 직렬과 동일하게 문맥이 이어지고, STT가 밀리는 환경에선
            # 문맥 없이 병렬 전사한다(문맥은 정확도 힌트일 뿐, 지연 누적 방지가 우선).
            if prev_ev is not None and not prev_ev.is_set():
                try:
                    await asyncio.wait_for(prev_ev.wait(), timeout=0.75)
                except asyncio.TimeoutError:
                    pass
            try:
                text = await _transcribe_chunk_bytes(chunk_bytes, c_start)
            except Exception as e:
                err = e
            # 직전 청크가 emit 을 끝낸 뒤에만 발행 — 화면/문맥/보정 순서 보장
            if prev_ev is not None:
                await prev_ev.wait()
            try:
                if err is not None:
                    stt_fail_streak += 1
                    self._stt_failed_chunks += 1
                    print(f"[http-stt] error: {err}")
                    if stt_fail_streak >= 2 and not stt_notified:
                        stt_notified = True
                        try:
                            await self.ws.send_json(
                                {"type": "error", "message": f"전사 실패: {err}"})
                        except Exception:
                            pass
                else:
                    stt_fail_streak = 0
                    if not (text or "").strip():
                        # 예외 없이 빈 텍스트 — 원인이 둘이다(무음/저음량 구간이거나,
                        # 제공자가 200 과 함께 아무 내용도 주지 않는 조용한 실패).
                        # 여기서는 세기만 한다: 라이브 중 경고나 다른 제공자 재시도는
                        # 발화 판정(RMS)이 거칠어 오탐 비용이 더 크다.
                        self._stt_empty_chunks += 1
                    await _emit_text(text or "", c_start, c_end)
            except Exception:
                traceback.print_exc()
            finally:
                # 실패해도 전진 — 보정 워커가 이 시각을 기다린다(영구 대기 방지)
                emitted_pos_sec = c_end
                my_ev.set()

        async def _consumer():
            nonlocal consumer_done
            prev_ev: Optional[asyncio.Event] = None
            while True:
                item = await stt_queue.get()
                try:
                    if item is None:
                        # 진행 중인 워커가 모두 emit 을 끝낼 때까지 대기
                        if prev_ev is not None:
                            await prev_ev.wait()
                        consumer_done = True
                        return
                    await stt_sem.acquire()
                    ev = asyncio.Event()
                    t = asyncio.create_task(_stt_worker(item[0], item[1], item[2], prev_ev, ev))
                    t.add_done_callback(lambda _t: stt_sem.release())
                    stt_workers.add(t)
                    t.add_done_callback(stt_workers.discard)
                    prev_ev = ev
                finally:
                    stt_queue.task_done()

        async def _flush_chunk():
            nonlocal audio_buffer, audio_pos_sec, silence_sec, chunk_has_speech
            nonlocal silent_chunks, silent_secs
            if not audio_buffer:
                return
            dur = len(audio_buffer) / BYTES_PER_SEC
            c_start = audio_pos_sec
            audio_pos_sec += dur
            if DROP_SILENT and not chunk_has_speech:
                # 발화 에너지가 전혀 없던 구간은 전사하지 않는다(환각의 최대 원인).
                # 타임라인(audio_pos_sec)은 그대로 전진시켜야 보정 윈도·PCM 슬라이싱
                # 기준이 어긋나지 않는다.
                silent_chunks += 1
                silent_secs += dur
                audio_buffer = bytearray()
                silence_sec = 0.0
                chunk_has_speech = False
                return
            await stt_queue.put((bytes(audio_buffer), c_start, audio_pos_sec))
            audio_buffer = bytearray()
            silence_sec = 0.0
            chunk_has_speech = False

        def _maybe_queue_revision():
            """flush 경계(=세그먼트 경계)에서 보정 윈도가 찼으면 발행.

            큐 포화 시 발행을 미룬다(revise_pos_sec 유지) → 다음 flush 에서 더 큰
            윈도로 재시도되어 구간이 누락되지 않는다.
            """
            nonlocal revise_pos_sec
            if not self._two_pass:
                return
            if (audio_pos_sec - revise_pos_sec) >= REVISE_WINDOW_SEC:
                try:
                    revise_queue.put_nowait((revise_pos_sec, audio_pos_sec))
                    revise_pos_sec = audio_pos_sec
                except asyncio.QueueFull:
                    pass

        async def _revise_worker():
            """보정 패스: 윈도 [t0,t1) PCM 을 풀 모델+문맥 prompt 로 재전사해
            빠른 패스 조각을 문장 세그먼트로 제자리 교체(메모리+DB+화면)."""
            nonlocal prompt_tail
            revise_tail = ""  # 보정 패스 전용 문맥(보정 텍스트가 더 정확)
            while True:
                item = await revise_queue.get()
                try:
                    if item is None:
                        return
                    t0, t1 = item
                    # 빠른 패스가 이 구간을 다 emit할 때까지 대기 — 교체 후에 조각이
                    # 다시 append 되는 역전 방지. consumer 종료 시엔 즉시 진행.
                    while emitted_pos_sec < t1 and not consumer_done:
                        await asyncio.sleep(0.2)
                    b0 = max(0, int((t0 - self._pcm_base_sec) * BYTES_PER_SEC)) & ~1
                    b1 = max(0, int((t1 - self._pcm_base_sec) * BYTES_PER_SEC)) & ~1
                    chunk = bytes(self._pcm[b0:b1])
                    if len(chunk) < BYTES_PER_SEC:  # 1초 미만은 보정 실익 없음
                        continue
                    if DROP_SILENT and _pcm_rms(chunk) < SILENCE_RMS:
                        continue  # 발화가 없는 윈도는 재전사하지 않는다(환각 방지)
                    revise_prompt = ""
                    if PROMPT_MODE == "static":
                        revise_prompt = static_prompt
                    elif PROMPT_MODE == "tail":
                        revise_prompt = f"{static_prompt} {revise_tail}".strip()
                    tmp_path = _write_wav(chunk)
                    try:
                        segs = await asyncio.to_thread(
                            stt.transcribe_chunk,
                            stt_client, tmp_path, revise_model,
                            stt_language,
                            None, t0, prompt=revise_prompt or None,
                        )
                    except Exception as e:
                        print(f"[revise] STT 실패(빠른 패스 결과 유지): {e}")
                        continue
                    finally:
                        try:
                            os.remove(tmp_path)
                        except OSError:
                            pass
                    joined = " ".join(
                        (s.get("text") or "").strip() for s in segs
                        if (s.get("text") or "").strip()
                    ).strip()
                    # prompt(정적 힌트·직전 윈도 문장) 에코 제거 후 문장 단위로
                    # 재분할 — 에코가 남으면 보정 결과가 이전 윈도 내용까지 중복 포함.
                    joined = _strip_prompt_echo(joined, revise_prompt)
                    sentences = [t for t in _split_sentences(joined)
                                 if not _is_cjk_hallucination(t)]
                    if FILTER_ON:
                        sentences = [_collapse_repetitions(t) for t in sentences]
                        sentences = [t for t in sentences if t]
                        # 윈도 결과가 같은 문장의 되풀이로 채워졌다면(모델 반복 루프)
                        # 교체를 포기하고 빠른 패스 결과를 유지한다 — 반복을 확정본으로
                        # 굳히지 않는 것이 안전하다.
                        if len(sentences) >= 4 and _unique_ratio(sentences) < 0.5:
                            print(f"[revise] 반복 과다({t0:.0f}~{t1:.0f}s) → 빠른 패스 유지")
                            continue
                        sentences = [_mark_suspect(t) if _is_script_mismatch(t, stt_language)
                                     else t for t in sentences]
                    if not sentences:
                        continue  # 빈/환각/전체-에코 결과면 교체하지 않음(안전)
                    segs = [{"text": t} for t in sentences]
                    _allocate_timestamps(segs, t0, t1)
                    if PROMPT_MODE == "tail":
                        clean = [s["text"] for s in segs
                                 if not s["text"].startswith(SUSPECT_MARKER)]
                        revise_tail = " ".join(clean)[-120:]
                        prompt_tail = revise_tail  # 빠른 패스 문맥도 보정 텍스트로 갱신
                    new_segs = [{
                        "start": s["start"], "end": s["end"],
                        "text": s["text"], "text_original": s["text"],
                        "speaker": "",
                    } for s in segs]
                    self._apply_revision(t0, t1, new_segs)
                    if self.session_id:
                        db.replace_segments_range(self.session_id, t0, t1, new_segs)
                    await self.ws.send_json({
                        "type": "revise", "fromTime": t0, "toTime": t1,
                        "segments": [{"text": s["text"], "translatedText": "",
                                      "speaker": "", "start": s["start"], "end": s["end"]}
                                     for s in new_segs],
                    })
                    # 보정 문장 기준 번역(윈도당 chat 1회) → 번역 포함 revise 재전송
                    if translate_enabled:
                        try:
                            kos = await asyncio.to_thread(
                                self._translate_batch,
                                [s["text"] for s in new_segs],
                                openai_client, translate_model, topic)
                            for s, ko in zip(new_segs, kos):
                                s["translated_text"] = ko
                            if self.session_id:
                                db.replace_segments_range(self.session_id, t0, t1, new_segs)
                            await self.ws.send_json({
                                "type": "revise", "fromTime": t0, "toTime": t1,
                                "segments": [{"text": s["text"],
                                              "translatedText": s.get("translated_text", ""),
                                              "speaker": "", "start": s["start"], "end": s["end"]}
                                             for s in new_segs],
                            })
                        except Exception as e:
                            print(f"[revise-translate] 실패(영문 유지): {e}")
                    # 메모리 관리: diarize 후처리가 전체 PCM 을 쓰지 않는 한, 보정이
                    # 끝난 구간은 폐기(유지량 ≈ 윈도 2개, 수 MB).
                    if not self._diarize_pp:
                        drop = max(0, int((t1 - self._pcm_base_sec) * BYTES_PER_SEC)) & ~1
                        del self._pcm[:drop]
                        self._pcm_base_sec = t1
                except Exception:
                    traceback.print_exc()  # 워커는 어떤 예외에도 죽지 않는다
                finally:
                    revise_queue.task_done()

        consumer_task = asyncio.create_task(_consumer())
        revise_task = asyncio.create_task(_revise_worker()) if self._two_pass else None
        cancelled = False  # 사용자가 '저장 안 하고 취소'를 눌렀는지

        try:
            while not self._stop:
                try:
                    data = await asyncio.wait_for(self.ws.receive(), timeout=1.0)
                except asyncio.TimeoutError:
                    # 수신이 잠시 끊긴(일시정지 등) 동안 남은 버퍼가 있으면 flush
                    if audio_buffer and (len(audio_buffer) / BYTES_PER_SEC) >= MIN_CHUNK_SEC:
                        await _flush_chunk()
                        _maybe_queue_revision()
                    continue
                except WebSocketDisconnect:
                    break

                _b = None
                if "bytes" in data and data["bytes"]:
                    _b = data["bytes"]
                elif "text" in data and data["text"]:
                    msg = json.loads(data["text"])
                    if msg.get("type") == "stop":
                        break
                    elif msg.get("type") == "cancel":
                        cancelled = True
                        break
                    elif msg.get("type") == "audio":
                        _b = base64.b64decode(msg["data"])

                if _b:
                    audio_buffer.extend(_b)
                    if self._diarize_pp or self._two_pass:
                        self._pcm.extend(_b)
                    # 무음 지속시간 추적(발화 경계 감지) + 이 버퍼에 발화가 있었는지 기록
                    fsec = (len(_b) // 2) / SR
                    if _pcm_rms(_b) >= SILENCE_RMS:
                        silence_sec = 0.0
                        chunk_has_speech = True
                    else:
                        silence_sec += fsec

                    buf_sec = len(audio_buffer) / BYTES_PER_SEC
                    # 발화 경계(무음)에서 자르거나, 무음이 없어도 최대 길이에서 강제 분할
                    if buf_sec >= MAX_CHUNK_SEC or (
                        buf_sec >= MIN_CHUNK_SEC and silence_sec >= SILENCE_HOLD_SEC
                    ):
                        await _flush_chunk()
                        _maybe_queue_revision()
        except WebSocketDisconnect:
            pass

        if cancelled:
            # 취소: 회의록을 만들지 않고 세션·진행물을 버린다(새 녹음 즉시 시작용).
            _cancel_targets = [consumer_task, revise_task,
                               *list(fast_tr_tasks), *list(stt_workers)]
            for t in _cancel_targets:
                if t is not None:
                    t.cancel()
            await asyncio.gather(
                *[t for t in _cancel_targets if t is not None],
                return_exceptions=True,
            )
            # HTTP 경로 전용 종료 — 스레드풀을 여기서도 정리(과거 WS 경로만 정리해 누수)
            self._translator_pool.shutdown(wait=False, cancel_futures=True)
            self._web_pool.shutdown(wait=False, cancel_futures=True)
            if self.session_id:
                try:
                    db.delete_session(self.session_id)
                except Exception:
                    db.update_session_status(self.session_id, "error",
                                             error_detail="사용자 취소")
                self.session_id = None
            if self._searcher is not None:
                try:
                    self._searcher.shutdown(wait=False)
                except Exception:
                    pass
            if self._facilitator is not None:
                try:
                    self._facilitator.shutdown(wait=False)
                except Exception:
                    pass
            try:
                await self.ws.send_json({
                    "type": "cancelled",
                    "message": "녹음을 저장하지 않고 종료했습니다.",
                })
            except Exception:
                pass
            print("[realtime] 세션 취소 — 저장 없이 종료")
            return

        # 종료: 남은 버퍼를 반드시 마지막 청크로 flush한 뒤 소비자를 드레인한다
        # (과거엔 5초 미만 잔여분이 버려져 발화 끝이 누락됐다).
        await _flush_chunk()
        await stt_queue.put(None)
        try:
            await consumer_task
        except Exception:
            traceback.print_exc()

        # 빠른 패스 번역 잔여 태스크 드레인 — 종료 직전 세그먼트의 번역 유실 방지
        if fast_tr_tasks:
            await asyncio.gather(*list(fast_tr_tasks), return_exceptions=True)

        # 보정 패스 드레인: 아직 보정되지 않은 꼬리 구간(>0.5s)을 마지막 윈도로 발행하고
        # 워커가 남은 윈도·번역까지 끝내길 기다린다 — 회의록은 보정본 기준이어야 하므로.
        if revise_task is not None:
            tail_pending = (audio_pos_sec - revise_pos_sec) > 0.5
            if tail_pending or not revise_queue.empty():
                try:
                    await self.ws.send_json({"type": "status", "message": "전사 보정 중..."})
                except Exception:
                    pass
            if tail_pending:
                await revise_queue.put((revise_pos_sec, audio_pos_sec))
            await revise_queue.put(None)
            try:
                await revise_task
            except Exception:
                traceback.print_exc()

        # 환각 방어 집계 — 조용히 동작하면 원인 추적이 어려우므로 세션당 1줄 남긴다.
        if silent_chunks or dropped_dup or marked_suspect:
            print(f"[http-stt] 환각 방어: 무음 청크 {silent_chunks}개({silent_secs:.0f}초) 건너뜀, "
                  f"중복 조각 {dropped_dup}개 제외, 환각 의심 {marked_suspect}개 표시 "
                  f"(lang={stt_language}, prompt={PROMPT_MODE})")

        # vault 검색 drain — _finalize()의 collected_notes() 완결성 보장 (WS 경로와 동일)
        if self._searcher is not None:
            self._searcher.shutdown(wait=True)
        # 페르소나 트리아지 drain — 관찰 로그(facilitation_log) 기록 완결성 보장
        if self._facilitator is not None:
            self._facilitator.shutdown(wait=True)

        # 스레드풀 정리 — 과거엔 WS 경로만 shutdown 해 HTTP 세션마다 유휴 스레드가 누적됐다
        self._translator_pool.shutdown(wait=True, cancel_futures=False)
        self._web_pool.shutdown(wait=True, cancel_futures=False)

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
            # 단, 반드시 종료 이벤트를 먼저 보낸다 — 안 그러면 프런트가 completed만
            # 기다리며 영구 대기('문서 생성 중')한다(이동 안 됨 버그의 한 원인).
            # 원인이 셋이다: 발화가 없었거나, 음성 인식 호출이 실패했거나(예외),
            # 호출은 됐는데 빈 결과만 돌아왔거나. 뒤의 둘을 "음성이 감지되지 않았다"로
            # 안내하면 사용자가 마이크만 붙들게 된다.
            if self._stt_failed_chunks:
                _empty_msg = (
                    f"음성 인식 호출이 {self._stt_failed_chunks}회 실패해 저장할 내용이 "
                    f"없습니다 (마이크 문제 아님) — API 키·네트워크를 확인하세요."
                )
                _empty_reason = "stt_failed"
            elif self._stt_empty_chunks:
                # 호출은 성공했는데 내용이 비어서 돌아온 경우. 마이크 음량이 매우 낮은
                # 것일 수도, 제공자가 응답만 하고 내용을 주지 않은 것일 수도 있어
                # 한쪽으로 단정하지 않는다(단정하면 나머지 절반에게 틀린 지시가 된다).
                _empty_msg = (
                    f"소리가 있던 구간 {self._stt_empty_chunks}개가 모두 빈 인식 결과로 "
                    f"돌아와 저장할 내용이 없습니다 — 마이크 음량이 매우 낮거나, 음성 "
                    f"인식이 내용을 돌려주지 못한 경우입니다. 마이크 음량과 API 키·"
                    f"네트워크를 함께 확인해 주세요."
                )
                _empty_reason = "stt_failed"   # 프런트는 조치가 필요한 안내로 취급
            else:
                _empty_msg = "음성이 감지되지 않아 저장할 내용이 없습니다."
                _empty_reason = "no_speech"
            try:
                await self.ws.send_json({
                    "type": "empty",
                    "message": _empty_msg,
                    "reason": _empty_reason,
                })
            except Exception:
                pass  # 소켓이 이미 죽었어도 아래 정리는 진행
            if self.session_id:
                try:
                    db.delete_session(self.session_id)
                except Exception:
                    db.update_session_status(self.session_id, "completed")
                self.session_id = None
            return
        # _finalize 전체에서 snapshot 사용 (스레드 안전)
        self.segments = segments_snapshot

        # 트리비얼 가드: 발화가 사실상 없는 세션(세그먼트 2개 미만 '또는' 전사
        # 15자 미만)은 회의록/요약 LLM 생성을 건너뛴다 — "안녕하세요" 한 마디까지
        # 완결된 회의록으로 저장돼 대시보드를 어지럽히던 문제. 전사·세션은 그대로
        # 보존하고(completed) 상세에서 전사만 볼 수 있게 한다.
        _total_chars = sum(len((s.get("text") or "").strip()) for s in segments_snapshot)
        if len(segments_snapshot) < 2 or _total_chars < 15:
            duration = (segments_snapshot[-1]["end"] - segments_snapshot[0]["start"]
                        if segments_snapshot else 0)
            # output_dir 미설정 — run_post_session을 건너뛰어 web_realtime_{id}/ 산출물
            # 폴더 자체를 만들지 않으므로 startup 스캐너가 오해할 대상도 없다.
            db.update_session_status(self.session_id, "completed", duration_sec=duration)
            print(f"[finalize] 내용 짧음(seg={len(segments_snapshot)}, chars={_total_chars})"
                  f" → 회의록 생성 생략, 전사만 저장")
            try:
                await self.ws.send_json({
                    "type": "completed",
                    "sessionId": self.session_id,
                    "segmentCount": len(segments_snapshot),
                    "duration": duration,
                    "minutesSkipped": True,
                    "message": "내용이 짧아 회의록 없이 전사만 저장했습니다.",
                })
            except Exception:
                pass
            return

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

        # 클라이언트가 '백그라운드로 두고 새 녹음'으로 이미 떠났어도(소켓 닫힘)
        # 생성은 계속돼야 한다 — 전송 실패로 finalize 가 중단되지 않게 감싼다.
        try:
            await self.ws.send_json({"type": "generating", "message": "회의록 생성 중..."})
        except Exception:
            pass

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

            # 번역 검수 패스 — 번역이 켜진 세션의 화면·DB 번역(translated_text)을 원문과
            # 병치해 주제 맥락으로 오역·누락을 교정한다. 실시간 번역은 발화/윈도 단위라
            # 문맥 없이 처리돼 오역이 남기 쉬운데, 종료 시 전체 맥락으로 한 번 더 다듬는다.
            # (회의록 본문은 원문 세그먼트에서 생성되므로 영향 없음 — 표시 품질 개선용)
            if translate and mm._c("stt.translation_review", True):
                try:
                    from meeting_minutes_app.meeting_pipeline.stt import review_translations
                    _tr_segs = [s for s in self.segments if (s.get("translated_text") or "").strip()]
                    if _tr_segs:
                        self._send_to_browser({"type": "status", "message": "번역 검수 중..."})
                        _pairs = [((s.get("text") or ""), (s.get("translated_text") or ""))
                                  for s in _tr_segs]
                        _fixed = await asyncio.to_thread(
                            review_translations, _pairs, llm, topic)
                        for s, ko in zip(_tr_segs, _fixed):
                            if ko and ko.strip():
                                s["translated_text"] = ko.strip()
                                if self.session_id:
                                    try:
                                        db.update_segment_translation(
                                            self.session_id, s["start"], ko.strip())
                                    except Exception:
                                        pass
                except Exception as _re:
                    print(f"[realtime] 번역 검수 실패(기존 번역 유지): {_re}")

            # 실시간 수집분 — vault 관련 노트 + 웹 검색 보완
            with self._notes_lock:
                _web_findings = list(self._web_findings)
            _rt_titles = (self._searcher.collected_titles()[:10]
                          if self._searcher else [])
            # 근거(점수·섹션경로·snippet·발화·경과시각)까지 SQLite 사이드카에 누적 —
            # 회의 상세의 '참조된 관련 노트'·교차 회의 집계에서 다시 열람한다(FR-4/5).
            _rt_evidence = (self._searcher.collected_evidence(limit=30)
                            if self._searcher else [])
            if _rt_evidence and self.session_id:
                try:
                    db.add_related_notes(self.session_id, _rt_evidence)
                except Exception as _re:
                    print(f"[realtime] 관련 노트 누적 저장 실패(무시): {_re}")
            extra_blocks = []
            if _web_findings:
                extra_blocks.append("[웹 검색 보완]:\n" + "\n".join(
                    f"- {f['result'][:200]}" for f in _web_findings[:3]))

            # 산출물 폴더: output/web_realtime_{session_id}
            # (상대경로를 CWD가 아닌 데이터 베이스 기준으로 해석하는 공용 로직 사용 —
            # 다른 엔트리포인트로 실행돼 CWD가 다르면 산출물이 엉뚱한 곳에 생겼다)
            from meeting_minutes_app.common.app_paths import get_output_dir as _god
            session_out = _god() / f"web_realtime_{self.session_id}"

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
                language=_resolve_stt_language(language, mm._c),
                **self.stt_usage(),
            )
            options = fz.FinalizeOptions(
                llm=llm,
                do_graph_sync=True,
                notify=("email" if mm._c("realtime.email_on_finish", False) else None),
                artifacts_dir=session_out,
                extra_related_titles=_rt_titles,
                extra_related_evidence=_rt_evidence,
                extra_memo_blocks=extra_blocks,
            )

            # LLM/발행 작업은 워커 스레드로 — 이벤트 루프를 막지 않아
            # status 이벤트가 생성 중에도 스트리밍된다
            await asyncio.to_thread(fz.run_post_session, inputs, options, _WebEvents())

            duration = self.segments[-1]["end"] - self.segments[0]["start"] if self.segments else 0
            # 예상 비용 기록 — 월 지출 한도(cost.monthly_cap_usd) 합계에 실시간 세션도
            # 포함되도록. 번역 비용은 분당 단가가 미미해 생략(대략값).
            try:
                from meeting_minutes_app.common import pricing, config_loader as _cfg
                _m = pricing.current_models(_cfg)
                _est = pricing.estimate_session_cost(
                    duration, _m["stt_model"], include_minutes=True,
                    llm=_m["llm"], minutes_model=_m["minutes_model"],
                    # 실시간 경로는 2단계 보정 전사를 거쳐 STT 과금이 두 번 난다.
                    # 이 두 인자가 없어서 월 합계·지출 한도가 실제의 1/3로 계산됐다.
                    two_pass=_m["two_pass"], revise_model=_m["revise_model"],
                    # facilitation= 는 여기서 절대 켜지 않는다 — 트리아지는 이미
                    # spend_guard.record() 로 usage_log 에 들어가 있고, 이 값은
                    # sessions.cost_estimate 에 저장된다. month_to_date_spend() 가
                    # 둘을 더하므로 켜면 이중 집계된다(pricing 독스트링 참조).
                    # 세션별 실제 발생액은 usage_log.session_spend() 로 조회한다.
                )["total"]
            except Exception:
                _est = 0.0
            # output_dir 기록 필수 — 없으면 startup 폴더 스캐너가 web_realtime_{id}/ 를
            # '미등록 CLI 산출물'로 보고 재시작마다 중복 세션을 만들어냈다.
            db.update_session_status(
                self.session_id, "completed",
                duration_sec=duration,
                cost_estimate=round(_est, 4),
                output_dir=str(session_out),
            )

            # 소켓이 이미 닫혔어도 세션은 방금 completed 로 저장 완료 — 전송 실패가
            # except 로 흘러 성공한 세션을 error 로 뒤집지 않게 별도로 감싼다.
            try:
                await self.ws.send_json({
                    "type": "completed",
                    "sessionId": self.session_id,
                    "segmentCount": len(self.segments),
                    "duration": duration,
                })
            except Exception:
                pass

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
    # CORS 는 WebSocket 에 적용되지 않는다. 그래서 예전에는 사용자가 앱을 켜 둔 채 아무
    # 웹페이지를 열기만 해도 그 페이지가 이 소켓을 열어 실시간 전사(=사용자 키로 과금)를
    # 시작시킬 수 있었다. loopback 바인딩은 이걸 막지 못한다 — 브라우저가 사용자 PC 안에서
    # 연결하기 때문이다. accept() **전에** Origin 을 보고 거절한다(SEC-009 / N-8).
    from web.backend.security import ws_reject_foreign_origin
    if await ws_reject_foreign_origin(ws):
        return
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
