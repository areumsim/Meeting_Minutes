#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
finalize.py — 회의 종료 후 공유 오케스트레이터
================================================
세그먼트 + 메타데이터를 받아 「컨텍스트 주입 → 교정 → 회의록 → 액션 →
사실검증 → 요약 → 스크립트 → Obsidian 발행 → wiki 산출물 → registry →
graph」를 한 곳에서 실행한다.

과거엔 이 흐름이 4곳에 복사돼 있었다(2026-07 통합 완료, 전부 이 함수를 씀):
  - batch  : pipeline.process_single (가장 완전)
  - CLI    : realtime_transcription._generate_output / cmd_recover
  - web    : web/backend/api/realtime.py BrowserRealtimeSession._finalize
  - ingest : ingestion_pipeline.ingest (watcher 자동 녹음처리 경로 — 과거엔
             write_recording_note() 스키마 차이로 부분 채택만 했으나, 이제
             write_meeting_note() 경로로 완전히 통합해 enrichment/재인덱싱/
             그래프 동기화가 배치·웹과 동일하게 동작한다)

호출자별 차이는 FinalizeOptions(무엇을 실행할지) + FinalizeEvents(산출물을
어디로 보낼지: 파일 저장 / DB upsert / WS 이벤트)로 흡수한다.

Import 규율 (pipeline.py와 동일):
  - 최상위 import는 표준 라이브러리 + common/ 만
  - 생성/발행 모듈(minutes_generation, publish, meeting_workflow, wiki_core.*)은
    스테이지 함수 안에서 lazy import — 순환 import 방지
  - meeting_minutes(facade 허브)는 절대 import 금지, llm은 호출자가 주입
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from meeting_minutes_app.common import config_loader as _cfg


def _c(key: str, default: Any = None) -> Any:
    return _cfg.get(key, default)


#: "plan_match 미지정 — publish가 직접 탐색" 센티널 (None="매칭 없음"과 구분)
PLAN_UNSET = object()

#: 회의록에 자동 삽입되는 실시간 관련 노트 섹션 헤딩 (FR-6)
RELATED_NOTES_HEADING = "## 🔗 관련 노트"

#: 출처유형 → 표시 아이콘. 규약 정본은 source_type 을 만드는 realtime_search 에 있다
#: (웹 Recorder·CLI 표시와 같은 표를 쓰기 위해 여기서 재정의하지 않는다).
from meeting_minutes_app.wiki_core.realtime_search import SOURCE_ICON as _SOURCE_ICON


def _evidence_link(item: Dict[str, Any]) -> str:
    """근거 1건 → "[[노트#헤딩]]" 위키링크 (헤딩 없으면 노트만)."""
    title = str((item or {}).get("title") or "").strip()
    if not title:
        return ""
    heading = str((item or {}).get("heading") or "").strip()
    return f"[[{title}#{heading}]]" if heading else f"[[{title}]]"


def _related_evidence_memo(evidence: Optional[List[Dict[str, Any]]],
                           limit: int = 8) -> str:
    """실시간 검색 근거를 회의록 생성 memo 블록으로 조립."""
    lines = []
    for item in (evidence or [])[:limit]:
        link = _evidence_link(item)
        if not link:
            continue
        snippet = " ".join(str(item.get("snippet") or "").split())[:160]
        seg = " ".join(str(item.get("segment_text") or "").split())[:80]
        row = f"- {link}"
        if snippet:
            row += f" — {snippet}"
        if seg:
            row += f" (발화: {seg})"
        lines.append(row)
    if not lines:
        return ""
    return ("[실시간 관련 노트 근거(회의 중 검색된 내부자료)]:\n"
            + "\n".join(lines))


def build_related_notes_section(evidence: Optional[List[Dict[str, Any]]],
                                titles: Optional[List[str]] = None,
                                max_rank: Optional[int] = None,
                                limit: int = 10) -> str:
    """회의록 말미에 붙일 "## 🔗 관련 노트" 섹션 마크다운.

    근거(섹션경로·점수)가 있으면 링크와 함께, 없으면 제목 링크만 나열한다.
    LLM 호출 없이 결정적으로 만든다 — 실시간 검색이 찾은 사실 그대로가 남아야
    한다(생성 모델이 관련 노트를 누락·변형하던 문제 회피).

    중복 제거는 **제목** 기준이다. 볼트에는 같은 제목의 노트가 여러 폴더에 있을 수
    있고(`01_References/Companies/Acme.md` vs `Archive/…/회사/Acme.md`), 회의록은
    `[[제목]]` 위키링크로 적으므로 두 줄이 구분되지 않는다. 과거엔 `[[제목#헤딩]]`
    전체를 키로 써서 같은 제목이 헤딩만 다르게 두 줄 남을 수 있었다.

    max_rank: 노이즈 컷(FR-6). 실시간 검색에서 이 순위(0-기반)보다 아래였던 노트는
    싣지 않는다. None/음수면 제한 없음. 순위 정보가 없는 근거는 통과시킨다.

    순위는 `arm_rank`(자기 검색 arm 안에서의 순위)를 먼저 본다. `rank`는 arm ②(논문
    폴더 한정 검색)를 arm ① 뒤에 이어 붙인 **통합 순번**이어서, 논문 arm 의 1위가
    후보 수(기본 10)만큼 밀린 값을 갖는다 — 그것으로 컷하면 max_rank 를 1~10 중 무엇으로
    두든 논문 arm 만이 찾은 노트가 100% 탈락해 FR-11(내부 논문 우선)이 무효화됐다.
    """
    rows: List[str] = []
    seen: set = set()
    has_reason = False        # 엔티티 겹침(근거 명시) 항목이 있나
    has_similarity = False    # 유사도만으로 걸린 항목이 있나
    for item in (evidence or []):
        link = _evidence_link(item)
        title_key = str((item or {}).get("title") or "").strip().lower()
        if not link or title_key in seen:
            continue
        if max_rank is not None and max_rank >= 0:
            rank = (item or {}).get("arm_rank")
            if rank is None:
                rank = (item or {}).get("rank")
            if rank is not None and int(rank) > max_rank:
                continue
        seen.add(title_key)
        icon = _SOURCE_ICON.get(str(item.get("source_type") or "note"), "📄")
        bits = [f"- {icon} {link}"]
        # 엔티티 겹침으로 걸린 노트는 **왜 걸렸는지**를 그대로 적는다("같은 인물: 남우진").
        # 유사도 점수와 달리 검증 가능한 근거이므로 관련도 숫자보다 먼저 보여 준다.
        reason = str(item.get("match_reason") or "").strip()
        if reason:
            bits.append(f"— {reason}")
            has_reason = True
        else:
            has_similarity = True
        score = float(item.get("score") or 0)
        if score:
            bits.append(f"(관련도 {score:.2f})")
        hits = int(item.get("hits") or 0)
        if hits > 1:
            bits.append(f"· {hits}회 참조")
        snippet = " ".join(str(item.get("snippet") or "").split())[:120]
        if snippet:
            bits.append(f"— {snippet}")
        rows.append(" ".join(bits))
        if len(rows) >= limit:
            break
    for t in (titles or []):
        title_key = str(t or "").strip().lower()
        if title_key and title_key not in seen and len(rows) < limit:
            seen.add(title_key)
            rows.append(f"- 📄 [[{t}]]")
            has_similarity = True     # 제목만 있는 항목 = 근거 없음
    if not rows:
        return ""
    # 머리말은 **목록에 실제로 담긴 종류**에 맞춘다. 두 종류가 섞이는데 신뢰도가 다르다.
    #
    # (가) 엔티티 겹침(`match_reason` 있음) — 같은 person/topic 노드를 가리킨다는 *기록*.
    #      추정이 아니므로 회의록 본문 보완에도 쓰인다.
    # (나) 유사도 회수 — scripts/measure_retrieval_floor.py 실측에서 전사에 대해 그
    #      전사 자신의 회의록조차 1위 회수율이 임베딩 0%(중위 15위)·TF-IDF 0%(중위 88위)였다.
    #      '근거'라고 적으면 회의록이 검증되지 않은 연결을 사실처럼 주장한다.
    #
    # 한쪽만 있는데 양쪽 문구를 다 붙이면 그것도 거짓이 된다 — 그래서 조건부다.
    notes: List[str] = []
    if has_reason:
        notes.append("> **같은 인물/같은 주제**로 걸린 항목은 이유가 함께 적혀 있습니다. "
                     "이 항목은 용어·배경 확인용으로 회의록 작성에 참고했습니다.")
    if has_similarity:
        notes.append("> 이유가 없는 항목은 내용이 **비슷해 보여서** 자동 검색된 것으로, "
                     "관련성은 검증되지 않았습니다.")
    notes.append("> 어느 쪽이든 이번 회의에서 논의되지 않은 내용은 회의록 본문에 "
                 "들어가지 않습니다. 확인이 필요하면 직접 열어 보세요.")
    return (f"{RELATED_NOTES_HEADING}\n\n"
            + "\n".join(notes) + "\n\n"
            + "\n".join(rows) + "\n")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  입출력 구조
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class SessionInputs:
    """세션 전사 결과 + 메타데이터 (호출자가 title 규칙을 이미 적용해서 전달)."""
    segments: List[Dict[str, Any]]
    title: str
    topic: str = ""
    doc_type: str = "meeting"
    session_dt: str = ""                    # "YYYY년 MM월 DD일 HH:MM"
    base_memo: Optional[str] = None         # CLI --memo 등
    source: str = "batch"                   # batch|realtime_cli|recover|web_realtime
    attendees: List[str] = field(default_factory=list)
    session_id: str = ""                    # graph_sync용 (웹 세션 ID 등)
    language: str = ""                       # ko|en (빈값이면 전사 내용으로 추정)
    #: 이번 세션에서 **실제로** 전사를 만든 STT 모델·제공자(stt.run_stt(meta_out=)의 결과).
    #: 설정값이 아니라 실측이라 폴백이 일어난 회의도 기록이 사실과 맞는다. 비워 두면
    #: 녹취 출처 메타에서 STT 항목이 생략된다(틀린 값을 적는 것보다 없는 편이 낫다).
    stt_models: List[str] = field(default_factory=list)
    stt_providers: List[str] = field(default_factory=list)
    stt_fallback_used: bool = False


@dataclass
class FinalizeOptions:
    llm: Any = None                                   # 필수 — LLMClient
    do_refine: bool = True
    refined_quality_check: bool = False               # 교정본 품질 게이트
    precomputed_refined: Optional[str] = None         # 호출자가 이미 교정·검증한 교정본 (batch)
    do_actions: bool = True                           # 내부에서 doc_type=="meeting" 재확인
    do_claim_verify: Optional[bool] = None            # None → config wiki.claim_verify
    do_publish: bool = True
    do_registries: bool = True
    do_proposal: bool = True
    do_graph_sync: bool = False                       # 웹만 True (기존 동작 보존)
    notify: Optional[str] = None                      # enrich_and_publish notify
    artifacts_dir: Optional[Path] = None              # wiki_context.json 저장 위치
    proposal_dir: Optional[Path] = None               # 미지정 시 artifacts_dir
    extra_related_titles: List[str] = field(default_factory=list)   # 실시간 vault 검색 등
    #: 실시간 검색 근거 [{title, filename, heading, section_path, score, snippet, ...}]
    #: — memo 주입 + 회의록 "🔗 관련 노트" 섹션의 근거 링크로 쓰인다(FR-6)
    extra_related_evidence: List[Dict[str, Any]] = field(default_factory=list)
    extra_memo_blocks: List[str] = field(default_factory=list)      # 웹검색 보완 블록 등
    plan_match: Any = PLAN_UNSET                      # 재탐색 방지용 전달
    indexer: Any = None
    obs: Any = None
    debug_dir: Optional[str] = None
    include_web: bool = True                          # build_generation_context_memo 웹 리서치
    context_metadata: Dict[str, Any] = field(default_factory=dict)  # wiki_context.json 메타
    publish_extra: Dict[str, Any] = field(default_factory=dict)     # enrich_and_publish 추가 kwargs


class FinalizeEvents:
    """산출물/상태 훅 — 기본은 전부 no-op. 호출자가 필요한 것만 오버라이드.

    on_document doc_type: refined_script | minutes | actions | fact_check |
                          summary | script | wiki_context | wiki_proposal
    (minutes는 사실검증 반영 시 한 번 더 방출된다 — 웹 DB upsert 파리티)
    """

    def on_status(self, stage: str, message: str) -> None:  # noqa: ARG002
        pass

    def on_document(self, doc_type: str, content: str, fmt: str = "markdown") -> None:  # noqa: ARG002
        pass

    def on_stage_error(self, stage: str, exc: Exception) -> None:  # noqa: ARG002
        pass


@dataclass
class FinalizeResult:
    minutes: str = ""
    summary: str = ""
    refined_text: Optional[str] = None
    script_md: str = ""
    actions_json: str = ""
    actions_md: str = ""
    verify_md: str = ""
    claim_results: List[Dict[str, Any]] = field(default_factory=list)
    related_note_titles: List[str] = field(default_factory=list)
    context_flags: Dict[str, Any] = field(default_factory=dict)
    plan_match: Any = None
    publish_result: Dict[str, Any] = field(default_factory=dict)
    source_note: str = ""
    decisions: Optional[List[str]] = None
    errors: List[Tuple[str, str]] = field(default_factory=list)   # (stage, message)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  녹취 출처 메타 (provenance)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

#: 이 메타의 스키마 버전. 필드가 아예 없는 노트 = 이 기능 도입 이전 산출물이다.
#: (과거 회의의 실제 출처는 지금 알 수 없으므로 백필하지 않는다 — 없는 게 정직하다.)
PROVENANCE_SCHEMA = 1

#: SessionInputs.source → 사람이 읽는 녹취 방식.
#: recover 는 크래시 백업(PCM/JSONL) 복구라 원래 녹취는 실시간이었다.
_CAPTURE_METHOD = {
    "batch": "file_upload",
    "ingest": "folder_watch",
    "realtime": "realtime",
    "realtime_cli": "realtime",
    "web_realtime": "realtime",
    "recover": "realtime",
}

#: 웹 UI에서 돌았는지 터미널에서 돌았는지 — 같은 녹취 방식이라도 진입점이 다르다.
_CAPTURE_ENTRY = {
    "batch": "cli", "ingest": "cli", "realtime": "cli", "realtime_cli": "cli",
    "recover": "cli", "web_realtime": "web",
}


def _build_provenance(inputs: "SessionInputs", options: "FinalizeOptions",
                      llm: Any = None) -> Dict[str, Any]:
    """회의록 frontmatter 에 넣을 녹취 출처 메타.

    "이 회의록이 언제·어떤 방식·어떤 모델로 만들어졌나"를 산출물 자체에 남긴다.
    사용자 입력은 0개 — 파이프라인이 이미 아는 값만 쓴다.

    **모르는 것은 적지 않는다.** 모델명은 설정값이 아니라 실측(stt.run_stt(meta_out=) ·
    LLMClient.models_used)이라, 폴백이 일어난 회의도 기록이 사실과 맞는다. 실측이 없으면
    (--resume 로 기존 전사를 재사용한 경우 등) 해당 키를 비워 둔다 — 빈 값은
    build_frontmatter 가 알아서 생략한다.
    """
    from meeting_minutes_app.common.version import app_version, build_commit

    src = str(getattr(inputs, "source", "") or "")
    meta: Dict[str, Any] = {
        "provenance_schema": PROVENANCE_SCHEMA,
        "capture_method": _CAPTURE_METHOD.get(src, src or "unknown"),
        "capture_entry": _CAPTURE_ENTRY.get(src, ""),
        "tool_version": app_version(),
        "tool_build": build_commit(),
    }
    if src == "recover":
        meta["capture_note"] = "recovered"      # 크래시 백업에서 복구한 세션

    stt_models = [m for m in (getattr(inputs, "stt_models", None) or []) if m]
    if stt_models:
        meta["stt_model"] = ", ".join(dict.fromkeys(stt_models))
        providers = [p for p in (getattr(inputs, "stt_providers", None) or []) if p]
        if providers:
            meta["stt_provider"] = ", ".join(dict.fromkeys(providers))
        if getattr(inputs, "stt_fallback_used", False):
            meta["stt_fallback_used"] = True

    llm_models = [m for m in (getattr(llm, "models_used", None) or []) if m]
    if llm_models:
        meta["llm_model"] = ", ".join(dict.fromkeys(llm_models))
    return meta


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  오케스트레이터
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def run_post_session(
    inputs: SessionInputs,
    options: FinalizeOptions,
    events: Optional[FinalizeEvents] = None,
) -> FinalizeResult:
    """세션 종료 후 전체 파이프라인 실행.

    각 스테이지는 독립적으로 try/except — 부가 스테이지 실패가 회의록
    생성을 막지 않는다(기존 4개 복사본과 동일 원칙). 실패는
    result.errors에 (stage, message)로 남고 events.on_stage_error로도 전달.
    """
    ev = events or FinalizeEvents()
    res = FinalizeResult()
    llm = options.llm
    if llm is None:
        raise ValueError("FinalizeOptions.llm 은 필수입니다 (호출자가 LLMClient 주입)")

    # STT 환각·반복 정화 — 모든 진입점(배치/CLI/웹/워처)이 이 함수로 수렴하므로
    # 여기서 한 번 정화하면 교정·회의록·요약·스크립트가 전부 정화본을 쓴다.
    # 보수적 정책: 되풀이만 축약·제거하고 환각 의심은 [불명] 표시만 남긴다.
    segments = inputs.segments or []
    if _c("realtime.hallucination_filter", True):
        try:
            from meeting_minutes_app.common.text_filters import (
                sanitize_stats_line, sanitize_transcript,
            )
            segments, _san_stats = sanitize_transcript(segments, inputs.language)
            _line = sanitize_stats_line(_san_stats)
            if _line:
                print(f"  [finalize] 전사 정화: {_line}")
        except Exception as e:  # 정화 실패가 회의록 생성을 막지 않는다
            print(f"  [finalize] 전사 정화 건너뜀: {e}")
            segments = inputs.segments or []
    title = inputs.title or inputs.topic or "무제 회의"
    memo: Optional[str] = inputs.base_memo
    # 회의록 **본문 생성 프롬프트**에 이전 노트 내용을 실을지 (기본 꺼짐).
    # 아래 스테이지 1~3 은 이 값과 무관하게 계속 회수한다 — 관련 노트 목록·근거 기록·
    # 사실 검증·wiki_context.json 은 그대로이고, 본문을 '쓰는 데' 참고하지 않을 뿐이다.
    from meeting_minutes_app.meeting_pipeline.meeting_workflow import (
        minutes_vault_context_enabled as _mv_ctx,
    )
    inject_vault = _mv_ctx()

    def _stage(name: str, fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception as e:
            res.errors.append((name, f"{type(e).__name__}: {e}"))
            ev.on_stage_error(name, e)
            print(f"  [finalize:{name}] 실패 (무시): {e}")

    # ── 1. 계획 매칭 + 사전 자료 memo ──
    def _plan():
        nonlocal memo
        from meeting_minutes_app.meeting_pipeline import publish as _pub
        if options.plan_match is PLAN_UNSET:
            res.plan_match, memo = _pub.plan_context_memo(
                title, inputs.session_dt, memo, include_plan_body=inject_vault)
        else:
            res.plan_match, memo = _pub.plan_context_memo(
                title, inputs.session_dt, memo, match=options.plan_match,
                include_plan_body=inject_vault)
        if res.plan_match:
            ev.on_status("plan", f"계획 회의 매칭: {res.plan_match.get('path', '')}")
    _stage("plan_context", _plan)

    # ── 2. 호출자 추가 memo 병합 (실시간 vault 노트 / 웹검색 보완 등) ──
    def _extra_memo():
        nonlocal memo
        blocks = list(options.extra_memo_blocks or [])
        # 실시간 검색이 찾은 볼트 노트(제목·근거)는 '이전 노트 내용'이므로 본문 주입이
        # 꺼져 있으면 싣지 않는다. 회의록 말미 "🔗 관련 노트" 섹션과 세션 상세의 누적
        # 목록에는 그대로 남는다(스테이지 7.5 는 extra_related_* 를 직접 읽는다).
        if inject_vault:
            if options.extra_related_titles:
                blocks.append(
                    "[실시간 관련 노트(Vault 검색)]:\n"
                    + "\n".join(f"- [[{t}]]" for t in options.extra_related_titles[:10]))
            # 근거(섹션경로·점수·snippet)까지 주입 — 제목만 넣던 과거엔 LLM이 어느
            # 대목이 관련인지 몰라 관련 노트를 사실상 활용하지 못했다.
            ev_block = _related_evidence_memo(options.extra_related_evidence)
            if ev_block:
                blocks.append(ev_block)
        if blocks:
            memo = "\n\n".join(([memo] if memo else []) + blocks)
    _stage("extra_memo", _extra_memo)

    # ── 3. Wiki/그래프/registry/웹 컨텍스트 ──
    def _context():
        nonlocal memo
        from meeting_minutes_app.meeting_pipeline import meeting_workflow as mw
        memo2, titles, flags = mw.build_generation_context_memo(
            llm=llm,
            title=title,
            topic=inputs.topic,
            segments_or_text=segments,
            base_memo=memo,
            indexer=options.indexer,
            obs=options.obs,
            include_web=options.include_web,
            inject_vault=inject_vault,
            # '같은 인물이 같은 주제로 얘기한 자료' 회수의 씨앗 — 추정이 아니라 이번
            # 세션이 이미 아는 참석자다(계획 노트 명단 + 화자 추론 결과).
            attendees=inputs.attendees,
        )
        memo = memo2
        res.related_note_titles = mw.merge_related_note_titles(
            options.extra_related_titles, titles)
        res.context_flags = flags or {}
        # 소스별 **회수** 현황 한 줄 요약 (A5 — silent failure 가시화).
        # 본문 주입 여부는 별도로 찍는다 — 둘을 한 줄에 섞으면 "회수 실패"와
        # "일부러 주입 안 함"이 구분되지 않는다.
        stats = " ".join(
            f"{k}={'O' if res.context_flags.get(k) else 'X'}"
            for k in ("wiki", "graph", "registry", "web"))
        ev.on_status("context",
                     f"컨텍스트 회수: {stats}, 노트 {len(res.related_note_titles)}개"
                     + (" · 회의록 본문 주입 O" if inject_vault
                        else " · 회의록 본문 주입 X(이전 회의 내용 미참고)"))
    _stage("context", _context)

    if not res.related_note_titles and not res.context_flags.get("registry"):
        print("  ⚠ 관련 노트를 찾지 못했습니다 "
              "(인덱스 미구축/Obsidian 미연결 여부를 확인하세요)")

    # wiki_context.json 진단 아티팩트 저장 — 회의록 생성 성공 여부와 무관하게
    # 항상 시도한다 (실패 시에도 이 시점까지 조립된 컨텍스트를 남겨 디버깅에 사용).
    def _artifacts():
        import json as _json
        from meeting_minutes_app.wiki_core.wiki_knowledge import (
            DATA_DIR as _wk_data_dir,
            build_wiki_context_package,
            save_wiki_context_package,
        )
        meta = {
            "title": title,
            "session_dt": inputs.session_dt,
            "doc_type": inputs.doc_type,
            "source": inputs.source,
            "segment_count": len(segments),
            "context_stats": {
                k: bool(res.context_flags.get(k))
                for k in ("wiki", "graph", "registry", "web")
            },
            **(options.context_metadata or {}),
        }
        entity_map = (res.publish_result or {}).get("entities") or {}
        known_entities: List[str] = []
        for vals in entity_map.values():
            known_entities.extend(vals if isinstance(vals, list) else [])
        pkg = build_wiki_context_package(
            related_titles=res.related_note_titles,
            data_dir=_wk_data_dir,
            metadata=meta,
            filter_query=" ".join(
                [title, inputs.topic or "", res.minutes[:1500]]).strip(),
            known_entities=known_entities,
            glossary_terms=list(entity_map.get("terms", []) or []),
            related_details=(res.context_flags or {}).get("evidence", []),
        )
        path = save_wiki_context_package(pkg, Path(options.artifacts_dir))
        if pkg and path:
            ev.on_document("wiki_context",
                           _json.dumps(pkg, ensure_ascii=False, indent=2), "json")

    # ── 4. 스크립트 교정 ──
    refined_for_minutes: Optional[str] = None
    if options.precomputed_refined is not None:
        # 호출자가 교정+품질검증을 이미 수행 (batch 5b) — 내부 교정 생략
        refined_for_minutes = options.precomputed_refined
        res.refined_text = options.precomputed_refined
    elif options.do_refine:
        def _refine():
            nonlocal refined_for_minutes
            from meeting_minutes_app.meeting_pipeline.minutes_generation import (
                refine_script, _refined_script_is_usable,
            )
            refined = refine_script(segments, llm, inputs.doc_type,
                                    topic=inputs.topic, debug_dir=options.debug_dir)
            res.refined_text = refined
            if options.refined_quality_check:
                ok, reason = _refined_script_is_usable(refined, segments)
                if not ok:
                    ev.on_status("refine", f"교정본 품질 미달 → 원본 사용 ({reason})")
                    return
            refined_for_minutes = refined
            if refined:
                ev.on_document("refined_script", refined)
        _stage("refine", _refine)

    # ── 5. 회의록 ──
    def _minutes():
        from meeting_minutes_app.meeting_pipeline.minutes_generation import generate_minutes
        res.minutes = generate_minutes(
            refined_for_minutes if refined_for_minutes else segments,
            llm, inputs.doc_type,
            memo or None, options.debug_dir,
            topic=inputs.topic,
            session_dt=inputs.session_dt,
            title=title,
        )
        if res.minutes:
            ev.on_document("minutes", res.minutes)
    _stage("minutes", _minutes)

    if not res.minutes:
        # 회의록 자체가 없으면 이후 스테이지는 무의미 — 진단 아티팩트만 남기고 반환
        if options.artifacts_dir is not None:
            _stage("wiki_context", _artifacts)
        return res

    # ── 6. 액션 아이템 (meeting 전용) ──
    if options.do_actions and inputs.doc_type == "meeting":
        def _actions():
            from meeting_minutes_app.meeting_pipeline.minutes_generation import (
                extract_action_items, format_actions_md,
            )
            aj = extract_action_items(res.minutes, llm, inputs.doc_type,
                                      options.debug_dir)
            if aj:
                res.actions_json = aj
                res.actions_md = format_actions_md(aj)
                ev.on_document("actions", aj, "json")
        _stage("actions", _actions)

    # ── 7. 사실 검증 (claim_verify) ──
    do_verify = (options.do_claim_verify
                 if options.do_claim_verify is not None
                 else bool(_c("wiki.claim_verify", False)))
    if do_verify:
        def _verify():
            from meeting_minutes_app.meeting_pipeline import meeting_workflow as mw
            from meeting_minutes_app.meeting_pipeline.publish import (
                _strip_fact_verification_sections,
            )
            ev.on_status("claim_verify", "사실 검증 중 (vault 비교)...")
            idx = options.indexer or mw.load_vault_indexer()
            obs_v = options.obs or mw.load_obsidian_client()
            own_obs = obs_v is not None and options.obs is None
            try:
                verify_md, claim_results = mw.claim_verify(
                    res.minutes, llm,
                    indexer=idx, obs=obs_v,
                    topic=inputs.topic,
                    max_claims=int(_c("wiki.claim_verify_max", 8) or 8),
                    current_title=title,
                )
            finally:
                if own_obs:
                    try:
                        obs_v.close()
                    except Exception:
                        pass
            if verify_md:
                res.verify_md = verify_md
                res.claim_results = claim_results or []
                res.minutes = (_strip_fact_verification_sections(res.minutes).rstrip()
                               + "\n\n" + verify_md)
                ev.on_document("fact_check", verify_md)
                ev.on_document("minutes", res.minutes)   # 검증 반영본 재방출 (웹 파리티)
                # 집계는 **구조화 결과**에서 센다. 예전엔 마크다운의 아이콘 문자열
                # ("- ✅" 등)을 셌는데, 표기를 바꾸면 집계가 조용히 0이 됐다
                # (실제로 '확인됨'→'노트와 일치' 표현을 고칠 때 걸렸다).
                verdicts = [str((r or {}).get("verdict", "")) for r in res.claim_results]
                conflicts = sum(1 for v in verdicts if v == "conflict")
                matches = sum(1 for v in verdicts if v == "match")
                unknowns = sum(1 for v in verdicts if v not in ("conflict", "match"))
                ev.on_status("claim_verify",
                             f"노트 대조 완료: 충돌 {conflicts}, 일치 {matches}, 확인불가 {unknowns}")
            else:
                ev.on_status("claim_verify", "대조할 주장을 추출하지 못했습니다")
        _stage("claim_verify", _verify)

    # ── 7.5 관련 노트 섹션 자동 삽입 (FR-6) ──
    # 사실검증 블록이 붙은 뒤에 append 해야 검증 섹션 재작성(_strip_fact_verification_
    # sections)에 지워지지 않는다. 요약 생성 전에 넣어 요약에도 반영된다.
    if options.extra_related_evidence or options.extra_related_titles:
        def _related_section():
            if RELATED_NOTES_HEADING in res.minutes:
                return          # 이미 있으면 중복 삽입 금지 (재생성·복구 경로)
            # 노이즈 컷(FR-6): 0=제한 없음. 실시간 검색에서 이 순위 밖이었던 노트는 제외.
            _max_rank = int(_c("wiki.related_notes_max_rank", 0) or 0)
            section = build_related_notes_section(
                options.extra_related_evidence,
                options.extra_related_titles,
                max_rank=(_max_rank - 1) if _max_rank > 0 else None,
            )
            if not section:
                return
            res.minutes = res.minutes.rstrip() + "\n\n" + section
            ev.on_document("minutes", res.minutes)   # 병합본 재방출 (웹 DB 파리티)
            ev.on_status("related_notes",
                         f"관련 노트 {section.count(chr(10) + '- ')}건을 회의록에 병합")
        _stage("related_notes", _related_section)

    # ── 8. 요약 ──
    def _summary():
        from meeting_minutes_app.meeting_pipeline.minutes_generation import generate_summary
        res.summary = generate_summary(
            res.minutes, llm, inputs.doc_type, options.debug_dir,
            topic=inputs.topic, session_dt=inputs.session_dt,
        )
        if res.summary:
            ev.on_document("summary", res.summary)
    _stage("summary", _summary)

    # ── 9. 스크립트 md ──
    def _script():
        from meeting_minutes_app.meeting_pipeline.script_formatting import build_script_md
        res.script_md = build_script_md(segments)
        if res.script_md:
            ev.on_document("script", res.script_md)
    _stage("script", _script)

    # ── 10. Obsidian 발행 (+ 발행 후 인덱스 갱신) ──
    if options.do_publish:
        def _publish_stage():
            from meeting_minutes_app.meeting_pipeline import meeting_workflow as mw
            from meeting_minutes_app.meeting_pipeline.publish import enrich_and_publish
            # frontmatter `evidence` 의 뜻은 "회의록 생성에 **실제로 주입된** 근거"다
            # (obsidian.write_meeting_note 참고). 본문 주입이 꺼져 있으면 아무것도
            # 주입되지 않았으므로 비워 둔다 — 회수한 목록을 여기 적으면 "이 노트를
            # 근거로 썼다"는 거짓 기록이 남는다. 회수 결과 자체는 본문 "🔗 관련 노트"
            # 섹션과 related_notes 로 그대로 남는다.
            evidence_links = (mw.evidence_to_wikilinks(
                (res.context_flags or {}).get("evidence", [])) if inject_vault else [])
            plan_for_publish = (res.plan_match
                                if res.plan_match is not None or options.plan_match is PLAN_UNSET
                                else options.plan_match)
            publish_kwargs = dict(options.publish_extra or {})
            # 호출자가 넘긴 품질 메타(stt_segment_count 등)와 녹취 출처 메타를 한 dict 로
            # 합쳐 넘긴다. 이 조립을 **여기 한 곳**에 두는 것이 중요하다 — publish_extra 를
            # 채우는 건 배치·폴더감시뿐이라 호출자 쪽에 두면 실시간(CLI·웹) 경로가
            # 통째로 빠진다(실제로 그 두 경로는 지금까지 stt_meta 자체가 없었다).
            publish_kwargs["note_meta"] = {
                **(publish_kwargs.pop("note_meta", None) or {}),
                **_build_provenance(inputs, options, llm),
            }
            result = enrich_and_publish(
                title=title,
                doc_type=inputs.doc_type,
                minutes_md=res.minutes,
                llm=llm,
                summary_md=res.summary,
                actions_md=res.actions_md,
                topic=inputs.topic,
                session_dt=inputs.session_dt,
                attendees=inputs.attendees,
                related_notes_extra=res.related_note_titles,
                notify=options.notify,
                planned_match=plan_for_publish,
                evidence=evidence_links,
                **publish_kwargs,
            ) or {}
            res.publish_result = result
            res.source_note = result.get("obsidian_path") or ""
            # 새 노트가 vault에 쓰였으면 인덱스 갱신 (indexing.auto_reindex_after_write)
            if res.source_note:
                try:
                    from meeting_minutes_app.wiki_core.wiki_knowledge import _reindex_if_configured
                    _reindex_if_configured(mw.load_vault_indexer())
                except Exception:
                    pass
        _stage("publish", _publish_stage)

    # ── 11. Wiki 산출물 (context package + update proposal) ──
    if options.artifacts_dir is not None:
        _stage("wiki_context", _artifacts)

    if (options.do_proposal and inputs.doc_type == "meeting"
            and res.related_note_titles):
        def _proposal():
            from meeting_minutes_app.wiki_core.wiki_knowledge import (
                build_wiki_update_proposal,
                save_wiki_update_proposal,
            )
            proposal = build_wiki_update_proposal(
                meeting_title=title,
                minutes_text=res.minutes,
                related_titles=res.related_note_titles,
                llm=llm,
                claim_results=res.claim_results,
            )
            out_dir = options.proposal_dir or options.artifacts_dir
            if proposal and out_dir is not None:
                saved = save_wiki_update_proposal(proposal, Path(out_dir))
                if saved:
                    _json_path, md_path = saved
                    ev.on_document("wiki_proposal",
                                   md_path.read_text(encoding="utf-8"))
        _stage("wiki_proposal", _proposal)

    # ── 12. Registry 갱신 (meeting 전용) ──
    if options.do_registries and inputs.doc_type == "meeting":
        def _registries():
            from meeting_minutes_app.wiki_core.wiki_knowledge import (
                update_action_registry_from_actions,
                update_decision_registry_from_minutes,
                extract_decisions_from_minutes,
            )
            if res.actions_json:
                update_action_registry_from_actions(
                    res.actions_json, source_meeting=title,
                    source_note=res.source_note)
            res.decisions = extract_decisions_from_minutes(res.minutes)
            if res.decisions:
                update_decision_registry_from_minutes(
                    res.decisions, source_meeting=title,
                    source_note=res.source_note)
        _stage("registry", _registries)

    # ── 13. Graph 동기화 (옵션 — 현재 웹만) ──
    if options.do_graph_sync and inputs.doc_type == "meeting":
        def _graph():
            from meeting_minutes_app.wiki_core import graph_sync
            graph_sync.sync_session_graph(
                session_id=inputs.session_id or title,
                title=title,
                actions_json=res.actions_json or None,
                decisions=res.decisions,
                related_note_titles=res.related_note_titles,
                evidence=(res.context_flags or {}).get("evidence", []),
                source_note=res.source_note,
            )
        _stage("graph_sync", _graph)

    if res.errors:
        failed = ", ".join(stage for stage, _ in res.errors)
        print(f"  ⚠ finalize 부가 스테이지 실패: {failed} (회의록 생성엔 영향 없음)")

    return res
