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

# 데이터 베이스는 app_paths 단일 소스에서 파생한다.
# frozen 시 exe 옆 MeetingMinutesData/ (쓰기 가능), dev 시 저장소 루트.
# 과거엔 BASE_DIR = HERE.parent.parent (=저장소 루트)라 frozen 시 data/가
# 읽기전용 _MEIPASS로 들어가 재실행마다 소멸하는 버그가 있었다.
from meeting_minutes_app.common import app_paths as _paths
BASE_DIR = _paths.get_base_dir()

from meeting_minutes_app.common.console import force_utf8_console
force_utf8_console()

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


DATA_DIR = _paths.get_data_dir()
OUTPUT_DIR = _paths.get_output_dir()

# 논문/학술 자료 판정 키워드
_PAPER_TYPE_VALUES = {"paper", "논문", "seminar", "lecture", "세미나", "강의", "학술"}
_PAPER_TITLE_KEYWORDS = re.compile(
    r'(논문|paper|research|study|journal|review|proceedings|arXiv|학술|발표자료)',
    re.IGNORECASE,
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Registry 관리
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _empty_action_reg() -> Dict[str, Any]:
    """빈 registry 구조를 매번 새로 만든다.

    과거엔 모듈 전역 dict를 dict()로 얕은 복사해 반환했는데, 내부 리스트가
    전역과 공유돼 update_*()의 append가 전역을 오염시켰다 — 장기 실행
    프로세스(웹 백엔드)에서 세션 간 항목이 누수되는 버그."""
    return {"version": "1.0", "actions": []}


def _empty_decision_reg() -> Dict[str, Any]:
    return {"version": "1.0", "decisions": []}


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
            _atomic_write_json(path, _empty_action_reg())
            print(f"[wiki] action_registry 초기화: {path}")
        except Exception as e:
            print(f"[wiki] action_registry 생성 실패 (무시): {e}")
        return _empty_action_reg()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "actions" not in data:
            return _empty_action_reg()
        return data
    except Exception as e:
        print(f"[wiki] action_registry 로드 실패 (무시): {e}")
        return _empty_action_reg()


def load_decision_registry(path: Path) -> dict:
    """결정 Registry 로드. 없으면 빈 구조 자동 생성."""
    if not path.exists():
        try:
            _atomic_write_json(path, _empty_decision_reg())
            print(f"[wiki] decision_registry 초기화: {path}")
        except Exception as e:
            print(f"[wiki] decision_registry 생성 실패 (무시): {e}")
        return _empty_decision_reg()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "decisions" not in data:
            return _empty_decision_reg()
        return data
    except Exception as e:
        print(f"[wiki] decision_registry 로드 실패 (무시): {e}")
        return _empty_decision_reg()


def _filter_actions_by_topic(
    actions: list,
    topic: str,
    attendees: Optional[List[str]] = None,
    limit: int = 10,
    extra_keywords: Optional[List[str]] = None,
) -> list:
    """open 상태 액션을 topic 키워드 + 참석자 owner + (선택) 추가 키워드로 필터링·정렬.

    extra_keywords: 회의 전 메모(memo)에서 추출한 키워드 등, topic 문자열 밖의 추가 단서.

    - topic/추가 키워드 매칭: score +1~2
    - owner가 attendees 중 하나와 일치: score +3 (참석자 액션 우선 노출)
    - 필터 기준(topic/attendees/extra_keywords)이 아예 없으면 open 전체 반환
      (빈 Registry 경우 자연스러운 fallback).
    - 필터 기준은 있는데 매칭되는 액션이 하나도 없으면 **빈 목록**을 반환한다 —
      registry가 여러 프로젝트를 섞어 담고 있을 때, 무관한 다른 회의 액션을
      "매칭 없음"이라는 이유로 전부 보여주면 오히려 잡음이 된다.
    """
    open_actions = [a for a in actions if isinstance(a, dict) and a.get("status") == "open"]
    if not open_actions:
        return open_actions[:limit]

    topic_keywords = [w.lower() for w in re.split(r'\s+', (topic or "").strip()) if len(w) >= 2]
    topic_keywords += [str(k).lower() for k in (extra_keywords or []) if len(str(k)) >= 2]
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
            # owner_norm이 빈 문자열이면 "" in an이 항상 True가 되어 담당자 미상 액션이
            # 모든 참석자와 매칭된 것처럼 처리되는 버그 방지 — owner가 실제로 있을 때만 비교.
            if owner_norm and any(an in owner_norm or owner_norm in an for an in attendee_norms if an):
                score += 3
        if score > 0:
            scored.append((score, action))

    return [a for _, a in sorted(scored, key=lambda x: -x[0])[:limit]]


def _filter_decisions_by_topic(
    decisions: list,
    topic: str,
    limit: int = 10,
    extra_keywords: Optional[List[str]] = None,
) -> list:
    """결정사항을 topic 키워드 + (선택) 추가 키워드로 필터링, 최신순 정렬.

    필터 기준이 아예 없으면 최신순 전체(기존 동작)를 반환한다. 기준은 있는데
    매칭이 하나도 없으면 빈 목록을 반환한다 — `_filter_actions_by_topic`과 동일한
    이유(무관한 다른 프로젝트 결정사항을 브리프에 잡음으로 채우지 않기 위함).
    """
    sorted_all = sorted(decisions, key=lambda d: str(d.get("created_at", "")), reverse=True)
    topic_keywords = [w.lower() for w in re.split(r'\s+', (topic or "").strip()) if len(w) >= 2]
    topic_keywords += [str(k).lower() for k in (extra_keywords or []) if len(str(k)) >= 2]
    if not topic_keywords:
        return sorted_all[:limit]

    scored: List[Tuple[int, dict]] = []
    for d in sorted_all:
        score = 0
        for t in d.get("topics", []):
            if any(kw in str(t).lower() for kw in topic_keywords):
                score += 2
        summary_text = str(d.get("summary", "")).lower()
        for kw in topic_keywords:
            if kw in summary_text:
                score += 1
        if score > 0:
            scored.append((score, d))

    return [d for _, d in sorted(scored, key=lambda x: -x[0])[:limit]]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Registry 조회 — 공개 진입점 (prep-brief · 실시간 개입 공용)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#
# "지난 결정·미완료 액션을 주제로 걸러 N건" 은 두 곳이 필요로 한다:
#   1. 회의 준비 브리핑(`api/tools.py::prep_brief`)
#   2. 회의 중 개입 판정(`wiki_core.facilitation`) — 이전 회의와 어긋나는 발화를 짚으려면
#      그 재료가 실시간 경로에도 있어야 한다(예전엔 참조 0건이라 판정 자체가 불가능했다)
# 로드+필터를 호출부마다 인라인으로 쓰면 두 경로가 갈라진다 — 이 리포가 반복해서 대가를
# 치른 패턴이라(단가 표 4곳·노트 판정 2곳·논문 arm 폴더 매칭) 진입점을 여기 하나로 둔다.
# PRD_실시간관련정보 §6-4 "웹/CLI 공용 로직은 wiki_core 1곳" 과 같은 규율.


def recent_decisions_for(topic: str, limit: int = 5,
                         extra_keywords: Optional[List[str]] = None) -> list:
    """주제와 관련된 지난 결정 N건(최신순). 실패하면 빈 목록 — 호출부를 막지 않는다."""
    if limit <= 0:
        return []
    try:
        reg = load_decision_registry(DATA_DIR / "decision_registry.json")
        return _filter_decisions_by_topic(
            reg.get("decisions", []), topic, limit=limit,
            extra_keywords=extra_keywords)
    except Exception as e:
        print(f"[wiki] 지난 결정 조회 건너뜀: {e}")
        return []


def open_actions_for(topic: str, attendees: Optional[List[str]] = None,
                     limit: int = 5,
                     extra_keywords: Optional[List[str]] = None) -> list:
    """주제·참석자와 관련된 미완료(open) 액션 N건. 실패하면 빈 목록."""
    if limit <= 0:
        return []
    try:
        reg = load_action_registry(DATA_DIR / "action_registry.json")
        return _filter_actions_by_topic(
            reg.get("actions", []), topic, attendees=attendees, limit=limit,
            extra_keywords=extra_keywords)
    except Exception as e:
        print(f"[wiki] 미완료 액션 조회 건너뜀: {e}")
        return []


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Registry 누적 (회의 후 자동 호출)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _norm_key(text: str) -> str:
    """중복 판정용 정규화: 소문자 + 공백·특수문자·언더스코어 제거.

    주의: `_`는 \\w에 속해 [\\s\\W]로는 제거되지 않는다 — 과거 이 차이로
    "260627_5"와 "260627 5"가 서로 다른 회의로 중복 등록됐다
    (graph_sync.resolve_canonical_key와 같은 기준).
    """
    return re.sub(r'[\s\W_]+', '', text.lower())


def _is_junk_registry_text(text: str) -> bool:
    """registry에 저장할 가치가 없는 항목 판정: 빈 값, `--` 같은 구분선,
    정규화 후 2자 미만인 불릿."""
    return len(_norm_key(text)) < 2


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
        if not task or _is_junk_registry_text(task):
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


_NO_RATIONALE_PLACEHOLDER = "스크립트에 명시되지 않음"


def extract_decisions_from_minutes(minutes_text: str) -> List[Dict[str, str]]:
    """회의록 텍스트에서 결정사항 목록을 파싱한다 (규칙 기반, LLM 없음).

    파싱 대상 섹션 헤더: 결정사항, 결정 사항, decisions, 확정.
    최상위 항목은 "-"/"*" 불릿 또는 "1."/"1)" 번호목록 모두 인식한다. 각 항목보다
    더 들여쓰기된 "배경: ..." 서브라인이 있으면 rationale로 함께 캡처한다(회의록
    프롬프트가 결정마다 배경 서브불릿을 요구하도록 바뀜 — 왜 그렇게 결정했는지).
    반환: [{"summary": str, "rationale": str}, ...] (빈 목록 가능)
    """
    decision_pat = re.compile(
        r'^\s*#{1,4}\s*(?:결정\s*사항?|decisions?|확정)\b', re.IGNORECASE
    )
    next_section_pat = re.compile(r'^\s*#{1,4}\s+\S')
    top_item_pat = re.compile(r'^(?:[-*]|\d+[.)])\s+(.*\S)?\s*$')
    rationale_pat = re.compile(r'^(?:[-*]\s*)?배경\s*[:：]\s*(.*\S)?\s*$')

    decisions: List[Dict[str, str]] = []
    in_section = False
    current: Optional[Dict[str, str]] = None
    current_indent = 0

    for line in minutes_text.splitlines():
        stripped = line.strip()
        if decision_pat.match(stripped):
            in_section = True
            current = None
            continue
        if not in_section:
            continue
        if next_section_pat.match(stripped):
            break
        if not stripped:
            continue

        indent = len(line) - len(line.lstrip())

        if current is not None and indent > current_indent:
            r_m = rationale_pat.match(stripped)
            if r_m:
                text_val = (r_m.group(1) or "").strip()
                # LLM이 같은 결정에 "배경:" 서브라인을 두 번 쓰는 경우가 있다(실제 내용 +
                # "스크립트에 명시되지 않음" 플레이스홀더). 먼저 채워진 값은 유지하되,
                # 플레이스홀더가 먼저 잡혔다면 나중에 나온 실제 내용으로 교체한다.
                if not current["rationale"] or (
                    current["rationale"] == _NO_RATIONALE_PLACEHOLDER and text_val != _NO_RATIONALE_PLACEHOLDER
                ):
                    current["rationale"] = text_val
            continue  # 배경이 아닌 하위 내용도 현재 항목을 깨지 않고 무시

        top_m = top_item_pat.match(stripped)
        if top_m:
            item = (top_m.group(1) or "").strip()
            if item and not _is_junk_registry_text(item):
                current = {"summary": item, "rationale": ""}
                current_indent = indent
                decisions.append(current)
            else:
                current = None
            continue

        current = None  # 불릿/번호가 아닌 동급 이하 줄 → 현재 항목 종료

    return decisions


def _decision_summary_rationale(item: Any) -> Tuple[str, str]:
    """decisions 리스트 항목에서 (summary, rationale)을 뽑는다.
    extract_decisions_from_minutes()의 {"summary","rationale"} dict와, 다른 호출부
    (ingestion_pipeline._extract_sections()["decisions"] 등)의 평문 문자열을 모두 허용."""
    if isinstance(item, dict):
        return str(item.get("summary", "") or "").strip(), str(item.get("rationale", "") or "").strip()
    return str(item or "").strip(), ""


def update_decision_registry_from_minutes(
    decisions: List[Any],
    source_meeting: str,
    source_note: str = "",
    registry_path: Optional[Path] = None,
) -> int:
    """회의 후 결정사항 목록을 decision_registry.json에 누적한다.

    Args:
        decisions:      결정사항 목록 — extract_decisions_from_minutes()가 반환하는
                        {"summary","rationale"} dict 또는(하위호환) 평문 문자열
                        (ingestion_pipeline._extract_sections()["decisions"] 등).
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
    for decision_item in decisions:
        summary, rationale = _decision_summary_rationale(decision_item)
        if not summary or _is_junk_registry_text(summary):
            continue

        norm_summary = _norm_key(summary)
        if (norm_meeting, norm_summary) in existing_keys:
            continue

        seq = initial_count + added + 1
        decision_id = f"DEC-{yymmdd}-{seq:03d}"

        new_decision: Dict[str, Any] = {
            "decision_id": decision_id,
            "summary": summary,
            "rationale": rationale,
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


def _note_date_from(content: str, wiki_title: str) -> str:
    """노트 날짜(YYYY-MM-DD): frontmatter date/session_date 우선, 없으면 파일명에서 추출."""
    m = re.search(r'(?m)^\s*(?:date|session_date)\s*:\s*"?(\d{4}-\d{2}-\d{2})', content or "")
    if m:
        return m.group(1)
    try:
        from meeting_minutes_app.meeting_pipeline.date_utils import parse_iso_date_from_text
        return parse_iso_date_from_text(wiki_title)
    except Exception:
        return ""


def _get_brief_related_notes(
    title: str,
    topic: str,
    indexer,
    obs,
    limit: int = 5,
    memo: str = "",
) -> Tuple[List[Tuple[str, str, str]], List[Tuple[str, str, str]]]:
    """Vault에서 관련 노트를 검색하고 일반/논문으로 분류한다.

    Returns:
        (regular_notes, paper_notes)
        각 요소: (wiki_title, body_snippet_2000chars)

    전략:
        1. TF-IDF 인덱스 검색(title+topic+memo) → frontmatter type 으로 분류
        2. Obsidian REST 검색 → type 불명이므로 title 키워드로 분류
        3. memo에서 추출한 키워드(인명·기관명 등)로 보강 검색(LLM 없음)
        4. 지금까지 찾은 노트 제목을 Wiki Knowledge Graph로 1-hop(이상) 확장해
           연결된 인물/조직/주제 노트를 추가로 포함 (graph_expand_titles, 옵트인)
        5. norm_title 기반 중복 제거
    """
    try:
        from meeting_minutes_app.wiki_core.vault_retrieval import (
            search_related_notes_rest,
            get_related_note_content,
            strip_frontmatter,
            norm_title,
            keyword_terms,
            is_domain_mismatched,
        )
    except ImportError as e:
        print(f"[wiki] meeting_workflow import 실패: {e}")
        return [], []

    regular: List[Tuple[str, str, str]] = []
    papers: List[Tuple[str, str, str]] = []
    # 자기참조 방지: 같은 제목으로 prep-brief를 재실행하면 직전에 저장된 브리프 자신이
    # vault 검색에 걸려 "관련 노트"로 다시 포함되고, 그 안에 자기 자신의 이전 관련 노트
    # 요약까지 통째로 중첩 인용되는 문제가 있었다(실전 재실행 중 확인).
    seen_norms: set = {norm_title(title)} if title else set()
    if title:
        seen_norms.add(norm_title(f"{title} 준비브리프"))

    def _add_note(wiki_title: str, note_type: str, content: str) -> None:
        nn = norm_title(wiki_title)
        if nn in seen_norms:
            return
        seen_norms.add(nn)
        date = _note_date_from(content, wiki_title)
        body = strip_frontmatter(content).strip()[:2000]
        if _is_paper_note(note_type, wiki_title):
            papers.append((wiki_title, body, date))
        else:
            regular.append((wiki_title, body, date))

    query = " ".join(filter(None, [title, topic, memo]))

    # 제목/주제/메모에서 도메인(양자/PhysicalAI 등)이 감지되면 검색 범위를 그
    # 아카이브 + 공유 참조노트로 좁힌다 — 감지 안 되면 볼트 전체 검색(기존 동작).
    path_prefixes: List[str] = []
    try:
        from meeting_minutes_app.wiki_core.vault_retrieval import (
            detect_query_domain, domain_search_prefixes,
        )
        path_prefixes = domain_search_prefixes(detect_query_domain(query))
    except Exception:
        path_prefixes = []

    # 1) TF-IDF 인덱스 검색
    if indexer and indexer.is_built:
        try:
            results = indexer.search(query, limit=limit * 2, path_prefixes=path_prefixes)
            for r in results:
                if r.get("score", 0) < 0.02:
                    continue
                wiki_title = r.get("wikilink_title") or r.get("title", "")
                if not wiki_title:
                    continue
                if is_domain_mismatched(r["path"], query):
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
            rest_hits = search_related_notes_rest(
                obs, title=title, topic=topic, limit=limit, return_paths=True,
            )
            for wiki_title, note_path in rest_hits:
                nn = norm_title(wiki_title)
                if nn in seen_norms or is_domain_mismatched(note_path, query):
                    continue
                content = get_related_note_content(indexer, obs, wiki_title) or ""
                _add_note(wiki_title, "", content)
        except Exception as e:
            print(f"[wiki] REST 검색 실패 (무시): {e}")

    # 3) memo 키워드 보강 검색 (LLM 없음) — 회의 전 메모에 담긴 인명·기관명·주제어를
    # 추가로 vault에서 탐색한다. title/topic만으로는 못 찾는 세부 맥락을 보완.
    if memo.strip() and (indexer or obs):
        try:
            for term in keyword_terms(memo)[:15]:
                if len(regular) + len(papers) >= limit * 3:
                    break
                candidates: List[Tuple[str, str]] = []
                if indexer and indexer.is_built:
                    try:
                        for hit in indexer.search(term, limit=2, path_prefixes=path_prefixes):
                            if not (hit.get("score", 0.0) >= 0.05 or hit.get("cosine", 0.0) > 0.0):
                                continue
                            t = hit.get("wikilink_title") or hit.get("title", "")
                            if t:
                                candidates.append((t, hit.get("path", "")))
                    except Exception:
                        pass
                if obs:
                    try:
                        candidates.extend(search_related_notes_rest(
                            obs, title=term, topic="", limit=2, return_paths=True))
                    except Exception:
                        pass
                for wiki_title, note_path in candidates:
                    if norm_title(wiki_title) in seen_norms:
                        continue
                    # 단일 키워드 쿼리라 신호가 약함 — memo 전체를 기준으로 도메인 오염만 검사
                    if is_domain_mismatched(note_path, memo):
                        continue
                    content = get_related_note_content(indexer, obs, wiki_title) or ""
                    if content:
                        _add_note(wiki_title, "", content)
        except Exception as e:
            print(f"[wiki] memo 키워드 검색 실패 (무시): {e}")

    # 4) 그래프 확장 (옵트인, wiki_knowledge.graph_retrieval_expand_enabled) — 지금까지
    # 찾은 노트 제목을 Wiki Knowledge Graph로 확장해 연결된 인물/조직/주제를 추가한다.
    try:
        from meeting_minutes_app.meeting_pipeline.meeting_workflow import graph_expand_titles
        found_titles = [t for t, *_ in regular] + [t for t, *_ in papers]
        for wiki_title in graph_expand_titles(found_titles, max_extra=limit):
            if norm_title(wiki_title) in seen_norms:
                continue
            content = get_related_note_content(indexer, obs, wiki_title) or ""
            if content:
                _add_note(wiki_title, "", content)
    except Exception as e:
        print(f"[wiki] 그래프 확장 실패 (무시): {e}")

    # memo 기반 보강 검색이 있으면 direct 검색(title/topic)만으로는 못 찾을 맥락을
    # 위해 소폭 여유를 둔다 — 그래프/메모 검색 결과가 direct 결과에 밀려 전부
    # 잘려나가지 않도록.
    effective_limit = limit + 3 if memo.strip() else limit
    return regular[:effective_limit], papers[:limit]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Prep Brief 포맷팅
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def build_prep_brief(
    title: str,
    topic: str,
    yymmdd: str,
    full_date: str,
    regular_notes: List[Tuple[str, str, str]],
    paper_notes: List[Tuple[str, str, str]],
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
        lines.append(", ".join(f"[[{t}]]" for t, *_ in regular_notes))
    else:
        lines.append("관련 노트 없음 (Vault 인덱스 미연결 또는 검색 결과 없음)")
    lines.append("")

    # 관련 논문·학술자료 (있을 때만 섹션 출력)
    if paper_notes:
        lines.append("## 관련 논문·학술자료")
        lines.append(", ".join(f"[[{t}]]" for t, *_ in paper_notes))
        lines.append("")

    # 관련 노트 요약
    if regular_notes:
        lines.append("## 관련 노트 요약")
        for note_title, body, date in regular_notes:
            date_tag = f" (작성일: {date})" if date else ""
            lines.append(f"### [[{note_title}]]{date_tag}")
            lines.append(body.strip() if body.strip() else "(내용 없음)")
            lines.append("")

    # 논문 요약
    if paper_notes:
        lines.append("## 논문 요약")
        for note_title, body, date in paper_notes:
            date_tag = f" (작성일: {date})" if date else ""
            lines.append(f"### [[{note_title}]]{date_tag}")
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

def reindex_on_start_if_configured() -> None:
    """config.indexing.auto_reindex_on_start=true 시 실행 시작 시점에 인덱스 1회 재빌드.

    배치(meeting_minutes.main)·실시간(realtime_transcription) 시작부에서 호출된다.
    (과거엔 config 키만 있고 어디서도 읽지 않았음)
    """
    if not _c("indexing.auto_reindex_on_start", False):
        return
    try:
        from meeting_minutes_app.wiki_core.vault_indexer import VaultIndexer
        idx = VaultIndexer.from_config()
        if not idx:
            return
        print("[wiki] 시작 시 인덱스 재빌드 중 (indexing.auto_reindex_on_start)...")
        n = idx.build(verbose=False)
        print(f"[wiki] 인덱스 갱신 완료: {n}개 노트")
    except Exception as e:
        print(f"[wiki] 시작 시 인덱스 재빌드 실패 (무시): {e}")


def _reindex_if_configured(indexer, force: bool = False) -> None:
    """config.indexing.auto_reindex_after_write=true 또는 force=True 시 재빌드.

    기존 이슈: obs.put_note() / write_meeting_note() 후 vault_index.json 자동 갱신 없음.
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
    # 인덱스만 갱신하면 지식 그래프가 새 회의를 반영하지 못한다 — 그래프도 함께 백필한다
    # (실패해도 인덱스 갱신은 유효하므로 무시). 그래프는 인덱스의 파생 뷰라 원본을 건드리지 않음.
    try:
        from meeting_minutes_app.wiki_core import graph_sync
        graph_sync.backfill_from_vault()
        print("[wiki] 지식 그래프 백필 완료")
    except Exception as e:
        print(f"[wiki] 그래프 백필 건너뜀 (무시): {e}")


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
                d.get("summary", "") + (f" (배경: {d['rationale']})" if d.get("rationale") else "")
                for d in sorted(
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
                draft_parts.append(f"  - 주요 결정: {_decision_summary_rationale(decisions[0])[0]}")
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
        summary, rationale = _decision_summary_rationale(decision)
        draft_content = f"- {summary}" + (f"\n  - 배경: {rationale}" if rationale else "")
        proposals.append({
            "target_note": None,
            "candidates": related_titles[:3],
            "section": "결정사항",
            "operation": "append",
            "draft_content": draft_content,
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
  python run_meeting.py prep-brief --title "퀀텀인텔리전트 회의" --memo agenda.txt
""",
    )
    ap.add_argument("--title", required=True, help="회의 제목")
    ap.add_argument("--topic", default="", help="회의 주제 (선택)")
    ap.add_argument("--attendees", default="", help="참석자 쉼표 구분 (선택) — 예: \"김철수,이영희\"")
    ap.add_argument("--memo", default="", help="회의 전 메모/아젠다 파일 경로 (선택) — "
                                              "관련 노트 검색·그래프 확장의 추가 근거로 사용")
    ap.add_argument("--project", default="", help="obsidian.project 오버라이드 (선택) — "
                                                 "config.json을 고치지 않고 세션 단위로 다른 "
                                                 "도메인(obsidian.project_domains 매핑)에 저장")
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
    memo: str = ""
    if args.memo:
        if not os.path.isfile(args.memo):
            print(f"[wiki] 메모 파일 없음: {args.memo}")
        else:
            # 이 블록은 아래(1352행경) main() 전체를 감싸는 try/except보다 앞에 있어서,
            # UTF-8이 아닌 파일(Windows 메모장으로 저장한 cp949 텍스트 등)을 그냥
            # open(encoding="utf-8")로 읽으면 UnicodeDecodeError가 여기서 바로
            # raw traceback으로 죽는다 — "--memo agenda.txt"가 도움말에 나오는
            # 정식 지원 경로이므로 인코딩을 못 맞춰도 실패하지 않게 방어한다.
            try:
                with open(args.memo, encoding="utf-8") as f:
                    memo = f.read()
            except UnicodeDecodeError:
                try:
                    with open(args.memo, encoding="cp949", errors="replace") as f:
                        memo = f.read()
                    print(f"[wiki] 메모 파일 인코딩 UTF-8 아님 → cp949로 읽음: {args.memo}")
                except Exception as e:
                    print(f"[wiki] 메모 파일 읽기 실패 (무시): {e}")
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
        # load_vault_client: REST(obsidian.enabled+ping) 우선, 없으면 폴더(.md) FS 폴백 —
        # 폴더만 연결해도 prep-brief가 볼트 폴더에 저장되도록(REST 전용 load_obsidian_client 대체).
        from meeting_minutes_app.wiki_core.vault_retrieval import load_vault_indexer, load_vault_client
    except ImportError as e:
        print(f"[wiki] meeting_workflow 없음 — Vault 검색 건너뜀: {e}")
        load_vault_indexer = lambda: None  # noqa: E731
        load_vault_client = lambda project="": None  # noqa: E731

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
                obs = load_vault_client(project=args.project)
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
            memo=memo,
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

        memo_keywords: List[str] = []
        if memo.strip():
            try:
                from meeting_minutes_app.wiki_core.vault_retrieval import keyword_terms
                memo_keywords = keyword_terms(memo)
            except Exception:
                memo_keywords = []

        open_actions = _filter_actions_by_topic(
            action_reg.get("actions", []), topic, attendees=attendees_list, limit=10,
            extra_keywords=memo_keywords,
        )
        recent_decisions = _filter_decisions_by_topic(
            decision_reg.get("decisions", []), topic, limit=10,
            extra_keywords=memo_keywords,
        )

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
