#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""실시간 관련노트 랭킹 실측 벤치마크 (재현용).

`docs/검색랭킹_이론과근거.md` 의 수치를 만든 스크립트다. 랭킹 구조·상수를 바꿀 때
"느낌"이 아니라 이 숫자로 판단하기 위해 리포에 둔다.

평가 방법
---------
정답 라벨이 없으므로 **합성 쿼리**를 쓴다: 볼트 노트의 특정 섹션 본문에서 발화 길이만큼
잘라 쿼리로 쓰고, 정답은 (그 노트, 그 섹션 heading) 쌍이다.
  - recall@1 / recall@3(화면 표시 개수) / MRR@10  … 노트 회수 품질
  - heading@3                                   … 회수된 노트의 근거 섹션이 맞았나
  - ms                                          … 쿼리당 지연(중앙값)
한계: 쿼리가 노트 본문에서 나왔으므로 어휘 겹침이 실제 발화보다 크다 → 절대 수치는
낙관적이다. 이 측정의 목적은 **변이 간 상대 비교**이며, 정답을 1건으로 가정하므로
"여러 노트가 모두 관련" 인 경우의 품질은 재지 않는다.

실행
----
    python scripts/bench_realtime_ranking.py            # 전체
    python scripts/bench_realtime_ranking.py --n 40     # 쿼리 수 조정
    python scripts/bench_realtime_ranking.py --no-embed # 임베딩 API 호출 없이(무료)

비용: 쿼리 1건당 OpenAI 임베딩 1회(text-embedding-3-small, 약 100토큰) — 24건이면
$0.001 미만. `--no-embed` 는 TF-IDF 만 쓴다.
사전 조건: `python run_meeting.py reindex` 로 인덱스(+임베딩)가 빌드돼 있어야 한다.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import random
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from meeting_minutes_app.wiki_core import realtime_search as rs   # noqa: E402
from meeting_minutes_app.wiki_core import vault_indexer as vi     # noqa: E402

RRF_K = vi.RRF_K          # 상수 정본은 vault_indexer (실시간 경로도 같은 값을 쓴다)
PAPER_MATCH = rs._PAPER_PATH_MATCH   # 논문 폴더 매칭 규칙도 구현과 공유
SEED = 20260729
DISPLAY_N = 3
_HANGUL = re.compile(r"[가-힣]")
_LATIN = re.compile(r"[A-Za-z]")


# ── 유틸 ──────────────────────────────────────────────────
def emb_toggle(on: bool):
    """vault_indexer._c 를 감싸 임베딩 게이트만 바꾼다. 반환값을 vi._c 에 복원할 것."""
    orig = vi._c

    def patched(key, default=None):
        if key == "wiki_knowledge.embedding_enabled":
            return on
        return orig(key, default)
    vi._c = patched
    return orig


def paper_dirs() -> Tuple[str, ...]:
    return rs._paper_dirs()


def is_paper(rel: str) -> bool:
    return rs._is_paper_path(rel, paper_dirs())


def clean(txt: str) -> str:
    txt = re.sub(r"^#{1,6}\s+.*$", " ", txt, flags=re.MULTILINE)
    txt = re.sub(r"```.*?```", " ", txt, flags=re.DOTALL)
    txt = re.sub(r"\[\[([^\]|]+)(\|[^\]]+)?\]\]", r"\1", txt)
    txt = re.sub(r"[#>*\-|`]", " ", txt)
    return " ".join(txt.split())


# ── 쿼리셋 ────────────────────────────────────────────────
def build_queries(idx, n: int, chars: int = 180, mode: str = "mixed"
                  ) -> List[Tuple[str, str, str]]:
    """[(쿼리, 정답 노트 rel, 정답 heading)] — mode: mixed|ko|en."""
    rnd = random.Random(SEED)
    rels = list(idx._notes)
    rnd.shuffle(rels)
    out: List[Tuple[str, str, str]] = []
    for rel in rels:
        secs = idx._notes[rel].get("sections") or []
        if len(secs) < 2:
            continue
        best = max(secs, key=lambda s: len(s.get("snippet") or ""))
        heading = str(best.get("heading") or "")
        if not heading:
            continue
        body = idx.get_section_content(rel, heading)
        if not body:
            continue
        body = clean(body)
        if mode == "ko":
            body = " ".join(w for w in body.split() if _HANGUL.search(w))
        elif mode == "en":
            body = " ".join(w for w in body.split()
                            if _LATIN.search(w) and not _HANGUL.search(w))
        if len(body) < 200:
            continue
        start = min(len(body) // 4, max(0, len(body) - chars - 1))
        q = body[start:start + chars].strip()
        if len(q) >= 60:
            out.append((q, rel, heading))
        if len(out) >= n:
            break
    return out


# ── 랭킹 변이 ─────────────────────────────────────────────
def _note_scores_single_idf(idx, query: str) -> Dict[str, float]:
    """질의 시 idf 재적용 없이 저장값(이미 tf·idf)만 합산 — '이중 idf' 대조군."""
    scores: Dict[str, float] = {}
    for token in set(vi._tokenize(query)):
        for rel, note in idx._notes.items():
            v = note["tf"].get(token, 0.0)
            if v:
                scores[rel] = scores.get(rel, 0.0) + v
    return scores


def rank(idx, query: str, *, mode: str, note_k: int = 10, paper_k: int = 4,
         paper_boost: float = 1.0, paper_tiebreak: bool = True,
         section_k: int = 12, section_weight: float = 1.0) -> List[Dict[str, Any]]:
    """mode:
      'shipped'      — 현행 구현: 노트 RRF + 논문보강, 섹션은 후보 안에서 위치특정만
      'section_arm'  — (구버전) 볼트 전체 섹션검색을 랭킹 arm 으로 융합
      'notes_only'   — 노트 검색만
      'single_idf'   — 'shipped' 와 같으나 노트 점수를 이중 idf 없이 계산
    """
    papers = list(paper_dirs())
    order: List[str] = []
    note_rank: Dict[str, int] = {}
    sec_rank: Dict[str, int] = {}
    sec_head: Dict[str, str] = {}
    meta: Dict[str, Dict[str, Any]] = {}

    def touch(rel: str, m: Optional[Dict[str, Any]] = None):
        if rel not in meta:
            order.append(rel)
            meta[rel] = m or {}

    if mode == "single_idf":
        scores = _note_scores_single_idf(idx, query)
        tfidf_ranked = sorted(scores.items(), key=lambda x: -x[1])[:max(note_k * 3, note_k)]
        sem = idx._semantic_ranking(query, max(note_k * 3, note_k))
        if sem:
            fused = vi._rrf_fuse([[r for r, _ in tfidf_ranked], [r for r, _ in sem]])
            ranked = sorted(fused, key=lambda r: -fused[r])[:note_k]
        else:
            ranked = [r for r, _ in tfidf_ranked[:note_k]]
        for r, rel in enumerate(ranked):
            touch(rel); note_rank[rel] = r
    else:
        for r, x in enumerate(idx.search(query, limit=note_k)):
            rel = x.get("path") or ""
            if rel:
                touch(rel); note_rank[rel] = r

    if mode in ("shipped", "single_idf"):
        base = len(note_rank)
        for r, x in enumerate(idx.search(query, limit=paper_k, path_prefixes=papers,
                                         path_match=PAPER_MATCH)):
            rel = x.get("path") or ""
            if rel and rel not in note_rank:
                touch(rel); note_rank[rel] = base + r
        for rel, sec in (idx.sections_in_notes(query, order) or {}).items():
            sec_head[rel] = sec.get("heading", "")
    elif mode == "section_arm":
        groups = [idx.search_sections(query, limit=section_k),
                  idx.search_sections(query, limit=max(3, section_k // 3),
                                      path_prefixes=papers, path_match=PAPER_MATCH)]
        for g in groups:
            for r, s in enumerate(g):
                rel = s.get("note_path") or ""
                if not rel:
                    continue
                touch(rel)
                if rel not in sec_rank or r < sec_rank[rel]:
                    sec_rank[rel] = r
                    sec_head[rel] = str(s.get("heading") or "")

    hits: List[Dict[str, Any]] = []
    for rel in order:
        v = 0.0
        if rel in note_rank:
            v += 1.0 / (RRF_K + note_rank[rel] + 1)
        if rel in sec_rank:
            v += section_weight / (RRF_K + sec_rank[rel] + 1)
        if is_paper(rel):
            v *= paper_boost
        hits.append({"rel": rel, "v": v, "heading": sec_head.get(rel, ""),
                     "paper": is_paper(rel)})
    if paper_tiebreak:
        hits.sort(key=lambda h: (-h["v"], not h["paper"]))
    else:
        hits.sort(key=lambda h: -h["v"])
    seen, out = set(), []
    for h in hits:
        t = str(idx._notes.get(h["rel"], {}).get("title") or "").strip().lower()
        if not t or t in seen:
            continue
        seen.add(t); out.append(h)
    return out


VARIANTS: Dict[str, Dict[str, Any]] = {
    # shipped 는 paper_tiebreak=False — 구현에서 제거했다. 순위를 1/(k+rank+1) 로
    # 재환산하면 rank 가 후보마다 유일해 동점이 없고, 그래서 tie-break 이 한 번도
    # 발동하지 않는 죽은 코드였다. 아래 구버전 변이들은 비교용으로만 남긴다.
    "shipped(노트RRF+논문보강+위치특정)": dict(mode="shipped", paper_tiebreak=False),
    "section_arm+논문1.2배(구버전)":     dict(mode="section_arm", paper_boost=1.2,
                                              paper_tiebreak=False),
    "section_arm(가산없음)":             dict(mode="section_arm"),
    "section_arm(가중0.5)":              dict(mode="section_arm", section_weight=0.5),
    "notes_only":                        dict(mode="notes_only"),
    "shipped+논문1.2배":                 dict(mode="shipped", paper_boost=1.2,
                                              paper_tiebreak=False),
    "single_idf(이중idf 제거)":          dict(mode="single_idf", paper_tiebreak=False),
}


def evaluate(idx, queries, cfg: Dict[str, Any]) -> Dict[str, Any]:
    r1 = r3 = head_ok = head_tot = paper_top3 = 0
    rr: List[float] = []
    times: List[float] = []
    for q, gold, gold_head in queries:
        t0 = time.perf_counter()
        hits = rank(idx, q, **cfg)
        times.append((time.perf_counter() - t0) * 1000)
        rels = [h["rel"] for h in hits[:10]]
        if rels[:1] == [gold]:
            r1 += 1
        if gold in rels[:DISPLAY_N]:
            r3 += 1
            h = next(x for x in hits[:DISPLAY_N] if x["rel"] == gold)
            head_tot += 1
            head_ok += int((h.get("heading") or "") == gold_head)
        rr.append(1.0 / (rels.index(gold) + 1) if gold in rels else 0.0)
        paper_top3 += sum(1 for h in hits[:DISPLAY_N] if h["paper"])
    n = max(1, len(queries))
    return {"recall@1": round(r1 / n, 3), f"recall@{DISPLAY_N}": round(r3 / n, 3),
            "MRR@10": round(sum(rr) / n, 3),
            "heading@3": round(head_ok / head_tot, 3) if head_tot else None,
            "paper_per_query_top3": round(paper_top3 / n, 2),
            "ms": round(statistics.median(times), 1), "n": n}


def index_stats(idx) -> Dict[str, Any]:
    sec = sum(len(n.get("sections", []) or []) for n in idx._notes.values())
    idx._load_embeddings()
    emb = (idx._emb or {}).get("notes", {})
    return {"notes": len(idx._notes), "sections": sec,
            "sections_per_note": round(sec / max(1, len(idx._notes)), 1),
            "embedded_notes": len(emb), "dims": (idx._emb or {}).get("dims"),
            "model": (idx._emb or {}).get("model"), "vocab_terms": len(idx._idf)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=24, help="쿼리 수 (기본 24)")
    ap.add_argument("--no-embed", action="store_true", help="임베딩 API 미사용")
    ap.add_argument("--json", type=str, default="", help="결과 JSON 저장 경로")
    args = ap.parse_args()

    idx = vi.VaultIndexer.from_config()
    if idx is None or not idx.load():
        print("인덱스를 로드할 수 없습니다 — 먼저 `python run_meeting.py reindex`")
        return 1

    restore = emb_toggle(not args.no_embed)
    res: Dict[str, Any] = {"index": index_stats(idx), "embed": not args.no_embed}
    print("== 인덱스 규모 ==")
    print(json.dumps(res["index"], ensure_ascii=False, indent=2))

    qs = build_queries(idx, args.n, 180, "mixed")
    res["gold_paper_ratio"] = round(
        sum(1 for _, r, _ in qs if is_paper(r)) / max(1, len(qs)), 2)
    print(f"\n== 변이 비교 (쿼리 {len(qs)}건, 180자, 임베딩 "
          f"{'ON' if not args.no_embed else 'OFF'}) ==")
    res["variants"] = {}
    for name, cfg in VARIANTS.items():
        m = evaluate(idx, qs, cfg)
        res["variants"][name] = m
        print(f"  {name:32s} R@1={m['recall@1']:.2f} R@3={m['recall@3']:.2f} "
              f"MRR={m['MRR@10']:.3f} heading@3={m['heading@3']} "
              f"논문/쿼리={m['paper_per_query_top3']:.2f} {m['ms']:.0f}ms")

    print("\n== 쿼리 길이 (shipped) ==")
    res["query_len"] = {}
    for chars in (60, 180, 400):
        m = evaluate(idx, build_queries(idx, args.n, chars, "mixed"),
                     VARIANTS["shipped(노트RRF+논문보강+위치특정)"])
        res["query_len"][str(chars)] = m
        print(f"  {chars:>4d}자 R@1={m['recall@1']:.2f} R@3={m['recall@3']:.2f} "
              f"MRR={m['MRR@10']:.3f}")

    if not args.no_embed:
        print("\n== 교차언어 (토큰을 한쪽 언어로만 남긴 쿼리) ==")
        res["cross_lang"] = {}
        for mode in ("ko", "en"):
            q2 = build_queries(idx, args.n, 180, mode)
            for on in (True, False):
                o = emb_toggle(on)
                try:
                    idx._query_vec_cache.clear()
                    m = evaluate(idx, q2, VARIANTS["shipped(노트RRF+논문보강+위치특정)"])
                finally:
                    vi._c = o
                res["cross_lang"][f"{mode}_emb_{'on' if on else 'off'}"] = m
                print(f"  {mode}_emb_{'on' if on else 'off':3s} n={m['n']:2d} "
                      f"R@1={m['recall@1']:.2f} R@3={m['recall@3']:.2f} "
                      f"MRR={m['MRR@10']:.3f}")

    print("\n== 지연 분해 (임베딩 OFF, median) ==")
    o = emb_toggle(False)
    try:
        idx._query_vec_cache.clear()
        lat: Dict[str, float] = {}
        for label, cfg in (("shipped", VARIANTS["shipped(노트RRF+논문보강+위치특정)"]),
                           ("section_arm", VARIANTS["section_arm(가산없음)"]),
                           ("notes_only", VARIANTS["notes_only"])):
            lat[label] = evaluate(idx, qs, cfg)["ms"]
        ts = [_t(idx.search_sections, q, limit=12) for q, _, _ in qs]
        lat["search_sections_full_scan"] = round(statistics.median(ts), 2)
        cands = [[h["rel"] for h in rank(idx, q, mode="notes_only")] for q, _, _ in qs]
        ts = [_t(idx.sections_in_notes, q, c) for (q, _, _), c in zip(qs, cands)]
        lat["sections_in_notes_candidates"] = round(statistics.median(ts), 2)
        res["latency_ms"] = lat
        for k, v in lat.items():
            print(f"  {k:30s} {v:8.2f} ms")
    finally:
        vi._c = o

    if not args.no_embed:
        idx._query_vec_cache.clear()
        ts = [_t(idx._embed_texts, [q[:2000]]) for q, _, _ in qs[:8]]
        res.setdefault("latency_ms", {})["embedding_api"] = round(statistics.median(ts), 1)
        print(f"  {'embedding_api':30s} {res['latency_ms']['embedding_api']:8.2f} ms")

    # 구현 일치 검증 — bench 'shipped' 와 실제 _search_index 가 같은 순위를 내는지
    s = rs.RealtimeVaultSearcher()
    s._indexer = idx
    s._init_done = True
    agree = 0
    for q, _, _ in qs:
        # 구현은 후보를 전량 반환하고 제목 중복 제거는 표시 단계에서 한다 —
        # 벤치의 rank() 는 화면에 뜨는 순서를 재므로 같은 단계를 거쳐 비교한다.
        a = [h["filename"] for h in rs.dedupe_by_title(s._search_index(q, q))][:5]
        b = [h["rel"] for h in rank(idx, q, **VARIANTS["shipped(노트RRF+논문보강+위치특정)"])][:5]
        agree += int(a == b)
    s.shutdown()
    res["impl_agreement_top5"] = f"{agree}/{len(qs)}"
    print(f"\n== 구현 일치(top5) == {agree}/{len(qs)}"
          f"{'  ← 불일치면 벤치가 구현과 어긋났다는 뜻' if agree != len(qs) else ''}")

    vi._c = restore
    if args.json:
        Path(args.json).write_text(json.dumps(res, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        print(f"\n저장: {args.json}")
    return 0


def _t(fn, *a, **kw) -> float:
    t0 = time.perf_counter()
    fn(*a, **kw)
    return (time.perf_counter() - t0) * 1000


if __name__ == "__main__":
    raise SystemExit(main())
