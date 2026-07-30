"""
note_builder.py — Obsidian 노트 마크다운/frontmatter 조립 (순수 함수 모음)
===============================================
wiki_core/obsidian.py의 ObsidianClient가 갖고 있던 노트 포맷팅 로직을 분리.
HTTP/ObsidianClient 의존성이 없으며, 구조화된 데이터를 받아 마크다운 문자열을 반환한다.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


def _yaml_escape(v: str) -> str:
    """YAML 스칼라 값에 안전하도록 따옴표 처리."""
    v = str(v).replace('"', '\\"')
    return f'"{v}"'


def build_frontmatter(meta: Dict[str, Any]) -> str:
    """dict → YAML frontmatter 블록. list 값은 YAML 시퀀스로."""
    lines = ["---"]
    for k, v in meta.items():
        if v is None or v == "" or v == []:
            continue
        if isinstance(v, (list, tuple)):
            lines.append(f"{k}:")
            for item in v:
                lines.append(f"  - {_yaml_escape(item)}")
        elif isinstance(v, bool):
            lines.append(f"{k}: {'true' if v else 'false'}")
        elif isinstance(v, (int, float)):
            lines.append(f"{k}: {v}")
        else:
            lines.append(f"{k}: {_yaml_escape(v)}")
    lines.append("---")
    return "\n".join(lines)


# Obsidian/OS 양쪽에서 안전하지 않은 문자
_UNSAFE = re.compile(r'[\\/:*?"<>|#^\[\]]')


def safe_filename(name: str, max_len: int = 80) -> str:
    """노트 파일명으로 안전한 문자열로 정리(확장자 제외)."""
    name = (name or "").strip()
    name = _UNSAFE.sub(" ", name)
    name = re.sub(r"\s+", " ", name).strip()
    if not name:
        name = "untitled"
    return name[:max_len].strip()


def meeting_note_basename(title: str, file_date: str = "",
                          fallback_label: str = "회의록") -> str:
    """회의록/전사 노트의 파일명(확장자 제외)을 만든다 — **이 규칙의 유일한 소스**.

    `{YYMMDD} {제목}` 이되 두 가지를 지킨다:

    1. **제목이 이미 날짜로 시작하면 접두를 붙이지 않는다.** 제목은 오디오 파일명에서
       오는 경우가 많아(`260627_5`, `2026-06-29 14.10_남우진교수`) 그대로 붙이면
       `260627 260627_5` 처럼 날짜가 두 번 들어간다.
    2. **날짜가 없으면 접두 없이 제목만 쓴다.** 예전에는 '오늘'로 폴백했는데, 그러면
       같은 오디오를 다른 날 재처리할 때 파일명이 달라져 같은 회의의 노트가 하나 더
       생겼다(put_note 는 덮어쓰기라 경로만 안정되면 중복이 안 생긴다).

    같은 조립이 obsidian.write_meeting_note / update_planned_note /
    ingestion_pipeline._expected_recording_note_paths 세 곳에 복제돼 있었다. 한쪽만
    고치면 '이미 처리됐나' 사전 검사와 실제 저장 경로가 어긋난다."""
    try:
        from meeting_minutes_app.meeting_pipeline.date_utils import leading_date_iso
    except ImportError:      # 부분 설치 방어 — 접두 중복만 놓치고 동작은 유지
        def leading_date_iso(_t: str) -> str:  # type: ignore[misc]
            return ""

    title = (title or "").strip()
    if not title:
        return safe_filename(f"{file_date} {fallback_label}".strip())
    if file_date and not leading_date_iso(title):
        return safe_filename(f"{file_date} {title}")
    return safe_filename(title)


def wikilink(basename: str, alias: Optional[str] = None) -> str:
    """[[노트]] 또는 [[노트|별칭]] 형식 위키링크."""
    base = safe_filename(basename)
    return f"[[{base}|{alias}]]" if alias and alias != base else f"[[{base}]]"


def build_references(
    related_notes: Optional[List[str]],
    external_refs: Optional[List[Dict[str, str]]],
) -> str:
    lines: List[str] = []
    for note in related_notes or []:
        if note:
            lines.append(f"- {wikilink(note)}")
    for ref in external_refs or []:
        t = ref.get("title", ref.get("url", ""))
        u = ref.get("url", "")
        if u:
            lines.append(f"- [{t}]({u})")
        elif t:
            lines.append(f"- {t}")
    return "\n".join(lines)


def render_transcript_note(meta: Dict[str, Any], heading_title: str, transcript_md: str) -> str:
    """전사 전용 노트(별도 파일) 본문 조립."""
    return "\n".join([
        build_frontmatter(meta),
        "",
        f"# {heading_title}\n",
        "## 전사 (Transcript)\n",
        transcript_md.strip(),
        "",
    ])


def build_meeting_note_content(
    meta: Dict[str, Any],
    title_heading: str,
    body_md: str,
    summary_md: str,
    glossary_md: str,
    actions_md: str,
    related_notes: Optional[List[str]],
    external_refs: Optional[List[Dict[str, str]]],
    web_sources_md: str,
    transcript_md: str,
    transcript_mode: str,
) -> str:
    """write_meeting_note 본문 조립."""
    parts = [build_frontmatter(meta), ""]
    parts.append(f"# {title_heading}\n")

    if summary_md.strip():
        parts.append("## 요약\n")
        parts.append(summary_md.strip() + "\n")
        parts.append("\n---\n")

    parts.append(body_md.strip() + "\n")

    if glossary_md.strip():
        parts.append("## 용어·배경\n")
        parts.append(glossary_md.strip() + "\n")

    if actions_md.strip():
        parts.append("## 액션 아이템\n")
        parts.append(actions_md.strip() + "\n")

    ref_lines = build_references(related_notes, external_refs)
    if ref_lines:
        parts.append("## 참고 자료\n")
        parts.append(ref_lines + "\n")

    if web_sources_md.strip():
        parts.append(web_sources_md.strip() + "\n")

    if transcript_md.strip() and transcript_mode == "append":
        parts.append("## 전사 (Transcript)\n")
        parts.append(transcript_md.strip() + "\n")

    return "\n".join(parts)


def build_planned_note_merge_content(
    meta: Dict[str, Any],
    pbody: str,
    summary_md: str,
    body_md: str,
    glossary_md: str,
    actions_md: str,
    related_notes: Optional[List[str]],
    external_refs: Optional[List[Dict[str, str]]],
    web_sources_md: str,
    transcript_md: str,
    transcript_mode: str,
    now_header: str,
) -> str:
    """update_planned_note 본문 조립(계획 노트에 병합)."""
    parts = [build_frontmatter(meta), ""]
    if pbody.lstrip().startswith("# "):
        parts.append(pbody + "\n")
    else:
        parts.append(f"# {meta['title']}\n")
        if pbody:
            parts.append(pbody + "\n")

    parts.append("\n---\n")
    parts.append(f"## 회의 기록 ({now_header})\n")
    if summary_md.strip():
        parts.append("### 한눈에 보는 요약\n")
        parts.append(summary_md.strip() + "\n")
    parts.append(body_md.strip() + "\n")
    if glossary_md.strip():
        parts.append("### 용어·배경\n")
        parts.append(glossary_md.strip() + "\n")
    if actions_md.strip():
        parts.append("### 액션 아이템\n")
        parts.append(actions_md.strip() + "\n")
    ref_lines = build_references(related_notes, external_refs)
    if ref_lines:
        parts.append("### 참고 자료\n")
        parts.append(ref_lines + "\n")

    if web_sources_md.strip():
        parts.append(web_sources_md.strip() + "\n")

    if transcript_md.strip() and transcript_mode == "append":
        parts.append("### 전사 (Transcript)\n")
        parts.append(transcript_md.strip() + "\n")

    return "\n".join(parts)


_REF_UPDATE_HEADER = "## 추가 언급 기록"


def build_reference_note_update(
    meta: Dict[str, Any],
    body: str,
    *,
    new_description: str,
    new_sources: Optional[List[Dict[str, str]]] = None,
    mentioned_by: str = "",
    now_iso: str = "",
    max_updates: int = 5,
) -> Optional[str]:
    """기존 참조 노트(create_reference_note가 만든 것)에 새 언급을 추가한다.

    원본 본문(제목/최초 설명/출처)은 그대로 보존하고, `## 추가 언급 기록` 섹션에
    날짜별 블록(### YYYY-MM-DD — 회의명)을 덧붙인다. 오래된 블록은 max_updates개를
    넘으면 가장 오래된 것부터 제거한다. 새 설명이 비어있거나 이미 본문에 있으면
    (변경 없음) None을 반환 — 호출자는 원본을 그대로 둔다.
    반환값은 frontmatter+body가 합쳐진 완성된 노트 콘텐츠 문자열.
    """
    new_description = (new_description or "").strip()
    if not new_description:
        return None
    body = body or ""
    if new_description[:200] in body:
        return None

    date_str = (now_iso or "")[:10] or "unknown-date"
    heading = f"### {date_str}" + (f" — {mentioned_by}" if mentioned_by else "")
    block_lines = [heading, "", new_description]
    if new_sources:
        block_lines.append("")
        block_lines.append("#### 출처")
        for s in new_sources:
            u = s.get("url", "")
            t = s.get("title", u)
            if u:
                block_lines.append(f"- [{t}]({u})")
    new_block = "\n".join(block_lines).strip()

    if _REF_UPDATE_HEADER in body:
        pre, _, rest = body.partition(_REF_UPDATE_HEADER)
        rest = rest.strip()
        blocks = [b.strip() for b in re.split(r"\n(?=### )", rest) if b.strip()] if rest else []
    else:
        pre = body
        blocks = []

    blocks.append(new_block)
    if len(blocks) > max_updates:
        blocks = blocks[-max_updates:]

    new_section = _REF_UPDATE_HEADER + "\n\n" + "\n\n".join(blocks)
    new_body = pre.rstrip() + "\n\n" + new_section + "\n"

    mentioned_in = meta.get("mentioned_in")
    mentioned_in = list(mentioned_in) if isinstance(mentioned_in, list) else ([mentioned_in] if mentioned_in else [])
    if mentioned_by and mentioned_by not in mentioned_in:
        mentioned_in.append(mentioned_by)
    try:
        mention_count = int(meta.get("mention_count") or 1)
    except (TypeError, ValueError):
        mention_count = 1
    mention_count += 1

    new_meta = dict(meta)
    new_meta["mentioned_in"] = mentioned_in
    new_meta["mention_count"] = mention_count
    new_meta["last_mentioned"] = now_iso or str(meta.get("last_mentioned", ""))

    return "\n".join([build_frontmatter(new_meta), "", new_body])


def build_recording_into_plan_content(
    pmeta: Dict[str, Any],
    pbody: str,
    rbody: str,
    now_header: str,
) -> str:
    """merge_recording_into_plan 본문 조립."""
    parts = [build_frontmatter(pmeta), ""]
    if pbody.strip():
        parts.append(pbody.strip() + "\n")
    parts.append("\n---\n")
    parts.append(f"## 회의 기록 ({now_header})\n")
    parts.append(rbody.strip() + "\n")
    return "\n".join(parts)
