"""[실전 버그] 같은 오디오를 재처리하면 회의록이 하나 더 생기던 문제의 마지막 방어선.

파일명 규칙(note_builder.meeting_note_basename)을 고쳐도 축이 둘 남는다 —
classify_meeting_route()가 LLM으로 고르는 **저장 폴더**가 실행마다 갈리고, **title**이
진입점마다 다르다(웹=사용자 입력, 폴더감시=파일 stem, 배치=stem 원형). 그래서 경로로는
같은 회의를 알아볼 수 없고 frontmatter(source_audio + session_date)로 판정해야 한다.

2026-07-30 실볼트 실측: 중복 4쌍 전부가 이 두 필드는 일치했고 파일명·폴더·title만 달랐다.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meeting_minutes_app.meeting_pipeline import publish as pb  # noqa: E402


def _note(text_meta: dict, body: str = "본문") -> str:
    lines = ["---"]
    for k, v in text_meta.items():
        lines.append(f'{k}: "{v}"')
    lines += ["---", "", body]
    return "\n".join(lines)


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """설정이 이 임시 볼트를 가리키게 한다(publish._c 와 vault_indexer._c 양쪽)."""
    from meeting_minutes_app.wiki_core import vault_indexer as vi

    v = tmp_path / "vault"
    (v / "00_Meetings" / "기타").mkdir(parents=True)
    (v / "00_Meetings" / "팀회의").mkdir(parents=True)

    def _cfg(key, default=None):
        if key in ("indexing.vault_path", "obsidian.vault_path"):
            return str(v)
        return default

    monkeypatch.setattr(pb, "_c", _cfg)
    monkeypatch.setattr(vi, "_c", _cfg)
    return v


class TestFindExistingNoteForAudio:
    def test_finds_note_in_a_different_folder(self, vault):
        """핵심 시나리오 — 폴더도 파일명도 다른데 같은 녹음이다."""
        (vault / "00_Meetings" / "팀회의" / "260708 AX 레이더 기획 회의.md").write_text(
            _note({"title": "AX 레이더 기획 회의", "session_date": "2026-07-08",
                   "source_audio": "2026-07-08 14.47_새로운녹음4.m4a"}), encoding="utf-8")

        found = pb.find_existing_note_for_audio(
            r"D:\Recordings\2026-07-08 14.47_새로운녹음4.m4a", "2026-07-08")
        assert found == "00_Meetings/팀회의/260708 AX 레이더 기획 회의.md"

    def test_matches_korean_and_iso_date_forms(self, vault):
        """session_dt 는 진입점마다 형식이 다르다(한글/ISO) — date_key 로 정규화해 비교."""
        (vault / "00_Meetings" / "기타" / "260627 회의.md").write_text(
            _note({"session_date": "2026년 06월 27일", "source_audio": "260627_5.m4a"}),
            encoding="utf-8")
        assert pb.find_existing_note_for_audio("260627_5.m4a", "2026-06-27")
        assert pb.find_existing_note_for_audio("260627_5.m4a", "2026년 06월 27일 09:00")

    def test_different_date_is_not_a_duplicate(self, vault):
        """같은 파일명의 다른 녹음(주간 반복 파일명 등)을 잘못 묶지 않는다."""
        (vault / "00_Meetings" / "기타" / "260627 회의.md").write_text(
            _note({"session_date": "2026-06-27", "source_audio": "녹음.m4a"}), encoding="utf-8")
        assert pb.find_existing_note_for_audio("녹음.m4a", "2026-07-04") == ""

    def test_transcript_note_is_not_reported(self, vault):
        """전사 노트도 source_audio 를 갖는다 — 부모를 따라가므로 판정 대상이 아니다."""
        (vault / "00_Meetings" / "기타" / "260627 회의 - 전사.md").write_text(
            _note({"type": "transcript", "session_date": "2026-06-27",
                   "source_audio": "녹음.m4a", "parent_note": "00_Meetings/기타/260627 회의.md"}),
            encoding="utf-8")
        assert pb.find_existing_note_for_audio("녹음.m4a", "2026-06-27") == ""

    def test_no_match_returns_empty(self, vault):
        (vault / "00_Meetings" / "기타" / "다른 회의.md").write_text(
            _note({"session_date": "2026-06-27", "source_audio": "다른녹음.m4a"}),
            encoding="utf-8")
        assert pb.find_existing_note_for_audio("녹음.m4a", "2026-06-27") == ""

    def test_missing_inputs_never_block(self, vault):
        """오디오나 날짜를 모르면 판정을 포기한다 — 오탐으로 발행을 멈추지 않는다."""
        assert pb.find_existing_note_for_audio("", "2026-06-27") == ""
        assert pb.find_existing_note_for_audio("녹음.m4a", "") == ""

    def test_missing_vault_never_blocks(self, monkeypatch):
        monkeypatch.setattr(pb, "_c", lambda key, default=None: "")
        assert pb.find_existing_note_for_audio("녹음.m4a", "2026-06-27") == ""

    def test_uses_the_shared_note_gate(self, vault):
        """그림자 사본·제외 폴더 판정은 iter_note_files() 하나만 쓴다(복제 금지).

        인덱서가 노트로 보지 않는 파일은 여기서도 후보가 아니어야 한다."""
        (vault / "00_Meetings" / "기타" / "발표자료.pdf.md").write_text(
            _note({"session_date": "2026-06-27", "source_audio": "녹음.m4a"}), encoding="utf-8")
        assert pb.find_existing_note_for_audio("녹음.m4a", "2026-06-27") == ""
