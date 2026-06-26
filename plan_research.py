"""
plan_research.py — 회의 '계획 노트' 사전 리서치
====================================================
status: planned 인 Obsidian 회의 노트에서, 사용자가 미리 적어둔
주제(topic) / 안건 / 메모 텍스트를 읽어 핵심 용어·인물·기관을 추출하고
LLM 웹검색으로 짧은 설명을 붙인 뒤, 노트의 '## 사전 조사' 섹션에
자동 리서치 블록을 채워 넣는다(원문은 보존, 자동 블록만 갱신).

enrichment.enrich() 를 그대로 재사용하며(=회의록 본문 대신 안건 텍스트 입력),
ObsidianClient.search_simple() 로 볼트 내 관련 노트도 함께 링크한다.

핵심 함수:
    research_planned_note(content, llm, obs=None) -> Optional[str]
        변경된 노트 전체 문자열 반환. 변경 불필요 시 None.
"""

from __future__ import annotations

import re
import hashlib
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

from obsidian import parse_frontmatter, build_frontmatter, safe_filename

logger = logging.getLogger("meeting_minutes")

# 자동 리서치 블록 경계 마커(재실행 시 이 블록만 교체)
_BEGIN = "<!-- 🔎 auto-research:begin -->"
_END = "<!-- 🔎 auto-research:end -->"
MARKER_BEGIN = _BEGIN   # 외부 모듈 공용
MARKER_END = _END
_SECTION = "사전 조사"   # 자동 블록을 넣을 섹션 헤더명


def strip_auto_block(text: str) -> str:
    """자동 리서치 블록(마커 사이)을 제거 — 외부 모듈에서도 재사용하는 공개 함수."""
    return _strip_auto_block(text)


def _strip_auto_block(text: str) -> str:
    """기존 자동 리서치 블록(마커 사이)을 제거."""
    pat = re.compile(re.escape(_BEGIN) + r".*?" + re.escape(_END) + r"\n?", re.DOTALL)
    return pat.sub("", text)


def _agenda_text(meta: Dict[str, Any], body: str) -> str:
    """리서치 입력으로 쓸 '사용자 작성' 텍스트.
    자동 블록은 제외하고, 제목/주제 + 본문(안건·메모·사전조사 사용자 메모)을 합친다."""
    clean_body = _strip_auto_block(body)
    # 마크다운 헤더/인용/빈 항목 기호는 가볍게 정리(엔티티 추출 품질용)
    parts = []
    title = meta.get("title", "")
    topic = meta.get("topic", "")
    if title:
        parts.append(str(title))
    if topic:
        parts.append(f"주제: {topic}")
    parts.append(clean_body)
    txt = "\n".join(parts)
    # 의미 없는 플레이스홀더 줄 제거(예: "-", "> (보고 전 준비 메모)")
    lines = []
    for ln in txt.splitlines():
        s = ln.strip()
        if s in ("", "-", "*"):
            continue
        if s.startswith(">") and len(s) <= 3:
            continue
        lines.append(ln)
    return "\n".join(lines).strip()


def _agenda_fingerprint(meta: Dict[str, Any], body: str) -> str:
    base = (meta.get("topic", "") or "") + "" + _agenda_text(meta, body)
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]


def _vault_related(obs, names: List[str], exclude_basename: str = "",
                   per_kw: int = 3, max_total: int = 8) -> List[str]:
    """추출된 키워드들로 볼트 내 기존 노트를 검색해 basename 목록 반환."""
    if obs is None:
        return []
    found: List[str] = []
    seen = set()
    for name in names:
        try:
            hits = obs.search_simple(name, limit=per_kw)
        except Exception:
            hits = []
        for h in hits:
            fn = (h.get("filename") or "").rsplit("/", 1)[-1]
            if fn.endswith(".md"):
                fn = fn[:-3]
            if not fn or fn == exclude_basename or fn in seen:
                continue
            seen.add(fn)
            found.append(fn)
            if len(found) >= max_total:
                return found
    return found


def build_research_md(enr: Dict[str, Any], related_notes: List[str]) -> str:
    """enrichment 결과 + 볼트 관련 노트 → '## 사전 조사' 자동 블록 마크다운."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [_BEGIN,
             f"> 🔎 회의 전 자동 리서치 · LLM 웹검색 기반 · {ts}", ""]
    glossary = (enr.get("glossary_md") or "").strip()
    if glossary:
        lines.append("**핵심 용어·인물·기관**")
        lines.append(glossary)
        lines.append("")
    if related_notes:
        lines.append("**볼트 내 관련 노트**")
        for b in related_notes:
            lines.append(f"- [[{b}]]")
        lines.append("")
    sources = enr.get("sources") or []
    if sources:
        lines.append("**외부 참고자료**")
        for s in sources:
            u = s.get("url", "")
            t = s.get("title", u) or u
            if u:
                lines.append(f"- [{t}]({u})")
        lines.append("")
    if not glossary and not related_notes and not sources:
        lines.append("> (자동 리서치에서 추출된 항목이 없습니다.)")
        lines.append("")
    lines.append(_END)
    return "\n".join(lines)


def _inject(body: str, research_md: str) -> str:
    """body의 '## 사전 조사' 섹션 끝에 자동 블록을 삽입(기존 자동 블록은 교체).
    섹션이 없으면 H1 다음(없으면 맨 앞)에 섹션과 함께 삽입."""
    body = _strip_auto_block(body).rstrip() + "\n"
    # 섹션 헤더 탐색 (## 사전 조사)
    hdr = re.compile(r"^##\s+" + re.escape(_SECTION) + r"\s*$", re.MULTILINE)
    m = hdr.search(body)
    if m:
        # 이 섹션의 끝 = 다음 '## ' 헤더 직전, 없으면 EOF
        nxt = re.compile(r"^##\s+", re.MULTILINE)
        n = nxt.search(body, m.end())
        insert_at = n.start() if n else len(body)
        seg = body[:insert_at].rstrip() + "\n\n" + research_md + "\n\n"
        return seg + body[insert_at:]
    # 섹션이 없으면 H1 뒤에 새로 만든다
    h1 = re.compile(r"^#\s+.*$", re.MULTILINE)
    mh = h1.search(body)
    block = f"## {_SECTION}\n\n{research_md}\n"
    if mh:
        pos = mh.end()
        return body[:pos] + "\n\n" + block + body[pos:].lstrip("\n")
    return block + "\n" + body


def research_planned_note(content: str, llm, obs=None,
                          force: bool = False) -> Optional[str]:
    """계획 노트 문자열을 받아 사전 리서치를 수행하고 갱신된 노트 문자열 반환.
    - status: planned 가 아니면 None
    - 안건/주제 텍스트가 비어 있으면 None
    - 직전과 안건이 동일(research_hash 일치)하면 None (중복 실행 방지; force=True면 무시)
    """
    meta, body = parse_frontmatter(content)
    if str(meta.get("status", "")).strip().lower() != "planned":
        return None
    agenda = _agenda_text(meta, body)
    if not agenda.strip():
        return None
    fp = _agenda_fingerprint(meta, body)
    if not force and meta.get("research_hash") == fp:
        return None

    import enrichment
    topic = meta.get("topic", "") or meta.get("title", "")
    enr = enrichment.enrich(agenda, llm, obs=obs, topic=topic)

    # 추출된 항목명으로 볼트 내 관련 노트 검색 (enrich 가 돌려준 entities 재사용 — 추가 LLM 호출 없음)
    ents = enr.get("entities", {}) or {}
    names: List[str] = []
    for key in ("terms", "people", "orgs"):
        names.extend(ents.get(key, []))
    self_base = safe_filename(meta.get("title", "") or "")
    related = _vault_related(obs, names, exclude_basename=self_base)
    # enrichment 가 만든 참고노트도 합침
    for b in (enr.get("related_notes") or []):
        if b not in related:
            related.append(b)

    research_md = build_research_md(enr, related)
    new_body = _inject(body, research_md)

    # 프론트매터에 research_hash / researched 갱신
    meta = dict(meta)
    meta["research_hash"] = fp
    meta["researched"] = datetime.now().isoformat(timespec="seconds")
    new_content = build_frontmatter(meta) + "\n\n" + new_body.lstrip("\n")
    return new_content
