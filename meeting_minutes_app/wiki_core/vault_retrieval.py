"""
vault_retrieval.py - generic Obsidian/Vault retrieval helpers.

Domain-agnostic helpers for searching and reading notes from an Obsidian
vault (via the REST API and/or the local vault index). Extracted from
meeting_workflow.py so that wiki_core never depends on the meeting-specific
pipeline.
"""

from __future__ import annotations

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


def segments_to_search_text(segments_or_text: Any, max_segments: int = 30) -> str:
    """Return compact searchable text from segments, a script, or minutes text.

    세그먼트 목록인 경우 전체를 균등 샘플링하여 회의 후반부 주제도 포함한다.
    """
    if isinstance(segments_or_text, str):
        return segments_or_text[:4000]
    if isinstance(segments_or_text, Sequence):
        segs = list(segments_or_text)
        n = len(segs)
        if n == 0:
            return ""
        if n <= max_segments:
            sample = segs
        else:
            # 앞 / 중간 / 끝 균등 샘플링 (전체 주제 커버)
            step = n // max_segments
            sample = [segs[i * step] for i in range(max_segments) if i * step < n]
        texts: List[str] = []
        for seg in sample:
            if isinstance(seg, dict):
                txt = str(seg.get("text") or "").strip()
                if txt:
                    texts.append(txt)
        return " ".join(texts)[:4000]
    return ""


def merge_memo_parts(*parts: Optional[str]) -> Optional[str]:
    merged = "\n\n".join(p.strip() for p in parts if p and p.strip())
    return merged or None


def norm_title(text: str) -> str:
    return re.sub(r"[\s_\-./\\]+", "", (text or "").lower())


DOMAIN_ALIASES: Dict[str, str] = {
    "한빗": "한빛",
    "한빚": "한빛",
    "한빝": "한빛",
    "hanbit": "한빛",
    "한빛베닛": "한빛솔루션",
    "코롱베니트": "한빛솔루션",
    "코론베니트": "한빛솔루션",
    "qday": "Q-Day",
    "큐데이": "Q-Day",
    "nisq": "NISQ",
    "큐램": "QRAM",
    "양자램": "QRAM",
    "퀀텀4 ai": "Quantum for AI",
    "퀀텀4AI": "Quantum for AI",
    "ai4 퀀텀": "AI for Quantum",
    "ai4퀀텀": "AI for Quantum",
}


def normalize_domain_text(text: str) -> str:
    out = text or ""
    for src, dst in DOMAIN_ALIASES.items():
        out = re.sub(re.escape(src), dst, out, flags=re.IGNORECASE)
    return out


def note_domain_score(title: str, content: str, query_text: str) -> float:
    hay = normalize_domain_text(f"{title} {content}")[:4000]
    q = normalize_domain_text(query_text)
    terms = keyword_terms(q)[:12]
    score = 0.0
    for t in terms:
        if norm_title(t) and norm_title(t) in norm_title(hay):
            score += 1.0
    for marker in ("해커톤", "기념품", "후원", "한빛", "양자어닐", "볼츠만", "qml", "nisq", "qnn", "ionq", "q-day"):
        if marker.lower() in q.lower() and marker.lower() in hay.lower():
            score += 2.0
    return score


def strip_frontmatter(content: str) -> str:
    return re.sub(r"^---\s*\n.*?\n---\s*\n", "", content or "", flags=re.DOTALL)


def keyword_terms(text: str) -> List[str]:
    stop = {
        # 일반 회의 단어
        "회의", "검토", "진행", "기존", "데이터", "참조", "제공", "가능",
        "구분", "내용", "관련", "그리고", "offline", "online", "오늘",
        "이번", "저번", "부분", "말씀", "정도",
        # 구어체 / 채움말
        "그런데", "그래서", "그러니까", "그러면", "하지만", "근데", "그냥",
        "뭔가", "이게", "저게", "아까", "아직", "일단", "우선", "사실",
        "맞아", "맞죠", "아니", "아니요", "맞습니다", "없는", "있는",
        # 시간/지시
        "다음", "지난", "오전", "오후", "이후", "현재", "앞으로",
        "여기", "거기", "저기", "이거", "저거",
        # 조직/역할 일반어
        "팀장", "담당자", "담당", "팀원", "직원", "대표", "본부",
    }
    seen = set()
    out: List[str] = []
    for term in re.findall(r"[가-힣A-Za-z0-9]{2,}", text or ""):
        low = term.lower()
        if low in stop or low in seen:
            continue
        seen.add(low)
        out.append(term)
    return out


def load_obsidian_client():
    if not _c("obsidian.enabled", False):
        return None
    try:
        from meeting_minutes_app.wiki_core.obsidian import ObsidianClient
        obs = ObsidianClient.from_config()
        if obs and obs.ping():
            return obs
        if obs:
            obs.close()
    except Exception:
        pass
    return None


def load_vault_indexer():
    if not _c("indexing.enabled", True):
        return None
    try:
        from meeting_minutes_app.wiki_core.vault_indexer import VaultIndexer
        idx = VaultIndexer.from_config()
        if idx and idx.load():
            return idx
    except Exception:
        pass
    return None


def search_related_notes_rest(
    obs,
    *,
    title: str = "",
    topic: str = "",
    search_text: str = "",
    limit: int = 5,
) -> List[str]:
    """Search Obsidian REST with conservative current-note filtering."""
    current_norm = norm_title(title)
    terms = keyword_terms(" ".join([title or "", topic or "", search_text or ""]))
    queries: List[str] = []
    for q in (title, topic, search_text[:220], " ".join(terms[:6])):
        q = (q or "").strip()
        if q and q not in queries:
            queries.append(q[:500])

    ranked: Dict[str, float] = {}
    for q in queries:
        try:
            for r in obs.search_simple(q, context_length=120, limit=max(limit * 2, 8)) or []:
                fname = str(r.get("filename", "")).replace("\\", "/")
                note_title = Path(fname).stem
                note_norm = norm_title(note_title)
                if (
                    not note_title
                    or note_norm == current_norm
                    or (current_norm and current_norm in note_norm)
                    or (note_norm and note_norm in current_norm)
                ):
                    continue
                bonus = sum(2.0 for term in terms[:10] if norm_title(term) in note_norm)
                ranked[note_title] = ranked.get(note_title, 0.0) + 1.0 + bonus
        except Exception:
            continue
    return [t for t, _ in sorted(ranked.items(), key=lambda x: (-x[1], x[0]))[:limit]]


def get_related_note_content(indexer, obs, title: str) -> str:
    norm = norm_title(title)
    if indexer and getattr(indexer, "_notes", None):
        for rel, note in indexer._notes.items():
            candidates = [
                note.get("title", ""),
                note.get("wikilink_title", ""),
                Path(rel).stem,
            ]
            if any(norm_title(c) == norm for c in candidates):
                return indexer.get_note_content(rel) or ""
    if obs:
        try:
            hits = obs.search_simple(title, context_length=80, limit=5) or []
            for h in hits:
                fname = str(h.get("filename", ""))
                if norm_title(Path(fname.replace("\\", "/")).stem) == norm:
                    return obs.get_note(fname) or ""
        except Exception:
            return ""
    return ""


def _sm_context_memo(*, title: str, topic: str, search_text: str, limit: int) -> str:
    """Supermemory에서 이전 회의 관련 기억을 검색해 컨텍스트 메모로 반환."""
    try:
        from meeting_minutes_app.wiki_core.supermemory_client import get_client as _sm_get  # type: ignore
        sm = _sm_get()
        if not sm.enabled():
            return ""
    except ImportError:
        return ""
    query = " ".join([topic or "", title or ""]).strip() or search_text[:200]
    if not query:
        return ""
    fragments = sm.search(query, container_tag=topic or title or "", limit=limit)
    blocks = [f"- {f.strip()[:500]}" for f in fragments if f.strip()]
    if not blocks:
        return ""
    return (
        "[이전 회의 기억 - 최종 출력 금지]\n"
        "아래는 Supermemory에서 검색한 이전 회의 관련 기억입니다. 사실 확인과 배경 연결에만 사용하세요.\n\n"
        + "\n".join(blocks)
    )


def build_related_notes_memo(
    indexer,
    obs,
    titles: List[str],
    max_chars_per_note: int = 2000,
) -> str:
    if not titles:
        return ""
    blocks: List[str] = []
    for title in titles:
        content = get_related_note_content(indexer, obs, title)
        if not content:
            continue
        blocks.append(
            f"### [[{title}]]\n"
            f"{strip_frontmatter(content).strip()[:max_chars_per_note]}"
        )
    if not blocks:
        return ""
    return (
        "[내부 참고자료 - 최종 출력 금지]\n"
        "아래는 전사 내용으로 검색한 내부 참고자료입니다. 최종 회의록에 이 블록 제목이나 원문을 그대로 출력하지 마세요. "
        "회의록 작성 시 사실 확인과 배경 연결에만 사용하고, 필요한 내용은 '참고 근거와 주의사항' 또는 관련 노트 링크로 짧게 요약하세요. "
        "새 회의에서 직접 언급되지 않은 내용은 '참고 배경'으로 구분하세요.\n\n"
        + "\n\n".join(blocks)
    )


def build_related_sections_memo(
    indexer,
    sections: List[Dict[str, Any]],
    max_chars_per_section: int = 2000,
) -> str:
    """섹션 단위 근거 블록 생성. sections: [{"title","heading","content"}]"""
    if not sections:
        return ""
    blocks = [
        f"### [[{s['title']}#{s['heading']}]]\n{s['content'].strip()[:max_chars_per_section]}"
        for s in sections if s.get("content", "").strip()
    ]
    if not blocks:
        return ""
    return (
        "[내부 참고자료 (섹션 근거) - 최종 출력 금지]\n"
        "아래는 관련 노트의 특정 섹션(heading)만 발췌한 근거입니다. 최종 회의록에 이 블록 제목이나 원문을 "
        "그대로 출력하지 마세요. 사실 확인과 배경 연결에만 사용하세요.\n\n"
        + "\n\n".join(blocks)
    )


def build_obsidian_context_memo(
    *,
    title: str = "",
    topic: str = "",
    segments_or_text: Any,
    limit: int = 5,
    indexer=None,
    obs=None,
) -> Tuple[str, List[str], List[Dict[str, Optional[str]]]]:
    """Return optional Vault/Obsidian context memo, related note titles, and evidence.

    evidence: [{"note": title, "heading": Optional[str]}] — 실제 메모에 주입된 근거 목록.
    This helper is deliberately best-effort. Obsidian/index errors must never
    fail transcript processing.
    """
    search_text = segments_to_search_text(segments_or_text)
    if not (title or topic or search_text):
        return "", [], []

    close_obs = False
    if indexer is None:
        indexer = load_vault_indexer()
    if obs is None:
        obs = load_obsidian_client()
        close_obs = obs is not None

    related_titles: List[str] = []
    evidence: List[Dict[str, Optional[str]]] = []
    try:
        query_for_score = normalize_domain_text(" ".join([title or "", topic or "", search_text or ""]))
        query = search_text or " ".join([title, topic])

        section_memo = ""
        section_hits: List[Dict[str, Any]] = []
        if indexer and indexer.is_built and _c("wiki_knowledge.section_index_enabled", True):
            for hit in indexer.find_related_sections(query, limit=limit):
                note_title = hit.get("note_title", "")
                heading = hit.get("heading", "")
                if not note_title or not heading:
                    continue
                content = indexer.get_section_content(hit["note_path"], heading)
                if not content or note_domain_score(note_title, content, query_for_score) < 1.0:
                    continue
                section_hits.append({"title": note_title, "heading": heading, "content": content})
                if note_title not in related_titles:
                    related_titles.append(note_title)
                evidence.append({"note": note_title, "heading": heading})
            section_memo = build_related_sections_memo(
                indexer, section_hits,
                max_chars_per_section=int(_c("wiki.context_max_chars", 2000) or 2000),
            )

        if indexer and indexer.is_built:
            for note_title in indexer.find_related(query, limit=limit):
                content = get_related_note_content(indexer, obs, note_title) or ""
                if note_domain_score(note_title, content, query_for_score) < 1.0:
                    continue
                if note_title not in related_titles:
                    related_titles.append(note_title)
                    evidence.append({"note": note_title, "heading": None})
        if obs:
            for note_title in search_related_notes_rest(
                obs, title=title, topic=topic, search_text=search_text, limit=limit
            ):
                content = get_related_note_content(indexer, obs, note_title) or ""
                if note_domain_score(note_title, content, query_for_score) < 1.0:
                    continue
                if note_title not in related_titles:
                    related_titles.append(note_title)
                    evidence.append({"note": note_title, "heading": None})
        related_titles = related_titles[:limit]
        memo = build_related_notes_memo(
            indexer,
            obs,
            related_titles,
            max_chars_per_note=int(_c("wiki.context_max_chars", 2000) or 2000),
        )
        memo = merge_memo_parts(memo, section_memo)
        sm_memo = _sm_context_memo(title=title, topic=topic, search_text=search_text, limit=limit)
        if sm_memo:
            memo = f"{memo}\n\n{sm_memo}".strip() if memo else sm_memo
        return memo, related_titles, evidence[: limit * 2]
    finally:
        if close_obs and obs:
            try:
                obs.close()
            except Exception:
                pass
