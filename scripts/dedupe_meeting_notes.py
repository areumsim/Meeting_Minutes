"""dedupe_meeting_notes.py — 같은 오디오로 두 번 이상 만들어진 회의록 정리.

같은 오디오를 재처리하면 회의록이 새로 하나 더 생긴다. `put_note()`는 덮어쓰기이므로
(obsidian_fs.py) 원인은 **파일 경로가 실행마다 달라지는 것**이고, 축이 넷이다:

  1. 날짜 폴백이 '오늘'      — 다른 날 재처리하면 파일명 접두가 달라진다
  2. 제목에 이미 날짜가 있는데 또 앞에 붙인다 (`260627 260627 5.md`)
  3. 저장 폴더가 실행마다 달라진다 — classify_meeting_route()가 LLM으로 폴더를 고른다
  4. title이 진입점마다 다르다 — 웹은 사용자 입력, 폴더감시는 stem, 배치는 stem 원형

1·2는 파일명 규칙 수정으로, 3·4는 발행 직전 재발행 차단으로 막는다. **이 스크립트는
그 수정 이전에 이미 만들어진 잔재**를 찾아 정리한다. 앞으로만 막으면 남은 중복이
영구히 남고, 다음 재처리 때 어느 쪽이 갱신될지 사람이 예측할 수 없다.

판정 키는 frontmatter `source_audio`(basename) + `session_date`다 — 2026-07-30 실측에서
중복 쌍 전부가 이 두 필드는 일치했다(달랐던 건 파일명·폴더·title 뿐).

사용:
    python scripts/dedupe_meeting_notes.py                 # dry-run (기본, 변경 없음)
    python scripts/dedupe_meeting_notes.py --apply         # 실제 정리
    python scripts/dedupe_meeting_notes.py --keep "경로"    # 특정 노트를 승자로 지정(반복 가능)

--apply 는 패자를 지우지 않고 `Archive/_dedupe/<원래 경로>` 로 **이동**한다(되돌릴 수 있게).
이동 후 인바운드 위키링크(`[[패자]]`·`[[패자#헤딩]]`·`[[패자|별칭]]`)를 승자로 치환하고,
승자의 `transcript_note` / 전사 노트의 `parent_note` 경로 문자열도 맞춘다.

정리 후에는 인덱스·그래프를 다시 맞춰야 한다:
    python run_meeting.py reindex
    python scripts/graph_backfill.py
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from meeting_minutes_app.common import config_loader  # noqa: E402
from meeting_minutes_app.wiki_core.obsidian import parse_frontmatter  # noqa: E402
from meeting_minutes_app.wiki_core.vault_indexer import iter_note_files  # noqa: E402

#: 패자를 옮겨 두는 폴더(볼트 안). 삭제 대신 이동이라 되돌릴 수 있다.
DEDUPE_DIR = "Archive/_dedupe"


# ── 노트 수집 ────────────────────────────────────────────────

class Note:
    __slots__ = ("rel", "abs", "meta", "body")

    def __init__(self, rel: str, abs_path: str, meta: Dict[str, Any], body: str):
        self.rel = rel
        self.abs = abs_path
        self.meta = meta
        self.body = body

    @property
    def base(self) -> str:
        """확장자 없는 파일명 — 위키링크가 참조하는 이름."""
        return Path(self.rel).stem

    @property
    def is_transcript(self) -> bool:
        return str(self.meta.get("type", "")).strip().strip('"') == "transcript"

    def m(self, key: str) -> str:
        return str(self.meta.get(key, "") or "").strip().strip('"')


def _vault_path() -> str:
    return (config_loader.get("indexing.vault_path")
            or config_loader.get("obsidian.vault_path", "") or "")


def _collect(vault: str) -> List[Note]:
    """판정은 iter_note_files() 한 곳 — 인덱서·graph_sync 와 같은 노트 집합을 본다."""
    out: List[Note] = []
    for fpath in iter_note_files(vault):
        try:
            content = open(fpath, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        meta, body = parse_frontmatter(content)
        rel = os.path.relpath(fpath, vault).replace("\\", "/")
        out.append(Note(rel, fpath, meta, body))
    return out


# ── 그룹핑 ──────────────────────────────────────────────────

def _group_key(n: Note) -> Optional[Tuple[str, str]]:
    """(source_audio basename, session_date). 둘 중 하나라도 없으면 판정하지 않는다."""
    audio = os.path.basename(n.m("source_audio"))
    date = n.m("session_date") or n.m("date")
    if not audio or not date:
        return None
    return (audio, date)


def find_duplicate_groups(notes: List[Note]) -> List[Tuple[Tuple[str, str], List[Note]]]:
    """회의록만 그룹핑한다(전사 노트는 부모를 따라 붙이므로 제외)."""
    groups: Dict[Tuple[str, str], List[Note]] = defaultdict(list)
    for n in notes:
        if n.is_transcript:
            continue
        key = _group_key(n)
        if key:
            groups[key].append(n)
    return sorted(((k, v) for k, v in groups.items() if len(v) > 1),
                  key=lambda kv: (kv[0][1], kv[0][0]))


def transcripts_of(note: Note, notes: List[Note]) -> List[Note]:
    """이 회의록에 딸린 전사 노트. parent_note 경로 일치가 1순위,
    없으면 `<base> - 전사` 파일명 규칙으로 보완한다(구버전 노트 대비)."""
    out = []
    for t in notes:
        if not t.is_transcript:
            continue
        if t.m("parent_note") == note.rel or t.base == f"{note.base} - 전사":
            out.append(t)
    return out


# ── 승자 선정 ────────────────────────────────────────────────

_DUP_PREFIX = re.compile(r"^(\d{6})\s+(?=\d{2}[-_. ]?\d{2}[-_. ]?\d{2}|\d{4}[-_. 년])")


def _has_duplicated_date_prefix(base: str) -> bool:
    """`260627 260627 5` 처럼 YYMMDD 접두 뒤에 또 날짜가 오는 파일명인가."""
    return bool(_DUP_PREFIX.match(base))


def pick_winner(cands: List[Note]) -> Tuple[Note, str]:
    """승자 + 사유. 기준 순서:
      (a) 파일명에 날짜 접두가 중복되지 않음  — 새 파일명 규칙과 일치하는 쪽
      (b) review_status != pending           — 사람이 검토한 노트를 보존
      (c) 본문 길이가 김                      — 더 완전한 산출물
      (d) processed_at 이 최신
    """
    def sort_key(n: Note):
        return (
            _has_duplicated_date_prefix(n.base),          # False(중복 아님) 우선
            n.m("review_status").lower() == "pending",    # False(검토됨) 우선
            -len(n.body),
            _neg_str(n.m("processed_at") or n.m("created")),
        )

    ranked = sorted(cands, key=sort_key)
    w = ranked[0]
    reasons = []
    if not _has_duplicated_date_prefix(w.base) and any(
            _has_duplicated_date_prefix(o.base) for o in cands if o is not w):
        reasons.append("파일명 날짜 접두 중복 없음")
    if w.m("review_status").lower() not in ("", "pending"):
        reasons.append(f"review_status={w.m('review_status')}")
    if len(w.body) == max(len(o.body) for o in cands):
        reasons.append(f"본문 최장({len(w.body)}자)")
    if not reasons:
        reasons.append("processed_at 최신")
    return w, " · ".join(reasons)


class _neg_str:
    """문자열 내림차순 정렬용 래퍼(튜플 안에서 -x 를 쓸 수 없어서)."""
    __slots__ = ("s",)

    def __init__(self, s: str):
        self.s = s

    def __lt__(self, other: "_neg_str") -> bool:
        return self.s > other.s

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _neg_str) and self.s == other.s


# ── 적용 ────────────────────────────────────────────────────

def _wikilink_pattern(base: str) -> re.Pattern:
    """`[[base]]` · `[[base#헤딩]]` · `[[base|별칭]]` 을 모두 잡는다."""
    return re.compile(r"\[\[" + re.escape(base) + r"(?=[\]|#])")


def rewrite_links(vault: str, notes: List[Note], loser_base: str, winner_base: str) -> int:
    """볼트 전체에서 패자를 가리키는 위키링크를 승자로 치환. 바뀐 파일 수 반환."""
    if loser_base == winner_base:
        return 0
    pat = _wikilink_pattern(loser_base)
    changed = 0
    for n in notes:
        try:
            content = open(n.abs, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        new = pat.sub("[[" + winner_base, content)
        if new != content:
            with open(n.abs, "w", encoding="utf-8") as f:
                f.write(new)
            changed += 1
    return changed


def _replace_meta_path(abs_path: str, key: str, old: str, new: str) -> bool:
    """frontmatter 의 경로 문자열 필드(transcript_note/parent_note) 한 줄을 교체."""
    try:
        content = open(abs_path, encoding="utf-8", errors="replace").read()
    except OSError:
        return False
    pat = re.compile(r'^(' + re.escape(key) + r':\s*)"?' + re.escape(old) + r'"?\s*$',
                     re.MULTILINE)
    new_content, cnt = pat.subn(lambda m: f'{m.group(1)}"{new}"', content)
    if not cnt:
        return False
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(new_content)
    return True


def move_to_dedupe(vault: str, note: Note) -> str:
    """패자를 Archive/_dedupe/<원래 경로>로 이동. 이동 후 볼트 상대경로 반환."""
    dest_rel = f"{DEDUPE_DIR}/{note.rel}"
    dest = Path(vault) / dest_rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():                      # 재실행 대비 — 덮어쓰지 않는다
        i = 2
        while (dest.parent / f"{dest.stem} ({i}){dest.suffix}").exists():
            i += 1
        dest = dest.parent / f"{dest.stem} ({i}){dest.suffix}"
        dest_rel = os.path.relpath(dest, vault).replace("\\", "/")
    shutil.move(note.abs, str(dest))
    return dest_rel


# ── 리포트 / main ───────────────────────────────────────────

def _use_utf8_stdout() -> None:
    """Windows 기본 콘솔은 cp949라 '—'·'✔' 같은 문자에서 죽는다.
    출력만 UTF-8로 올린다(실패하면 조용히 넘어가고, 아래 출력은 어차피 ASCII 기호만 쓴다)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


def _report_group(key: Tuple[str, str], cands: List[Note],
                  winner: Note, reason: str, notes: List[Note]) -> None:
    audio, date = key
    print(f"\n[그룹] {audio}  ({date})  - 사본 {len(cands)}개")
    for n in sorted(cands, key=lambda x: x.rel):
        mark = "* 승자" if n is winner else "  패자"
        flags = []
        if _has_duplicated_date_prefix(n.base):
            flags.append("날짜접두중복")
        if n.m("review_status") and n.m("review_status").lower() != "pending":
            flags.append(f"review={n.m('review_status')}")
        ts = transcripts_of(n, notes)
        if ts:
            flags.append(f"전사 {len(ts)}건")
        tail = f"  [{', '.join(flags)}]" if flags else ""
        print(f"   {mark}  {n.rel}")
        print(f"           본문 {len(n.body):>6,}자 · processed_at {n.m('processed_at') or '?'}{tail}")
    print(f"   → 승자 사유: {reason}")

    # 승자가 가장 길지 않으면 사람이 판단할 여지가 있다 — 조용히 넘기지 않는다.
    # (파일명이 멀쩡한 쪽 = LLM이 제목·폴더를 제대로 고른 실행이지만, 나중에 재처리된
    #  쪽이 더 완전한 회의록일 수 있다. 둘 중 무엇을 남길지는 내용을 봐야 안다.)
    longest = max(cands, key=lambda x: len(x.body))
    if longest is not winner:
        print(f"   ! 주의: 패자 쪽이 {len(longest.body) - len(winner.body):,}자 더 깁니다"
              f" ({longest.rel}).")
        print(f"           내용을 확인하고 그쪽을 남기려면:"
              f' --keep "{longest.rel}"')


def main() -> int:
    ap = argparse.ArgumentParser(
        description="같은 오디오로 중복 생성된 회의록을 찾아 정리한다(기본 dry-run).")
    ap.add_argument("--apply", action="store_true",
                    help="실제로 정리한다(패자를 Archive/_dedupe/ 로 이동 + 링크 치환). "
                         "지정하지 않으면 리포트만 출력하고 볼트는 건드리지 않는다")
    ap.add_argument("--keep", action="append", default=[], metavar="경로",
                    help="이 볼트 상대경로 노트를 해당 그룹의 승자로 강제 지정(반복 가능)")
    args = ap.parse_args()
    _use_utf8_stdout()

    vault = _vault_path()
    if not vault or not os.path.isdir(vault):
        print(f"[dedupe] 볼트 경로를 찾을 수 없습니다: {vault!r}")
        print("         config.json 의 indexing.vault_path 또는 obsidian.vault_path 를 확인하세요.")
        return 1

    notes = _collect(vault)
    groups = find_duplicate_groups(notes)
    keep = {k.replace("\\", "/") for k in args.keep}

    print(f"[dedupe] 볼트: {vault}")
    print(f"[dedupe] 노트 {len(notes)}개 스캔 · 중복 그룹 {len(groups)}개")
    if not groups:
        print("[dedupe] 정리할 중복이 없습니다.")
        return 0

    plans: List[Tuple[Tuple[str, str], Note, List[Note], str]] = []
    for key, cands in groups:
        forced = [n for n in cands if n.rel in keep]
        if forced:
            winner, reason = forced[0], "--keep 로 지정됨"
        else:
            winner, reason = pick_winner(cands)
        _report_group(key, cands, winner, reason, notes)
        plans.append((key, winner, [n for n in cands if n is not winner], reason))

    total_losers = sum(len(p[2]) for p in plans)
    print(f"\n[dedupe] 패자 {total_losers}개 (+ 딸린 전사 노트)")

    if not args.apply:
        print("\n(dry-run 이므로 볼트는 변경되지 않았습니다.)")
        print("실제로 정리하려면: python scripts/dedupe_meeting_notes.py --apply")
        print("승자를 직접 고르려면: --keep \"00_Meetings/.../원하는 노트.md\"")
        return 0

    moved = 0
    relinked = 0
    for key, winner, losers, _reason in plans:
        for loser in losers:
            for t in transcripts_of(loser, notes):
                dest = move_to_dedupe(vault, t)
                print(f"  이동(전사) {t.rel}  →  {dest}")
                moved += 1
            dest = move_to_dedupe(vault, loser)
            print(f"  이동       {loser.rel}  →  {dest}")
            moved += 1
            # 이동된 노트는 링크 치환 대상에서 빼고(자기 자신 참조 방지) 나머지를 고친다
            remaining = [n for n in notes if os.path.exists(n.abs)]
            relinked += rewrite_links(vault, remaining, loser.base, winner.base)

        # 승자의 전사 연결 정합 — 승자 노트가 가리키는 전사가 살아 있는지 확인만 하고,
        # 어긋나면 실제 파일 경로로 맞춘다(전사는 승자 것만 남아 있다).
        wts = transcripts_of(winner, [n for n in notes if os.path.exists(n.abs)])
        if wts and winner.m("transcript_note") != wts[0].rel:
            if _replace_meta_path(winner.abs, "transcript_note",
                                  winner.m("transcript_note"), wts[0].rel):
                print(f"  경로수정   {winner.rel}: transcript_note → {wts[0].rel}")
        for t in wts:
            if t.m("parent_note") != winner.rel:
                if _replace_meta_path(t.abs, "parent_note", t.m("parent_note"), winner.rel):
                    print(f"  경로수정   {t.rel}: parent_note → {winner.rel}")

    print(f"\n[dedupe] 완료 — {moved}개 이동, {relinked}개 파일의 위키링크 치환.")
    print("다음을 실행해 인덱스·그래프를 맞추세요:")
    print("    python run_meeting.py reindex")
    print("    python scripts/graph_backfill.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
