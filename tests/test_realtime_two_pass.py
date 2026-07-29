"""실시간 2-pass 보정(빠른 표시 + 문장 교체) 회귀 테스트 — 네트워크 없이 실행.

방지하려는 재발 버그:
  1) 고정/무음 청크 분할로 문장이 조각나고("Things like / gathering."), 조각 번역으로
     해석이 이상해짐 → 보정 패스(revise)가 윈도 단위 재전사로 문장 교체.
  2) 번역 동기 호출이 이벤트 루프를 블로킹해 수신·STT까지 정지 → 영어 즉시 전송 후
     비동기 번역(translation 이벤트).
  3) 청크 STT 예외 시 텍스트 조용히 소실 → 폴백 모델 1회 재시도.

실행:
    python -m pytest tests/test_realtime_two_pass.py -q
"""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meeting_minutes_app.meeting_pipeline import stt  # noqa: E402
from web.backend.api import realtime as rt  # noqa: E402


class _FakeResponse:
    def __init__(self, data):
        self._data = data

    def model_dump(self):
        return self._data


# ━━━━━━━━ 단위: transcribe_chunk prompt 전달 ━━━━━━━━

class TestTranscribeChunkPrompt:
    def _client(self, text="hello world"):
        client = MagicMock()
        client.audio.transcriptions.create.return_value = _FakeResponse({"text": text})
        return client

    def test_prompt_passed_for_plain_model(self, tmp_path):
        wav = tmp_path / "c.wav"
        wav.write_bytes(b"\x00" * 64)
        client = self._client()
        stt.transcribe_chunk(client, str(wav), "gpt-4o-mini-transcribe", prompt="이전 문맥")
        params = client.audio.transcriptions.create.call_args.kwargs
        assert params["prompt"] == "이전 문맥"

    def test_prompt_omitted_when_none(self, tmp_path):
        wav = tmp_path / "c.wav"
        wav.write_bytes(b"\x00" * 64)
        client = self._client()
        stt.transcribe_chunk(client, str(wav), "gpt-4o-mini-transcribe")
        assert "prompt" not in client.audio.transcriptions.create.call_args.kwargs

    def test_prompt_omitted_for_diarize(self, tmp_path):
        wav = tmp_path / "c.wav"
        wav.write_bytes(b"\x00" * 64)
        client = self._client()
        client.audio.transcriptions.create.return_value = _FakeResponse({"segments": []})
        stt.transcribe_chunk(client, str(wav), "gpt-4o-transcribe-diarize", prompt="ctx")
        assert "prompt" not in client.audio.transcriptions.create.call_args.kwargs

    def test_prompt_truncated_for_gpt_models(self, tmp_path):
        wav = tmp_path / "c.wav"
        wav.write_bytes(b"\x00" * 64)
        client = self._client()
        stt.transcribe_chunk(client, str(wav), "gpt-4o-transcribe", prompt="x" * 2000)
        assert (len(client.audio.transcriptions.create.call_args.kwargs["prompt"])
                == stt.GPT_PROMPT_MAX_CHARS)

    def test_prompt_truncated_harder_for_whisper(self, tmp_path):
        """whisper 계열 prompt 는 224토큰 상한이라 gpt-4o 계열과 같은 800자를 넣으면
        폴백 시도 자체가 깨질 수 있다 — 훨씬 짧게 자른다."""
        wav = tmp_path / "c.wav"
        wav.write_bytes(b"\x00" * 64)
        client = self._client()
        client.audio.transcriptions.create.return_value = _FakeResponse({"segments": []})
        stt.transcribe_chunk(client, str(wav), "whisper-1", prompt="x" * 2000)
        assert (len(client.audio.transcriptions.create.call_args.kwargs["prompt"])
                == stt.WHISPER_PROMPT_MAX_CHARS)
        assert stt.WHISPER_PROMPT_MAX_CHARS < stt.GPT_PROMPT_MAX_CHARS

    def test_prompt_omitted_for_non_openai_provider(self, tmp_path):
        """Groq(whisper)는 폴백 단계다 — 224토큰 상한 위험을 안고 문맥을 넣지 않는다."""
        wav = tmp_path / "c.wav"
        wav.write_bytes(b"\x00" * 64)
        client = self._client()
        client.audio.transcriptions.create.return_value = _FakeResponse({"segments": []})
        stt.transcribe_chunk(client, str(wav), "whisper-large-v3-turbo",
                             prompt="ctx", provider="Groq")
        assert "prompt" not in client.audio.transcriptions.create.call_args.kwargs


# ━━━━━━━━ 단위: prompt 에코 제거 ━━━━━━━━

class TestStripPromptEcho:
    def test_full_echo_returns_empty(self):
        assert rt._strip_prompt_echo("Hello world today.", "hello, world today") == ""

    def test_partial_echo_stripped(self):
        out = rt._strip_prompt_echo(
            "the foundational model. You can generate anything.",
            "like I said, the foundational model.")
        assert out == "You can generate anything."

    def test_no_overlap_unchanged(self):
        assert rt._strip_prompt_echo("Completely new sentence.", "previous context") == \
            "Completely new sentence."

    def test_short_coincidence_not_stripped(self):
        # 2토큰 우연 일치는 자르지 않는다(min_tokens=3)
        assert rt._strip_prompt_echo("the model works fine.", "we like the model") == \
            "the model works fine."

    def test_case_and_punct_insensitive(self):
        out = rt._strip_prompt_echo("WE'RE RENDERING, AT 60fps! and more here.",
                                    "we're rendering at 60fps")
        assert out == "and more here."

    def test_empty_inputs(self):
        assert rt._strip_prompt_echo("", "ctx") == ""
        assert rt._strip_prompt_echo("text stays here.", "") == "text stays here."


# ━━━━━━━━ 단위: 타임스탬프 배분 / 구간 교체 ━━━━━━━━

class TestAllocateTimestamps:
    def test_monotonic_and_bounds(self):
        segs = [{"text": "short."}, {"text": "a much longer sentence here."}, {"text": "mid one."}]
        rt._allocate_timestamps(segs, 10.0, 35.0)
        assert segs[0]["start"] == 10.0
        assert segs[-1]["end"] == 35.0
        for prev, cur in zip(segs, segs[1:]):
            assert prev["end"] <= cur["start"] + 1e-9
            assert prev["start"] < prev["end"]
        # 긴 문장이 더 넓은 구간을 차지
        assert (segs[1]["end"] - segs[1]["start"]) > (segs[0]["end"] - segs[0]["start"])

    def test_empty_and_degenerate(self):
        rt._allocate_timestamps([], 0, 10)  # 예외 없어야
        segs = [{"text": "a"}]
        rt._allocate_timestamps(segs, 5.0, 5.0)
        assert segs[0]["start"] == 5.0 and segs[0]["end"] == 5.0


class TestApplyRevision:
    def _session(self):
        ws = MagicMock()
        s = rt.BrowserRealtimeSession(ws, {})
        return s

    def test_replaces_only_range(self):
        s = self._session()
        s.segments = [
            {"start": 0.0, "end": 4.0, "text": "frag1"},
            {"start": 5.0, "end": 9.0, "text": "frag2"},
            {"start": 26.0, "end": 30.0, "text": "keep"},
        ]
        s._apply_revision(0.0, 25.0, [{"start": 0.0, "end": 25.0, "text": "full sentence."}])
        texts = [x["text"] for x in s.segments]
        assert texts == ["full sentence.", "keep"]
        assert s.segments[0]["start"] == 0.0 and s.segments[1]["start"] == 26.0


# ━━━━━━━━ 단위: DB 구간 교체 ━━━━━━━━

class TestReplaceSegmentsRange:
    def test_roundtrip(self, monkeypatch, tmp_path):
        from web.backend import database as db
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
        db.init_db()
        sid = db.create_session(title="x")
        db.add_segment(sid, "", "frag1", 0.0, 4.0)
        db.add_segment(sid, "", "frag2", 5.0, 9.0)
        db.add_segment(sid, "", "keep", 26.0, 30.0)
        db.replace_segments_range(sid, 0.0, 25.0, [
            {"start": 0.0, "end": 25.0, "text": "full sentence.", "translated_text": "온전한 문장."},
        ])
        rows = db.get_segments(sid)
        assert [r["text"] for r in rows] == ["full sentence.", "keep"]
        assert rows[0]["translated_text"] == "온전한 문장."
        # translated_text 미지정 시 원문이 아닌 빈 값이어야 함(add_segments_bulk 폴백 버그 방지)
        db.replace_segments_range(sid, 26.0, 31.0, [{"start": 26.0, "end": 30.0, "text": "korean?"}])
        rows = db.get_segments(sid)
        assert rows[-1]["translated_text"] == ""


# ━━━━━━━━ 통합: HTTP 폴백 2-pass 파이프라인 (가짜 WS/클라이언트) ━━━━━━━━

class _FakeCfg:
    def __init__(self, overrides=None):
        self.values = {
            "realtime.fast_max_chunk_sec": 5.0,
            "realtime.two_pass": True,
            "realtime.revise_window_sec": 25.0,
            "realtime.revise_model": "gpt-4o-transcribe",
            "models.stt": "gpt-4o-mini-transcribe",
        }
        self.values.update(overrides or {})

    def get(self, key, default=None):
        return self.values.get(key, default)


class _FakeWS:
    """수신 프레임을 순서대로 돌려주고 send_json 을 기록하는 가짜 WebSocket."""

    def __init__(self, frames):
        self._frames = list(frames)
        self.sent = []

    async def receive(self):
        if not self._frames:
            return {"text": json.dumps({"type": "stop"})}
        return self._frames.pop(0)

    async def send_json(self, obj):
        self.sent.append(obj)


def _loud_frame(seconds: float) -> bytes:
    # int16 진폭 4096(RMS >> 300) 프레임 — 무음으로 판정되지 않는다
    n = int(24000 * seconds)
    return b"\x00\x10" * n


def _silent_frame(seconds: float) -> bytes:
    # 완전 무음 프레임 — RMS 0 이라 발화로 판정되지 않는다
    return b"\x00\x00" * int(24000 * seconds)


def _run_session(frames, cfg, fast_text="quick frag.", revise_text=None,
                 fail_first_stt=False, translate=False, language="en",
                 create_side_effect=None, topic="", speakers=""):
    """_run_http_fallback 을 가짜 WS/OpenAI 로 끝까지 실행하고 (session, ws) 반환."""
    ws = _FakeWS(frames)
    session = rt.BrowserRealtimeSession(ws, {})
    session.session_id = None          # DB 기록 생략
    session._finalize_called = False

    async def _fake_finalize(*a, **k):
        session._finalize_called = True

    session._finalize = _fake_finalize

    client = MagicMock()
    calls = {"n": 0}

    def _create(**params):
        calls["n"] += 1
        if fail_first_stt and calls["n"] == 1:
            raise RuntimeError("boom")
        model = params.get("model", "")
        if revise_text is not None and model == "gpt-4o-transcribe":
            return _FakeResponse({"text": revise_text})
        return _FakeResponse({"text": fast_text})

    client.audio.transcriptions.create.side_effect = create_side_effect or _create
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content='["번역된 문장"]'))])

    asyncio.run(session._run_http_fallback(
        client, language, translate, "gpt-4o-mini",
        "meeting", topic, "테스트", speakers, cfg,
    ))
    return session, ws, client


class TestTwoPassPipeline:
    def test_fast_segments_are_provisional_and_revised(self):
        # 5.5초 프레임 6개 = 33초 → 25초 윈도 1개 + 꼬리 보정
        # 보정 응답은 윈도마다 다른 문장이어야 한다 — 같으면 에코 필터가
        # (정상 동작으로) 꼬리 보정을 전체-에코로 보고 건너뛴다.
        revise_texts = iter([
            "This is a fully corrected sentence with proper punctuation.",
            "Another corrected tail sentence arrives separately.",
        ])

        def _create(**params):
            if params.get("model") == "gpt-4o-transcribe":
                return _FakeResponse({"text": next(revise_texts)})
            return _FakeResponse({"text": "quick frag."})

        frames = [{"bytes": _loud_frame(5.5)} for _ in range(6)]
        session, ws, client = _run_session(
            frames, _FakeCfg(), create_side_effect=_create,
        )
        types = [m.get("type") for m in ws.sent]
        assert types[0] == "fallback_http"
        seg_events = [m for m in ws.sent if m.get("type") == "segment"]
        assert seg_events and all(m.get("provisional") for m in seg_events)
        revises = [m for m in ws.sent if m.get("type") == "revise"]
        assert revises, "보정(revise) 이벤트가 발행돼야 함"
        # 첫 윈도는 0부터 시작, 구간이 이어져 전체를 덮는다
        assert revises[0]["fromTime"] == 0.0
        assert max(r["toTime"] for r in revises) == pytest.approx(33.0, abs=0.2)
        # 메모리 세그먼트도 보정본으로 교체됨
        assert all("corrected" in s["text"] for s in session.segments)
        assert session._finalize_called

    def test_two_pass_off_keeps_legacy_behavior(self):
        frames = [{"bytes": _loud_frame(5.5)} for _ in range(2)]
        session, ws, _ = _run_session(
            frames, _FakeCfg({"realtime.two_pass": False}), fast_text="plain text.")
        assert not [m for m in ws.sent if m.get("type") == "revise"]
        seg_events = [m for m in ws.sent if m.get("type") == "segment"]
        assert seg_events and all(not m.get("provisional") for m in seg_events)

    def test_stt_failure_falls_back_once(self):
        frames = [{"bytes": _loud_frame(5.5)}]
        session, ws, client = _run_session(
            frames, _FakeCfg({"realtime.two_pass": False}),
            fast_text="recovered text after fallback.", fail_first_stt=True)
        # 첫 호출 실패 → 폴백 모델로 재시도되어 세그먼트가 소실되지 않는다
        models = [c.kwargs.get("model") for c in client.audio.transcriptions.create.call_args_list]
        assert models[0] == "gpt-4o-mini-transcribe"
        assert models[1] == stt.FALLBACK_STT_MODEL
        assert [m for m in ws.sent if m.get("type") == "segment"]

    def test_translation_is_async_and_updates(self):
        frames = [{"bytes": _loud_frame(5.5)}]
        session, ws, _ = _run_session(
            frames, _FakeCfg({"realtime.two_pass": False}),
            fast_text="english sentence to translate.", translate=True)
        seg_events = [m for m in ws.sent if m.get("type") == "segment"]
        # 영어가 번역을 기다리지 않고 먼저 전송된다
        assert seg_events[0]["translatedText"] == ""
        tr = [m for m in ws.sent if m.get("type") == "translation"]
        assert tr and tr[0]["translatedText"]

    def test_revise_includes_batch_translation(self):
        frames = [{"bytes": _loud_frame(5.5)} for _ in range(6)]
        session, ws, _ = _run_session(
            frames, _FakeCfg(), translate=True,
            fast_text="quick frag.",
            revise_text="One corrected sentence stands here nicely.")
        revises = [m for m in ws.sent if m.get("type") == "revise"]
        translated = [m for m in revises if any(s.get("translatedText") for s in m["segments"])]
        assert translated, "보정 후 번역이 포함된 revise 재전송이 있어야 함"

    def test_fast_pass_prompt_echo_stripped(self):
        """STT가 prompt(직전 문장)를 출력에 되풀이해도 화면에 중복되지 않는다.

        꼬리 문맥(prompt_context="tail")을 쓰는 구동작에서의 방어다 — 기본값
        static 에서는 꼬리를 아예 넘기지 않아 이 에코가 생기지 않는다.
        """
        def _create(**params):
            prompt = params.get("prompt") or ""
            if not prompt:
                return _FakeResponse({"text": "We are rendering at."})
            # 모델이 prompt를 그대로 앞에 붙여 반환하는 에코 상황 재현
            return _FakeResponse({"text": f"{prompt} Sixty FPS and beyond."})

        frames = [{"bytes": _loud_frame(5.5)} for _ in range(2)]
        session, ws, _ = _run_session(
            frames, _FakeCfg({"realtime.two_pass": False,
                              "realtime.prompt_context": "tail"}),
            create_side_effect=_create)
        texts = [m["text"] for m in ws.sent if m.get("type") == "segment"]
        assert texts[0] == "We are rendering at."
        assert texts[1] == "Sixty FPS and beyond."  # 에코 제거됨

    def test_cancel_discards_session_without_finalize(self):
        frames = [
            {"bytes": _loud_frame(5.5)},
            {"text": json.dumps({"type": "cancel"})},
        ]
        session, ws, _ = _run_session(frames, _FakeCfg(), fast_text="some words here.")
        assert not session._finalize_called, "취소 시 회의록 생성이 없어야 함"
        assert any(m.get("type") == "cancelled" for m in ws.sent)

    def test_pcm_trimmed_after_revision(self):
        frames = [{"bytes": _loud_frame(5.5)} for _ in range(6)]
        session, ws, _ = _run_session(
            frames, _FakeCfg(), fast_text="quick frag.",
            revise_text="Corrected sentence.")
        # 보정 완료 구간의 PCM 은 폐기되고 기준 시각이 전진한다
        assert session._pcm_base_sec > 0.0
        assert len(session._pcm) < 33 * 48000


# ━━━━━━━━ 통합: 환각 방어 (무음 스킵 / 언어 고정 / 반복 차단) ━━━━━━━━

class TestHallucinationDefense:
    """한국어 회의 전사에 외국어 조각·반복 문장이 섞이던 문제(2026-07-28)의 회귀 방어."""

    def test_silent_chunks_are_not_transcribed(self):
        # 무음만 30초 — STT 를 한 번도 호출하지 않아야 한다(무음 전사 = 환각의 원인)
        frames = [{"bytes": _silent_frame(5.5)} for _ in range(6)]
        session, ws, client = _run_session(frames, _FakeCfg(), fast_text="ghost text.")
        assert client.audio.transcriptions.create.call_count == 0
        assert not [m for m in ws.sent if m.get("type") == "segment"]
        assert not session.segments

    def test_silence_skip_can_be_disabled(self):
        frames = [{"bytes": _silent_frame(5.5)} for _ in range(2)]
        session, ws, client = _run_session(
            frames, _FakeCfg({"realtime.drop_silent_chunks": False,
                              "realtime.two_pass": False}),
            fast_text="ghost text.")
        assert client.audio.transcriptions.create.call_count > 0

    def test_speech_after_silence_still_transcribed(self):
        # 무음 → 발화 → 무음: 발화 구간은 정상 전사되고 타임스탬프가 어긋나지 않는다
        frames = [{"bytes": _silent_frame(5.5)},
                  {"bytes": _loud_frame(5.5)},
                  {"bytes": _silent_frame(5.5)}]
        session, ws, _ = _run_session(
            frames, _FakeCfg({"realtime.two_pass": False}),
            fast_text="실제 발화 내용입니다.")
        segs = [m for m in ws.sent if m.get("type") == "segment"]
        assert len(segs) == 1
        assert segs[0]["start"] >= 5.0   # 앞 무음 구간만큼 타임라인이 전진

    def test_language_auto_is_pinned_to_ko(self):
        frames = [{"bytes": _loud_frame(5.5)}]
        session, ws, client = _run_session(
            frames, _FakeCfg({"realtime.two_pass": False,
                              "realtime.language": "auto"}),
            language="auto", fast_text="한국어 발화 내용입니다.")
        langs = [c.kwargs.get("language")
                 for c in client.audio.transcriptions.create.call_args_list]
        assert langs and all(l == "ko" for l in langs), langs

    def test_explicit_language_is_forwarded(self):
        frames = [{"bytes": _loud_frame(5.5)}]
        session, ws, client = _run_session(
            frames, _FakeCfg({"realtime.two_pass": False}),
            language="en", fast_text="english sentence here.")
        langs = [c.kwargs.get("language")
                 for c in client.audio.transcriptions.create.call_args_list]
        assert all(l == "en" for l in langs)

    def test_static_prompt_has_no_transcript_tail(self):
        """기본(static) 모드는 전사 결과를 prompt 로 되먹이지 않는다 — 반복 루프 차단."""
        frames = [{"bytes": _loud_frame(5.5)} for _ in range(3)]
        prompts = []

        def _create(**params):
            prompts.append(params.get("prompt"))
            return _FakeResponse({"text": "고유한 문장 하나입니다."})

        _run_session(frames, _FakeCfg({"realtime.two_pass": False}),
                     language="ko", topic="위키 오픈 준비", speakers="김책임",
                     create_side_effect=_create)
        assert prompts, "STT 가 호출되어야 함"
        for p in prompts:
            assert "고유한 문장" not in (p or ""), f"전사 결과가 prompt 로 되먹여짐: {p}"
            assert "위키 오픈 준비" in (p or "")   # 주제·참석자 힌트는 전달

    def test_duplicate_fast_fragments_not_emitted(self):
        # 같은 조각이 청크마다 반복되면(모델 반복 루프) 화면·DB에 한 번만 남는다
        frames = [{"bytes": _loud_frame(5.5)} for _ in range(4)]
        session, ws, _ = _run_session(
            frames, _FakeCfg({"realtime.two_pass": False}),
            language="ko", fast_text="뭐가 있냐 뭐가 있냐 뭐가 있냐.")
        segs = [m for m in ws.sent if m.get("type") == "segment"]
        assert len(segs) == 1
        # 조각 안의 되풀이도 축약된다
        assert segs[0]["text"] == "뭐가 있냐."

    def test_repetitive_revision_is_rejected(self):
        """보정 결과가 같은 문장의 되풀이면 교체하지 않고 빠른 패스를 유지한다."""
        loop = " ".join(["같은 문장이 계속 반복됩니다."] * 6)

        def _create(**params):
            if params.get("model") == "gpt-4o-transcribe":
                return _FakeResponse({"text": loop})
            return _FakeResponse({"text": "빠른 패스 조각입니다."})

        frames = [{"bytes": _loud_frame(5.5)} for _ in range(6)]
        session, ws, _ = _run_session(frames, _FakeCfg(), language="ko",
                                      create_side_effect=_create)
        assert not [m for m in ws.sent if m.get("type") == "revise"]
        assert all("빠른 패스" in s["text"] for s in session.segments)

    def test_foreign_script_is_marked_not_dropped(self):
        frames = [{"bytes": _loud_frame(5.5)}]
        session, ws, _ = _run_session(
            frames, _FakeCfg({"realtime.two_pass": False}),
            language="ko", fast_text="где-нибудь 뭐가 있냐.")
        segs = [m for m in ws.sent if m.get("type") == "segment"]
        assert len(segs) == 1
        assert segs[0]["text"].startswith("[불명]")
        assert "где-нибудь" in segs[0]["text"]   # 원문은 남긴다(보수적)
