#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
단일 오디오 파일에 대한 전체 처리 파이프라인 (STT -> 화자 추론 -> 번역 ->
스크립트/교정 -> 회의록/요약/액션아이템 생성 -> 후처리 발행) 오케스트레이션.
meeting_minutes.py에서 분리 (2026-07 리팩토링 3단계).
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from meeting_minutes_app.common.llm_client import LLMClient
from meeting_minutes_app.meeting_pipeline.meeting_minutes import (
    TYPE_LABELS, logger, info, warn, _c,
    load_segments_from_transcript, parse_session_dt_from_filename, _date_key_local,
    segments_to_plain_text,
)
from meeting_minutes_app.meeting_pipeline.stt import prepare_audio, run_stt, translate_segments
from meeting_minutes_app.meeting_pipeline.script_formatting import build_script_md
from meeting_minutes_app.meeting_pipeline.minutes_generation import (
    generate_minutes, generate_summary, extract_action_items, format_actions_md,
    refine_script, _refined_script_is_usable, infer_speaker_names, save,
)
from meeting_minutes_app.meeting_pipeline.publish import (
    enrich_and_publish, _lookup_plan, _clean_attendee_names, plan_context_memo,
    _strip_fact_verification_sections, _attendee_candidates, _stt_quality_meta,
)


def process_single(
    input_path: str,
    args,
    llm: LLMClient,
    output_dir: str,
    title: str,
    work_dir: str,
    file_prefix: str = "",
    memo: Optional[str] = None,
    debug_dir: Optional[str] = None,
) -> Tuple[str, Optional[str]]:
    """
    단일 파일 처리 파이프라인.
    Returns: summary 텍스트 (알림 본문용)
    """
    labels = TYPE_LABELS[args.type]
    pfx    = file_prefix
    seg_path = os.path.join(output_dir, f"{pfx}segments.json")
    transcript_path = os.path.join(output_dir, f"{pfx}transcript.md")
    force_stt = bool(getattr(args, "force_stt", False))
    stt_source = "new_stt"

    # ── 기존 STT 결과 재사용 ──
    if not force_stt and os.path.isfile(seg_path):
        info(f"기존 세그먼트 로드 (--resume): {seg_path}")
        with open(seg_path, "r", encoding="utf-8") as f:
            segments = json.load(f)
        stt_source = "segments_json"
    elif not force_stt and os.path.isfile(transcript_path):
        info(f"기존 전사 로드 (--resume): {transcript_path}")
        segments = load_segments_from_transcript(transcript_path)
        with open(seg_path, "w", encoding="utf-8") as f:
            json.dump(segments, f, ensure_ascii=False, indent=2)
        info(f"전사에서 세그먼트 복원 → {seg_path}")
        stt_source = "transcript_md"
    elif getattr(args, "resume", False):
        raise RuntimeError(
            "--resume 지정됨: 기존 segments.json/transcript.md를 찾지 못해 STT를 중단합니다. "
            "기존 출력 폴더 제목을 --title로 지정하거나 --force-stt로 새 STT를 명시하세요."
        )
    else:
        # 1. 오디오 준비
        audio_path = prepare_audio(input_path, work_dir)

        # 2. STT
        speaker_names = (
            [n.strip() for n in args.speakers.split(",") if n.strip()]
            if getattr(args, "speakers", None) else None
        )
        segments = run_stt(
            audio_path, model=args.model,
            language=getattr(args, "language", None),
            speaker_names=speaker_names,
            work_dir=work_dir, debug_dir=debug_dir,
        )
        if not segments:
            raise RuntimeError(f"STT 결과 비어있음: {input_path}")

        with open(seg_path, "w", encoding="utf-8") as f:
            json.dump(segments, f, ensure_ascii=False, indent=2)
        info(f"세그먼트 → {seg_path}")

    # 3. 화자 매핑 재사용 (--reuse-speakers)
    if getattr(args, "reuse_speakers", False):
        try:
            from meeting_minutes_app.meeting_pipeline.speaker_cache import SpeakerCache
            cache = SpeakerCache(
                os.path.join(os.path.dirname(output_dir), "speaker_map.json")
            )
            cached_key = cache.fuzzy_match(title)
            if cached_key:
                mapping = cache.get_mapping(cached_key)
                if mapping:
                    info(f"화자 매핑 재사용: [{cached_key}]")
                    for seg in segments:
                        orig = seg.get("speaker", "")
                        if orig in mapping:
                            seg["speaker"] = mapping[orig]
        except ImportError:
            pass

    # 3b. 화자 이름 LLM 추론 (diarize 모델 사용 시 'Speaker A' → 실명/역할)
    # 계획 매칭 1회 탐색 — 화자 추론(참석자 힌트)·사전자료·발행에 공통 사용
    session_dt = getattr(args, 'session_dt', '') or parse_session_dt_from_filename(input_path)
    _plan_match = None
    try:
        _plan_match = _lookup_plan(title, session_dt)
    except Exception as _e:
        logger.warning(f"[plan] 계획 노트 탐색 실패: {_e}")

    unique_spks = {s.get("speaker", "") for s in segments if s.get("speaker")}
    has_generic_labels = any(
        re.match(r'[Ss]peaker[\s_]?[A-Za-z0-9]', spk) for spk in unique_spks
    )
    if has_generic_labels:
        known_names_arg = ([n.strip() for n in args.speakers.split(",") if n.strip()]
                           if getattr(args, "speakers", None) else [])
        if _plan_match:  # 계획 노트 참석자(직책 제거)를 화자 추론 힌트로 자동 주입
            for nm in _clean_attendee_names((_plan_match.get("meta") or {}).get("attendees")):
                if nm not in known_names_arg:
                    known_names_arg.append(nm)
        if known_names_arg:
            info(f"화자 추론 힌트(참석자): {', '.join(known_names_arg)}")
        try:
            inferred = infer_speaker_names(segments, llm, known_names=known_names_arg or None)
            if inferred:
                info(f"화자 추론 결과: {inferred}")
                for seg in segments:
                    orig = seg.get("speaker", "")
                    if orig in inferred:
                        seg["speaker"] = inferred[orig]
        except Exception as e:
            warn(f"화자 이름 추론 실패 ({e}) → 원본 레이블 유지")

    # 4. 번역
    segments_for_doc = segments
    if getattr(args, "translate", False):
        seg_ko_path = os.path.join(output_dir, f"{pfx}segments_translated.json")
        if getattr(args, "resume", False) and os.path.isfile(seg_ko_path):
            info("기존 번역 세그먼트 로드 (--resume)")
            with open(seg_ko_path, "r", encoding="utf-8") as f:
                segments_for_doc = json.load(f)
        else:
            segments_for_doc = translate_segments(segments, llm, debug_dir=debug_dir)
            with open(seg_ko_path, "w", encoding="utf-8") as f:
                json.dump(segments_for_doc, f, ensure_ascii=False, indent=2)

    # 5. 스크립트 (원본 raw 보존)
    script_md = build_script_md(segments)
    save(script_md, os.path.join(output_dir, f"{pfx}script.md"), "스크립트")
    save(script_md, transcript_path, "전사")

    if getattr(args, "translate", False) and getattr(args, "translate_script", False):
        script_ko = build_script_md(segments_for_doc, include_original=True)
        save(script_ko, os.path.join(output_dir, f"{pfx}script_ko.md"), "스크립트 (한국어)")

    # 5b. STT 교정 — 회의록 생성 전에 실행하여 교정본을 입력으로 사용
    topic_str = getattr(args, 'topic', '') or ""
    refined_text: Optional[str] = None
    try:
        refined_text = refine_script(
            segments_for_doc, llm, args.type,
            topic=topic_str, debug_dir=debug_dir,
        )
        usable, reason = _refined_script_is_usable(refined_text, segments_for_doc)
        if usable:
            save(refined_text,
                 os.path.join(output_dir, f"{pfx}script_refined.txt"), "교정 스크립트")
        else:
            rejected = (
                "[REJECTED] 교정 결과가 회의록 입력 품질 기준을 통과하지 못해 원본 STT를 사용합니다.\n"
                f"사유: {reason}\n\n"
                "---- LLM 교정 결과 ----\n"
                f"{refined_text or ''}"
            )
            save(rejected,
                 os.path.join(output_dir, f"{pfx}script_refined.txt"), "교정 스크립트(미사용)")
            warn(f"교정본 품질 검증 실패 → 원본 STT로 회의록 생성 ({reason})")
            refined_text = None
    except Exception as e:
        warn(f"STT 교정 실패 ({e}) → 원본 스크립트로 회의록 생성")

    # 6. 회의록 — 교정본 우선, 실패 시 원본 segments 사용
    full_memo = memo or ""
    if getattr(args, "custom_prompt", None):
        full_memo = (full_memo + f"\n\n[추가 지시]: {args.custom_prompt}").strip()

    # 6-0. [공용] 사전 자료 주입 (계획 매칭은 위에서 1회 탐색해 화자 추론에도 사용)
    related_note_titles: List[str] = []
    context_flags: Dict[str, Any] = {}
    from meeting_minutes_app.meeting_pipeline import meeting_workflow as mw
    try:
        _, full_memo = plan_context_memo(title, session_dt, full_memo, match=_plan_match)
        if _plan_match:
            info(f"계획 회의 매칭: {_plan_match.get('path')} (사유: {_plan_match.get('reason','')})")
    except Exception as e:
        warn(f"계획 매칭/사전자료 주입 실패: {e}")

    # 6-1. Obsidian Wiki/온라인 배경 컨텍스트 주입.
    # 실패해도 회의록 생성 자체는 계속 진행한다.
    try:
        full_memo, related_note_titles, context_flags = mw.build_generation_context_memo(
            llm=llm,
            title=title,
            topic=topic_str,
            segments_or_text=segments_for_doc,
            base_memo=full_memo,
        )
        if context_flags.get("wiki"):
            info(f"Obsidian Wiki 컨텍스트 주입: {len(related_note_titles)}개 노트")
        if context_flags.get("web"):
            info("웹 리서치 컨텍스트 주입")
    except Exception as e:
        warn(f"Obsidian Wiki 컨텍스트 주입 실패: {e}")

    # Wiki Context Package 저장 (wiki_context.json)
    try:
        from meeting_minutes_app.wiki_core.wiki_knowledge import build_wiki_context_package, save_wiki_context_package
        entities_for_context: List[str] = []
        _ctx_pkg = build_wiki_context_package(
            related_titles=related_note_titles,
            data_dir=Path(__file__).resolve().parent.parent.parent / "data",
            metadata={
                "title": title,
                "session_dt": session_dt,
                "session_date": _date_key_local(session_dt),
                "source_file": Path(input_path).name,
                "source_file_date": _date_key_local(parse_session_dt_from_filename(input_path)),
                "doc_type": args.type,
                "stt_source": stt_source,
            },
            filter_query=" ".join([title, topic_str, segments_to_plain_text(segments_for_doc)[:1000]]),
            known_entities=entities_for_context,
            related_details=context_flags.get("evidence", []),
        )
        save_wiki_context_package(_ctx_pkg, Path(output_dir))
    except Exception as _cpe:
        warn(f"wiki_context.json 저장 실패 (무시): {_cpe}")

    minutes = generate_minutes(
        refined_text if refined_text else segments_for_doc,
        llm, args.type,
        full_memo or None, debug_dir,
        topic=topic_str,
        session_dt=session_dt,
        title=title,
    )

    verify_md = ""
    claim_results: List[Dict[str, Any]] = []
    if _c("wiki.claim_verify", False):
        obs_for_verify = None
        try:
            from meeting_minutes_app.meeting_pipeline import meeting_workflow as mw
            info("사실 검증 중 (vault 비교)...")
            indexer_for_verify = mw.load_vault_indexer()
            obs_for_verify = mw.load_obsidian_client()
            verify_md, claim_results = mw.claim_verify(
                minutes,
                llm,
                indexer=indexer_for_verify,
                obs=obs_for_verify,
                topic=topic_str,
                max_claims=int(_c("wiki.claim_verify_max", 8) or 8),
                current_title=title,
            )
            if verify_md:
                conflicts = verify_md.count("- ⚠️")
                matches = verify_md.count("- ✅")
                unknowns = verify_md.count("- ❓") + verify_md.count("- 🔍")
                info(f"사실 검증 완료: 충돌 {conflicts}, 일치 {matches}, 확인불가 {unknowns}")
                minutes = _strip_fact_verification_sections(minutes).rstrip() + "\n\n" + verify_md
            else:
                warn("사실 검증 결과 없음: 검증 가능한 주장을 추출하지 못했습니다")
        except Exception as e:
            warn(f"사실 검증 실패 (무시): {e}")
        finally:
            if obs_for_verify:
                try:
                    obs_for_verify.close()
                except Exception:
                    pass

    header = (
        f"<!-- Generated: {datetime.now().isoformat()} -->\n"
        f"<!-- Source: {Path(input_path).name} | Type: {args.type} | "
        f"STT: {args.model} | LLM: {args.llm} -->\n\n"
    )
    save(header + minutes,
         os.path.join(output_dir, f"{pfx}minutes.md"), labels["title"])

    # 7. 요약
    summary = generate_summary(minutes, llm, args.type, debug_dir,
                                topic=topic_str, session_dt=session_dt)
    save(summary, os.path.join(output_dir, f"{pfx}summary.md"), "요약본")

    # 8. 액션 아이템 추출 (meeting 전용)
    actions_json = extract_action_items(minutes, llm, args.type, debug_dir)
    if actions_json:
        save(actions_json,
             os.path.join(output_dir, f"{pfx}actions.json"), "액션 아이템 (JSON)")
        save(format_actions_md(actions_json),
             os.path.join(output_dir, f"{pfx}actions.md"), "액션 아이템 (마크다운)")

    # 9. 후처리: 용어 보완 + Obsidian 기록 (이메일은 main 루프가 일괄 발송)
    _obs_path: Optional[str] = None
    enr: Dict[str, Any] = {"entities": {}, "glossary_md": "", "related_notes": [], "sources": []}
    try:
        actions_md = format_actions_md(actions_json) if actions_json else ""
        attendees_for_doc = _attendee_candidates(segments_for_doc, _plan_match)
        stt_meta = _stt_quality_meta(
            segments_for_doc,
            refined_text,
            bool(refined_text),
            stt_source,
        )
        enr = enrich_and_publish(
            title=title, doc_type=args.type, minutes_md=minutes, llm=llm,
            summary_md=summary, actions_md=actions_md,
            topic=topic_str, session_dt=session_dt,
            attendees=attendees_for_doc,
            related_notes_extra=related_note_titles,
            planned_match=_plan_match,   # 1회 탐색 결과 재사용(중복 탐색 방지)
            source_audio=input_path,
            source_file_date=_date_key_local(parse_session_dt_from_filename(input_path)),
            stt_meta=stt_meta,
            transcript_md=script_md,
            evidence=mw.evidence_to_wikilinks(context_flags.get("evidence", [])),
        )
        _obs_path = enr.get("obsidian_path") or None
        # 로컬 minutes.md 에도 용어·배경 + 웹 검색 추가 자료 append
        glossary = enr.get("glossary_md", "")
        web_sources = enr.get("web_sources_md", "")
        with open(os.path.join(output_dir, f"{pfx}minutes.md"),
                  "a", encoding="utf-8") as f:
            if glossary:
                f.write(f"\n\n## 용어·배경\n\n{glossary}\n")
            if web_sources:
                f.write(f"\n\n{web_sources}\n")
    except Exception as e:
        warn(f"후처리(용어/Obsidian) 실패 → 본문은 정상 저장됨: {e}")

    # Wiki Context Package 최종 저장: enrichment 엔티티와 정제된 registry 반영
    try:
        from meeting_minutes_app.wiki_core.wiki_knowledge import build_wiki_context_package, save_wiki_context_package
        entity_map = enr.get("entities") or {}
        known_entities = []
        for vals in entity_map.values():
            known_entities.extend(vals if isinstance(vals, list) else [])
        glossary_terms = list(entity_map.get("terms", []) or [])
        _ctx_pkg = build_wiki_context_package(
            related_titles=related_note_titles,
            data_dir=Path(__file__).resolve().parent.parent.parent / "data",
            metadata={
                "title": title,
                "session_dt": session_dt,
                "session_date": _date_key_local(session_dt),
                "source_file": Path(input_path).name,
                "source_file_date": _date_key_local(parse_session_dt_from_filename(input_path)),
                "doc_type": args.type,
                "stt_source": stt_source,
                **_stt_quality_meta(segments_for_doc, refined_text, bool(refined_text), stt_source),
            },
            known_entities=known_entities,
            glossary_terms=glossary_terms,
            filter_query=" ".join([title, topic_str, minutes[:1500]]),
            related_details=context_flags.get("evidence", []),
        )
        save_wiki_context_package(_ctx_pkg, Path(output_dir))
    except Exception as _cpe:
        warn(f"wiki_context.json 최종 저장 실패 (무시): {_cpe}")

    # 10. Wiki Registry 갱신 (실패해도 회의록 결과에 영향 없음)
    if args.type == "meeting":
        try:
            from meeting_minutes_app.wiki_core.wiki_knowledge import (
                update_action_registry_from_actions,
                update_decision_registry_from_minutes,
                extract_decisions_from_minutes,
            )
            obs_note = _obs_path or ""
            if actions_json:
                update_action_registry_from_actions(
                    actions_json,
                    source_meeting=title,
                    source_note=obs_note,
                )
            decisions = extract_decisions_from_minutes(minutes)
            if decisions:
                update_decision_registry_from_minutes(
                    decisions,
                    source_meeting=title,
                    source_note=obs_note,
                )
        except Exception as _wke:
            warn(f"Wiki Registry 갱신 실패 (무시): {_wke}")

        # ── Wiki Update Proposal ──
        if related_note_titles:
            try:
                from meeting_minutes_app.wiki_core.wiki_knowledge import (
                    build_wiki_update_proposal,
                    save_wiki_update_proposal,
                )
                _proposal = build_wiki_update_proposal(
                    meeting_title=title,
                    minutes_text=minutes,
                    related_titles=related_note_titles,
                    llm=llm,
                    claim_results=claim_results,
                )
                from meeting_minutes_app.common import config_loader as _cfg
                _root_out = Path(__file__).resolve().parent.parent.parent / str(_cfg.get("output_dir", "output"))
                save_wiki_update_proposal(_proposal, _root_out)
            except Exception as _wpe:
                warn(f"Wiki Update Proposal 생성 실패 (무시): {_wpe}")

    return summary, _obs_path
