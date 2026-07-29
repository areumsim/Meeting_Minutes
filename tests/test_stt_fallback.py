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
    # **kw 는 STT 체인이 넘기는 timeout/max_retries 를 받는다.
    monkeypatch.setattr(stt, "make_openai_client",
                        lambda k, **kw: MagicMock(name="openai"))
    monkeypatch.setattr(stt, "make_groq_client",
                        lambda k, **kw: MagicMock(name="groq"))
    monkeypatch.setattr(stt, "DEFAULT_STT_MODEL", "gpt-4o-mini-transcribe")
    monkeypatch.setattr(stt, "FALLBACK_STT_MODEL", "gpt-4o-transcribe")
    monkeypatch.setattr(stt, "GROQ_STT_MODEL", "whisper-large-v3-turbo")
    monkeypatch.setattr(stt, "LOCAL_STT_ENABLED", False)
    monkeypatch.setattr(stt, "LOCAL_STT_MODEL", "base")
    # 로컬 단계는 "라이브러리 + 가중치 준비됨"을 기본 전제로 둔다(개발 환경엔 둘 다 없다).
    # 미준비 상황은 아래 TestLocalStageGating 이 따로 검증한다.
    monkeypatch.setattr(stt, "local_lib_available", lambda: True)
    monkeypatch.setattr(stt, "local_model_status",
                        lambda m: {"installed": True, "model": m, "size_mb": 1.0,
                                   "path": "p", "lib_available": True})
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

        def _boom(_k, **_kw):
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


# ━━━━━━━━ 1.5) 로컬 단계 게이팅 — 미준비면 체인에 넣지 않는다 ━━━━━━━━
# 준비 안 된 로컬을 체인 끝에 넣으면 그 오류가 last_err 가 되어 앞선 진짜 원인
# (401/429)을 덮어쓴다. 매뉴얼도 "준비 안 됐으면 건너뛴다"고 안내한다.

class TestLocalStageGating:
    def test_excluded_when_library_missing(self, keys, monkeypatch):
        keys["OPENAI_API_KEY"] = "sk-test"
        monkeypatch.setattr(stt, "LOCAL_STT_ENABLED", True)
        monkeypatch.setattr(stt, "local_lib_available", lambda: False)
        assert all(p != "local" for p, _m in _shape(
            stt._build_stt_provider_chain("gpt-4o-mini-transcribe")))

    def test_excluded_when_weights_missing(self, keys, monkeypatch):
        keys["OPENAI_API_KEY"] = "sk-test"
        monkeypatch.setattr(stt, "LOCAL_STT_ENABLED", True)
        monkeypatch.setattr(stt, "local_model_status",
                            lambda m: {"installed": False, "model": m, "size_mb": 0.0,
                                       "path": "p", "lib_available": True})
        assert all(p != "local" for p, _m in _shape(
            stt._build_stt_provider_chain("gpt-4o-mini-transcribe")))

    def test_root_cause_error_is_not_masked_by_local(self, keys, monkeypatch):
        """로컬이 미준비면 사용자는 로컬 안내가 아니라 OpenAI 원인 오류를 봐야 한다."""
        keys["OPENAI_API_KEY"] = "sk-test"
        monkeypatch.setattr(stt, "LOCAL_STT_ENABLED", True)
        monkeypatch.setattr(stt, "local_lib_available", lambda: False)
        monkeypatch.setattr(stt, "_transcribe_chunk_checked",
                            lambda *a, **kw: (_ for _ in ()).throw(
                                RuntimeError("401 invalid_api_key")))
        chain = stt._build_stt_provider_chain("gpt-4o-mini-transcribe")
        with pytest.raises(RuntimeError, match="invalid_api_key"):
            stt._transcribe_chunk_via_chain(
                chain, "chunk.mp3", "ko", None, 0.0, None, 0, "work")

    def test_openai_client_failure_keeps_groq(self, keys, monkeypatch):
        """OpenAI 클라이언트 생성이 깨져도 Groq 단계는 남아야 한다."""
        keys["OPENAI_API_KEY"] = "sk-test"
        keys["GROQ_API_KEY"] = "gsk-test"

        def _boom(_k, **_kw):
            raise RuntimeError("proxy 설정 오류")

        monkeypatch.setattr(stt, "make_openai_client", _boom)
        assert _shape(stt._build_stt_provider_chain("gpt-4o-mini-transcribe")) == [
            ("Groq", "whisper-large-v3-turbo"),
        ]


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

    def test_empty_transcript_falls_through_to_next_provider(self, monkeypatch):
        """200 + 빈 텍스트(조용한 실패)도 실패로 보고 다음 제공자로 넘어간다."""
        calls = []

        def _checked(client, path, model, *a, **kw):
            calls.append(model)
            if model == "first":
                return [{"start": 0.0, "end": 1.0, "text": "   ", "speaker": ""}]
            return [{"start": 0.0, "end": 1.0, "text": "제대로 된 전사", "speaker": ""}]

        monkeypatch.setattr(stt, "_transcribe_chunk_checked", _checked)
        monkeypatch.setattr(stt, "_chunk_is_silent", lambda *a, **kw: False)
        chain = [("OpenAI", "first", MagicMock()), ("Groq", "second", MagicMock())]
        segs = stt._transcribe_chunk_via_chain(
            chain, "chunk.mp3", "ko", None, 0.0, None, 0, "work")
        assert calls == ["first", "second"]
        assert segs[0]["text"] == "제대로 된 전사"

    def test_silent_chunk_does_not_burn_the_chain(self, monkeypatch):
        """정말 무음인 구간은 첫 제공자의 빈 결과를 그대로 쓴다(헛돈 방지)."""
        calls = []

        def _checked(client, path, model, *a, **kw):
            calls.append(model)
            return [{"start": 0.0, "end": 1.0, "text": "", "speaker": ""}]

        monkeypatch.setattr(stt, "_transcribe_chunk_checked", _checked)
        monkeypatch.setattr(stt, "_chunk_is_silent", lambda *a, **kw: True)
        chain = [("OpenAI", "first", MagicMock()), ("Groq", "second", MagicMock())]
        segs = stt._transcribe_chunk_via_chain(
            chain, "chunk.mp3", "ko", None, 0.0, None, 0, "work")
        assert calls == ["first"], "무음이면 다른 제공자를 부르지 않는다"
        assert segs[0]["text"] == ""

    def test_all_empty_returns_empty_instead_of_raising(self, monkeypatch):
        """전 제공자가 (예외 없이) 비면 파일 전체를 중단시키지 않고 빈 결과를 쓴다."""
        monkeypatch.setattr(
            stt, "_transcribe_chunk_checked",
            lambda *a, **kw: [{"start": 0.0, "end": 1.0, "text": "", "speaker": ""}])
        monkeypatch.setattr(stt, "_chunk_is_silent", lambda *a, **kw: False)
        chain = [("OpenAI", "a", MagicMock()), ("Groq", "b", MagicMock())]
        segs = stt._transcribe_chunk_via_chain(
            chain, "chunk.mp3", "ko", None, 0.0, None, 0, "work")
        assert segs[0]["text"] == ""

    def test_empty_then_exception_prefers_the_empty_result(self, monkeypatch):
        """빈 결과라도 '예외 없이 받은 것'이 있으면 예외를 던지지 않는다(기존 동작 유지)."""
        def _checked(client, path, model, *a, **kw):
            if model == "a":
                return [{"start": 0.0, "end": 1.0, "text": "", "speaker": ""}]
            raise RuntimeError("네트워크 끊김")

        monkeypatch.setattr(stt, "_transcribe_chunk_checked", _checked)
        monkeypatch.setattr(stt, "_chunk_is_silent", lambda *a, **kw: False)
        chain = [("OpenAI", "a", MagicMock()), ("Groq", "b", MagicMock())]
        segs = stt._transcribe_chunk_via_chain(
            chain, "chunk.mp3", "ko", None, 0.0, None, 0, "work")
        assert segs[0]["text"] == ""

    def test_chain_passes_provider_through(self, monkeypatch):
        """배치 체인도 provider 를 넘겨야 벤더 전용 파라미터가 걸러진다."""
        seen = {}

        def _tc(client, path, model, language=None, speaker_names=None, offset=0.0,
                debug_dir=None, chunk_index=0, prompt=None, provider="OpenAI"):
            seen["provider"] = provider
            return [{"start": 0, "end": 1, "text": "ok", "speaker": ""}]

        monkeypatch.setattr(stt, "transcribe_chunk", _tc)
        monkeypatch.setattr(stt, "audio_duration", lambda _p: 0.0)
        stt._transcribe_chunk_via_chain(
            [("Groq", "whisper-large-v3-turbo", MagicMock())],
            "c.mp3", "ko", None, 0.0, None, 0, "work")
        assert seen["provider"] == "Groq"

    def test_speaker_hint_only_for_diarize_model(self):
        """화자명 힌트는 diarize 모델만 받는다(그 외 모델은 파라미터 자체를 거부).

        게이트는 요청 계약을 만드는 한 곳(stt_request_params)에만 있어야 한다 —
        예전엔 체인 순회에도 같은 판정이 복사돼 있었다."""
        names = ["김", "이"]
        p_plain, _ = stt.stt_request_params("OpenAI", "whisper-1", "ko", names)
        assert "known_speaker_names" not in p_plain
        p_diar, kind = stt.stt_request_params(
            "OpenAI", "gpt-4o-transcribe-diarize", "ko", names)
        assert p_diar["known_speaker_names"] == names and kind == "diarized"
        # Groq 에는 diarize 자체가 없으므로 모델명에 diarize 가 있어도 새어나가면 안 된다
        p_groq, gkind = stt.stt_request_params(
            "Groq", "gpt-4o-transcribe-diarize", "ko", names)
        assert "known_speaker_names" not in p_groq
        assert "chunking_strategy" not in p_groq and gkind != "diarized"


# ━━━━━━━━ 2.5) sticky — 죽은 제공자를 청크마다 다시 때리지 않는다 ━━━━━━━━
# 호출마다 timeout·재시도가 붙어 있어, 죽은 벤더를 청크 수만큼 반복 시도하면 처리가
# 멈춘 것처럼 보인다. 단, 한 번의 일시적 오류로 파일 끝까지 강등되면 안 된다.

class TestStickyProvider:
    def _chain(self):
        return [("OpenAI", "down", MagicMock()), ("Groq", "up", MagicMock())]

    def test_dead_provider_is_skipped_after_threshold(self, monkeypatch):
        calls = []

        def _checked(client, path, model, *a, **kw):
            calls.append(model)
            if model == "down":
                raise RuntimeError("connection timeout")
            return [{"start": 0.0, "end": 1.0, "text": "ok", "speaker": ""}]

        monkeypatch.setattr(stt, "_transcribe_chunk_checked", _checked)
        state = stt._ChainState()
        for i in range(4):
            stt._transcribe_chunk_via_chain(
                self._chain(), "c.mp3", "ko", None, 0.0, None, i, "work", state)
        # 임계값 2 → 청크 0,1 에서만 'down' 을 시도하고 이후엔 건너뛴다
        assert calls.count("down") == 2
        assert calls.count("up") == 4

    def test_single_transient_failure_does_not_demote(self, monkeypatch):
        calls = []
        cur = {"chunk": 0}

        def _checked(client, path, model, *a, **kw):
            calls.append(model)
            if model == "down" and cur["chunk"] == 0:
                raise RuntimeError("429 rate limit")   # 첫 청크에서만 일시 실패
            return [{"start": 0.0, "end": 1.0, "text": "ok", "speaker": ""}]

        monkeypatch.setattr(stt, "_transcribe_chunk_checked", _checked)
        state = stt._ChainState()
        for i in range(3):
            cur["chunk"] = i
            stt._transcribe_chunk_via_chain(
                self._chain(), "c.mp3", "ko", None, 0.0, None, i, "work", state)
        # 청크0: down 실패 → up. 청크1·2: down 이 회복돼 계속 1순위로 쓰인다.
        assert calls == ["down", "up", "down", "down"]

    def test_all_down_still_attempts_and_raises(self, monkeypatch):
        def _checked(client, path, model, *a, **kw):
            raise RuntimeError(f"{model} 죽음")

        monkeypatch.setattr(stt, "_transcribe_chunk_checked", _checked)
        state = stt._ChainState()
        for i in range(3):
            with pytest.raises(RuntimeError):
                stt._transcribe_chunk_via_chain(
                    self._chain(), "c.mp3", "ko", None, 0.0, None, i, "work", state)
        # 전부 죽었다고 판정돼도 아무것도 시도하지 않는 상태가 되면 안 된다
        assert state.is_down(0) and state.is_down(1)
        with pytest.raises(RuntimeError, match="죽음"):
            stt._transcribe_chunk_via_chain(
                self._chain(), "c.mp3", "ko", None, 0.0, None, 9, "work", state)

    def test_silent_chunk_does_not_demote_provider(self, monkeypatch):
        monkeypatch.setattr(
            stt, "_transcribe_chunk_checked",
            lambda *a, **kw: [{"start": 0.0, "end": 1.0, "text": "", "speaker": ""}])
        monkeypatch.setattr(stt, "_chunk_is_silent", lambda *a, **kw: True)
        state = stt._ChainState()
        for i in range(3):
            stt._transcribe_chunk_via_chain(
                self._chain(), "c.mp3", "ko", None, 0.0, None, i, "work", state)
        assert not state.is_down(0), "무음은 제공자 탓이 아니다"


# ━━━━━━━━ 2.6) HTTP 한도 — 죽은 벤더에 오래 매달리지 않는다 ━━━━━━━━

class TestSttHttpLimits:
    def test_chain_clients_get_explicit_timeout(self, keys, monkeypatch):
        seen = []
        keys["OPENAI_API_KEY"] = "sk-test"
        keys["GROQ_API_KEY"] = "gsk-test"
        monkeypatch.setattr(stt, "make_openai_client",
                            lambda k, **kw: seen.append(("openai", kw)) or MagicMock())
        monkeypatch.setattr(stt, "make_groq_client",
                            lambda k, **kw: seen.append(("groq", kw)) or MagicMock())
        stt._build_stt_provider_chain("gpt-4o-mini-transcribe")
        assert len(seen) == 2
        for _name, kw in seen:
            assert kw["timeout"] == stt.STT_REQUEST_TIMEOUT_SEC
            assert kw["max_retries"] == stt.STT_MAX_RETRIES
        # SDK 기본값(600초 × 재시도 2회)보다 짧아야 의미가 있다
        assert stt.STT_REQUEST_TIMEOUT_SEC < 600
        assert stt.STT_MAX_RETRIES < 2

    def test_chat_clients_keep_sdk_defaults(self):
        """채팅·회의록 생성 경로는 인자를 안 넘겨 기존 동작을 유지한다."""
        assert llm_client._sdk_limits(None, None) == {}
        assert llm_client._sdk_limits(300.0, 1) == {"timeout": 300.0, "max_retries": 1}


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

def _import_rt(monkeypatch):
    """realtime_transcription 임포트 — sounddevice(마이크 캡처)는 웹 전용 개발 환경엔
    없다. 여기서 보는 로직은 마이크와 무관하므로 없으면 스텁을 끼워 넣어 테스트가
    건너뛰이지 않게 한다."""
    if "sounddevice" not in sys.modules:
        try:
            import sounddevice  # noqa: F401
        except Exception:
            monkeypatch.setitem(sys.modules, "sounddevice", MagicMock())
    from meeting_minutes_app.meeting_pipeline import realtime_transcription as rt
    return rt


class TestSttFailureIsCounted:
    """전사 0건의 원인을 마이크 문제와 구분하기 위한 카운터.

    과거엔 STT 벤더 실패로 전 청크가 폐기돼도 종료 화면이 "마이크 및 음량을
    확인하세요"만 띄워 사용자를 엉뚱한 곳으로 보냈다."""

    def test_process_counts_discarded_chunks(self, monkeypatch):
        import numpy as np
        rt = _import_rt(monkeypatch)

        tr = rt.RealtimeTranscriber(MagicMock(), stt_model="gpt-4o-transcribe",
                                    language="ko")
        assert tr._stt_error_chunks == 0
        monkeypatch.setattr(rt.AudioRecorder, "to_wav_bytes",
                            staticmethod(lambda _a: b"\x00" * 16))
        monkeypatch.setattr(tr, "_run_stt",
                            lambda _w: (_ for _ in ()).throw(RuntimeError("503")))
        assert tr.process(np.zeros(16, dtype=np.float32)) is None
        assert tr.process(np.zeros(16, dtype=np.float32)) is None
        assert tr._stt_error_chunks == 2


def _mock_openai_client() -> MagicMock:
    """실시간 경로용 OpenAI 클라이언트 목.

    `_stt_client()` 이 `with_options()` 로 한도만 좁힌 **사본**을 쓰므로, 사본이 원본과
    같은 동작을 하도록 자기 자신을 돌려준다(실제 SDK 도 하위 httpx 를 공유하는 얕은
    사본이다). 이걸 안 해 주면 사본이 side_effect 없는 새 목이 되어, 테스트가 폴백을
    전혀 검증하지 못하면서도 통과한다."""
    c = MagicMock()
    c.with_options.return_value = c
    return c


class TestCliRealtimeGroqFallback:
    def test_stt_client_gets_explicit_request_limits(self, monkeypatch):
        """라이브 STT 는 SDK 기본값(600초×2회)이 아니라 짧은 한도를 써야 한다.

        기본값이면 _run_stt 의 3회 루프와 곱해져, 응답 없이 매달리는 벤더에서 청크
        하나가 Groq 에 닿기까지 몇 시간 규모로 막힌다."""
        rt = _import_rt(monkeypatch)
        openai_client = _mock_openai_client()
        tr = rt.RealtimeTranscriber(openai_client, stt_model="gpt-4o-transcribe",
                                    language="ko")
        assert tr._stt_client() is openai_client
        openai_client.with_options.assert_called_once_with(
            timeout=stt.STT_REQUEST_TIMEOUT_SEC, max_retries=stt.STT_MAX_RETRIES)
        # 세션당 1회만 만든다(청크마다 새로 만들면 낭비)
        tr._stt_client()
        assert openai_client.with_options.call_count == 1

    def test_openai_fallback_model_is_tried_before_groq(self, monkeypatch):
        """기본 모델 3회 실패 후 OpenAI 폴백 모델을 먼저 시도한다.

        이 단계가 없던 동안엔 Groq 키가 없는 사용자(대부분)는 기본 모델이 계정에서
        못 쓰는 상태이면 청크가 그대로 폐기됐다. 웹 라이브에는 있고 CLI 에만 없던
        비대칭이기도 했다."""
        rt = _import_rt(monkeypatch)
        monkeypatch.setattr(rt.time, "sleep", lambda *_: None)

        seen: list = []

        def _create(**params):
            seen.append(params["model"])
            if params["model"] == "gpt-4o-transcribe-diarize":
                raise RuntimeError("403 model not available")
            resp = MagicMock()
            resp.text = "폴백 모델 전사"
            return resp

        openai_client = _mock_openai_client()
        openai_client.audio.transcriptions.create.side_effect = _create
        # Groq 까지 갔는지 확인 — 가면 안 된다
        groq_client = MagicMock()
        monkeypatch.setattr(stt, "groq_fallback",
                            lambda: (groq_client, "whisper-large-v3-turbo"))
        monkeypatch.setattr(stt, "FALLBACK_STT_MODEL", "gpt-4o-transcribe")

        tr = rt.RealtimeTranscriber(openai_client, stt_model="gpt-4o-transcribe-diarize",
                                    language="ko")
        out = tr._run_stt(b"\x00" * 32)

        assert out == "폴백 모델 전사"
        assert seen == ["gpt-4o-transcribe-diarize"] * 3 + ["gpt-4o-transcribe"]
        groq_client.audio.transcriptions.create.assert_not_called()

    def test_falls_back_to_groq_after_openai_retries(self, monkeypatch):
        rt = _import_rt(monkeypatch)

        monkeypatch.setattr(rt.time, "sleep", lambda *_: None)  # 재시도 대기 제거

        openai_client = _mock_openai_client()
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
        monkeypatch.setattr(stt, "FALLBACK_STT_MODEL", "gpt-4o-transcribe")

        tr = rt.RealtimeTranscriber(openai_client, stt_model="gpt-4o-transcribe-diarize",
                                    language="ko")
        out = tr._run_stt(b"\x00" * 32)

        assert out == "그록 전사"
        # 기본 모델 3회 + 폴백 모델 1회 = 4회 (그 뒤에야 다른 벤더)
        assert openai_client.audio.transcriptions.create.call_count == 4
        assert captured["model"] == "whisper-large-v3-turbo"
        # 웹·배치와 같은 계약을 쓴다(stt.stt_request_params 단일 소스) — 과거엔 이
        # 경로만 response_format='json' 을 강제해 벤더 하나에 계약이 두 벌이었다.
        assert captured["response_format"] == "verbose_json"
        assert "chunking_strategy" not in captured        # OpenAI 전용 파라미터
        assert "prompt" not in captured                   # Whisper prompt 224토큰 제한
