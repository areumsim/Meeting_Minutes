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
                if name and name.lower() not in seen:
                    seen.add(name.lower())
                    out[key].append(name)
            out[key] = out[key][:6]
    return out


def enrich(minutes: str, llm, obs=None, topic: str = "",
           max_items: int = 8) -> Dict[str, Any]:
    """
    회의록을 보완: 엔티티 추출 → 외부검색 설명 → 글로서리/참고노트 구성.

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

    # (category_label, name) 평탄화 — 중요도 순서 유지(terms→people→orgs)
    flat: List[tuple] = []
    for key, label in _CATEGORIES:
        for name in entities.get(key, []):
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
    for r in results:
        if not r:
            continue
        glossary_lines.append(f"- **{r['name']}** ({r['label']}): {r['short']}")
        for s in r["sources"]:
            if s.get("url") and s not in all_sources:
                all_sources.append(s)
        if r["base"]:
            related_notes.append(r["base"])

    return {
        "glossary_md": "\n".join(glossary_lines),
        "related_notes": related_notes,
        "sources": all_sources[:8],
        "entity_count": len(glossary_lines),
        "entities": entities,
    }


def _research_one(label: str, name: str, llm, obs, topic: str) -> Optional[Dict[str, Any]]:
    """단일 항목: 외부검색 설명 + (옵션)Obsidian 참고노트 생성. 워커 스레드에서 실행."""
    res = llm.web_research(_build_query(name, label, topic))
    desc = (res.get("text") or "").strip()
    if not desc:
        return None
    srcs = res.get("sources") or []
    base = None
    if obs is not None:
        try:
            base = obs.create_reference_note(
                term=name, description=desc, sources=srcs, category=label,
            )
        except Exception as e:
            logger.warning(f"[enrichment] 참고노트 생성 실패({name}): {e}")
    return {"label": label, "name": name,
            "short": _first_sentences(desc, 2), "sources": srcs, "base": base}


# ── 내부 유틸 ─────────────────────────────────────────────────
def _build_query(name: str, label: str, topic: str) -> str:
    ctx = f" (회의 주제: {topic})" if topic else ""
    return (f"'{name}' — 이 {label}에 대해 설명해 주세요{ctx}. "
            f"무엇/누구이며 이 분야에서 왜 중요한지 한국어로 간결히.")


def _parse_json_object(raw: str) -> Dict[str, Any]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text.strip())
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                obj = json.loads(m.group())
                return obj if isinstance(obj, dict) else {}
            except json.JSONDecodeError:
                pass
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
