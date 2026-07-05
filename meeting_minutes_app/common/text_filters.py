#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""텍스트 품질 필터 (STT 환각 감지 등)."""

import re

_CJK_RANGES = (
    "　-〿"   # CJK 기호
    "぀-ゟ"   # 히라가나
    "゠-ヿ"   # 가타카나
    "一-鿿"   # CJK 통합 한자
    "豈-﫿"   # CJK 호환 한자
)
_RE_CJK = re.compile(f'[{_CJK_RANGES}]')


def is_cjk_hallucination(text: str, threshold: float = 0.3) -> bool:
    """텍스트 내 CJK(중국어/일본어) 문자 비율이 threshold 이상이면 True."""
    if not text or len(text.strip()) < 2:
        return False
    cjk_count = len(_RE_CJK.findall(text))
    return (cjk_count / len(text)) >= threshold
