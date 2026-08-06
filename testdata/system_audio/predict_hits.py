"""대본 문장별로 '이번 녹음에서 무엇이 떠야 정상인가'를 미리 뽑는다.

왜 필요한가 — "관련 노트가 안 뜬다" 를 신고할 때, 그것이 **결함인지 정상 동작인지**
구분할 수 없으면 시간을 버린다. 실시간 관련 노트는 내용 게이트
(`wiki.realtime_min_terms`, 기본 3)를 통과한 발화에만 도는데, 인사말·짧은 문장은
설계상 통과하지 못한다. 이 스크립트는 녹음 전에 그 판정을 그대로 돌려 보여준다.

출력은 **이 PC 의 볼트 기준**이라 사람마다 다르다. 그래서 결과를 문서에 적어 두지
않는다(내부 노트 제목이 공개 리포에 남지도 않는다) — 필요할 때 여기서 뽑는다.

사용:
    python testdata/system_audio/predict_hits.py
    python testdata/system_audio/predict_hits.py --topic "양자컴퓨터 도입 검토"
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

SCRIPT = Path(__file__).with_name("회의_테스트_대본.txt")


def body_lines() -> list:
    """대본에서 낭독 구간만 — 음원 생성 스크립트와 같은 구분선을 본다."""
    lines = SCRIPT.read_text(encoding="utf-8").splitlines()
    try:
        i0 = next(i for i, l in enumerate(lines) if "여기서부터 낭독" in l)
        i1 = next(i for i, l in enumerate(lines) if "낭독 끝" in l)
    except StopIteration:
        raise SystemExit("대본에서 낭독 구간 표시를 찾지 못했습니다.")
    return [l.strip() for l in lines[i0 + 1:i1] if l.strip()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", default="양자컴퓨터 도입 검토",
                    help="녹음 화면의 '주제' 칸에 넣을 값과 같게 두면 예측이 정확하다")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    from meeting_minutes_app.common import config_loader as cfg
    from meeting_minutes_app.wiki_core.realtime_search import RealtimeVaultSearcher
    from meeting_minutes_app.wiki_core.vault_indexer import VaultIndexer

    min_terms = int(cfg.get("wiki.realtime_min_terms", 3) or 0)
    idx = VaultIndexer.from_config()
    if idx is None or not idx.load():
        print("검색 인덱스를 열 수 없습니다 — [설정]에서 노트 폴더 지정 후 "
              "'검색 인덱스·그래프 재빌드'를 먼저 실행하세요.")
        print("(이 상태로 녹음하면 관련 노트 바에 '검색 꺼짐 — 검색 인덱스가 없습니다'가 뜹니다.)")
        return 1

    searcher = RealtimeVaultSearcher(topic=args.topic)
    searcher._lazy_init()
    print(f"볼트 노트 {len(idx._notes)}건 · 내용 게이트 min_terms={min_terms} · "
          f"주제='{args.topic}'")
    print("-" * 78)
    print(f"  {'게이트':^8} {'일치어':>4}  문장 / 뜰 것으로 예상되는 노트")
    print("-" * 78)
    for text in body_lines():
        n = idx.known_term_count(text)
        passed = n >= min_terms
        hits = searcher.search_now(text, limit=2) if passed else []
        titles = " · ".join(h["title"][:30] for h in hits) or "—"
        print(f"  {'통과' if passed else '차단':^8} {n:>4}  {text}")
        print(f"  {'':^8} {'':>4}  → {titles}")
    searcher.shutdown()
    print("-" * 78)
    print("'차단' 은 정상이다 — 인사말·짧은 문장으로 유료 검색을 쏘지 않기 위한 게이트다")
    print("(wiki.realtime_min_terms). '통과' 문장에서 노트가 하나도 안 뜨면 그때가 결함이다.")

    # ── 이전 회의 대조 재료 ────────────────────────────────
    # 이 경로는 registry 에 걸리는 게 없으면 **조용히** 아무 일도 하지 않는다. 그래서
    # 녹음 전에 재료가 있는지 세어 두지 않으면, 카드가 안 뜬 이유가 '없어서'인지
    # '고장'인지 알 수 없다.
    from meeting_minutes_app.wiki_core import wiki_knowledge as wk
    decisions = wk.recent_decisions_for(args.topic, limit=5)
    actions = wk.open_actions_for(args.topic, limit=5)
    print()
    print(f"이전 회의 대조 재료 — 지난 결정 {len(decisions)}건 / 미완료 액션 {len(actions)}건")
    for d in decisions[:3]:
        print(f"   · 결정: {str(d.get('summary', ''))[:60]}")
    for a in actions[:3]:
        print(f"   · 액션: {str(a.get('title', ''))[:50]} ({a.get('owner') or '담당 미상'})")
    if not decisions and not actions:
        print("   → 0건. 주제를 registry 에 실제로 있는 말로 바꾸면 이 경로도 볼 수 있다.")

    # ── 이 설정으로 무엇이 안 뜨는가 ───────────────────────
    fac_on = bool(cfg.get("facilitation.enabled", False))
    web_on = bool(cfg.get("wiki.online_search_enabled", False))
    web_iv = int(cfg.get("wiki.realtime_web_search_interval", 0) or 0)
    vault_first = bool(cfg.get("wiki.realtime_web_only_if_no_vault_hit", True))
    print()
    print("설정 진단 — 이 상태로 녹음하면:")
    print(f"   회의진행 페르소나: {'켜짐' if fac_on else '꺼짐 → 카드·이전 회의 대조 카드가 안 뜬다'}"
          f"  (facilitation.enabled)")
    if web_on and web_iv > 0:
        print(f"   웹 보완 검색: 켜짐 — {web_iv}번째 발화마다 시도"
              + ("하되, 내부 노트가 잡힌 구간은 건너뛴다(볼트 우선)" if vault_first else ""))
        if vault_first:
            print("      → 볼트가 잘 맞는 회의에서는 🌐 배지가 거의 안 뜬다(정상). 굳이 보려면"
                  " wiki.realtime_web_only_if_no_vault_hit=false, 1회 약 $0.077.")
    else:
        print("   웹 보완 검색: 꺼짐 → 🌐 배지가 안 뜬다"
              " (wiki.online_search_enabled / realtime_web_search_interval)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
