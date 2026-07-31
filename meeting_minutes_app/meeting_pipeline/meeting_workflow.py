"""
meeting_workflow.py - shared meeting workflow helpers.

The project has several entrypoints: CLI batch, bat launchers, web upload,
realtime finalization, and Obsidian/audio ingestion. This module contains
small shared helpers that can be adopted incrementally without changing the
stable input-specific code paths.
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


from meeting_minutes_app.wiki_core.vault_retrieval import (
    segments_to_search_text,
    merge_memo_parts,
    norm_title,
    strip_frontmatter,
    keyword_terms,
    note_domain_score,
    build_obsidian_context_memo,
    build_related_notes_memo,
    # 재노출 — pipeline/realtime/finalize가 mw.load_*()로 호출한다.
    # (과거 이 재노출이 빠져 있어 mw.load_vault_indexer()가 AttributeError를
    # 일으켰고, 광범위한 except에 삼켜져 claim_verify가 조용히 스킵됐다.)
    load_vault_indexer,   # noqa: F401 — mw.load_*()로 재노출
    load_obsidian_client,  # noqa: F401
)
from meeting_minutes_app.meeting_pipeline.minutes_generation import _split_script_chunks


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


#: graph_expand_titles가 title→node를 찾을 때 시도할 타입 순서. "note"가 가장 흔한
#: 경우(회의/세미나 노트)라 먼저 시도하고, 참조 노트 제목 자신이 넘어온 경우
#: (backfill_from_vault가 참조 노트를 note 대신 이 타입들로 직접 upsert함)를 위해
#: person/organization/topic도 순서대로 시도한다.
_GRAPH_EXPAND_NODE_TYPES = ("note", "person", "organization", "topic")


def graph_expand_titles(titles: List[str], hop: int = 1, max_extra: int = 5) -> List[str]:
    """관련 노트 제목 목록을 Wiki Knowledge Graph로 1-hop 확장한다.

    각 제목을 그래프 노드로 조회하고(회의/세미나 노트면 `note` 타입, 참조 노트 제목
    자신이면 person/organization/topic 타입 — graph_sync.backfill_from_vault가
    참조 노트를 그 엔티티 타입으로 직접 upsert하므로), 연결된 person/organization/topic
    노드의 라벨을 추가로 반환한다(대부분 People/Organizations/Topics 폴더의 실제 노트
    제목과 일치하므로, build_related_notes_memo()가 그대로 본문을 찾아 주입할 수 있다 —
    일치하는 노트가 없는 라벨은 build_related_notes_memo()가 이미 조용히 건너뛴다).

    실제 그래프 위상에서는 엔티티끼리 직접 연결되지 않고 항상 `note -[:MENTIONED]->
    person/organization/topic`을 통해서만 연결된다. 즉 `note` 노드에서 시작하면 1-hop
    만에 엔티티에 닿지만, 참조 노트 제목(엔티티 노드) 자신에서 시작하면 1-hop은 그
    엔티티를 언급한 `note`들만 나오고(필터링되어 결과 0건), 다른 엔티티까지 가려면
    한 hop 더(entity → note → other entity) 필요하다 — 그래서 시작 노드가 `note`가
    아니면 유효 hop을 `max(hop, 2)`로 올린다.

    옵트인 기능 — `wiki_knowledge.graph_retrieval_expand_enabled`(기본 true)로 게이트한다.
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
            node = None
            node_type_found = None
            for node_type in _GRAPH_EXPAND_NODE_TYPES:
                key = graph_sync.resolve_canonical_key(node_type, title)
                node = graph_db.get_node_by_key(node_type, key)
                if node:
                    node_type_found = node_type
                    break
            if not node:
                continue
            effective_hop = hop if node_type_found == "note" else max(hop, 2)
            neighbors = graph_db.get_neighbors(node["id"], depth=effective_hop).get("neighbors", [])
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


def minutes_vault_context_enabled() -> bool:
    """회의록 **본문 생성 프롬프트**에 이전 노트 내용을 주입할지 (기본 False).

    회수(관련 노트 목록·근거 기록·사실 검증·registry·prep-brief)는 이 값과 무관하게
    계속 돌아간다. 이 값이 끄는 것은 "이전 회의 내용을 회의록 본문을 **쓰는 데**
    참고하게 할지" 하나뿐이다.

    기본을 False 로 둔 이유: 주입된 이전 노트는 회의록에 두 가지 방식으로 새어 들어왔다 —
    (1) 이번 회의에서 다뤄지지 않은 배경·경과가 '배경 및 진행 경과'로 서술되고,
    (2) 이전 회의의 결정·액션이 이번 회의 결정으로 승격됐다. 프롬프트에 "다뤄진 경우에만
    반영" 지시가 있어도 근거 블록이 본문 컨텍스트에 들어와 있으면 모델은 그것을 쓴다.
    회의록은 **그 회의에서 실제로 나온 말**의 기록이어야 하므로 기본은 주입하지 않는다.
    (사용자 본인이 적은 메모(`base_memo`)와 웹 리서치는 '이전 회의 내용'이 아니라 그대로 쓴다.)
    """
    return bool(_c("analysis.minutes_vault_context", False))


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
    inject_vault: Optional[bool] = None,
) -> Tuple[Optional[str], List[str], Dict[str, Any]]:
    """Build one shared generation memo for batch, realtime, and ingestion.

    Returns:
        (merged_memo, related_note_titles, flags)

    flags["evidence"]: [{"note": title, "heading": Optional[str]}] — 회수된 근거 목록.
    Personal Wiki frontmatter의 `evidence` 필드, wiki_context.json의 근거 기록에 재사용된다.

    inject_vault: 볼트에서 회수한 내용을 **회의록 생성 memo 에 실을지**. None 이면
    `minutes_vault_context_enabled()`(config `analysis.minutes_vault_context`, 기본 False).
    False 라도 회수·근거 기록·flags 는 그대로다 — 목록과 사실 검증은 계속 동작하고,
    본문 생성 프롬프트에서만 빠진다. flags 는 "무엇이 회수됐나"를 뜻하며(주입 여부와
    별개) 주입 여부는 flags["injected"] 로 따로 남긴다.

    This is the single place where local Obsidian Wiki context and optional
    online research are combined for LLM generation.
    """
    if inject_vault is None:
        inject_vault = minutes_vault_context_enabled()
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
            # 본문 주입이 꺼져 있으면 텍스트를 만들지 않는다 — 노트 본문 읽기가
            # 그만큼 줄고(노트당 최대 2000자 × 5건), 회수 결과는 그대로 남는다.
            if inject_vault:
                extra_memo = build_related_notes_memo(
                    indexer, obs, extra_titles,
                    max_chars_per_note=int(_c("wiki.context_max_chars", 2000) or 2000),
                )
            related_titles = related_titles + extra_titles
            evidence = evidence + [{"note": t, "heading": None} for t in extra_titles]
    #: 1·2차(볼트 검색)에서 노트를 하나라도 회수했나 — flags["wiki"] 판정용.
    #: 그래프 확장분은 아래에서 related_titles 에 더해지므로 그 전에 확정한다.
    vault_found = bool(related_titles)

    # 3차: 그래프 기반 확장 (옵트인, 기본 off) — 지금까지 모은 관련 노트를 그래프로
    # 1-hop 확장해 연결된 인물/조직/주제 노트를 추가로 끌어온다. 실패해도 위 결과에 영향 없음.
    graph_memo = ""
    graph_titles = graph_expand_titles(related_titles)
    if graph_titles:
        if inject_vault:
            graph_memo = build_related_notes_memo(
                indexer, obs, graph_titles,
                max_chars_per_note=int(_c("wiki.context_max_chars", 2000) or 2000),
            )
        related_titles = related_titles + graph_titles
        evidence = evidence + [{"note": t, "heading": None} for t in graph_titles]

    # Registry 컨텍스트: 이전 결정사항/미완료 액션.
    # 이전 회의의 결정·액션이 이번 회의 것으로 승격되던 경로라, 본문 주입이 꺼져 있으면
    # 조립하지 않는다(registry 자체의 적재·prep-brief 용도는 그대로 살아 있다).
    registry_memo = ""
    registry_found = False
    if inject_vault:
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
            registry_found = bool(registry_memo)
        except Exception:
            registry_memo = ""

    web_memo = build_online_research_memo(llm, title=title, topic=topic) if include_web else ""
    if inject_vault:
        merged = merge_memo_parts(base_memo, wiki_memo, extra_memo, graph_memo,
                                  registry_memo, web_memo)
    else:
        # 사용자 본인 메모 + 웹 리서치만 — 볼트(이전 회의) 내용은 싣지 않는다.
        merged = merge_memo_parts(base_memo, web_memo)

    _max_chars = int(_c("wiki_knowledge.max_context_chars", 12000) or 12000)
    if merged and len(merged) > _max_chars:
        merged = merged[:_max_chars].rstrip() + "\n\n...(Wiki 참고 자료 초과로 일부 생략)"

    return merged, related_titles, {
        # flags 는 "회수됐나"를 뜻한다 — 주입 여부는 아래 injected 로 따로 본다.
        # (memo 텍스트 유무로 판정하면 주입을 끈 순간 진단이 전부 X 로 보인다.)
        "wiki": vault_found,
        "graph": bool(graph_titles),
        "registry": registry_found,
        "web": bool(web_memo),
        "injected": bool(inject_vault),
        "evidence": evidence,
        # 주입된 관련 노트 수 — "wiki 근거 없이 생성됨" 감지용 (finalize에서 경고)
        "note_count": len(related_titles),
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

def _domain_keywords() -> List[str]:
    """wiki.domain_keywords — 주 지식 도메인(예: 양자컴퓨팅) 판정 키워드."""
    kws = _c("wiki.domain_keywords", []) or []
    if not isinstance(kws, list):
        return []
    return [str(k).strip().lower() for k in kws if str(k).strip()]


def _claim_in_domain_by_keywords(claim_text: str, keywords: Sequence[str],
                                 domain_kws: Sequence[str]) -> bool:
    """하드 키워드 매칭 — 빠르지만 인접·응용 개념(예: 양자 세미나의 '볼츠만 머신')을 놓친다."""
    hay = (claim_text + " " + " ".join(str(k) for k in keywords or [])).lower()
    return any(k in hay for k in domain_kws)


def _classify_domain_llm(claim_text: str, domain_kws: Sequence[str], llm,
                         topic: str = "") -> Optional[bool]:
    """하드 키워드 매칭이 놓친 경계 사례를 LLM으로 재분류한다.

    domain_keywords는 도메인의 '대표 예시'일 뿐 전체 어휘집이 아니다 — 문자열
    매칭만 쓰면 인접·응용 개념(양자 세미나에서의 '볼츠만 머신', '텐서네트워크' 등)이
    전부 도메인 외로 오분류된다. 이 함수는 그 키워드들이 속하는 더 넓은 분야를
    LLM이 스스로 추론해, 인접 개념까지 포함해 판정하게 한다.
    LLM 실패/미지정 시 None — 호출자는 하드매칭 결과(out)를 그대로 쓴다
    (품질을 낮추려고 검증을 건너뛰는 게 아니라, 판정에 실패했을 때만 보수적 기본값 유지).
    """
    if llm is None or not domain_kws:
        return None
    sample = ", ".join(domain_kws[:15])
    topic_line = f"세미나/회의 주제: {topic}\n" if topic else ""
    system = (
        "당신은 지식 도메인 분류기입니다.\n"
        "아래는 어떤 지식 도메인의 대표 키워드 예시입니다(전체 어휘가 아니라 예시일 뿐):\n"
        f"{sample}\n\n"
        "이 키워드들이 속하는 더 넓은 학문/기술 분야를 스스로 추론하세요. 그 분야의 핵심 개념뿐 "
        "아니라 인접 이론·응용·구성 기법(예: 양자컴퓨팅 분야라면 양자 머신러닝에 쓰이는 고전 "
        "신경망 구조, 최적화 기법 등)까지 '도메인 내'로 판단하세요. 반대로 일정·예산·행정처럼 "
        "그 분야와 무관한 일반 주제는 '도메인 외'로 판단하세요.\n"
        "출력은 오직 in 또는 out 한 단어만."
    )
    user = f"{topic_line}주장: {claim_text}"
    try:
        raw = (llm.chat(system, user, temp=0.0, max_tokens=5) or "").strip().lower()
        if raw.startswith("in"):
            return True
        if raw.startswith("out"):
            return False
    except Exception:
        pass
    return None


def _claim_in_domain(claim_text: str, keywords: Sequence[str], llm=None,
                     topic: str = "") -> bool:
    """주장이 주 지식 도메인(wiki.domain_keywords)에 속하는지 판정.

    domain_keywords 미설정(빈 목록) 시 True — 모든 주장을 vault 도메인으로
    취급해 기존 동작을 유지한다. in-domain은 vault/그래프 우선 검증,
    out-domain은 웹 검증으로 직행한다 (vault에 근거가 없으므로).

    하드 키워드 매칭이 "out"으로 판정한 경우, wiki.domain_classify_llm(기본 true)이
    켜져 있으면 LLM으로 한 번 더 확인해 인접 개념 오분류를 줄인다.
    """
    domain_kws = _domain_keywords()
    if not domain_kws:
        return True
    if _claim_in_domain_by_keywords(claim_text, keywords, domain_kws):
        return True
    if _c("wiki.domain_classify_llm", True):
        refined = _classify_domain_llm(claim_text, domain_kws, llm, topic=topic)
        if refined is not None:
            return refined
    return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  회의 자동 분류 라우팅 (obsidian.auto_route_enabled)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _meeting_categories() -> Dict[str, Dict[str, Any]]:
    """obsidian.meeting_categories — 회의 자동분류 카테고리 정의.

    각 카테고리가 자기 라우팅 모드를 직접 선언한다:
      {"양자": {"mode": "domain", "keywords": [...]},
       "팀회의": {"mode": "folder", "folder": "00_Meetings/팀회의", "keywords": [...]}}
    "domain" 모드는 obsidian.project_domains에도 같은 키가 등록돼 있어야 한다
    (meetings_path {project} 토큰 해석용 — 별개 메커니즘).
    mode 생략 시 "folder"로 취급, folder 생략 시 "00_Meetings/<키>"로 기본값.
    이 함수 하나로 카테고리의 존재·모드·저장폴더·키워드를 전부 판단해, 예전처럼
    category_keywords/project_domains 두 딕셔너리를 손으로 동기화하다 하나를
    깜빡해 잘못된 경로로 새는 문제를 구조적으로 없앤다."""
    raw = _c("obsidian.meeting_categories", {}) or {}
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for key, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        kws = entry.get("keywords", [])
        vals = [str(k).strip().lower() for k in kws if str(k).strip()] if isinstance(kws, list) else []
        mode = "domain" if entry.get("mode") == "domain" else "folder"
        folder = str(entry.get("folder", "") or f"00_Meetings/{key}")
        out[str(key)] = {"mode": mode, "folder": folder, "keywords": vals}
    return out


def _score_meeting_categories(text: str, categories: Dict[str, Dict[str, Any]]) -> Dict[str, int]:
    hay = text.lower()
    return {cat: sum(1 for kw in entry["keywords"] if kw in hay) for cat, entry in categories.items()}


def _classify_or_discover_category_llm(text: str, categories: Sequence[str],
                                       llm) -> Optional[Dict[str, Any]]:
    """키워드 매칭이 전부 0점일 때 LLM에게 (1) 기존 카테고리 중 하나를 고르거나
    (2) 반복될 만한 뚜렷한 새 주제라고 판단되면 새 카테고리(이름+키워드)를 제안하게 한다.

    반환: {"existing": "팀회의"} | {"new": {"name": "...", "keywords": [...]}} | None
    (None이면 호출자는 기본 카테고리(기타)를 쓴다 — 일회성/애매한 회의를 새 카테고리로
    남발하지 않기 위해 프롬프트에서 "일회성이면 기타"를 명시한다.)"""
    if llm is None:
        return None
    cat_list = ", ".join(categories) if categories else "(없음)"
    system = (
        "당신은 회의록 분류기입니다. 기존 카테고리 목록:\n"
        f"{cat_list}\n\n"
        "이 회의가 기존 카테고리 중 하나에 맞으면 그 이름만 정확히 목록 표기 그대로 출력하세요.\n"
        "기존 카테고리 어디에도 맞지 않고, 앞으로도 반복될 만한 뚜렷한 새 주제/프로젝트라고 "
        "판단되면 다음 형식으로만 출력하세요: NEW: <짧은 폴더명> | <키워드1>, <키워드2>, <키워드3>\n"
        "일회성이거나 애매한 회의면 '기타'라고 출력하세요."
    )
    try:
        raw = (llm.chat(system, text[:500], temp=0.0, max_tokens=40) or "").strip()
    except Exception:
        return None
    if raw.upper().startswith("NEW:"):
        body = raw[4:].strip()
        if "|" in body:
            name_part, kw_part = body.split("|", 1)
            name = name_part.strip().strip('"').strip("'")
            kws = [k.strip().lower() for k in kw_part.split(",") if k.strip()]
            if name and kws:
                return {"new": {"name": name, "keywords": kws}}
        return None
    for cat in categories:
        if cat in raw:
            return {"existing": cat}
    return None


def _register_new_category(name: str, keywords: List[str]) -> None:
    """LLM이 발견한 새 회의 주제를 obsidian.meeting_categories에 folder 모드로 등록해,
    다음부터는 같은 주제가 키워드 매칭만으로(LLM 호출 없이) 인식되게 한다. 새 카테고리는
    항상 folder 모드(00_Meetings/<이름>)로만 등록된다 — domain 모드(Archive 아카이브 구조)
    승격은 PhysicalAI 때처럼 사람이 project_domains에 수동으로 등록해야 한다.
    obsidian.auto_register_categories=false면 이번 회의 라우팅에만 쓰고 저장하지 않는다."""
    if not _c("obsidian.auto_register_categories", True) or not _cfg_ok:
        return
    try:
        current = dict(_c("obsidian.meeting_categories", {}) or {})
        current[name] = {"mode": "folder", "folder": f"00_Meetings/{name}", "keywords": keywords}
        _cfg.set_nested("obsidian.meeting_categories", current)
    except Exception:
        pass


def classify_meeting_route(title: str, topic: str = "", script_excerpt: str = "",
                           llm=None) -> Dict[str, str]:
    """회의 제목/주제/스크립트로 저장 경로를 자동 분류한다 (obsidian.auto_route_enabled=true 일 때만 호출).

    obsidian.meeting_categories에서 매칭된 카테고리가 mode="domain"이면
    {"mode": "domain", "project": <키>} — ObsidianClient.from_config(project_override=...)에 전달.
    mode="folder"(기본, 예: 팀회의/주간보고/외부회의, 또는 LLM이 새로 발견해 자동 등록한
    카테고리)면 {"mode": "folder", "output_folder": <folder>} —
    write_meeting_note(output_folder=...)에 전달.
    전부 매칭 안 되면 00_Meetings/기타로 기본 라우팅한다.

    domain/folder 모드는 각 카테고리 자신의 설정에서 직접 읽으며, 다른 딕셔너리
    (project_domains)와의 교차조회로 추론하지 않는다 — 그렇게 하면 category_keywords와
    project_domains를 손으로 동기화하다 하나를 깜빡했을 때 조용히 잘못된 경로로 새는
    문제가 반복됐다(2026-07: 백서온톨로지가 domain 모드로 잘못 분류돼 기존 00_Meetings/
    백서온톨로지 대신 새 아카이브 경로로 갈 뻔한 사례).
    """
    categories = _meeting_categories()
    text = f"{title} {topic} {script_excerpt}".strip()
    scores = _score_meeting_categories(text, categories)
    matched = [c for c in scores if scores[c] > 0]
    best_cat = max(matched, key=lambda c: scores[c]) if matched else ""
    if best_cat in categories:
        entry = categories[best_cat]
        if entry["mode"] == "domain":
            return {"mode": "domain", "project": best_cat}
        return {"mode": "folder", "output_folder": entry["folder"]}

    if _c("wiki_knowledge.category_classify_llm", True):
        decision = _classify_or_discover_category_llm(text, list(categories.keys()), llm)
        if decision:
            if "existing" in decision and decision["existing"] in categories:
                entry = categories[decision["existing"]]
                if entry["mode"] == "domain":
                    return {"mode": "domain", "project": decision["existing"]}
                return {"mode": "folder", "output_folder": entry["folder"]}
            if "new" in decision:
                new_cat = decision["new"]
                _register_new_category(new_cat["name"], new_cat["keywords"])
                # 새 카테고리는 _register_new_category()가 항상 folder 모드로 등록한다.
                return {"mode": "folder", "output_folder": f"00_Meetings/{new_cat['name']}"}
    return {"mode": "folder", "output_folder": "00_Meetings/기타"}


_DOC_TYPE_LABELS = ("meeting", "seminar", "lecture")


def classify_doc_type_llm(text: str, llm) -> str:
    """전사 내용을 보고 문서 유형(meeting/seminar/lecture)을 LLM으로 판단한다.

    파일명 키워드로 유형을 못 정했을 때만 호출한다(예:
    ingestion_pipeline._detect_type_from_filename()이 빈 문자열을 반환한 경우) —
    자동 녹음기 기본 파일명("20260707_143012.m4a")처럼 키워드가 없으면 내용과
    무관하게 무조건 기본값(meeting)으로 처리되던 공백을 메운다.
    LLM 실패/미지정/빈 텍스트 시 빈 문자열 — 호출자는 config.analysis.default_type을 쓴다."""
    if llm is None or not (text or "").strip():
        return ""
    system = (
        "당신은 회의/녹음 유형 분류기입니다. 다음 중 하나만 정확히 출력하세요: "
        "meeting(정기/실무 회의, 여러 참석자가 논의·결정), "
        "seminar(발표·세미나·웨비나, 한두 명이 청중 대상으로 설명·발표), "
        "lecture(강의·교육 세션).\n"
        "구분이 애매하면 meeting을 출력하세요. 출력은 단어 하나만."
    )
    try:
        raw = (llm.chat(system, text[:1500], temp=0.0, max_tokens=5) or "").strip().lower()
        for label in _DOC_TYPE_LABELS:
            if label in raw:
                return label
    except Exception:
        pass
    return ""


def _paper_verify_claim(claim: str, llm, topic: str = "") -> Optional[Dict]:
    """학술 논문 근거로 주장을 검증한다 (wiki.claim_paper_verify=true).

    arXiv·peer-reviewed 논문을 우선 근거로 요구하는 쿼리로 web_research를
    호출한다. vault 검증이 불확실/충돌인 도메인 내 기술 주장에 사용.
    """
    if llm is None:
        return None
    topic_hint = f"({topic}) " if topic else ""
    query = (
        f"{topic_hint}다음 주장을 학술 논문 근거로 검증하세요. "
        f"arXiv, peer-reviewed 저널, 주요 학회 논문을 우선 인용하고 "
        f"논문 제목·저자·연도를 명시하세요. "
        f"논문 근거를 찾지 못하면 '학술 근거 없음'이라고 답하세요: {claim}"
    )
    try:
        res = llm.web_research(query, max_uses=3, max_tokens=800)
        if isinstance(res, dict) and res.get("text"):
            return res
    except Exception:
        pass
    return None


def _save_out_domain_fact_note(obs, claim: str, web_res: Dict, current_title: str = "") -> None:
    """도메인 외 주장의 웹검증 결과를 01_References에 축적한다
    (wiki.out_domain_fact_notes=true).

    주 도메인이 아닌 주제는 vault에 지식이 없으므로, 웹으로 확인한 사실을
    참고 노트로 남겨 다음 회의부터는 vault 검색으로 로컬 검증이 가능해진다.
    이미 같은 이름의 노트가 있으면 새 웹검증 결과를 "추가 언급 기록"으로
    보강한다 (create_reference_note 내부 처리).
    """
    try:
        title = claim[:40].strip().rstrip(".")
        if not title:
            return
        desc = (
            "> 회의 사실검증(웹 검색)에서 자동 수집된 도메인 외 지식입니다. "
            "검토 후 필요 시 보완하세요.\n\n"
            f"**주장**: {claim}\n\n"
            f"**웹 검증 결과**: {str(web_res.get('text', ''))[:800]}\n"
        )
        obs.create_reference_note(
            title, desc,
            sources=(web_res.get("sources") or [])[:3],
            category="사실검증",
            mentioned_by=current_title,
        )
    except Exception:
        pass


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
    use_paper_verify = _c("wiki.claim_paper_verify", False)
    out_domain_web = _c("wiki.claim_web_verify_out_domain", True)
    fact_notes_on = _c("wiki.out_domain_fact_notes", False)

    def _attach_web(target: Dict) -> Optional[Dict]:
        web_res = _web_verify_claim(target["claim"], llm, topic=topic)
        if web_res:
            target["web_opinion"] = web_res.get("text", "")
            target["web_sources"] = web_res.get("sources", [])
            if web_res.get("source_warning"):
                target["web_source_warning"] = web_res.get("source_warning")
        return web_res

    def _attach_paper(target: Dict) -> None:
        paper_res = _paper_verify_claim(target["claim"], llm, topic=topic)
        if paper_res:
            target["paper_opinion"] = paper_res.get("text", "")
            target["paper_sources"] = paper_res.get("sources", [])

    results: List[Dict] = []
    for item in claims:
        claim_text = (item.get("claim") or "").strip()
        if not claim_text:
            continue
        keywords = item.get("keywords") or []

        # ── 도메인 라우팅: 주 도메인(예: 양자) 주장은 vault 우선,
        #    도메인 외 주장은 vault에 근거가 없으므로 웹 검증으로 직행 ──
        if not _claim_in_domain(claim_text, keywords, llm, topic=topic):
            result: Dict = {
                "claim": claim_text,
                "verdict": "unknown",
                "summary": "주 지식 도메인 외 주장 — vault 대신 웹 검색으로 확인",
                "evidence": "",
                "sources": [],
                "no_vault_data": True,
                "domain": "out",
            }
            if out_domain_web:
                web_res = _attach_web(result)
                # 확인된 사실을 참고 위키 노트로 축적 → 다음 회의부터 vault 검증 가능
                if web_res and fact_notes_on and obs is not None:
                    _save_out_domain_fact_note(obs, claim_text, web_res, current_title=current_title)
            results.append(result)
            continue

        vault_notes = _fetch_vault_notes_for_claim(
            claim_text, keywords, indexer, obs, current_title=current_title
        )
        if not vault_notes:
            # vault에서 관련 자료를 아예 찾지 못함
            result = {
                "claim": claim_text,
                "verdict": "unknown",
                "summary": "관련 자료를 vault에서 찾을 수 없어 검증 불가",
                "evidence": "",
                "sources": [],
                "no_vault_data": True,
                "domain": "in",
            }
            # 웹 전문가 의견/논문 검색 (no_vault_data일 때 특히 유용)
            if use_web_verify:
                _attach_web(result)
            if use_paper_verify:
                _attach_paper(result)
            results.append(result)
            continue

        verdict = _compare_claim_with_notes(claim_text, vault_notes, llm, topic=topic)
        verdict.setdefault("claim", claim_text)
        verdict["domain"] = "in"
        # 불확실·충돌 주장에 대해 웹/논문 보강 검색
        if verdict.get("verdict") in ("unknown", "conflict"):
            if use_web_verify:
                _attach_web(verdict)
            if use_paper_verify:
                _attach_paper(verdict)
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


_CLAIMS_SYSTEM_PROMPT = (
    "회의록에서 기존 지식베이스(vault)로 검증할 수 있는 '사실적 주장'을 추출하세요.\n"
    "목적: 언급된 기술·조직·제품·합의사항이 기존 지식과 일치/충돌하는지 확인.\n\n"
    "포함해야 할 주장:\n"
    "  - 특정 기술/제품/플랫폼의 기능·특성 (예: 'X는 양자회로 자동화 플랫폼')\n"
    "  - 조직·회사의 역할이나 관계 (예: 'A사는 B사의 파트너')\n"
    "  - 이전에 합의/체결했다고 언급된 사항 (예: 'A사와 NDA를 체결')\n"
    "  - 구체적 수치나 규모 (예: '참가팀 30팀', '예산 1억')\n\n"
    "제외해야 할 것:\n"
    "  - 이 회의의 날짜·장소 (vault로 검증 불가)\n"
    "  - 이 회의에서 새로 결정한 사항 (아직 vault에 없음)\n"
    "  - 순수 의견·계획·미래 목표\n\n"
    "출력: JSON 배열만 (코드블록/설명 없이)\n"
    '[{"claim":"주장 한 문장","keywords":["검색키1","검색키2"]}]'
)


def _extract_claims_chunk(minutes_chunk: str, llm, topic: str, max_claims: int) -> List[Dict]:
    """사실적 주장 추출 — 단일 청크(또는 전체 회의록)에 대해 LLM 1회 호출."""
    topic_line = f"회의 주제: {topic}\n" if topic else ""
    user = f"{topic_line}회의록:\n\n{minutes_chunk}"
    try:
        from meeting_minutes_app.meeting_pipeline.json_utils import parse_json_loose
        raw = (llm.chat(_CLAIMS_SYSTEM_PROMPT, user, temp=0.1, max_tokens=1200) or "").strip()
        arr = parse_json_loose(raw, expect="list", default=[])
        return [x for x in arr if isinstance(x, dict) and x.get("claim")][:max_claims]
    except Exception:
        return []


def _extract_claims(
    minutes: str, llm, topic: str = "", max_claims: int = 8
) -> List[Dict]:
    """회의록에서 검증 가능한 사실적 주장(수치/날짜/기술사실)을 추출한다.

    회의록이 발췌 한도(wiki.claim_source_max_chars)보다 길면 청크로 나눠 각각
    추출한 뒤, 앞부분에만 몰리지 않도록 청크 간 라운드로빈으로 max_claims까지 채운다.
    """
    _src_max = int(_c("wiki.claim_source_max_chars", 5000) or 5000)
    chunks = [minutes] if len(minutes) <= _src_max else _split_script_chunks(minutes, _src_max)
    per_chunk_cap = max(2, -(-max_claims // len(chunks)))  # ceil(max_claims / len(chunks))

    seen: set = set()
    per_chunk_results: List[List[Dict]] = []
    for chunk in chunks:
        claims = _extract_claims_chunk(chunk, llm, topic, per_chunk_cap)
        filtered = []
        for item in claims:
            key = re.sub(r"\s+", "", str(item.get("claim", ""))).lower()[:40]
            if key and key not in seen:
                seen.add(key)
                filtered.append(item)
        per_chunk_results.append(filtered)

    merged: List[Dict] = []
    while len(merged) < max_claims and any(per_chunk_results):
        for lst in per_chunk_results:
            if lst:
                merged.append(lst.pop(0))
                if len(merged) >= max_claims:
                    break
    return merged


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
                relevance = note_domain_score(title, content, query, note_path=hit.get("note_path", ""))
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
                relevance = note_domain_score(title, content, query, note_path=hit.get("path", ""))
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
                    relevance = note_domain_score(title, note_content, query, note_path=fname)
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

    # 머리말의 수위를 **실측에 맞춘다**. 판정은 "노트를 얼마나 잘 회수했나"에 종속되는데,
    # scripts/measure_retrieval_floor.py 로 실볼트(457노트)를 재 보면 전사에 대해 그
    # 전사 자신의 회의록조차 1위로 회수되는 비율이 0%(임베딩 중위 15위 · TF-IDF 중위
    # 88위)다. 즉 "관련 노트를 못 찾아서 unknown"인 경우와 "정말 근거가 없어서 unknown"인
    # 경우를 이 기능은 구분할 수 없다. 그래서 '검증'이 아니라 '대조'라고 쓰고, 확인됨도
    # 단정하지 않는다 — 사람이 열어 보게 만드는 것이 이 섹션의 실제 효용이다.
    # 제목은 publish 의 상수를 쓴다 — 제거 정규식과 갈라지면 재발행 때 중복된다.
    from meeting_minutes_app.meeting_pipeline.publish import FACT_SECTION_HEADING
    lines: List[str] = [
        f"{FACT_SECTION_HEADING}\n",
        "> 회의록의 주장을 기존 노트와 **자동으로 대조**한 결과입니다. 노트 검색이 "
        "관련 자료를 놓칠 수 있어, 아래 판정은 참고용이며 확정된 사실 검증이 아닙니다.\n",
    ]

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
            # '확인됨'이라고 쓰지 않는다 — 검증한 것이 아니라 '어긋나는 기록을 못 봤다'가
            # 사실이다(위 머리말의 실측 근거 참고). 표현이 판정보다 강하면 사용자가
            # 확인을 건너뛴다.
            lines.append(f"- ☑️ **[노트와 일치]** {claim}")
            if summary:
                lines.append(f"  - 판정: {summary}")
            if confidence:
                lines.append(f"  - 신뢰도: {confidence}")
            if evidence:
                lines.append(f"  - vault 근거: _{evidence}_")
            if src:
                lines.append(f"  - 출처: {src}")
        elif no_vault:
            if r.get("domain") == "out":
                lines.append(f"- 🌍 **[도메인 외]** {claim}")
                lines.append(f"  - 주 지식 도메인 외 주장 — 웹 검색으로 확인")
            else:
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

        # 웹 전문가 의견 (wiki.claim_web_verify / out-domain 라우팅 시)
        if web_opinion:
            lines.append(f"  - 🌐 전문가 의견: {web_opinion[:300]}")
            if web_src:
                lines.append(f"  - 웹 출처: {web_src}")
            elif r.get("web_source_warning"):
                lines.append(f"  - 웹 출처 상태: {r.get('web_source_warning')}")
            elif web_sources == []:
                lines.append("  - 웹 출처 상태: 검색/폴백 결과에 URL 출처 없음")

        # 학술 논문 근거 (wiki.claim_paper_verify=true 시)
        paper_opinion = r.get("paper_opinion", "")
        if paper_opinion:
            lines.append(f"  - 📄 논문 근거: {paper_opinion[:300]}")
            paper_src = " · ".join(
                f"[{s.get('title', '논문')}]({s.get('url', '')})"
                for s in (r.get("paper_sources") or [])[:3]
            )
            if paper_src:
                lines.append(f"  - 논문 출처: {paper_src}")

    lines.append("")
    return "\n".join(lines)
