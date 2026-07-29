"""STT 폴백 체인 회귀 테스트 — 실제 API/네트워크/모델 가중치 없이 실행.

지키려는 계약:
  1) 체인 구성: OpenAI 기본 → OpenAI 폴백모델 → Groq(다른 벤더) → 로컬(faster-whisper).
     키가 없거나 로컬이 꺼져 있으면 그 단계는 조용히 빠지고, 아무 제공자도 없으면
     사용자에게 무엇을 설정해야 하는지 알려주는 한국어 예외가 난다.
  2) 앞 제공자가 어떤 이유로든 실패하면 다음 제공자로 넘어간다(전부 실패 시 마지막 예외).
  3) 로컬 단계는 **전사 중에 가중치를 내려받지 않는다** — 준비 안 됐으면 즉시 안내 예외.
     (다운로드는 웹 [설정]의 prepare_local_model 경로에서만)
  4) 비용 표가 Groq/로컬 모델을 알고 있어 폴백 세션 추정이 OpenAI 단가로 왜곡되지 않는다.

실행:
    python -m pytest tests/test_stt_fallback.py -q
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meeting_minutes_app.common import llm_client, pricing  # noqa: E402
from meeting_minutes_app.meeting_pipeline import stt  # noqa: E402


@pytest.fixture
def keys(monkeypatch):
    """get_api_key 를 가로채 원하는 키 조합을 만든다(환경변수·config 무관하게)."""
    state = {"OPENAI_API_KEY": "", "GROQ_API_KEY": ""}

    def _get(name, default=""):
        return state.get(name, "")

    monkeypatch.setattr(stt, "get_api_key", _get)
    monkeypatch.setattr(stt, "make_openai_client", lambda k: MagicMock(name="openai"))
    monkeypatch.setattr(stt, "make_groq_client", lambda k: MagicMock(name="groq"))
    monkeypatch.setattr(stt, "DEFAULT_STT_MODEL", "gpt-4o-mini-transcribe")
    monkeypatch.setattr(stt, "FALLBACK_STT_MODEL", "gpt-4o-transcribe")
    monkeypatch.setattr(stt, "GROQ_STT_MODEL", "whisper-large-v3-turbo")
    monkeypatch.setattr(stt, "LOCAL_STT_ENABLED", False)
    monkeypatch.setattr(stt, "LOCAL_STT_MODEL", "base")
    return state


def _shape(chain):
    """체인을 (제공자, 모델) 목록으로 — 클라이언트 객체는 비교에서 제외."""
    return [(p, m) for p, m, _c in chain]


# ━━━━━━━━ 1) 체인 구성 ━━━━━━━━

class TestProviderChain:
    def test_openai_only(self, keys):
        keys["OPENAI_API_KEY"] = "sk-test"
        chain = stt._build_stt_provider_chain("gpt-4o-mini-transcribe")
        assert _shape(chain) == [
            ("OpenAI", "gpt-4o-mini-transcribe"),
            ("OpenAI", "gpt-4o-transcribe"),
        ]

    def test_groq_appended_after_openai(self, keys):
        keys["OPENAI_API_KEY"] = "sk-test"
        keys["GROQ_API_KEY"] = "gsk-test"
        chain = stt._build_stt_provider_chain("gpt-4o-mini-transcribe")
        assert _shape(chain)[-1] == ("Groq", "whisper-large-v3-turbo")

    def test_local_is_last_when_enabled(self, keys, monkeypatch):
        keys["OPENAI_API_KEY"] = "sk-test"
        keys["GROQ_API_KEY"] = "gsk-test"
        monkeypatch.setattr(stt, "LOCAL_STT_ENABLED", True)
        chain = stt._build_stt_provider_chain("gpt-4o-mini-transcribe")
        assert _shape(chain) == [
            ("OpenAI", "gpt-4o-mini-transcribe"),
            ("OpenAI", "gpt-4o-transcribe"),
            ("Groq", "whisper-large-v3-turbo"),
            ("local", "base"),
        ]
        assert chain[-1][2] is None, "로컬 단계는 클라이언트가 없다(별도 경로)"

    def test_groq_only_when_openai_key_missing(self, keys):
        """OpenAI 키가 없어도 Groq만으로 전사할 수 있어야 한다."""
        keys["GROQ_API_KEY"] = "gsk-test"
        assert _shape(stt._build_stt_provider_chain("gpt-4o-mini-transcribe")) == [
            ("Groq", "whisper-large-v3-turbo"),
        ]

    def test_same_fallback_model_is_not_duplicated(self, keys):
        keys["OPENAI_API_KEY"] = "sk-test"
        chain = stt._build_stt_provider_chain("gpt-4o-transcribe")
        assert _shape(chain) == [("OpenAI", "gpt-4o-transcribe")]

    def test_groq_client_failure_excludes_stage(self, keys, monkeypatch):
        keys["OPENAI_API_KEY"] = "sk-test"
        keys["GROQ_API_KEY"] = "gsk-test"

        def _boom(_k):
            raise RuntimeError("openai SDK 없음")

        monkeypatch.setattr(stt, "make_groq_client", _boom)
        assert all(p != "Groq" for p, _m in _shape(
            stt._build_stt_provider_chain("gpt-4o-mini-transcribe")))

    def test_no_provider_raises_korean_guidance(self, keys):
        """키도 없고 로컬도 꺼져 있으면 무엇을 해야 하는지 알려준다."""
        with pytest.raises(RuntimeError) as ei:
            stt.run_stt("없는파일.mp3")
        msg = str(ei.value)
        assert "STT 제공자가 없습니다" in msg
        assert "OPENAI_API_KEY" in msg and "GROQ_API_KEY" in msg


# ━━━━━━━━ 2) 폴백 동작 ━━━━━━━━

class TestChainFallbackBehavior:
    def test_moves_to_next_provider_on_failure(self, monkeypatch):
        calls = []

        def _checked(client, path, model, *a, **kw):
            calls.append(model)
            if model == "first":
                raise RuntimeError("429 rate limit")
            return [{"start": 0.0, "end": 1.0, "text": "ok", "speaker": ""}]

        monkeypatch.setattr(stt, "_transcribe_chunk_checked", _checked)
        chain = [("OpenAI", "first", MagicMock()), ("Groq", "second", MagicMock())]
        segs = stt._transcribe_chunk_via_chain(
            chain, "chunk.mp3", "ko", None, 0.0, None, 0, "work")
        assert calls == ["first", "second"]
        assert segs[0]["text"] == "ok"

    def test_local_stage_uses_transcribe_local(self, monkeypatch):
        monkeypatch.setattr(stt, "_transcribe_chunk_checked",
                            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("net down")))
        seen = {}

        def _local(path, model_size, language=None, offset=0.0):
            seen.update(path=path, model=model_size, language=language, offset=offset)
            return [{"start": offset, "end": offset + 1, "text": "로컬", "speaker": ""}]

        monkeypatch.setattr(stt, "transcribe_local", _local)
        chain = [("OpenAI", "gpt-4o-transcribe", MagicMock()), ("local", "base", None)]
        segs = stt._transcribe_chunk_via_chain(
            chain, "chunk.mp3", "ko", ["김"], 12.5, None, 3, "work")
        assert segs[0]["text"] == "로컬"
        assert seen == {"path": "chunk.mp3", "model": "base",
                        "language": "ko", "offset": 12.5}

    def test_last_error_propagates_when_all_fail(self, monkeypatch):
        def _checked(client, path, model, *a, **kw):
            raise RuntimeError(f"{model} 실패")

        monkeypatch.setattr(stt, "_transcribe_chunk_checked", _checked)
        chain = [("OpenAI", "a", MagicMock()), ("Groq", "b", MagicMock())]
        with pytest.raises(RuntimeError, match="b 실패"):
            stt._transcribe_chunk_via_chain(
                chain, "chunk.mp3", "ko", None, 0.0, None, 0, "work")

    def test_speaker_hint_only_for_diarize_model(self, monkeypatch):
        """화자명 힌트는 diarize 모델만 받는다(그 외 모델은 파라미터 자체를 거부)."""
        seen = []

        def _checked(client, path, model, language, spk, *a, **kw):
            seen.append((model, spk))
            return [{"start": 0, "end": 1, "text": "x", "speaker": ""}]

        monkeypatch.setattr(stt, "_transcribe_chunk_checked", _checked)
        stt._transcribe_chunk_via_chain(
            [("OpenAI", "whisper-large-v3", MagicMock())],
            "c.mp3", "ko", ["김", "이"], 0.0, None, 0, "work")
        stt._transcribe_chunk_via_chain(
            [("OpenAI", "gpt-4o-transcribe-diarize", MagicMock())],
            "c.mp3", "ko", ["김", "이"], 0.0, None, 0, "work")
        assert seen == [("whisper-large-v3", None),
                        ("gpt-4o-transcribe-diarize", ["김", "이"])]


# ━━━━━━━━ 3) Groq 클라이언트 ━━━━━━━━

class TestGroqClient:
    def test_points_at_groq_endpoint(self):
        client = llm_client.make_groq_client("gsk-test")
        assert "api.groq.com" in str(client.base_url)

    def test_groq_fallback_without_key(self, keys):
        assert stt.groq_fallback() == (None, "")

    def test_groq_fallback_with_key(self, keys):
        keys["GROQ_API_KEY"] = "gsk-test"
        client, model = stt.groq_fallback()
        assert client is not None and model == "whisper-large-v3-turbo"


# ━━━━━━━━ 4) 로컬 백업 — 전사 중 다운로드 금지 ━━━━━━━━

class TestLocalBackup:
    def test_missing_library_message(self, monkeypatch):
        def _boom(model_size, allow_download=False):
            raise ImportError("No module named 'faster_whisper'")

        monkeypatch.setattr(stt, "_get_local_model", _boom)
        with pytest.raises(RuntimeError, match="faster-whisper"):
            stt.transcribe_local("a.mp3", "base")

    def test_missing_weights_tells_user_to_prepare(self, monkeypatch):
        """가중치가 없으면 '준비하세요' 안내로 즉시 실패한다(다운로드 시작 금지)."""
        def _boom(model_size, allow_download=False):
            assert allow_download is False, "전사 경로는 다운로드를 허용하지 않는다"
            raise OSError("LocalEntryNotFoundError")

        monkeypatch.setattr(stt, "_get_local_model", _boom)
        with pytest.raises(RuntimeError, match="로컬 백업 모델이 준비되지 않았습니다"):
            stt.transcribe_local("a.mp3", "base")

    def test_segments_shape_and_offset(self, monkeypatch):
        class _Seg:
            def __init__(self, s, e, t):
                self.start, self.end, self.text = s, e, t

        class _Model:
            def transcribe(self, path, **kw):
                assert kw.get("vad_filter") is True, "무음 환각 방지용 VAD 유지"
                assert kw.get("language") == "ko"
                return iter([_Seg(0.0, 1.5, " 안녕하세요 "), _Seg(1.5, 2.0, "")]), None

        monkeypatch.setattr(stt, "_get_local_model", lambda m, allow_download=False: _Model())
        segs = stt.transcribe_local("a.mp3", "base", language="ko", offset=10.0)
        assert segs == [{"start": 10.0, "end": 11.5, "text": "안녕하세요", "speaker": ""}]

    def test_auto_language_is_not_forwarded(self, monkeypatch):
        class _Model:
            def transcribe(self, path, **kw):
                assert kw.get("language") is None, "auto 면 모델이 스스로 판정"
                return iter([]), None

        monkeypatch.setattr(stt, "_get_local_model", lambda m, allow_download=False: _Model())
        segs = stt.transcribe_local("a.mp3", "base", language="auto", offset=3.0)
        assert segs == [{"start": 3.0, "end": 3.0, "text": "", "speaker": ""}]

    def test_status_detects_prepared_weights(self, monkeypatch, tmp_path):
        snap = tmp_path / "models--Systran--faster-whisper-base" / "snapshots" / "rev1"
        snap.mkdir(parents=True)
        (snap / "model.bin").write_bytes(b"x" * 2048)
        monkeypatch.setattr(stt, "local_models_dir", lambda: str(tmp_path))

        st = stt.local_model_status("base")
        assert st["installed"] is True and st["model"] == "base"
        assert st["path"] == str(snap)
        # 다른 크기는 준비된 것으로 오인하지 않는다
        assert stt.local_model_status("large-v3")["installed"] is False

    def test_prepare_allows_download(self, monkeypatch):
        seen = {}

        def _get(model_size, allow_download=False):
            seen.update(model=model_size, allow_download=allow_download)

        monkeypatch.setattr(stt, "local_lib_available", lambda: True)
        monkeypatch.setattr(stt, "_get_local_model", _get)
        monkeypatch.setattr(stt, "local_model_status",
                            lambda m: {"installed": True, "model": m, "size_mb": 1.0,
                                       "path": "p", "lib_available": True})
        out = stt.prepare_local_model("base")
        assert seen == {"model": "base", "allow_download": True}
        assert out["installed"] is True and "elapsed_sec" in out

    def test_prepare_without_library_fails_clearly(self, monkeypatch):
        monkeypatch.setattr(stt, "local_lib_available", lambda: False)
        with pytest.raises(RuntimeError, match="faster-whisper"):
            stt.prepare_local_model("base")


# ━━━━━━━━ 5) 비용 단가 ━━━━━━━━

class TestFallbackPricing:
    def test_groq_rates_are_cheaper_than_openai(self):
        assert pricing.stt_rate_per_min("whisper-large-v3-turbo") < \
               pricing.stt_rate_per_min("gpt-4o-mini-transcribe")
        # 표에 없으면 기본 단가(0.006)로 잘못 계산된다 — 그 회귀 방지
        assert pricing.stt_rate_per_min("whisper-large-v3") != \
               pricing.DEFAULT_STT_PRICE_PER_MIN

    def test_local_models_are_free(self):
        for size in ("tiny", "base", "small", "medium", "large-v3"):
            assert pricing.stt_rate_per_min(size) == 0.0


# ━━━━━━━━ 6) 실시간(CLI http 청크) Groq 폴백 ━━━━━━━━
# 웹(web/backend/api/realtime.py) 쪽 같은 폴백은 WS 세션 내부 클로저라 단위 테스트로
# 접근할 수 없다 — docs 의 수동 검증(녹음 중 키 무효화)으로 확인한다.

class TestCliRealtimeGroqFallback:
    def test_falls_back_to_groq_after_openai_retries(self, monkeypatch):
        # sounddevice(마이크 캡처)는 웹 전용 개발 환경엔 없다. _run_stt 는 마이크와
        # 무관하므로 없으면 스텁을 끼워 넣어 테스트가 건너뛰이지 않게 한다.
        if "sounddevice" not in sys.modules:
            try:
                import sounddevice  # noqa: F401
            except Exception:
                monkeypatch.setitem(sys.modules, "sounddevice", MagicMock())
        from meeting_minutes_app.meeting_pipeline import realtime_transcription as rt

        monkeypatch.setattr(rt.time, "sleep", lambda *_: None)  # 재시도 대기 제거

        openai_client = MagicMock()
        openai_client.audio.transcriptions.create.side_effect = RuntimeError("503")

        captured = {}

        def _groq_create(**params):
            captured.update(params)
            resp = MagicMock()
            resp.text = "그록 전사"
            return resp

        groq_client = MagicMock()
        groq_client.audio.transcriptions.create.side_effect = _groq_create
        monkeypatch.setattr(stt, "groq_fallback",
                            lambda: (groq_client, "whisper-large-v3-turbo"))

        tr = rt.RealtimeTranscriber(openai_client, stt_model="gpt-4o-transcribe-diarize",
                                    language="ko")
        out = tr._run_stt(b"\x00" * 32)

        assert out == "그록 전사"
        assert openai_client.audio.transcriptions.create.call_count == 3
        assert captured["model"] == "whisper-large-v3-turbo"
        assert captured["response_format"] == "json"      # diarize 미지원
        assert "chunking_strategy" not in captured
        assert "prompt" not in captured                   # Whisper prompt 224토큰 제한
