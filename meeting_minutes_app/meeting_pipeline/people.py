"""
people.py — 인물/팀 레지스트리 (참석자 ↔ 사람 ↔ 팀 매칭)
==========================================================
회의 참석자 표기("최민석(팀장)", "정하윤 수석", "심아름 책임(나)")를
이름/직책으로 분리하고, 01_References/People/<이름>.md 인물 노트를 만들어
직책(role)·부서(department)·회사(company)를 기록한다. 인물 노트의 dataview가
attendees 에 그 이름이 든 회의를 자동으로 모아주므로 사람별 협업 이력이 생긴다.

CLI:
    python run_meeting.py people --vault "D:\\Claude\\QC" --from-meetings
    python run_meeting.py people --vault "D:\\Claude\\QC" --add "최민석(팀장),정하윤 수석"
"""

from __future__ import annotations

import os
import re
import glob
from typing import Optional, List, Dict, Tuple

from meeting_minutes_app.wiki_core.obsidian import parse_frontmatter, build_frontmatter, safe_filename, _as_str_list

# 이름 뒤/괄호 안에 올 수 있는 직책 토큰
ROLE_TOKENS = ["팀장", "본부장", "실장", "센터장", "그룹장", "파트장", "리더",
               "수석", "책임", "선임", "주임", "사원", "연구원", "매니저",
               "대표", "이사", "상무", "전무", "부사장", "사장",
               "부장", "차장", "과장", "대리", "주무관", "교수", "박사"]
_ROLE_RE = "|".join(sorted(ROLE_TOKENS, key=len, reverse=True))


def parse_attendee(raw: str) -> Tuple[str, str]:
    """'최민석(팀장)'→('최민석','팀장'), '정하윤 수석'→('정하윤','수석'),
    '심아름 책임(나)'→('심아름','책임'), '민지'→('민지','')."""
    s = str(raw or "").strip()
    role = ""
    # 괄호 안에서 직책 추출(없으면 괄호 내용은 메모로 보고 버림)
    m = re.search(r"\(([^)]*)\)", s)
    if m:
        inner = m.group(1).strip()
        rm = re.search(_ROLE_RE, inner)
        if rm:
            role = rm.group(0)
        s = re.sub(r"\([^)]*\)", "", s).strip()
    # 이름 뒤 공백+직책
    rm2 = re.search(r"\s+(" + _ROLE_RE + r")$", s)
    if rm2:
        if not role:
            role = rm2.group(1)
        s = s[:rm2.start()].strip()
    name = s.strip()
    return name, role


def person_path(vault_root: str, name: str, refs_subdir: str = "01_References") -> str:
    return os.path.join(vault_root, refs_subdir, "People", safe_filename(name) + ".md")


def _person_content(name: str, role: str = "", department: str = "",
                    company: str = "") -> str:
    meta = {"name": name, "type": "person", "company": company,
            "department": department, "role": role, "tags": ["인물"]}
    fm = build_frontmatter(meta)
    dv = ("```dataview\n"
          "TABLE date AS \"일시\", topic AS \"주제\", status AS \"상태\"\n"
          "FROM \"00_Meetings\"\n"
          f"WHERE contains(attendees, \"{name}\")\n"
          "SORT date DESC\n"
          "```")
    return (f"{fm}\n\n# {name}\n\n## 기본 정보\n\n"
            f"- **소속**: {company}\n- **부서**: {department}\n- **역할/직책**: {role}\n\n"
            f"---\n\n## 협업 이력\n\n{dv}\n\n---\n\n## 메모\n\n- \n")


def ensure_person(vault_root: str, name: str, role: str = "", department: str = "",
                  company: str = "", refs_subdir: str = "01_References") -> str:
    """인물 노트를 만들거나(없으면), 비어 있는 role/department/company 만 채운다.
    반환: 'created' | 'updated' | 'unchanged'."""
    if not name:
        return "unchanged"
    path = person_path(vault_root, name, refs_subdir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        open(path, "w", encoding="utf-8").write(
            _person_content(name, role, department, company))
        return "created"
    # 존재 → 비어 있는 필드만 보강
    content = open(path, encoding="utf-8").read()
    meta, body = parse_frontmatter(content)
    changed = False
    for key, val in (("role", role), ("department", department), ("company", company)):
        if val and not str(meta.get(key, "")).strip():
            meta[key] = val
            changed = True
    if not changed:
        return "unchanged"
    open(path, "w", encoding="utf-8").write(build_frontmatter(meta) + "\n" + body)
    return "updated"


def sync_from_list(vault_root: str, attendees: List[str], department: str = "",
                   company: str = "", refs_subdir: str = "01_References") -> Dict[str, str]:
    """['최민석(팀장)', ...] → 인물 노트 동기화. {name: 상태} 반환."""
    out = {}
    for raw in attendees or []:
        name, role = parse_attendee(raw)
        if name:
            out[name] = ensure_person(vault_root, name, role, department, company, refs_subdir)
    return out


def sync_from_meetings(vault_root: str, notes_subdir: str = "00_Meetings",
                       department: str = "", company: str = "",
                       refs_subdir: str = "01_References") -> Dict[str, str]:
    """모든 회의 노트의 attendees 를 모아 인물 노트 동기화."""
    root = os.path.join(vault_root, notes_subdir)
    seen: List[str] = []
    for p in glob.glob(os.path.join(root, "**", "*.md"), recursive=True):
        if os.path.basename(p).startswith("_"):
            continue
        try:
            meta, _ = parse_frontmatter(open(p, encoding="utf-8").read())
        except Exception:
            continue
        for a in _as_str_list(meta.get("attendees")):
            if a not in seen:
                seen.append(a)
    return sync_from_list(vault_root, seen, department, company, refs_subdir)


def main():
    import argparse
    ap = argparse.ArgumentParser(description="인물/팀 레지스트리")
    ap.add_argument("--vault", required=True)
    ap.add_argument("--notes-subdir", default="00_Meetings")
    ap.add_argument("--refs-subdir", default="01_References")
    ap.add_argument("--department", default="", help="기본 부서/팀")
    ap.add_argument("--company", default="", help="기본 회사")
    ap.add_argument("--add", default="", help="쉼표로 구분된 참석자 목록")
    ap.add_argument("--from-meetings", action="store_true", help="회의 노트에서 수집")
    a = ap.parse_args()
    if a.add:
        res = sync_from_list(a.vault, [x.strip() for x in a.add.split(",") if x.strip()],
                             a.department, a.company, a.refs_subdir)
    elif a.from_meetings:
        res = sync_from_meetings(a.vault, a.notes_subdir, a.department, a.company, a.refs_subdir)
    else:
        print("--add 또는 --from-meetings 중 하나를 지정하세요."); return
    for name, st in res.items():
        print(f"  {st:9s} {name}")
    print(f"\n[people] {len(res)}명 처리")


if __name__ == "__main__":
    main()
