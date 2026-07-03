"""
enrichment.py — 용어·인물·기업 추출 + 외부검색 보완
========================================================
회의록에서 주요 전문용어/기술명/인물/기업을 추출하고,
각 항목을 LLM 웹 검색으로 보완해 "용어·배경" 섹션과
Obsidian 참고 노트(01_References)를 생성합니다.

LLMClient(meeting_minutes.LLMClient)의 .chat() / .web_research() 를 사용.
ObsidianClient 는 선택(없으면 글로서리 텍스트만 생성, 백링크는 생략).

사용:
    from enrichment import enrich
    result = enrich(minutes_md, llm, obs=obsidian_client, topic="양자컴퓨팅")
    # result = {"glossary_md", "related_notes", "sources"}
"""

from __future__ import annotations

import re
import json
import logging
from typing import Optional, List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger("meeting_minutes")

# 외부검색 병렬 워커 수 (Anthropic 웹검색 동시 호출 — 과도하면 레이트리밋)
_MAX_WORKERS = 5

# 항목 종류별 메타
_CATEGORIES = [
    ("terms",  "용어·기술"),
    ("people", "인물"),
    ("orgs",   "기업·기관"),
]

_ALIASES = {
    "한빗": "한빛",
    "한빚": "한빛",
    "한빝": "한빛",
    "코롱베니트": "한빛솔루션",
    "코론베니트": "한빛솔루션",
    "한빗 커스텀 문제": "한빛 커스텀 트랙",
    "한빚 커스텀 문제": "한빛 커스텀 트랙",
    "퀀텀4 AI": "Quantum for AI",
    "AI4 퀀텀": "AI for Quantum",
}

_BAD_ENTITY_PATTERNS = (
    re.compile(r"코[롱론]\s*커스텀\s*문제"),
)


def normalize_entity_name(name: str) -> str:
    out = (name or "").strip()
    if re.search(r"코[오롱론]+\s*커스텀\s*문제", out):
        return "한빛 커스텀 트랙"
    for src, dst in _ALIASES.items():
        out = re.sub(re.escape(src), dst, out, flags=re.IGNORECASE)
    return out


def extract_entities(minutes: str, llm, topic: str = "",
                     max_chars: int = 6000) -> Dict[str, List[str]]:
    """회의록에서 용어/인물/기업을 추출해 dict 반환.
    {"terms": [...], "people": [...], "orgs": [...]}
    """
    topic_line = f"회의 주제: {topic}\n" if topic else ""
    system = (
        "당신은 회의·세미나 기록 분석가입니다.\n"
        "회의록에서 '배경 설명이 필요한' 핵심 항목만 골라 JSON으로 반환하세요.\n\n"
        "규칙:\n"
        "- terms: 전문용어·기술명·제품명 (일반 상식 단어 제외)\n"
        "- people: 언급된 인물명 (화자 레이블 'Speaker A' 등은 제외)\n"
        "- orgs: 기업·기관·단체명\n"
        "- 각 배열 최대 6개, 중요도 순\n"
        "- 확실하지 않으면 포함하지 말 것\n"
        "- 설명 없이 순수 JSON만 출력 (코드블록 금지)\n\n"
        '출력 형식: {"terms":["..."],"people":["..."],"orgs":["..."]}'
    )
    user = f"{topic_line}다음 회의록에서 항목을 추출하세요:\n\n{minutes[:max_chars]}"

    raw = ""
    try:
        raw = llm.chat(system, user, temp=0.1, max_tokens=1000) or ""
    except Exception as e:
        logger.warning(f"[enrichment] 엔티티 추출 호출 실패: {e}")
        return {"terms": [], "people": [], "orgs": []}

    data = _parse_json_object(raw)
    out: Dict[str, List[str]] = {"terms": [], "people": [], "orgs": []}
    for key in out:
        vals = data.get(key, []) if isinstance(data, dict) else []
        if isinstance(vals, list):
            # 문자열 정규화 + 중복 제거
            seen = set()
            for v in vals:
                name = (v if isinstance(v, str) else str(v)).strip()
                name = normalize_entity_name(name)
                if any(p.search(name) for p in _BAD_ENTITY_PATTERNS):
                    name = "한빛 커스텀 트랙"
                if name and name.lower() not in seen:
                    seen.add(name.lower())
                    out[key].append(name)
            out[key] = out[key][:6]
    return out


def enrich(minutes: str, llm, obs=None, topic: str = "",
           max_items: int = 8, presenter_name: str = "") -> Dict[str, Any]:
    """
    회의록을 보완: 엔티티 추출 → 외부검색 설명 → 글로서리/참고노트 구성.

    presenter_name: 발표자 이름 (동명이인 오검색 방지를 위해 인물 enrichment에서 제외)

    Returns:
      {
        "glossary_md": str,                 # "## 용어·배경" 본문
        "related_notes": [basename, ...],   # Obsidian 위키링크 대상(노트 생성된 것)
        "sources": [{"title","url"}, ...],  # 외부 출처(상위)
        "entity_count": int,
        "entities": {"terms":[...],"people":[...],"orgs":[...]},  # 추출 원본(재사용용)
      }
    """
    entities = extract_entities(minutes, llm, topic=topic)

    # 발표자 이름 토큰 (성만 있어도 제외 — 동명이인 오검색 방지)
    presenter_tokens = set(presenter_name.replace("교수", "").replace("박사", "")
                           .replace("님", "").split()) if presenter_name else set()

    # (category_label, name) 평탄화 — 중요도 순서 유지(terms→people→orgs)
    flat: List[tuple] = []
    for key, label in _CATEGORIES:
        for name in entities.get(key, []):
            # 발표자 본인은 enrichment에서 제외 (동명이인 오검색 위험)
            if label == "인물" and presenter_tokens:
                name_tokens = set(name.replace("교수", "").replace("박사", "")
                                  .replace("님", "").split())
                if name_tokens & presenter_tokens:
                    logger.info(f"[enrichment] 발표자 인물 제외(동명이인 방지): {name}")
                    continue
            flat.append((label, name))
    flat = flat[:max_items]

    if not flat:
        return {"glossary_md": "", "related_notes": [], "sources": [],
                "entity_count": 0, "entities": entities}

    # 각 항목을 병렬로 외부검색·노트생성 (웹검색이 항목당 수~십초라 직렬이면 느림).
    # 결과는 원래 순서(중요도순)로 재조립한다.
    workers = min(_MAX_WORKERS, len(flat))
    results: List[Optional[Dict[str, Any]]] = [None] * len(flat)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        fut_idx = {ex.submit(_research_one, label, name, llm, obs, topic): i
                   for i, (label, name) in enumerate(flat)}
        for fut in as_completed(fut_idx):
            i = fut_idx[fut]
            try:
                results[i] = fut.result()
            except Exception as e:
                logger.warning(f"[enrichment] 항목 처리 실패({flat[i][1]}): {e}")

    glossary_lines: List[str] = []
    related_notes: List[str] = []
    all_sources: List[Dict[str, str]] = []
    source_warnings: List[str] = []
    for r in results:
        if not r:
            continue
        glossary_lines.append(f"- **{r['name']}** ({r['label']}): {r['short']}")
        for s in r["sources"]:
            if s.get("url") and s not in all_sources:
                all_sources.append(s)
        if r.get("source_warning") and r.get("source_warning") not in source_warnings:
            source_warnings.append(r["source_warning"])
        if r["base"]:
            related_notes.append(r["base"])

    top_sources = all_sources[:8]
    web_sources_md = _build_web_sources_md(top_sources)
    if not web_sources_md and source_warnings:
        web_sources_md = (
            "## 웹 검색 추가 자료\n\n"
            "> 웹 리서치가 실행되었지만 검증 가능한 URL 출처가 반환되지 않았습니다.\n"
            + "\n".join(f"- {w}" for w in source_warnings[:3])
        )

    return {
        "glossary_md": "\n".join(glossary_lines),
        "web_sources_md": web_sources_md,
        "related_notes": related_notes,
        "sources": top_sources,
        "entity_count": len(glossary_lines),
        "entities": entities,
    }


_NO_INFO_PHRASES = (
    "해당 분야 학술 정보 없음", "확인하기 어렵습니다", "찾을 수 없습니다",
    "정보가 없습니다", "알 수 없습니다", "자세한 정보를 제공하기 어렵",
    "죄송합니다", "명확한 정보를 찾지 못했습니다", "정확한 용어에 대한",
)

_UNKNOWN_DESC = "확인 불가: 외부 검색에서 신뢰할 만한 설명을 찾지 못했습니다."


def _research_one(label: str, name: str, llm, obs, topic: str) -> Optional[Dict[str, Any]]:
    """단일 항목: 외부검색 설명 + (옵션)Obsidian 참고노트 생성. 워커 스레드에서 실행."""
    res = llm.web_research(_build_query(name, label, topic))
    desc = (res.get("text") or "").strip()
    if not desc:
        return None
    no_info = any(p in desc for p in _NO_INFO_PHRASES) and len(desc) < 400
    # 학술 정보를 찾지 못해 동명이인 언급 등 무관한 정보만 있는 경우 제외
    if label == "인물" and no_info:
        logger.info(f"[enrichment] 인물 정보 없음 → 제외: {name}")
        return None
    srcs = res.get("sources") or []
    short = _UNKNOWN_DESC if no_info else _first_sentences(desc, 2)
    base = None
    if obs is not None and not no_info:
        try:
            base = obs.create_reference_note(
                term=name, description=desc, sources=srcs, category=label,
            )
        except Exception as e:
            logger.warning(f"[enrichment] 참고노트 생성 실패({name}): {e}")
    return {"label": label, "name": name,
            "short": short, "sources": srcs, "base": base,
            "source_warning": res.get("source_warning", "")}


# ── 내부 유틸 ─────────────────────────────────────────────────

def _build_web_sources_md(sources: List[Dict[str, str]]) -> str:
    """웹 검색 출처 목록을 마크다운 섹션으로 변환."""
    if not sources:
        return ""
    lines = ["## 🌐 웹 검색 추가 자료\n",
             "> 아래 자료는 용어·기관 enrichment 웹 검색으로 수집된 외부 출처입니다.\n"]
    for s in sources:
        title = s.get("title") or s.get("url", "")
        url = s.get("url", "")
        if url:
            lines.append(f"- [{title}]({url})")
        elif title:
            lines.append(f"- {title}")
    return "\n".join(lines)
def _build_query(name: str, label: str, topic: str) -> str:
    name = normalize_entity_name(name)
    if name == "한빛 커스텀 트랙":
        return (
            "'한빛 커스텀 트랙'은 회의 맥락상 한빛/Hanbit 관련 해커톤 산업문제 트랙을 뜻합니다. "
            "Kolmogorov complexity나 수학의 콜모고로프 복잡도와 혼동하지 말고, "
            "회의 맥락에서 산업 문제 정의·데이터 제공·평가 기준 관점으로 한국어 설명을 작성하세요."
        )
    if label == "인물":
        domain = f"'{topic}' 분야" if topic else "이 세미나/회의"
        return (
            f"'{name}' 교수/연구자 — {domain}에서 활동하는 학자에 대해 설명해 주세요. "
            f"소속 기관, 전공 분야, 주요 연구 업적을 한국어로 간결히 설명하세요. "
            f"방송·연예·스포츠·문화예술·사업 등 학술 외 분야의 동명이인은 절대 언급하지 마세요. "
            f"학술·연구 분야 인물 정보를 찾을 수 없으면 '해당 분야 학술 정보 없음'이라고만 답하세요."
        )
    ctx = f" (회의 주제: {topic})" if topic else ""
    return (f"'{name}' — 이 {label}에 대해 설명해 주세요{ctx}. "
            f"무엇/누구이며 이 분야에서 왜 중요한지 한국어로 간결히.")


def _parse_json_object(raw: str) -> Dict[str, Any]:
    try:
        from json_utils import parse_json_loose
        return parse_json_loose(raw, expect="dict", default={}) or {}
    except ImportError:
        # json_utils 없을 때 최소 폴백
        try:
            obj = json.loads((raw or "").strip())
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}


def _first_sentences(text: str, n: int = 2) -> str:
    # 마크다운 헤더/구분선/강조/이모지성 기호 제거 후 첫 문장만
    text = re.sub(r"`{1,3}", "", text)
    text = re.sub(r"^\s*#{1,6}\s*", " ", text, flags=re.MULTILINE)  # 헤더
    text = re.sub(r"^\s*-{3,}\s*$", " ", text, flags=re.MULTILINE)   # 구분선
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)                      # 볼드
    text = re.sub(r"[#*_>`]", " ", text)                              # 잔여 기호
    text = re.sub(r"\s+", " ", text).strip()
    parts = re.split(r"(?<=[.!?。])\s+|(?<=다\.)\s+", text)
    out = " ".join(p for p in parts[:n] if p).strip()
    return out or text[:200]
