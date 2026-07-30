#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
오디오 준비 → STT(제공자 폴백 체인) → 영→한 번역.
meeting_minutes.py에서 분리 (2026-07 리팩토링 3단계).

STT는 한 벤더에 묶이지 않는다: OpenAI 기본 → OpenAI 폴백모델 → Groq(다른 벤더) →
로컬 faster-whisper 순서로 청크별 폴백한다(`_build_stt_provider_chain`).
적용 범위와 알려진 한계는 docs/ARCHITECTURE.md "STT 제공자 폴백 체인" 참고 —
실시간 라이브 청크는 Groq까지만이고 로컬은 업로드·배치 경로에서만 쓰인다.
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
    LLMClient, OPENAI_API_KEY, GROQ_API_KEY, get_api_key,
    make_openai_client, make_groq_client,
)
from meeting_minutes_app.common.text_filters import (
    is_cjk_hallucination as _is_cjk_hallucination,
    sanitize_stats_line as _sanitize_stats_line,
    sanitize_transcript as _sanitize_transcript,
)
from meeting_minutes_app.meeting_pipeline import meeting_minutes as _mm
from meeting_minutes_app.meeting_pipeline.meeting_minutes import (
    DEFAULT_STT_MODEL, FALLBACK_STT_MODEL, GROQ_STT_MODEL,
    LOCAL_STT_ENABLED, LOCAL_STT_MODEL,
    MAX_FILE_SIZE_MB, MAX_CHUNK_DURATION_SEC,
    MIN_STT_CHARS_PER_SEC, MAX_STT_RETRY_SPLIT_DEPTH, UPLOAD_FORMATS,
    logger, step, info, ok, warn, debug_save,
    ts, file_mb, run_cmd, audio_duration, FFMPEG,
)


def _refresh_config_globals() -> None:
    """config_loader.reload() 훅 — 위 from-import 로 복사된 키/모델 전역을
    웹 UI 설정 저장 시 재시작 없이 갱신한다(원본 모듈 훅이 먼저 실행됨)."""
    global OPENAI_API_KEY, GROQ_API_KEY, DEFAULT_STT_MODEL, FALLBACK_STT_MODEL
    global GROQ_STT_MODEL, LOCAL_STT_ENABLED, LOCAL_STT_MODEL
    from meeting_minutes_app.common import llm_client as _llm
    OPENAI_API_KEY = _llm.OPENAI_API_KEY
    GROQ_API_KEY = _llm.GROQ_API_KEY
    DEFAULT_STT_MODEL = _mm.DEFAULT_STT_MODEL
    FALLBACK_STT_MODEL = _mm.FALLBACK_STT_MODEL
    GROQ_STT_MODEL = _mm.GROQ_STT_MODEL
    LOCAL_STT_ENABLED = _mm.LOCAL_STT_ENABLED
    LOCAL_STT_MODEL = _mm.LOCAL_STT_MODEL


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
# prompt 길이 상한 — 모델 계열별로 다르다.
# whisper 계열(OpenAI whisper-1, Groq whisper-*)의 prompt 는 224토큰이 상한이다.
# 한국어는 대략 1.5토큰/자라서 224토큰 ≈ 150자 → 여유를 둬 120자로 자른다.
# (실시간 tail 문맥도 어차피 120자로 유지된다 — web/backend/api/realtime.py)
WHISPER_PROMPT_MAX_CHARS = 120
GPT_PROMPT_MAX_CHARS = 800


def stt_request_params(
    provider: str, model: str,
    language: Optional[str] = None,
    speaker_names: Optional[List[str]] = None,
    prompt: Optional[str] = None,
) -> Tuple[Dict[str, Any], str]:
    """(요청 파라미터, 응답 종류) — 벤더·모델별 전사 API 계약의 단일 소스.

    응답 종류는 "diarized" | "verbose" | "simple" 이며 파싱 함수 선택에 그대로 쓰인다.
    파라미터와 파서를 한 함수에서 함께 결정해 둘이 어긋나지 않게 한다(과거 호출부마다
    response_format 을 따로 정해 diarize 모델이 매 청크 조용히 실패한 사고가 있었다).

    **모델명 문자열이 아니라 provider 로 벤더 전용 파라미터를 가른다**:
    `chunking_strategy`·`diarized_json`·`known_speaker_names` 는 OpenAI 전용이라
    Groq 로 새어나가면 400 이 된다. 배치·실시간(웹/CLI) 세 경로가 이 함수를 공유한다.
    """
    is_openai = (provider or "OpenAI") == "OpenAI"
    use_diarize = is_openai and "diarize" in model
    use_whisper = "whisper" in model

    params: Dict[str, Any] = {"model": model}
    if use_diarize:
        params["response_format"]   = "diarized_json"
        params["chunking_strategy"] = "auto"
        if speaker_names:
            params["known_speaker_names"] = speaker_names[:4]
        kind = "diarized"
    elif use_whisper:
        params["response_format"]         = "verbose_json"
        params["timestamp_granularities"] = ["segment"]
        kind = "verbose"
    else:
        params["response_format"] = "json"
        if is_openai:
            params["chunking_strategy"] = "auto"
        kind = "simple"

    # language 가 "auto"/빈값이면 파라미터 생략 → 모델이 자동 감지(한국어·영어 모두 처리)
    if language and str(language).strip().lower() != "auto":
        params["language"] = language

    # 호출자가 준 문맥(prompt)을 전달 — 청크 경계 단어 오인식을 줄인다.
    # 주의: **직전 전사 꼬리를 넘기는 것은 위험하다**. 모델이 그 문장을 되풀이하고
    # 그 출력이 다시 꼬리가 되면 같은 문장이 세션 내내 반복된다(2026-07-28 실사고).
    # 실시간 경로의 기본값은 세션 내내 불변인 정적 힌트(주제·참석자)다
    # — realtime.prompt_context (static|tail|off), web/backend/api/realtime.py 참고.
    # diarize 계열은 prompt 미지원이고, Groq 는 폴백 단계라 문맥 없이 정확도만 취한다
    # (정적 힌트가 224토큰 상한을 넘겨 폴백 자체가 깨지는 것을 피한다).
    if prompt and is_openai and not use_diarize:
        cap = WHISPER_PROMPT_MAX_CHARS if use_whisper else GPT_PROMPT_MAX_CHARS
        params["prompt"] = prompt[:cap]

    return params, kind


def transcribe_chunk(
    client, audio_path: str, model: str,
    language: Optional[str] = None,
    speaker_names: Optional[List[str]] = None,
    offset: float = 0.0,
    debug_dir: Optional[str] = None,
    chunk_index: int = 0,
    prompt: Optional[str] = None,
    provider: str = "OpenAI",
) -> List[Dict]:
    params, kind = stt_request_params(
        provider, model, language, speaker_names, prompt)
    logger.debug(f"[STT] {provider}/{model}, file={audio_path}, "
                 f"{file_mb(audio_path):.2f}MB, offset={offset:.1f}s, kind={kind}")

    f = open(audio_path, "rb")
    try:
        params["file"] = f
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

    if kind == "diarized":
        return _parse_diarized(data, offset)
    elif kind == "verbose":
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
    # 필드는 .get 으로 읽는다 — 이 파서는 OpenAI whisper-1 과 Groq whisper-* 응답을
    # 함께 받으므로 한쪽에 없는 필드로 KeyError 가 나면 폴백 결과 전체가 날아간다.
    segments = []
    for seg in data.get("segments", []):
        segments.append({
            "start": (seg.get("start") or 0.0) + offset,
            "end":   (seg.get("end")   or 0.0) + offset,
            "text":  (seg.get("text")  or "").strip(), "speaker": "",
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
    work_dir: str, depth: int = 0, provider: str = "OpenAI",
) -> List[Dict]:
    """transcribe_chunk 결과가 잘린 것으로 보이면 청크를 반으로 나눠 재시도."""
    segs = transcribe_chunk(
        client, audio_path, model, language, speaker_names,
        offset, debug_dir, chunk_index, provider=provider,
    )

    dur = audio_duration(audio_path)
    # 타임스탬프 유무는 요청 계약과 같은 판단을 써야 한다(응답 종류로 판정).
    _, _kind = stt_request_params(provider, model)
    has_ts   = _kind in ("diarized", "verbose")
    if depth < MAX_STT_RETRY_SPLIT_DEPTH and _looks_truncated(segs, dur, has_ts):
        # 빈 전사는 '분량이 짧다'의 극단값이라 여기 걸리지만, 정말 발화가 없는 구간이면
        # 쪼개도 나올 텍스트가 없다 → 무음이면 재시도를 생략한다.
        # 이 판정이 없던 동안엔 무음 청크가 **제공자당** STT 3회 + ffmpeg 추출 2회를
        # 치른 뒤에야 _transcribe_chunk_via_chain 의 무음 판정에 도달했다(4단 체인이면
        # 최대 12회). 0e5f106 의 무음 최적화가 이 낡은 분할 재시도에 무력화돼 있었다.
        # 비무음이면 분할 재시도는 그대로 둔다 — 큰 청크에서 벤더가 조용히 실패하고
        # 절반씩은 성공하는 실제 복구 경로이므로 없애면 안 된다.
        if not _segments_have_text(segs) and _chunk_is_silent(audio_path):
            logger.debug(f"[STT] 청크 {chunk_index} 는 무음 — 2분할 재시도 생략")
            return segs
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
                    work_dir, depth + 1, provider,
                ))
            finally:
                if os.path.exists(sub_path):
                    os.remove(sub_path)
        return retried

    if _looks_truncated(segs, dur, has_ts):
        warn(f"  청크 {chunk_index} 전사 결과가 여전히 짧지만 재시도 한도 도달 → 그대로 사용")

    return segs


# ──────────────────────────────────────────────
#  로컬 STT — faster-whisper (네트워크 무관 최종 백업)
# ──────────────────────────────────────────────
_LOCAL_MODEL_CACHE: Dict[str, Any] = {}

LOCAL_LIB_MISSING_MSG = (
    "로컬 STT 라이브러리(faster-whisper)가 없습니다 → pip install faster-whisper "
    "(포터블 배포본에는 기본 포함)"
)
LOCAL_WEIGHTS_MISSING_MSG = (
    "로컬 백업 모델이 준비되지 않았습니다 — 웹 [설정] → '오디오 전처리'의 "
    "[로컬 백업 모델 준비]를 먼저 눌러 가중치를 내려받으세요"
)


def local_models_dir() -> str:
    """로컬 STT 가중치 저장 폴더 — 쓰기 가능한 데이터 폴더 아래(MM_DATA_DIR 반영).

    HuggingFace 기본 캐시(~/.cache)가 아니라 앱 데이터 폴더에 두어, 포터블 배포본을
    폴더째 옮기거나 여러 PC 에 복사해도 준비해 둔 가중치가 함께 따라가게 한다."""
    from meeting_minutes_app.common import app_paths
    d = app_paths.get_data_dir() / "models"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.debug(f"[STT local] 모델 폴더 생성 실패({e}) — 경로만 반환")
    return str(d)


def _local_snapshot_dir(model_size: str) -> Optional[Path]:
    """내려받은 가중치(model.bin)가 있는 스냅샷 폴더. 없으면 None.

    huggingface_hub 캐시 구조: <root>/models--Systran--faster-whisper-<size>/snapshots/<rev>/"""
    root = Path(local_models_dir())
    if not root.exists():
        return None
    for repo in root.iterdir():
        if not repo.is_dir() or not repo.name.endswith(f"faster-whisper-{model_size}"):
            continue
        for snap in (repo / "snapshots").glob("*"):
            if (snap / "model.bin").exists():
                return snap
    return None


def local_lib_available() -> bool:
    """faster-whisper 설치 여부(무거운 import 없이 확인)."""
    import importlib.util
    return importlib.util.find_spec("faster_whisper") is not None


def local_model_status(model_size: str) -> Dict[str, Any]:
    """로컬 백업 준비 상태 — 웹 [설정]의 상태 배지용."""
    snap = _local_snapshot_dir(model_size)
    size_mb = 0.0
    if snap:
        size_mb = round(sum(
            f.stat().st_size for f in snap.rglob("*") if f.is_file()
        ) / (1024 * 1024), 1)
    return {
        "lib_available": local_lib_available(),
        "installed": snap is not None,
        "model": model_size,
        "path": str(snap) if snap else local_models_dir(),
        "size_mb": size_mb,
    }


def _get_local_model(model_size: str, allow_download: bool = False):
    """faster-whisper 모델을 로딩(최초 1회, 이후 캐시).

    CPU int8 로 로딩해 GPU 없이도 동작한다(포터블 배포 환경 고려).
    allow_download=False(기본)면 이미 내려받은 가중치만 쓴다 — 전사 도중에
    수백 MB 다운로드가 시작돼 회의 처리가 몇 분 멈추는 일을 막는다.
    다운로드는 prepare_local_model()(웹 [설정] 버튼)에서만 일어난다."""
    m = _LOCAL_MODEL_CACHE.get(model_size)
    if m is not None:
        return m
    from faster_whisper import WhisperModel  # 지연 임포트 — 미설치 시 여기서만 실패
    if allow_download:
        info(f"  로컬 STT 모델 준비: faster-whisper '{model_size}' 다운로드/로딩")
    else:
        info(f"  로컬 STT 모델 로딩: faster-whisper '{model_size}' (준비된 가중치 사용)")
    m = WhisperModel(
        model_size, device="cpu", compute_type="int8",
        download_root=local_models_dir(),
        local_files_only=not allow_download,
    )
    _LOCAL_MODEL_CACHE[model_size] = m
    return m


def prepare_local_model(model_size: str) -> Dict[str, Any]:
    """로컬 백업 모델 가중치를 미리 내려받는다(웹 [설정] 버튼 → tools API).

    실제 전사 경로는 절대 다운로드하지 않으므로(위 _get_local_model 참고) 사용자가
    장애 전에 한 번 눌러 두는 것이 이 기능의 전제다. 라이브러리 미설치는 그대로 알린다."""
    if not local_lib_available():
        raise RuntimeError(LOCAL_LIB_MISSING_MSG)
    t0 = time.time()
    _get_local_model(model_size, allow_download=True)
    st = local_model_status(model_size)
    st["elapsed_sec"] = round(time.time() - t0, 1)
    return st


def transcribe_local(
    audio_path: str, model_size: str,
    language: Optional[str] = None, offset: float = 0.0,
) -> List[Dict]:
    """faster-whisper 로 로컬 전사 — OpenAI/Groq 세그먼트와 동일한 dict 형식으로 반환.

    faster-whisper 는 세그먼트 단위 타임스탬프를 제공하므로 verbose 경로와 같은 형태로
    맞춘다. 화자분리는 없다(speaker=\"\"). 긴 파일도 내부에서 처리하므로 별도 분할 불필요."""
    try:
        model = _get_local_model(model_size)
    except ImportError as e:
        raise RuntimeError(LOCAL_LIB_MISSING_MSG) from e
    except Exception as e:
        # local_files_only=True 라 가중치가 없으면 huggingface_hub 가 여기서 실패한다
        # (LocalEntryNotFoundError 등). 원인 메시지를 붙여 사용자 안내로 바꾼다.
        raise RuntimeError(f"{LOCAL_WEIGHTS_MISSING_MSG} ({type(e).__name__}: {e})") from e

    lang: Optional[str] = None
    if language and str(language).strip().lower() != "auto":
        lang = language

    logger.debug(f"[STT local] model={model_size}, file={audio_path}, "
                 f"{file_mb(audio_path):.2f}MB, offset={offset:.1f}s, lang={lang}")
    t0 = time.time()
    # vad_filter: 무음 구간을 걸러 환각(없는 말 생성)을 줄인다.
    segments, _info = model.transcribe(audio_path, language=lang, beam_size=5, vad_filter=True)
    out: List[Dict] = []
    for seg in segments:  # 제너레이터 — 순회 시 실제 전사가 수행됨
        txt = (getattr(seg, "text", "") or "").strip()
        if txt:
            out.append({
                "start": (getattr(seg, "start", 0.0) or 0.0) + offset,
                "end":   (getattr(seg, "end",   0.0) or 0.0) + offset,
                "text":  txt, "speaker": "",
            })
    logger.debug(f"[STT local TIME] {time.time()-t0:.1f}s, {len(out)} segs")
    if not out:
        out.append({"start": offset, "end": offset, "text": "", "speaker": ""})
    return out


# ──────────────────────────────────────────────
#  STT 제공자 폴백 체인
# ──────────────────────────────────────────────
# STT 호출의 HTTP 한도 — 폴백 체인이 있으므로 한 제공자에 오래 매달릴 이유가 없다.
# SDK 기본값(요청 600초 × 재시도 2회 = 청크당 최대 30분)을 그대로 쓰면 벤더가 응답 없이
# 매달리는 장애에서 청크 수에 비례해 처리가 멈춘 것처럼 보인다(2모델이면 60분/청크).
# 여기 값은 "정상 응답에는 넉넉하고, 장애에는 빨리 포기"를 노린다 —
# 20분 청크(48kbps mono ≈ 7MB) 업로드+전사가 사내망에서도 300초 안에는 끝난다.
STT_REQUEST_TIMEOUT_SEC = 300.0
STT_MAX_RETRIES = 1


def groq_fallback() -> Tuple[Any, str]:
    """Groq STT 폴백 (클라이언트, 모델). 키가 없거나 생성 실패면 (None, "").

    배치 체인과 실시간(http 청크) 경로가 같은 규칙을 쓰도록 여기 한 곳에 둔다.
    호출부는 client 가 None 이면 Groq 단계를 건너뛴다."""
    gkey = get_api_key("GROQ_API_KEY", GROQ_API_KEY)
    if not gkey:
        return None, ""
    try:
        return make_groq_client(
            gkey, timeout=STT_REQUEST_TIMEOUT_SEC, max_retries=STT_MAX_RETRIES,
        ), GROQ_STT_MODEL
    except Exception as e:
        warn(f"  Groq 클라이언트 생성 실패 → 폴백에서 제외: {e}")
        return None, ""


class _ChainState:
    """파일 하나를 처리하는 동안의 제공자 건강 상태(청크 간 공유).

    죽은 벤더를 청크마다 처음부터 다시 때리면, 호출마다 timeout·재시도를 품고 있어
    청크 수에 비례해 헛시간이 쌓인다. 그래서 연속 실패가 쌓인 제공자는 이후 청크에서
    건너뛴다.

    임계값 2는 "한 번의 일시적 오류(429·순간 네트워크 끊김)로 제공자를 파일 끝까지
    강등하지 않기" 위한 값이다 — 서로 다른 청크에서 연속 2회 실패하면 실제로 죽은
    것으로 본다. 성공하면 카운터를 지워 '연속' 의미를 유지한다."""

    DOWN_AFTER_CONSECUTIVE_FAILURES = 2

    def __init__(self) -> None:
        self._fails: Dict[int, int] = {}

    def is_down(self, idx: int) -> bool:
        return self._fails.get(idx, 0) >= self.DOWN_AFTER_CONSECUTIVE_FAILURES

    def record_failure(self, idx: int) -> None:
        self._fails[idx] = self._fails.get(idx, 0) + 1

    def record_success(self, idx: int) -> None:
        self._fails.pop(idx, None)


def _local_stage_ready() -> bool:
    """로컬 단계를 체인에 넣어도 되는지 — 라이브러리 + 가중치가 모두 준비됐을 때만.

    준비 안 된 로컬을 체인에 넣으면 마지막 단계라서 그 오류가 `last_err`가 되어
    **앞선 진짜 원인(키 오류·한도 초과)을 덮어쓴다**. 사용자 매뉴얼도 "준비 안 된
    상태면 이 백업은 그냥 건너뛰어집니다"라고 안내하므로 여기서 미리 걸러 낸다."""
    if not local_lib_available():
        warn(f"로컬 STT 폴백이 켜져 있으나 체인에서 제외 — {LOCAL_LIB_MISSING_MSG}")
        return False
    if not local_model_status(LOCAL_STT_MODEL).get("installed"):
        warn(f"로컬 STT 폴백이 켜져 있으나 '{LOCAL_STT_MODEL}' 가중치가 없어 "
             f"체인에서 제외 — {LOCAL_WEIGHTS_MISSING_MSG}")
        return False
    return True


def _build_stt_provider_chain(default_model: str) -> List[Tuple[str, str, Any]]:
    """앞에서부터 시도할 (제공자명, 모델, 클라이언트) 목록.

    OpenAI 기본 → OpenAI 폴백모델 → Groq(다른 벤더) → 로컬 faster-whisper.
    OpenAI 두 항목은 같은 벤더라 벤더 전체 장애 시 함께 죽는다 — Groq/로컬이 그때의
    진짜 백업이다. 로컬은 client=None 으로 표시(별도 경로)."""
    chain: List[Tuple[str, str, Any]] = []

    okey = get_api_key("OPENAI_API_KEY", OPENAI_API_KEY)
    if okey:
        # 클라이언트 생성 실패(SDK 미설치·프록시 설정 오류 등)가 Groq·로컬 단계까지
        # 못 쓰게 만들면 안 된다 — Groq 와 같은 규칙으로 감싼다.
        try:
            oclient = make_openai_client(
                okey, timeout=STT_REQUEST_TIMEOUT_SEC, max_retries=STT_MAX_RETRIES,
            )
        except Exception as e:
            warn(f"OpenAI 클라이언트 생성 실패 → 폴백에서 제외: {e}")
        else:
            chain.append(("OpenAI", default_model, oclient))
            if FALLBACK_STT_MODEL and FALLBACK_STT_MODEL != default_model:
                chain.append(("OpenAI", FALLBACK_STT_MODEL, oclient))

    gclient, gmodel = groq_fallback()
    if gclient is not None:
        chain.append(("Groq", gmodel, gclient))

    if LOCAL_STT_ENABLED and _local_stage_ready():
        chain.append(("local", LOCAL_STT_MODEL, None))

    return chain


def _segments_have_text(segs: List[Dict]) -> bool:
    return any((s.get("text") or "").strip() for s in segs)


def _chunk_is_silent(audio_path: str, threshold_db: float = -50.0) -> bool:
    """청크가 사실상 무음인지 ffmpeg volumedetect 로 판정한다.

    STT 가 HTTP 200 으로 빈 텍스트를 주는 경우는 두 가지다 — (a) 정말 발화가 없었거나
    (b) 제공자가 조용히 실패했거나. (b)만 다른 제공자로 넘겨야 하므로 여기서 갈라낸다.
    판정 자체가 실패하면 False(발화 있음)로 봐서 폴백 기회를 잃지 않는다."""
    try:
        r = run_cmd([FFMPEG, "-i", audio_path, "-af", "volumedetect",
                     "-f", "null", "-"], check=False)
        m = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?) dB", r.stderr or "")
        if not m:
            return False
        mean_db = float(m.group(1))
        logger.debug(f"[STT] 빈 전사 원인 판정: mean_volume={mean_db}dB "
                     f"(기준 {threshold_db}dB)")
        return mean_db < threshold_db
    except Exception as e:
        logger.debug(f"[STT] 무음 판정 실패({e}) — 발화 있음으로 간주")
        return False


def _transcribe_chunk_via_chain(
    chain: List[Tuple[str, str, Any]], cp: str,
    language: Optional[str], speaker_names: Optional[List[str]],
    chunk_offset: float, debug_dir: Optional[str], chunk_index: int,
    work_dir: str, state: Optional["_ChainState"] = None,
) -> List[Dict]:
    """청크 하나를 제공자 체인 순서대로 시도. 하나가 성공하면 그 결과를 쓴다.

    앞 제공자의 오류가 '일시적/영구적'인지 구분하지 않고 다음 제공자로 넘어간다 —
    폴백의 목적은 어떤 이유로든 앞 제공자가 실패했을 때 결과를 얻는 것이기 때문이다.

    **예외뿐 아니라 '빈 전사'도 실패로 본다**: 제공자가 200 과 함께 빈 텍스트를 주는
    조용한 실패가 실제로는 예외보다 흔하다. 단, 정말 발화가 없는 구간(무음)까지 전
    제공자를 순회하면 헛돈·헛시간이므로 `_chunk_is_silent()`로 한 번 갈라낸다.

    `state`(_ChainState)를 주면 청크 간에 제공자 건강 상태를 공유해, 이미 죽은 것으로
    판정된 제공자를 건너뛴다(같은 파일의 남은 청크에서 헛시간 반복 방지).

    무음 판정은 여기와 `_transcribe_chunk_checked`(분할 재시도 생략)에서 청크당 각각
    1회 돈다 — 의도적으로 합치지 않았다. 발동 조건이 '빈 전사'로 좁고 절약분은 ffmpeg
    디코드 한 번(배치 백그라운드)인데, 두 판정을 엮으면 전사 유실과 직결된 복구 경로를
    건드리게 된다."""
    last_err: Optional[Exception] = None
    empty_result: Optional[List[Dict]] = None   # 예외 없이 받은 빈 결과(있으면 성공으로 취급)
    silence_checked = False
    chunk_silent = False

    # 죽은 제공자를 뺀 이번 청크의 시도 순서. 전부 죽었다면 그대로 다시 시도해
    # (건너뛰기 때문에 아무것도 시도하지 않는 상태를 만들지 않고) 진짜 오류를 남긴다.
    active = [(i, e) for i, e in enumerate(chain) if not (state and state.is_down(i))]
    if not active:
        active = list(enumerate(chain))
    if state and len(active) < len(chain):
        skipped = [f"{chain[i][0]}/{chain[i][1]}"
                   for i in range(len(chain)) if state.is_down(i)]
        logger.debug(f"[STT] 청크 {chunk_index}: 응답 없는 제공자 건너뜀 — "
                     f"{', '.join(skipped)}")

    for pos, (idx, (provider, pmodel, client)) in enumerate(active):
        nxt = active[pos + 1][1] if pos + 1 < len(active) else None
        try:
            if provider == "local":
                segs = transcribe_local(cp, pmodel, language, chunk_offset)
            else:
                # 화자명 힌트를 어느 모델이 받는지는 stt_request_params 가 판단한다
                # (여기서 또 걸러내면 규칙이 두 곳으로 갈라진다).
                segs = _transcribe_chunk_checked(
                    client, cp, pmodel, language, speaker_names,
                    chunk_offset, debug_dir, chunk_index, work_dir,
                    provider=provider,
                )
        except Exception as e:
            last_err = e
            if state:
                state.record_failure(idx)
            logger.error(f"[STT FAIL] chunk {chunk_index} via {provider}/{pmodel}: "
                         f"{type(e).__name__}: {e}")
            if _mm.DEBUG:
                logger.debug(traceback.format_exc())
            if nxt:
                warn(f"  청크 {chunk_index} {provider}/{pmodel} 실패 ({e}) "
                     f"→ {nxt[0]}/{nxt[1]} 로 폴백")
            else:
                warn(f"  청크 {chunk_index} {provider}/{pmodel} 실패 — 더 이상 폴백 없음")
            continue

        if _segments_have_text(segs):
            if state:
                state.record_success(idx)
            return segs

        if empty_result is None:
            empty_result = segs
        if not silence_checked:
            silence_checked = True
            chunk_silent = _chunk_is_silent(cp)
        if chunk_silent:
            # 발화가 없는 구간이다 — 제공자 탓이 아니므로 건강 상태를 깎지 않는다.
            logger.debug(f"[STT] 청크 {chunk_index} 는 무음 — 빈 전사를 그대로 사용")
            return segs
        if state:
            state.record_failure(idx)
        if nxt:
            warn(f"  청크 {chunk_index} {provider}/{pmodel} 이 빈 전사를 반환"
                 f"(발화는 감지됨) → {nxt[0]}/{nxt[1]} 로 폴백")
        else:
            warn(f"  청크 {chunk_index} 모든 제공자가 빈 전사를 반환 — 그대로 사용")

    # 예외 없이 받은 빈 결과가 있으면 그것을 쓴다(기존 동작 유지 — 한 청크의 공백이
    # 파일 전체 처리를 중단시키지 않는다). 전 제공자가 예외로 죽었을 때만 던진다.
    if empty_result is not None:
        return empty_result
    if last_err:
        raise last_err
    raise RuntimeError("사용 가능한 STT 제공자가 없습니다.")


def run_stt(
    audio_path: str, model: Optional[str] = None,
    language: Optional[str] = None,
    speaker_names: Optional[List[str]] = None,
    work_dir: Optional[str] = None,
    debug_dir: Optional[str] = None,
) -> List[Dict]:
    # 기본값을 인자 기본식으로 두면 import 시점 값이 고정돼 설정 reload 가 반영되지
    # 않는다(웹 [설정]에서 STT 모델을 바꿔도 예전 모델로 돈다) → 호출 시점에 읽는다.
    model = model or DEFAULT_STT_MODEL
    step(f"STT 수행 중  (model: {model})")
    work_dir = work_dir or tempfile.gettempdir()

    chain = _build_stt_provider_chain(model)
    if not chain:
        raise RuntimeError(
            "사용 가능한 STT 제공자가 없습니다.\n"
            "  → OpenAI API 키(OPENAI_API_KEY)를 설정하거나,\n"
            "  → Groq 키(GROQ_API_KEY)를 넣거나,\n"
            "  → 로컬 폴백을 쓰려면 stt.local_fallback=true **와** 가중치 준비가 모두\n"
            "     필요합니다(켜기만 하면 체인에서 조용히 제외된다):\n"
            "     python run_meeting.py prepare-local-stt"
        )
    if len(chain) > 1:
        info("  STT 폴백 체인: " + " → ".join(f"{p}/{m}" for p, m, _ in chain))

    chunks       = split_audio(audio_path, work_dir)
    all_segments: List[Dict] = []
    total_time   = 0.0
    # 제공자 건강 상태는 이 파일 처리 동안만 유지한다(세션 간 오염 방지 — 전역 금지).
    chain_state  = _ChainState()

    # 청크 분할 시 청크 간 화자 연속성은 보장되지 않지만(예: 청크1의 "화자 A"와
    # 청크2의 "화자 A"가 동일 인물이라는 보장 없음), 청크 내부에서는 diarize가
    # 유효하므로 완전히 포기하지 않고 청크별로 유지 + 라벨에 청크 번호를 붙여
    # 청크 간 오인 병합을 방지한다. (diarize가 아닌 제공자로 폴백되면 speaker가
    # 비어 있어 아래 라벨링이 자연히 건너뛰어진다.)
    if len(chunks) > 1 and "diarize" in model:
        warn(f"  청크 분할됨({len(chunks)}개): 청크별 diarize 유지 (청크 간 화자 연속성은 보장되지 않음)")

    for i, (cp, chunk_offset) in enumerate(chunks):
        if len(chunks) > 1:
            info(f"  청크 {i+1}/{len(chunks)} 처리 중...")

        t0 = time.time()
        segs = _transcribe_chunk_via_chain(
            chain, cp, language, speaker_names,
            chunk_offset, debug_dir, i, work_dir, chain_state,
        )
        if len(chunks) > 1:
            for s in segs:
                if s.get("speaker"):
                    s["speaker"] = f"{s['speaker']} (청크{i+1})"
        all_segments.extend(segs)

        elapsed     = time.time() - t0
        total_time += elapsed
        logger.debug(f"  청크 {i}: {elapsed:.1f}s, 누적 {len(all_segments)} segs")

        if cp != audio_path and os.path.exists(cp):
            os.remove(cp)

    # CJK 환각 필터 — 중국어/일본어 텍스트 제거
    filtered = [s for s in all_segments if not _is_cjk_hallucination(s.get("text", ""))]
    if len(filtered) < len(all_segments):
        warn(f"  CJK 환각 필터: {len(all_segments) - len(filtered)}개 세그먼트 제거")
    # 환각·반복 정화 — 되풀이 축약/제거 + 이질 문자(키릴 등) [불명] 표시.
    # (회의록 생성 경로는 finalize에서 한 번 더 정화하지만, 여기서 정화하면
    #  transcribe-only/텍스트 반환 경로도 같은 이득을 본다. 멱등이라 중복 무해)
    if _mm._c("realtime.hallucination_filter", True):
        try:
            filtered, _stats = _sanitize_transcript(filtered, language or "")
            _line = _sanitize_stats_line(_stats)
            if _line:
                warn(f"  전사 정화: {_line}")
        except Exception as e:
            warn(f"  전사 정화 건너뜀: {e}")
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
