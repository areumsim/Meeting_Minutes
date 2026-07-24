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
from typing import Any, Dict, List, Tuple

for _s in (sys.stdout, sys.stderr):
    if getattr(_s, "encoding", None) and _s.encoding.lower() in ("cp949", "euc-kr", "ansi"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

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

규칙:
1. 아래 노트 컨텍스트(제목·헤딩·본문 전부)를 근거로 답하세요. 컨텍스트에 문장으로 똑같이
   적혀 있지 않더라도, 노트에서 **직접 도출·추론 가능한 인물/기관/용어/사실은 적극적으로 답하세요.**
   특히 블록 제목 "### [[노트#헤딩]]"의 헤딩은 인용 대상일 뿐 아니라 **그 자체가 사실 정보**입니다
   (예: 헤딩에 사람 이름·소속·그룹명이 들어 있으면 그것이 곧 답의 근거가 됩니다). 질문의 표현이
   노트와 달라도(예: "연대"="양자컴퓨팅") 합리적으로 연결해 답하세요. 외부 지식으로 지어내지는 마세요.
2. 주장에는 가능한 한 [출처: [[노트 제목]]] 또는 [출처: [[노트 제목#헤딩]]] 형식으로 인용하세요.
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


class WikiQA:
    """Vault 기반 LLM Q&A."""

    def __init__(self, llm=None, obs=None, indexer=None):
        self._llm = llm
        self._obs = obs
        self._indexer = indexer
        self._unverified = _c("wiki.unverified_marker", _UNVERIFIED_MARKER)
        self._conflict = _c("wiki.conflict_marker", _CONFLICT_MARKER)
        self._max_notes = int(_c("wiki.max_context_notes", 5))
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

        # 컨텍스트 수집
        context_notes = self._gather_context(question, limit)

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

        return {
            "answer": answer,
            "sources": context_notes,
            "has_conflict": has_conflict,
            "unverified": unverified,
        }

    def _gather_context(self, question: str, max_notes: int) -> List[Dict[str, Any]]:
        """인덱서 + Obsidian search 로 관련 노트/섹션을 수집한다."""
        seen_titles: set = set()
        seen_sections: set = set()
        results: List[Dict] = []

        # 검색에만 config.analysis.entity_aliases(줄임말/구어체→정식 명칭)를 적용한다.
        # 예: "연대"(2글자) vs "양자컴퓨팅"(3글자)는 bigram 토큰이 하나도 안 겹쳐서
        # TF-IDF가 완전히 놓치는데, 노트 본문은 정식 명칭만 쓰는 경우가 많다 —
        # meeting_workflow의 STT 오인식 보정과 같은 별칭 테이블을 재사용한다.
        # LLM 프롬프트(_build_prompt)에는 원문 question을 그대로 넘겨 사용자가
        # 실제로 쓴 표현이 보존되게 한다.
        try:
            from meeting_minutes_app.wiki_core.vault_retrieval import normalize_domain_text
            search_question = normalize_domain_text(question)
        except Exception:
            search_question = question
        terms = _keyword_terms(search_question)

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
                )
                # 헤딩은 정답(인물/그룹명 등)을 담는 경우가 많은데 본문(content)에는 헤딩 줄이
                # 빠져 있다. 헤딩을 본문 앞에 붙여 LLM이 인용링크가 아닌 '사실 텍스트'로 보게 한다.
                sec_body = f"# {heading}\n{content}" if heading else content
                results.append({
                    "title": title,
                    "heading": heading,
                    "path": hit.get("note_path", ""),
                    "snippet": hit.get("snippet", ""),
                    "content": _truncate_note(sec_body, self._max_chars),
                    "score": hit.get("score", 0),
                    "rank_score": rank_score,
                    "source": "index_section",
                })
            # 섹션으로 이미 확보한 노트는 whole-note 레이어에서 제외 (섹션 근거 우선)
            seen_titles.update(norm for norm, _h in seen_sections)

        # Layer 1: 로컬 TF-IDF 인덱스
        if self._indexer:
            idx_results = self._indexer.search(
                search_question, limit=max_notes * 2, path_prefixes=path_prefixes)
            for r in idx_results:
                title = r.get("title", "")
                norm = _norm_title(title)
                if not norm or _seen_equiv(norm, seen_titles):
                    continue
                seen_titles.add(norm)
                # 노트 전체 내용 읽기
                content = self._indexer.get_note_content(r["path"]) or ""
                rank_score = _context_rank_score(
                    title=title,
                    path=r.get("path", ""),
                    snippet=r.get("snippet", ""),
                    terms=terms,
                    source="index",
                    order=len(results),
                )
                results.append({
                    "title": title,
                    "heading": None,
                    "path": r.get("path", ""),
                    "snippet": r.get("snippet", ""),
                    "content": _truncate_note(content, self._max_chars),
                    "score": r.get("score", 0),
                    "rank_score": rank_score,
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
                for i, r in enumerate(obs_results):
                    fname = r.get("filename", "")
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
                        "content": _truncate_note(content, self._max_chars),
                        "score": r.get("score", 0),
                        "rank_score": rank_score,
                        "source": "obsidian",
                    })
            except Exception:
                pass

        # raw score는 index/REST 간 스케일이 달라 자체 랭킹으로 정렬
        results.sort(key=lambda x: -x.get("rank_score", 0))
        return results[:max_notes]

    def _build_prompt(
        self, question: str, context_notes: List[Dict]
    ) -> Tuple[str, str]:
        """system + user 프롬프트를 구성한다."""
        # 컨텍스트 블록 구성
        context_blocks = []
        for note in context_notes:
            anchor = _note_anchor(note)
            content = note.get("content") or note.get("snippet") or ""
            block = f"### [[{anchor}]]\n{content.strip()}"
            context_blocks.append(block)
        context_str = "\n\n".join(context_blocks)

        system = _SYSTEM_PROMPT_TEMPLATE.format(
            unverified=self._unverified,
            conflict=self._conflict,
            context=context_str,
        )
        # wiki.citation_required=false 시 출처 인용 강제를 완화 (기본 true)
        if not _c("wiki.citation_required", True):
            system = system.replace(
                "2. 모든 주장에는 반드시 [출처: [[노트 제목]]] 또는 "
                "[출처: [[노트 제목#헤딩]]] 형식으로 인용하세요.",
                "2. 가능하면 [출처: [[노트 제목]]] 형식으로 인용하세요 (필수 아님).",
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


def _note_anchor(note: Dict[str, Any]) -> str:
    """노트 dict → 'Title#Heading' 또는 'Title' 앵커 문자열 (heading 있을 때만 붙임)."""
    title = note.get("title", "")
    heading = note.get("heading")
    return f"{title}#{heading}" if heading else title


def _truncate_note(content: str, max_chars: int) -> str:
    """frontmatter 제거 후 max_chars로 자른다."""
    m = re.match(r'^---\s*\n.*?\n---\s*\n', content, re.DOTALL)
    body = content[m.end():] if m else content
    body = body.strip()
    if len(body) <= max_chars:
        return body
    return body[:max_chars] + "\n...(truncated)"


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
) -> float:
    hay_title = _norm_title(title)
    hay_path = _norm_title(path)
    hay_snippet = _norm_title(snippet)
    score = 100.0 if source == "obsidian" else 80.0
    score -= order
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
