"""
wiki_knowledge.py — Wiki 지식 순환 모듈
=========================================
회의 전·중·후에 Obsidian Vault 지식을 활용하는 독립 모듈.

주요 기능:
  - prep-brief: 회의 준비 브리프 생성 (Vault 검색 + Registry 기반, LLM 없음)
  - Action Registry: 액션 항목 누적 관리
  - Decision Registry: 결정 사항 누적 관리
  - Wiki Update Proposal: Obsidian 노트 업데이트 후보 생성 (내부 API, 자동 반영 없음)

CLI:
    python run_meeting.py prep-brief --title "회의 제목" --topic "주제"
    python run_meeting.py prep-brief --title "제목" --no-obsidian --no-email
    python run_meeting.py prep-brief --title "제목" --reindex
"""

from __future__ import annotations

import json
import os
import re
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent      # meeting_minutes_app/wiki_core/
BASE_DIR = HERE.parent.parent                # project root

# UTF-8 재설정 — Windows CP949 환경 대응 (ingestion_pipeline.py 동일 패턴)
for _stream in (sys.stdout, sys.stderr):
    if getattr(_stream, "encoding", None) and _stream.encoding.lower() in ("cp949", "euc-kr", "ansi"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
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


def _feature_enabled(sub_key: Optional[str] = None) -> bool:
    """wiki_knowledge.enabled(전체) + 개별 기능 플래그를 함께 검사한다.

    두 플래그 모두 기본값 true — config에 키가 없으면 기존 동작 유지.
    """
    if not _c("wiki_knowledge.enabled", True):
        return False
    if sub_key is not None and not _c(f"wiki_knowledge.{sub_key}", True):
        return False
    return True


DATA_DIR = BASE_DIR / "data"
_output_cfg = str(_c("output_dir", "output") or "output")
OUTPUT_DIR = (BASE_DIR / _output_cfg).resolve()

# 논문/학술 자료 판정 키워드
_PAPER_TYPE_VALUES = {"paper", "논문", "seminar", "lecture", "세미나", "강의", "학술"}
_PAPER_TITLE_KEYWORDS = re.compile(
    r'(논문|paper|research|study|journal|review|proceedings|arXiv|학술|발표자료)',
    re.IGNORECASE,
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Registry 관리
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_EMPTY_ACTION_REG: Dict[str, Any] = {"version": "1.0", "actions": []}
_EMPTY_DECISION_REG: Dict[str, Any] = {"version": "1.0", "decisions": []}


def _atomic_write_json(path: Path, data: dict) -> None:
    """temp 파일 → os.replace() 원자적 쓰기 (파일 손상 방지)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def load_action_registry(path: Path) -> dict:
    """액션 Registry 로드. 없으면 빈 구조 자동 생성."""
    if not path.exists():
        try:
            _atomic_write_json(path, _EMPTY_ACTION_REG)
            print(f"[wiki] action_registry 초기화: {path}")
        except Exception as e:
            print(f"[wiki] action_registry 생성 실패 (무시): {e}")
        return dict(_EMPTY_ACTION_REG)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "actions" not in data:
            return dict(_EMPTY_ACTION_REG)
        return data
    except Exception as e:
        print(f"[wiki] action_registry 로드 실패 (무시): {e}")
        return dict(_EMPTY_ACTION_REG)


def load_decision_registry(path: Path) -> dict:
    """결정 Registry 로드. 없으면 빈 구조 자동 생성."""
    if not path.exists():
        try:
            _atomic_write_json(path, _EMPTY_DECISION_REG)
            print(f"[wiki] decision_registry 초기화: {path}")
        except Exception as e:
            print(f"[wiki] decision_registry 생성 실패 (무시): {e}")
        return dict(_EMPTY_DECISION_REG)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "decisions" not in data:
            return dict(_EMPTY_DECISION_REG)
        return data
    except Exception as e:
        print(f"[wiki] decision_registry 로드 실패 (무시): {e}")
        return dict(_EMPTY_DECISION_REG)


def _filter_actions_by_topic(
    actions: list,
    topic: str,
    attendees: Optional[List[str]] = None,
    limit: int = 10,
) -> list:
    """open 상태 액션을 topic 키워드 + 참석자 owner로 필터링·정렬.

    - topic 키워드 매칭: score +1~2
    - owner가 attendees 중 하나와 일치: score +3 (참석자 액션 우선 노출)
    - 매칭 없으면 open 전체 반환 (빈 Registry 경우 자연스러운 fallback)
    """
    open_actions = [a for a in actions if isinstance(a, dict) and a.get("status") == "open"]
    if not open_actions:
        return open_actions[:limit]

    topic_keywords = [w.lower() for w in re.split(r'\s+', (topic or "").strip()) if len(w) >= 2]
    attendee_norms = [re.sub(r'\s+', '', a.strip().lower()) for a in (attendees or []) if a.strip()]

    if not topic_keywords and not attendee_norms:
        return open_actions[:limit]

    scored: List[Tuple[int, dict]] = []
    for action in open_actions:
        score = 0
        # topic 매칭
        for t in action.get("topics", []):
            if any(kw in str(t).lower() for kw in topic_keywords):
                score += 2
        title_text = str(action.get("title", "")).lower()
        for kw in topic_keywords:
            if kw in title_text:
                score += 1
        # attendees 매칭 — owner 필드
        if attendee_norms:
            owner_norm = re.sub(r'\s+', '', str(action.get("owner", "")).lower())
            if any(an in owner_norm or owner_norm in an for an in attendee_norms if an):
                score += 3
        if score > 0:
            scored.append((score, action))

    if scored:
        return [a for _, a in sorted(scored, key=lambda x: -x[0])[:limit]]
    return open_actions[:limit]


def collect_open_actions(
    topic: str,
    attendees: Optional[List[str]] = None,
    limit: int = 10,
    data_dir: Optional[Path] = None,
) -> list:
    """미완료 액션아이템을 topic/attendees 기준으로 필터링해서 반환 (prep-brief·외부 모듈용)."""
    _dir = data_dir or DATA_DIR
    registry = load_action_registry(_dir / "action_registry.json")
    return _filter_actions_by_topic(registry.get("actions", []), topic, attendees, limit)


def collect_previous_decisions(
    topic: str,
    limit: int = 10,
    data_dir: Optional[Path] = None,
) -> list:
    """topic과 관련된 이전 결정사항을 registry에서 반환 (prep-brief·외부 모듈용).

    topic 키워드가 summary/topics에 포함되면 점수 우선 정렬.
    매칭 없으면 최근 N건 반환.
    """
    _dir = data_dir or DATA_DIR
    registry = load_decision_registry(_dir / "decision_registry.json")
    decisions = registry.get("decisions", [])
    if not decisions:
        return []

    topic_keywords = [w.lower() for w in re.split(r'\s+', (topic or "").strip()) if len(w) >= 2]
    if not topic_keywords:
        return sorted(decisions, key=lambda d: str(d.get("created_at", "")), reverse=True)[:limit]

    scored = []
    for d in decisions:
        score = 0
        summary = str(d.get("summary", "") or d.get("decision", "")).lower()
        for kw in topic_keywords:
            if kw in summary:
                score += 1
        for t in d.get("topics", []):
            if any(kw in str(t).lower() for kw in topic_keywords):
                score += 2
        scored.append((score, d))

    matched = [(s, d) for s, d in scored if s > 0]
    if matched:
        return [d for _, d in sorted(matched, key=lambda x: -x[0])[:limit]]
    return sorted(decisions, key=lambda d: str(d.get("created_at", "")), reverse=True)[:limit]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Registry 누적 (회의 후 자동 호출)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _norm_key(text: str) -> str:
    """중복 판정용 정규화: 소문자 + 공백·특수문자 제거."""
    return re.sub(r'[\s\W]+', '', text.lower())


def _extract_topic_keywords_from_title(title: str) -> List[str]:
    """회의 제목에서 의미 있는 키워드를 추출해 topics 필드에 사용한다."""
    words = re.split(r'[\s\-_/,.·]+', title.strip())
    return [w.lower() for w in words if len(w) >= 2 and not re.match(r'^\d+$', w)]


def update_action_registry_from_actions(
    actions_json_str: str,
    source_meeting: str,
    source_note: str = "",
    registry_path: Optional[Path] = None,
) -> int:
    """회의 후 extract_action_items() 결과를 action_registry.json에 누적한다.

    Args:
        actions_json_str: extract_action_items() 반환값 (JSON string, list of dicts).
                          각 항목: {assignee, task, deadline, context}
        source_meeting:   회의 제목 (dedup key + 표시용)
        source_note:      저장된 Obsidian 노트 경로 (vault-relative 또는 절대)
        registry_path:    기본값 DATA_DIR / "action_registry.json"

    Returns:
        새로 추가된 액션 수 (중복 제외)

    중복 판정: same source_meeting + normalized task text → 건너뜀
    """
    if not _feature_enabled("action_registry_enabled"):
        print("[wiki] action_registry 갱신 건너뜀 (config에서 비활성화)")
        return 0
    if not actions_json_str:
        return 0

    try:
        items = json.loads(actions_json_str)
        if not isinstance(items, list):
            return 0
    except Exception as e:
        print(f"[wiki] actions_json 파싱 실패 (무시): {e}")
        return 0

    if not items:
        return 0

    path = registry_path or (DATA_DIR / "action_registry.json")
    reg = load_action_registry(path)
    existing = reg.get("actions", [])

    # 중복 판정용 집합: (norm_meeting, norm_task)
    existing_keys = {
        (_norm_key(str(a.get("source_meeting", ""))), _norm_key(str(a.get("title", ""))))
        for a in existing
        if isinstance(a, dict)
    }

    now = datetime.now()
    yymmdd = now.strftime("%y%m%d")
    created_at = now.strftime("%Y-%m-%d")
    topics = _extract_topic_keywords_from_title(source_meeting)
    norm_meeting = _norm_key(source_meeting)

    # initial_count를 루프 전에 캡처해 ID 번호가 연속되게 한다
    initial_count = len(existing)
    added = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        task = str(item.get("task", "")).strip()
        if not task:
            continue

        norm_task = _norm_key(task)
        if (norm_meeting, norm_task) in existing_keys:
            continue  # 이미 등록된 액션

        seq = initial_count + added + 1
        action_id = f"ACT-{yymmdd}-{seq:03d}"

        new_action: Dict[str, Any] = {
            "action_id": action_id,
            "title": task,
            "owner": item.get("assignee") or "",
            "due_date": item.get("deadline") or "",
            "status": "open",
            "context": str(item.get("context", "") or "").strip(),
            "source_meeting": source_meeting,
            "source_note": source_note,
            "created_at": created_at,
            "topics": topics,
        }
        existing.append(new_action)
        existing_keys.add((norm_meeting, norm_task))
        added += 1

    if added > 0:
        reg["actions"] = existing
        try:
            _atomic_write_json(path, reg)
            print(f"[wiki] action_registry 갱신: +{added}개 (합계 {len(existing)}개)")
        except Exception as e:
            print(f"[wiki] action_registry 저장 실패 (무시): {e}")
            return 0

    return added


def extract_decisions_from_minutes(minutes_text: str) -> List[str]:
    """회의록 텍스트에서 결정사항 목록을 파싱한다 (규칙 기반, LLM 없음).

    파싱 대상 섹션 헤더: 결정사항, 결정 사항, decisions, 확정
    반환: 각 불릿 항목 문자열 목록 (빈 목록 가능)
    """
    decision_pat = re.compile(
        r'^\s*#{1,4}\s*(?:결정\s*사항?|decisions?|확정)\b', re.IGNORECASE
    )
    next_section_pat = re.compile(r'^\s*#{1,4}\s+\S')

    decisions: List[str] = []
    in_section = False

    for line in minutes_text.splitlines():
        stripped = line.strip()
        if decision_pat.match(stripped):
            in_section = True
            continue
        if in_section:
            if next_section_pat.match(stripped):
                break
            if stripped.startswith("-") or stripped.startswith("*"):
                item = re.sub(r'^[-*]\s*', '', stripped).strip()
                if item:
                    decisions.append(item)

    return decisions


def update_decision_registry_from_minutes(
    decisions: List[str],
    source_meeting: str,
    source_note: str = "",
    registry_path: Optional[Path] = None,
) -> int:
    """회의 후 결정사항 목록을 decision_registry.json에 누적한다.

    Args:
        decisions:      결정사항 문자열 목록.
                        ingestion_pipeline._extract_sections()["decisions"] 또는
                        extract_decisions_from_minutes() 결과.
        source_meeting: 회의 제목
        source_note:    저장된 Obsidian 노트 경로
        registry_path:  기본값 DATA_DIR / "decision_registry.json"

    Returns:
        새로 추가된 결정 수 (중복 제외)

    중복 판정: same source_meeting + normalized summary → 건너뜀
    """
    if not _feature_enabled("decision_registry_enabled"):
        print("[wiki] decision_registry 갱신 건너뜀 (config에서 비활성화)")
        return 0
    if not decisions:
        return 0

    path = registry_path or (DATA_DIR / "decision_registry.json")
    reg = load_decision_registry(path)
    existing = reg.get("decisions", [])

    existing_keys = {
        (_norm_key(str(d.get("source_meeting", ""))), _norm_key(str(d.get("summary", ""))))
        for d in existing
        if isinstance(d, dict)
    }

    now = datetime.now()
    yymmdd = now.strftime("%y%m%d")
    created_at = now.strftime("%Y-%m-%d")
    topics = _extract_topic_keywords_from_title(source_meeting)
    norm_meeting = _norm_key(source_meeting)

    initial_count = len(existing)
    added = 0
    for decision_text in decisions:
        summary = decision_text.strip()
        if not summary:
            continue

        norm_summary = _norm_key(summary)
        if (norm_meeting, norm_summary) in existing_keys:
            continue

        seq = initial_count + added + 1
        decision_id = f"DEC-{yymmdd}-{seq:03d}"

        new_decision: Dict[str, Any] = {
            "decision_id": decision_id,
            "summary": summary,
            "source_meeting": source_meeting,
            "source_note": source_note,
            "status": "active",
            "created_at": created_at,
            "topics": topics,
        }
        existing.append(new_decision)
        existing_keys.add((norm_meeting, norm_summary))
        added += 1

    if added > 0:
        reg["decisions"] = existing
        try:
            _atomic_write_json(path, reg)
            print(f"[wiki] decision_registry 갱신: +{added}개 (합계 {len(existing)}개)")
        except Exception as e:
            print(f"[wiki] decision_registry 저장 실패 (무시): {e}")
            return 0

    return added


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Vault 검색 (논문/일반 분리)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _is_paper_note(note_type: str, title: str) -> bool:
    """Vault 노트가 논문/학술 자료인지 판정."""
    if note_type and note_type.lower() in _PAPER_TYPE_VALUES:
        return True
    if _PAPER_TITLE_KEYWORDS.search(title):
        return True
    return False


def _get_brief_related_notes(
    title: str,
    topic: str,
    indexer,
    obs,
    limit: int = 5,
) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]]]:
    """Vault에서 관련 노트를 검색하고 일반/논문으로 분류한다.

    Returns:
        (regular_notes, paper_notes)
        각 요소: (wiki_title, body_snippet_2000chars)

    전략:
        1. TF-IDF 인덱스 검색 → frontmatter type 으로 분류
        2. Obsidian REST 검색 → type 불명이므로 title 키워드로 분류
        3. norm_title 기반 중복 제거
    """
    try:
        from meeting_minutes_app.wiki_core.vault_retrieval import (
            search_related_notes_rest,
            get_related_note_content,
            strip_frontmatter,
            norm_title,
        )
    except ImportError as e:
        print(f"[wiki] meeting_workflow import 실패: {e}")
        return [], []

    regular: List[Tuple[str, str]] = []
    papers: List[Tuple[str, str]] = []
    seen_norms: set = set()

    def _add_note(wiki_title: str, note_type: str, content: str) -> None:
        nn = norm_title(wiki_title)
        if nn in seen_norms:
            return
        seen_norms.add(nn)
        body = strip_frontmatter(content).strip()[:2000]
        if _is_paper_note(note_type, wiki_title):
            papers.append((wiki_title, body))
        else:
            regular.append((wiki_title, body))

    query = " ".join(filter(None, [title, topic]))

    # 1) TF-IDF 인덱스 검색
    if indexer and indexer.is_built:
        try:
            results = indexer.search(query, limit=limit * 2)
            for r in results:
                if r.get("score", 0) < 0.02:
                    continue
                wiki_title = r.get("wikilink_title") or r.get("title", "")
                if not wiki_title:
                    continue
                note_meta = indexer._notes.get(r["path"], {})
                note_type = str(note_meta.get("type", ""))
                content = indexer.get_note_content(r["path"]) or ""
                _add_note(wiki_title, note_type, content)
                if len(regular) + len(papers) >= limit * 2:
                    break
        except Exception as e:
            print(f"[wiki] TF-IDF 검색 실패 (무시): {e}")

    # 2) Obsidian REST 검색
    if obs:
        try:
            rest_titles = search_related_notes_rest(
                obs, title=title, topic=topic, limit=limit
            )
            for wiki_title in rest_titles:
                nn = norm_title(wiki_title)
                if nn in seen_norms:
                    continue
                content = get_related_note_content(indexer, obs, wiki_title) or ""
                _add_note(wiki_title, "", content)
        except Exception as e:
            print(f"[wiki] REST 검색 실패 (무시): {e}")

    return regular[:limit], papers[:limit]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Prep Brief 포맷팅
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def build_prep_brief(
    title: str,
    topic: str,
    yymmdd: str,
    full_date: str,
    regular_notes: List[Tuple[str, str]],
    paper_notes: List[Tuple[str, str]],
    open_actions: List[dict],
    recent_decisions: List[dict],
    attendees: Optional[List[str]] = None,
) -> str:
    """회의 준비 브리프 마크다운을 생성한다 (LLM 없음)."""
    lines: List[str] = []
    attendees = [a.strip() for a in (attendees or []) if a.strip()]

    # Frontmatter
    safe_title_yaml = title.replace('"', "'")
    safe_topic_yaml = (topic or "").replace('"', "'")
    lines += [
        "---",
        f'title: "{safe_title_yaml} 준비브리프"',
        f"date: {full_date}",
        "type: prep-brief",
        f'topic: "{safe_topic_yaml}"',
    ]
    if attendees:
        lines.append(f"attendees: [{', '.join(attendees)}]")
    lines += [
        "tags: [준비브리프, 회의준비]",
        "---",
        "",
    ]

    # 헤더
    lines += [
        f"# 회의 준비 브리프: {title}",
        "",
    ]
    if topic:
        lines.append(f"- **주제**: {topic}")
    if attendees:
        lines.append(f"- **참석자**: {', '.join(attendees)}")
    lines.append(f"- **작성**: {full_date} ({yymmdd})")
    lines.append("")

    # 관련 Wiki 노트 (일반)
    lines.append("## 관련 Wiki 노트")
    if regular_notes:
        lines.append(", ".join(f"[[{t}]]" for t, _ in regular_notes))
    else:
        lines.append("관련 노트 없음 (Vault 인덱스 미연결 또는 검색 결과 없음)")
    lines.append("")

    # 관련 논문·학술자료 (있을 때만 섹션 출력)
    if paper_notes:
        lines.append("## 관련 논문·학술자료")
        lines.append(", ".join(f"[[{t}]]" for t, _ in paper_notes))
        lines.append("")

    # 관련 노트 요약
    if regular_notes:
        lines.append("## 관련 노트 요약")
        for note_title, body in regular_notes:
            lines.append(f"### [[{note_title}]]")
            lines.append(body.strip() if body.strip() else "(내용 없음)")
            lines.append("")

    # 논문 요약
    if paper_notes:
        lines.append("## 논문 요약")
        for note_title, body in paper_notes:
            lines.append(f"### [[{note_title}]]")
            lines.append(body.strip() if body.strip() else "(내용 없음)")
            lines.append("")

    # 진행 중인 액션
    lines.append("## 진행 중인 액션")
    if open_actions:
        lines.append("| # | 내용 | 담당자 | 마감일 | 상태 |")
        lines.append("|---|---|---|---|---|")
        for i, a in enumerate(open_actions, 1):
            content = str(a.get("title", "")).replace("|", "｜")
            owner = str(a.get("owner", "-")).replace("|", "｜")
            due = str(a.get("due_date", "-") or "-")
            status = str(a.get("status", "open"))
            lines.append(f"| {i} | {content} | {owner} | {due} | {status} |")
    else:
        lines.append("현재 등록된 액션 없음")
    lines.append("")

    # 관련 결정 사항
    lines.append("## 관련 결정 사항")
    if recent_decisions:
        for d in recent_decisions:
            summary = str(d.get("summary", "")).strip()
            source = str(d.get("source_meeting", "")).strip()
            created = str(d.get("created_at", ""))[:10]
            if summary:
                src_info = f" _(출처: {source}, {created})_" if source else f" _({created})_"
                lines.append(f"- {summary}{src_info}")
    else:
        lines.append("등록된 결정 사항 없음")
    lines.append("")

    return "\n".join(lines)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Obsidian 저장
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _save_prep_brief_to_obsidian(
    obs, title: str, brief_md: str, yymmdd: str
) -> Optional[str]:
    """prep-brief를 Obsidian vault에 저장한다.

    config.obsidian.planning_path 기본값: "Planning/Prep Briefs"
    저장 경로: "{planning_path}/{yymmdd} {safe_title} 준비브리프.md"
    실패 시 None 반환 (output/ 파일은 이미 저장됨).
    """
    try:
        planning_folder = str(_c("obsidian.planning_path", "") or "Planning/Prep Briefs").strip("/")
        safe_t = re.sub(r'[\\/:*?"<>|]', "_", title)[:40].strip()
        note_name = f"{yymmdd} {safe_t} 준비브리프.md"
        note_path = f"{planning_folder}/{note_name}"
        if obs.put_note(note_path, brief_md):
            return note_path
        print(f"[wiki] Obsidian 저장 실패: {note_path}")
        return None
    except Exception as e:
        print(f"[wiki] Obsidian 저장 예외 (무시): {e}")
        return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  이메일/알림 발송
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _send_prep_brief_notification(
    title: str, brief_path: Path, obs_path: str = ""
) -> None:
    """config.notify.on_finish 설정에 따라 이메일/Slack/Teams 발송.
    채널이 없거나 실패해도 brief 생성 흐름을 막지 않는다.
    """
    try:
        from meeting_minutes_app.common.notifier import Notifier
        notify_cfg = _c("notify.on_finish", "") or ""
        if not notify_cfg:
            return
        notifier = Notifier.from_config({"notify": notify_cfg})
        if not notifier.has_channels:
            return
        results = notifier.send(
            title=f"[회의 준비 브리프] {title}",
            summary_path=str(brief_path),
            obsidian_path=obs_path,
        )
        failed = [r for r in results if not r.get("success")]
        if failed:
            channels = ", ".join(r["channel"] for r in failed)
            print(f"[wiki] 알림 발송 실패 ({channels}) — 무시")
        else:
            channels = ", ".join(r["channel"] for r in results)
            print(f"[wiki] 알림 발송 완료: {channels}")
    except Exception as e:
        print(f"[wiki] 알림 발송 예외 (무시): {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  인덱스 자동 업데이트
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _reindex_if_configured(indexer, force: bool = False) -> None:
    """config.indexing.auto_reindex_after_write=true 또는 force=True 시 재빌드.

    기존 이슈: obs.put_note() / write_recording_note() 후 vault_index.json 자동 갱신 없음.
    이 함수가 Obsidian 저장 후 호출되어 선택적으로 해결한다.
    """
    if not indexer:
        return
    if not force and not _c("indexing.auto_reindex_after_write", False):
        print("[wiki] 인덱스 갱신이 필요하면 `python run_meeting.py reindex` 실행")
        return
    try:
        print("[wiki] 인덱스 재빌드 중...")
        n = indexer.build(verbose=False)
        print(f"[wiki] 인덱스 갱신 완료: {n}개 노트")
    except Exception as e:
        print(f"[wiki] 인덱스 재빌드 실패 (무시): {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Wiki Context Package (내부 API)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _clean_context_items(items: List[str], *, query: str = "", limit: int = 10) -> List[str]:
    bad = {"", "-", "--", "미정", "없음", "n/a", "none", "null"}
    out: List[str] = []
    q_terms = {t.lower() for t in re.findall(r"[가-힣A-Za-z0-9]{2,}", query or "")}
    for item in items or []:
        s = str(item or "").strip()
        if s.lower() in bad:
            continue
        if q_terms:
            hay = s.lower()
            # Registry 항목은 현재 주제 단어와 하나도 맞지 않으면 제외한다.
            if not any(t in hay for t in q_terms):
                continue
        if s not in out:
            out.append(s)
        if len(out) >= limit:
            break
    return out


def build_wiki_context_package(
    related_titles: List[str],
    data_dir: Optional[Path] = None,
    *,
    metadata: Optional[dict] = None,
    related_details: Optional[List[dict]] = None,
    known_entities: Optional[List[str]] = None,
    glossary_terms: Optional[List[str]] = None,
    filter_query: str = "",
) -> dict:
    """회의록 생성 시 사용된 Wiki Context를 JSON 구조로 반환한다.

    data_dir 지정 시 action/decision registry에서 open_actions, previous_decisions도 채운다.
    wiki_knowledge.enabled=false 이면 빈 dict를 반환한다 (save_* 는 빈 입력을 건너뜀).
    """
    if not _feature_enabled():
        return {}
    pkg: dict = {
        "metadata": metadata or {},
        "related_notes": _clean_context_items(list(related_titles), limit=10),
        "related_note_details": related_details or [],
        "previous_decisions": [],
        "open_actions": [],
        "known_entities": _clean_context_items(list(known_entities or []), limit=20),
        "glossary_terms": _clean_context_items(list(glossary_terms or []), limit=20),
        "possible_conflicts": [],
    }
    if data_dir:
        try:
            reg = load_action_registry(data_dir / "action_registry.json")
            pkg["open_actions"] = _clean_context_items([
                a.get("title", "") for a in reg.get("actions", [])
                if a.get("status", "open") == "open" and a.get("title")
            ], query=filter_query, limit=10)
        except Exception:
            pass
        try:
            reg = load_decision_registry(data_dir / "decision_registry.json")
            pkg["previous_decisions"] = _clean_context_items([
                d.get("summary", "") for d in sorted(
                    reg.get("decisions", []),
                    key=lambda x: str(x.get("created_at", "")),
                    reverse=True,
                ) if d.get("summary")
            ], query=filter_query, limit=10)
        except Exception:
            pass
    return pkg


def save_wiki_context_package(pkg: dict, output_dir: Path) -> Optional[Path]:
    """wiki_context.json을 output 폴더에 저장한다. 빈 패키지(기능 비활성)면 건너뛴다."""
    if not pkg:
        return None
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "wiki_context.json"
    _atomic_write_json(path, pkg)
    return path


def format_wiki_context_for_prompt(package: dict, max_chars: int = 12000) -> str:
    """Wiki Context Package를 LLM 프롬프트용 마크다운으로 변환한다.

    섹션 우선순위: previous_decisions > open_actions > related_notes > known_entities > possible_conflicts
    max_chars 초과 시 낮은 우선순위 섹션부터 잘라낸다.
    """
    sections: List[Tuple[str, str]] = []

    decisions = package.get("previous_decisions", [])
    if decisions:
        sections.append(("[이전 결정사항]", "\n".join(f"- {d}" for d in decisions[:10] if d)))

    actions = package.get("open_actions", [])
    if actions:
        sections.append(("[미완료 액션]", "\n".join(f"- {a}" for a in actions[:10] if a)))

    notes = package.get("related_notes", [])
    if notes:
        sections.append(("[관련 노트]", "\n".join(f"- [[{n}]]" for n in notes[:10] if n)))

    entities = package.get("known_entities", [])
    if entities:
        sections.append(("[관련 용어/인물/조직]", "\n".join(f"- {e}" for e in entities[:10] if e)))

    conflicts = package.get("possible_conflicts", [])
    if conflicts:
        sections.append(("[기존 기록과 충돌 가능성]", "\n".join(f"- {c}" for c in conflicts[:5] if c)))

    parts: List[str] = []
    remaining = max_chars
    for header, body in sections:
        if remaining <= 0:
            break
        block = f"{header}\n{body}"
        if len(block) > remaining:
            block = block[:remaining].rstrip() + "\n...(생략)"
        parts.append(block)
        remaining -= len(block) + 1

    return "\n\n".join(parts)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Wiki Update Proposal (내부 API — CLI 없음)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _extract_action_items_text(minutes_text: str) -> List[str]:
    """회의록에서 액션아이템 텍스트 목록을 규칙 기반으로 파싱한다 (LLM 없음)."""
    section_pat = re.compile(
        r'^\s*#{1,4}\s*(?:액션\s*아이템|action\s*items?|할\s*일|to[\s-]?do)\b',
        re.IGNORECASE,
    )
    lines = minutes_text.splitlines()
    items: List[str] = []
    in_section = False
    for line in lines:
        if section_pat.match(line):
            in_section = True
            continue
        if in_section:
            if re.match(r'^\s*#{1,4}\s+\S', line):
                break
            bullet = re.match(r'^\s*[-*•]\s+(.+)', line)
            if bullet:
                text = bullet.group(1).strip()
                if text and not text.startswith('|') and len(text) > 3:
                    items.append(text)
    return items


def _build_proposal_v2_sections(claim_results: List[Dict], meeting_title: str) -> Dict[str, List[Dict]]:
    """claim_verify()의 구조화 결과에서 new_questions/new_claims/conflicts를 파생한다.

    새 LLM 호출 없음 — claim_verify가 이미 계산한 verdict/evidence/sources 재사용.
    """
    new_questions: List[Dict] = []
    new_claims: List[Dict] = []
    conflicts: List[Dict] = []
    for r in claim_results or []:
        claim = r.get("claim", "")
        if not claim:
            continue
        verdict = r.get("verdict", "unknown")
        if verdict == "unknown":
            new_questions.append({
                "text": claim,
                "source_meeting": meeting_title,
                "status": "open",
            })
        if verdict in ("unknown", "conflict"):
            new_claims.append({
                "claim": claim,
                "verdict": verdict,
                "evidence_notes": r.get("sources", []),
                "status": "unverified",
            })
        if verdict == "conflict":
            sources = r.get("sources", [])
            conflicts.append({
                "claim": claim,
                "existing_note": sources[0] if sources else None,
                "existing_excerpt": r.get("evidence", ""),
                "note": "회의 발언과 vault 기존 내용이 상충합니다 — 검토 필요",
            })
    return {"new_questions": new_questions, "new_claims": new_claims, "conflicts": conflicts}


def build_wiki_update_proposal(
    meeting_title: str,
    minutes_text: str,
    related_titles: List[str],
    llm=None,
    claim_results: Optional[List[Dict]] = None,
) -> dict:
    """회의록 처리 후 Obsidian 노트 업데이트 후보를 생성한다.

    llm 이 전달되고 config wiki_knowledge.proposal_llm_enabled=true 이면
    LLM으로 노트별 초안을 생성하고, 실패 시 규칙 기반으로 폴백한다.
    Obsidian에 자동 반영하지 않는다 — 사람이 검토 후 직접 반영.
    status: suggested (기본값, 자동 적용 금지)

    claim_results (claim_verify()가 반환하는 구조화 결과)가 전달되면 new_questions/
    new_claims/conflicts도 함께 생성한다 (Wiki Update Proposal v2, 추가 LLM 호출 없음).
    wiki_knowledge.enabled 또는 update_proposals_enabled=false 이면 빈 dict 반환.
    """
    if not _feature_enabled("update_proposals_enabled"):
        print("[wiki] Wiki Update Proposal 생성 건너뜀 (config에서 비활성화)")
        return {}
    now = datetime.now()
    yymmdd = now.strftime("%y%m%d")
    full_date = now.strftime("%Y-%m-%d")
    proposals = []

    decisions = extract_decisions_from_minutes(minutes_text)
    actions = _extract_action_items_text(minutes_text)

    use_llm = (
        llm is not None
        and _c("wiki_knowledge.proposal_llm_enabled", False)
        and bool(related_titles)
    )

    # 관련 노트별 회의 참조 추가 후보
    for title in related_titles[:5]:
        draft = None
        if use_llm:
            try:
                _sys = "당신은 Obsidian Wiki 편집 도우미입니다."
                _usr = (
                    f"회의명: {meeting_title}\n\n"
                    f"회의록(발췌):\n{minutes_text[:1500]}\n\n"
                    f"관련 노트: {title}\n\n"
                    f"위 회의 내용을 바탕으로 이 노트에 추가할 내용을 1-3줄의 마크다운 불릿으로 "
                    f"작성해주세요. 간결하고 구체적으로. "
                    f"예시: '- [{meeting_title}] ({full_date}) 주요 결정: ...'"
                )
                draft = llm.chat(_sys, _usr, temp=0.3, max_tokens=200)
            except Exception:
                draft = None
        if not draft:
            draft_parts = [f"- [{meeting_title}] ({full_date}) 회의에서 언급됨"]
            if decisions:
                draft_parts.append(f"  - 주요 결정: {decisions[0]}")
            draft = "\n".join(draft_parts)
        proposals.append({
            "target_note": title,
            "candidates": [title],
            "section": "관련 회의",
            "operation": "append",
            "draft_content": draft,
            "status": "suggested",
            "note": "자동 생성된 초안입니다. 내용을 확인하고 직접 편집해 주세요.",
        })

    # 결정사항 후보 (대상 노트 미확정)
    for decision in decisions[:3]:
        proposals.append({
            "target_note": None,
            "candidates": related_titles[:3],
            "section": "결정사항",
            "operation": "append",
            "draft_content": f"- {decision}",
            "status": "suggested",
            "note": f"결정사항 — '{meeting_title}' 회의에서 추출. 검토 후 적합한 노트에 반영하세요.",
        })

    # 액션아이템 후보 (대상 노트 미확정)
    for action in actions[:3]:
        proposals.append({
            "target_note": None,
            "candidates": related_titles[:3],
            "section": "액션아이템",
            "operation": "append",
            "draft_content": f"- {action}",
            "status": "suggested",
            "note": f"액션아이템 — '{meeting_title}' 회의에서 추출. 담당자 확인 후 반영하세요.",
        })

    v2_sections = _build_proposal_v2_sections(claim_results or [], meeting_title)

    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "yymmdd": yymmdd,
        "source_meeting": meeting_title,
        "proposals": proposals,
        "new_questions": v2_sections["new_questions"],
        "new_claims": v2_sections["new_claims"],
        "conflicts": v2_sections["conflicts"],
    }


def save_wiki_update_proposal(proposal: dict, output_dir: Path) -> Optional[Tuple[Path, Path]]:
    """Wiki Update Proposal을 JSON + Markdown으로 저장한다.

    파일명: "{yymmdd} {safe_title} wiki_proposal.{ext}"
    Obsidian 자동 반영 없음. 빈 proposal(기능 비활성)이면 건너뛴다.
    """
    if not proposal:
        return None
    output_dir.mkdir(parents=True, exist_ok=True)
    yymmdd = proposal.get("yymmdd", datetime.now().strftime("%y%m%d"))
    source = proposal.get("source_meeting", "unknown")
    safe_src = re.sub(r'[\\/:*?"<>|]', "_", source)[:40].strip()

    json_path = output_dir / f"{yymmdd} {safe_src} wiki_proposal.json"
    md_path = output_dir / f"{yymmdd} {safe_src} wiki_proposal.md"

    _atomic_write_json(json_path, proposal)

    proposals = proposal.get("proposals", [])
    md_lines = [
        f"# Wiki 업데이트 후보: {source}",
        f"- 생성일: {proposal.get('generated_at', '')}",
        "",
    ]
    if not proposals:
        md_lines.append("관련 노트가 없어 업데이트 후보를 생성하지 못했습니다.")
    else:
        for i, p in enumerate(proposals, 1):
            target = p.get("target_note") or "(미확정)"
            candidates = p.get("candidates") or []
            draft = p.get("draft_content", "")
            md_lines += [
                f"## 후보 {i}",
                f"- 대상 노트: {target}",
            ]
            if candidates:
                md_lines.append("- 후보 노트: " + ", ".join(f"[[{c}]]" for c in candidates))
            md_lines += [
                f"- 업데이트 유형: {p.get('operation', p.get('update_type', 'append'))}",
                f"- 상태: {p.get('status', 'suggested')}",
                "",
                "**초안 내용 (검토 후 반영):**",
                "",
                draft,
                "",
            ]

    new_questions = proposal.get("new_questions", [])
    if new_questions:
        md_lines += ["## 새 질문 후보", ""]
        for q in new_questions:
            md_lines.append(f"- {q.get('text', '')} ({q.get('status', 'open')})")
        md_lines.append("")

    new_claims = proposal.get("new_claims", [])
    if new_claims:
        md_lines += ["## 검증 필요 주장", ""]
        for c in new_claims:
            notes = ", ".join(f"[[{n}]]" for n in c.get("evidence_notes", []) or [])
            suffix = f" — 참고: {notes}" if notes else ""
            md_lines.append(f"- ({c.get('verdict', 'unknown')}) {c.get('claim', '')}{suffix}")
        md_lines.append("")

    conflicts = proposal.get("conflicts", [])
    if conflicts:
        md_lines += ["## 충돌 항목", ""]
        for c in conflicts:
            existing = f" vs [[{c['existing_note']}]]" if c.get("existing_note") else ""
            md_lines.append(f"- ⚠️ {c.get('claim', '')}{existing}")
            if c.get("existing_excerpt"):
                md_lines.append(f"  - 기존 근거: {c['existing_excerpt']}")
        md_lines.append("")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))

    return json_path, md_path


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  CLI — prep-brief
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(
        description="회의 준비 브리프 생성 — Vault 검색 + Registry 기반 (LLM 없음)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""예시:
  python run_meeting.py prep-brief --title "Q3 계획 회의" --topic "OKR 점검"
  python run_meeting.py prep-brief --title "PoC 검토" --attendees "김철수,이영희"
  python run_meeting.py prep-brief --title "AI 세미나 준비" --no-email
  python run_meeting.py prep-brief --title "주간 회의" --reindex
""",
    )
    ap.add_argument("--title", required=True, help="회의 제목")
    ap.add_argument("--topic", default="", help="회의 주제 (선택)")
    ap.add_argument("--attendees", default="", help="참석자 쉼표 구분 (선택) — 예: \"김철수,이영희\"")
    ap.add_argument("--no-obsidian", action="store_true", help="Obsidian 저장 건너뜀")
    ap.add_argument("--no-email", action="store_true", help="이메일/알림 발송 건너뜀")
    ap.add_argument("--reindex", action="store_true", help="완료 후 Vault 인덱스 강제 재빌드")
    ap.add_argument("--limit", type=int, default=5, help="관련 노트 최대 개수 (기본: 5)")
    args = ap.parse_args()

    if not _feature_enabled("prep_brief_enabled"):
        print("[wiki] prep-brief 비활성화됨 — config의 wiki_knowledge.enabled / prep_brief_enabled 를 확인하세요")
        return

    title: str = args.title.strip()
    topic: str = args.topic.strip()
    attendees_list: List[str] = [a.strip() for a in args.attendees.split(",") if a.strip()]
    now = datetime.now()
    yymmdd = now.strftime("%y%m%d")
    full_date = now.strftime("%Y-%m-%d")

    print(f"[wiki] 회의 준비 브리프 생성: {title}")
    if topic:
        print(f"[wiki] 주제: {topic}")
    if attendees_list:
        print(f"[wiki] 참석자: {', '.join(attendees_list)}")

    # meeting_workflow import (best-effort)
    try:
        from meeting_minutes_app.wiki_core.vault_retrieval import load_vault_indexer, load_obsidian_client
    except ImportError as e:
        print(f"[wiki] meeting_workflow 없음 — Vault 검색 건너뜀: {e}")
        load_vault_indexer = lambda: None  # noqa: E731
        load_obsidian_client = lambda: None  # noqa: E731

    indexer = None
    obs = None
    try:
        # Vault 연결 (실패해도 계속)
        try:
            indexer = load_vault_indexer()
            if indexer and not indexer.is_built:
                print("[wiki] Vault 인덱스 없음 — `run_meeting.py reindex` 실행 권장")
                indexer = None
        except Exception as e:
            print(f"[wiki] 인덱스 로드 실패 (무시): {e}")
            indexer = None

        try:
            if not args.no_obsidian:
                obs = load_obsidian_client()
        except Exception as e:
            print(f"[wiki] Obsidian 연결 실패 (무시): {e}")
            obs = None

        if not indexer and not obs:
            print("[wiki] Vault 연결 없음 — 관련 노트 검색 건너뜀")

        # 관련 노트 검색 (일반 + 논문 분리)
        regular_notes, paper_notes = _get_brief_related_notes(
            title=title,
            topic=topic,
            indexer=indexer,
            obs=obs,
            limit=args.limit,
        )
        total_notes = len(regular_notes) + len(paper_notes)
        if total_notes:
            print(f"[wiki] 관련 노트: 일반 {len(regular_notes)}개, 논문/학술 {len(paper_notes)}개")
        else:
            print("[wiki] 관련 노트 없음")

        # Registry 로드
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        action_reg = load_action_registry(DATA_DIR / "action_registry.json")
        decision_reg = load_decision_registry(DATA_DIR / "decision_registry.json")

        open_actions = _filter_actions_by_topic(
            action_reg.get("actions", []), topic, attendees=attendees_list, limit=10
        )
        recent_decisions = sorted(
            decision_reg.get("decisions", []),
            key=lambda d: str(d.get("created_at", "")),
            reverse=True,
        )[:10]

        # Prep Brief 생성
        brief_md = build_prep_brief(
            title=title,
            topic=topic,
            yymmdd=yymmdd,
            full_date=full_date,
            regular_notes=regular_notes,
            paper_notes=paper_notes,
            open_actions=open_actions,
            recent_decisions=recent_decisions,
            attendees=attendees_list,
        )

        # output/ 저장 (항상 먼저)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        safe_t = re.sub(r'[\\/:*?"<>|]', "_", title)[:40].strip()
        out_path = OUTPUT_DIR / f"{yymmdd} {safe_t} 준비브리프.md"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(brief_md)
        print(f"[wiki] 저장 완료: {out_path}")

        # Obsidian 저장
        obs_path = ""
        if obs and not args.no_obsidian:
            obs_path = _save_prep_brief_to_obsidian(obs, title, brief_md, yymmdd) or ""
            if obs_path:
                print(f"[wiki] Obsidian 저장 완료: {obs_path}")

        # 이메일/알림
        if not args.no_email:
            _send_prep_brief_notification(title, out_path, obs_path)

        # 인덱스 재빌드 (Obsidian 저장 후)
        _reindex_if_configured(indexer, force=args.reindex)

        # 결과 요약
        print("")
        print("━" * 50)
        print(f"  브리프 파일 : {out_path}")
        if obs_path:
            print(f"  Obsidian    : {obs_path}")
        print(f"  관련 노트   : 일반 {len(regular_notes)}, 논문 {len(paper_notes)}")
        print(f"  액션        : {len(open_actions)}건")
        print(f"  결정 사항   : {len(recent_decisions)}건")
        print("━" * 50)

    except Exception as e:
        print(f"[wiki] prep-brief 실패: {e}")
        traceback.print_exc()
        sys.exit(1)
    finally:
        if obs:
            try:
                obs.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
