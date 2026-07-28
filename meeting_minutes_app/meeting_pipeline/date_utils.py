"""Date parsing helpers shared by batch, ingest, and Obsidian paths."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path


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


def normalize_iso_date(text: str) -> str:
    """임의 형식의 날짜 문자열에서 YYYY-MM-DD를 뽑는다(정렬·비교 키용).

    지원: ISO(2026-07-08), 한글(2026년 06월 29일), 슬래시/닷(2026/7/8), 컴팩트(20260708),
    시각이 붙은 형태(2026-07-08T14:00). 못 뽑으면 ''.

    parse_iso_date_from_text 는 '-_.' 구분자·컴팩트만 다뤄 한글 날짜("2026년 06월 29일")를
    놓쳤다 — 그 경우 정렬 키가 원문 그대로라 한글 '년'(U+B144)이 숫자보다 커서 한글 날짜
    노트가 '가장 최근'으로 잘못 올라왔다. 이 함수는 구분자를 가리지 않고 연·월·일을 뽑는다."""
    s = str(text or "").strip().strip('"')
    if not s:
        return ""
    # 구분자(하이픈/한글/슬래시/닷/공백 등 1~4자)로 구분된 연·월·일
    m = re.search(r"(20\d{2}|19\d{2})\D{1,4}(\d{1,2})\D{1,4}(\d{1,2})", s)
    if m:
        y, mo, d = map(int, m.groups())
        if _valid_date(y, mo, d):
            return f"{y:04d}-{mo:02d}-{d:02d}"
    # 컴팩트 YYYYMMDD
    m = re.search(r"(?<!\d)(20\d{2}|19\d{2})(\d{2})(\d{2})(?!\d)", s)
    if m:
        y, mo, d = map(int, m.groups())
        if _valid_date(y, mo, d):
            return f"{y:04d}-{mo:02d}-{d:02d}"
    return ""


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

