#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""텍스트 품질 필터 (STT 환각 감지·반복 정화 등).

STT(특히 gpt-4o-transcribe 계열)는 무음·잡음 구간에서 없는 말을 만들어내고,
직전 문맥을 prompt 로 받으면 그 문장을 되풀이하는 실패 모드가 있다. 이 모듈은
그 산출물을 **보수적으로** 정화한다:

  - 반복: 연속 중복(문장/토큰 n-gram)을 1회로 축약, 전사 전체에서 3회 이상
          되풀이되는 동일 문장은 앞의 2개만 남긴다.
  - 환각: 한국어 회의에 등장할 이유가 없는 이질 문자(키릴·아랍·타이·한자·가나 등)나
          반복되는 정체불명 라틴 조각은 **삭제하지 않고** `[불명]` 으로 표시한다.

전 경로(배치 stt.run_stt / 실시간 웹·CLI / finalize)가 이 모듈을 공유한다.
"""

import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

# 문자 범위는 반드시 \uXXXX 이스케이프로 적는다 — 리터럴 한자를 쓰면 편집 과정에서
# 비슷하게 생긴 다른 코드포인트(예: U+F900 豈 ↔ U+8C48 豈)로 바뀌어 한글(U+AC00~)까지
# 범위에 삼켜지는 사고가 난다.
_CJK_RANGES = (
    "\u3000-\u303f"   # CJK 기호
    "\u3040-\u309f"   # 히라가나
    "\u30a0-\u30ff"   # 가타카나
    "\u4e00-\u9fff"   # CJK 통합 한자
    "\uf900-\ufaff"   # CJK 호환 한자
)
_RE_CJK = re.compile(f'[{_CJK_RANGES}]')

#: 한국어/영어 회의에 나올 이유가 없는 문자 집합(라틴·한글·숫자는 제외).
#: 사내 회의엔 영어 용어가 섞이는 것이 정상이므로 라틴 문자는 이질로 보지 않는다.
_FOREIGN_SCRIPT_RANGES = (
    "\u0370-\u03ff"   # 그리스
    "\u0400-\u052f"   # 키릴
    "\u0530-\u058f"   # 아르메니아
    "\u0590-\u05ff"   # 히브리
    "\u0600-\u06ff"   # 아랍
    "\u0700-\u074f"   # 시리아
    "\u0900-\u097f"   # 데바나가리
    "\u0e00-\u0e7f"   # 타이
    "\u3040-\u30ff"   # 히라가나·가타카나
    "\u4e00-\u9fff"   # CJK 통합 한자
    "\uf900-\ufaff"   # CJK 호환 한자
)
_RE_FOREIGN = re.compile(f"[{_FOREIGN_SCRIPT_RANGES}]")
_RE_HANGUL = re.compile(r"[가-힣ㄱ-ㅎㅏ-ㅣ]")
_RE_LATIN = re.compile(r"[A-Za-z]")
#: 문자(letter)만 — 공백·숫자·구두점 제외. 비율 계산의 분모.
_RE_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)

SUSPECT_MARKER = "[불명]"

#: 반복/중복 판정 대상 최소 길이(정규화 문자 수) — "네.", "맞아요." 같은 짧은
#: 맞장구는 실제로 여러 번 나오는 것이 정상이므로 중복 제거 대상에서 제외한다.
_MIN_DEDUPE_LEN = 4


def is_cjk_hallucination(text: str, threshold: float = 0.3) -> bool:
    """텍스트 내 CJK(중국어/일본어) 문자 비율이 threshold 이상이면 True."""
    if not text or len(text.strip()) < 2:
        return False
    cjk_count = len(_RE_CJK.findall(text))
    return (cjk_count / len(text)) >= threshold


# ──────────────────────────────────────────────
#  이질 문자(스크립트) 환각
# ──────────────────────────────────────────────
def foreign_script_ratio(text: str) -> float:
    """전체 문자 수 대비 이질 문자(키릴·아랍·한자·가나 등) 비율."""
    letters = _RE_LETTER.findall(text or "")
    if not letters:
        return 0.0
    foreign = sum(1 for ch in letters if _RE_FOREIGN.match(ch))
    return foreign / len(letters)


def is_script_mismatch(text: str, language: str = "ko",
                       threshold: float = 0.3) -> bool:
    """세션 언어에 맞지 않는 문자 체계가 섞여 있으면 True (환각 의심).

    language 는 향후 확장을 위해 받되, 판정 자체는 언어 무관하게 "한국어·영어
    회의에 나올 수 없는 문자"만 본다 — ko 세션의 영어 용어, en 세션의 한국어
    고유명사처럼 정상적인 혼용을 오탐하지 않기 위함.
    """
    if not text or len(text.strip()) < 2:
        return False
    return foreign_script_ratio(text) >= threshold


def _norm(text: str) -> str:
    """비교용 정규화: 소문자 + 문자/숫자만 (공백 단일화)."""
    return " ".join(re.sub(r"[^\w\s]", " ", (text or "").lower()).split())


def looks_like_gibberish_fragment(text: str) -> bool:
    """한글이 하나도 없는 짧은 라틴 조각(예: 'Na velolodu', 'Okei') 인지.

    한국어 회의에서 이런 조각이 **여러 번 되풀이되면** 무음 구간 환각일 가능성이
    높다. 단독 1회 등장(영어 한마디)은 정상일 수 있어 호출자가 반복 횟수와 함께
    판단한다 — 이 함수만으로 표시하지 않는다.
    """
    t = (text or "").strip()
    if not t or _RE_HANGUL.search(t):
        return False
    if not _RE_LATIN.search(t):
        return False
    words = _norm(t).split()
    return len(t) <= 30 and len(words) <= 5


# ──────────────────────────────────────────────
#  반복 축약
# ──────────────────────────────────────────────
_RE_SENT_SPLIT = re.compile(r"(?<=[.!?。！？])\s+")


def _collapse_consecutive(keys: List[str], values: Optional[List[str]] = None,
                          max_n: int = 12,
                          max_passes: int = 3) -> Tuple[List[str], bool]:
    """연속으로 되풀이되는 n-gram(1..max_n)을 1회로 축약. (값 목록, 변경여부) 반환.

    - 비교는 `keys`(정규화 문자열), 출력은 `values`(원문) 기준 — 구두점·대소문자
      차이로 반복을 놓치지 않으면서 원문 표기를 보존한다.
    - 위치별로 스캔한다. n 배수 정렬로만 훑으면 시작 위치가 어긋난 반복(예: 앞에
      3토큰이 붙은 7토큰 반복)을 놓친다.
    - 되풀이 그룹에서는 **마지막** 반복을 남긴다 — 문장 종결 구두점이 대개 마지막
      조각에 붙어 있어 결과가 자연스럽다.
    """
    ks = list(keys)
    vals = list(values if values is not None else keys)
    changed_any = False
    for _ in range(max_passes):
        res_k: List[str] = []
        res_v: List[str] = []
        changed = False
        i = 0
        while i < len(ks):
            matched = False
            for n in range(1, max_n + 1):
                if i + 2 * n > len(ks):
                    break
                gram = ks[i:i + n]
                j = i + n
                reps = 1
                while ks[j:j + n] == gram:
                    reps += 1
                    j += n
                if reps > 1:
                    res_k.extend(ks[j - n:j])
                    res_v.extend(vals[j - n:j])
                    i = j
                    matched = True
                    changed = True
                    break
            if not matched:
                res_k.append(ks[i])
                res_v.append(vals[i])
                i += 1
        ks, vals = res_k, res_v
        if not changed:
            break
        changed_any = True
    return vals, changed_any


def collapse_repetitions(text: str, max_n: int = 12) -> str:
    """한 텍스트 안의 연속 중복(문장 단위 → 토큰 단위)을 1회로 축약.

    예) "A. B. A. B. A. B." → "A. B."
        "뭐가 있냐 뭐가 있냐 뭐가 있냐." → "뭐가 있냐."
    """
    t = (text or "").strip()
    if not t:
        return t

    sents = [s for s in _RE_SENT_SPLIT.split(t) if s.strip()]
    if len(sents) > 1:
        keys = [_norm(s) or s for s in sents]
        collapsed, changed = _collapse_consecutive(keys, sents, max_n=max_n)
        if changed:
            t = " ".join(collapsed).strip()

    tokens = t.split()
    if len(tokens) > 1:
        keys = [_norm(tok) or tok for tok in tokens]
        collapsed, changed = _collapse_consecutive(keys, tokens, max_n=max_n)
        if changed:
            t = " ".join(collapsed).strip()
    return t


def is_near_duplicate(a: str, b: str, threshold: float = 0.95) -> bool:
    """정규화 후 사실상 같은 문장이면 True.

    임계값을 높게(0.95) 잡고 숫자 차이는 따로 본다 — "첫 번째 안건입니다" /
    "두 번째 안건입니다", "3시에 만나요" / "4시에 만나요" 처럼 한 글자만 다른
    **다른 내용**을 중복으로 지워버리는 사고를 막기 위함.
    """
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if sorted(re.findall(r"\d+", na)) != sorted(re.findall(r"\d+", nb)):
        return False   # 숫자가 다르면 다른 내용
    if abs(len(na) - len(nb)) > max(len(na), len(nb)) * 0.5:
        return False
    return SequenceMatcher(None, na, nb).ratio() >= threshold


def unique_ratio(texts: List[str]) -> float:
    """텍스트 목록의 고유 비율(1.0=중복 없음). 보정 결과 폐기 판단용."""
    keys = [_norm(t) for t in texts if _norm(t)]
    if not keys:
        return 1.0
    return len(set(keys)) / len(keys)


def mark_suspect(text: str, marker: str = SUSPECT_MARKER) -> str:
    """환각 의심 텍스트에 표시만 붙인다(삭제하지 않는다)."""
    t = (text or "").strip()
    if not t or t.startswith(marker):
        return t
    return f"{marker} {t}"


def _dedupe_policy(key: str, lookback: int,
                   max_occurrences: int) -> Optional[Tuple[int, int]]:
    """길이별 중복 허용 정책 — (lookback, max_occurrences) 또는 None(대상 아님).

    한국어는 짧은 문장에 정보가 압축되므로 문자 수 기준을 낮게 잡되, 맞장구
    ("네", "맞아요")는 보존하고 짧은 구절은 3회까지 허용해 오삭제를 피한다.
    """
    n = len(key)
    if n < _MIN_DEDUPE_LEN:
        return None
    if n < 8:
        return max(2, lookback // 2), max_occurrences + 1
    return lookback, max_occurrences


def dedupe_segments(segments: List[Dict], lookback: int = 8,
                    threshold: float = 0.95,
                    max_occurrences: int = 2) -> Tuple[List[Dict], int]:
    """반복 세그먼트 제거 — 첫 등장을 남기고 되풀이를 버린다.

    R1(국소): 직전 `lookback` 개 중 근접 중복이면 버린다.
    R2(전역): 같은 문장이 이미 허용 횟수만큼 남아 있으면 버린다.
    길이별 정책은 `_dedupe_policy` 참조 — 짧은 맞장구는 손대지 않는다.
    """
    kept: List[Dict] = []
    counts: Dict[str, int] = {}
    dropped = 0
    for seg in segments:
        text = (seg.get("text") or "").strip()
        key = _norm(text)
        policy = _dedupe_policy(key, lookback, max_occurrences)
        if policy is None:
            kept.append(seg)
            continue
        look, max_occ = policy
        if counts.get(key, 0) >= max_occ:
            dropped += 1
            continue
        recent = [k.get("text") or "" for k in kept[-look:]]
        if any(is_near_duplicate(text, r, threshold) for r in recent):
            dropped += 1
            continue
        counts[key] = counts.get(key, 0) + 1
        kept.append(seg)
    return kept, dropped


# ──────────────────────────────────────────────
#  전사 정화 (공용 진입점)
# ──────────────────────────────────────────────
def infer_language(segments_or_text: Any) -> str:
    """전사 내용으로 세션 언어 추정 — 한글이 있으면 ko, 없으면 en."""
    if isinstance(segments_or_text, str):
        text = segments_or_text
    else:
        text = " ".join(
            str((s or {}).get("text", "")) for s in (segments_or_text or [])
        )
    letters = _RE_LETTER.findall(text)
    if not letters:
        return "ko"
    hangul = sum(1 for ch in letters if _RE_HANGUL.match(ch))
    return "ko" if (hangul / len(letters)) >= 0.1 else "en"


def sanitize_transcript(
    segments: List[Dict],
    language: str = "",
    enabled: bool = True,
    gibberish_min_occurrences: int = 3,
) -> Tuple[List[Dict], Dict[str, int]]:
    """전사 세그먼트 정화 — 반복 축약·중복 제거·환각 표시.

    보수적 정책: 내용을 지우는 것은 **반복(같은 말의 3번째 이후)** 뿐이고,
    환각 의심 텍스트는 `[불명]` 표시만 붙여 남긴다.

    반환: (정화된 세그먼트, {"collapsed": n, "deduped": n, "marked": n, "empty": n})
    """
    stats = {"collapsed": 0, "deduped": 0, "marked": 0, "empty": 0}
    segs = list(segments or [])
    if not enabled or not segs:
        return segs, stats

    lang = (language or "").strip().lower()
    if not lang or lang == "auto":
        lang = infer_language(segs)

    # 1) 세그먼트 내부 반복 축약 + 빈 세그먼트 제거
    stage1: List[Dict] = []
    for seg in segs:
        raw = (seg.get("text") or "").strip()
        if not raw:
            stats["empty"] += 1
            continue
        collapsed = collapse_repetitions(raw)
        new_seg = dict(seg)
        if collapsed != raw:
            stats["collapsed"] += 1
            new_seg["text"] = collapsed
            if (seg.get("text_original") or "").strip() == raw:
                new_seg["text_original"] = collapsed
        stage1.append(new_seg)

    # 2) 반복 조각 빈도 집계(중복 제거 전 기준) — 정체불명 라틴 조각 표시에 사용
    freq: Dict[str, int] = {}
    for seg in stage1:
        key = _norm(seg.get("text") or "")
        if key:
            freq[key] = freq.get(key, 0) + 1

    # 3) 반복 세그먼트 제거
    stage2, dropped = dedupe_segments(stage1)
    stats["deduped"] = dropped

    # 4) 환각 의심 표시
    result: List[Dict] = []
    for seg in stage2:
        text = (seg.get("text") or "").strip()
        suspect = is_script_mismatch(text, lang) or is_cjk_hallucination(text)
        if not suspect and looks_like_gibberish_fragment(text):
            suspect = freq.get(_norm(text), 0) >= gibberish_min_occurrences
        marked = mark_suspect(text) if suspect else text
        if marked != text:   # 이미 표시된 텍스트는 다시 세지 않는다(멱등)
            new_seg = dict(seg)
            new_seg["text"] = marked
            if (seg.get("text_original") or "").strip() == text:
                new_seg["text_original"] = marked
            stats["marked"] += 1
            result.append(new_seg)
        else:
            result.append(seg)
    return result, stats


def sanitize_stats_line(stats: Optional[Dict[str, int]]) -> str:
    """로그용 한 줄 요약 — 변경이 없으면 빈 문자열."""
    if not stats or not any(stats.values()):
        return ""
    parts = []
    if stats.get("collapsed"):
        parts.append(f"반복축약 {stats['collapsed']}")
    if stats.get("deduped"):
        parts.append(f"중복제거 {stats['deduped']}")
    if stats.get("marked"):
        parts.append(f"환각표시 {stats['marked']}")
    if stats.get("empty"):
        parts.append(f"빈세그먼트 {stats['empty']}")
    return ", ".join(parts)
