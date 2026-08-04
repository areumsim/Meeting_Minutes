"""
실시간(웹) HTTP 청크 전사 회귀 테스트 — 실제 OpenAI/네트워크 없이 실행.

방지하려는 재발 버그:
  1) HTTP 청크 폴백이 diarize 모델 + response_format="text"로 호출해 매 청크가 조용히
     실패 → 화면에 아무것도 안 뜸. (모델 정규화 + model-aware transcribe_chunk로 해결)
  2) 세그먼트 0개일 때 _finalize가 종료 이벤트를 안 보내 프런트가 영구 대기 → 이동 안 됨.
     (빈 세그먼트에서도 "empty" 이벤트를 반드시 전송)

실행:
    python -m pytest tests/test_realtime_http_stt.py -q
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meeting_minutes_app.common.realtime_ws_session import normalize_ws_model  # noqa: E402
from meeting_minutes_app.meeting_pipeline import stt  # noqa: E402


class _FakeResponse:
    def __init__(self, data):
        self._data = data

    def model_dump(self):
        return self._data


# ━━━━━━━━ 버그 1: 실시간 STT 모델/포맷 정합성 ━━━━━━━━

class TestRealtimeSttModelNormalization:
    def test_diarize_model_is_not_used_for_realtime(self):
        """실시간 경로는 diarize 모델을 그대로 쓰지 않는다(평문 모델로 정규화)."""
        model, reason = normalize_ws_model("gpt-4o-transcribe-diarize")
        assert "diarize" not in model
        assert reason  # 전환 사유가 남아야 함

    def test_normalized_model_never_requests_diarized_format(self, tmp_path):
        """정규화된 모델을 transcribe_chunk에 넣으면 diarize 전용 포맷을 요청하지 않는다.

        (모델이 거부하는 response_format으로 호출해 조용히 실패하던 버그의 회귀 방지.)
        """
        model, _ = normalize_ws_model("gpt-4o-transcribe-diarize")
        wav = tmp_path / "chunk.wav"
        wav.write_bytes(b"\x00")

        captured = []
        client = MagicMock()

        def _create(**params):
            captured.append(params)
            return _FakeResponse({"text": "hello world"})

        client.audio.transcriptions.create.side_effect = _create
        stt.transcribe_chunk(client, str(wav), model)

        assert captured, "STT 호출이 일어나야 함"
        assert captured[0]["response_format"] != "diarized_json"


# ━━━━━━━━ 버그 2: finalize가 항상 종료 이벤트 전송 ━━━━━━━━

class TestFinalizeAlwaysEmitsTerminalEvent:
    def test_empty_segments_sends_empty_event(self):
        """세그먼트 0개여도 종료 이벤트를 보내 프런트가 멈추지 않게 한다."""
        # realtime API 모듈은 fastapi/DB 의존 — 없으면 스킵(핵심은 위 정규화 테스트).
        realtime = pytest.importorskip("web.backend.api.realtime")

        ws = MagicMock()
        ws.send_json = AsyncMock()

        session = realtime.BrowserRealtimeSession(ws, {})
        session.segments = []
        session.session_id = None  # DB 접근 없이 순수 이벤트 경로만 검증

        asyncio.run(session._finalize(None, "en", True, "minutes", "", ""))

        sent_types = [
            call.args[0].get("type")
            for call in ws.send_json.call_args_list
            if call.args and isinstance(call.args[0], dict)
        ]
        assert "empty" in sent_types


# ━━━━━━━━ 버그 3: 폴백 감사 기록이 사실과 달랐다 ━━━━━━━━

class TestSttFallbackAudit:
    """`stt_fallback_used` 는 회의 상세의 벤더 전환 고지와 노트 frontmatter 의
    감사 기록에 쓰인다 — 틀리면 "무엇으로 전사됐는지"의 기록이 사실과 달라진다.

    회귀: 판정 기준점을 `config["stt_model"]`(**녹음별 임시 오버라이드**)로 잡아서,
    사용자가 모델을 바꾸지 않은 평상시에는 기준점이 없어 판정이 "다른 벤더가
    쓰였는가"로 줄었다. 그래서 OpenAI 내부 폴백(기본 모델 → stt_fallback 모델)이
    일어난 회의가 '폴백 없음'으로 기록됐다.
    """

    def _session(self, config=None):
        realtime = pytest.importorskip("web.backend.api.realtime")
        return realtime.BrowserRealtimeSession(MagicMock(), config or {})

    def test_openai_internal_fallback_is_recorded_without_override(self):
        """녹음별 오버라이드가 없어도 OpenAI 내부 폴백을 잡아낸다(평상시 경로)."""
        s = self._session({})                      # stt_model 오버라이드 없음
        s._note_stt_primary("OpenAI", "gpt-4o-mini-transcribe")
        s._note_stt_model("OpenAI", "gpt-4o-mini-transcribe")
        s._note_stt_model("OpenAI", "gpt-4o-transcribe")   # 폴백 모델로 넘어감
        usage = s.stt_usage()
        assert usage["stt_fallback_used"] is True
        assert usage["stt_models"] == ["gpt-4o-mini-transcribe", "gpt-4o-transcribe"]

    def test_no_fallback_when_only_primary_used(self):
        s = self._session({})
        s._note_stt_primary("OpenAI", "gpt-4o-mini-transcribe")
        s._note_stt_model("OpenAI", "gpt-4o-mini-transcribe")
        assert s.stt_usage()["stt_fallback_used"] is False

    def test_other_vendor_is_always_a_fallback(self):
        """Groq 는 기준점을 몰라도 폴백이다 — 회의 음성이 다른 회사로 나갔다."""
        s = self._session({})
        s._note_stt_model("Groq", "whisper-large-v3-turbo")
        assert s.stt_usage()["stt_fallback_used"] is True

    def test_primary_keeps_the_first_attempt(self):
        """WS 로 먼저 시도한 뒤 HTTP 로 폴백해도 기준점은 첫 시도다."""
        s = self._session({})
        s._note_stt_primary("OpenAI", "gpt-4o-transcribe")     # WS 경로
        s._note_stt_primary("OpenAI", "gpt-4o-mini-transcribe")  # HTTP 폴백 — 무시
        assert s._stt_primary == ("OpenAI", "gpt-4o-transcribe")

    def test_no_transcription_means_no_fallback(self):
        """전사가 하나도 없었으면 폴백도 없다(빈 세션이 고지를 띄우면 안 된다)."""
        s = self._session({})
        s._note_stt_primary("OpenAI", "gpt-4o-mini-transcribe")
        assert s.stt_usage()["stt_fallback_used"] is False
