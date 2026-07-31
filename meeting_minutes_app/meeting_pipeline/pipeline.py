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
from typing import Optional, Tuple

from meeting_minutes_app.common.llm_client import LLMClient
from meeting_minutes_app.meeting_pipeline.meeting_minutes import (
    TYPE_LABELS, logger, info, warn, _c,
    load_segments_from_transcript, parse_session_dt_from_filename, _date_key_local,
)
from meeting_minutes_app.meeting_pipeline.stt import (
    prepare_audio, run_stt, translate_segments, review_translation_segments,
)
from meeting_minutes_app.meeting_pipeline.script_formatting import build_script_md
from meeting_minutes_app.meeting_pipeline.minutes_generation import (
    refine_script, _refined_script_is_usable, infer_speaker_names, save,
)
from meeting_minutes_app.meeting_pipeline.publish import (
    _lookup_plan, _clean_attendee_names, _attendee_candidates, _stt_quality_meta,
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
    progress_cb=None,
    stt_meta_out: Optional[dict] = None,
) -> Tuple[str, Optional[str]]:
    """
    단일 파일 처리 파이프라인.
    Returns: summary 텍스트 (알림 본문용)

    progress_cb: 선택. progress_cb(percent:int, stage:str) 형태로 단계 진행을 보고.
                 지정하지 않으면(CLI 등) 아무 동작도 하지 않는다.

    stt_meta_out: 선택. dict 를 주면 **실제로 전사를 만든** 제공자·모델을 채워 준다
                 (`stt_models`, `stt_providers`, `stt_fallback_used`). 반환 시그니처를
                 바꾸면 CLI 호출부가 깨지므로 `run_stt(meta_out=...)` 과 같은 out-param
                 방식을 쓴다. 웹은 이 값을 세션에 기록해 벤더 전환을 화면에 표시한다 —
                 과거에는 폴백 사실이 노트 frontmatter 에만 남아 배치·업로드 사용자는
                 자기 회의가 다른 벤더로 갔는지 알 수 없었다.
    """
    def _p(pct: int, stage: str):
        if progress_cb:
            try:
                progress_cb(pct, stage)
            except Exception:
                pass

    _p(3, "오디오 준비 중")
    labels = TYPE_LABELS[args.type]
    pfx    = file_prefix
    seg_path = os.path.join(output_dir, f"{pfx}segments.json")
    transcript_path = os.path.join(output_dir, f"{pfx}transcript.md")
    force_stt = bool(getattr(args, "force_stt", False))
    stt_source = "new_stt"
    # 이번 실행에서 실제로 STT 를 돌렸을 때만 채워진다(--resume 재사용 경로는 빈 채로
    # 남는다 — 그 경우 어느 모델이 만든 전사인지 지금 알 수 없으므로 기록하지 않는다).
    stt_used: dict = {}

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
        _p(10, "음성 인식(STT) 중 — 가장 오래 걸리는 단계입니다")
        speaker_names = (
            [n.strip() for n in args.speakers.split(",") if n.strip()]
            if getattr(args, "speakers", None) else None
        )
        segments = run_stt(
            audio_path, model=args.model,
            language=getattr(args, "language", None),
            speaker_names=speaker_names,
            work_dir=work_dir, debug_dir=debug_dir,
            meta_out=stt_used,
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

    _p(55, "전사 완료 · 후처리 중")

    # 4. STT 교정 (원문 언어) — 번역·회의록 생성 '전에' 원문 STT 오류를 먼저 교정한다.
    #    과거엔 번역(EN→KR) 뒤 한국어 세그먼트를 교정해, refine이 영어 원문을 보지 못하고
    #    (오역을 검증·수정할 수 없고) 회의록도 '번역→교정' 이중 손실 텍스트로 생성됐다.
    #    이제 원문을 교정해 회의록 입력(precomputed_refined)으로 쓰고 — 회의록은 어차피
    #    한국어로 출력되므로 원문 언어 교정본이 오히려 손실이 적다 — 번역은 그 뒤에 한다.
    _p(60, "STT 교정 중")
    topic_str = getattr(args, 'topic', '') or ""
    refined_text: Optional[str] = None
    try:
        refined_text = refine_script(
            segments, llm, args.type,
            topic=topic_str, debug_dir=debug_dir,
        )
        usable, reason = _refined_script_is_usable(refined_text, segments)
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

    # 5. 번역 (원문 세그먼트 → 한국어) — 전사 표시·세그먼트 폴백용.
    segments_for_doc = segments
    if getattr(args, "translate", False):
        _p(70, "번역 중")
        seg_ko_path = os.path.join(output_dir, f"{pfx}segments_translated.json")
        if getattr(args, "resume", False) and os.path.isfile(seg_ko_path):
            info("기존 번역 세그먼트 로드 (--resume)")
            with open(seg_ko_path, "r", encoding="utf-8") as f:
                segments_for_doc = json.load(f)
        else:
            segments_for_doc = translate_segments(segments, llm, debug_dir=debug_dir)
            # 번역 검수 패스 — 원문·번역을 병치해 주제 맥락으로 오역·누락만 교정(문장 정합
            # 유지). 번역과 별도 LLM 호출이라 비용이 늘어 config 로 켜고 끈다(기본 켜짐).
            if _c("stt.translation_review", True):
                _p(75, "번역 검수 중")
                segments_for_doc = review_translation_segments(
                    segments_for_doc, llm, topic=topic_str, debug_dir=debug_dir)
            with open(seg_ko_path, "w", encoding="utf-8") as f:
                json.dump(segments_for_doc, f, ensure_ascii=False, indent=2)

    # 6. 스크립트 파일 (원본 raw 보존)
    script_md = build_script_md(segments)
    save(script_md, os.path.join(output_dir, f"{pfx}script.md"), "스크립트")
    save(script_md, transcript_path, "전사")

    if getattr(args, "translate", False) and getattr(args, "translate_script", False):
        script_ko = build_script_md(segments_for_doc, include_original=True)
        save(script_ko, os.path.join(output_dir, f"{pfx}script_ko.md"), "스크립트 (한국어)")

    # 7. 발행용 전사 노트
    #  - 번역 OFF: 원문 언어 교정본(품질 게이트 통과 시)을 전사 노트로 — 오탈자·고유명사
    #    가 정리된 깔끔한 전사가 남는다.
    #  - 번역 ON: 교정본은 '원문 언어'라 한국어 표시 노트로는 부적합 → 한국어(원문 병기)
    #    전사를 발행한다. (번역 품질은 8단계 번역 검수 패스가 별도로 다듬는다.)
    transcript_for_publish = script_md
    if getattr(args, "translate", False):
        transcript_for_publish = build_script_md(segments_for_doc, include_original=True)
    elif refined_text:
        transcript_for_publish = (
            "# 스크립트 (Transcript, 교정본)\n\n"
            f"> 생성: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"> 세그먼트: {len(segments)}개 · STT 교정 적용(오탈자·고유명사 수정)\n\n"
            "---\n\n"
            + refined_text.strip()
        )

    # 6~10. [공용] 종료 후 파이프라인 — finalize.run_post_session
    # (컨텍스트 주입 → 회의록 → 액션 → 사실검증 → 요약 → 발행 → wiki 산출물/registry)
    # 과거 이 자리에 있던 개별 스테이지들은 meeting_pipeline/finalize.py로 통합됐다.
    full_memo = memo or ""
    if getattr(args, "custom_prompt", None):
        full_memo = (full_memo + f"\n\n[추가 지시]: {args.custom_prompt}").strip()

    _p(80, "회의록·요약 생성 중")

    from meeting_minutes_app.meeting_pipeline import finalize as fz

    _session_inputs = fz.SessionInputs(
        segments=segments_for_doc,
        title=title,
        topic=topic_str,
        doc_type=args.type,
        session_dt=session_dt,
        base_memo=full_memo or None,
        source="batch",
        attendees=_attendee_candidates(segments_for_doc, _plan_match),
        language=getattr(args, "language", "") or "",
        stt_models=stt_used.get("stt_models", []),
        stt_providers=stt_used.get("stt_providers", []),
        stt_fallback_used=bool(stt_used.get("stt_fallback_used")),
    )

    # 실측 제공자를 호출부(웹 세션 기록)로 넘긴다. 노트 frontmatter 에만 남기면
    # 배치·업로드 사용자는 벤더 전환을 알 수 없다.
    if stt_meta_out is not None:
        stt_meta_out.update({
            "stt_models": list(stt_used.get("stt_models", [])),
            "stt_providers": list(stt_used.get("stt_providers", [])),
            "stt_fallback_used": bool(stt_used.get("stt_fallback_used")),
        })

    def header() -> str:
        """로컬 산출물 맨 위 주석 — 볼트 노트 frontmatter 와 **같은 dict** 에서 렌더한다.

        저장 시점에 평가해야 한다: llm.models_used 는 회의록 생성 중에 채워지므로
        미리 만들어 두면 LLM 항목이 비어 버린다(예전 리터럴은 폴백 전 **설정값**을 적어
        폴백이 일어난 회의에 틀린 값이 남았다)."""
        from meeting_minutes_app.wiki_core.note_builder import render_provenance_comment
        return render_provenance_comment(
            fz._build_provenance(_session_inputs, None, llm),
            generated_at=datetime.now().isoformat(timespec="seconds"),
            extra={"원본": Path(input_path).name},
        )

    class _BatchEvents(fz.FinalizeEvents):
        """finalize 산출물 → output 폴더 파일 저장 (batch 파일명 규칙)."""

        def on_status(self, stage, message):
            info(message)

        def on_document(self, dtype, content, fmt="markdown"):
            try:
                if dtype == "minutes":
                    save(header() + content,
                         os.path.join(output_dir, f"{pfx}minutes.md"), labels["title"])
                elif dtype == "summary":
                    # 요약본에도 붙인다 — 메일 첨부로 이것만 받아 보는 경우가 있는데
                    # 예전엔 minutes.md 에만 있어 출처를 확인할 수 없었다.
                    save(header() + content,
                         os.path.join(output_dir, f"{pfx}summary.md"), "요약본")
                elif dtype == "actions":
                    save(content, os.path.join(output_dir, f"{pfx}actions.json"),
                         "액션 아이템 (JSON)")
                # refined_script/script는 5·5b에서 이미 저장, fact_check는 본문에 병합됨
            except Exception as e:
                warn(f"{dtype} 저장 실패 (무시): {e}")

        def on_stage_error(self, stage, exc):
            warn(f"[{stage}] 실패 (무시): {exc}")

    # 교정본은 '원문 언어'이므로 품질 지표(refined_ratio)도 원문 세그먼트 기준으로 계산
    stt_meta = _stt_quality_meta(segments, refined_text, bool(refined_text), stt_source)
    # 파일명에서 날짜를 못 뽑으면 오디오 mtime 을 쓴다 — 예전엔 여기가 빈 값이면
    # obsidian 이 '오늘'로 폴백해, 같은 오디오를 다른 날 재처리할 때 회의록 파일명이
    # 달라지고 같은 회의의 노트가 하나 더 생겼다. mtime 은 재처리에 불변이다.
    source_file_date = _date_key_local(parse_session_dt_from_filename(input_path))
    if not source_file_date:
        try:
            source_file_date = datetime.fromtimestamp(
                os.path.getmtime(input_path)).strftime("%Y-%m-%d")
        except OSError:
            pass
    _root_out = Path(__file__).resolve().parent.parent.parent / str(_c("output_dir", "output"))

    res = fz.run_post_session(
        _session_inputs,
        fz.FinalizeOptions(
            llm=llm,
            do_refine=False,
            precomputed_refined=refined_text,   # 5b에서 품질 게이트까지 통과한 교정본
            plan_match=_plan_match,             # 위에서 1회 탐색한 결과 재사용
            artifacts_dir=Path(output_dir),
            proposal_dir=_root_out,             # proposal은 루트 output에 저장 (기존 규칙)
            debug_dir=debug_dir,
            context_metadata={
                "session_date": _date_key_local(session_dt),
                "source_file": Path(input_path).name,
                "source_file_date": source_file_date,
                "stt_source": stt_source,
                **stt_meta,
            },
            publish_extra={
                "source_audio": input_path,
                "source_file_date": source_file_date,
                "note_meta": stt_meta,
                "transcript_md": transcript_for_publish,
                "force_republish": bool(getattr(args, "force_republish", False)),
            },
        ),
        _BatchEvents(),
    )

    minutes = res.minutes
    summary = res.summary
    _obs_path = res.source_note or None

    if res.actions_md:
        save(res.actions_md, os.path.join(output_dir, f"{pfx}actions.md"),
             "액션 아이템 (마크다운)")

    # 로컬 minutes.md 에도 용어·배경 + 웹 검색 추가 자료 append (Obsidian 노트와 동일)
    try:
        glossary = (res.publish_result or {}).get("glossary_md", "")
        web_sources = (res.publish_result or {}).get("web_sources_md", "")
        if minutes and (glossary or web_sources):
            with open(os.path.join(output_dir, f"{pfx}minutes.md"),
                      "a", encoding="utf-8") as f:
                if glossary:
                    f.write(f"\n\n## 용어·배경\n\n{glossary}\n")
                if web_sources:
                    f.write(f"\n\n{web_sources}\n")
    except Exception as e:
        warn(f"용어·배경 append 실패 (무시): {e}")

    # 세션 메타데이터 저장 — web UI(session_scanner._find_meta)가 CLI/배치로 처리된
    # 세션의 doc_type/language/번역 여부/재생시간을 알아내는 유일한 통로.
    # realtime_transcription.py._save_meta()와 필드명을 맞춘다(배치는 비용 추정 생략).
    try:
        duration_sec = max((s.get("end", 0) for s in segments_for_doc), default=0)
        meta = {
            "doc_type": args.type,
            "language": getattr(args, "language", None) or "",
            "translate": bool(getattr(args, "translate", False)),
            "stt_model": args.model,
            "topic": topic_str,
            "duration_sec": round(duration_sec, 1),
        }
        with open(os.path.join(output_dir, f"{pfx}meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    except Exception as e:
        warn(f"메타데이터 저장 실패 (무시): {e}")

    return summary, _obs_path
