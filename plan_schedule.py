"""
plan_schedule.py — 회의 일정 정리 · 충돌/중복 점검 · Obsidian 대시보드
========================================================================
Obsidian 볼트의 00_Meetings 노트(프론트매터 date/time/attendees/status…)를 읽어
  (1) 다가오는 회의 일정 정리
  (2) 시간 겹침 / 같은 사람 이중예약 / 준비 미비 등 '충돌' 감지
  (3) Obsidian 일정 대시보드 노트(_일정.md) 자동 생성·갱신
을 수행한다. Cowork에서 요청 시 직접 호출하거나, CLI/워처로도 사용 가능.

CLI:
    python plan_schedule.py --vault "D:\\Obsidian\\MyVault"            # 일정+충돌 출력
    python plan_schedule.py --vault "..." --write-dashboard           # 대시보드 노트 갱신
    python plan_schedule.py --vault "..." --days 14                   # 향후 14일만

표준 라이브러리만 사용. obsidian.parse_frontmatter / date_key 재사용.
"""

from __future__ import annotations

import os
import glob
import argparse
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

from obsidian import parse_frontmatter, date_key, _as_str_list

DEFAULT_DURATION_MIN = 60   # 회의에 duration/end 가 없을 때 가정 길이(분)


# ── 로드 ──────────────────────────────────────────────────────
def _parse_time(t: str):
    """'09:00' / '9:00' / '0900' → (h, m) 또는 None."""
    t = str(t or "").strip()
    if not t:
        return None
    import re
    m = re.search(r"(\d{1,2})\s*[:시]\s*(\d{2})", t) or re.match(r"^(\d{2})(\d{2})$", t)
    if not m:
        return None
    try:
        h, mi = int(m.group(1)), int(m.group(2))
        if 0 <= h < 24 and 0 <= mi < 60:
            return h, mi
    except ValueError:
        pass
    return None


def _body_has_agenda(body: str) -> bool:
    """'## 안건' 섹션에 실제 항목이 있는지(자동 리서치 블록 제외)."""
    import re
    import plan_research
    b = plan_research.strip_auto_block(body)
    m = re.search(r"^##\s+안건\s*$(.*?)(?=^##\s|\Z)", b, flags=re.MULTILINE | re.DOTALL)
    if not m:
        return False
    for ln in m.group(1).splitlines():
        s = ln.strip().lstrip("-*").strip()
        if s and s not in (">",):
            return True
    return False


def load_meetings(vault_path: str, notes_subdir: str = "00_Meetings") -> List[Dict[str, Any]]:
    root = os.path.join(vault_path, notes_subdir)
    if not os.path.isdir(root):
        root = vault_path
    out: List[Dict[str, Any]] = []
    for p in glob.glob(os.path.join(root, "**", "*.md"), recursive=True):
        if os.path.basename(p).startswith("_"):
            continue
        try:
            content = open(p, encoding="utf-8").read()
        except Exception:
            continue
        meta, body = parse_frontmatter(content)
        if not meta:
            continue
        if meta.get("type") not in ("meeting", "seminar", "lecture") and not meta.get("status"):
            continue
        d = date_key(meta.get("date", ""))
        hm = _parse_time(meta.get("time", ""))
        start = None
        if d and hm:
            try:
                yy, mm, dd = map(int, d.split("-"))
                start = datetime(yy, mm, dd, hm[0], hm[1])
            except ValueError:
                start = None
        elif d:
            try:
                yy, mm, dd = map(int, d.split("-"))
                start = datetime(yy, mm, dd, 0, 0)
            except ValueError:
                start = None
        dur = meta.get("duration")
        try:
            dur = int(dur)
        except (TypeError, ValueError):
            dur = DEFAULT_DURATION_MIN
        out.append({
            "path": os.path.relpath(p, vault_path).replace("\\", "/"),
            "title": meta.get("title", "") or os.path.basename(p)[:-3],
            "date": d, "time": meta.get("time", ""),
            "start": start, "end": start + timedelta(minutes=dur) if start else None,
            "has_time": hm is not None,
            "status": str(meta.get("status", "")).strip().lower(),
            "attendees": _as_str_list(meta.get("attendees")),
            "topic": meta.get("topic", ""),
            "has_agenda": _body_has_agenda(body),
            "researched": bool(meta.get("research_hash")),
            "matched_plan": meta.get("matched_plan", ""),
        })
    return out


# ── 충돌/중복 점검 ────────────────────────────────────────────
def pending_merges(meetings: List[Dict[str, Any]]):
    """녹음 노트가 계획과 매칭됐지만 아직 병합 안 된 항목 [(recording, plan_or_None), ...].
    계획 노트가 아직 status: planned 이면 '병합 대기'로 본다."""
    by_path = {m["path"]: m for m in meetings}
    out = []
    for m in meetings:
        mp = m.get("matched_plan")
        if not mp:
            continue
        plan = by_path.get(mp)
        if plan is None or plan.get("status") == "planned":
            out.append((m, plan))
    return out


def detect_conflicts(meetings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """시간 겹침 + 같은 사람 이중예약 감지. 반환: 충돌 항목 리스트."""
    timed = [m for m in meetings if m["start"] and m["end"] and m["has_time"]]
    conflicts = []
    for i in range(len(timed)):
        for j in range(i + 1, len(timed)):
            a, b = timed[i], timed[j]
            if a["start"] < b["end"] and b["start"] < a["end"]:   # 구간 겹침
                shared = sorted(set(a["attendees"]) & set(b["attendees"]))
                conflicts.append({
                    "kind": "이중예약" if shared else "시간겹침",
                    "a": a, "b": b, "shared": shared,
                })
    return conflicts


def prep_warnings(meetings: List[Dict[str, Any]], now: datetime,
                  within_days: int = 3) -> List[Dict[str, Any]]:
    """곧 다가오는 planned 회의인데 안건/사전조사가 비어 있으면 준비 미비 경고."""
    warns = []
    horizon = now + timedelta(days=within_days)
    for m in meetings:
        if m["status"] != "planned" or not m["start"]:
            continue
        if now <= m["start"] <= horizon and not (m["has_agenda"] or m["researched"]):
            warns.append(m)
    return warns


# ── 대시보드 ──────────────────────────────────────────────────
def _fmt_dt(m) -> str:
    if m["start"]:
        return m["start"].strftime("%Y-%m-%d %H:%M") if m["has_time"] else m["start"].strftime("%Y-%m-%d")
    return f"{m['date']} {m['time']}".strip() or "(일시 미정)"


def build_dashboard_md(meetings, conflicts, warns, now: datetime,
                       days: Optional[int] = None) -> str:
    upcoming = [m for m in meetings if m["start"] and m["start"] >= now.replace(hour=0, minute=0)]
    if days is not None:
        horizon = now + timedelta(days=days)
        upcoming = [m for m in upcoming if m["start"] <= horizon]
    upcoming.sort(key=lambda m: m["start"])

    L = ["---", "type: moc", "tags:", "  - 일정", "  - 대시보드",
         f"updated: \"{now.isoformat(timespec='seconds')}\"", "---", "",
         "# 📆 회의 일정 대시보드", "",
         f"> 자동 생성 · {now.strftime('%Y-%m-%d %H:%M')} · "
         f"다가오는 회의 {len(upcoming)}건 · 충돌 {len(conflicts)}건 · 준비미비 {len(warns)}건 · 병합대기 {len(pending_merges(meetings))}건", ""]

    if conflicts:
        L += ["## ⚠️ 일정 충돌", ""]
        for c in conflicts:
            a, b = c["a"], c["b"]
            extra = f" — 중복 인원: {', '.join(c['shared'])}" if c["shared"] else ""
            L.append(f"- **{c['kind']}**: [[{os.path.basename(a['path'])[:-3]}|{a['title']}]] "
                     f"({_fmt_dt(a)}) ↔ [[{os.path.basename(b['path'])[:-3]}|{b['title']}]] "
                     f"({_fmt_dt(b)}){extra}")
        L.append("")

    if warns:
        L += ["## 📝 준비 미비 (안건·사전조사 비어 있음)", ""]
        for m in warns:
            L.append(f"- [[{os.path.basename(m['path'])[:-3]}|{m['title']}]] — {_fmt_dt(m)}")
        L.append("")

    pend = pending_merges(meetings)
    if pend:
        L += ["## 🔗 병합 대기 (확인 후 병합)", ""]
        for rec, plan in pend:
            pt = f"[[{os.path.basename(plan['path'])[:-3]}|{plan['title']}]]" if plan else rec.get("matched_plan", "")
            L.append(f"- 녹음 [[{os.path.basename(rec['path'])[:-3]}|{rec['title']}]] → 계획 {pt}")
        L.append("")

    L += ["## 🗓️ 다가오는 회의", ""]
    if upcoming:
        L += ["| 일시 | 회의 | 상태 | 참석자 | 준비 |", "|---|---|---|---|---|"]
        for m in upcoming:
            ready = "✅" if (m["has_agenda"] or m["researched"]) else ("—" if m["status"] != "planned" else "❌")
            att = ", ".join(m["attendees"][:6])
            L.append(f"| {_fmt_dt(m)} | [[{os.path.basename(m['path'])[:-3]}|{m['title']}]] "
                     f"| {m['status'] or '-'} | {att} | {ready} |")
    else:
        L.append("> 예정된 회의가 없습니다.")
    L.append("")
    L += ["## 전체 회의 (Dataview)", "",
          "```dataview", "TABLE date AS \"날짜\", time AS \"시간\", status AS \"상태\", topic AS \"주제\"",
          "FROM \"00_Meetings\"", "WHERE type = \"meeting\"", "SORT date DESC", "```", ""]
    return "\n".join(L)


# ── 요약(텍스트, Cowork/CLI 출력용) ──────────────────────────
def summarize(meetings, conflicts, warns, now: datetime, days: Optional[int] = None) -> str:
    upcoming = sorted([m for m in meetings if m["start"] and m["start"] >= now.replace(hour=0, minute=0)],
                      key=lambda m: m["start"])
    if days is not None:
        horizon = now + timedelta(days=days)
        upcoming = [m for m in upcoming if m["start"] <= horizon]
    pend = pending_merges(meetings)
    lines = [f"다가오는 회의 {len(upcoming)}건 / 충돌 {len(conflicts)}건 / 임박 준비미비 {len(warns)}건 / 병합 대기 {len(pend)}건", ""]
    for m in upcoming:
        ready = "준비완료" if (m["has_agenda"] or m["researched"]) else ("" if m["status"] != "planned" else "준비 필요")
        lines.append(f"  {_fmt_dt(m)}  [{m['status'] or '-'}] {m['title']}"
                     + (f"  ({ready})" if ready else "")
                     + (f"  · {', '.join(m['attendees'])}" if m["attendees"] else ""))
    if conflicts:
        lines += ["", "⚠️ 충돌:"]
        for c in conflicts:
            extra = f" (중복인원 {', '.join(c['shared'])})" if c["shared"] else ""
            lines.append(f"  - {c['kind']}: {c['a']['title']} ↔ {c['b']['title']}{extra}")
    if warns:
        lines += ["", "📝 준비 미비:"]
        for m in warns:
            lines.append(f"  - {m['title']} ({_fmt_dt(m)})")
    if pend:
        lines += ["", "🔗 병합 대기 (녹음이 계획과 매칭됨 — 확인 후 병합):"]
        for rec, plan in pend:
            pt = plan["title"] if plan else rec.get("matched_plan", "")
            lines.append(f"  - 녹음 '{rec['title']}'  →  계획 '{pt}'")
    return "\n".join(lines)


def write_dashboard(vault_path: str, md: str, notes_subdir: str = "00_Meetings",
                    filename: str = "_일정.md") -> str:
    root = os.path.join(vault_path, notes_subdir)
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, filename)
    open(path, "w", encoding="utf-8").write(md)
    return path


def main():
    ap = argparse.ArgumentParser(description="회의 일정 정리·충돌 점검·대시보드")
    ap.add_argument("--vault", default="", help="Obsidian 볼트 폴더 (미지정 시 config.obsidian.vault_path 사용)")
    ap.add_argument("--notes-subdir", default="00_Meetings")
    ap.add_argument("--days", type=int, default=None, help="향후 N일만(기본: 전체 미래)")
    ap.add_argument("--write-dashboard", action="store_true", help="_일정.md 대시보드 갱신")
    ap.add_argument("--prep-days", type=int, default=3, help="준비 미비 경고 기준(일)")
    args = ap.parse_args()

    vault = args.vault
    if not vault:
        try:
            import config_loader as _cfg
            vault = _cfg.get("obsidian.vault_path", "") or ""
        except ImportError:
            pass
    if not vault:
        ap.error("--vault 가 필요합니다. 또는 config.json 의 obsidian.vault_path 를 설정하세요.")

    now = datetime.now()
    meetings = load_meetings(vault, args.notes_subdir)
    conflicts = detect_conflicts(meetings)
    warns = prep_warnings(meetings, now, within_days=args.prep_days)
    print(summarize(meetings, conflicts, warns, now, days=args.days))
    if args.write_dashboard:
        md = build_dashboard_md(meetings, conflicts, warns, now, days=args.days)
        path = write_dashboard(vault, md, args.notes_subdir)
        print(f"\n대시보드 갱신 → {path}")


if __name__ == "__main__":
    main()
