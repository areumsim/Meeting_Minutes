#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""measure_retrieval_floor.py — 관련 노트 회수 문턱을 **실측으로** 정하기 위한 도구.

왜 필요한가
-----------
`wiki_knowledge.embedding_min_cosine`(기본 0.25)과 `vault_retrieval` 의
`score >= 0.05` 는 근거 없이 정해진 상수였다. 이 값들이 노이즈 문턱보다 낮으면
"의미상 무관한 노트"가 회의록의 '🔗 관련 노트'와 frontmatter evidence 에 근거로
올라간다 — 사용자가 체감한 "관련 노트를 과대해석해서 찾는다"의 정체다.

무엇을 재는가 (라벨 없이 문턱을 정하는 방법)
--------------------------------------------
정답 라벨이 없으므로 **귀무분포(null distribution)** 를 측정한다:

  무작위로 고른 노트 쌍의 코사인 분포 = "아무 관계 없는 두 문서의 점수"

이 분포의 상위 백분위가 문턱의 하한선이다. 예컨대 무작위 쌍의 50%가 0.25를 넘는다면
`min_cosine=0.25`는 무관한 노트의 절반을 통과시키는 값이므로 문턱이 아니다.

같은 방식으로 대조군을 둔다:
  - **같은 폴더** 쌍 = 약한 양성(같은 주제 영역에 있으니 관련 가능성이 높다)
  - 무작위 쌍       = 음성

두 분포가 겹치는 지점이 아니라, **음성 분포의 상위 꼬리**(p95/p99)를 문턱으로 쓴다 —
관련 노트는 없어도 되지만 무관한 노트가 근거로 올라가면 회의록이 거짓말을 한다.
비대칭 손실이므로 정밀도(precision) 쪽으로 치우친 문턱이 맞다.

한계 (반드시 함께 읽을 것)
--------------------------
- **양성 라벨이 없다.** '같은 폴더'는 관련성의 대리 지표일 뿐이다. 폴더가 주제별로
  정리돼 있지 않은 볼트에서는 이 대조군이 의미를 잃는다(스크립트가 폴더별 노트 수를
  함께 출력하므로 확인할 수 있다).
- 여기서 재는 것은 **노트↔노트** 유사도다. 실제 회수는 **회의 전사↔노트**다. 전사는
  구어체·STT 오류가 섞여 분포가 다를 수 있다. `--query-file` 로 실제 전사를 주면
  그 쿼리에 대한 상위 점수 분포도 함께 출력한다.
- 임베딩은 기존 인덱스(`data/vault_index.emb.json`)를 그대로 읽는다 — API 호출·과금이
  없다. 따라서 인덱스가 만들어진 시점의 모델·차원(현재 text-embedding-3-small/256)에
  대한 측정이다. 모델이나 차원을 바꾸면 다시 재야 한다.

사용법
------
    python scripts/measure_retrieval_floor.py
    python scripts/measure_retrieval_floor.py --pairs 20000 --query-file output/xxx_transcript.md
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _pct(sorted_vals: Sequence[float], q: float) -> float:
    """0~100 백분위(선형 보간 없이 최근접 인덱스)."""
    if not sorted_vals:
        return float("nan")
    i = min(len(sorted_vals) - 1, max(0, int(round(q / 100.0 * (len(sorted_vals) - 1)))))
    return sorted_vals[i]


def _describe(name: str, vals: List[float]) -> Dict[str, float]:
    v = sorted(vals)
    n = len(v)
    stats = {
        "n": n,
        "mean": sum(v) / n if n else float("nan"),
        "p50": _pct(v, 50), "p75": _pct(v, 75), "p90": _pct(v, 90),
        "p95": _pct(v, 95), "p99": _pct(v, 99),
        "max": v[-1] if n else float("nan"),
    }
    print(f"  {name:<22} n={n:<7} mean={stats['mean']:.3f}  "
          f"p50={stats['p50']:.3f}  p75={stats['p75']:.3f}  p90={stats['p90']:.3f}  "
          f"p95={stats['p95']:.3f}  p99={stats['p99']:.3f}  max={stats['max']:.3f}")
    return stats


def _cos(a: Sequence[float], b: Sequence[float]) -> float:
    """인덱스 벡터는 저장 시점에 L2 정규화돼 있으므로 내적 = 코사인.
    (반올림 5자리 저장으로 노름이 1에서 미세하게 벗어나지만 3자리 통계에는 영향 없다.)"""
    return sum(x * y for x, y in zip(a, b))


def load_embeddings(emb_path: Path) -> Tuple[Dict[str, List[float]], str, int]:
    with emb_path.open(encoding="utf-8") as f:
        payload = json.load(f)
    notes = payload.get("notes") or {}
    vecs = {rel: e["v"] for rel, e in notes.items() if e.get("v")}
    return vecs, str(payload.get("model", "?")), int(payload.get("dims", 0) or 0)


def folder_of(rel: str) -> str:
    p = rel.replace("\\", "/")
    return p.rsplit("/", 1)[0] if "/" in p else "(root)"


def sample_pairs(keys: List[str], count: int, rng: random.Random,
                 same_folder: Optional[bool] = None) -> List[Tuple[str, str]]:
    """무작위 쌍 표본. same_folder=True/False 면 그 조건을 만족하는 쌍만 모은다."""
    out: List[Tuple[str, str]] = []
    if len(keys) < 2:
        return out
    # 조건부 표본은 거절 표집이라 시도 횟수에 상한을 둔다(조건을 만족하는 쌍이
    # 희소한 볼트에서 무한 루프가 되지 않게).
    attempts = 0
    max_attempts = count * 200 + 10_000
    while len(out) < count and attempts < max_attempts:
        attempts += 1
        a, b = rng.sample(keys, 2)
        if same_folder is not None and (folder_of(a) == folder_of(b)) != same_folder:
            continue
        out.append((a, b))
    return out


def measure_query(vecs: Dict[str, List[float]], query_vec: Sequence[float],
                  top: int = 20) -> List[Tuple[str, float]]:
    scored = [(rel, _cos(query_vec, v)) for rel, v in vecs.items()]
    scored.sort(key=lambda x: -x[1])
    return scored[:top]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--emb", default="data/vault_index.emb.json",
                    help="임베딩 인덱스 경로 (기본: data/vault_index.emb.json)")
    ap.add_argument("--pairs", type=int, default=20000, help="쌍 표본 수 (기본 20000)")
    ap.add_argument("--seed", type=int, default=20260731, help="재현용 시드")
    ap.add_argument("--query-file", default="",
                    help="실제 전사/회의록 파일 — 이 텍스트에 대한 상위 회수 점수 분포도 출력"
                         " (임베딩 API 1회 호출·약 $0.000001)")
    ap.add_argument("--top", type=int, default=20, help="--query-file 상위 몇 건을 보일지")
    args = ap.parse_args()

    emb_path = Path(args.emb)
    if not emb_path.is_file():
        print(f"[!] 임베딩 인덱스가 없습니다: {emb_path}\n"
              f"    python run_meeting.py reindex 로 먼저 만드세요.")
        return 2

    vecs, model, dims = load_embeddings(emb_path)
    if len(vecs) < 20:
        print(f"[!] 임베딩 노트가 {len(vecs)}개뿐 — 통계가 의미 없습니다.")
        return 2

    keys = sorted(vecs)
    rng = random.Random(args.seed)

    print(f"\n=== 임베딩 인덱스 ===")
    print(f"  모델 {model} · {dims}차원 · 노트 {len(vecs)}개 · seed={args.seed}")

    folders = Counter(folder_of(k) for k in keys)
    multi = {f: c for f, c in folders.items() if c >= 2}
    print(f"  폴더 {len(folders)}개 (노트 2개 이상인 폴더 {len(multi)}개)")
    print("  상위 폴더: " + ", ".join(
        f"{f}({c})" for f, c in folders.most_common(6)))

    print(f"\n=== 코사인 분포 ===")
    print("  ※ '무작위 쌍'이 음성(관계 없음) 대조군, '같은 폴더 쌍'이 약한 양성 대조군.")
    rand_pairs = sample_pairs(keys, args.pairs, rng, same_folder=None)
    rand_vals = [_cos(vecs[a], vecs[b]) for a, b in rand_pairs]
    rand = _describe("무작위 쌍(음성)", rand_vals)

    same_vals: List[float] = []
    if len(multi) >= 2:
        same_pairs = sample_pairs(keys, min(args.pairs, 5000), rng, same_folder=True)
        same_vals = [_cos(vecs[a], vecs[b]) for a, b in same_pairs]
        if same_vals:
            _describe("같은 폴더 쌍(약양성)", same_vals)
    else:
        print("  같은 폴더 쌍       (폴더가 주제별로 나뉘어 있지 않아 생략)")

    print(f"\n=== 현재 설정값이 통과시키는 비율 ===")
    try:
        from meeting_minutes_app.common import config_loader as cfg
        cur_cos = float(cfg.get("wiki_knowledge.embedding_min_cosine", 0.25) or 0.25)
    except Exception:
        cur_cos = 0.25
    for th in sorted({cur_cos, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50}):
        share = sum(1 for v in rand_vals if v >= th) / len(rand_vals) * 100
        pos = (sum(1 for v in same_vals if v >= th) / len(same_vals) * 100
               if same_vals else float("nan"))
        mark = "  ← 현재 설정" if abs(th - cur_cos) < 1e-9 else ""
        pos_s = f"{pos:5.1f}%" if same_vals else "   n/a"
        print(f"  min_cosine={th:.2f} → 무작위 쌍 통과 {share:5.1f}% · "
              f"같은 폴더 쌍 통과 {pos_s}{mark}")

    print(f"\n=== 권고 문턱 (음성 분포 상위 꼬리) ===")
    print(f"  p95 = {rand['p95']:.3f}   (무관 노트 20건 중 1건만 통과)")
    print(f"  p99 = {rand['p99']:.3f}   (무관 노트 100건 중 1건만 통과)")
    print("  → 회의록 근거로 쓰이는 값이므로 정밀도 우선(p99 쪽)을 권한다.")
    print("     관련 노트가 아예 안 나오는 회의가 늘어나는 것은 허용 가능한 손실이다 —")
    print("     '없음'은 정직하지만 무관한 근거는 회의록을 틀리게 만든다.")

    # ── 진짜 양성 라벨: `X - 전사.md` ↔ `X.md` ──────────────────────────
    # 전사 노트와 그 부모 회의록은 **같은 회의**다. 라벨을 사람이 붙일 필요 없이
    # 볼트 구조가 보장하는 유일한 양성 쌍이고, 쿼리 쪽이 실제 회의 전사라서
    # '전사↔노트' 회수를 그대로 재현한다(노트↔노트 대리 측정이 아니다).
    pairs: List[Tuple[str, str]] = []
    for rel in keys:
        if not rel.endswith(" - 전사.md"):
            continue
        parent = rel[: -len(" - 전사.md")] + ".md"
        if parent in vecs:
            pairs.append((rel, parent))

    if pairs:
        print(f"\n=== 진짜 양성 쌍: 전사 ↔ 그 회의록 ({len(pairs)}쌍) ===")
        true_vals = [_cos(vecs[t], vecs[p]) for t, p in pairs]
        _describe("전사↔부모(양성)", true_vals)

        # 각 전사를 쿼리로 전체 랭킹 → 부모가 몇 위인가 + 상위 점수의 상대적 위치
        ranks: List[int] = []
        top1_gap: List[float] = []
        parent_z: List[float] = []
        for t, p in pairs:
            scored = [(rel, _cos(vecs[t], v)) for rel, v in vecs.items() if rel != t]
            scored.sort(key=lambda x: -x[1])
            vals = [s for _, s in scored]
            mean = sum(vals) / len(vals)
            var = sum((s - mean) ** 2 for s in vals) / len(vals)
            sd = math.sqrt(var) or 1e-9
            pos = next(i for i, (rel, _) in enumerate(scored, 1) if rel == p)
            ranks.append(pos)
            top1_gap.append(vals[0] - mean)
            parent_z.append((dict(scored)[p] - mean) / sd)
        ranks_sorted = sorted(ranks)
        hit1 = sum(1 for r in ranks if r == 1) / len(ranks) * 100
        hit5 = sum(1 for r in ranks if r <= 5) / len(ranks) * 100
        hit10 = sum(1 for r in ranks if r <= 10) / len(ranks) * 100
        print(f"  부모 회의록의 순위: top1 {hit1:.0f}% · top5 {hit5:.0f}% · top10 {hit10:.0f}%"
              f" · 중위 {_pct(ranks_sorted, 50):.0f}위 · 최악 {ranks_sorted[-1]}위")
        _describe("부모의 z점수(양성)", parent_z)
        print("  ※ z = (그 쿼리 안에서의 점수 - 쿼리별 평균) / 표준편차.")
        print("    절대 코사인이 아니라 **쿼리마다 다시 계산한 상대 위치**다.")

        # 절대 문턱 vs 순위/z 문턱 — 어느 쪽이 양성/음성을 가르나
        print(f"\n=== 절대 코사인 문턱은 양성·음성을 가르는가 ===")
        for th in (0.25, 0.35, 0.45, 0.55, rand["p95"], rand["p99"]):
            tp = sum(1 for v in true_vals if v >= th) / len(true_vals) * 100
            fp = sum(1 for v in rand_vals if v >= th) / len(rand_vals) * 100
            print(f"  cos>={th:.3f} → 양성 유지 {tp:5.1f}% · 무관 통과 {fp:5.1f}%")
        print(f"\n=== z 문턱(쿼리별 상대 위치)은 어떤가 ===")
        z_sorted = sorted(parent_z)
        for zt in (1.0, 1.5, 2.0, 2.5, 3.0):
            keep = sum(1 for z in z_sorted if z >= zt) / len(z_sorted) * 100
            # 정규분포 가정 시 상위 비율 — 무관 노트가 통과할 대략적 상한
            approx_fp = (1 - 0.5 * (1 + math.erf(zt / math.sqrt(2)))) * 100
            print(f"  z>={zt:.1f} → 양성 유지 {keep:5.1f}% · 무관 통과 상한 약 {approx_fp:4.1f}%")

    # ── 중복 노트 점검 (cos≈1.0) ──────────────────────────────────────
    near_dup = [(a, b, v) for (a, b), v in zip(rand_pairs, rand_vals) if v >= 0.99]
    if near_dup:
        print(f"\n=== 사실상 동일한 노트 (무작위 표본에서 cos>=0.99) ===")
        print(f"  {len(near_dup)}쌍 발견 — 내용이 같은 노트가 여러 경로에 있으면")
        print(f"  '관련 노트' 목록에 같은 내용이 두 줄로 올라간다.")
        for a, b, v in near_dup[:5]:
            print(f"  {v:.4f}  {a}\n          {b}")

    if args.query_file:
        qp = Path(args.query_file)
        if not qp.is_file():
            print(f"\n[!] --query-file 없음: {qp}")
            return 1
        text = qp.read_text(encoding="utf-8", errors="replace")
        print(f"\n=== 실제 쿼리 회수 (전사↔노트) ===")
        print(f"  파일 {qp.name} · {len(text)}자")
        try:
            from meeting_minutes_app.wiki_core.vault_indexer import VaultIndexer
            idx = VaultIndexer.from_config()
            if idx is None:
                print("  [!] 인덱서를 만들 수 없습니다(노트 폴더 미설정).")
                return 1
            max_chars = 4000
            qv = idx._embed_texts([text[:max_chars]])
            if not qv or not qv[0]:
                print("  [!] 쿼리 임베딩 실패(API 키/네트워크 확인).")
                return 1
            for i, (rel, s) in enumerate(measure_query(vecs, qv[0], args.top), 1):
                flag = "  " if s >= rand["p99"] else " ←p99 미달"
                print(f"  {i:2}. {s:.3f}{flag} {rel}")
        except Exception as e:
            print(f"  [!] 측정 실패: {type(e).__name__}: {e}")
            return 1

    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
