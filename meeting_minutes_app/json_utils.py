"""
json_utils.py — LLM 응답 JSON 강건 파싱
============================================================
LLM이 반환하는 JSON은 코드펜스, 앞뒤 설명 문장, trailing comma,
스마트 따옴표 등으로 자주 오염된다. 이 모듈은 단계적 복구로
파싱 성공률을 높인다 (외부 의존성 없음).

사용:
    from json_utils import parse_json_loose
    items = parse_json_loose(raw, expect="list", default=[])

복구 단계:
    1) 코드펜스 제거 후 json.loads
    2) 첫 균형 괄호 블록([...] 또는 {...}) 추출 후 json.loads
       — 문자열 리터럴 내부의 괄호/따옴표는 무시 (정규식 \\[.*\\] 방식의
         과잉 매칭 문제 해결)
    3) trailing comma·스마트 따옴표 복구 후 재시도
    실패 시 default 반환 (예외 없음).
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

_FENCE_OPEN = re.compile(r"^\s*```[a-zA-Z0-9_-]*\s*\n")
_FENCE_CLOSE = re.compile(r"\n?\s*```\s*$")
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")
_SMART_QUOTES = str.maketrans({
    "“": '"', "”": '"',   # “ ”
    "‘": "'", "’": "'",   # ‘ ’
})


def strip_code_fences(raw: str) -> str:
    """앞뒤 마크다운 코드펜스를 제거한다."""
    text = (raw or "").strip()
    text = _FENCE_OPEN.sub("", text)
    text = _FENCE_CLOSE.sub("", text)
    return text.strip()


def extract_balanced(raw: str, open_ch: str, close_ch: str) -> Optional[str]:
    """첫 open_ch부터 괄호 균형이 맞는 부분 문자열을 반환한다.

    JSON 문자열 리터럴 내부의 괄호·이스케이프는 무시하므로
    `re.search(r"\\[.*\\]")` 류의 과잉/과소 매칭이 없다.
    """
    start = raw.find(open_ch)
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(raw)):
        ch = raw[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return raw[start:i + 1]
    return None


def _repair(text: str) -> str:
    """흔한 JSON 오염(trailing comma, 스마트 따옴표)을 복구한다."""
    text = text.translate(_SMART_QUOTES)
    text = _TRAILING_COMMA.sub(r"\1", text)
    return text


def parse_json_loose(raw: str, expect: Optional[str] = None,
                     default: Any = None) -> Any:
    """LLM 응답에서 JSON을 단계적으로 복구해 파싱한다.

    Args:
        raw:     LLM 원문 응답
        expect:  "list" | "dict" | None — 지정 시 타입이 다르면 default 반환
        default: 모든 단계 실패 시 반환값 (예외를 던지지 않는다)
    """
    if not raw or not isinstance(raw, str):
        return default

    text = strip_code_fences(raw)
    candidates = [text]

    # expect에 맞는 균형 블록 우선, 없으면 양쪽 모두 시도
    pairs = {"list": [("[", "]")], "dict": [("{", "}")]}.get(
        expect or "", [("[", "]"), ("{", "}")]
    )
    for open_ch, close_ch in pairs:
        block = extract_balanced(text, open_ch, close_ch)
        if block and block != text:
            candidates.append(block)

    for candidate in candidates:
        for attempt in (candidate, _repair(candidate)):
            try:
                obj = json.loads(attempt)
            except Exception:
                continue
            if expect == "list" and not isinstance(obj, list):
                continue
            if expect == "dict" and not isinstance(obj, dict):
                continue
            return obj
    return default
