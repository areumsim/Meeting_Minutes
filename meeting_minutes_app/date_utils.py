"""Date parsing helpers shared by batch, ingest, and Obsidian paths."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Optional


def _valid_date(year: int, month: int, day: int) -> bool:
    try:
        datetime(year, month, day)
        return True
    except ValueError:
        return False


def parse_iso_date_from_text(text: str, *, default_today: bool = False) -> str:
    """Extract YYYY-MM-DD from common recording filename/path patterns."""
    s = str(text or "").replace("\\", "/")

    # YYYY-MM-DD / YYYY_MM_DD / YYYY.MM.DD
    m = re.search(r"(?<!\d)(20\d{2}|19\d{2})[-_.](\d{2})[-_.](\d{2})(?!\d)", s)
    if m:
        y, mo, d = map(int, m.groups())
        if _valid_date(y, mo, d):
            return f"{y:04d}-{mo:02d}-{d:02d}"

    # YYYYMMDD, optionally followed by time.
    m = re.search(r"(?<!\d)(20\d{2}|19\d{2})(\d{2})(\d{2})(?!\d)", s)
    if m:
        y, mo, d = map(int, m.groups())
        if _valid_date(y, mo, d):
            return f"{y:04d}-{mo:02d}-{d:02d}"

    # YYMMDD. Project recordings are current-century; keep this conservative.
    m = re.search(r"(?<!\d)(\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)", s)
    if m:
        yy, mo, d = map(int, m.groups())
        y = 2000 + yy
        if _valid_date(y, mo, d):
            return f"{y:04d}-{mo:02d}-{d:02d}"

    return datetime.now().strftime("%Y-%m-%d") if default_today else ""


def parse_session_dt_from_path(path: str, *, default: str = "") -> str:
    """Return Korean session datetime text from a filename or path."""
    s = str(path or "")
    stem = Path(s).stem
    search_text = s.replace("\\", "/")

    # YYYY-MM-DD 14.10 / YYYY-MM-DD 14:10
    m = re.search(
        r"(?<!\d)(20\d{2}|19\d{2})[-_.](\d{2})[-_.](\d{2})[ T_]+(\d{1,2})[.:시](\d{2})(?!\d)",
        search_text,
    )
    if m:
        y, mo, d, h, mi = map(int, m.groups())
        if _valid_date(y, mo, d) and 0 <= h < 24 and 0 <= mi < 60:
            return f"{y:04d}년 {mo:02d}월 {d:02d}일 {h:02d}:{mi:02d}"

    # YYYYMMDD_HHMMSS / YYYYMMDD-HHMMSS
    m = re.search(r"(?<!\d)(20\d{2}|19\d{2})(\d{2})(\d{2})[_-](\d{2})(\d{2})(\d{2})(?!\d)", stem)
    if m:
        y, mo, d, h, mi, _sec = map(int, m.groups())
        if _valid_date(y, mo, d) and 0 <= h < 24 and 0 <= mi < 60:
            return f"{y:04d}년 {mo:02d}월 {d:02d}일 {h:02d}:{mi:02d}"

    iso = parse_iso_date_from_text(search_text, default_today=False)
    if iso:
        y, mo, d = iso.split("-")
        return f"{y}년 {mo}월 {d}일"
    return default


def iso_to_yymmdd(iso_date: str) -> str:
    d = parse_iso_date_from_text(iso_date, default_today=False)
    return f"{d[2:4]}{d[5:7]}{d[8:10]}" if d else ""

