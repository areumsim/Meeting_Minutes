"""
wiki_ask.py — 노트 폴더(.md) 기반 LLM Q&A (사실 검증 + 출처 인용 강제)
=========================================================================
로컬 볼트 인덱스(.md 폴더)로 관련 노트를 수집한 뒤 LLM이 그 컨텍스트만으로
답변하도록 강제한다. Obsidian 앱/REST는 선택 — 연결돼 있으면 full-text 검색 결과를
추가로 병합하지만, 없어도 폴더 인덱스만으로 동작한다.

- 출처 인용 강제: [출처: [[노트 제목]]] 형식
- 근거 부족 시: "확인 불가" 마커 사용
- 충돌 정보: "⚠️ 충돌" 마커 사용

단독 실행:
    python run_meeting.py wiki-ask --question "지난 회의에서 결정된 사항은?"
    python run_meeting.py wiki-ask --question "양자컴퓨팅 현황" --max-notes 7
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from meeting_minutes_app.common.console import force_utf8_console
force_utf8_console()

try:
    from meeting_minutes_app.common import config_loader as _cfg
    _cfg_ok = True
except ImportError:
    _cfg = None  # type: ignore
    _cfg_ok = False


def _c(key: str, default: Any = None) -> Any:
    return _cfg.get(key, default) if _cfg_ok else default


_UNVERIFIED_MARKER = "확인 불가"
_CONFLICT_MARKER = "⚠️ 충돌"

_SYSTEM_PROMPT_TEMPLATE = """당신은 Obsidian 볼트의 지식을 기반으로 정확하게 답변하는 개인 LLM Wiki 어시스턴트입니다.

오늘 날짜는 {today} 입니다. 아래 각 노트 블록 제목 옆의 "(작성일: YYYY-MM-DD)"이 그 노트의 날짜입니다.
"최근·최신·요즘·지난·언제·며칠 전" 같은 시점 표현은 오늘 날짜와 각 노트의 작성일을 비교해 판단하고,
날짜를 물으면 해당 노트의 작성일을 근거로 명시하세요. 작성일이 없는 노트는 날짜 추정을 하지 마세요.

규칙:
1. 아래 노트 컨텍스트(제목·헤딩·본문 전부)를 근거로 답하세요. 컨텍스트에 문장으로 똑같이
   적혀 있지 않더라도, 노트에서 **직접 도출·추론 가능한 인물/기관/용어/사실은 적극적으로 답하세요.**
   특히 블록 제목 "### [[노트#헤딩]]"의 헤딩은 인용 대상일 뿐 아니라 **그 자체가 사실 정보**입니다
   (예: 헤딩에 사람 이름·소속·그룹명이 들어 있으면 그것이 곧 답의 근거가 됩니다). 질문의 표현이
   노트와 달라도(예: "양컴"="양자컴퓨팅") 합리적으로 연결해 답하세요. 외부 지식으로 지어내지는 마세요.
{citation_rule}
3. 노트에서 합리적으로 도출할 수 있으면 답을 제시하고, **근거가 전혀 없을 때만** "{unverified}"라고
   명시하세요. 컨텍스트에 관련 단서가 있는데 표현이 다르다는 이유로 "{unverified}"를 쓰지 마세요.
4. 두 노트가 서로 상충하는 정보를 담고 있으면 "{conflict}"을 표시하고 두 입장을 모두 제시하세요.
5. 답변은 한국어로 작성하고, 반드시 아래 6개 섹션 구조를 그대로 사용하세요 (내용 없는 섹션도 헤딩은 유지하고 "해당 없음"이라고 쓰세요):

## 요약 답변
(1~2문장 핵심 결론)

## 상세 답변
(근거와 함께 상세 설명, [출처: [[...]]] 인용 포함)

## 근거
(실제로 답변에 사용한 노트/섹션을 "- [[노트#헤딩]]" 또는 "- [[노트]]" 형식 불릿으로 나열)

## 확실한 내용
(노트로 명확히 확인된 사실만)

## 불확실한 내용
(노트에 없거나 상충하는 부분 — "{unverified}"/"{conflict}" 사용)

## 다음 액션 또는 업데이트 후보
(추가로 확인이 필요하거나 Wiki에 반영할 만한 항목. 없으면 "없음")

컨텍스트 노트:
{context}
"""

_ONLINE_SUPPLEMENT_PROMPT = """위 노트 컨텍스트 외에도 웹 검색으로 다음을 보완해도 됩니다.
단, 웹 검색 결과에서 나온 내용은 [웹 출처: URL] 형식으로 표시하고,
볼트 노트 내용과 구분하세요."""

_PLAN_SYSTEM = """사용자의 위키 질문을 검색 계획(JSON)으로 변환하세요. 오늘 날짜는 {today} 입니다.
아래 JSON 객체 '하나만' 출력하세요(설명·마크다운 금지):
{{"intent":"recency|aggregate|lookup|general","date_from":"","date_to":"","types":[],"top_k":0,"entities":[]}}

필드 규칙:
- intent: "가장 최근/최신/마지막" 한 건 → recency. "N개/목록/전부/6월 회의들"처럼 여러 건 나열 →
  aggregate. 특정 대상 조회 → lookup. 그 외 → general.
- date_from/date_to: "지난 달/이번 주/올해/6월/최근 3개월" 같은 표현을 오늘 날짜 기준 실제
  범위(YYYY-MM-DD)로 환산해 채우세요. 기간 언급이 없으면 둘 다 "".
- types: 회의→"meeting", 세미나→"seminar", 강의→"lecture". 특정하지 않으면 빈 배열.
- top_k: 사용자가 명시한 개수(예 "3개"→3). 없으면 0.
- entities: 노트 검색에 쓸 핵심 명사 1~6개(조사·일반어 '회의/최근/관련' 등은 제외).
"""


class WikiQA:
    """Vault 기반 LLM Q&A."""

    def __init__(self, llm=None, obs=None, indexer=None):
        self._llm = llm
        self._obs = obs
        self._indexer = indexer
        self._unverified = _c("wiki.unverified_marker", _UNVERIFIED_MARKER)
        self._conflict = _c("wiki.conflict_marker", _CONFLICT_MARKER)
        self._max_notes = int(_c("wiki.max_context_notes", 10))
        self._max_chars = int(_c("wiki.context_max_chars", 2000))
        self._online = bool(_c("wiki.online_search_enabled", False))

    def _ensure_clients(self) -> None:
        if self._llm is None:
            from meeting_minutes_app.common.llm_client import LLMClient
            self._llm = LLMClient(preferred=_c("models.llm", "gpt") or "gpt")
        if self._obs is None:
            try:
                from meeting_minutes_app.wiki_core.obsidian import ObsidianClient
                obs = ObsidianClient.from_config()
                if obs and obs.ping():
                    self._obs = obs
            except Exception:
                pass
        if self._indexer is None:
            try:
                from meeting_minutes_app.wiki_core.vault_indexer import VaultIndexer
                idx = VaultIndexer.from_config()
                if idx and idx.load():
                    self._indexer = idx
            except Exception:
                pass

    def ask(self, question: str, max_context_notes: int = 0) -> Dict[str, Any]:
        """질문에 답변한다.

        Returns:
            {"answer": str, "sources": list, "has_conflict": bool, "unverified": bool}
        """
        self._ensure_clients()
        limit = max_context_notes or self._max_notes

        # 질문을 검색 계획(의도/기간/유형/개수/핵심어)으로 변환 — 실패·비활성 시 휴리스틱 폴백.
        plan = self._plan_query(question)

        # 컨텍스트 수집
        context_notes = self._gather_context(question, limit, plan)

        if not context_notes:
            # 노트 폴더는 연결됐지만 검색 인덱스가 아직 없으면(=폴더-only 사용자가
            # 최초 실행 직후, 자동 인덱스 생성이 아직 안 끝났거나 실패한 경우) 무성의한
            # "못 찾음" 대신 무엇을 하면 되는지 명확히 안내한다. Obsidian 앱/REST는 불필요.
            vault_set = bool(_c("indexing.vault_path") or _c("obsidian.vault_path"))
            if vault_set and self._indexer is None:
                return {
                    "answer": ("노트 폴더는 연결됐지만 검색 인덱스가 아직 준비되지 않았습니다.\n"
                               "잠시 후(자동 생성 중일 수 있음) 다시 질문하거나, [설정] → "
                               "**검색 인덱스·그래프 재빌드**를 한 번 눌러 인덱스를 만든 뒤 다시 시도하세요."),
                    "sources": [],
                    "has_conflict": False,
                    "unverified": True,
                }
            msg = (f"{self._unverified}: 관련 노트를 찾지 못했습니다."
                   if vault_set
                   else f"{self._unverified}: 노트 폴더가 연결되지 않았습니다. "
                        "[설정]에서 노트 폴더(.md)를 지정하면 그 기록을 근거로 답변합니다.")
            return {
                "answer": msg,
                "sources": [],
                "has_conflict": False,
                "unverified": True,
            }

        # 프롬프트 구성
        system_prompt, user_prompt = self._build_prompt(question, context_notes)

        # LLM 호출
        try:
            answer = self._llm.chat(system_prompt, user_prompt, temp=0.1)
        except Exception as e:
            return {
                "answer": f"LLM 오류: {e}",
                "sources": context_notes,
                "has_conflict": False,
                "unverified": True,
            }

        # 마커 감지
        has_conflict, unverified = self._detect_markers(answer)

        # 온라인 보완 (설정된 경우)
        if self._online and unverified:
            answer = self._supplement_online(question, answer, context_notes)

        # 인용 근거 검증(기본 켜짐, wiki.verify_citations) — 답변이 [출처: [[X]]]로 든
        # 노트가 실제로 제공된 컨텍스트에 있는지 확인한다. 컨텍스트에 없는 노트를 근거로
        # 들면(환각 인용) 본문은 건드리지 않고 말미에 검증 경고만 덧붙인다(추가 LLM 호출 없음).
        citation_issues: List[str] = []
        if _c("wiki.verify_citations", True):
            citation_issues = _verify_citations(answer, context_notes)
            if citation_issues:
                unverified = True
                answer += (
                    "\n\n---\n⚠️ 인용 검증: 다음 출처는 제공된 컨텍스트 노트에 없어 근거를 "
                    "확인할 수 없습니다 — "
                    + ", ".join(f"[[{s}]]" for s in citation_issues)
                    + ". 해당 주장은 노트로 뒷받침되지 않았을 수 있습니다."
                )

        return {
            "answer": answer,
            "sources": context_notes,
            "has_conflict": has_conflict,
            "unverified": unverified,
            "citation_issues": citation_issues,
        }

    def _plan_query(self, question: str) -> Dict[str, Any]:
        """질문 → 검색 계획 dict. LLM 쿼리 플래너(기본 켜짐, wiki.query_planner_enabled).

        반환: {intent, date_from, date_to, types, top_k, entities}
        - intent: recency(가장 최근) | aggregate(여러 건·기간) | lookup(특정 대상) | general
        - 실패·비활성 시 정규식 휴리스틱으로 폴백(기존 동작과 동일한 안전값).

        정규식 recency 감지만으로는 "6월 회의들"(기간)·"3개"(개수)·"지난 달"(상대기간)을
        이해하지 못한다. LLM으로 이런 시점/개수/유형 의도를 구조화해 검색을 유도한다.
        """
        from datetime import datetime as _dt
        # 휴리스틱 폴백(플래너 꺼짐/실패 시) — 기존 recency·meeting 감지 재사용.
        fallback = {
            "intent": "recency" if _is_recency_query(question) else "general",
            "date_from": "", "date_to": "",
            "types": (["meeting", "seminar", "lecture"]
                      if _MEETING_PAT.search(question or "") else []),
            "top_k": 0,
            "entities": _keyword_terms(question),
        }
        if not bool(_c("wiki.query_planner_enabled", True)):
            return fallback
        try:
            from meeting_minutes_app.meeting_pipeline.json_utils import parse_json_loose
            system = _PLAN_SYSTEM.format(today=_dt.now().strftime("%Y-%m-%d"))
            raw = self._llm.chat(system, question, temp=0.0)
            data = parse_json_loose(raw, expect="dict")
            if not isinstance(data, dict):
                return fallback
            intent = str(data.get("intent") or "").strip().lower()
            if intent not in ("recency", "aggregate", "lookup", "general"):
                intent = fallback["intent"]
            types = [str(t).strip().lower() for t in (data.get("types") or [])
                     if str(t).strip()]
            ents = [str(e).strip() for e in (data.get("entities") or []) if str(e).strip()]
            try:
                top_k = int(data.get("top_k") or 0)
            except (TypeError, ValueError):
                top_k = 0
            _dpat = re.compile(r"^\d{4}-\d{2}-\d{2}$")
            df = str(data.get("date_from") or "").strip()
            dt = str(data.get("date_to") or "").strip()
            return {
                "intent": intent,
                "date_from": df if _dpat.match(df) else "",
                "date_to": dt if _dpat.match(dt) else "",
                "types": types,
                "top_k": max(0, top_k),
                "entities": ents or fallback["entities"],
            }
        except Exception:
            return fallback

    def _gather_context(self, question: str, max_notes: int,
                        plan: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """인덱서 + Obsidian search 로 관련 노트/섹션을 수집한다.

        plan(검색 계획)이 주어지면 시점/기간/유형/개수 의도를 검색에 반영한다.
        """
        plan = plan or {}
        seen_titles: set = set()
        seen_sections: set = set()
        results: List[Dict] = []

        # 검색에만 config.analysis.entity_aliases(줄임말/구어체→정식 명칭)를 적용한다.
        # 예: "양컴"(2글자) vs "양자컴"(3글자)는 bigram 토큰이 하나도 안 겹쳐서
        # TF-IDF가 완전히 놓치는데, 노트 본문은 정식 명칭만 쓰는 경우가 많다 —
        # meeting_workflow의 STT 오인식 보정과 같은 별칭 테이블을 재사용한다.
        # LLM 프롬프트(_build_prompt)에는 원문 question을 그대로 넘겨 사용자가
        # 실제로 쓴 표현이 보존되게 한다.
        try:
            from meeting_minutes_app.wiki_core.vault_retrieval import normalize_domain_text
            search_question = normalize_domain_text(question)
        except Exception:
            search_question = question
        # 랭킹·발췌용 키워드는 쿼리 플래너가 뽑은 핵심어(entities)를 우선 사용한다 —
        # 일반어가 걸러진 명사 위주라 관련도 가산·질문어 중심 발췌가 더 정확하다.
        terms = [t for t in (plan.get("entities") or []) if t] or _keyword_terms(search_question)

        # 질문에서 도메인(양자/PhysicalAI 등)이 감지되면 검색 범위를 그 아카이브 +
        # 공유 참조노트로 좁힌다 — 감지 안 되면 빈 리스트(=볼트 전체 검색, 기존 동작).
        path_prefixes: List[str] = []
        try:
            from meeting_minutes_app.wiki_core.vault_retrieval import (
                detect_query_domain, domain_search_prefixes,
            )
            path_prefixes = domain_search_prefixes(detect_query_domain(search_question))
        except Exception:
            path_prefixes = []

        # Layer 0: 섹션 단위 검색 (section_index_enabled=true 일 때 우선 — 근거 정확도 향상)
        if self._indexer and _c("wiki_knowledge.section_index_enabled", True):
            try:
                sec_hits = self._indexer.find_related_sections(
                    search_question, limit=max_notes * 2, path_prefixes=path_prefixes)
            except Exception:
                sec_hits = []
            _sec_max = max((h.get("score", 0) or 0) for h in sec_hits) if sec_hits else 0
            for i, hit in enumerate(sec_hits):
                title = hit.get("note_title", "")
                heading = hit.get("heading", "")
                norm = _norm_title(title)
                if not norm or not heading:
                    continue
                sec_key = (norm, _norm_title(heading))
                if sec_key in seen_sections:
                    continue
                content = self._indexer.get_section_content(hit.get("note_path", ""), heading)
                if not content:
                    continue
                seen_sections.add(sec_key)
                rank_score = _context_rank_score(
                    title=f"{title} {heading}",
                    path=hit.get("note_path", ""),
                    snippet=hit.get("snippet", ""),
                    terms=terms,
                    source="index",
                    order=i,
                    relevance=(hit.get("score", 0) / _sec_max) if _sec_max else 0.0,
                )
                # 헤딩은 정답(인물/그룹명 등)을 담는 경우가 많은데 본문(content)에는 헤딩 줄이
                # 빠져 있다. 헤딩을 본문 앞에 붙여 LLM이 인용링크가 아닌 '사실 텍스트'로 보게 한다.
                sec_body = f"# {heading}\n{content}" if heading else content
                results.append({
                    "title": title,
                    "heading": heading,
                    "path": hit.get("note_path", ""),
                    "snippet": hit.get("snippet", ""),
                    "content": _truncate_note(sec_body, self._max_chars, terms=terms),
                    "score": hit.get("score", 0),
                    "rank_score": rank_score,
                    "date": hit.get("date", ""),
                    "source": "index_section",
                })
            # 섹션으로 이미 확보한 노트는 whole-note 레이어에서 제외 (섹션 근거 우선)
            seen_titles.update(norm for norm, _h in seen_sections)

        # Layer 1: 로컬 TF-IDF 인덱스
        if self._indexer:
            idx_results = self._indexer.search(
                search_question, limit=max_notes * 2, path_prefixes=path_prefixes)
            # 관련도 하한(P1-2): TF-IDF 점수도 낮고 의미유사도도 없는 '노이즈' 노트는
            # 컨텍스트에서 제외한다 — 무관 노트가 들어가면 LLM이 억지로 엮어 환각한다
            # (예: requirements.txt를 회의로 오인). min_context_score=0(기본)이면 필터 비활성.
            #
            # 의미매치 판정은 **인덱서가 이미 했다**. `cosine` 필드는 vault_indexer 의
            # z 컷(`_semantic_cut`)을 통과한 노트에만 붙으므로, 여기서 코사인을 다시
            # 절대값으로 재판정하면 규칙이 두 곳으로 갈린다. 예전엔 여기서
            # `embedding_min_cosine`(0.25)로 다시 봤는데, 실측에서 그 값은 무작위 노트
            # 쌍의 78.5%를 통과시키는 값이었다(vault_indexer._SEMANTIC_MIN_Z 주석 참고).
            _min_score = float(_c("wiki.min_context_score", 0.0) or 0.0)
            _idx_max = max((r.get("score", 0) or 0) for r in idx_results) if idx_results else 0
            for r in idx_results:
                if (_min_score > 0 and (r.get("score", 0) or 0) < _min_score
                        and not (r.get("cosine", 0.0) or 0.0) > 0.0):
                    continue
                title = r.get("title", "")
                norm = _norm_title(title)
                if not norm or _seen_equiv(norm, seen_titles):
                    continue
                seen_titles.add(norm)
                # 노트 전체 내용 읽기
                content = self._indexer.get_note_content(r["path"]) or ""
                # 하이브리드 관련도(P1-1): 의미유사도(cosine) 우선, 없으면 TF-IDF 정규화값.
                _rel = (r.get("cosine", 0.0) or 0.0) or (
                    (r.get("score", 0) / _idx_max) if _idx_max else 0.0)
                rank_score = _context_rank_score(
                    title=title,
                    path=r.get("path", ""),
                    snippet=r.get("snippet", ""),
                    terms=terms,
                    source="index",
                    order=len(results),
                    relevance=_rel,
                )
                results.append({
                    "title": title,
                    "heading": None,
                    "path": r.get("path", ""),
                    "snippet": r.get("snippet", ""),
                    "content": _truncate_note(content, self._max_chars, terms=terms),
                    "score": r.get("score", 0),
                    "rank_score": rank_score,
                    "date": r.get("date", ""),
                    "source": "index",
                })

        # Layer 2: Obsidian full-text search (항상 병합; 로컬 TF-IDF 오탐 보정)
        if self._obs:
            try:
                obs_results: List[Dict[str, Any]] = []
                queries = [search_question]
                if terms:
                    queries.append(" ".join(terms[:8]))
                    for i in range(0, min(len(terms), 8), 3):
                        q = " ".join(terms[i:i + 3])
                        if q:
                            queries.append(q)
                seen_paths = set()
                for q in dict.fromkeys(x.strip() for x in queries if x and x.strip()):
                    for hit in self._obs.search_simple(
                        q[:300], context_length=200, limit=max_notes * 2
                    ) or []:
                        path_key = hit.get("filename", "")
                        if path_key and path_key not in seen_paths:
                            seen_paths.add(path_key)
                            obs_results.append(hit)
                from meeting_minutes_app.wiki_core.vault_indexer import (
                    _is_indexable_note, default_exclude_dirs,
                )
                _ex_dirs = default_exclude_dirs()
                for i, r in enumerate(obs_results):
                    fname = r.get("filename", "")
                    # 비-.md / 그림자 사본(*.txt.md 등)은 회의로 오인용되므로 제외(인덱서와 동일 기준).
                    # exclude_dirs 도 함께 넘긴다 — 안 넘기던 동안엔 이 REST 레이어만
                    # 제외 폴더(바이너리 원본 아카이브)의 노트를 근거로 인용할 수 있었다.
                    _fn = str(fname).replace("\\", "/")
                    if not _fn.lower().endswith(".md") or not _is_indexable_note(_fn, _ex_dirs):
                        continue
                    title = Path(fname.replace("\\", "/")).stem
                    norm = _norm_title(title)
                    if not norm or _seen_equiv(norm, seen_titles):
                        continue
                    seen_titles.add(norm)
                    # REST API로 전체 내용 읽기
                    content = self._obs.get_note(fname) or ""
                    snippet = _obs_matches_snippet(r.get("matches", []))[:200]
                    rank_score = _context_rank_score(
                        title=title,
                        path=fname,
                        snippet=snippet,
                        terms=terms,
                        source="obsidian",
                        order=i,
                    )
                    results.append({
                        "title": title,
                        "heading": None,
                        "path": fname,
                        "snippet": snippet,
                        "content": _truncate_note(content, self._max_chars, terms=terms),
                        "score": r.get("score", 0),
                        "rank_score": rank_score,
                        "date": _fname_iso(fname),
                        "source": "obsidian",
                    })
            except Exception:
                pass

        # Layer 3: 시점/기간 질의 — 날짜 기준 최신 노트 직접 주입.
        # 키워드 관련도만으로는 '가장 최근 회의'가 후보 풀에 아예 안 들어오는 문제가 있다
        # (일반어 '최근/회의'가 정작 최신 회의 노트 본문과 겹치지 않음). 관련도와 무관하게
        # 인덱스에서 날짜 내림차순 최신 노트를 뽑아 합류시켜, 아래 recency 정렬이 이들을
        # 상위로 올린다. 플래너가 유형(types)/개수(top_k)를 주면 그에 맞춰 좁히고 넉넉히 뽑는다.
        _intent = str(plan.get("intent") or "")
        _time_query = _intent in ("recency", "aggregate") or _is_recency_query(question)
        if self._indexer and _time_query:
            try:
                _ptypes = [t for t in (plan.get("types") or []) if t]
                _types = tuple(_ptypes) if _ptypes else (
                    ("meeting", "seminar", "lecture") if _MEETING_PAT.search(question) else None)
                _want = max(max_notes, int(plan.get("top_k") or 0) or 0)
                for r in self._indexer.recent_notes(limit=_want, types=_types):
                    title = r.get("title", "")
                    norm = _norm_title(title)
                    if not norm or _seen_equiv(norm, seen_titles):
                        continue
                    seen_titles.add(norm)
                    content = self._indexer.get_note_content(r.get("path", "")) or ""
                    results.append({
                        "title": title,
                        "heading": None,
                        "path": r.get("path", ""),
                        "snippet": r.get("snippet", ""),
                        "content": _truncate_note(content, self._max_chars, terms=terms),
                        "score": r.get("score", 0),
                        "rank_score": 0.0,  # 후보 유지용 최소값 — 최신 정렬이 순위를 정함
                        "date": r.get("date", ""),
                        "source": "recent",
                    })
            except Exception:
                pass

        # 기간 필터(플래너 date_from/date_to) — 명시된 기간을 벗어난 노트는 제외한다.
        # 기간 질의에서는 날짜 없는 노트도 노이즈이므로 함께 제외. 단, 전부 걸러지면
        # 빈 컨텍스트를 피하려 원본을 유지한다(과필터 방지).
        df, dt = str(plan.get("date_from") or ""), str(plan.get("date_to") or "")
        if df or dt:
            def _in_range(x: Dict) -> bool:
                d = _recency_date_key(x)
                if not d:
                    return False
                if df and d < df:
                    return False
                if dt and d > dt:
                    return False
                return True
            _ranged = [r for r in results if _in_range(r)]
            if _ranged:
                results = _ranged

        # 회의 시점질의(예: "가장 최근 회의 3개")는 최종적으로 회의류 노트만 남긴다 —
        # 비회의 파일이 '작성일'만으로 상위에 오르는 것을 차단(근본원인 B/G). 게이트는
        # 회의 의도(_MEETING_PAT 또는 플래너 types가 회의류)일 때로 한정해 'aggregate'(비회의)
        # 시점질의는 건드리지 않는다. 필터 결과가 비면 원본을 유지(과필터 방지).
        _meeting_scope = bool(_MEETING_PAT.search(question)) or bool(
            {str(t).lower() for t in (plan.get("types") or [])}
            & {"meeting", "seminar", "lecture"})
        if _time_query and _meeting_scope:
            _mdirs = _c("indexing.meeting_dirs",
                        ["00_Meetings", "회의", "Meetings", "회의별"]) or []
            _filtered = [r for r in results if _is_meeting_note(r, _mdirs)]
            if _filtered:
                results = _filtered

        # raw score는 index/REST 간 스케일이 달라 자체 랭킹으로 정렬
        results.sort(key=lambda x: -x.get("rank_score", 0))
        # 시점/기간 질의는 관련 후보 중 최신 노트가 컨텍스트에 포함되도록 작성일 내림차순을
        # 1순위로 재정렬한다(동점은 관련도 유지).
        if _time_query:
            results.sort(key=lambda x: (_recency_date_key(x), x.get("rank_score", 0)),
                         reverse=True)
        return results[:max_notes]

    def _build_prompt(
        self, question: str, context_notes: List[Dict]
    ) -> Tuple[str, str]:
        """system + user 프롬프트를 구성한다."""
        # 컨텍스트 블록 구성 — 블록 제목 옆에 노트 작성일을 명시해 LLM이 시점/최근 판단에 쓰게 한다.
        context_blocks = []
        for note in context_notes:
            anchor = _note_anchor(note)
            date = str(note.get("date") or "").strip()
            date_tag = f" (작성일: {date})" if date else ""
            content = note.get("content") or note.get("snippet") or ""
            block = f"### [[{anchor}]]{date_tag}\n{content.strip()}"
            context_blocks.append(block)
        context_str = "\n\n".join(context_blocks)

        # wiki.citation_required(기본 true) 로 인용 규칙(규칙 2)을 실제로 분기한다.
        # (과거엔 템플릿에 없는 문구를 replace 하려 해 토글이 아무 효과도 없었다 — 죽은 코드.)
        if _c("wiki.citation_required", True):
            citation_rule = ("2. 답변의 각 주장에는 가능한 한 [출처: [[노트 제목]]] 또는 "
                             "[출처: [[노트 제목#헤딩]]] 형식으로 근거 노트를 인용하세요.")
        else:
            citation_rule = "2. 필요하면 [출처: [[노트 제목]]] 형식으로 인용해도 됩니다(필수 아님)."

        from datetime import datetime as _dt
        system = _SYSTEM_PROMPT_TEMPLATE.format(
            unverified=self._unverified,
            conflict=self._conflict,
            context=context_str,
            today=_dt.now().strftime("%Y-%m-%d"),
            citation_rule=citation_rule,
        )
        if self._online:
            system += "\n" + _ONLINE_SUPPLEMENT_PROMPT

        user = question
        return system, user

    def _detect_markers(self, answer: str) -> Tuple[bool, bool]:
        has_conflict = self._conflict in answer
        unverified = self._unverified in answer
        return has_conflict, unverified

    def _supplement_online(
        self, question: str, answer: str, sources: List[Dict]
    ) -> str:
        """볼트에 없는 내용을 웹 검색으로 보완한다.

        이 함수는 볼트 답변이 이미 '{unverified}'로 판정된 경우에만 호출된다(ask 참고).
        즉 볼트로는 답을 못 찾은 상태이므로, 기술어 휴리스틱으로 막지 않고 웹 검색을 실행한다
        (한국어/소문자 기술 질문이 '비기술'로 오분류돼 폴백이 스킵되던 문제 수정). trigger='never'만 차단."""
        trigger = _c("wiki.online_search_trigger", "technical")
        if trigger == "never":
            return answer
        try:
            web_result = self._llm.web_research(question)
            web_text = web_result.get("text", "")
            web_sources = web_result.get("sources", [])
            if web_text:
                supplement = "\n\n---\n**웹 검색 보완:**\n" + web_text[:1000]
                if web_sources:
                    urls = "\n".join(
                        f"- [{s.get('title', s.get('url', ''))}]({s.get('url', '')})"
                        for s in web_sources[:3]
                    )
                    supplement += f"\n\n[웹 출처]\n{urls}"
                return answer + supplement
        except Exception:
            pass
        return answer


_RECENCY_PAT = re.compile(
    r"최근|최신|요즘|근래|지난|언제|며칠|얼마\s*전|latest|recent|when|last\s+meeting",
    re.IGNORECASE,
)
_MEETING_PAT = re.compile(r"회의|미팅|세미나|강의|meeting|seminar", re.IGNORECASE)


def _is_recency_query(text: str) -> bool:
    """질문이 시점/최근성을 묻는지 여부 — 맞으면 컨텍스트를 작성일 내림차순 우선 정렬한다."""
    return bool(_RECENCY_PAT.search(str(text or "")))


def _is_meeting_note(note: Dict[str, Any], meeting_dirs: Sequence[str] = ()) -> bool:
    """노트가 '회의류'(회의/미팅/세미나/강의)인지 판정 — 시점질의 최종 필터용.

    실제 회의 노트도 frontmatter type이 비어 있는 경우가 많아, type 외에 회의 폴더 경로·
    제목/파일명의 회의 키워드로도 인정한다. 이로써 비회의 파일(requirements.txt 등)이
    '작성일'만으로 '가장 최근 회의' 상위에 오르는 것을 차단한다."""
    t = str(note.get("type") or "").lower()
    if t in ("meeting", "seminar", "lecture"):
        return True
    path = str(note.get("path") or "").replace("\\", "/")
    for m in meeting_dirs or ():
        if m and str(m).replace("\\", "/") in path:
            return True
    base = Path(path).name
    if _MEETING_PAT.search(str(note.get("title") or "")) or _MEETING_PAT.search(base):
        return True
    return False


def _recency_date_key(note: Dict[str, Any]) -> str:
    """작성일 정렬용 정규화 키(YYYY-MM-DD). 형식이 섞여도(ISO/한글 '2026년 06월 29일'/
    슬래시·닷/컴팩트/시각 포함) 연·월·일을 뽑아 사전식 비교가 시간순과 일치하게 한다.
    못 뽑으면 앞 10자를 '-'로 통일(최후 폴백), 빈 값이면 ''."""
    d = str(note.get("date") or "").strip().strip('"')
    if not d:
        return ""
    try:
        from meeting_minutes_app.meeting_pipeline.date_utils import normalize_iso_date
        iso = normalize_iso_date(d)
        if iso:
            return iso
    except Exception:
        pass
    return d[:10].replace("/", "-").replace(".", "-")


def _fname_iso(path: str) -> str:
    """파일명/경로에서 YYYY-MM-DD 추출(Obsidian REST 결과처럼 인덱스 날짜가 없을 때 폴백)."""
    try:
        from meeting_minutes_app.meeting_pipeline.date_utils import parse_iso_date_from_text
        return parse_iso_date_from_text(path)
    except Exception:
        return ""


_CITATION_RE = re.compile(r"\[출처:\s*\[\[([^\]]+?)\]\]\]")


def _verify_citations(answer: str, context_notes: List[Dict[str, Any]]) -> List[str]:
    """답변의 [출처: [[제목(#헤딩)]]] 인용이 실제 컨텍스트 노트를 가리키는지 검증한다.

    컨텍스트에 없는 노트를 근거로 든 인용(환각 인용)만 목록으로 반환한다. 답변 본문은
    바꾸지 않는다(호출부에서 경고만 덧붙임). 오탐(정당한 인용을 거짓으로 표기)을 막기 위해
    매칭은 관대하게 한다 — 정규화 제목이 컨텍스트 제목과 완전일치하거나 서로 부분 포함이면
    근거 있음으로 본다. 검증 대상은 명시적 [출처:] 인용뿐(본문에 인용된 관련 노트는 제외)."""
    if not answer:
        return []
    ctx_norms = {t for n in context_notes for t in [_norm_title(n.get("title", ""))] if t}
    issues: List[str] = []
    seen: set = set()
    for m in _CITATION_RE.finditer(answer):
        raw = m.group(1).strip()
        title = raw.split("#", 1)[0].strip()
        nt = _norm_title(title)
        if not nt or nt in seen:
            continue
        seen.add(nt)
        if any(nt == c or nt in c or c in nt for c in ctx_norms):
            continue
        issues.append(title)
    return issues


def _note_anchor(note: Dict[str, Any]) -> str:
    """노트 dict → 'Title#Heading' 또는 'Title' 앵커 문자열 (heading 있을 때만 붙임)."""
    title = note.get("title", "")
    heading = note.get("heading")
    return f"{title}#{heading}" if heading else title


def _truncate_note(content: str, max_chars: int, terms: Optional[List[str]] = None) -> str:
    """frontmatter 제거 후 max_chars로 자른다.

    terms(질문 키워드)가 주어지고 정답 후보가 본문 뒤쪽에 있어 앞부분만 자르면 잘려나가는
    경우, 첫 질문어 등장 위치를 중심으로 발췌한다(+ 노트 앞부분 일부는 맥락용으로 유지).
    질문어가 앞부분(head 범위)에 있거나 못 찾으면 기존처럼 앞부분을 자른다."""
    m = re.match(r'^---\s*\n.*?\n---\s*\n', content, re.DOTALL)
    body = content[m.end():] if m else content
    body = body.strip()
    if len(body) <= max_chars:
        return body

    # 질문어 첫 등장 위치(대소문자 무시). 여러 어절 중 가장 앞선 위치 사용.
    pos = -1
    if terms:
        low = body.lower()
        for t in terms:
            t = str(t or "").strip().lower()
            if len(t) < 2:
                continue
            i = low.find(t)
            if i != -1 and (pos == -1 or i < pos):
                pos = i

    # 정답 후보가 head 범위 안이거나 못 찾으면 앞부분 유지(기존 동작).
    if pos < 0 or pos < max_chars:
        return body[:max_chars] + "\n...(truncated)"

    # 정답이 뒤쪽 → 앞부분 일부(맥락) + 질문어 주변 창을 함께 넘긴다.
    head_keep = min(600, max_chars // 4)
    win = max_chars - head_keep
    start = max(0, pos - win // 4)
    excerpt = body[start:start + win]
    tail_more = "\n...(truncated)" if start + win < len(body) else ""
    return (body[:head_keep].strip() + "\n\n...(중략)...\n\n" + excerpt.strip() + tail_more)


def _obs_matches_snippet(matches: Any) -> str:
    parts: List[str] = []
    if isinstance(matches, list):
        for m in matches:
            if isinstance(m, str):
                parts.append(m)
            elif isinstance(m, dict):
                parts.append(str(m.get("context") or m.get("match") or ""))
    elif isinstance(matches, str):
        parts.append(matches)
    return " ".join(p for p in parts if p)


def _has_technical_terms(text: str) -> bool:
    """간단한 기술 용어 감지 (영어 대문자 약어, 영문 단어 비율)."""
    # 영어 단어 비율이 30% 이상이거나 대문자 약어가 있으면 기술적 내용으로 판단
    words = text.split()
    if not words:
        return False
    en_words = [w for w in words if re.match(r'[a-zA-Z]', w)]
    if len(en_words) / len(words) > 0.3:
        return True
    # 대문자 약어 (예: GPT, LLM, API)
    if re.search(r'\b[A-Z]{2,}\b', text):
        return True
    return False


def _keyword_terms(text: str) -> List[str]:
    stop = {
        "회의", "관련", "기존", "내용", "참고", "검토", "진행", "제공",
        "가능", "무엇", "어떤", "해서", "그리고", "offline", "online",
    }
    out: List[str] = []
    seen = set()
    for term in re.findall(r"[가-힣A-Za-z0-9]{2,}", text or ""):
        low = term.lower()
        if low in stop or low in seen:
            continue
        seen.add(low)
        out.append(term)
    return out


def _norm_title(text: str) -> str:
    return re.sub(r"[\s_\-./\\]+", "", (text or "").lower())


def _seen_equiv(norm: str, seen: set) -> bool:
    if norm in seen:
        return True
    return any(norm in old or old in norm for old in seen if old and norm)


def _context_rank_score(
    *,
    title: str,
    path: str,
    snippet: str,
    terms: List[str],
    source: str,
    order: int,
    relevance: float = 0.0,
) -> float:
    """컨텍스트 병합 랭킹 점수.

    relevance(0~1): 인덱서가 계산한 의미유사도/정규화 TF-IDF. 과거엔 이 값을 아예 쓰지
    않아 하이브리드 검색 순위가 병합 단계에서 소실되고, source 상수(+제목 부분일치)만으로
    순위가 뒤집혔다. 이제 relevance를 실제 가중치(≈제목일치 1건)로 반영한다.
    source 프리미엄은 과거 20점(100 vs 80)이라 의미 1등을 눌렀던 것을 5점으로 낮춘다.
    """
    hay_title = _norm_title(title)
    hay_path = _norm_title(path)
    hay_snippet = _norm_title(snippet)
    score = 85.0 if source == "obsidian" else 80.0
    score -= order
    score += 30.0 * max(0.0, min(1.0, relevance))
    for term in terms[:12]:
        nt = _norm_title(term)
        if not nt:
            continue
        if nt in hay_title:
            score += 25.0
        elif nt in hay_path:
            score += 12.0
        elif nt in hay_snippet:
            score += 4.0
    return score


def ask(question: str, **kwargs) -> str:
    """편의 함수 — answer 텍스트만 반환."""
    qa = WikiQA()
    result = qa.ask(question, **kwargs)
    return result["answer"]


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Vault 기반 Q&A")
    ap.add_argument("--question", required=True, help="질문")
    ap.add_argument("--max-notes", type=int, default=0, help="최대 컨텍스트 노트 수")
    ap.add_argument("--show-sources", action="store_true", help="출처 노트 목록 출력")
    args = ap.parse_args()

    qa = WikiQA()
    result = qa.ask(args.question, max_context_notes=args.max_notes)

    print("\n" + "=" * 60)
    print(result["answer"])
    print("=" * 60)

    if args.show_sources and result["sources"]:
        print(f"\n[컨텍스트로 사용된 노트 {len(result['sources'])}개]")
        for s in result["sources"]:
            print(f"  - {_note_anchor(s)} (점수: {s.get('score', 0):.3f})")

    markers = []
    if result["has_conflict"]:
        markers.append("⚠️ 충돌 정보 있음")
    if result["unverified"]:
        markers.append("확인 불가 항목 있음")
    if markers:
        print("\n" + " | ".join(markers))


if __name__ == "__main__":
    main()
