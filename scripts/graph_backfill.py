"""
scripts/graph_backfill.py
Wiki Knowledge Graph 1회성 백필 — registry(action/decision) + Obsidian vault frontmatter를
그래프 DB(data/wiki_graph.db)로 채워 넣는다.

사용법:
    python scripts/graph_backfill.py               # 실제 반영
    python scripts/graph_backfill.py --dry-run      # 반영 없이 카운트만 미리보기
"""
import sys
import os
import argparse

# 저장소 루트 경로 추가 (meeting_minutes_app 패키지 임포트용)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from meeting_minutes_app.wiki_core import graph_sync  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Wiki Knowledge Graph 백필 (registry + vault)")
    ap.add_argument("--dry-run", action="store_true", help="반영 없이 예상 카운트만 출력")
    args = ap.parse_args()

    verb = "미리보기(dry-run)" if args.dry_run else "반영"
    print(f"[graph-backfill] Registry 백필 {verb} 시작...")
    reg_counts = graph_sync.backfill_from_registries(dry_run=args.dry_run)
    print(f"  - 노드 {'추가 예정' if args.dry_run else '추가'}: {reg_counts.get('nodes_would_add', 0)}개")
    print(f"  - 엣지 {'추가 예정' if args.dry_run else '추가'}: {reg_counts.get('edges_would_add', 0)}개")

    print(f"\n[graph-backfill] Vault 백필 {verb} 시작...")
    vault_counts = graph_sync.backfill_from_vault(dry_run=args.dry_run)
    print(f"  - 스캔한 노트 수: {vault_counts.get('notes_found', 0)}개")
    print(f"  - 노드 {'추가 예정' if args.dry_run else '추가'}: {vault_counts.get('nodes_would_add', 0)}개")
    print(f"  - 엣지 {'추가 예정' if args.dry_run else '추가'}: {vault_counts.get('edges_would_add', 0)}개")

    print("\n[graph-backfill] 완료.")
    if args.dry_run:
        print("(dry-run 이므로 실제 DB에는 반영되지 않았습니다. --dry-run 없이 다시 실행하세요.)")


if __name__ == "__main__":
    main()
