#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
STT 세그먼트 → 사람이 읽을 수 있는 스크립트(Transcript) 마크다운 변환.
meeting_minutes.py에서 분리 (2026-07 리팩토링 3단계).
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List

from meeting_minutes_app.meeting_pipeline.meeting_minutes import has_timestamps, ts


def build_script_md(segments: List[Dict], include_original: bool = False) -> str:
    use_ts = has_timestamps(segments)
    lines = [
        "# 스크립트 (Transcript)\n",
        f"> 생성: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"> 세그먼트: {len(segments)}개\n",
        "---\n",
    ]
    cur_spk = None
    for s in segments:
        spk = s.get("speaker", "")
        if spk and spk != cur_spk:
            lines.append(f"\n### {spk}\n")
            cur_spk = spk

        line = (f"`[{ts(s['start'])}]` {s['text']}" if use_ts else s["text"])
        if include_original and s.get("text_original"):
            line += f"\n> _{s['text_original']}_"
        lines.append(line)

    return "\n".join(lines)
