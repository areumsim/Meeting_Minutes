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


class TestWriteRecordingNote:
    def test_full(self, monkeypatch, frozen_now, no_supermemory):
        monkeypatch.setattr(ob, "_c", _cfg("separate"))
        client = _make_client()
        box = _capture(client, monkeypatch)
        path = client.write_recording_note(
            title="녹음 테스트", body_md="녹음 본문", doc_type="meeting", topic="녹음주제",
            attendees=["박민수"], session_dt="2026-07-02 14:00", summary_md="녹음 요약",
            actions_md="- [ ] 녹음 액션", glossary_md="- **녹음용어**: 설명",
            related_notes=["관련노트2"], source_audio="/path/to/audio.mp3",
            processed_at="2026-07-02T14:30:00", duration=125.5,
            key_points=["포인트1", "포인트2"], decisions=["결정1"],
            open_questions=["질문1"], important_claims=["주장1"],
            transcript_md="녹음 전사 내용",
        )
        golden = GOLDEN["recording_note_full"]
        assert path == golden["path"]
        assert box == golden["calls"]

    def test_minimal(self, monkeypatch, frozen_now, no_supermemory):
        monkeypatch.setattr(ob, "_c", _cfg("separate"))
        client = _make_client()
        box = _capture(client, monkeypatch)
        path = client.write_recording_note(
            title="녹음 최소", body_md="최소 본문", processed_at="2026-07-02T15:00:00",
        )
        golden = GOLDEN["recording_note_minimal"]
        assert path == golden["path"]
        assert box == golden["calls"]


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
