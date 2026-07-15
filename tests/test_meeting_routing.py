# -*- coding: utf-8 -*-
"""회의 자동 분류 라우팅(meeting_workflow.classify_meeting_route) 테스트.

obsidian.auto_route_enabled=true 일 때 제목/주제/스크립트로 도메인(양자/PhysicalAI)
또는 00_Meetings 하위 폴더(팀회의/주간보고/외부회의/기타)를 자동 결정하는 로직 검증.
각 카테고리가 자기 모드(domain/folder)를 직접 선언하는 obsidian.meeting_categories
구조(2026-07 재설계 — category_keywords/project_domains 두 딕셔너리를 손으로
동기화하다 하나를 빠뜨리는 사고를 구조적으로 없앰) 기준. 전부 오프라인 (monkeypatch)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meeting_minutes_app.meeting_pipeline import meeting_workflow as mw


MEETING_CATEGORIES = {
    "양자": {"mode": "domain", "keywords": ["양자", "퀀텀", "quantum", "큐비트"]},
    "PhysicalAI": {"mode": "domain", "keywords": ["physical ai", "로보틱스", "휴머노이드"]},
    "주간보고": {"mode": "folder", "folder": "00_Meetings/주간보고", "keywords": ["주간보고", "위클리"]},
    "팀회의": {"mode": "folder", "folder": "00_Meetings/팀회의", "keywords": ["팀회의", "정기미팅", "사내"]},
    "외부회의": {"mode": "folder", "folder": "00_Meetings/외부회의", "keywords": ["외부", "고객", "벤더"]},
    # 백서온톨로지: project_domains에는 등록 안 됨 — folder 모드가 project_domains와
    # 무관하게 동작하는지 확인하는 회귀 케이스(2026-07 발견 버그: 이 값이 project_domains에도
    # 있으면 domain 모드로 잘못 분류됐었다).
    "백서온톨로지": {"mode": "folder", "folder": "00_Meetings/백서온톨로지", "keywords": ["백서", "온톨로지"]},
}
PROJECT_DOMAINS = {
    "양자": "Archive/도메인_아카이브",
    "PhysicalAI": "Archive/PhysicalAI_통합아카이브",
}


def _cfg(overrides):
    base = {
        "obsidian.meeting_categories": MEETING_CATEGORIES,
        "obsidian.project_domains": PROJECT_DOMAINS,
        "wiki.domain_classify_llm": True,
    }
    base.update(overrides)
    return lambda k, d=None: base.get(k, d)


class TestClassifyMeetingRouteKeywords:
    def test_quantum_title_routes_to_domain(self, monkeypatch):
        monkeypatch.setattr(mw, "_c", _cfg({}))
        result = mw.classify_meeting_route("260714 양자 정기미팅", "퀀텀 얽힘 논의")
        assert result == {"mode": "domain", "project": "양자"}

    def test_physicalai_title_routes_to_domain(self, monkeypatch):
        monkeypatch.setattr(mw, "_c", _cfg({}))
        result = mw.classify_meeting_route("휴머노이드 로봇 리뷰", "")
        assert result == {"mode": "domain", "project": "PhysicalAI"}

    def test_weekly_report_routes_to_folder(self, monkeypatch):
        monkeypatch.setattr(mw, "_c", _cfg({}))
        result = mw.classify_meeting_route("2026-07-08 주간보고", "")
        assert result == {"mode": "folder", "output_folder": "00_Meetings/주간보고"}

    def test_external_meeting_routes_to_folder(self, monkeypatch):
        monkeypatch.setattr(mw, "_c", _cfg({}))
        result = mw.classify_meeting_route("고객사 미팅", "벤더 계약 논의")
        assert result == {"mode": "folder", "output_folder": "00_Meetings/외부회의"}

    def test_highest_score_wins_on_multiple_matches(self, monkeypatch):
        monkeypatch.setattr(mw, "_c", _cfg({}))
        # "팀회의" 키워드 1개, "양자" 키워드 2개 매칭 → 양자가 이겨야 함
        result = mw.classify_meeting_route("팀회의: 양자 퀀텀 컴퓨팅 논의", "")
        assert result == {"mode": "domain", "project": "양자"}

    def test_folder_mode_ignores_project_domains_absence(self, monkeypatch):
        """백서온톨로지는 project_domains에 없지만 meeting_categories에서
        mode:"folder"로 직접 선언했으므로 project_domains 조회 없이 바로
        폴더로 라우팅돼야 한다(2026-07 재설계 회귀 테스트)."""
        monkeypatch.setattr(mw, "_c", _cfg({}))
        result = mw.classify_meeting_route("백서 온톨로지 진행상황 공유", "그래프db 전환")
        assert result == {"mode": "folder", "output_folder": "00_Meetings/백서온톨로지"}


class TestClassifyMeetingRouteFallback:
    def test_no_match_no_llm_falls_back_to_기타(self, monkeypatch):
        monkeypatch.setattr(mw, "_c", _cfg({"wiki.domain_classify_llm": False}))
        result = mw.classify_meeting_route("아무 상관 없는 제목", "")
        assert result == {"mode": "folder", "output_folder": "00_Meetings/기타"}

    def test_llm_fallback_picks_category(self, monkeypatch):
        monkeypatch.setattr(mw, "_c", _cfg({}))

        class FakeLLM:
            def chat(self, system, user, temp=0.0, max_tokens=10):
                return "팀회의"

        result = mw.classify_meeting_route("애매한 제목", "", llm=FakeLLM())
        assert result == {"mode": "folder", "output_folder": "00_Meetings/팀회의"}

    def test_llm_fallback_picks_domain_category(self, monkeypatch):
        """LLM이 기존 카테고리 중 domain 모드인 것을 고르면 domain 모드로 반환돼야 함."""
        monkeypatch.setattr(mw, "_c", _cfg({}))

        class FakeLLM:
            def chat(self, system, user, temp=0.0, max_tokens=10):
                return "양자"

        result = mw.classify_meeting_route("애매한 제목", "", llm=FakeLLM())
        assert result == {"mode": "domain", "project": "양자"}

    def test_llm_failure_falls_back_to_기타(self, monkeypatch):
        monkeypatch.setattr(mw, "_c", _cfg({}))

        class FailingLLM:
            def chat(self, *a, **kw):
                raise RuntimeError("boom")

        result = mw.classify_meeting_route("애매한 제목", "", llm=FailingLLM())
        assert result == {"mode": "folder", "output_folder": "00_Meetings/기타"}

    def test_empty_meeting_categories_falls_back_to_기타(self, monkeypatch):
        monkeypatch.setattr(mw, "_c", _cfg({
            "obsidian.meeting_categories": {}, "wiki.domain_classify_llm": False}))
        result = mw.classify_meeting_route("양자 회의", "")
        assert result == {"mode": "folder", "output_folder": "00_Meetings/기타"}


class TestClassifyMeetingRouteNewCategoryDiscovery:
    """키워드 매칭 실패 시 LLM이 반복될 만한 새 주제를 발견하면 카테고리를
    자동 등록(config.json)하고 그 폴더로 라우팅해야 한다."""

    def test_llm_discovers_new_category_and_registers_it(self, monkeypatch):
        monkeypatch.setattr(mw, "_c", _cfg({}))
        registered = {}
        monkeypatch.setattr(mw._cfg, "set_nested",
                            lambda key, value, persist=True: registered.update({key: value}))

        class FakeLLM:
            def chat(self, system, user, temp=0.0, max_tokens=40):
                return "NEW: Claude TF | claude, tf, 태스크포스"

        result = mw.classify_meeting_route("클로드 TF 킥오프", "", llm=FakeLLM())
        assert result == {"mode": "folder", "output_folder": "00_Meetings/Claude TF"}
        new_entry = registered["obsidian.meeting_categories"]["Claude TF"]
        assert new_entry == {
            "mode": "folder", "folder": "00_Meetings/Claude TF",
            "keywords": ["claude", "tf", "태스크포스"],
        }

    def test_auto_register_disabled_still_routes_but_does_not_persist(self, monkeypatch):
        monkeypatch.setattr(mw, "_c", _cfg({"obsidian.auto_register_categories": False}))
        registered = {}
        monkeypatch.setattr(mw._cfg, "set_nested",
                            lambda key, value, persist=True: registered.update({key: value}))

        class FakeLLM:
            def chat(self, system, user, temp=0.0, max_tokens=40):
                return "NEW: Claude TF | claude, tf"

        result = mw.classify_meeting_route("클로드 TF 킥오프", "", llm=FakeLLM())
        assert result == {"mode": "folder", "output_folder": "00_Meetings/Claude TF"}
        assert registered == {}

    def test_malformed_new_response_falls_back_to_기타(self, monkeypatch):
        monkeypatch.setattr(mw, "_c", _cfg({}))

        class FakeLLM:
            def chat(self, system, user, temp=0.0, max_tokens=40):
                return "NEW: 키워드 없이 이름만"

        result = mw.classify_meeting_route("애매한 제목", "", llm=FakeLLM())
        assert result == {"mode": "folder", "output_folder": "00_Meetings/기타"}


class TestEnrichAndPublishRoutingFallback:
    """classify_meeting_route()가 예외를 던지면 publish.py가 output_folder=""로
    조용히 떨어져 static obsidian.project(예: "양자") 경로에 섞이지 않고,
    명시적으로 00_Meetings/기타 output_folder를 써야 한다(발견된 HIGH 버그 수정 검증)."""

    def test_classify_exception_routes_to_기타_not_default_project(self, monkeypatch):
        from meeting_minutes_app.meeting_pipeline import publish as pub
        from meeting_minutes_app.meeting_pipeline import enrichment as enr
        from meeting_minutes_app.wiki_core import obsidian as ob_mod

        monkeypatch.setattr(pub, "_c", lambda k, d=None: {
            "obsidian.auto_route_enabled": True,
        }.get(k, d))

        def boom(*a, **kw):
            raise RuntimeError("classify boom")
        monkeypatch.setattr(mw, "classify_meeting_route", boom)

        monkeypatch.setattr(ob_mod.ObsidianClient, "from_config",
                            classmethod(lambda cls, project_override="": None))
        monkeypatch.setattr(enr, "enrich", lambda *a, **kw: {
            "glossary_md": "", "related_notes": [], "sources": []})

        result = pub.enrich_and_publish(
            title="애매한 제목", doc_type="meeting", minutes_md="본문", llm=None,
        )
        assert result["auto_route"] == {"mode": "folder", "output_folder": "00_Meetings/기타"}


class TestClassifyDocTypeLlm:
    """전사 내용 기반 doc_type(meeting/seminar/lecture) LLM 보완 분류.
    파일명에 유형 키워드가 없는 자동 녹음(watcher 경로)에서 내용과 무관하게
    항상 "meeting"으로 처리되던 공백을 메우는 함수."""

    def test_no_llm_returns_empty(self):
        assert mw.classify_doc_type_llm("아무 전사 내용", None) == ""

    def test_empty_text_returns_empty(self):
        class FakeLLM:
            def chat(self, *a, **kw):
                raise AssertionError("빈 텍스트면 LLM 호출 자체를 하면 안 됨")
        assert mw.classify_doc_type_llm("", FakeLLM()) == ""
        assert mw.classify_doc_type_llm("   ", FakeLLM()) == ""

    def test_llm_detects_seminar(self):
        class FakeLLM:
            def chat(self, system, user, temp=0.0, max_tokens=5):
                return "seminar"
        assert mw.classify_doc_type_llm("오늘 발표에서는...", FakeLLM()) == "seminar"

    def test_llm_detects_lecture(self):
        class FakeLLM:
            def chat(self, system, user, temp=0.0, max_tokens=5):
                return "Lecture"
        assert mw.classify_doc_type_llm("오늘 강의 내용은...", FakeLLM()) == "lecture"

    def test_llm_unrecognized_output_returns_empty(self):
        class FakeLLM:
            def chat(self, system, user, temp=0.0, max_tokens=5):
                return "모르겠음"
        assert mw.classify_doc_type_llm("애매한 내용", FakeLLM()) == ""

    def test_llm_failure_returns_empty(self):
        class FailingLLM:
            def chat(self, *a, **kw):
                raise RuntimeError("boom")
        assert mw.classify_doc_type_llm("아무 전사 내용", FailingLLM()) == ""
