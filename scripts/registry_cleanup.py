"""registry_cleanup.py — action/decision registry 일회성 정리 마이그레이션.

과거 _norm_key가 언더스코어를 제거하지 않아 생긴 중복
(예: source_meeting "260627_5" vs "260627 5")과 쓰기 정제 이전에
저장된 쓰레기 항목("--" 등)을 정리한다.

사용:
    python scripts/registry_cleanup.py            # dry-run (기본, 변경 없음)
    python scripts/registry_cleanup.py --apply    # .bak 백업 후 실제 적용
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from meeting_minutes_app.wiki_core.wiki_knowledge import (  # noqa: E402
    DATA_DIR,
    _atomic_write_json,
    _is_junk_registry_text,
    _norm_key,
)


def _clean_entries(
    entries: List[Dict[str, Any]], text_field: str
) -> Tuple[List[Dict[str, Any]], List[str], List[str]]:
    """junk 제거 + (norm_meeting, norm_text) 기준 중복 병합.

    반환: (정리된 목록, 제거된 junk 설명, 병합된 중복 설명)
    """
    kept: List[Dict[str, Any]] = []
    kept_by_key: Dict[Tuple[str, str], Dict[str, Any]] = {}
    junk_log: List[str] = []
    merged_log: List[str] = []

    for e in entries:
        if not isinstance(e, dict):
            junk_log.append(f"dict 아님: {e!r}")
            continue
        text = str(e.get(text_field, "")).strip()
        eid = e.get("action_id") or e.get("decision_id") or "?"
        if _is_junk_registry_text(text):
            junk_log.append(f"{eid}: {text_field}={text!r}")
            continue

        key = (_norm_key(str(e.get("source_meeting", ""))), _norm_key(text))
        prev = kept_by_key.get(key)
        if prev is None:
            kept.append(e)
            kept_by_key[key] = e
            continue

        # 중복 — 먼저 등록된 항목을 유지하되, 뒤 항목이 더 채워진 필드가 있으면 보완
        for f in ("source_note", "owner", "due_date", "context", "topics"):
            if not prev.get(f) and e.get(f):
                prev[f] = e[f]
        merged_log.append(
            f"{eid} → {prev.get('action_id') or prev.get('decision_id')} "
            f"(meeting {e.get('source_meeting')!r} ≡ {prev.get('source_meeting')!r})"
        )

    return kept, junk_log, merged_log


def _process(path: Path, list_key: str, text_field: str, apply: bool) -> None:
    print(f"\n=== {path.name} ===")
    if not path.exists():
        print("  파일 없음 — 건너뜀")
        return

    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get(list_key, [])
    cleaned, junk_log, merged_log = _clean_entries(entries, text_field)

    for line in junk_log:
        print(f"  [junk 제거] {line}")
    for line in merged_log:
        print(f"  [중복 병합] {line}")
    print(f"  {len(entries)}개 → {len(cleaned)}개 "
          f"(junk {len(junk_log)}, 병합 {len(merged_log)})")

    if not junk_log and not merged_log:
        print("  변경 없음")
        return

    if apply:
        backup = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, backup)
        data[list_key] = cleaned
        _atomic_write_json(path, data)
        print(f"  적용 완료 (백업: {backup.name})")
    else:
        print("  dry-run — 적용하려면 --apply")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help=".bak 백업 후 실제로 파일을 수정한다 (기본: dry-run)")
    ap.add_argument("--data-dir", type=Path, default=DATA_DIR,
                    help=f"registry 파일 위치 (기본: {DATA_DIR})")
    args = ap.parse_args()

    _process(args.data_dir / "action_registry.json", "actions", "title", args.apply)
    _process(args.data_dir / "decision_registry.json", "decisions", "summary", args.apply)


if __name__ == "__main__":
    main()
