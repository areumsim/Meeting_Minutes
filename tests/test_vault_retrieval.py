# -*- coding: utf-8 -*-
"""vault_retrieval.py 테스트 — 도메인 확장(PhysicalAI 등) 시 관련도 가산점
마커를 config로 오버라이드할 수 있는지 확인."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meeting_minutes_app.wiki_core import vault_retrieval as vr


class TestDomainRelevanceMarkers:
    def test_default_markers_used_when_not_configured(self, monkeypatch):
        monkeypatch.setattr(vr, "_c", lambda key, default=None: default)
        markers = vr._domain_relevance_markers()
        assert "한빛" in markers
        assert "볼츠만" in markers

    def test_config_override_replaces_defaults(self, monkeypatch):
        custom = ["physicalai", "로보틱스"]
        monkeypatch.setattr(
            vr, "_c",
            lambda key, default=None: custom if key == "wiki.domain_relevance_keywords" else default,
        )
        assert list(vr._domain_relevance_markers()) == custom

    def test_note_domain_score_boosts_new_domain_marker(self, monkeypatch):
        """PhysicalAI 마커를 config에 추가하면 해당 노트도 기존 양자 노트와
        동등하게 관련도 가산점을 받아야 한다(실전 검증 목적)."""
        monkeypatch.setattr(
            vr, "_c",
            lambda key, default=None: ["로보틱스"] if key == "wiki.domain_relevance_keywords" else default,
        )
        score = vr.note_domain_score(
            "휴머노이드 로봇 연구", "로보틱스 최신 동향 정리", "로보틱스 관련 회의")
        assert score > 0

    def test_empty_config_list_falls_back_to_default(self, monkeypatch):
        monkeypatch.setattr(
            vr, "_c",
            lambda key, default=None: [] if key == "wiki.domain_relevance_keywords" else default,
        )
        markers = vr._domain_relevance_markers()
        assert "한빛" in markers  # 빈 리스트는 무시하고 기본값 유지


PROJECT_DOMAINS = {
    "양자": "Archive/도메인_아카이브",
    "PhysicalAI": "Archive/PhysicalAI_통합아카이브",
}
MEETING_CATEGORIES = {
    "양자": {"mode": "domain", "keywords": ["양자", "퀀텀", "quantum", "큐비트"]},
    "PhysicalAI": {"mode": "domain", "keywords": ["physical ai", "로보틱스", "휴머노이드"]},
    # project_domains에 없는 일반 카테고리 — 검색 스코프 후보 아님
    "팀회의": {"mode": "folder", "folder": "00_Meetings/팀회의", "keywords": ["팀회의", "정기미팅"]},
}


def _cfg(overrides):
    base = {
        "obsidian.project_domains": PROJECT_DOMAINS,
        "obsidian.meeting_categories": MEETING_CATEGORIES,
        "obsidian.refs_subdir": "01_References",
    }
    base.update(overrides)
    return lambda k, d=None: base.get(k, d)


class TestDetectQueryDomain:
    def test_quantum_query_detected(self, monkeypatch):
        monkeypatch.setattr(vr, "_c", _cfg({}))
        assert vr.detect_query_domain("양자 컴퓨팅 큐비트 오류율") == "양자"

    def test_physicalai_query_detected(self, monkeypatch):
        monkeypatch.setattr(vr, "_c", _cfg({}))
        assert vr.detect_query_domain("휴머노이드 로보틱스 최신 동향") == "PhysicalAI"

    def test_folder_category_detected(self, monkeypatch):
        """mode="folder" 카테고리(팀회의 등)도 감지 대상 — 전용 아카이브가 없는
        일반 회의도 자기 폴더로 검색 범위를 좁힐 수 있어야 한다 (도메인 오염 버그 수정)."""
        monkeypatch.setattr(vr, "_c", _cfg({}))
        assert vr.detect_query_domain("팀회의 정기미팅 안건") == "팀회의"

    def test_no_match_returns_empty(self, monkeypatch):
        monkeypatch.setattr(vr, "_c", _cfg({}))
        assert vr.detect_query_domain("완전히 무관한 잡담 내용") == ""

    def test_detection_independent_of_project_domains_mapping(self, monkeypatch):
        """감지는 meeting_categories 키워드만으로 이뤄진다 — project_domains 매핑
        여부와 무관(경로 해석은 domain_search_prefixes의 책임)."""
        monkeypatch.setattr(vr, "_c", _cfg({"obsidian.project_domains": {}}))
        assert vr.detect_query_domain("양자 컴퓨팅") == "양자"


class TestDomainSearchPrefixes:
    def test_detected_domain_returns_archive_and_refs(self, monkeypatch):
        monkeypatch.setattr(vr, "_c", _cfg({}))
        assert vr.domain_search_prefixes("양자") == \
            ["Archive/도메인_아카이브", "01_References"]

    def test_empty_project_returns_no_filter(self, monkeypatch):
        monkeypatch.setattr(vr, "_c", _cfg({}))
        assert vr.domain_search_prefixes("") == []

    def test_unmapped_project_returns_no_filter(self, monkeypatch):
        monkeypatch.setattr(vr, "_c", _cfg({}))
        assert vr.domain_search_prefixes("백서온톨로지") == []

    def test_folder_category_returns_folder_and_refs(self, monkeypatch):
        monkeypatch.setattr(vr, "_c", _cfg({}))
        assert vr.domain_search_prefixes("팀회의") == \
            ["00_Meetings/팀회의", "01_References"]

    def test_domain_without_project_mapping_returns_no_filter(self, monkeypatch):
        """detect_query_domain은 키워드만으로 "양자"를 감지할 수 있지만, project_domains에
        경로가 없으면 domain_search_prefixes는 안전하게 필터 없음(전체 검색)으로 폴백한다."""
        monkeypatch.setattr(vr, "_c", _cfg({"obsidian.project_domains": {}}))
        assert vr.domain_search_prefixes("양자") == []


class TestArchiveDomainForPath:
    def test_quantum_archive_path_detected(self, monkeypatch):
        monkeypatch.setattr(vr, "_c", _cfg({}))
        assert vr._archive_domain_for_path(
            "Archive/도메인_아카이브/01_회의_세미나/회의별/2026/260627_5.md"
        ) == "양자"

    def test_physicalai_archive_path_detected(self, monkeypatch):
        monkeypatch.setattr(vr, "_c", _cfg({}))
        assert vr._archive_domain_for_path(
            "Archive/PhysicalAI_통합아카이브/01_회의_세미나/회의별/2026/foo.md"
        ) == "PhysicalAI"

    def test_non_archive_path_returns_empty(self, monkeypatch):
        monkeypatch.setattr(vr, "_c", _cfg({}))
        assert vr._archive_domain_for_path("00_Meetings/팀회의/260708 foo.md") == ""

    def test_empty_path_returns_empty(self, monkeypatch):
        monkeypatch.setattr(vr, "_c", _cfg({}))
        assert vr._archive_domain_for_path("") == ""


class TestNoteDomainScoreCrossDomainGate:
    """실전 버그 재현: 무관한 팀 회의 스크립트에 "양자컴퓨터"라는 말이 한 번
    스쳐 지나갔다는 이유만으로 양자 아카이브의 무관한 노트가 관련 자료로
    끌려 들어와 LLM이 내용을 섞어버린 사고를 막는 하드 게이트 검증."""

    def test_domain_archived_note_rejected_without_domain_signal(self, monkeypatch):
        """단일 키워드("양자") 하나만 우연히 겹치는 것으로는 신호로 인정하지 않는다 —
        일부러 노트 content에 쿼리와 공유되는 일반 단어("사업")를 넣어, 게이트가 없었다면
        점수가 0보다 커졌을 상황에서도 정확히 0.0으로 차단되는지 확인한다."""
        monkeypatch.setattr(vr, "_c", _cfg({}))
        query = "AX 인텔리전스 레이더 대시보드 기획 회의인데 양자컴퓨터 사업도 예전에 했었다"
        score = vr.note_domain_score(
            "260627_5", "한빛 해커톤 문제 정의 사업과 양자컴퓨팅 평가 방법 논의", query,
            note_path="Archive/도메인_아카이브/01_회의_세미나/회의별/2026/260627_5.md",
        )
        assert score == 0.0

    def test_domain_archived_note_allowed_with_domain_signal(self, monkeypatch):
        monkeypatch.setattr(vr, "_c", _cfg({}))
        query = "양자 컴퓨팅 큐비트 오류율 논의"
        score = vr.note_domain_score(
            "260627_5", "한빛 해커톤 문제 정의와 양자 컴퓨팅 평가 방법 논의", query,
            note_path="Archive/도메인_아카이브/01_회의_세미나/회의별/2026/260627_5.md",
        )
        assert score > 0.0

    def test_non_domain_note_unaffected_by_gate(self, monkeypatch):
        """도메인 아카이브 밖의 노트(일반 팀회의/참조노트)는 기존 키워드 겹침 방식 그대로."""
        monkeypatch.setattr(vr, "_c", _cfg({}))
        score = vr.note_domain_score(
            "팀회의록", "대시보드 기획 논의", "대시보드 기획 회의",
            note_path="00_Meetings/팀회의/260708 foo.md",
        )
        assert score > 0.0
