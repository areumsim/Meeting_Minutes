"""
meeting_workflow.py - shared meeting workflow helpers.

The project has several entrypoints: CLI batch, bat launchers, web upload,
realtime finalization, and Obsidian/audio ingestion. This module contains
small shared helpers that can be adopted incrementally without changing the
stable input-specific code paths.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    from meeting_minutes_app.common import config_loader as _cfg
    _cfg_ok = True
except ImportError:
    _cfg = None  # type: ignore
    _cfg_ok = False


def _c(key: str, default: Any = None) -> Any:
    return _cfg.get(key, default) if _cfg_ok else default


from meeting_minutes_app.wiki_core.vault_retrieval import (
    segments_to_search_text,
    merge_memo_parts,
    norm_title,
    strip_frontmatter,
    keyword_terms,
    note_domain_score,
    build_obsidian_context_memo,
    build_related_notes_memo,
)


def build_online_research_memo(llm, *, title: str = "", topic: str = "") -> str:
    """Return optional web research memo when wiki.online_search_enabled is true.

    웹리서치는 '발표 주제' 기반으로만 검색합니다.
    - topic이 있으면 topic 사용
    - topic이 없으면 title에서 인물명 패턴(교수/박사/이름)을 제거한 주제 키워드 사용
    발표자 이름으로 검색하면 동명이인 오검색으로 세미나 내용이 오염될 수 있습니다.
    """
    if not _c("wiki.online_search_enabled", False):
        return ""
    if llm is None:
        return ""

    if topic:
        query = topic.strip()
    elif title:
        # 인물명 패턴 제거: "교수", "박사", "님" 및 한글 2자 이름 토큰 제거
        # 예) "서지훈교수 퀀텀 세미나" → "퀀텀 세미나"
        import re as _re
        cleaned = _re.sub(r'[가-힣]{2,3}(?:교수|박사|님|대표|원장|소장)', '', title)
        cleaned = _re.sub(r'\s+', ' ', cleaned).strip()
        query = cleaned or title
    else:
        return ""

    if not query:
        return ""
    try:
        res = llm.web_research(query)
    except Exception:
        return ""
    text = res.get("text") if isinstance(res, dict) else ""
    if not text:
        return ""
    src_lines = ""
    sources = res.get("sources", []) if isinstance(res, dict) else []
    if sources:
        src_lines = "\n" + "\n".join(
            f"  - [{s.get('title', s.get('url', ''))}]({s.get('url', '')})"
            for s in sources[:3]
        )
    elif isinstance(res, dict) and res.get("source_warning"):
        src_lines = f"\n  - 출처 상태: {res.get('source_warning')}"
    return f"[웹 리서치: '{query}']\n{text}{src_lines}"


def _keyword_vault_search(
    search_text: str,
    *,
    exclude: set,
    indexer=None,
    obs=None,
    limit: int = 5,
) -> List[str]:
    """STT 텍스트에서 키워드 추출 → 개별 vault 검색 → 추가 관련 노트 반환.
    LLM 호출 없이 keyword_terms()만 사용해 생성 전에 실행 가능.
    exclude: 이미 수집된 노트 제목 집합 (중복 방지)
    """
    terms = keyword_terms(search_text)
    # 3자 이상 or 영문 포함 고유명사 우선 (노이즈 제거)
    candidates = [t for t in terms if len(t) >= 3 or re.search(r"[A-Za-z]", t)][:20]

    hits: Dict[str, float] = {}
    cur_norm = {norm_title(e) for e in exclude}

    for term in candidates:
        if indexer and indexer.is_built:
            try:
                for n_title in indexer.find_related(term, limit=2):
                    nn = norm_title(n_title)
                    if nn not in cur_norm:
                        hits[n_title] = hits.get(n_title, 0) + 1.0
                        cur_norm.add(nn)
            except Exception:
                pass
        if obs:
            try:
                for r in obs.search_simple(term, context_length=60, limit=3) or []:
                    fname = str(r.get("filename", "")).replace("\\", "/")
                    n_title = Path(fname).stem
                    nn = norm_title(n_title)
                    if n_title and nn not in cur_norm:
                        hits[n_title] = hits.get(n_title, 0) + 1.0
                        cur_norm.add(nn)
            except Exception:
                pass

    return [t for t, _ in sorted(hits.items(), key=lambda x: -x[1])[:limit]]


def graph_expand_titles(titles: List[str], hop: int = 1, max_extra: int = 5) -> List[str]:
    """관련 노트 제목 목록을 Wiki Knowledge Graph로 1-hop 확장한다.

    각 제목을 그래프의 `note` 노드로 조회하고, 연결된 person/organization/topic 노드의
    라벨을 추가로 반환한다(대부분 People/Organizations/Topics 폴더의 실제 노트 제목과
    일치하므로, build_related_notes_memo()가 그대로 본문을 찾아 주입할 수 있다 — 일치하는
    노트가 없는 라벨은 build_related_notes_memo()가 이미 조용히 건너뛴다).

    옵트인 기능 — `wiki_knowledge.graph_retrieval_expand_enabled`(기본 false)로 게이트한다.
    그래프 DB가 없거나(아직 백필 전) 조회 중 오류가 나도 원본 titles 처리에 전혀 영향을
    주지 않는다(항상 새 목록만 반환하고 실패 시 빈 리스트).
    """
    if not _c("wiki_knowledge.graph_retrieval_expand_enabled", False) or not titles:
        return []
    try:
        from meeting_minutes_app.wiki_core import graph_db, graph_sync

        seen = {norm_title(t) for t in titles}
        extra: List[str] = []
        for title in titles:
            key = graph_sync.resolve_canonical_key("note", title)
            node = graph_db.get_node_by_key("note", key)
            if not node:
                continue
            neighbors = graph_db.get_neighbors(node["id"], depth=hop).get("neighbors", [])
            for neighbor in neighbors:
                if neighbor.get("type") not in ("person", "organization", "topic"):
                    continue
                label = str(neighbor.get("label", "")).strip()
                nn = norm_title(label)
                if not label or nn in seen:
                    continue
                seen.add(nn)
                extra.append(label)
                if len(extra) >= max_extra:
                    return extra
        return extra
    except Exception:
        return []


def build_generation_context_memo(
    *,
    llm=None,
    title: str = "",
    topic: str = "",
    segments_or_text: Any,
    base_memo: Optional[str] = None,
    limit: int = 5,
    indexer=None,
    obs=None,
    include_web: bool = True,
) -> Tuple[Optional[str], List[str], Dict[str, Any]]:
    """Build one shared generation memo for batch, realtime, and ingestion.

    Returns:
        (merged_memo, related_note_titles, flags)

    flags["evidence"]: [{"note": title, "heading": Optional[str]}] — 실제 주입된 근거 목록.
    Personal Wiki frontmatter의 `evidence` 필드, wiki_context.json의 근거 기록에 재사용된다.

    This is the single place where local Obsidian Wiki context and optional
    online research are combined for LLM generation.
    """
    wiki_memo, related_titles, evidence = build_obsidian_context_memo(
        title=title,
        topic=topic,
        segments_or_text=segments_or_text,
        limit=limit,
        indexer=indexer,
        obs=obs,
    )

    # 2차: 키워드 기반 추가 검색 (LLM 없음, 생성 전 실행)
    extra_memo = ""
    if (indexer or obs) and _c("wiki.enabled", True):
        search_text = segments_to_search_text(segments_or_text)
        extra_titles = _keyword_vault_search(
            search_text,
            exclude=set(related_titles),
            indexer=indexer,
            obs=obs,
            limit=limit,
        )
        if extra_titles:
            extra_memo = build_related_notes_memo(
                indexer, obs, extra_titles,
                max_chars_per_note=int(_c("wiki.context_max_chars", 2000) or 2000),
            )
            related_titles = related_titles + extra_titles
            evidence = evidence + [{"note": t, "heading": None} for t in extra_titles]

    # 3차: 그래프 기반 확장 (옵트인, 기본 off) — 지금까지 모은 관련 노트를 그래프로
    # 1-hop 확장해 연결된 인물/조직/주제 노트를 추가로 끌어온다. 실패해도 위 결과에 영향 없음.
    graph_memo = ""
    graph_titles = graph_expand_titles(related_titles)
    if graph_titles:
        graph_memo = build_related_notes_memo(
            indexer, obs, graph_titles,
            max_chars_per_note=int(_c("wiki.context_max_chars", 2000) or 2000),
        )
        related_titles = related_titles + graph_titles
        evidence = evidence + [{"note": t, "heading": None} for t in graph_titles]

    # Registry 컨텍스트: 이전 결정사항/미완료 액션을 생성 프롬프트에 주입
    registry_memo = ""
    try:
        from meeting_minutes_app.wiki_core.wiki_knowledge import (
            DATA_DIR as _wk_data_dir,
            build_wiki_context_package,
            format_wiki_context_for_prompt,
        )
        _search_text = segments_to_search_text(segments_or_text)
        _pkg = build_wiki_context_package(
            related_titles=[],  # 관련 노트는 wiki_memo가 담당 — 여기서는 registry만
            data_dir=_wk_data_dir,
            filter_query=" ".join([title or "", topic or "", (_search_text or "")[:1000]]).strip(),
        )
        registry_memo = format_wiki_context_for_prompt(
            _pkg, max_chars=int(_c("wiki_knowledge.registry_context_max_chars", 2000) or 2000)
        )
    except Exception:
        registry_memo = ""

    web_memo = build_online_research_memo(llm, title=title, topic=topic) if include_web else ""
    merged = merge_memo_parts(base_memo, wiki_memo, extra_memo, graph_memo, registry_memo, web_memo)

    _max_chars = int(_c("wiki_knowledge.max_context_chars", 12000) or 12000)
    if merged and len(merged) > _max_chars:
        merged = merged[:_max_chars].rstrip() + "\n\n...(Wiki 참고 자료 초과로 일부 생략)"

    return merged, related_titles, {
        "wiki": bool(wiki_memo or extra_memo),
        "graph": bool(graph_memo),
        "registry": bool(registry_memo),
        "web": bool(web_memo),
        "evidence": evidence,
    }


def evidence_to_wikilinks(evidence: Sequence[Dict[str, Any]]) -> List[str]:
    """[{"note","heading"}] → "[[노트#헤딩]]" / "[[노트]]" 문자열 목록 (frontmatter evidence 필드용)."""
    out: List[str] = []
    for e in evidence or []:
        note = (e or {}).get("note")
        if not note:
            continue
        heading = (e or {}).get("heading")
        link = f"[[{note}#{heading}]]" if heading else f"[[{note}]]"
        if link not in out:
            out.append(link)
    return out


def merge_related_note_titles(*groups: Sequence[str]) -> List[str]:
    out: List[str] = []
    for group in groups:
        for title in group or []:
            if title and title not in out:
                out.append(title)
    return out


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  사실 검증 (Claim Verification)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def claim_verify(
    minutes: str,
    llm,
    *,
    indexer=None,
    obs=None,
    topic: str = "",
    max_claims: int = 8,
    current_title: str = "",
) -> Tuple[str, List[Dict]]:
    """회의록 주장을 vault 노트와 비교해 사실 검증한다.

    config.wiki.claim_verify=true 일 때 ingestion_pipeline에서 호출된다.
    current_title: 현재 생성 중인 노트 제목 — 자기참조 방지용 필터
    config.wiki.claim_web_verify=true 시 vault 불확실 주장에 대해 웹 전문가 의견 추가 검색.
    반환값: ('## 사실 검증' 마크다운 섹션 (검증 결과 없으면 ""), 주장별 구조화 결과 리스트)
    구조화 결과는 build_wiki_update_proposal()의 new_questions/new_claims/conflicts 생성에 재사용된다.
    """
    claims = _extract_claims(minutes, llm, topic=topic, max_claims=max_claims)
    if not claims:
        return "", []

    use_web_verify = _c("wiki.claim_web_verify", False)

    results: List[Dict] = []
    for item in claims:
        claim_text = (item.get("claim") or "").strip()
        if not claim_text:
            continue
        keywords = item.get("keywords") or []

        vault_notes = _fetch_vault_notes_for_claim(
            claim_text, keywords, indexer, obs, current_title=current_title
        )
        if not vault_notes:
            # vault에서 관련 자료를 아예 찾지 못함
            result: Dict = {
                "claim": claim_text,
                "verdict": "unknown",
                "summary": "관련 자료를 vault에서 찾을 수 없어 검증 불가",
                "evidence": "",
                "sources": [],
                "no_vault_data": True,
            }
            # 웹 전문가 의견 검색 (no_vault_data일 때 특히 유용)
            if use_web_verify:
                web_res = _web_verify_claim(claim_text, llm, topic=topic)
                if web_res:
                    result["web_opinion"] = web_res.get("text", "")
                    result["web_sources"] = web_res.get("sources", [])
                    if web_res.get("source_warning"):
                        result["web_source_warning"] = web_res.get("source_warning")
            results.append(result)
            continue

        verdict = _compare_claim_with_notes(claim_text, vault_notes, llm, topic=topic)
        # 불확실·충돌 주장에 대해 웹 보강 검색
        if use_web_verify and verdict.get("verdict") in ("unknown", "conflict"):
            web_res = _web_verify_claim(claim_text, llm, topic=topic)
            if web_res:
                verdict["web_opinion"] = web_res.get("text", "")
                verdict["web_sources"] = web_res.get("sources", [])
                if web_res.get("source_warning"):
                    verdict["web_source_warning"] = web_res.get("source_warning")
        results.append(verdict)

    return _format_verification_section(results), results


def _web_verify_claim(claim: str, llm, topic: str = "") -> Optional[Dict]:
    """웹 검색으로 주장에 대한 전문가 의견·공식 자료를 찾는다.
    llm.web_research()를 사용 (Anthropic web_search → GPT responses 폴백).
    반환: {"text": ..., "sources": [...]} or None
    """
    if llm is None:
        return None
    topic_hint = f"({topic}) " if topic else ""
    query = (
        f"{topic_hint}다음 주장의 사실 여부를 확인할 수 있는 "
        f"전문가 의견·공식 자료·연구 결과를 찾으세요: {claim}"
    )
    try:
        res = llm.web_research(query, max_uses=2, max_tokens=600)
        if isinstance(res, dict) and res.get("text"):
            return res
    except Exception:
        pass
    return None


def _extract_claims(
    minutes: str, llm, topic: str = "", max_claims: int = 8
) -> List[Dict]:
    """회의록에서 검증 가능한 사실적 주장(수치/날짜/기술사실)을 추출한다."""
    topic_line = f"회의 주제: {topic}\n" if topic else ""
    system = (
        "회의록에서 기존 지식베이스(vault)로 검증할 수 있는 '사실적 주장'을 추출하세요.\n"
        "목적: 언급된 기술·조직·제품·합의사항이 기존 지식과 일치/충돌하는지 확인.\n\n"
        "포함해야 할 주장:\n"
        "  - 특정 기술/제품/플랫폼의 기능·특성 (예: 'Classiq는 양자회로 자동화 플랫폼')\n"
        "  - 조직·회사의 역할이나 관계 (예: '메가존은 AWS 파트너')\n"
        "  - 이전에 합의/체결했다고 언급된 사항 (예: 'NDA를 한빛솔루션와 체결')\n"
        "  - 구체적 수치나 규모 (예: '참가팀 30팀', '예산 1억')\n\n"
        "제외해야 할 것:\n"
        "  - 이 회의의 날짜·장소 (vault로 검증 불가)\n"
        "  - 이 회의에서 새로 결정한 사항 (아직 vault에 없음)\n"
        "  - 순수 의견·계획·미래 목표\n\n"
        "출력: JSON 배열만 (코드블록/설명 없이)\n"
        '[{"claim":"주장 한 문장","keywords":["검색키1","검색키2"]}]'
    )
    user = f"{topic_line}회의록:\n\n{minutes[:5000]}"
    try:
        from meeting_minutes_app.meeting_pipeline.json_utils import parse_json_loose
        raw = (llm.chat(system, user, temp=0.1, max_tokens=1200) or "").strip()
        arr = parse_json_loose(raw, expect="list", default=[])
        return [x for x in arr if isinstance(x, dict) and x.get("claim")][:max_claims]
    except Exception:
        pass
    return []


def _fetch_vault_notes_for_claim(
    claim_text: str,
    keywords: List[str],
    indexer,
    obs,
    max_notes: int = 3,
    current_title: str = "",
) -> List[Dict]:
    """주장과 관련된 vault 노트 내용을 가져온다 (오프라인 우선).
    current_title: 현재 생성 중인 노트 — 자기참조 방지용 필터
    """
    query = " ".join([claim_text[:80]] + keywords[:3])
    notes: List[Dict] = []
    seen: set = set()
    cur_norm = norm_title(current_title) if current_title else ""

    def _is_current(title: str) -> bool:
        if not cur_norm:
            return False
        n = norm_title(title)
        return n == cur_norm or cur_norm in n or n in cur_norm

    # 0) 섹션 단위 인덱스 (활성화 시 우선 — heading 단위 근거로 인용 정확도 향상)
    if indexer and indexer.is_built and _c("wiki_knowledge.section_index_enabled", True):
        try:
            for hit in indexer.search_sections(query, limit=max_notes + 2):
                if hit.get("score", 0) < 0.02:
                    continue
                title = hit.get("note_title", "")
                heading = hit.get("heading", "")
                if not title or title in seen or _is_current(title):
                    continue
                content = indexer.get_section_content(hit["note_path"], heading)
                if not content:
                    continue
                relevance = note_domain_score(title, content, query)
                if relevance < 1.0:
                    continue
                seen.add(title)
                notes.append({
                    "title": title,
                    "heading": heading or None,
                    "anchor": f"{title}#{heading}" if heading else title,
                    "content": content[:1500],
                    "relevance": relevance,
                })
                if len(notes) >= max_notes:
                    break
        except Exception:
            pass

    # 1) 로컬 TF-IDF 인덱스 (오프라인, whole-note — 섹션에서 못 찾은 나머지 채움)
    #    임베딩 유사도(cosine)로 검색된 노트는 TF-IDF 점수가 낮아도 유지
    if len(notes) < max_notes and indexer and indexer.is_built:
        for hit in indexer.search(query, limit=max_notes + 2):
            if hit.get("score", 0) < 0.02 and hit.get("cosine", 0.0) <= 0.0:
                continue
            title = hit.get("wikilink_title") or hit.get("title", "")
            if title in seen or _is_current(title):
                continue
            content = indexer.get_note_content(hit["path"])
            if content:
                relevance = note_domain_score(title, content, query)
                if relevance < 1.0:
                    continue
                seen.add(title)
                notes.append({
                    "title": title,
                    "heading": None,
                    "anchor": title,
                    "content": strip_frontmatter(content)[:1500],
                    "relevance": relevance,
                })
            if len(notes) >= max_notes:
                break

    # 2) Obsidian REST (플러그인 실행 중일 때만 — 섹션 API 없음, whole-note)
    if len(notes) < max_notes and obs:
        try:
            for hit in obs.search_simple(query, context_length=200, limit=max_notes * 2) or []:
                fname = str(hit.get("filename", "")).replace("\\", "/")
                title = Path(fname).stem
                if title in seen or _is_current(title):
                    continue
                note_content = obs.get_note(fname) or ""
                if note_content:
                    relevance = note_domain_score(title, note_content, query)
                    if relevance < 1.0:
                        continue
                    seen.add(title)
                    notes.append({
                        "title": title,
                        "heading": None,
                        "anchor": title,
                        "content": strip_frontmatter(note_content)[:1500],
                        "relevance": relevance,
                    })
                if len(notes) >= max_notes:
                    break
        except Exception:
            pass

    # 3) Supermemory 이전 회의 기억
    if len(notes) < max_notes:
        try:
            from meeting_minutes_app.wiki_core.supermemory_client import get_client as _sm_get  # type: ignore
            sm = _sm_get()
            if sm.enabled():
                for fragment in sm.search(query, limit=max_notes - len(notes)):
                    if fragment.strip():
                        notes.append({
                            "title": "이전 회의 기억",
                            "heading": None,
                            "anchor": "이전 회의 기억",
                            "content": fragment.strip()[:1500],
                            "relevance": 1.5,
                        })
        except ImportError:
            pass

    return notes


def _compare_claim_with_notes(
    claim: str,
    vault_notes: List[Dict],
    llm,
    topic: str = "",
) -> Dict:
    """주장 하나를 vault 노트들과 LLM으로 비교해 판정한다."""
    notes_block = "\n\n".join(
        f"### [{n.get('anchor') or n['title']}]\n{n['content']}"
        for n in vault_notes
    )
    system = (
        "사실 검증 전문가로서, 회의 주장을 vault 노트와 비교해 판정하세요.\n\n"
        "판정 기준:\n"
        '- "match": 노트 내용이 주장을 지지하거나 일치\n'
        '- "conflict": 수치·날짜·사실관계가 노트와 명확히 다름\n'
        '- "unknown": 노트를 검토했으나 주장 관련 정보를 확인할 수 없음\n\n'
        "출력: JSON 한 줄 (코드블록/설명 없이)\n"
        '{"verdict":"match|conflict|unknown",'
        '"summary":"판정 근거 한 문장 (match→무엇이 일치하는지, conflict→무엇이 다른지, unknown→왜 불확실한지)",'
        '"evidence":"근거가 된 노트의 핵심 문장 또는 수치 (없으면 빈 문자열)",'
        '"sources":["근거 노트 제목 — 위 [제목] 표기를 그대로 사용 (헤딩 포함 시 \'노트명#헤딩\' 형식 유지)"]}'
    )
    topic_hint = f"(회의 주제: {topic})\n" if topic else ""
    user = (
        f"{topic_hint}검증할 주장: {claim}\n\n"
        f"Vault 노트:\n{notes_block}"
    )
    try:
        from meeting_minutes_app.meeting_pipeline.json_utils import parse_json_loose
        raw = (llm.chat(system, user, temp=0.0, max_tokens=400) or "").strip()
        data = parse_json_loose(raw, expect="dict", default={})
        if isinstance(data, dict) and data.get("verdict") in ("match", "conflict", "unknown"):
            verdict = data["verdict"]
            min_relevance = max((float(n.get("relevance", 0.0)) for n in vault_notes), default=0.0)
            if verdict == "match" and min_relevance < 1.0:
                verdict = "unknown"
            return {
                "claim": claim,
                "verdict": verdict,
                "summary": (data.get("summary") or "").strip(),
                "evidence": (data.get("evidence") or "").strip(),
                "sources": data.get("sources") or [n.get("anchor") or n["title"] for n in vault_notes],
                "confidence": "medium" if verdict in ("match", "conflict") else "low",
                "no_vault_data": False,
            }
    except Exception:
        pass
    return {
        "claim": claim, "verdict": "unknown",
        "summary": "판정 파싱 실패", "evidence": "", "sources": [], "confidence": "low",
        "no_vault_data": False,
    }


def _format_verification_section(results: List[Dict]) -> str:
    """검증 결과 리스트를 '## 사실 검증' 마크다운 섹션으로 변환한다.

    판정 기호:
      ✅ match   — vault 지식과 일치 (근거 있음)
      ⚠️ conflict — vault 지식과 충돌 (수치·날짜·관계 불일치)
      ❓ unknown  — vault 검토했으나 근거 없음
      🔍 no_data — vault에서 관련 자료 자체를 찾지 못함
    """
    if not results:
        return ""

    lines: List[str] = ["## 사실 검증\n"]

    for r in results:
        verdict      = r.get("verdict", "unknown")
        claim        = r.get("claim", "")
        summary      = r.get("summary", "")
        evidence     = r.get("evidence", "")
        sources      = r.get("sources", [])
        no_vault     = r.get("no_vault_data", False)
        web_opinion  = r.get("web_opinion", "")
        web_sources  = r.get("web_sources", [])
        confidence   = r.get("confidence", "")
        src          = ", ".join(f"[[{s}]]" for s in sources)
        web_src      = " · ".join(
            f"[{s.get('title', '링크')}]({s.get('url', '')})"
            for s in (web_sources or [])[:3]
        )

        if verdict == "conflict":
            lines.append(f"- ⚠️ **[충돌]** {claim}")
            if summary:
                lines.append(f"  - 판정: {summary}")
            if confidence:
                lines.append(f"  - 신뢰도: {confidence}")
            if evidence:
                lines.append(f"  - vault 근거: _{evidence}_")
            if src:
                lines.append(f"  - 출처: {src}")
        elif verdict == "match":
            lines.append(f"- ✅ **[확인됨]** {claim}")
            if summary:
                lines.append(f"  - 판정: {summary}")
            if confidence:
                lines.append(f"  - 신뢰도: {confidence}")
            if evidence:
                lines.append(f"  - vault 근거: _{evidence}_")
            if src:
                lines.append(f"  - 출처: {src}")
        elif no_vault:
            lines.append(f"- 🔍 **[자료 없음]** {claim}")
            lines.append(f"  - vault에서 관련 자료를 찾지 못해 검증 불가")
        else:
            # unknown: vault 검토했으나 확인 불가
            lines.append(f"- ❓ **[불확실]** {claim}")
            if summary:
                lines.append(f"  - 판정: {summary}")
            if confidence:
                lines.append(f"  - 신뢰도: {confidence}")
            if src:
                lines.append(f"  - 검토 자료: {src}")

        # 웹 전문가 의견 (wiki.claim_web_verify=true 시)
        if web_opinion:
            lines.append(f"  - 🌐 전문가 의견: {web_opinion[:300]}")
            if web_src:
                lines.append(f"  - 웹 출처: {web_src}")
            elif r.get("web_source_warning"):
                lines.append(f"  - 웹 출처 상태: {r.get('web_source_warning')}")
            elif web_sources == []:
                lines.append("  - 웹 출처 상태: 검색/폴백 결과에 URL 출처 없음")

    lines.append("")
    return "\n".join(lines)
