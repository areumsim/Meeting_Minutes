"""
realtime_search.py — 실시간 녹취 중 세그먼트별 Vault 검색 (공유 모듈)
=====================================================================
회의 중 발화(세그먼트)가 확정될 때마다 관련 vault 노트를 백그라운드로
검색해 호출자(터미널 UI / 웹 WS)에 전달한다.

웹 백엔드 BrowserRealtimeSession._search_vault_segment 로직을 추출·일반화 —
CLI(realtime_transcription)와 웹(web/backend/api/realtime.py) 양쪽에서 사용.

설계 원칙:
  - offer_segment()는 STT 핫패스에서 호출되므로 절대 블로킹/raise 금지
  - 검색 백엔드: VaultIndexer(로컬 인덱스, ms 단위) 우선, Obsidian REST 폴백
    (wiki.realtime_search_backend: "auto"|"index"|"rest")
  - 인덱스/Obsidian 둘 다 못 쓰면 비활성화하되 **사유를 status()로 노출**한다
    (과거엔 조용히 꺼져 "기능이 없는 것처럼" 보였다 — 실시간 스트림은 여전히 무영향)
  - config 게이트: wiki.realtime_vault_search / wiki.realtime_search_interval

내부자료 우선(꼼꼼) 검색 — 인덱스 백엔드일 때:
  ① 노트 인덱스 search (TF-IDF+임베딩 RRF) … 랭킹의 주축, 교차언어(en↔ko)도 담당
  ② 논문/이론 폴더 한정 노트 검색           … 로컬 논문·원문추출의 후보 풀 진입 보장
     폴더 매칭은 `path_match="segment"` — 볼트 하위에 묻힌
     `Archive/…/02_이론_학습` 같은 경로도 잡는다(배지 판정과 같은 규칙).
  ③ 후보 노트 안에서만 섹션 채점             … "어느 대목이 근거인가"(표시·인용용)
  → 순위는 ① 랭킹 순서 그대로이고, ②에서만 나온 논문 후보를 그 뒤에 이어붙인다.
    후보는 넉넉히(기본 14) 모아 **전량 누적**하고, 제목 중복 제거는 화면·회의록에
    같은 `[[제목]]` 이 두 번 보이지 않게 하려는 것이므로 **표시 단계에서만** 한다.
    랭킹 구조는 실측으로 고른 것 — docs/검색랭킹_이론과근거.md 참고.
  웹 검색은 이 모듈이 하지 않는다 — 항상 보완재로 호출자(웹 UI)에서 별도 처리.

사용:
    searcher = RealtimeVaultSearcher(topic=topic, on_notes=display_fn,
                                     on_status=badge_fn)
    searcher.warmup()                   # 백엔드 연결 상태를 미리 확인(논블로킹)
    ...
    searcher.offer_segment(text)        # 세그먼트 확정 시마다
    ...
    titles = searcher.collected_titles()    # 종료 후 memo 병합용
    ev = searcher.collected_evidence()       # 근거(점수·snippet·섹션·발화) 누적분
    searcher.shutdown(wait=True)
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from meeting_minutes_app.wiki_core.vault_indexer import RRF_K, path_matcher

try:
    from meeting_minutes_app.common import config_loader as _cfg
    _cfg_ok = True
except ImportError:
    _cfg = None  # type: ignore
    _cfg_ok = False


def _c(key: str, default: Any = None) -> Any:
    return _cfg.get(key, default) if _cfg_ok else default


#: 비활성 사유 코드 → 사용자에게 보여줄 한국어 문구 (Recorder 상태 배지 / CLI 안내)
REASON_TEXT: Dict[str, str] = {
    "": "",
    "off": "설정에서 '실시간 볼트 검색'이 꺼져 있습니다.",
    "no_vault": "노트 폴더(볼트)가 설정되지 않았습니다 — [설정]에서 지정하세요.",
    "index_missing": "검색 인덱스가 없습니다 — [설정]의 '인덱스 재빌드'를 실행하세요.",
    "obsidian_unreachable": "Obsidian REST에 연결할 수 없습니다.",
    "no_backend": "검색 인덱스도 Obsidian도 사용할 수 없습니다 — 인덱스를 재빌드하세요.",
}

#: 출처유형 → 표시 아이콘. 이 모듈이 `source_type` 을 만드는 곳이므로 규약도 여기 둔다
#: (회의록 `finalize`·CLI 표시가 이걸 import 한다. 프런트 `Recorder.tsx` 는 TS라
#:  같은 표를 복제하지만 값을 바꿀 땐 이 상수가 기준이다.)
SOURCE_ICON: Dict[str, str] = {"note": "📄", "paper": "🎓", "web": "🌐"}

#: 논문 폴더 매칭 규칙 — 접두사가 아니라 **경로 세그먼트** 일치.
#: `02_이론_학습` 처럼 볼트 하위(`Archive/…/02_이론_학습`)에 있는 폴더도 잡아야
#: 논문 보강 arm 과 배지 판정이 같은 노트 집합을 보게 된다(과거엔 갈라져 있었다).
_PAPER_PATH_MATCH = "segment"

#: rank_score 가 가질 수 있는 최대값(1위) = 1/(RRF_K+1) ≈ 0.0164.
#: rank_score 는 **순위의 단조 변환**이지 관련도 점수가 아니다 — 임계값을 줄 때
#: 0.1 같은 '점수처럼 보이는' 값을 쓰면 전부 걸러진다. 그래서 관련 파라미터 이름을
#: min_rank_score 로 명시한다.
RANK_SCORE_TOP = 1.0 / (RRF_K + 1)


def _paper_dirs() -> Tuple[str, ...]:
    """논문/이론/원문추출 폴더 — 로컬 논문을 웹 arXiv보다 먼저 인용하기 위한 우선 경로."""
    dirs = _c("wiki.realtime_paper_dirs",
              ["02_이론_학습", "01_References", "원문추출"]) or []
    if not isinstance(dirs, (list, tuple)):
        return ()
    return tuple(str(d).strip().strip("/") for d in dirs if str(d).strip())


def _is_paper_path(rel_path: str, paper_dirs: Sequence[str]) -> bool:
    """노트가 논문/이론 폴더 소속인가 — 논문 arm 의 검색 필터와 **같은 판정**을 쓴다."""
    match = path_matcher(paper_dirs, _PAPER_PATH_MATCH)
    return bool(match and match(rel_path))


def dedupe_by_title(hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """제목이 같은 노트는 상위 1건만 남긴다 (입력은 이미 정렬된 상태).

    볼트에는 같은 제목의 노트가 다른 폴더에 여러 개 있을 수 있다(원문추출 사본,
    `01_References/Companies/Acme.md` vs `Archive/…/회사/Acme.md` 등). 표시는
    `[[제목]]` 위키링크라 두 건이 화면·회의록에서 구분되지 않으므로 하나만 남긴다.

    **표시 단계에서만 쓴다.** 누적(`_notes`)에는 전량을 넣는다 — 제목이 같아도 서로
    다른 노트이고(같은 제목의 다른 회의록이 실제로 존재한다), 누적 검토·사이드카는
    경로로 구분해 보여주기 때문이다. 과거엔 검색 결과 조립 단계에서 걸러 누적분까지
    함께 사라졌다."""
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for h in hits:
        key = str(h.get("title") or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(h)
    return out


class RealtimeVaultSearcher:
    """세그먼트 텍스트로 vault를 스로틀 검색하는 논블로킹 헬퍼.

    on_notes(notes) / on_status(status)는 검색 풀 스레드에서 호출된다 —
    표시/전송의 스레드 안전성은 호출자 책임 (예: RecordingIndicator.claim/release,
    _send_to_browser 큐).
    """

    def __init__(
        self,
        *,
        topic: str = "",
        on_notes: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
        on_status: Optional[Callable[[Dict[str, Any]], None]] = None,
        allow_launch: bool = False,
    ):
        self.topic = (topic or "").strip()
        self.on_notes = on_notes
        self.on_status = on_status
        # Obsidian 앱 자동 실행 허용 여부 — CLI 녹음 중엔 포커스 강탈/최대 40초
        # 블록이 있어 금지, 웹 백엔드는 기존 동작 보존을 위해 허용
        self.allow_launch = allow_launch

        self._gate = bool(_c("wiki.realtime_vault_search", True))
        self._interval = max(int(_c("wiki.realtime_search_interval", 3) or 3), 1)
        self._backend_pref = str(_c("wiki.realtime_search_backend", "auto") or "auto")
        # 내부 후보는 넉넉히 모으고(누적 검토용) 표시만 상위 N개로 제한한다
        self._note_k = max(int(_c("wiki.realtime_note_candidates", 10) or 10), 1)
        self._paper_k = max(int(_c("wiki.realtime_paper_candidates", 4) or 4), 1)
        self._display_n = max(int(_c("wiki.realtime_display_count", 3) or 3), 1)
        self._query_chars = max(int(_c("wiki.realtime_query_chars", 180) or 180), 20)

        self._counter = 0
        self._notes: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._last_shown_titles: frozenset = frozenset()
        self._t0 = time.time()

        self._pool: Optional[ThreadPoolExecutor] = None
        if self._gate:
            self._pool = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="vault-search")

        # 검색 백엔드 (풀 스레드에서 lazy init — 인덱스 로드는 수 초 걸릴 수 있음)
        self._indexer = None
        self._obs = None
        self._init_done = False
        self._disabled = False   # lazy init 실패 후 no-op 전환
        self._reason = "" if self._gate else "off"
        self._status_sent = False

    # ── 상태 ──────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return self._gate and not self._disabled

    @property
    def backend(self) -> str:
        if self._indexer is not None:
            return "index"
        if self._obs is not None:
            return "rest"
        return ""

    def status(self) -> Dict[str, Any]:
        """현재 연결 상태 — 웹 Recorder 상태 배지 / CLI 안내용(FR-1).

        reason 이 빈 문자열이면 정상, 아니면 REASON_TEXT 의 사유 코드다.
        initialized=False 는 "아직 첫 검색 전(연결 미확인)" 을 뜻한다.
        """
        return {
            "enabled": self.enabled,
            "gate": self._gate,
            "backend": self.backend,
            "reason": self._reason,
            "reasonText": REASON_TEXT.get(self._reason, self._reason),
            "initialized": self._init_done,
        }

    def warmup(self) -> None:
        """백엔드 연결을 미리(논블로킹) 확인하고 on_status 로 상태를 1회 보고한다.

        세션 시작 직후 호출하면 첫 발화를 기다리지 않고 배지를 띄울 수 있다.
        게이트가 꺼져 있으면 풀이 없으므로 이 자리에서 바로 보고한다."""
        try:
            if self._pool is not None:
                self._pool.submit(self._warmup_task)
            else:
                self._report_status()
        except Exception:
            pass

    def _warmup_task(self) -> None:
        try:
            self._lazy_init()
        except Exception:
            pass
        self._report_status()

    def _report_status(self) -> None:
        """on_status 를 1회만 호출 (사유가 바뀌면 다시 보고)."""
        try:
            if self.on_status is None:
                return
            key = (self._reason, self.backend, self._disabled)
            if self._status_sent and key == getattr(self, "_status_key", None):
                return
            self._status_sent = True
            self._status_key = key
            self.on_status(self.status())
        except Exception:
            pass

    # ── 핫패스 API ────────────────────────────────────────

    def offer_segment(self, text: str) -> None:
        """세그먼트 확정 시 호출. 스로틀 간격에 맞으면 검색을 풀에 제출.
        절대 블로킹하지 않고, 절대 예외를 전파하지 않는다."""
        try:
            if not self.enabled or not text or not text.strip():
                return
            self._counter += 1
            if self._counter % self._interval != 0:
                return
            if self._pool is not None:
                self._pool.submit(self._search, text)
        except Exception:
            pass

    # ── 결과 스냅샷 ───────────────────────────────────────

    def collected_notes(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._notes)

    def collected_titles(self) -> List[str]:
        """수집된 노트의 고유 title (삽입 순서 유지) — 종료 후 memo 병합용."""
        with self._lock:
            titles = [n.get("title", "") for n in self._notes]
        return list(dict.fromkeys(t for t in titles if t))

    def collected_evidence(self, limit: int = 50,
                           min_rank_score: float = 0.0) -> List[Dict[str, Any]]:
        """노트별 대표 근거 1건씩 — SQLite 누적/회의록 병합용(FR-4/6).

        같은 노트가 여러 발화에서 반복 매칭되면 **가장 높은 순위에 들었던 히트**를
        대표로 남기고 참조 횟수(hits)를 센다. 정렬도 같은 기준(rank_score 내림차순).

        rank_score 를 쓰는 이유: TF-IDF `score` 는 쿼리마다 스케일이 달라 발화 간
        비교가 안 되지만, 순위는 쿼리 내부에서 정규화된 값이라 비교가 가능하다.
        다만 그래서 이 값은 "관련도의 세기"가 아니라 "어느 발화에서 몇 위였나"다.

        min_rank_score: rank_score(≤ RANK_SCORE_TOP ≈ 0.0164) 하한. 관련도 점수가
        아니므로 0.1 같은 값을 주면 전부 걸러진다 — 기본 0.0(필터 없음).
        """
        with self._lock:
            notes = list(self._notes)
        best: Dict[str, Dict[str, Any]] = {}
        for n in notes:
            key = str(n.get("filename") or n.get("title") or "")
            if not key:
                continue
            cur = best.get(key)
            if cur is None:
                item = dict(n)
                item["hits"] = 1
                best[key] = item
            else:
                cur["hits"] = int(cur.get("hits", 1)) + 1
                if float(n.get("rank_score", 0) or 0) > float(cur.get("rank_score", 0) or 0):
                    hits = cur["hits"]
                    item = dict(n)
                    item["hits"] = hits
                    best[key] = item
        out = sorted(best.values(),
                     key=lambda n: -float(n.get("rank_score", 0) or 0))
        out = [n for n in out if float(n.get("rank_score", 0) or 0) >= min_rank_score]
        return out[:max(1, limit)]

    def shutdown(self, wait: bool = True) -> None:
        """검색 풀 drain 후 종료. collected_*()의 완결성을 보장하려면 wait=True."""
        try:
            if self._pool is not None:
                self._pool.shutdown(wait=wait)
        except Exception:
            pass
        try:
            if self._obs is not None:
                self._obs.close()
        except Exception:
            pass

    # ── 내부 (검색 풀 스레드에서만 실행) ──────────────────

    def _lazy_init(self) -> None:
        if self._init_done:
            return
        self._init_done = True

        reason = ""
        if self._backend_pref in ("auto", "index"):
            try:
                from meeting_minutes_app.wiki_core.vault_indexer import VaultIndexer
                idx = VaultIndexer.from_config()
                if idx is None:
                    reason = "no_vault"
                elif idx.load():
                    self._indexer = idx
                else:
                    reason = "index_missing"
            except Exception:
                self._indexer = None
                reason = reason or "index_missing"

        if self._indexer is None and self._backend_pref in ("auto", "rest"):
            try:
                from meeting_minutes_app.wiki_core.obsidian import ObsidianClient
                obs = ObsidianClient.from_config()
                if obs:
                    if not obs.ping() and self.allow_launch:
                        obs.ensure_running()
                    if obs.ping():
                        self._obs = obs
                    else:
                        reason = "obsidian_unreachable"
            except Exception:
                self._obs = None
                reason = reason or "obsidian_unreachable"

        if self._indexer is None and self._obs is None:
            self._disabled = True  # 이후 offer_segment는 전부 no-op
            self._reason = reason or "no_backend"
        else:
            self._reason = ""

    def _search(self, text: str) -> None:
        """단일 세그먼트 검색 — 실패는 전부 무시 (실시간 스트림 보호)."""
        try:
            self._lazy_init()
            self._report_status()
            if self._disabled:
                return

            query = self._build_query(text)
            if self._indexer is not None:
                hits = self._search_index(query, text)
            else:
                hits = self._search_rest(query, text)
            if not hits:
                return

            with self._lock:
                self._notes.extend(hits)

            if self.on_notes:
                # 누적은 전량, 표시는 제목 중복 제거 후 상위 N개 (dedupe_by_title 주석 참고)
                top = dedupe_by_title(hits)[:self._display_n]
                titles = frozenset(n["title"] for n in top)
                # 같은 노트 세트가 연속 매칭되면 표시 생략 (터미널/UI 스팸 방지)
                if titles and titles != self._last_shown_titles:
                    self._last_shown_titles = titles
                    self.on_notes(top)
        except Exception:
            pass

    def _build_query(self, text: str) -> str:
        """검색 쿼리 = 발화 앞부분 + 주제.

        임베딩(교차언어) 경로는 문맥이 길수록 유리하고 TF-IDF는 토큰이 많아도
        idf 가중으로 걸러지므로, 과거 60자보다 넉넉하게(기본 180자) 쓴다."""
        return (text[:self._query_chars]
                + (" " + self.topic if self.topic else "")).strip()

    # ── 인덱스 백엔드: 내부자료 우선·꼼꼼 검색 (FR-11) ────

    def _search_index(self, query: str, segment_text: str) -> List[Dict[str, Any]]:
        """후보 = 노트 인덱스(TF-IDF+임베딩 RRF) + 논문/이론 폴더 한정 노트 검색,
        근거 위치(섹션)는 그 후보 안에서만 특정한다. 반환은 rank_score 내림차순
        (= arm ① 랭킹 순, 그 뒤에 ②에서만 나온 논문 후보). 제목 중복은 남겨 둔다 —
        누적은 전량이고 중복 제거는 표시 단계 책임이다(`dedupe_by_title`).

        설계 근거(실측 2026-07-29, 472노트·3,744섹션 볼트 / 합성 쿼리 24건):
          · 논문 폴더 점수 1.2배 가산은 랭킹을 크게 악화시켰다(MRR 0.920→0.713,
            R@3 0.96→0.71). 낡은 인덱스(802노트)에서도 같은 방향이었다(0.664→0.575).
            폴더 소속은 관련도의 근거가 아니므로 **점수를 건드리지 않는다**.
            논문의 후보 풀 진입은 논문 폴더 한정 검색 arm 이 전담한다(FR-11).
            (과거엔 "동점이면 논문 우선" tie-break 도 함께 뒀는데, 순위를
             1/(k+rank+1) 로 재환산하면 rank 가 후보마다 유일해 동점이 발생하지
             않는다 — 실측 확인 후 죽은 코드를 제거했다. 논문을 동점에서라도
             끌어올리는 것은 위에서 반박된 '가산' 방향이므로 되살리지 않는다.)
          · 볼트 전체 섹션 검색을 랭킹 arm 으로 융합해도 회수 이득이 없고(노이즈 범위)
            로컬 지연이 2.7배(89ms→240ms)다. 섹션 TF-IDF 는 tf 길이정규화 때문에 짧은
            섹션에서 과대평가되므로 노트 점수와 같은 축에 두면 안 된다. 섹션은 '어느
            대목이 근거인가'에만 쓴다.
          · 전체 섹션 스캔은 섹션 수에 선형 — 후보 안에서만 보면 ~0.5ms 로 같은 heading
            정보를 얻는다. 노트 회수는 notes_only 와 동일하고 heading 정확도만
            0 → 0.87 로 올라간다.
          · 위 ms 는 **로컬 계산만**이다. 기본 설정에선 검색마다 쿼리 임베딩 API 왕복
            (~270ms)이 붙어 실사용 1회는 0.3~0.5초다 — 전용 워커 스레드에서 돌므로
            전사 스트림에는 영향이 없다.
        자세한 수치·측정 한계·재검증 대상: docs/검색랭킹_이론과근거.md
        """
        idx = self._indexer
        papers = _paper_dirs()

        notes = self._safe(idx.search, query, limit=self._note_k) or []
        paper_notes = []
        if papers:
            # 로컬 논문/원문추출이 일반 노트에 밀려 후보에 못 들어오는 것을 막는 보강 arm.
            # path_match="segment" — 볼트 하위에 묻힌 논문 폴더까지 잡는다(_is_paper_path
            # 와 같은 규칙). 접두사 매칭만 하던 과거엔 `Archive/…/02_이론_학습` 74노트와
            # `원문추출` 9노트가 이 arm 에서 영구히 0건이었다.
            paper_notes = self._safe(idx.search, query, limit=self._paper_k,
                                     path_prefixes=list(papers),
                                     path_match=_PAPER_PATH_MATCH) or []

        # 후보 조립 — 삽입 순서(노트 랭킹 → 논문 보강)를 유지해 동점 시 순서가 안정적
        cand: Dict[str, Dict[str, Any]] = {}
        note_rank: Dict[str, int] = {}
        for group, offset in ((notes, 0), (paper_notes, len(notes))):
            for rank, r in enumerate(group):
                rel = str(r.get("path") or "")
                if not rel or rel in cand:
                    continue
                note_rank[rel] = offset + rank
                cand[rel] = {
                    "title": str(r.get("wikilink_title") or r.get("title") or ""),
                    "snippet": str(r.get("snippet", "") or "")[:200],
                    "score": float(r.get("score", 0) or 0),
                    "cosine": float(r.get("cosine", 0) or 0),
                    "date": str(r.get("date", "") or ""),
                }
        if not cand:
            return []

        # 근거 위치 특정 — 후보 노트들의 섹션만 채점.
        # 메서드가 없는 구버전 인덱서(또는 섹션 인덱스 없이 빌드된 인덱스)에서도
        # 후보 자체는 그대로 살려야 한다 → getattr 로 존재 여부부터 확인한다.
        _locate = getattr(idx, "sections_in_notes", None)
        located = (self._safe(_locate, query, list(cand)) or {}) if _locate else {}

        hits: List[Dict[str, Any]] = []
        for rel, item in cand.items():
            title = item["title"] or (Path(rel).stem if rel else "")
            if not title:
                continue
            sec = located.get(rel) or {}
            heading = str(sec.get("heading") or "")
            snippet = str(sec.get("snippet") or "")[:200] or item["snippet"]
            is_paper = _is_paper_path(rel, papers)
            hits.append({
                "filename": rel,
                "title": title,
                "score": round(float(item["score"]), 4),
                # rank = 이 발화에서의 0-기반 순위, rank_score = 그 순위의 단조 변환.
                # 둘은 같은 정보이고 관련도 점수가 아니다(RANK_SCORE_TOP 주석 참고).
                "rank": note_rank[rel],
                "rank_score": round(1.0 / (RRF_K + note_rank[rel] + 1), 6),
                "cosine": round(float(item["cosine"]), 4),
                "matches": [],
                "snippet": snippet,
                "heading": heading,
                "section_path": f"{title} › {heading}" if heading else title,
                "source": "index",
                "source_type": "paper" if is_paper else "note",
                "found_by": "section" if heading else "note",
                "date": item["date"],
                "segment_text": segment_text[:200],
                "elapsed_sec": round(time.time() - self._t0, 1),
            })
        hits.sort(key=lambda n: -float(n.get("rank_score", 0) or 0))
        return hits

    def _search_rest(self, query: str, segment_text: str) -> List[Dict[str, Any]]:
        """Obsidian REST 폴백. 섹션 인덱스가 없어 heading 은 못 채우지만, 순위 규칙은
        인덱스 경로와 같게 유지한다 — 과거엔 이쪽에만 논문 1.2배 가산이 남아 있어
        같은 발화가 백엔드에 따라 다른 순서로 보였다(실측에서 반박된 그 가산이다)."""
        results = self._safe(self._obs.search_simple, query,
                             context_length=150, limit=self._note_k) or []
        papers = _paper_dirs()
        hits = []
        for rank, r in enumerate(results):
            filename = str(r.get("filename", "") or "")
            if not filename:
                continue
            rel = filename.replace("\\", "/")
            is_paper = _is_paper_path(rel, papers)
            hits.append({
                "filename": filename,
                "title": Path(rel).stem,
                "score": round(float(r.get("score", 0) or 0), 3),
                "rank": rank,
                "rank_score": round(1.0 / (RRF_K + rank + 1), 6),
                "cosine": 0.0,
                "matches": (r.get("matches") or [])[:2],
                "snippet": "",
                "heading": "",
                "section_path": Path(rel).stem,
                "source": "rest",
                "source_type": "paper" if is_paper else "note",
                "found_by": "note",
                "date": "",
                "segment_text": segment_text[:200],
                "elapsed_sec": round(time.time() - self._t0, 1),
            })
        hits.sort(key=lambda n: -float(n.get("rank_score", 0) or 0))
        return hits

    @staticmethod
    def _safe(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """검색 호출 1건 실패가 나머지 후보 수집을 막지 않게 한다."""
        try:
            return fn(*args, **kwargs)
        except Exception:
            return None
