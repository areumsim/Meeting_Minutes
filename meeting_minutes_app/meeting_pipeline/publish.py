#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
회의록 후처리 발행 파이프라인: 알림 발송, 첨부파일 수집, 참석자 정리,
Obsidian 노트 기록(+계획 노트 병합), 계획 매칭 기반 사전 컨텍스트 주입.
meeting_minutes.py에서 분리 (2026-07 리팩토링 3단계).
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from meeting_minutes_app.common.llm_client import LLMClient
from meeting_minutes_app.meeting_pipeline.meeting_minutes import (
    _c, logger, info, ok, warn, _norm_resume_key,
)


# ──────────────────────────────────────────────
#  알림 발송
# ──────────────────────────────────────────────
def _send_notification(
    notify_type: str,
    title: str,
    summary_path: str,
    files: List[str],
    obsidian_path: str = "",
    doc_type: str = "meeting",
):
    try:
        from meeting_minutes_app.common.notifier import Notifier
    except ImportError:
        warn("notifier.py 없음 → 알림 건너뜀")
        return

    # config.json 이메일 설정 읽기
    email_cfg = {
        "sender":     _c("email.sender",    ""),
        "password":   _c("email.password",  ""),
        "recipients": [r.strip() for r in
                       _c("email.recipient", "").split(",") if r.strip()],
        "smtp_host":  _c("email.smtp_host", ""),
        "smtp_port":  int(_c("email.smtp_port", 0) or 0),
    }
    slack_cfg = {"webhook_url": os.environ.get("SLACK_WEBHOOK_URL", "") or _c("notify.slack.webhook_url", "")}
    teams_cfg = {"webhook_url": os.environ.get("TEAMS_WEBHOOK_URL", "") or _c("notify.teams.webhook_url", "")}

    notify_dict: Dict[str, dict] = {}
    if notify_type in ("email", "all") and email_cfg["sender"] and email_cfg["password"]:
        notify_dict["email"] = email_cfg
    if notify_type in ("slack", "all") and slack_cfg["webhook_url"]:
        notify_dict["slack"] = slack_cfg
    if notify_type in ("teams", "all") and teams_cfg["webhook_url"]:
        notify_dict["teams"] = teams_cfg

    if not notify_dict:
        warn(f"알림 설정 없음 ({notify_type}) → config.json email 섹션 또는 환경변수 확인")
        return

    notifier = Notifier.from_config({"notify": notify_dict})
    if notifier.has_channels:
        results = notifier.send(
            title=title, summary_path=summary_path, files=files,
            obsidian_path=obsidian_path, doc_type=doc_type,
        )
        for r in results:
            status = "완료" if r["success"] else f"실패: {r.get('error', '')}"
            print(f"  알림 ({r['channel']}): {status}")


def _dedupe_existing(paths: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for p in paths:
        if not p:
            continue
        try:
            rp = str(Path(p).resolve())
        except Exception:
            rp = p
        if rp in seen or not os.path.isfile(p):
            continue
        seen.add(rp)
        out.append(p)
    return out


def _collect_wiki_proposal_files(title: str, out_dir: str) -> List[str]:
    """현재 회의 제목과 맞는 wiki_proposal 파일을 output 루트/세션 폴더에서 수집."""
    candidates: List[Path] = []
    roots = [Path(out_dir)]
    try:
        roots.append(Path(__file__).resolve().parent.parent.parent / str(_c("output_dir", "output")))
    except Exception:
        pass

    title_norm = _norm_resume_key(title)
    for root in roots:
        if not root.exists():
            continue
        for p in root.glob("*wiki_proposal.*"):
            name_norm = _norm_resume_key(p.name)
            if not title_norm or title_norm in name_norm or name_norm in title_norm:
                candidates.append(p)

    candidates = sorted(set(candidates), key=lambda p: p.stat().st_mtime, reverse=True)
    # md/json 한 쌍이면 충분하다. 오래된 동명이 산출물 전체 첨부를 피한다.
    return [str(p) for p in candidates[:2]]


def _collect_notification_artifacts(out_dir: str, pfx: str, title: str) -> List[str]:
    """메일/알림 첨부 기본 세트.

    상세/요약 외에 STT 원본, 교정본, segments, LLM/Wiki context/proposal/fact_check까지
    같이 전달해 메일만으로 검토 가능하게 한다.
    """
    d = Path(out_dir)
    paths: List[str] = []

    for name in (
        f"{pfx}minutes.md",
        f"{pfx}summary.md",
        f"{pfx}summary.txt",
        f"{pfx}actions.md",
        f"{pfx}actions.json",
        f"{pfx}script_refined.txt",
        f"{pfx}script.md",
        f"{pfx}transcript.md",
        f"{pfx}transcript.txt",
        f"{pfx}segments.json",
        f"{pfx}wiki_context.json",
        "wiki_context.json",
    ):
        paths.append(str(d / name))

    for pattern in (
        f"{pfx}*fact_check*.md",
        f"{pfx}*fact_check*.json",
        "*fact_check*.md",
        "*fact_check*.json",
    ):
        paths.extend(str(p) for p in d.glob(pattern))

    paths.extend(_collect_wiki_proposal_files(title, out_dir))
    return _dedupe_existing(paths)


# ──────────────────────────────────────────────
#  후처리: 용어 보완 + Obsidian 기록 (+ 옵션 이메일)
# ──────────────────────────────────────────────
def _gather_attendees(segments: List[Dict]) -> List[str]:
    """세그먼트 화자에서 실명/역할만 추출(‘Speaker A’ 류 제외)."""
    names: List[str] = []
    seen = set()
    for s in segments or []:
        spk = (s.get("speaker") or "").strip()
        if not spk or re.match(r'(?i)^speaker(?:[\s_]*[A-Za-z0-9]+)?$', spk):
            continue
        if spk.lower() not in seen:
            seen.add(spk.lower())
            names.append(spk)
    return names[:10]


def _attendee_candidates(segments: List[Dict], planned_match: Optional[Dict[str, Any]] = None) -> List[str]:
    names = _gather_attendees(segments)
    if planned_match:
        for nm in _clean_attendee_names((planned_match.get("meta") or {}).get("attendees")):
            if nm and nm not in names:
                names.append(nm)
    # 발화 중 "저는 OOO입니다" 형태의 자기소개를 보수적으로 수집
    for seg in segments or []:
        txt = str(seg.get("text", ""))
        for m in re.finditer(r"저는\s+([가-힣]{2,4})(?:이라고|입니다|교수|박사)", txt):
            nm = m.group(1).strip()
            if nm and nm not in names:
                names.append(nm)
        if len(names) >= 10:
            break
    return names[:10]


def _strip_fact_verification_sections(markdown: str) -> str:
    """LLM이 생성한 중복 사실 검증 섹션을 제거한다.

    Vault 기반 검증은 후처리에서 별도로 붙이므로, 본문에 이미 같은 제목이 있으면
    다음 2레벨 섹션 전까지 삭제해 결과가 중복되지 않게 한다.
    """
    if not markdown:
        return ""
    return re.sub(
        r"(?ms)^##\s*사실\s*검증\b.*?(?=^##\s+|\Z)",
        "",
        markdown,
    ).strip()


def _stt_quality_meta(
    segments: List[Dict],
    refined_text: Optional[str],
    used_refined: bool,
    source: str,
) -> Dict[str, Any]:
    raw_chars = sum(len(str(s.get("text", ""))) for s in segments or [])
    refined_chars = len(refined_text or "")
    return {
        "stt_segment_count": len(segments or []),
        "stt_raw_chars": raw_chars,
        "refined_chars": refined_chars,
        "refined_ratio": round(refined_chars / raw_chars, 3) if raw_chars else 0,
        "used_refined_script": used_refined,
        "stt_source": source,
    }


def _detect_meeting_scope(title: str = "", topic: str = "") -> str:
    """내부/외부 회의 구분을 보수적으로 추론한다."""
    text = f"{title} {topic}".lower()
    external_terms = (
        "외부", "고객", "클라이언트", "파트너", "협력사", "벤더", "후원",
        "제안", "계약", "mou", "po", "견적", "미팅",
    )
    internal_terms = (
        "내부", "팀회의", "주간보고", "데일리", "사내", "1on1", "원온원",
    )
    if any(term in text for term in external_terms):
        return "external"
    if any(term in text for term in internal_terms):
        return "internal"
    return "unknown"


_PLAN_UNSET = object()   # enrich_and_publish planned_match 미지정 센티넬


def _confirm_plan_merge(match: Dict[str, Any], title: str) -> bool:
    """계획 회의 매칭 시, 회의록을 그 노트에 '병합'할지 사용자에게 확인.
    - config obsidian.auto_merge=true 면 묻지 않고 병합
    - 비대화형(웹/워처 등 TTY 아님)에서는 절대 자동 병합하지 않음(원칙: 합병 전 확인)
    - 그 외에는 대화형 프롬프트. 기본값 Y(병합)."""
    if _c("obsidian.auto_merge", False):
        return True
    meta = match.get("meta") or {}
    is_tty = bool(getattr(sys, "stdin", None)) and sys.stdin.isatty()
    print(f"\n  ── 계획된 회의와 일치하는 노트를 찾았습니다 ──")
    print(f"     노트   : {match.get('path')}")
    print(f"     제목   : {meta.get('title','')}  (녹음 제목: {title})")
    print(f"     날짜   : {meta.get('date','')} {meta.get('time','')}".rstrip())
    if match.get("reason"):
        print(f"     매칭사유: {match.get('reason')}")
    att = meta.get("attendees")
    if att:
        print(f"     참석자 : {', '.join(att) if isinstance(att, list) else att}")
    if not is_tty:
        warn("비대화형 환경 → 자동 병합하지 않고 새 노트로 생성합니다 "
             "(나중에 직접 확인 후 병합하세요).")
        return False
    try:
        ans = input("  이 계획 노트에 회의록을 병합할까요? [Y=병합 / n=새 노트] : ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return ans in ("", "y", "yes", "ㅛ")


def enrich_and_publish(
    *,
    title: str,
    doc_type: str,
    minutes_md: str,
    llm: "LLMClient",
    summary_md: str = "",
    actions_md: str = "",
    topic: str = "",
    session_dt: str = "",
    attendees: Optional[List[str]] = None,
    related_notes_extra: Optional[List[str]] = None,
    notify: Optional[str] = None,
    email_summary_path: str = "",
    email_files: Optional[List[str]] = None,
    planned_match: Any = _PLAN_UNSET,
    source_audio: str = "",
    source_file_date: str = "",
    stt_meta: Optional[Dict[str, Any]] = None,
    transcript_md: str = "",
    evidence: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """배치/실시간/화자수정 경로가 공유하는 후처리:
       1) 용어·인물·기업 외부검색 보완(enrichment)
       2) Obsidian 볼트에 회의록 노트 기록(+참고노트 백링크)
       3) (옵션) 이메일 발송
    반환: {"glossary_md","obsidian_path","related_notes","sources"}
    """
    result: Dict[str, Any] = {"glossary_md": "", "obsidian_path": None,
                              "related_notes": [], "sources": []}
    meeting_scope = _detect_meeting_scope(title, topic)
    result["meeting_scope"] = meeting_scope

    # 자동 분류 라우팅 (obsidian.auto_route_enabled=true) — --project 없이도
    # 제목/주제/스크립트로 도메인(양자/PhysicalAI) 또는 00_Meetings 하위 폴더 결정.
    route: Optional[Dict[str, str]] = None
    if _c("obsidian.auto_route_enabled", False):
        try:
            from meeting_minutes_app.meeting_pipeline import meeting_workflow as mw
            route = mw.classify_meeting_route(
                title, topic, script_excerpt=(transcript_md or "")[:1000], llm=llm)
        except Exception as e:
            logger.warning(f"[publish] 자동 분류 라우팅 실패 → 00_Meetings/기타로 폴백: {e}")
            # None으로 두면 project_override=""가 static obsidian.project(예: "양자")로
            # 조용히 떨어져 무관한 회의가 도메인 아카이브에 섞인다 — 실패도 명시적으로 기타 라우팅.
            route = {"mode": "folder", "output_folder": "00_Meetings/기타"}
        result["auto_route"] = route
    project_override = route.get("project", "") if route and route.get("mode") == "domain" else ""
    output_folder = route.get("output_folder", "") if route and route.get("mode") == "folder" else ""

    # Obsidian 클라이언트 (설정 없거나 연결 실패 시 None → 볼트 기록만 생략)
    obs = None
    try:
        from meeting_minutes_app.wiki_core.obsidian import ObsidianClient
        obs = ObsidianClient.from_config(project_override=project_override)
        if obs is not None and not obs.ping():
            warn("Obsidian 연결 실패 → 볼트 기록 건너뜀")
            obs.close(); obs = None
    except Exception as e:
        logger.warning(f"[publish] Obsidian 초기화 실패: {e}")
        obs = None

    # 1) 용어 보완
    enr = {"glossary_md": "", "related_notes": [], "sources": []}
    try:
        from meeting_minutes_app.meeting_pipeline import enrichment
        enr = enrichment.enrich(minutes_md, llm, obs=obs, topic=topic or title,
                                presenter_name=title, meeting_title=title)
        if enr.get("entity_links"):
            minutes_md = enrichment.autolink_entities(minutes_md, enr["entity_links"])
        if related_notes_extra:
            merged_related: List[str] = []
            for rn in list(enr.get("related_notes", []) or []) + list(related_notes_extra or []):
                if rn and rn not in merged_related:
                    merged_related.append(rn)
            enr["related_notes"] = merged_related
        result.update(enr)
    except Exception as e:
        warn(f"용어 보완 실패: {e}")
        if related_notes_extra:
            result["related_notes"] = list(dict.fromkeys(related_notes_extra))

    # 2) Obsidian 노트 기록 — 계획(planned) 노트와 매칭되면 '확인 후 병합'
    if obs is not None:
        try:
            # 2-1) 계획 회의 매칭 — 호출자가 이미 찾았으면 재사용, 아니면 직접 탐색
            if planned_match is not _PLAN_UNSET:
                match = planned_match
            else:
                match = None
                try:
                    match = obs.find_planned_note(title, session_dt)
                except Exception as e:
                    logger.warning(f"[publish] 계획 노트 탐색 실패: {e}")
            result["planned_match"] = match.get("path") if match else None

            # 2-2) 매칭 시 병합 여부 확인(합병 전 사용자 확인 원칙)
            do_merge = _confirm_plan_merge(match, title) if match else False
            result["merged"] = do_merge

            if match and do_merge:
                path = obs.update_planned_note(
                    match, title=title, body_md=minutes_md, doc_type=doc_type,
                    topic=topic, attendees=attendees or [], session_dt=session_dt,
                    glossary_md=enr.get("glossary_md", ""),
                    related_notes=result.get("related_notes", enr.get("related_notes", [])),
                    external_refs=enr.get("sources", []),
                    summary_md=summary_md, actions_md=actions_md,
                    meeting_scope=meeting_scope,
                    web_sources_md=enr.get("web_sources_md", ""),
                    source_audio=source_audio,
                    source_file_date=source_file_date,
                    processed_at=datetime.now().isoformat(timespec="seconds"),
                    stt_meta=stt_meta,
                    transcript_md=transcript_md,
                )
                result["obsidian_path"] = path
                if path:
                    ok(f"계획 회의에 병합 → {path}  (status: planned → done)")
            else:
                if match:
                    info(f"계획 회의 매칭됨(병합 보류): {match.get('path')} → 새 노트로 생성")
                path = obs.write_meeting_note(
                    title=title, body_md=minutes_md, doc_type=doc_type,
                    topic=topic, attendees=attendees or [], session_dt=session_dt,
                    glossary_md=enr.get("glossary_md", ""),
                    related_notes=result.get("related_notes", enr.get("related_notes", [])),
                    external_refs=enr.get("sources", []),
                    summary_md=summary_md, actions_md=actions_md,
                    meeting_scope=meeting_scope,
                    web_sources_md=enr.get("web_sources_md", ""),
                    source_audio=source_audio,
                    source_file_date=source_file_date,
                    processed_at=datetime.now().isoformat(timespec="seconds"),
                    stt_meta=stt_meta,
                    transcript_md=transcript_md,
                    evidence=evidence,
                    output_folder=output_folder,
                    # 매칭됐지만 병합 보류 → 계획 경로 기록(대시보드 '병합 대기' 표시용)
                    extra_meta=({"matched_plan": match["path"]} if match else None),
                )
                result["obsidian_path"] = path
                if path:
                    ok(f"Obsidian 노트 기록 → {path}")
                    if match:
                        ok(f"→ 계획 '{match['path']}' 와(과) 매칭됨. 확인 후 병합하려면 Cowork에서 요청하세요.")
        except Exception as e:
            warn(f"Obsidian 노트 기록 실패: {e}")
        finally:
            obs.close()

    # 3) 이메일(옵션) — 배치는 main 루프가 일괄 발송하므로 보통 None.
    #    실시간 경로는 .md 파일이 없으므로(DB 저장), 회의록을 임시파일로 만들어 본문/첨부에 실어 보냄.
    if notify:
        tmp_dir = None
        try:
            summary_path = email_summary_path
            files = list(email_files or [])
            # Obsidian 노트 경로가 있으면 자동 첨부
            # obsidian_path는 vault 상대경로(예: "Inbox/note.md") → 풀 경로로 변환
            _obs_note = result.get("obsidian_path")
            if _obs_note:
                _vault_root = _c("obsidian.vault_path", "") or ""
                if not _vault_root:
                    try:
                        from meeting_minutes_app.wiki_core.obsidian import _detect_obsidian_config as _dOC
                        _vault_root = _dOC().get("vault_path", "")
                    except Exception:
                        pass
                _obs_full = (
                    os.path.join(_vault_root, str(_obs_note))
                    if _vault_root else str(_obs_note)
                )
                if os.path.isfile(_obs_full) and _obs_full not in files:
                    files.append(_obs_full)
            if not summary_path and (summary_md or minutes_md):
                body = minutes_md or summary_md
                glossary = result.get("glossary_md", "")
                if glossary and glossary not in body:
                    body = f"{body}\n\n## 용어·배경\n\n{glossary}\n"
                tmp_dir = tempfile.mkdtemp(prefix="mtg_mail_")
                safe = re.sub(r'[\\/:*?"<>|]', "_", title)[:40].strip() or "회의록"
                tmp_path = os.path.join(tmp_dir, f"{safe}_회의록.md")
                with open(tmp_path, "w", encoding="utf-8") as f:
                    f.write(body)
                summary_path = tmp_path
                files.append(tmp_path)
            _send_notification(notify, title, summary_path or "", files,
                              doc_type=doc_type)
        except Exception as e:
            warn(f"이메일 발송 실패: {e}")
        finally:
            if tmp_dir:
                shutil.rmtree(tmp_dir, ignore_errors=True)

    return result


# ──────────────────────────────────────────────
#  계획(planned) 회의 매칭 및 사전 컨텍스트 주입
# ──────────────────────────────────────────────
def _lookup_plan(title: str, session_dt: str):
    """계획(planned) 노트를 1회만 탐색해 match dict(or None) 반환. Obsidian 미연결시 None."""
    try:
        from meeting_minutes_app.wiki_core.obsidian import ObsidianClient
        obs = ObsidianClient.from_config()
        if obs is None or not obs.ping():
            if obs:
                obs.close()
            return None
        try:
            return obs.find_planned_note(title, session_dt)
        finally:
            obs.close()
    except Exception as e:
        logger.warning(f"[plan] 계획 노트 탐색 실패: {e}")
        return None


def _plan_context_text(match) -> str:
    """match 본문에서 회의록 정리에 참고할 '사전 자료'(병합 전 부분)를 추출.
    자동 리서치 내용은 참고용으로 유지하고 마커 주석만 제거한다."""
    if not match:
        return ""
    body = match.get("body") or ""
    cut = re.split(r"^##\s+회의 기록", body, maxsplit=1, flags=re.MULTILINE)[0]
    try:
        from meeting_minutes_app.meeting_pipeline import plan_research
        cut = cut.replace(plan_research.MARKER_BEGIN, "").replace(plan_research.MARKER_END, "")
    except Exception:
        pass
    return cut.strip()


def _clean_attendee_names(attendees):
    """['최민석(팀장)','정하윤 수석','심아름 책임(나)'] → ['최민석','정하윤','심아름'] (화자 힌트용 이름만).

    직책 토큰 판정은 people.ROLE_TOKENS 단일 소스를 사용한다
    (과거엔 부분 복사된 직책 목록이 여기 하드코딩돼 있어 '교수/박사' 등이 누락됐음).
    """
    from meeting_minutes_app.meeting_pipeline.people import parse_attendee
    out = []
    for a in (attendees or []):
        nm, _role = parse_attendee(a)
        if nm and nm not in out:
            out.append(nm)
    return out


def plan_context_memo(title, session_dt, base_memo=None, match=_PLAN_UNSET):
    """[모든 진입점 공용] 계획 매칭(+사전 자료)을 회의록 생성용 memo 에 주입.
    match 를 넘기면 재탐색하지 않고 재사용한다. 반환: (match_or_None, memo_or_None)."""
    if match is _PLAN_UNSET:
        match = _lookup_plan(title, session_dt)
    ctx = _plan_context_text(match)
    directives = []
    if match:
        names = _clean_attendee_names((match.get("meta") or {}).get("attendees"))
        if names:
            directives.append(
                "[참석자 참고 명단] 아래는 계획상 참석 예정자입니다. 화자가 이 중 누구인지 "
                "분명한 경우에만 그 실명으로 표기하세요('발언자 A/B'보다 우선). 확실하지 않으면 "
                "억지로 맞추지 말고, 명단에 없어도 실제 발언자가 있으면 들은 대로 두세요: "
                + ", ".join(names))
    if ctx:
        directives.append(
            "[회의 전 사전 자료 \u2014 맥락 참고용. 실제 회의에서 다뤄진 경우에만 반영하고, "
            "다뤄지지 않은 항목을 억지로 넣지 말 것]:\n" + ctx)
    memo = base_memo or ""
    if directives:
        memo = ("\n\n".join(directives) + ("\n\n" + memo if memo else "")).strip()
    return match, (memo or None)
