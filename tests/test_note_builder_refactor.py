"""
wiki_core/obsidian.py 노트 포맷팅 로직을 note_builder.py로 분리하는 리팩토링용
characterization test — 리팩토링 전/후 생성되는 마크다운이 byte-for-byte 동일한지 확인.

골든 픽스처(tests/fixtures/note_builder_golden.json)는 리팩토링 전 코드로 생성됨.
datetime.now()는 고정 시각으로 monkeypatch하여 결정적으로 만듦.
"""

import datetime as _dt
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meeting_minutes_app.wiki_core import obsidian as ob  # noqa: E402

GOLDEN_PATH = Path(__file__).parent / "fixtures" / "note_builder_golden.json"
with open(GOLDEN_PATH, encoding="utf-8") as f:
    GOLDEN = json.load(f)


class _FrozenDateTime(_dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 7, 3, 12, 0, 0)


@pytest.fixture
def frozen_now(monkeypatch):
    monkeypatch.setattr(ob, "datetime", _FrozenDateTime)


@pytest.fixture
def no_supermemory(monkeypatch):
    monkeypatch.setattr(ob, "_sm_save", lambda *a, **k: None)


def _make_client():
    return ob.ObsidianClient(api_url="https://127.0.0.1:27124", api_key="testkey", project="테스트프로젝트")


def _capture(client, monkeypatch):
    box = []

    def fake_put(path, content):
        box.append([path, content])
        return True

    monkeypatch.setattr(client, "put_note", fake_put)
    return box


def _cfg(transcript_mode):
    def _get(key, default=None):
        if key == "obsidian.transcript_mode":
            return transcript_mode
        return default
    return _get


class TestWriteMeetingNote:
    def test_separate_transcript(self, monkeypatch, frozen_now, no_supermemory):
        monkeypatch.setattr(ob, "_c", _cfg("separate"))
        client = _make_client()
        box = _capture(client, monkeypatch)
        path = client.write_meeting_note(
            title="테스트 회의", body_md="본문 내용입니다.", doc_type="meeting", topic="주제",
            attendees=["김철수", "이영희"], session_dt="2026-07-01 10:00",
            summary_md="요약 내용", glossary_md="- **용어**: 설명",
            actions_md="- [ ] 할 일", related_notes=["관련노트1"],
            external_refs=[{"title": "참고", "url": "https://example.com"}],
            transcript_md="화자1: 안녕하세요\n화자2: 네 안녕하세요",
            processed_at="2026-07-01T10:30:00",
        )
        golden = GOLDEN["meeting_note_separate"]
        assert path == golden["path"]
        assert box == golden["calls"]

    def test_append_transcript_no_glossary(self, monkeypatch, frozen_now, no_supermemory):
        monkeypatch.setattr(ob, "_c", _cfg("append"))
        client = _make_client()
        box = _capture(client, monkeypatch)
        path = client.write_meeting_note(
            title="테스트 회의2", body_md="본문2", doc_type="seminar", topic="주제2",
            attendees=[], session_dt="", summary_md="", glossary_md="",
            actions_md="", transcript_md="전사 내용", processed_at="2026-07-01T11:00:00",
        )
        golden = GOLDEN["meeting_note_append"]
        assert path == golden["path"]
        assert box == golden["calls"]

    def test_output_folder_override_wins_over_meetings_path(self, monkeypatch, frozen_now, no_supermemory):
        """자동분류 라우팅(meeting_workflow.classify_meeting_route)이 고른
        output_folder가 meetings_path 템플릿보다 우선해야 한다."""
        monkeypatch.setattr(ob, "_c", _cfg("off"))
        client = ob.ObsidianClient(
            api_url="https://127.0.0.1:27124", api_key="testkey", project="양자",
            meetings_path="{project}/01_회의_세미나/회의별/{year}",
            project_domains={"양자": "Archive/도메인_아카이브"},
        )
        box = _capture(client, monkeypatch)
        path = client.write_meeting_note(
            title="주간보고", body_md="본문", doc_type="meeting", topic="",
            session_dt="2026-07-08", processed_at="2026-07-08T09:00:00",
            output_folder="00_Meetings/주간보고",
        )
        assert path == "00_Meetings/주간보고/260708 주간보고.md"

    def test_no_output_folder_falls_back_to_meetings_path(self, monkeypatch, frozen_now, no_supermemory):
        monkeypatch.setattr(ob, "_c", _cfg("off"))
        client = ob.ObsidianClient(
            api_url="https://127.0.0.1:27124", api_key="testkey", project="양자",
            meetings_path="{project}/01_회의_세미나/회의별/{year}",
            project_domains={"양자": "Archive/도메인_아카이브"},
        )
        box = _capture(client, monkeypatch)
        path = client.write_meeting_note(
            title="양자 회의", body_md="본문", doc_type="meeting", topic="",
            session_dt="2026-07-08", processed_at="2026-07-08T09:00:00",
        )
        assert path == "Archive/도메인_아카이브/01_회의_세미나/회의별/2026/260708 양자 회의.md"

    def test_output_folder_keeps_transcript_alongside_note(self, monkeypatch, frozen_now, no_supermemory):
        """output_folder로 라우팅된 회의(팀회의/주간보고 등)는 전사 노트도 같은 폴더에
        저장돼야 한다 — transcripts_path 아카이브 템플릿으로 갈라지면 본문/전사가
        서로 다른 폴더로 분리되는 버그가 있었다."""
        monkeypatch.setattr(ob, "_c", _cfg("separate"))
        client = ob.ObsidianClient(
            api_url="https://127.0.0.1:27124", api_key="testkey", project="양자",
            meetings_path="{project}/01_회의_세미나/회의별/{year}",
            transcripts_path="{project}/01_회의_세미나/전사/{year}",
            project_domains={"양자": "Archive/도메인_아카이브"},
        )
        box = _capture(client, monkeypatch)
        client.write_meeting_note(
            title="주간보고", body_md="본문", doc_type="meeting", topic="",
            session_dt="2026-07-08", processed_at="2026-07-08T09:00:00",
            transcript_md="화자1: 안녕하세요",
            output_folder="00_Meetings/주간보고",
        )
        transcript_paths = [p for p, _ in box if "전사" in p]
        assert transcript_paths == ["00_Meetings/주간보고/260708 주간보고 - 전사.md"]


class TestUpdatePlannedNote:
    def test_merge(self, monkeypatch, frozen_now, no_supermemory):
        monkeypatch.setattr(ob, "_c", _cfg("separate"))
        client = _make_client()
        box = _capture(client, monkeypatch)
        match = {
            "path": "00_Meetings/공통/260701 계획회의.md",
            "meta": {"title": "계획 회의", "date": "2026-07-01", "time": "10:00",
                     "type": "meeting", "project": "테스트프로젝트", "topic": "계획주제",
                     "attendees": ["김철수"], "company": "", "status": "planned",
                     "created": "2026-06-30T09:00:00"},
            "body": "# 계획 회의\n\n## 사전 조사\n\n- 조사 내용",
        }
        path = client.update_planned_note(
            match, title="계획 회의", body_md="실제 본문", doc_type="meeting", topic="실제주제",
            attendees=["이영희"], session_dt="2026-07-01 10:05", summary_md="병합 요약",
            actions_md="- [ ] 병합 액션", glossary_md="- **병합용어**: 설명",
            related_notes=["관련노트3"], processed_at="2026-07-01T11:00:00",
            transcript_md="병합 전사",
        )
        golden = GOLDEN["update_planned_note"]
        assert path == golden["path"]
        assert box == golden["calls"]


class TestMergeRecordingIntoPlan:
    def test_merge(self, monkeypatch, frozen_now, no_supermemory):
        client = _make_client()

        def fake_get_note(path):
            if path == "rec.md":
                return "---\nattendees:\n  - 김철수\nmatched_plan: plan.md\n---\n녹음 본문 내용"
            if path == "plan.md":
                return "---\nattendees:\n  - 이영희\nstatus: planned\n---\n계획 본문 내용"
            return None

        monkeypatch.setattr(client, "get_note", fake_get_note)
        box = _capture(client, monkeypatch)
        path = client.merge_recording_into_plan("rec.md")
        golden = GOLDEN["merge_recording_into_plan"]
        assert path == golden["path"]
        assert box == golden["calls"]


# ── 트랙 A: 참조 노트 자동 보강 ──────────────────────────────────

from meeting_minutes_app.wiki_core.note_builder import build_reference_note_update  # noqa: E402


class TestBuildReferenceNoteUpdate:
    def test_appends_new_block_and_updates_meta(self):
        meta = {"title": "한빛", "type": "reference", "category": "기업·기관",
                "tags": ["용어집"], "created": "2026-07-01T10:00:00"}
        body = "\n# 한빛\n\n한빛은 국내 화학·소재 기업입니다.\n"
        result = build_reference_note_update(
            meta, body,
            new_description="한빛은 2026년 양자컴퓨팅 사업에도 진출했습니다.",
            new_sources=[{"title": "기사", "url": "https://example.com/a"}],
            mentioned_by="260708 전략회의",
            now_iso="2026-07-08T09:00:00",
            max_updates=5,
        )
        assert result is not None
        assert "한빛은 국내 화학·소재 기업입니다." in result  # 원본 보존
        assert "## 추가 언급 기록" in result
        assert "### 2026-07-08 — 260708 전략회의" in result
        assert "한빛은 2026년 양자컴퓨팅 사업에도 진출했습니다." in result
        assert "[기사](https://example.com/a)" in result
        assert 'mentioned_in:\n  - "260708 전략회의"' in result
        assert "mention_count: 2" in result

    def test_skips_when_description_already_present(self):
        meta = {"title": "용어", "type": "reference"}
        body = "\n# 용어\n\n이미 있는 설명입니다. 추가 정보 없음.\n"
        result = build_reference_note_update(
            meta, body,
            new_description="이미 있는 설명입니다. 추가 정보 없음.",
            now_iso="2026-07-08T09:00:00",
        )
        assert result is None

    def test_skips_when_new_description_empty(self):
        meta = {"title": "용어", "type": "reference"}
        result = build_reference_note_update(meta, "본문", new_description="   ")
        assert result is None

    def test_caps_history_to_max_updates(self):
        meta = {"title": "용어", "type": "reference", "mention_count": 5}
        body = "\n# 용어\n\n최초 설명\n\n## 추가 언급 기록\n\n" + "\n\n".join(
            f"### 2026-07-0{i} — 회의{i}\n\n설명{i}" for i in range(1, 6)
        )
        result = build_reference_note_update(
            meta, body,
            new_description="새로운 6번째 설명",
            mentioned_by="회의6",
            now_iso="2026-07-09T09:00:00",
            max_updates=5,
        )
        assert result is not None
        assert "회의1" not in result  # 가장 오래된 블록 제거됨
        assert "회의2" in result
        assert "회의6" in result
        assert result.count("### 2026-07") == 5  # 캡 유지


class TestCreateReferenceNoteEnrichment:
    def test_new_note_created_when_absent(self, monkeypatch, frozen_now, no_supermemory):
        client = _make_client()
        box = _capture(client, monkeypatch)
        monkeypatch.setattr(client, "_find_ref_note_path", lambda base: None)
        base = client.create_reference_note(
            "새용어", "새 용어 설명입니다.", category="용어·기술", mentioned_by="첫 회의",
        )
        assert base == "새용어"
        assert len(box) == 1  # 신규 생성 1회 put

    def test_existing_note_is_enriched_not_skipped(self, monkeypatch, frozen_now, no_supermemory):
        client = _make_client()
        box = _capture(client, monkeypatch)
        existing_path = "01_References/공통/기존용어.md"
        existing_content = (
            '---\ntitle: "기존용어"\ntype: "reference"\ncreated: "2026-07-01T10:00:00"\n---\n\n'
            "# 기존용어\n\n최초 설명입니다.\n"
        )
        monkeypatch.setattr(client, "_find_ref_note_path", lambda base: existing_path)
        monkeypatch.setattr(client, "get_note", lambda path: existing_content)
        base = client.create_reference_note(
            "기존용어", "새로 알게 된 추가 설명입니다.", category="용어·기술",
            mentioned_by="두번째 회의",
        )
        assert base == "기존용어"
        assert len(box) == 1
        put_path, put_content = box[0]
        assert put_path == existing_path
        assert "최초 설명입니다." in put_content
        assert "새로 알게 된 추가 설명입니다." in put_content
        assert "두번째 회의" in put_content

    def test_existing_note_unchanged_when_description_duplicate(
        self, monkeypatch, frozen_now, no_supermemory,
    ):
        client = _make_client()
        box = _capture(client, monkeypatch)
        existing_path = "01_References/공통/중복용어.md"
        existing_content = (
            '---\ntitle: "중복용어"\ntype: "reference"\n---\n\n'
            "# 중복용어\n\n동일한 설명입니다.\n"
        )
        monkeypatch.setattr(client, "_find_ref_note_path", lambda base: existing_path)
        monkeypatch.setattr(client, "get_note", lambda path: existing_content)
        base = client.create_reference_note(
            "중복용어", "동일한 설명입니다.", category="용어·기술",
        )
        assert base == "중복용어"
        assert len(box) == 0  # 변경 없음 → put_note 호출 안 됨


class TestExpandPathTemplateProject:
    """PhysicalAI 등 두 번째 도메인 확장을 위한 {project} 토큰 지원."""

    def test_project_token_replaced(self):
        result = ob._expand_path_template(
            "{project}/01_회의_세미나/회의별/{year}", "2026-07-08", project="도메인_아카이브")
        assert result == "도메인_아카이브/01_회의_세미나/회의별/2026"

    def test_no_project_token_unaffected(self):
        """기존 (project 토큰 없는) 경로 값은 project 인자와 무관하게 그대로 동작해야 한다."""
        result = ob._expand_path_template(
            "도메인_아카이브/01_회의_세미나/회의별/{year}", "2026-07-08", project="PhysicalAI_통합아카이브")
        assert result == "도메인_아카이브/01_회의_세미나/회의별/2026"

    def test_empty_project_falls_back_to_기타(self):
        result = ob._expand_path_template("{project}/foo", "2026-07-08", project="")
        assert result == "기타/foo"


class TestMultiDomainMeetingFolder:
    """project_domains 매핑 + meetings_path의 {project} 토큰으로 두 도메인을
    같은 볼트 안에서 서로 다른 폴더에 분리 저장(실전 검증 대상)."""

    def _client(self, project):
        return ob.ObsidianClient(
            api_url="https://127.0.0.1:27124", api_key="testkey",
            meetings_path="{project}/01_회의_세미나/회의별/{year}",
            project=project,
            project_domains={"양자": "도메인_아카이브", "PhysicalAI": "PhysicalAI_통합아카이브"},
        )

    def test_default_domain_folder(self):
        client = self._client("양자")
        assert client._meeting_folder(date_str="2026-07-08") == \
            "도메인_아카이브/01_회의_세미나/회의별/2026"

    def test_second_domain_folder(self):
        client = self._client("PhysicalAI")
        assert client._meeting_folder(date_str="2026-07-08") == \
            "PhysicalAI_통합아카이브/01_회의_세미나/회의별/2026"

    def test_from_config_project_override(self, monkeypatch):
        monkeypatch.setattr(ob, "_c", lambda key, default=None: {
            "obsidian.enabled": True,
            "obsidian.api_url": "https://127.0.0.1:27124",
            "obsidian.api_key": "testkey",
            "obsidian.project": "양자",
            "obsidian.project_domains": {"양자": "도메인_아카이브", "PhysicalAI": "PhysicalAI_통합아카이브"},
        }.get(key, default))
        client = ob.ObsidianClient.from_config(project_override="PhysicalAI")
        assert client.project == "PhysicalAI"
        # override 없이 호출하면 config의 기본값("양자")을 그대로 씀
        client_default = ob.ObsidianClient.from_config()
        assert client_default.project == "양자"


class TestRefsSubfolderArchivePrefixStripped:
    """meetings_path용 project_domains 값에 아카이브 컨테이너 접두사가 있어도
    (예: "Archive/도메인_아카이브") 참조노트(01_References)는
    그 접두사 없이 도메인명만으로 저장돼야 한다(용어 노트 위치 분산 버그 수정)."""

    def _client(self, project):
        return ob.ObsidianClient(
            api_url="https://127.0.0.1:27124", api_key="testkey", project=project,
            project_domains={
                "양자": "Archive/도메인_아카이브",
                "PhysicalAI": "Archive/PhysicalAI_통합아카이브",
                "백서온톨로지": "GraphDB-온톨로지",
            },
        )

    def test_quantum_term_refs_strip_archive_prefix(self):
        client = self._client("양자")
        assert client._refs_subfolder("용어·기술") == "도메인_아카이브"

    def test_physicalai_term_refs_strip_archive_prefix(self):
        client = self._client("PhysicalAI")
        assert client._refs_subfolder("용어·기술") == "PhysicalAI_통합아카이브"

    def test_no_slash_domain_unaffected(self):
        client = self._client("백서온톨로지")
        assert client._refs_subfolder("용어·기술") == "GraphDB-온톨로지"

    def test_person_company_unaffected_by_domain(self):
        client = self._client("양자")
        assert client._refs_subfolder("인물") == "People"
        assert client._refs_subfolder("기업") == "Companies"

    def test_empty_project_falls_back_to_공통(self):
        client = self._client("")
        assert client._refs_subfolder("용어·기술") == "공통"


class TestRefsSubfolderRefDomains:
    """obsidian.ref_domains가 있으면 project_domains 기반 추론보다 우선한다 —
    이미 수동으로 만들어둔 참조노트 폴더(예: 01_References/퀀텀)가 있을 때
    새 폴더(도메인_아카이브)가 따로 생기지 않게 한다."""

    def _client(self, project, ref_domains=None):
        return ob.ObsidianClient(
            api_url="https://127.0.0.1:27124", api_key="testkey", project=project,
            project_domains={
                "양자": "Archive/도메인_아카이브",
                "백서온톨로지": "GraphDB-온톨로지",
            },
            ref_domains=ref_domains or {},
        )

    def test_ref_domains_takes_priority_over_project_domains(self):
        client = self._client("양자", ref_domains={"양자": "퀀텀"})
        assert client._refs_subfolder("용어·기술") == "퀀텀"

    def test_ref_domains_entry_for_existing_project_domain_alias(self):
        client = self._client("백서온톨로지", ref_domains={"백서온톨로지": "GraphDB-온톨로지"})
        assert client._refs_subfolder("용어·기술") == "GraphDB-온톨로지"

    def test_no_ref_domains_entry_falls_back_to_project_domains(self):
        client = self._client("양자", ref_domains={"내부온톨로지": "뭔가"})
        assert client._refs_subfolder("용어·기술") == "도메인_아카이브"

    def test_person_company_unaffected_by_ref_domains(self):
        client = self._client("양자", ref_domains={"양자": "퀀텀"})
        assert client._refs_subfolder("인물") == "People"
        assert client._refs_subfolder("기업") == "Companies"
