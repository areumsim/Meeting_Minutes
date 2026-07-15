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
  - 인덱스/Obsidian 둘 다 못 쓰면 조용히 비활성화 (실시간 스트림 영향 0)
  - config 게이트: wiki.realtime_vault_search / wiki.realtime_search_interval

사용:
    searcher = RealtimeVaultSearcher(topic=topic, on_notes=display_fn)
    ...
    searcher.offer_segment(text)        # 세그먼트 확정 시마다
    ...
    titles = searcher.collected_titles()  # 종료 후 memo 병합용
    searcher.shutdown(wait=True)
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    from meeting_minutes_app.common import config_loader as _cfg
    _cfg_ok = True
except ImportError:
    _cfg = None  # type: ignore
    _cfg_ok = False


def _c(key: str, default: Any = None) -> Any:
    return _cfg.get(key, default) if _cfg_ok else default


class RealtimeVaultSearcher:
    """세그먼트 텍스트로 vault를 스로틀 검색하는 논블로킹 헬퍼.

    on_notes(notes)는 검색 풀 스레드에서 호출된다 — 표시/전송의
    스레드 안전성은 호출자 책임 (예: RecordingIndicator.claim/release,
    _send_to_browser 큐).
    """

    def __init__(
        self,
        *,
        topic: str = "",
        on_notes: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
        allow_launch: bool = False,
    ):
        self.topic = (topic or "").strip()
        self.on_notes = on_notes
        # Obsidian 앱 자동 실행 허용 여부 — CLI 녹음 중엔 포커스 강탈/최대 40초
        # 블록이 있어 금지, 웹 백엔드는 기존 동작 보존을 위해 허용
        self.allow_launch = allow_launch

        self._gate = bool(_c("wiki.realtime_vault_search", False))
        self._interval = max(int(_c("wiki.realtime_search_interval", 3) or 3), 1)
        self._backend_pref = str(_c("wiki.realtime_search_backend", "auto") or "auto")

        self._counter = 0
        self._notes: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._last_shown_titles: frozenset = frozenset()

        self._pool: Optional[ThreadPoolExecutor] = None
        if self._gate:
            self._pool = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="vault-search")

        # 검색 백엔드 (풀 스레드에서 lazy init — 인덱스 로드는 수 초 걸릴 수 있음)
        self._indexer = None
        self._obs = None
        self._init_done = False
        self._disabled = False   # lazy init 실패 후 no-op 전환

    # ── 상태 ──────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return self._gate and not self._disabled

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

        if self._backend_pref in ("auto", "index"):
            try:
                from meeting_minutes_app.wiki_core.vault_indexer import VaultIndexer
                idx = VaultIndexer.from_config()
                if idx and idx.load():
                    self._indexer = idx
            except Exception:
                self._indexer = None

        if self._indexer is None and self._backend_pref in ("auto", "rest"):
            try:
                from meeting_minutes_app.wiki_core.obsidian import ObsidianClient
                obs = ObsidianClient.from_config()
                if obs:
                    if not obs.ping() and self.allow_launch:
                        obs.ensure_running()
                    if obs.ping():
                        self._obs = obs
            except Exception:
                self._obs = None

        if self._indexer is None and self._obs is None:
            self._disabled = True  # 이후 offer_segment는 전부 no-op

    def _search(self, text: str) -> None:
        """단일 세그먼트 검색 — 실패는 전부 무시 (실시간 스트림 보호)."""
        try:
            self._lazy_init()
            if self._disabled:
                return

            query = (text[:60] + (" " + self.topic if self.topic else "")).strip()
            if self._indexer is not None:
                hits = self._search_index(query, text)
            else:
                hits = self._search_rest(query, text)
            if not hits:
                return

            with self._lock:
                self._notes.extend(hits)

            if self.on_notes:
                top = sorted(hits, key=lambda n: -float(n.get("score", 0)))[:3]
                titles = frozenset(n["title"] for n in top)
                # 같은 노트 세트가 연속 매칭되면 표시 생략 (터미널/UI 스팸 방지)
                if titles and titles != self._last_shown_titles:
                    self._last_shown_titles = titles
                    self.on_notes(top)
        except Exception:
            pass

    def _search_index(self, query: str, segment_text: str) -> List[Dict[str, Any]]:
        results = self._indexer.search(query, limit=5)
        hits = []
        for r in results or []:
            path = str(r.get("path", "") or "")
            title = str(r.get("wikilink_title") or r.get("title")
                        or (Path(path).stem if path else ""))
            if not title:
                continue
            hits.append({
                "filename": path,
                "title": title,
                "score": round(float(r.get("score", 0) or 0), 4),
                "matches": [],
                "snippet": str(r.get("snippet", "") or "")[:200],
                "source": "index",
                "segment_text": segment_text[:80],
            })
        return hits

    def _search_rest(self, query: str, segment_text: str) -> List[Dict[str, Any]]:
        results = self._obs.search_simple(query, context_length=150, limit=5)
        hits = []
        for r in results or []:
            filename = str(r.get("filename", "") or "")
            if not filename:
                continue
            hits.append({
                "filename": filename,
                "title": Path(filename.replace("\\", "/")).stem,
                "score": round(float(r.get("score", 0) or 0), 3),
                "matches": (r.get("matches") or [])[:2],
                "snippet": "",
                "source": "rest",
                "segment_text": segment_text[:80],
            })
        return hits
