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


# ━━━━━━━━━━━━━━━━━━━━ 분할 재시도 × 무음 판정 ━━━━━━━━━━━━━━━━━━━━

class TestSplitRetrySkipsSilence:
    """빈 전사는 _looks_truncated 에 항상 걸린다(글자수 0). 그래서 무음 구간이
    **제공자당** STT 3회 + ffmpeg 추출 2회를 치른 뒤에야 체인의 무음 판정에 도달했다
    (4단 체인이면 최대 12회). 무음이면 분할을 생략한다.

    단 비무음 짧은/빈 결과의 분할 재시도는 **유지**돼야 한다 — 큰 청크에서 벤더가
    조용히 실패하고 절반씩은 성공하는 실제 복구 경로다."""

    def _patch(self, monkeypatch, texts, silent):
        calls: list = []
        extracted: list = []

        def _transcribe(client, path, model, *a, **kw):
            calls.append(path)
            txt = texts.pop(0) if texts else ""
            return [{"start": 0, "end": 0, "text": txt, "speaker": ""}]

        def _extract(src, offset, dur, dst):
            extracted.append(dst)

        monkeypatch.setattr(mm, "transcribe_chunk", _transcribe)
        monkeypatch.setattr(mm, "audio_duration", lambda _p: 800.0)
        monkeypatch.setattr(mm, "_chunk_is_silent", lambda *a, **kw: silent)
        monkeypatch.setattr(mm, "_extract_audio_segment", _extract)
        return calls, extracted

    def _run(self, dummy_audio, tmp_path):
        return mm._transcribe_chunk_checked(
            MagicMock(), dummy_audio, "gpt-4o-transcribe",
            None, None, 0.0, None, 0, str(tmp_path))

    def test_silent_chunk_is_not_split(self, monkeypatch, dummy_audio, tmp_path):
        calls, extracted = self._patch(monkeypatch, [""], silent=True)
        out = self._run(dummy_audio, tmp_path)
        assert len(calls) == 1      # 한 번만 — 분할 재시도 없음
        assert extracted == []      # ffmpeg 추출도 없음
        assert out and out[0]["text"] == ""

    def test_empty_but_not_silent_is_still_split(self, monkeypatch, dummy_audio, tmp_path):
        # 발화가 감지됐는데 빈 전사 = 제공자가 조용히 실패한 경우 → 분할로 복구 시도
        calls, extracted = self._patch(monkeypatch, ["", "", ""], silent=False)
        self._run(dummy_audio, tmp_path)
        assert len(calls) == 3      # 원본 1 + 절반 2
        assert len(extracted) == 2

    def test_non_silent_short_text_is_still_split(self, monkeypatch, dummy_audio, tmp_path):
        # 텍스트가 있으면 무음 판정을 볼 필요조차 없다(기존 잘림 복구 경로 그대로)
        calls, extracted = self._patch(
            monkeypatch, ["짧은 텍스트", "앞 절반", "뒤 절반"], silent=False)
        out = self._run(dummy_audio, tmp_path)
        assert len(calls) == 3
        assert len(extracted) == 2
        assert [s["text"] for s in out] == ["앞 절반", "뒤 절반"]
