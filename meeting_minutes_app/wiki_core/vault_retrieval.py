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


def _load_domain_aliases() -> Dict[str, str]:
    """config.analysis.entity_aliases — STT 오인식 표기 → 정식 명칭 치환 맵.

    과거엔 도메인 별칭("한빛", "NISQ" 등)이 여기 하드코딩돼 있었다 —
    enrichment.normalize_entity_name과 같은 config 키를 공유한다.
    """
    try:
        from meeting_minutes_app.common import config_loader as _cfg
        raw = _cfg.get("analysis.entity_aliases", {}) or {}
        if isinstance(raw, dict):
            return {str(k): str(v) for k, v in raw.items()
                    if not str(k).startswith("_")}
    except Exception:
        pass
    return {}


DOMAIN_ALIASES: Dict[str, str] = _load_domain_aliases()


def normalize_domain_text(text: str) -> str:
    out = text or ""
    for src, dst in DOMAIN_ALIASES.items():
        out = re.sub(re.escape(src), dst, out, flags=re.IGNORECASE)
    return out


# note_domain_score()의 가산점 마커 기본값 — 과거 하드코딩값(양자 도메인 전용).
# wiki.domain_relevance_keywords로 오버라이드하지 않으면 이 기본값을 그대로 쓴다
# (하위호환). 여러 도메인(예: 양자 + PhysicalAI)을 동시에 다루려면 config에
# 두 도메인 마커를 모두 합쳐 넣으면 된다 — 이 목록은 "관련도 가산점" 용도라
# 도메인 구분 없이 매칭되는 마커가 많을수록 검색 품질이 오른다.
_DEFAULT_DOMAIN_RELEVANCE_MARKERS = (
    "해커톤", "기념품", "후원", "한빛", "양자어닐", "볼츠만", "qml", "nisq", "qnn", "ionq", "q-day",
)


def _domain_relevance_markers() -> Sequence[str]:
    raw = _c("wiki.domain_relevance_keywords", None)
    if isinstance(raw, list) and raw:
        return [str(m) for m in raw if str(m).strip()]
    return _DEFAULT_DOMAIN_RELEVANCE_MARKERS


def _domain_archive_paths() -> Dict[str, str]:
    domains = _c("obsidian.project_domains", {}) or {}
    return {str(k): str(v) for k, v in domains.items() if v}


def _archive_domain_for_path(note_path: str) -> str:
    """note_path가 특정 도메인 전용 아카이브 하위에 있으면 그 도메인 키를, 아니면 빈 문자열을 반환.
    (예: "Archive/도메인_아카이브/..." → "양자")"""
    if not note_path:
        return ""
    norm = note_path.replace("\\", "/")
    for key, path in _domain_archive_paths().items():
        p = path.rstrip("/")
        if norm == p or norm.startswith(p + "/"):
            return key
    return ""


def _domain_signal_count(domain: str, query_text: str) -> int:
    categories = _c("obsidian.meeting_categories", {}) or {}
    entry = categories.get(domain, {})
    kws = entry.get("keywords", []) if isinstance(entry, dict) else []
    if not isinstance(kws, list):
        return 0
    q = (query_text or "").lower()
    return sum(1 for kw in kws if str(kw).strip() and str(kw).strip().lower() in q)


def _domain_has_signal(domain: str, query_text: str) -> bool:
    """query_text가 해당 도메인에 대한 실제 신호를 담고 있는가.
    키워드 1개만 우연히 겹치는 것으로는 불충분하다 — 예를 들어 전혀 무관한 회의에서
    "양자컴퓨터"라는 말이 스쳐 지나간 것만으로 "양자" 키워드 하나가 매칭되지만, 이는
    실제로 그 도메인을 다루는 맥락이라 보기엔 근거가 너무 약하다(실제 발생했던 컨텍스트
    오염 버그의 원인). 서로 다른 도메인 키워드가 최소 2개 이상 나와야 통과시킨다."""
    return _domain_signal_count(domain, query_text) >= 2


def is_domain_mismatched(note_path: str, query_text: str) -> bool:
    """note_path가 도메인 전용 아카이브 소속인데 query_text에 그 도메인 고유 신호가
    없으면 True(=배제해야 함). 일반 relevance 재채점 없이 순수 도메인 오염만 걸러낼 때
    쓰는 가벼운 게이트 — 기존에 relevance 재채점이 없던 호출부(예: TF-IDF 인덱서가 이미
    랭킹한 결과를 그대로 신뢰하던 곳)에 추가 필터링 부작용 없이 적용하기 위함."""
    note_domain = _archive_domain_for_path(note_path)
    return bool(note_domain) and not _domain_has_signal(note_domain, query_text)


def note_domain_score(title: str, content: str, query_text: str, note_path: str = "") -> float:
    # 후보 노트가 특정 도메인 전용 아카이브 소속이면, 쿼리에 그 도메인 고유 키워드가
    # 실제로 있어야만 채택한다. 일반 단어 하나가 우연히 겹쳤다는 이유만으로(예: 무관한
    # 사내 회의에서 "양자컴퓨터"라는 말이 스쳐 지나갔다는 것) 전혀 다른 도메인 아카이브의
    # 노트가 "관련 노트"로 끌려 들어와 LLM이 무관한 내용을 회의록에 섞어버리는 컨텍스트
    # 오염을 막기 위한 하드 게이트.
    if is_domain_mismatched(note_path, query_text):
        return 0.0

    hay = normalize_domain_text(f"{title} {content}")[:4000]
    q = normalize_domain_text(query_text)
    terms = keyword_terms(q)[:12]
    score = 0.0
    for t in terms:
        if norm_title(t) and norm_title(t) in norm_title(hay):
            score += 1.0
    for marker in _domain_relevance_markers():
        if marker.lower() in q.lower() and marker.lower() in hay.lower():
            score += 2.0
    return score


def detect_query_domain(text: str) -> str:
    """검색 쿼리/메모/질문 텍스트에서 obsidian.meeting_categories 카테고리 키를 자동 감지한다.
    도메인 모드(mode="domain", 전용 아카이브가 있는 양자/PhysicalAI 등)든 폴더 모드
    (mode="folder", 00_Meetings 하위 폴더인 팀회의/외부회의 등)든 키워드 매칭 점수가 가장
    높은 카테고리를 반환한다. 매칭 없으면 빈 문자열 — 호출자는 필터 없이(볼트 전체) 검색해야 한다."""
    categories = _c("obsidian.meeting_categories", {}) or {}
    if not categories:
        return ""
    hay = (text or "").lower()
    scores: Dict[str, int] = {}
    for key, entry in categories.items():
        kws = entry.get("keywords", []) if isinstance(entry, dict) else []
        if not isinstance(kws, list):
            continue
        scores[key] = sum(1 for kw in kws if str(kw).strip().lower() in hay)
    matched = [k for k, v in scores.items() if v > 0]
    return max(matched, key=lambda k: scores[k]) if matched else ""


def domain_search_prefixes(category: str) -> List[str]:
    """탐지된 카테고리의 검색 스코프 + 항상 공유되는 참조노트 폴더.
    도메인 모드 → 전용 아카이브 경로, 폴더 모드 → 00_Meetings 하위 카테고리 폴더.
    category가 비어있거나 알 수 없으면 빈 리스트(=필터 없음, 볼트 전체 검색)."""
    if not category:
        return []
    categories = _c("obsidian.meeting_categories", {}) or {}
    entry = categories.get(category, {})
    if not isinstance(entry, dict):
        return []
    refs = str(_c("obsidian.refs_subdir", "01_References") or "01_References")
    mode = entry.get("mode", "")
    if mode == "domain":
        domain_path = _domain_archive_paths().get(category, "")
        return [domain_path, refs] if domain_path else []
    if mode == "folder":
        folder = str(entry.get("folder", "") or "")
        return [folder, refs] if folder else []
    return []


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


def load_obsidian_client(project: str = ""):
    """project를 넘기면 config.json의 obsidian.project 대신 이 값으로 연결한다 —
    CLI `--project` 플래그로 config를 고치지 않고 세션 단위로 다른 도메인
    (obsidian.project_domains 매핑)에 발행할 때 쓴다."""
    if not _c("obsidian.enabled", False):
        return None
    try:
        from meeting_minutes_app.wiki_core.obsidian import ObsidianClient
        obs = ObsidianClient.from_config(project_override=project)
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


def load_vault_client(project: str = ""):
    """볼트에 '쓰기·매칭'용 통합 클라이언트를 반환한다.

    REST(obsidian.enabled + ping 성공) 우선, 안 되면 노트 폴더(.md) 파일 클라이언트로
    폴백, 둘 다 없으면 None. '어떤 볼트 클라이언트를 쓸지' 선택 로직을 한 곳으로 모아
    publish/prep-brief/계획매칭이 REST·폴더 어느 쪽이든 동일 코드로 동작하게 한다
    (이원화 방지 + 폴더-only 기능 동등). load_obsidian_client(REST 전용)을 재사용하므로
    REST 판정 로직도 단일 소스다. 저장은 항상 이 한 클라이언트로만 이뤄져 이중 저장은 없다."""
    obs = load_obsidian_client(project)  # REST(obsidian.enabled 게이트 + ping)
    if obs is not None:
        return obs
    try:
        from meeting_minutes_app.wiki_core.obsidian_fs import FilesystemObsidianClient
        return FilesystemObsidianClient.from_config(project_override=project)
    except Exception:
        return None


def _norm_note_title(text: str) -> str:
    """노트 제목 정규화(공백/구분자/대소문자 무시) — 계층 간 중복 제거용."""
    import re as _re
    return _re.sub(r"[\s_\-./\\]+", "", (text or "").lower())


def _title_already_in(note_title: str, titles: list) -> bool:
    """정규화 기준으로 이미 담긴 제목인지(프론트매터 title≠파일명이어도 같은 노트로 인식)."""
    nt = _norm_note_title(note_title)
    return any(_norm_note_title(t) == nt for t in titles)


def search_related_notes_rest(
    obs,
    *,
    title: str = "",
    topic: str = "",
    search_text: str = "",
    limit: int = 5,
    return_paths: bool = False,
) -> List[Any]:
    """Search Obsidian REST with conservative current-note filtering.

    return_paths=True면 [(title, vault_relative_path), ...]를 반환한다 —
    호출부가 note_domain_score()에 path를 넘겨 도메인 오염 게이트를 적용할 수 있도록.
    기본(False)은 기존 호출부와의 하위호환을 위해 title 문자열 리스트만 반환한다."""
    current_norm = norm_title(title)
    terms = keyword_terms(" ".join([title or "", topic or "", search_text or ""]))
    queries: List[str] = []
    for q in (title, topic, search_text[:220], " ".join(terms[:6])):
        q = (q or "").strip()
        if q and q not in queries:
            queries.append(q[:500])

    ranked: Dict[str, float] = {}
    paths: Dict[str, str] = {}
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
                paths.setdefault(note_title, fname)
        except Exception:
            continue
    top = sorted(ranked.items(), key=lambda x: (-x[1], x[0]))[:limit]
    if return_paths:
        return [(t, paths.get(t, "")) for t, _ in top]
    return [t for t, _ in top]


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
        "새 회의에서 직접 언급되지 않은 내용은 '참고 배경'으로 구분하세요. "
        "⚠️ 아래 자료의 주제가 실제 스크립트 내용과 명백히 무관하면(예: 다른 프로젝트/도메인 얘기) "
        "완전히 무시하고 회의록에 절대 반영하지 마세요 — 검색 알고리즘이 우연히 겹치는 단어 때문에 "
        "무관한 자료를 가져왔을 수 있습니다.\n\n"
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
        "그대로 출력하지 마세요. 사실 확인과 배경 연결에만 사용하세요. "
        "⚠️ 주제가 실제 스크립트와 명백히 무관하면 완전히 무시하고 회의록에 반영하지 마세요.\n\n"
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
                if not content or note_domain_score(
                    note_title, content, query_for_score, note_path=hit.get("note_path", "")
                ) < 1.0:
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
            path_prefixes = domain_search_prefixes(detect_query_domain(query))
            # find_related()는 title만 반환해 도메인 게이트에 필요한 path 정보를 잃는다 —
            # 같은 필터(min_score 또는 임베딩 코사인 매치)를 여기서 재현해 path를 보존한다.
            for hit in indexer.search(query, limit=limit, path_prefixes=path_prefixes):
                if not (hit.get("score", 0.0) >= 0.05 or hit.get("cosine", 0.0) > 0.0):
                    continue
                note_title = hit.get("wikilink_title") or hit.get("title", "")
                if not note_title:
                    continue
                content = get_related_note_content(indexer, obs, note_title) or ""
                if note_domain_score(
                    note_title, content, query_for_score, note_path=hit.get("path", "")
                ) < 1.0:
                    continue
                if not _title_already_in(note_title, related_titles):
                    related_titles.append(note_title)
                    evidence.append({"note": note_title, "heading": None})
        if obs:
            for note_title, note_path in search_related_notes_rest(
                obs, title=title, topic=topic, search_text=search_text, limit=limit,
                return_paths=True,
            ):
                content = get_related_note_content(indexer, obs, note_title) or ""
                if note_domain_score(
                    note_title, content, query_for_score, note_path=note_path
                ) < 1.0:
                    continue
                if not _title_already_in(note_title, related_titles):
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
