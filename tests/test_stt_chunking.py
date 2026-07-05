"""
STT 청크 분할/잘림 방지 회귀 테스트 — 실제 OpenAI API 호출 없이 실행 가능.

실행:
    python -m pytest tests/test_stt_chunking.py -q
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meeting_minutes_app.meeting_pipeline import stt as mm  # noqa: E402


class _FakeResponse:
    def __init__(self, data):
        self._data = data

    def model_dump(self):
        return self._data


def _fake_client(captured_params, return_data):
    client = MagicMock()

    def _create(**params):
        captured_params.append(params)
        return _FakeResponse(return_data)

    client.audio.transcriptions.create.side_effect = _create
    return client


@pytest.fixture
def dummy_audio(tmp_path):
    p = tmp_path / "dummy.mp3"
    p.write_bytes(b"\x00")
    return str(p)


# ━━━━━━━━━━━━━━━━━━━━ transcribe_chunk params ━━━━━━━━━━━━━━━━━━━━

class TestTranscribeChunkParams:
    def test_diarize_sets_chunking_strategy(self, dummy_audio):
        captured = []
        client = _fake_client(captured, {"text": "hello"})
        mm.transcribe_chunk(client, dummy_audio, "gpt-4o-transcribe-diarize")
        params = captured[0]
        assert params["response_format"] == "diarized_json"
        assert params["chunking_strategy"] == "auto"

    def test_fallback_json_sets_chunking_strategy(self, dummy_audio):
        # gpt-4o-transcribe (diarize 실패/다중 청크 시 fallback 모델) — 회귀 대상 버그
        captured = []
        client = _fake_client(captured, {"text": "hello"})
        mm.transcribe_chunk(client, dummy_audio, "gpt-4o-transcribe")
        params = captured[0]
        assert params["response_format"] == "json"
        assert params["chunking_strategy"] == "auto"

    def test_whisper_has_no_chunking_strategy(self, dummy_audio):
        captured = []
        client = _fake_client(captured, {"text": "hello", "segments": []})
        mm.transcribe_chunk(client, dummy_audio, "whisper-1")
        params = captured[0]
        assert params["response_format"] == "verbose_json"
        assert "chunking_strategy" not in params


# ━━━━━━━━━━━━━━━━━━━━ _looks_truncated ━━━━━━━━━━━━━━━━━━━━

class TestLooksTruncated:
    def test_short_text_is_truncated(self):
        segs = [{"text": "a" * 10, "start": 0, "end": 0}]
        assert mm._looks_truncated(segs, duration=800, has_timestamps=False) is True

    def test_sufficient_text_not_truncated(self):
        segs = [{"text": "a" * 4000, "start": 0, "end": 795}]
        assert mm._looks_truncated(segs, duration=800, has_timestamps=False) is False

    def test_timestamp_coverage_gap_flagged(self):
        # 글자수는 충분하지만 마지막 세그먼트 end가 청크 길이의 70% 미만 → 잘림 의심
        segs = [{"text": "a" * 4000, "start": 0, "end": 300}]
        assert mm._looks_truncated(segs, duration=800, has_timestamps=True) is True

    def test_zero_duration_never_truncated(self):
        assert mm._looks_truncated([], duration=0, has_timestamps=False) is False
