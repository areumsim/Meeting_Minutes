#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
오디오 준비 → STT(OpenAI Transcription API) → 영→한 번역.
meeting_minutes.py에서 분리 (2026-07 리팩토링 3단계).
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from meeting_minutes_app.common.llm_client import (
    LLMClient, OPENAI_API_KEY, get_api_key, make_openai_client,
)
from meeting_minutes_app.common.text_filters import is_cjk_hallucination as _is_cjk_hallucination
from meeting_minutes_app.meeting_pipeline import meeting_minutes as _mm
from meeting_minutes_app.meeting_pipeline.meeting_minutes import (
    DEFAULT_STT_MODEL, FALLBACK_STT_MODEL, MAX_FILE_SIZE_MB, MAX_CHUNK_DURATION_SEC,
    MIN_STT_CHARS_PER_SEC, MAX_STT_RETRY_SPLIT_DEPTH, UPLOAD_FORMATS,
    logger, step, info, ok, warn, debug_save,
    ts, file_mb, run_cmd, audio_duration, FFMPEG,
)


def _refresh_config_globals() -> None:
    """config_loader.reload() 훅 — 위 from-import 로 복사된 키/모델 전역을
    웹 UI 설정 저장 시 재시작 없이 갱신한다(원본 모듈 훅이 먼저 실행됨)."""
    global OPENAI_API_KEY, DEFAULT_STT_MODEL, FALLBACK_STT_MODEL
    from meeting_minutes_app.common import llm_client as _llm
    OPENAI_API_KEY = _llm.OPENAI_API_KEY
    DEFAULT_STT_MODEL = _mm.DEFAULT_STT_MODEL
    FALLBACK_STT_MODEL = _mm.FALLBACK_STT_MODEL


try:
    from meeting_minutes_app.common import config_loader as _cfg_mod
    _cfg_mod.on_reload(_refresh_config_globals)
except ImportError:
    pass


# ──────────────────────────────────────────────
#  오디오 준비
# ──────────────────────────────────────────────
def _audio_filters() -> List[str]:
    """config(stt.*)에 따라 STT 전처리용 ffmpeg 오디오 필터 목록을 만든다.

    - loudnorm(기본 켜짐): 음량 정규화. 마이크 게인이 낮은 녹음도 STT 정확도가 오른다.
      타임라인을 바꾸지 않아 타임스탬프에 안전하다.
    - silenceremove(기본 꺼짐): 무음 구간 제거. 파일이 짧아져 비용·환각이 줄지만
      무음을 지워 타임스탬프가 실제 경과시간과 어긋난다 → 고급 설정에서만 켠다.
    """
    try:
        from meeting_minutes_app.common import config_loader as cfg
        loudnorm = bool(cfg.get("stt.preprocess_audio", True))
        trim = bool(cfg.get("stt.trim_silence", False))
    except Exception:
        loudnorm, trim = True, False
    filters: List[str] = []
    if loudnorm:
        # 방송 표준(EBU R128) 근사 — 발화용으로 무난한 값.
        filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")
    if trim:
        # 앞뒤 무음을 다듬고, 1.5초 이상 이어지는 내부 무음을 제거(-45dB 이하를 무음으로).
        filters.append(
            "silenceremove=start_periods=1:start_duration=0.2:start_threshold=-45dB:"
            "stop_periods=-1:stop_duration=1.5:stop_threshold=-45dB"
        )
    return filters


def prepare_audio(input_path: str, work_dir: str) -> str:
    step("오디오 준비 중...")
    ext  = Path(input_path).suffix.lower()
    size = file_mb(input_path)
    info(f"입력: {Path(input_path).name}  ({size:.1f} MB, {ext})")
    logger.debug(f"입력 파일: {input_path}, {size:.2f}MB")

    filters = _audio_filters()
    can_direct = size <= MAX_FILE_SIZE_MB and ext in UPLOAD_FORMATS

    # 전처리가 필요 없고(필터 없음) 직접 업로드 가능하면 재인코딩 없이 그대로 — 기존 빠른 경로.
    if not filters and can_direct:
        info(f"포맷 {ext}, {size:.1f}MB → 변환 없이 직접 업로드")
        return input_path

    out = os.path.join(work_dir, Path(input_path).stem + ".mp3")
    base_cmd = [FFMPEG, "-y", "-i", input_path, "-vn", "-ar", "16000", "-ac", "1", "-b:a", "48k"]
    cmd = base_cmd + (["-af", ",".join(filters)] if filters else []) + [out]
    if filters:
        info(f"오디오 전처리·변환 중... (필터: {', '.join(f.split('=')[0] for f in filters)})")
    else:
        info(f"mp3 변환 중... (원본 {size:.1f}MB)")
    try:
        run_cmd(cmd)
    except Exception as e:
        # 전처리(필터) 실패 시: 직접 업로드 가능하면 원본으로, 아니면 필터 없이 단순 변환 재시도.
        # (비개발자 사용 환경에서 전처리 오류로 전체 처리가 멈추지 않도록 방어)
        warn(f"오디오 전처리 실패 ({e}) → 필터 없이 진행")
        if can_direct:
            return input_path
        run_cmd(base_cmd + [out])
    new_size = file_mb(out)
    ok(f"오디오 준비 완료: {size:.1f}MB → {new_size:.1f}MB  ({out})")
    return out


def _extract_audio_segment(audio_path: str, offset: float, duration: float, out_path: str) -> str:
    """audio_path의 [offset, offset+duration) 구간을 out_path(mp3)로 추출."""
    run_cmd([
        FFMPEG, "-y", "-i", audio_path,
        "-ss", str(offset), "-t", str(duration),
        "-ar", "16000", "-ac", "1", "-b:a", "48k", out_path,
    ])
    return out_path


def split_audio(audio_path: str, work_dir: str) -> List[Tuple[str, float]]:
    """파일 크기(25MB) 또는 길이(1200s) 초과 시 청크 분할."""
    size = file_mb(audio_path)
    dur  = audio_duration(audio_path)
    logger.debug(f"오디오: {size:.2f}MB, {dur:.1f}s ({ts(dur)})")

    if size <= MAX_FILE_SIZE_MB and dur <= MAX_CHUNK_DURATION_SEC:
        return [(audio_path, 0.0)]

    # 크기 기준과 시간 기준 중 더 많은 청크 수 사용
    n_by_size = math.ceil(size / (MAX_FILE_SIZE_MB * 0.85)) if size > MAX_FILE_SIZE_MB else 1
    n_by_dur  = math.ceil(dur / MAX_CHUNK_DURATION_SEC) if dur > MAX_CHUNK_DURATION_SEC else 1
    n         = max(n_by_size, n_by_dur)

    info(f"파일 {size:.1f}MB, {dur:.0f}s → {n}개 청크 분할")
    chunk_dur = dur / n
    stem      = Path(audio_path).stem
    chunks    = []

    for i in range(n):
        offset = i * chunk_dur
        cp     = os.path.join(work_dir, f"{stem}_chunk{i:03d}.mp3")
        _extract_audio_segment(audio_path, offset, chunk_dur, cp)
        logger.debug(f"  청크 {i}: offset={ts(offset)}, {file_mb(cp):.2f}MB")
        chunks.append((cp, offset))

    info(f"{n}개 청크 생성")
    return chunks


# ──────────────────────────────────────────────
#  STT — OpenAI Transcription API
# ──────────────────────────────────────────────
def transcribe_chunk(
    client, audio_path: str, model: str,
    language: Optional[str] = None,
    speaker_names: Optional[List[str]] = None,
    offset: float = 0.0,
    debug_dir: Optional[str] = None,
    chunk_index: int = 0,
    prompt: Optional[str] = None,
) -> List[Dict]:
    use_diarize = "diarize" in model
    use_whisper = model.startswith("whisper")
    logger.debug(f"[STT] model={model}, file={audio_path}, "
                 f"{file_mb(audio_path):.2f}MB, offset={offset:.1f}s")

    f = open(audio_path, "rb")
    try:
        params: Dict[str, Any] = {"model": model, "file": f}

        if use_diarize:
            params["response_format"]   = "diarized_json"
            params["chunking_strategy"] = "auto"
            if speaker_names:
                params["known_speaker_names"] = speaker_names[:4]
        elif use_whisper:
            params["response_format"]         = "verbose_json"
            params["timestamp_granularities"] = ["segment"]
        else:
            params["response_format"]   = "json"
            params["chunking_strategy"] = "auto"

        # language 가 "auto"/빈값이면 파라미터 생략 → 모델이 자동 감지(한국어·영어 모두 처리)
        if language and str(language).strip().lower() != "auto":
            params["language"] = language

        # 직전 전사 꼬리를 문맥으로 전달 — 청크 경계 단어 오인식·언어 환각을 줄인다.
        # (whisper-1·gpt-4o-(mini-)transcribe 지원, diarize 계열은 미지원이라 제외)
        if prompt and not use_diarize:
            params["prompt"] = prompt[:800]

        t0   = time.time()
        resp = client.audio.transcriptions.create(**params)
        logger.debug(f"[STT TIME] {time.time()-t0:.1f}s")
    finally:
        f.close()

    data = resp if isinstance(resp, dict) else (
        resp.model_dump() if hasattr(resp, "model_dump") else json.loads(resp)
    )

    if debug_dir:
        debug_save(data,
                   os.path.join(debug_dir, f"stt_raw_chunk{chunk_index:03d}.json"),
                   f"STT raw chunk {chunk_index}")

    logger.debug(f"[STT KEYS] {list(data.keys())}")

    if use_diarize:
        return _parse_diarized(data, offset)
    elif use_whisper:
        return _parse_verbose(data, offset)
    else:
        return _parse_json_simple(data, offset)


def _parse_diarized(data: dict, offset: float) -> List[Dict]:
    segments: List[Dict] = []

    if "speakers" in data and isinstance(data["speakers"], list):
        logger.debug("[PARSE] speakers 배열")
        for spk in data["speakers"]:
            label = spk.get("name") or spk.get("id", "Speaker")
            for seg in spk.get("segments", []):
                segments.append({
                    "start":   seg.get("start", 0) + offset,
                    "end":     seg.get("end",   0) + offset,
                    "text":    seg.get("text", "").strip(),
                    "speaker": label,
                })
        segments.sort(key=lambda x: x["start"])
        if segments:
            return segments

    if "segments" in data and isinstance(data["segments"], list):
        logger.debug("[PARSE] flat segments")
        for seg in data["segments"]:
            segments.append({
                "start":   seg.get("start", 0) + offset,
                "end":     seg.get("end",   0) + offset,
                "text":    seg.get("text", "").strip(),
                "speaker": seg.get("speaker", "Speaker"),
            })
        if segments:
            return segments

    if "words" in data and isinstance(data["words"], list):
        logger.debug("[PARSE] words → 문장 병합")
        cur: Dict = {"start": 0, "end": 0, "text": "", "speaker": ""}
        for w in data["words"]:
            spk  = w.get("speaker", "Speaker")
            word = w.get("word", w.get("text", ""))
            if spk != cur["speaker"] and cur["text"].strip():
                segments.append({"start": cur["start"], "end": cur["end"],
                                 "text": cur["text"].strip(), "speaker": cur["speaker"]})
                cur = {"start": w.get("start", 0) + offset,
                       "end":   w.get("end",   0) + offset,
                       "text":  word, "speaker": spk}
            else:
                if not cur["text"]:
                    cur["start"]   = w.get("start", 0) + offset
                    cur["speaker"] = spk
                cur["end"]  = w.get("end", 0) + offset
                cur["text"] += " " + word
        if cur["text"].strip():
            segments.append({"start": cur["start"], "end": cur["end"],
                             "text": cur["text"].strip(), "speaker": cur["speaker"]})
        if segments:
            return segments

    segments.append({"start": offset, "end": offset,
                     "text": data.get("text", ""), "speaker": "Speaker"})
    return segments


def _parse_verbose(data: dict, offset: float) -> List[Dict]:
    segments = []
    for seg in data.get("segments", []):
        segments.append({
            "start": seg["start"] + offset, "end": seg["end"] + offset,
            "text":  seg["text"].strip(), "speaker": "",
        })
    if not segments and data.get("text"):
        segments.append({"start": offset, "end": offset,
                         "text": data["text"], "speaker": ""})
    return segments


def _parse_json_simple(data: dict, offset: float) -> List[Dict]:
    """
    gpt-4o-transcribe / gpt-4o-mini-transcribe → {"text": "..."} 만 반환.
    타임스탬프 없음 → 문장 단위로 분할하여 세그먼트화.
    """
    text = data.get("text", "").strip()
    if not text:
        return [{"start": offset, "end": offset, "text": "", "speaker": ""}]

    sentences = re.split(r'(?<=[.!?。！？])\s+', text)
    merged, buf = [], ""
    for s in sentences:
        buf = (buf + " " + s).strip() if buf else s
        if len(buf) > 30:
            merged.append(buf)
            buf = ""
    if buf:
        if merged:
            merged[-1] = merged[-1] + " " + buf
        else:
            merged.append(buf)

    result = [{"start": offset, "end": offset,
               "text": sent.strip(), "speaker": ""}
              for sent in merged]
    logger.debug(f"[PARSE JSON] {len(text)}자 → {len(result)}개 세그먼트")
    return result


def _looks_truncated(segs: List[Dict], duration: float, has_timestamps: bool) -> bool:
    """청크 길이 대비 전사 결과 분량이 비정상적으로 적으면(중간에 잘렸으면) True."""
    if duration <= 0:
        return False
    total_chars = sum(len(s.get("text", "")) for s in segs)
    if (total_chars / duration) < MIN_STT_CHARS_PER_SEC:
        return True
    if has_timestamps and segs:
        last_end = max(s.get("end", 0) for s in segs)
        if last_end < duration * 0.7:
            return True
    return False


def _transcribe_chunk_checked(
    client, audio_path: str, model: str,
    language: Optional[str], speaker_names: Optional[List[str]],
    offset: float, debug_dir: Optional[str], chunk_index: int,
    work_dir: str, depth: int = 0,
) -> List[Dict]:
    """transcribe_chunk 결과가 잘린 것으로 보이면 청크를 반으로 나눠 재시도."""
    segs = transcribe_chunk(
        client, audio_path, model, language, speaker_names,
        offset, debug_dir, chunk_index,
    )

    dur      = audio_duration(audio_path)
    has_ts   = "diarize" in model or model.startswith("whisper")
    if depth < MAX_STT_RETRY_SPLIT_DEPTH and _looks_truncated(segs, dur, has_ts):
        warn(f"  청크 {chunk_index} 전사 결과가 {dur:.0f}s 길이 대비 비정상적으로 짧음 → 2분할 재시도")
        half = dur / 2
        retried: List[Dict] = []
        for j, sub_offset in enumerate((0.0, half)):
            sub_path = os.path.join(
                work_dir, f"{Path(audio_path).stem}_retry{depth}_{j}.mp3",
            )
            _extract_audio_segment(audio_path, sub_offset, half, sub_path)
            try:
                retried.extend(_transcribe_chunk_checked(
                    client, sub_path, model, language, speaker_names,
                    offset + sub_offset, debug_dir, chunk_index,
                    work_dir, depth + 1,
                ))
            finally:
                if os.path.exists(sub_path):
                    os.remove(sub_path)
        return retried

    if _looks_truncated(segs, dur, has_ts):
        warn(f"  청크 {chunk_index} 전사 결과가 여전히 짧지만 재시도 한도 도달 → 그대로 사용")

    return segs


def run_stt(
    audio_path: str, model: str = DEFAULT_STT_MODEL,
    language: Optional[str] = None,
    speaker_names: Optional[List[str]] = None,
    work_dir: Optional[str] = None,
    debug_dir: Optional[str] = None,
) -> List[Dict]:
    step(f"STT 수행 중  (model: {model})")
    work_dir = work_dir or tempfile.gettempdir()

    key    = get_api_key("OPENAI_API_KEY", OPENAI_API_KEY)
    client = make_openai_client(key)

    chunks       = split_audio(audio_path, work_dir)
    all_segments: List[Dict] = []
    total_time   = 0.0

    # 청크 분할 시 청크 간 화자 연속성은 보장되지 않지만(예: 청크1의 "화자 A"와
    # 청크2의 "화자 A"가 동일 인물이라는 보장 없음), 청크 내부에서는 diarize가
    # 유효하므로 완전히 포기하지 않고 청크별로 유지 + 라벨에 청크 번호를 붙여
    # 청크 간 오인 병합을 방지한다.
    effective_model   = model
    per_chunk_diarize = len(chunks) > 1 and "diarize" in model
    if per_chunk_diarize:
        warn(f"  청크 분할됨({len(chunks)}개): 청크별 diarize 유지 (청크 간 화자 연속성은 보장되지 않음)")

    for i, (cp, chunk_offset) in enumerate(chunks):
        if len(chunks) > 1:
            info(f"  청크 {i+1}/{len(chunks)} 처리 중...")

        t0 = time.time()
        try:
            segs = _transcribe_chunk_checked(
                client, cp, effective_model, language, speaker_names,
                chunk_offset, debug_dir, i, work_dir,
            )
            if per_chunk_diarize:
                for s in segs:
                    if s.get("speaker"):
                        s["speaker"] = f"{s['speaker']} (청크{i+1})"
            all_segments.extend(segs)
        except Exception as e:
            logger.error(f"[STT FAIL] chunk {i}: {type(e).__name__}: {e}")
            if _mm.DEBUG:
                logger.debug(traceback.format_exc())
            if effective_model != FALLBACK_STT_MODEL:
                warn(f"  {model} 실패 ({e})")
                warn(f"  → {FALLBACK_STT_MODEL} 로 폴백")
                segs = _transcribe_chunk_checked(
                    client, cp, FALLBACK_STT_MODEL, language, None,
                    chunk_offset, debug_dir, i, work_dir,
                )
                all_segments.extend(segs)
            else:
                raise

        elapsed     = time.time() - t0
        total_time += elapsed
        logger.debug(f"  청크 {i}: {elapsed:.1f}s, 누적 {len(all_segments)} segs")

        if cp != audio_path and os.path.exists(cp):
            os.remove(cp)

    # CJK 환각 필터 — 중국어/일본어 텍스트 제거
    filtered = [s for s in all_segments if not _is_cjk_hallucination(s.get("text", ""))]
    if len(filtered) < len(all_segments):
        warn(f"  CJK 환각 필터: {len(all_segments) - len(filtered)}개 세그먼트 제거")
    ok(f"STT 완료: {len(filtered)}개 세그먼트 ({total_time:.1f}초)")
    return filtered


# ──────────────────────────────────────────────
#  번역 (영어 → 한국어)
# ──────────────────────────────────────────────
_TRANSLATE_CONTEXT_WINDOW = 5  # 이전 배치에서 가져올 컨텍스트 세그먼트 수


def translate_segments(
    segments: List[Dict], llm: LLMClient,
    batch_size: int = 30, debug_dir: Optional[str] = None,
) -> List[Dict]:
    step("영어 → 한국어 번역 중...")
    translated: List[Dict] = []
    total = math.ceil(len(segments) / batch_size)

    for bi in range(total):
        batch = segments[bi * batch_size : (bi + 1) * batch_size]
        info(f"  배치 {bi+1}/{total} ({len(batch)}개)")

        # 이전 배치의 마지막 N개를 컨텍스트 힌트로 제공 (용어 일관성 유지)
        context_hint = ""
        if bi > 0 and translated:
            prev_ctx = translated[-_TRANSLATE_CONTEXT_WINDOW:]
            ctx_lines = "\n".join(
                f"원문: {s.get('text_original', s['text'])} | 번역: {s['text']}"
                for s in prev_ctx
            )
            context_hint = (
                "[이전 문맥 참조 — 번역 대상 아님, 용어 일관성 유지용]\n"
                f"{ctx_lines}\n\n"
            )

        items = json.dumps(
            [{"i": i, "t": s["text"]} for i, s in enumerate(batch)],
            ensure_ascii=False,
        )
        system = (
            "전문 영한 번역가. 회의/세미나/강의 발화를 자연스러운 한국어로 번역.\n"
            "전문 용어는 원문 병기(예: 인공지능(AI)).\n"
            "동일 개념은 배치 전반에 걸쳐 일관된 용어로 번역.\n"
            "반드시 한국어로만 번역. 중국어·일본어·기타 언어로 절대 출력하지 마세요.\n"
            'JSON 배열로만 응답: [{"i":0,"t":"번역"},...]\n'
            "Markdown·설명 없이 순수 JSON만."
        )
        user = context_hint + items
        try:
            raw = llm.chat(system, user, temp=0.2)
            if debug_dir:
                debug_save(raw,
                           os.path.join(debug_dir, f"translate_batch{bi:03d}.txt"),
                           f"Translate {bi}")
            from meeting_minutes_app.meeting_pipeline.json_utils import parse_json_loose
            arr = parse_json_loose(raw, expect="list")
            if arr is None:
                raise ValueError("번역 JSON 파싱 실패")
            tmap = {a["i"]: a["t"] for a in arr if isinstance(a, dict) and "i" in a}
            for i, s in enumerate(batch):
                ns = s.copy()
                ns["text_original"] = s["text"]
                ns["text"]          = tmap.get(i, s["text"])
                translated.append(ns)
        except Exception as e:
            warn(f"  배치 {bi+1} 번역 실패: {e} → 원문 유지")
            translated.extend(batch)
        if bi < total - 1:
            time.sleep(0.5)

    ok(f"번역 완료: {len(translated)}개")
    return translated


def review_translations(
    pairs: List[Tuple[str, str]], llm: LLMClient,
    topic: str = "", batch_size: int = 20, debug_dir: Optional[str] = None,
) -> List[str]:
    """번역 검수 패스: (원문, 번역) 쌍을 주제 맥락으로 대조해 오역·누락·의미 왜곡·용어
    불일치만 고친 한국어 리스트를 반환한다.

    - 입력과 같은 길이·순서를 유지한다(문장 단위 정합, 원문 i ↔ 번역 i).
    - 번역이 이미 정확하면 그대로 둔다(불필요한 재작성 억제).
    - 배치 실패 시 그 배치는 기존 번역을 유지한다(전체가 멈추지 않음).
    번역 1회 패스로는 못 잡는 문맥·주제 의존 오역을 정리하는 용도이며, 번역과 별도의
    LLM 호출이라 비용이 늘어난다(config stt.translation_review 로 켜고 끔).
    """
    out: List[str] = [ko for _, ko in pairs]
    if not pairs:
        return out
    from meeting_minutes_app.meeting_pipeline.json_utils import parse_json_loose
    total = math.ceil(len(pairs) / batch_size)
    topic_hint = f"\n주제 맥락: {topic}" if topic else ""
    step("번역 검수 중...")
    for bi in range(total):
        lo = bi * batch_size
        batch = pairs[lo: lo + batch_size]
        items = json.dumps(
            [{"i": i, "src": s, "ko": k} for i, (s, k) in enumerate(batch)],
            ensure_ascii=False,
        )
        system = (
            f"전문 영한 번역 검수자입니다.{topic_hint}\n"
            "각 항목의 'src'(원문)와 'ko'(현재 번역)를 대조해, 오역·누락·의미 왜곡·"
            "부자연스러운 표현·용어 불일치가 있으면 고친 한국어를 출력하세요.\n"
            "규칙:\n"
            "- 번역이 이미 정확하고 자연스러우면 ko를 그대로 반환\n"
            "- 원문에 없는 내용을 추가하거나 여러 문장을 합치지 말 것(문장 단위 대응 유지)\n"
            "- 전문 용어는 원문 병기 가능(예: 인공지능(AI))\n"
            "- 반드시 한국어로만 출력. 다른 언어 금지\n"
            'JSON 배열로만 응답: [{"i":0,"t":"검수된 한국어"},...] — 입력과 같은 개수·순서.'
        )
        try:
            raw = llm.chat(system, items, temp=0.1)
            if debug_dir:
                debug_save(raw,
                           os.path.join(debug_dir, f"review_batch{bi:03d}.txt"),
                           f"Review {bi}")
            arr = parse_json_loose(raw, expect="list")
            if arr is None:
                raise ValueError("검수 JSON 파싱 실패")
            tmap = {a["i"]: a["t"] for a in arr
                    if isinstance(a, dict) and "i" in a and a.get("t")}
            for i in range(len(batch)):
                fixed = tmap.get(i)
                if fixed and str(fixed).strip():
                    out[lo + i] = str(fixed).strip()
        except Exception as e:
            warn(f"  검수 배치 {bi+1}/{total} 실패: {e} → 기존 번역 유지")
        if bi < total - 1:
            time.sleep(0.3)
    ok("번역 검수 완료")
    return out


def review_translation_segments(
    segments: List[Dict], llm: LLMClient,
    topic: str = "", debug_dir: Optional[str] = None,
) -> List[Dict]:
    """번역된 세그먼트(text=한국어, text_original=원문)를 검수해 text를 갱신한 새 리스트 반환.

    translate_segments 산출물(배치 경로)을 그대로 받아 처리한다.
    """
    pairs = [((s.get("text_original") or ""), (s.get("text") or "")) for s in segments]
    fixed = review_translations(pairs, llm, topic=topic, debug_dir=debug_dir)
    out: List[Dict] = []
    for s, ko in zip(segments, fixed):
        ns = s.copy()
        ns["text"] = ko or s.get("text", "")
        out.append(ns)
    return out
