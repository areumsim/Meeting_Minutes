# -*- coding: utf-8 -*-
"""ingestion_pipeline.py 테스트.

1) _detect_type_from_filename(): 파일명 키워드 매치/미매치.
2) IngestionPipeline.ingest(): watcher 경로가 finalize.run_post_session()을
   통해 배치/웹과 동일한 오케스트레이션을 타는지(중복 로직 재발 방지 회귀 테스트).
전부 오프라인 — STT/LLM/Obsidian/finalize를 fake로 대체."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meeting_minutes_app.meeting_pipeline import ingestion_pipeline as ip


class TestDetectTypeFromFilename:
    def test_seminar_keyword_matches(self):
        assert ip._detect_type_from_filename("2026-07-08_세미나_발표자료.m4a") == "seminar"

    def test_lecture_keyword_matches(self):
        assert ip._detect_type_from_filename("AI강의_3주차.mp3") == "lecture"

    def test_english_keyword_matches(self):
        assert ip._detect_type_from_filename("webinar_quantum.wav") == "seminar"

    def test_no_keyword_returns_empty(self):
        # 자동 녹음기 기본 파일명 — 키워드 없음. 과거엔 여기서 바로 "meeting"으로
        # 떨어졌으나, 지금은 빈 문자열을 반환해 호출자(ingest())가 STT 이후
        # 내용 기반 LLM 보완 판단으로 넘길 수 있어야 한다.
        assert ip._detect_type_from_filename("20260707_143012.m4a") == ""


class TestExpectedRecordingNotePaths:
    """자동분류 라우팅 때문에 실제 저장 위치가 동적으로 정해질 수 있어,
    스킵 체크가 정적 경로 하나만이 아니라 라우팅 후보도 함께 봐야 한다.

    classify_meeting_route()는 meeting_workflow._c를 통해 config를 읽으므로
    (ingestion_pipeline._c와는 별개 함수 객체) 두 모듈 모두 patch해야
    실제 config.json 내용에 우연히 기대는 일 없이 테스트가 격리된다."""

    def _patch_both(self, monkeypatch, overrides):
        from meeting_minutes_app.meeting_pipeline import meeting_workflow as mw
        cfg = lambda k, d=None: overrides.get(k, d)  # noqa: E731
        monkeypatch.setattr(ip, "_c", cfg)
        monkeypatch.setattr(mw, "_c", cfg)

    def test_auto_route_disabled_returns_single_static_path(self, monkeypatch):
        self._patch_both(monkeypatch, {
            "obsidian.auto_route_enabled": False,
            "obsidian.meetings_path": "{project}/01_회의_세미나/회의별/{year}",
        })
        paths = ip._expected_recording_note_paths("주간보고", "", "2026-07-08")
        assert len(paths) == 1

    def test_auto_route_enabled_adds_folder_candidate(self, monkeypatch):
        self._patch_both(monkeypatch, {
            "obsidian.auto_route_enabled": True,
            "obsidian.meeting_categories": {
                "주간보고": {"mode": "folder", "folder": "00_Meetings/주간보고",
                          "keywords": ["주간보고"]},
            },
            "obsidian.project_domains": {},
            "obsidian.meetings_path": "{project}/01_회의_세미나/회의별/{year}",
        })
        paths = ip._expected_recording_note_paths("2026-07-08 주간보고", "", "2026-07-08")
        assert len(paths) == 2
        assert any(p.startswith("00_Meetings/주간보고/") for p in paths)

    def test_auto_route_enabled_domain_match_resolves_project_folder(self, monkeypatch):
        self._patch_both(monkeypatch, {
            "obsidian.auto_route_enabled": True,
            "obsidian.meeting_categories": {
                "양자": {"mode": "domain", "keywords": ["양자", "퀀텀"]},
            },
            "obsidian.project_domains": {"양자": "Archive/도메인_아카이브"},
            "obsidian.meetings_path": "{project}/01_회의_세미나/회의별/{year}",
        })
        paths = ip._expected_recording_note_paths("양자 정기미팅", "", "2026-07-08")
        assert any("Archive/도메인_아카이브" in p for p in paths)

    def test_folder_mode_category_absent_from_project_domains_still_resolves(self, monkeypatch):
        """2026-07 재설계 회귀 테스트: folder 모드 카테고리는 project_domains에
        없어도(백서온톨로지 사례) 자기 folder 필드로 정확히 라우팅돼야 한다."""
        self._patch_both(monkeypatch, {
            "obsidian.auto_route_enabled": True,
            "obsidian.meeting_categories": {
                "백서온톨로지": {"mode": "folder", "folder": "00_Meetings/백서온톨로지",
                             "keywords": ["백서", "온톨로지"]},
            },
            "obsidian.project_domains": {},  # 의도적으로 비움 — 교차조회 없어도 동작해야 함
            "obsidian.meetings_path": "{project}/01_회의_세미나/회의별/{year}",
        })
        paths = ip._expected_recording_note_paths("백서 온톨로지 공유", "", "2026-07-08")
        assert any(p.startswith("00_Meetings/백서온톨로지/") for p in paths)

    def test_classify_error_falls_back_to_static_only(self, monkeypatch):
        self._patch_both(monkeypatch, {
            "obsidian.auto_route_enabled": True,
        })
        from meeting_minutes_app.meeting_pipeline import meeting_workflow as mw

        def boom(*a, **kw):
            raise RuntimeError("boom")
        monkeypatch.setattr(mw, "classify_meeting_route", boom)
        paths = ip._expected_recording_note_paths("아무 제목", "", "2026-07-08")
        assert len(paths) == 1


SEGMENTS = [
    {"start": 0.0, "end": 3.0, "text": "오늘 안건은 PoC 범위입니다.", "speaker": "김철수"},
    {"start": 3.0, "end": 6.0, "text": "네, 동의합니다.", "speaker": "이영희"},
]


class FakeObs:
    def exists(self, path):
        return False

    def get_note(self, path):
        return ""


class FakeIndexer:
    is_built = True

    def search(self, query, limit=10):
        return []

    def get_note_content(self, path):
        return ""


class FakeLLM:
    pass


@pytest.fixture
def audio_file(tmp_path):
    p = tmp_path / "20260707_143012.m4a"
    p.write_bytes(b"0" * (1024 * 1024))  # 1MB, min_size 기본값(0.5MB) 통과
    return str(p)


@pytest.fixture
def patched(monkeypatch):
    """STT/화자추론/finalize를 기록형 fake로 대체하고 finalize 호출 인자를 캡처."""
    calls = {}

    monkeypatch.setattr(ip, "_c", lambda k, d=None: {
        "vault_watcher.min_size_mb": 0.1,
        "wiki.domain_classify_llm": True,
        "analysis.default_type": "meeting",
        "notify.on_finish": "",
        "output_dir": "output",
    }.get(k, d))

    from meeting_minutes_app.meeting_pipeline import stt as stt_mod
    from meeting_minutes_app.meeting_pipeline import meeting_minutes as mm_mod
    from meeting_minutes_app.meeting_pipeline import minutes_generation as mg_mod
    from meeting_minutes_app.meeting_pipeline import finalize as fz_mod

    monkeypatch.setattr(stt_mod, "prepare_audio", lambda path, work_dir: path)
    monkeypatch.setattr(stt_mod, "run_stt", lambda *a, **kw: SEGMENTS)
    monkeypatch.setattr(mm_mod, "audio_duration", lambda path: 120.0)
    monkeypatch.setattr(mg_mod, "infer_speaker_names", lambda *a, **kw: {})

    fake_result = SimpleNamespace(
        minutes="회의록 본문", summary="요약", actions_md="", actions_json="",
        source_note="00_Meetings/기타/260707 143012.md",
        related_note_titles=[],
    )

    def fake_run_post_session(inputs, options, events=None):
        calls["inputs"] = inputs
        calls["options"] = options
        return fake_result

    monkeypatch.setattr(fz_mod, "run_post_session", fake_run_post_session)
    return calls


class TestIngestUsesFinalize:
    def test_calls_finalize_run_post_session_once(self, patched, audio_file):
        pipeline = ip.IngestionPipeline(llm=FakeLLM(), obs=FakeObs(), vault_indexer=FakeIndexer())
        result = pipeline.ingest(audio_file)

        assert "inputs" in patched, "finalize.run_post_session이 호출되지 않음"
        assert result["status"] == "done"
        assert result["note_path"] == "00_Meetings/기타/260707 143012.md"

    def test_session_inputs_carry_segments_and_doc_type(self, patched, audio_file):
        pipeline = ip.IngestionPipeline(llm=FakeLLM(), obs=FakeObs(), vault_indexer=FakeIndexer())
        pipeline.ingest(audio_file, doc_type="meeting")

        inputs = patched["inputs"]
        assert inputs.segments == SEGMENTS
        assert inputs.doc_type == "meeting"
        assert inputs.source == "ingest"
        assert sorted(inputs.attendees) == ["김철수", "이영희"]

    def test_finalize_options_enable_graph_sync(self, patched, audio_file):
        pipeline = ip.IngestionPipeline(llm=FakeLLM(), obs=FakeObs(), vault_indexer=FakeIndexer())
        pipeline.ingest(audio_file)

        options = patched["options"]
        # 오늘 발견된 갭 수정 검증 — watcher도 그래프 동기화를 켜야 한다(기존엔 웹앱만).
        assert options.do_graph_sync is True
        assert options.llm is not None
        assert options.publish_extra.get("source_audio") == audio_file

    def test_explicit_doc_type_skips_content_classification(self, monkeypatch, patched, audio_file):
        from meeting_minutes_app.meeting_pipeline import meeting_workflow as mw

        def boom(*a, **kw):
            raise AssertionError("명시적 doc_type이 있으면 LLM 내용 분류를 호출하면 안 됨")
        monkeypatch.setattr(mw, "classify_doc_type_llm", boom)

        pipeline = ip.IngestionPipeline(llm=FakeLLM(), obs=FakeObs(), vault_indexer=FakeIndexer())
        pipeline.ingest(audio_file, doc_type="seminar")

        assert patched["inputs"].doc_type == "seminar"

    def test_no_note_falls_back_to_local_output(self, monkeypatch, patched, audio_file, tmp_path):
        from meeting_minutes_app.meeting_pipeline import finalize as fz_mod

        fake_result = SimpleNamespace(
            minutes="회의록 본문", summary="요약", actions_md="",
            source_note="",  # Obsidian 발행 실패
            related_note_titles=[],
        )
        monkeypatch.setattr(fz_mod, "run_post_session",
                            lambda inputs, options, events=None: fake_result)
        monkeypatch.setattr(ip, "_c", lambda k, d=None: {
            "vault_watcher.min_size_mb": 0.1,
            "output_dir": str(tmp_path / "out"),
        }.get(k, d))

        pipeline = ip.IngestionPipeline(llm=FakeLLM(), obs=FakeObs(), vault_indexer=FakeIndexer())
        result = pipeline.ingest(audio_file)

        assert result["status"] == "done"
        assert result["note_path"]
        assert Path(result["note_path"]).exists()
