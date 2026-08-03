"""facilitation.py — 회의 진행 페르소나 오케스트레이터 (M0 관찰모드)
=====================================================================
실시간 전사 세그먼트 위에서 페르소나(personas.py) 후보 판정을 도는 오케스트레이터.
PRD: docs/prd/PRD_회의진행_페르소나에이전트_20260803.md (§5 파이프라인, §10 비용, §13 M0).

M0 관찰모드 범위 — **화면에는 아무것도 내보내지 않는다.**
  - Tier 0 트리아지(경량 모델 1회 호출)만 돌리고, 후보 판정을 DB(`facilitation_log`)에
    기록한다. 종료 후 finalize 사실검증과 대조해 페르소나별 오탐률을 실측하기 위한
    데이터다(§15) — 이 수치 없이 위험 페르소나를 화면에 열지 않는다.
  - Tier 1 생성(_generate)·WS `facilitation` 이벤트·음성은 M1 이후. `on_intervention`
    콜백은 그 계약 자리만 잡아둔 것으로 M0 에서는 절대 호출되지 않는다(테스트 고정).

offer_segment() 계약 — `realtime_search.RealtimeVaultSearcher` 와 동일:
  - STT 핫패스에서 호출되므로 절대 블로킹/raise 금지. LLM 은 전용 스레드풀에서만.
  - 전용 풀(max_workers=2)을 새로 둔다 — 기존 `_web_pool` 은 max_workers=1 이라
    트리아지를 얹으면 관련노트·웹 표시가 뒤로 밀린다(PRD §5).
  - 게이트는 **시간 기반**(`facilitation.triage_period_sec`, 기본 25초) — 세그먼트 수
    기반이면 발화량(시간당 최대 ~700 세그먼트)에 비용 상한이 휘둘린다(§5 정정).
    침묵 구간에는 offer_segment 자체가 안 오므로 자연히 트리아지도 0회다.

비용 배선 — 이 리포에서 반복적으로 누락돼온 지점이라 3종을 전부 지난다(§10, CLAUDE.md):
  - 추정: `pricing.facilitation_triage_call_cost()` — estimate_session_cost 의
    facilitation 항과 같은 함수(표시 금액 = 한도 판정 입력).
  - 한도: 매 트리아지 전에 `spend_guard.automation_paused()`(상시 호출은 사실상
    자동 실행) → `spend_guard.blocked()`(월 한도; per-item 한도는 '오디오 1건' 규칙이라
    임베딩과 같이 제외) → 기능 전용 회의당 캡 `facilitation.max_cost_usd_per_meeting`
    (기본 설정에선 전역 캡이 0=무제한이라 blocked() 가 무력하므로 별도로 둔다).
  - 집계: `spend_guard.record(kind=KIND_FACILITATION)` → usage_log →
    `month_to_date_spend()`/`month_to_date_by_kind()` 에 잡힌다. 세션이 있어도
    `db.add_session_cost()` 를 쓰지 않는 것은 의도다 — (a) wiki_core 는 web.backend 를
    import 하지 않는 구조를 유지하고, (b) 관찰모드의 목적이 kind 별 분리 실측이며,
    (c) 두 곳에 다 적으면 월 합계에 이중 집계된다.

참견도(§4) — 실효값은 config `facilitation.personas.<key>.level` 이 정본.
  0(금지)  = 트리아지 입력에서 제외 → 진짜 0 비용(테스트 고정).
  1(관찰)  = 판정을 DB에만 기록. M0 의 전원 기본값(config 키가 없어도 1로 폴백).
  2~5      = M1 이후 채널 — M0 에서는 1과 동일하게 기록만 한다.
  위험 페르소나의 `hard_cap`(personas.py)과 전역 `facilitation.max_level` 은
  설정만으로 넘을 수 없다(persona_level 에서 클램프).
"""

from __future__ import annotations

import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from meeting_minutes_app.wiki_core.personas import Persona, all_personas, get_persona

try:
    from meeting_minutes_app.common import config_loader as _cfg
    _cfg_ok = True
except ImportError:
    _cfg = None  # type: ignore
    _cfg_ok = False


def _c(key: str, default: Any = None) -> Any:
    return _cfg.get(key, default) if _cfg_ok else default


#: 관찰(silent) 참견도 — M0 의 전원 기본값이자 config 키 부재 시 폴백.
#: PRD §3 로스터 권장값(personas.Persona.default_level)이 아니라 이 값이 기본인 것은
#: 의도다 — 화면 개입은 오탐률 실측(§15) 통과 뒤에만 연다.
OBSERVE_LEVEL = 1

#: 트리아지 프롬프트에 넣는 최근 발화 창의 최대 길이(자).
#: PRD §10 비용 추정(회당 입력 ~1.5k 토큰)의 근거 값 — 늘리면 pricing 의
#: FACILITATION_TRIAGE_INPUT_TOKENS 도 같이 늘려야 추정과 실사용이 안 갈라진다.
TRIAGE_WINDOW_CHARS = 2000

#: 관찰 로그 테이블 — usage_log 와 같은 sqlite 파일을 직접 연다(core 가 web.backend 를
#: import 하지 않는 구조 유지, usage_log.py·graph_db.py 와 같은 방식).
_SCHEMA = """
CREATE TABLE IF NOT EXISTS facilitation_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    session_id   TEXT,
    persona      TEXT NOT NULL,
    trigger_type TEXT,
    confidence   REAL,
    span         TEXT,
    need_search  INTEGER,
    level        INTEGER,
    note         TEXT
);
CREATE INDEX IF NOT EXISTS idx_facilitation_log_session
    ON facilitation_log(session_id);
"""


def _resolve_db_path(db_path: Optional[Union[str, Path]] = None) -> Optional[Path]:
    if db_path:
        return Path(db_path)
    try:
        from meeting_minutes_app.common.app_paths import get_db_path
        return get_db_path()
    except Exception:
        return None


def _connect(db_path: Optional[Union[str, Path]] = None) -> Optional[sqlite3.Connection]:
    p = _resolve_db_path(db_path)
    if p is None:
        return None
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(str(p), check_same_thread=False, timeout=30.0)
        c.execute("PRAGMA journal_mode=WAL")
        return c
    except sqlite3.Error:
        return None


def record_observation(session_id: str, persona: str, *, trigger_type: str = "",
                       confidence: float = 0.0, span: str = "",
                       need_search: bool = False, level: int = OBSERVE_LEVEL,
                       note: str = "",
                       db_path: Optional[Union[str, Path]] = None) -> bool:
    """관찰모드 판정 1건 기록. 실패해도 예외를 올리지 않는다 — 로깅은 부수 효과이지
    실시간 스트림의 본 작업이 아니다(usage_log.record 와 같은 정책)."""
    c = _connect(db_path)
    if c is None:
        return False
    try:
        with c:
            c.executescript(_SCHEMA)
            c.execute(
                "INSERT INTO facilitation_log "
                "(ts, session_id, persona, trigger_type, confidence, span, "
                " need_search, level, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (datetime.now().isoformat(timespec="seconds"), session_id, persona,
                 trigger_type, float(confidence or 0.0), span[:500],
                 1 if need_search else 0, int(level), note),
            )
        return True
    except sqlite3.Error:
        return False
    finally:
        c.close()


def observations(session_id: Optional[str] = None,
                 db_path: Optional[Union[str, Path]] = None) -> List[Dict[str, Any]]:
    """관찰 로그 조회 — 오탐률 실측(§15)·테스트용. 실패 시 빈 리스트."""
    c = _connect(db_path)
    if c is None:
        return []
    try:
        c.row_factory = sqlite3.Row
        if session_id:
            rows = c.execute(
                "SELECT * FROM facilitation_log WHERE session_id = ? ORDER BY id",
                (session_id,)).fetchall()
        else:
            rows = c.execute("SELECT * FROM facilitation_log ORDER BY id").fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error:
        return []
    finally:
        c.close()


def _call_llm(model: str, system: str, user: str, max_tokens: int = 800) -> str:
    """트리아지 LLM 1회 호출 — 반드시 common.llm_client 경유(우회 금지, PRD §7).

    llm_client.chat 의 model 파라미터는 GPT 전용이다 — claude-* 를 고르면
    preferred="claude" 로 라우팅되지만 실제 모델은 models.claude_model 을 따른다
    (Claude 모델 오버라이드는 llm_client 확장이 필요해 M1 몫). 그래서 기본값은
    지정 모델이 그대로 반영되는 gpt-4o-mini 다(§16 미결 #4 — 사내 기본도 GPT).
    """
    from meeting_minutes_app.common.llm_client import LLMClient
    preferred = "claude" if str(model or "").lower().startswith("claude") else "gpt"
    llm = LLMClient(preferred=preferred)
    if preferred == "gpt":
        return llm.chat(system, user, temp=0.0, model=model,
                        max_tokens=max_tokens) or ""
    return llm.chat(system, user, temp=0.0, max_tokens=max_tokens) or ""


class FacilitationOrchestrator:
    """세그먼트 확정 훅에서 페르소나 트리아지를 도는 논블로킹 헬퍼 (M0 관찰모드).

    on_intervention(dict) 은 M1 화면 채널의 계약 자리 — M0 에서는 호출되지 않는다.
    """

    def __init__(self, *, session_id: str = "", topic: str = "",
                 on_intervention: Optional[Callable[[Dict[str, Any]], None]] = None):
        self.session_id = str(session_id or "")
        self.topic = (topic or "").strip()
        self.on_intervention = on_intervention

        self._gate = bool(_c("facilitation.enabled", False))
        self._period = max(float(_c("facilitation.triage_period_sec", 25) or 25), 1.0)
        self._triage_model = str(_c("facilitation.triage_model", "gpt-4o-mini")
                                 or "gpt-4o-mini")
        self._meeting_cap = float(_c("facilitation.max_cost_usd_per_meeting", 0.50)
                                  or 0.0)

        self._lock = threading.Lock()
        self._window: List[str] = []
        self._last_triage: Optional[float] = None   # None = 첫 발화에서 즉시 1회
        self._session_cost = 0.0
        self._triage_count = 0
        self._observed_count = 0
        self._skip_reason = ""

        # 게이트가 꺼져 있으면 풀 자체를 만들지 않는다 — LLM 호출 0회가 구조적으로
        # 보장된다(수용 기준 §14 첫 항목, 테스트 고정).
        self._pool: Optional[ThreadPoolExecutor] = None
        if self._gate:
            self._pool = ThreadPoolExecutor(
                max_workers=2, thread_name_prefix="facilitation")

    # ── 상태 ──────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return self._pool is not None

    def persona_level(self, key: str) -> int:
        """실효 참견도 — config 값(기본 1 관찰)을 hard_cap·전역 max_level 로 클램프.

        위험 페르소나는 설정만으로 hard_cap 을 넘길 수 없다(PRD §4 수용 기준)."""
        p = get_persona(key)
        if p is None:
            return 0
        try:
            lvl = int(_c(f"facilitation.personas.{key}.level", OBSERVE_LEVEL))
        except (TypeError, ValueError):
            lvl = OBSERVE_LEVEL
        try:
            max_level = int(_c("facilitation.max_level", 3))
        except (TypeError, ValueError):
            max_level = 3
        if p.hard_cap is not None:
            lvl = min(lvl, p.hard_cap)
        return max(0, min(lvl, max_level))

    def active_personas(self) -> List[Persona]:
        """참견도 > 0 인 페르소나만 — 0(금지)은 트리아지 입력에서 제외돼 비용이 0이다."""
        return [p for p in all_personas() if self.persona_level(p.key) > 0]

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "gate": self._gate,
            "active_personas": [p.key for p in self.active_personas()],
            "triage_count": self._triage_count,
            "observed_count": self._observed_count,
            "session_cost_usd": round(self._session_cost, 6),
            "skip_reason": self._skip_reason,
        }

    # ── 핫패스 API (RealtimeVaultSearcher 와 동일 계약) ────

    def offer_segment(self, text: str) -> None:
        """세그먼트 확정 시 호출. 절대 블로킹하지 않고, 절대 예외를 전파하지 않는다.

        시간 게이트에 맞으면 트리아지를 풀에 제출한다. 게이트 시각은 제출 시점에
        선점한다 — 판정 완료를 기다리면 그 사이 세그먼트들이 중복 제출된다."""
        try:
            if self._pool is None or not text or not text.strip():
                return
            with self._lock:
                self._window.append(text.strip())
                self._trim_window_locked()
            now = time.monotonic()
            if (self._last_triage is not None
                    and now - self._last_triage < self._period):
                return
            active = self.active_personas()
            if not active:
                return
            self._last_triage = now
            with self._lock:
                window_text = "\n".join(self._window)
            self._pool.submit(self._triage_task, window_text, active)
        except Exception:
            pass

    def shutdown(self, wait: bool = True) -> None:
        """트리아지 풀 drain 후 종료 — 진행 중 판정의 기록 완결성을 보장하려면 wait=True."""
        try:
            if self._pool is not None:
                self._pool.shutdown(wait=wait)
        except Exception:
            pass

    # ── 내부 (트리아지 풀 스레드에서만 실행) ──────────────

    def _trim_window_locked(self) -> None:
        """최근 발화 창을 TRIAGE_WINDOW_CHARS 로 유지 (호출부가 락을 든다)."""
        total = 0
        kept: List[str] = []
        for t in reversed(self._window):
            total += len(t)
            if total > TRIAGE_WINDOW_CHARS and kept:
                break
            kept.append(t)
        self._window = list(reversed(kept))

    def _triage_task(self, window_text: str, active: List[Persona]) -> None:
        """Tier 0 트리아지 1회 — 실패는 전부 무시(실시간 스트림 보호).

        비용 3관문(자동 일시정지 → 월 한도 → 회의당 캡)을 지나야만 LLM 을 부른다."""
        try:
            from meeting_minutes_app.common import pricing, spend_guard

            if spend_guard.automation_paused():
                self._skip_reason = "자동 실행 일시정지로 트리아지 보류"
                return
            est = pricing.facilitation_triage_call_cost(self._triage_model)
            # per-item 한도는 '오디오 1건' 규칙 — 트리아지 1회는 그 대상이 아니다
            # (임베딩 _embedding_budget_blocked 와 같은 이유).
            reason = spend_guard.blocked(est, check_per_item=False)
            if reason:
                self._skip_reason = reason
                return
            if self._meeting_cap > 0 and self._session_cost + est > self._meeting_cap:
                self._skip_reason = (
                    f"이 회의의 facilitation 비용 ${self._session_cost:.4f}이 "
                    f"회의당 캡 ${self._meeting_cap:.2f}에 도달")
                return

            raw = _call_llm(self._triage_model, *self._build_triage_prompt(
                window_text, active))

            # 기록은 파싱 성공 여부와 무관 — LLM 은 이미 호출됐다(과금 발생).
            spend_guard.record(
                spend_guard.KIND_FACILITATION, est, model=self._triage_model,
                units=1, unit_kind="triage_call",
                note=f"session={self.session_id}")
            self._session_cost += est
            self._triage_count += 1
            self._skip_reason = ""

            for cand in self._parse_candidates(raw, active):
                # M0 관찰모드: DB 로깅만. on_intervention(화면 채널)은 호출하지 않는다.
                if record_observation(
                        self.session_id, cand["persona"],
                        trigger_type=cand.get("trigger_type", ""),
                        confidence=cand.get("confidence", 0.0),
                        span=cand.get("span", ""),
                        need_search=bool(cand.get("need_search")),
                        level=self.persona_level(cand["persona"])):
                    self._observed_count += 1
        except Exception:
            pass

    def _build_triage_prompt(self, window_text: str,
                             active: List[Persona]) -> tuple:
        """(system, user) — 활성 페르소나만 넣는다(0=금지는 여기 등장하지 않는다)."""
        system = (
            "당신은 회의 진행 보조 에이전트의 트리아지(1차 선별) 판정기입니다. "
            "아래 활성 페르소나 각각에 대해, 최근 발화에 그 페르소나가 개입할 후보가 "
            "있는지만 판정하세요. 확실한 후보만 고르고, 없으면 빈 배열을 출력하세요. "
            "출력은 JSON 배열만: "
            '[{"persona": "<키>", "trigger_type": "<트리거 유형>", '
            '"confidence": 0.0~1.0, "span": "<근거 발화 인용>", '
            '"need_search": true|false}] '
            "persona 키는 반드시 아래 목록의 키만 사용합니다."
        )
        lines = [
            f"- {p.key}: {p.role} (트리거: {', '.join(p.triggers)})"
            for p in active
        ]
        topic = f"\n회의 주제: {self.topic}" if self.topic else ""
        user = ("## 활성 페르소나\n" + "\n".join(lines)
                + topic
                + "\n\n## 최근 발화\n" + window_text)
        return system, user

    def _parse_candidates(self, raw: str,
                          active: List[Persona]) -> List[Dict[str, Any]]:
        """트리아지 응답 파싱 — 활성 페르소나 키가 아닌 항목(환각)은 버린다."""
        from meeting_minutes_app.meeting_pipeline.json_utils import parse_json_loose
        arr = parse_json_loose(raw, expect="list", default=None)
        if not isinstance(arr, list):
            return []
        allowed = {p.key for p in active}
        out: List[Dict[str, Any]] = []
        for item in arr:
            if not isinstance(item, dict):
                continue
            key = str(item.get("persona") or "").strip()
            if key not in allowed:
                continue
            try:
                conf = float(item.get("confidence") or 0.0)
            except (TypeError, ValueError):
                conf = 0.0
            out.append({
                "persona": key,
                "trigger_type": str(item.get("trigger_type") or "")[:50],
                "confidence": max(0.0, min(conf, 1.0)),
                "span": str(item.get("span") or "")[:500],
                "need_search": bool(item.get("need_search")),
            })
        return out
