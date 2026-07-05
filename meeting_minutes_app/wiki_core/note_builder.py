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


def wikilink(basename: str, alias: Optional[str] = None) -> str:
    """[[노트]] 또는 [[노트|별칭]] 형식 위키링크."""
    base = safe_filename(basename)
    return f"[[{base}|{alias}]]" if alias and alias != base else f"[[{base}]]"


def _has_section(content: str, *names: str) -> bool:
    """Markdown 본문에 특정 섹션 제목이 이미 있는지 확인."""
    if not content:
        return False
    for name in names:
        if re.search(rf"(?mi)^#{{1,6}}\s*{re.escape(name)}\b", content):
            return True
    return False


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


def build_recording_note_content(
    meta: Dict[str, Any],
    body_md: str,
    summary_md: str,
    key_points: Optional[List[str]],
    decisions: Optional[List[str]],
    actions_md: str,
    open_questions: Optional[List[str]],
    important_claims: Optional[List[str]],
    glossary_md: str,
    related_notes: Optional[List[str]],
    external_refs: Optional[List[Dict[str, str]]],
    web_sources_md: str,
    transcript_md: str,
    transcript_mode: str,
    transcript_note_path: str,
) -> str:
    """write_recording_note 본문 조립."""
    parts = [build_frontmatter(meta), ""]
    parts.append(f"# {meta['title']}\n")

    # Summary
    if summary_md.strip():
        parts.append("## 요약\n")
        parts.append(summary_md.strip() + "\n")
        parts.append("\n---\n")

    # Key Points. Do not duplicate sections already present in body_md.
    if key_points and not _has_section(body_md, "핵심 포인트", "주요 논의 내용"):
        parts.append("## 핵심 포인트\n")
        for kp in key_points:
            parts.append(f"- {kp.strip()}\n")
        parts.append("")

    # Main body (minutes)
    if body_md.strip():
        parts.append(body_md.strip() + "\n")

    # Decisions
    if decisions and not _has_section(body_md, "결정 사항", "결정 사항(합의/정리된 방향)"):
        parts.append("## 결정 사항\n")
        for d in decisions:
            parts.append(f"- {d.strip()}\n")
        parts.append("")

    # Action Items
    if actions_md.strip() and not _has_section(body_md, "Action Item", "액션 아이템"):
        parts.append("## 액션 아이템\n")
        parts.append(actions_md.strip() + "\n")

    # Open Questions
    if open_questions and not _has_section(body_md, "미해결 질문", "오픈 이슈"):
        parts.append("## 미해결 질문\n")
        for q in open_questions:
            parts.append(f"- {q.strip()}\n")
        parts.append("")

    # Important Claims
    if important_claims and not _has_section(body_md, "중요 주장", "중요 주장/검증 필요"):
        parts.append("## 중요 주장\n")
        for c in important_claims:
            parts.append(f"- {c.strip()}\n")
        parts.append("")

    # Glossary
    if glossary_md.strip():
        parts.append("## 용어·배경\n")
        parts.append(glossary_md.strip() + "\n")

    # Related Obsidian Notes
    if related_notes:
        parts.append("## 관련 노트\n")
        for rn in related_notes:
            clean = rn.strip().strip("[]")
            if clean:
                parts.append(f"- [[{clean}]]\n")
        parts.append("")

    # External refs
    ref_lines = build_references(None, external_refs)
    if ref_lines:
        parts.append("## 참고 자료\n")
        parts.append(ref_lines + "\n")

    if web_sources_md.strip():
        parts.append(web_sources_md.strip() + "\n")

    # Transcript
    if transcript_note_path:
        transcript_base = (
            transcript_note_path[:-3]
            if transcript_note_path.lower().endswith(".md")
            else transcript_note_path
        )
        parts.append("## 원문 전사\n")
        parts.append(f"- [[{transcript_base}|전체 STT 전사]]\n")
    elif transcript_md.strip() and transcript_mode == "append":
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
