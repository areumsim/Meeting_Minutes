"""facilitation.py — 회의 진행 페르소나 오케스트레이터 (M0 관찰모드)
=====================================================================
실시간 전사 세그먼트 위에서 페르소나(personas.py) 후보 판정을 도는 오케스트레이터.
PRD: docs/prd/PRD_회의진행_페르소나에이전트_20260803.md (§5 파이프라인, §10 비용, §13 M0).

M0 관찰모드 범위 — **화면에는 아무것도 내보내지 않는다.**
  - Tier 0 트리아지(경량 모델 1회)만 돌리고, 판정을 DB에 기록한다. 종료 후 finalize
    사실검증과 대조해 페르소나별 오탐률을 실측하기 위한 데이터다(§15) — 이 수치 없이
    위험 페르소나를 화면에 열지 않는다.
  - Tier 1 생성(_generate)·WS `facilitation` 이벤트·음성은 M1 이후. `on_intervention`
    콜백은 그 계약 자리만 잡아둔 것으로 M0 에서는 절대 호출되지 않는다(테스트 고정).

**관찰 로그는 두 테이블이다 — 이유가 있다.**
  - `facilitation_log`  = 후보(candidate) 1건 = 오탐률의 **분자**.
  - `facilitation_triage` = 트리아지 시도 1건 = **분모**. 후보 0건이었던 회차와
    비용 관문에 막힌 회차(`skip_reason`)까지 남긴다.
  분모가 없으면 "기능이 안 돌았다"와 "돌았지만 후보가 없었다"를 구분할 수 없어 precision
  을 계산할 수 없다 — M0 의 유일한 산출물이 측정 가능한 데이터인데 초기 구현이 후보만
  남겨 그 계산이 불가능했다. 조회는 `report()` / CLI `facilitation-report`.

**같은 후보를 반복 기록하지 않는다.** 창(window)은 트리아지마다 비우고 마지막 1건만
문맥 다리로 남기며, 그래도 겹치는 후보는 `span_hash` 로 판정해 새 행 대신 `repeats` 를
올린다. 초기 구현은 창을 비우지 않아(2000자 ≈ 발화 5분) 같은 발화가 25초마다 최대
~12회 다시 판정·기록됐다 — 분자가 부풀어 실측이 무의미해지고 그만큼 토큰도 헛돌았다.

offer_segment() 계약 — `realtime_search.RealtimeVaultSearcher` 와 동일:
  - STT 핫패스에서 호출되므로 절대 블로킹/raise 금지. LLM 은 전용 스레드풀에서만.
  - 전용 풀(max_workers=2)을 새로 둔다 — 기존 `_web_pool` 은 max_workers=1 이라
    트리아지를 얹으면 관련노트·웹 표시가 뒤로 밀린다(PRD §5).
  - 게이트는 **시간 기반**(`facilitation.triage_period_sec`, 기본 25초) — 세그먼트 수
    기반이면 발화량(시간당 최대 ~700 세그먼트)에 비용 상한이 휘둘린다(§5 정정).
    침묵 구간에는 offer_segment 자체가 안 오므로 자연히 트리아지도 0회다.
  - 판정 근거는 **보정 전 조각 전사(provisional)일 수 있다.** HTTP 청크 경로는
    `realtime.py` 가 `provisional:true` 로 먼저 내보내고 25초 뒤 revise 가 문장을
    교체하는데, 교체분은 다시 offer 되지 않는다(§17 이 이 구조적 한계를 오탐률 게이트의
    근거로 든다). 그래서 두 테이블 모두 `provisional` 을 기록한다 — 실측 시 조각
    기반 판정과 확정 기반 판정을 섞어 세면 안 된다.
  - **웹 녹음(`web/backend/api/realtime.py`) 경로에만 배선한다.** CLI 실시간
    (`meeting_pipeline/realtime_transcription.py`)에는 붙이지 않는다 — 그 경로는
    web DB 세션을 만들지 않아(`session_id` 없음) 종료 후 finalize 사실검증과
    대조할 키가 없고, 키 없는 관찰 로그는 M0 의 목적(대조 실측)에 쓸 수 없다.
    표본을 웹 녹음으로 한정하는 것은 의도된 선택이다.

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
    (c) 두 곳에 다 적으면 월 합계에 이중 집계된다. 대신 `note` 에 세션 키를
    `spend_guard.session_note()` 규약으로 남겨 세션별 실제 금액을 되찾을 수 있게 한다.

참견도(§4) — 실효값은 config `facilitation.personas.<key>.level` 이 정본.
  0(금지)  = 트리아지 입력에서 제외 → 진짜 0 비용(테스트 고정).
  1(관찰)  = 판정을 DB에만 기록. M0 의 전원 기본값(config 키가 없어도 1로 폴백).
  2~5      = M1 이후 채널 — M0 에서는 1과 동일하게 기록만 한다.
  위험 페르소나의 `hard_cap`(personas.py)과 전역 `facilitation.max_level` 은
  설정만으로 넘을 수 없다(persona_level 에서 클램프).
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Tuple, Union

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
#: PRD §10 비용 추정의 근거 값 — 늘리면 pricing 의
#: FACILITATION_TRIAGE_INPUT_TOKENS 도 같이 늘려야 추정과 실사용이 안 갈라진다.
TRIAGE_WINDOW_CHARS = 2000

#: 트리아지 후 창에 남기는 최근 세그먼트 수(문맥 다리). 0 이면 문장이 창 경계에서
#: 잘려 후보를 놓치고, 크게 잡으면 같은 발화를 반복 판정한다 — 1 이 그 절충이며
#: 남은 중복은 span_hash dedup 이 흡수한다.
WINDOW_CARRYOVER = 1


class Utterance(NamedTuple):
    """창(window)에 쌓이는 발화 1건 — 시각이 없으면 t0/t1 은 None."""
    text: str
    t0: Optional[float]
    t1: Optional[float]
    provisional: bool


#: 관찰 로그 테이블 — usage_log 와 같은 sqlite 파일을 직접 연다(core 가 web.backend 를
#: import 하지 않는 구조 유지, usage_log.py·graph_db.py 와 같은 방식).
#: span_hash/t0/t1/provisional/repeats 는 나중에 추가된 컬럼이라 구버전 DB 승급은
#: `sqlite_util.ensure_columns` 가 맡는다(_ensure 참조).
_SCHEMA = """
CREATE TABLE IF NOT EXISTS facilitation_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    session_id   TEXT,
    persona      TEXT NOT NULL,
    trigger_type TEXT,
    confidence   REAL,
    span         TEXT,
    span_hash    TEXT,
    t0           REAL,
    t1           REAL,
    provisional  INTEGER DEFAULT 0,
    repeats      INTEGER DEFAULT 0,
    need_search  INTEGER,
    level        INTEGER,
    note         TEXT
);
CREATE INDEX IF NOT EXISTS idx_facilitation_log_session
    ON facilitation_log(session_id);
CREATE INDEX IF NOT EXISTS idx_facilitation_log_dedup
    ON facilitation_log(session_id, persona, span_hash);

CREATE TABLE IF NOT EXISTS facilitation_triage (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    session_id   TEXT,
    model        TEXT,
    cost_usd     REAL DEFAULT 0,
    candidates   INTEGER DEFAULT 0,
    personas     INTEGER DEFAULT 0,
    window_chars INTEGER DEFAULT 0,
    t0           REAL,
    t1           REAL,
    provisional  INTEGER DEFAULT 0,
    ok           INTEGER DEFAULT 0,
    skip_reason  TEXT
);
CREATE INDEX IF NOT EXISTS idx_facilitation_triage_session
    ON facilitation_triage(session_id);
"""

#: 초기 M0 구현(커밋 917d578)으로 만들어진 facilitation_log 에는 없던 컬럼.
_LOG_COLUMNS = {
    "span_hash": "TEXT",
    "t0": "REAL",
    "t1": "REAL",
    "provisional": "INTEGER DEFAULT 0",
    "repeats": "INTEGER DEFAULT 0",
}


def _resolve_db_path(db_path: Optional[Union[str, Path]] = None) -> Optional[Path]:
    if db_path:
        return Path(db_path)
    try:
        from meeting_minutes_app.common.app_paths import get_db_path
        return get_db_path()
    except Exception:
        return None


def _connect(db_path: Optional[Union[str, Path]] = None) -> Optional[sqlite3.Connection]:
    """접속 정책(WAL·timeout·실패 시 None)은 common/sqlite_util 하나만 쓴다 —
    usage_log.py 에 같은 코드가 복제돼 있던 자리다."""
    from meeting_minutes_app.common import sqlite_util
    return sqlite_util.connect(_resolve_db_path(db_path))


def _ensure(c: sqlite3.Connection) -> None:
    """스키마 생성 + 구버전 DB 컬럼 승급. 매 기록마다 호출해도 싸다(IF NOT EXISTS)."""
    from meeting_minutes_app.common import sqlite_util
    c.executescript(_SCHEMA)
    sqlite_util.ensure_columns(c, "facilitation_log", _LOG_COLUMNS)


_WS_RE = re.compile(r"\s+")


def span_key(persona: str, span: str, trigger_type: str = "") -> str:
    """같은 후보인지 판정하는 키. 공백·대소문자 차이는 같은 것으로 본다.

    근거 인용(span)이 비면 트리거 유형으로 대체한다 — 그러면 "근거 없는 같은 유형의
    후보"가 회차마다 새 행으로 쌓이는 것을 막는다(빈 문자열끼리는 서로 같다)."""
    base = _WS_RE.sub(" ", (span or "").strip().lower())[:200]
    if not base:
        base = f"@{(trigger_type or '').strip().lower()}"
    return hashlib.sha1(f"{persona}|{base}".encode("utf-8")).hexdigest()[:16]


def record_observation(session_id: str, persona: str, *, trigger_type: str = "",
                       confidence: float = 0.0, span: str = "",
                       need_search: bool = False, level: int = OBSERVE_LEVEL,
                       note: str = "", t0: Optional[float] = None,
                       t1: Optional[float] = None, provisional: bool = False,
                       db_path: Optional[Union[str, Path]] = None) -> str:
    """관찰모드 판정 1건 기록. 반환: "new" | "repeat" | "" (실패).

    같은 세션·페르소나·근거의 후보가 이미 있으면 새 행을 만들지 않고 `repeats` 를
    올린다 — 창이 겹쳐 같은 발화가 다시 판정되는 몫을 분자에서 걷어낸다.
    실패해도 예외를 올리지 않는다 — 로깅은 부수 효과이지 실시간 스트림의 본 작업이
    아니다(usage_log.record 와 같은 정책)."""
    c = _connect(db_path)
    if c is None:
        return ""
    key = span_key(persona, span, trigger_type)
    try:
        with c:
            _ensure(c)
            row = c.execute(
                "SELECT id, confidence FROM facilitation_log "
                "WHERE session_id = ? AND persona = ? AND span_hash = ?",
                (session_id, persona, key)).fetchone()
            if row is not None:
                # 신뢰도는 관측된 최대값을 남긴다(같은 후보를 두 번째로 더 확신했다면
                # 그게 이 후보에 대한 모델의 최종 판단에 가깝다).
                c.execute(
                    "UPDATE facilitation_log SET repeats = repeats + 1, ts = ?, "
                    "confidence = MAX(COALESCE(confidence, 0), ?) WHERE id = ?",
                    (datetime.now().isoformat(timespec="seconds"),
                     float(confidence or 0.0), row[0]))
                return "repeat"
            c.execute(
                "INSERT INTO facilitation_log "
                "(ts, session_id, persona, trigger_type, confidence, span, "
                " span_hash, t0, t1, provisional, repeats, need_search, level, note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
                (datetime.now().isoformat(timespec="seconds"), session_id, persona,
                 trigger_type, float(confidence or 0.0), span[:500], key,
                 t0, t1, 1 if provisional else 0,
                 1 if need_search else 0, int(level), note),
            )
        return "new"
    except sqlite3.Error:
        return ""
    finally:
        c.close()


def record_triage(session_id: str, *, model: str = "", cost_usd: float = 0.0,
                  candidates: int = 0, personas: int = 0,
                  window_chars: int = 0, t0: Optional[float] = None,
                  t1: Optional[float] = None, provisional: bool = False,
                  ok: bool = False, skip_reason: str = "",
                  db_path: Optional[Union[str, Path]] = None) -> bool:
    """트리아지 **시도** 1건 기록 — 오탐률의 분모이자 건너뜀 사유의 영속 기록.

    `skip_reason` 이 비어 있지 않으면 LLM 을 부르지 않은 회차다(cost_usd=0).
    이 행이 없으면 나중에 "관찰 데이터가 비어 있다"의 원인을 알 수 없다 —
    한도에 막혔는지, 후보가 없었는지, 아예 안 돌았는지가 구분되지 않는다."""
    c = _connect(db_path)
    if c is None:
        return False
    try:
        with c:
            _ensure(c)
            c.execute(
                "INSERT INTO facilitation_triage "
                "(ts, session_id, model, cost_usd, candidates, personas, "
                " window_chars, t0, t1, provisional, ok, skip_reason) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (datetime.now().isoformat(timespec="seconds"), session_id, model,
                 float(cost_usd or 0.0), int(candidates), int(personas),
                 int(window_chars), t0, t1, 1 if provisional else 0,
                 1 if ok else 0, skip_reason),
            )
        return True
    except sqlite3.Error:
        return False
    finally:
        c.close()


def observations(session_id: Optional[str] = None,
                 db_path: Optional[Union[str, Path]] = None) -> List[Dict[str, Any]]:
    """후보(분자) 조회 — 오탐률 실측(§15)·테스트용. 실패 시 빈 리스트."""
    return _rows("facilitation_log", session_id, db_path)


def triages(session_id: Optional[str] = None,
            db_path: Optional[Union[str, Path]] = None) -> List[Dict[str, Any]]:
    """트리아지 시도(분모) 조회 — 건너뜀 사유 포함. 실패 시 빈 리스트."""
    return _rows("facilitation_triage", session_id, db_path)


def _rows(table: str, session_id: Optional[str],
          db_path: Optional[Union[str, Path]]) -> List[Dict[str, Any]]:
    c = _connect(db_path)
    if c is None:
        return []
    try:
        c.row_factory = sqlite3.Row
        if session_id:
            rows = c.execute(
                f"SELECT * FROM {table} WHERE session_id = ? ORDER BY id",
                (session_id,)).fetchall()
        else:
            rows = c.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error:
        return []
    finally:
        c.close()


def delete_session_observations(session_id: str,
                                db_path: Optional[Union[str, Path]] = None) -> int:
    """세션의 관찰 데이터 삭제 — 회의 완전 삭제(purge)가 호출한다.

    `span` 에는 발화 원문 인용(최대 500자)이 들어간다. 회의를 완전 삭제했는데 이
    테이블만 남으면 회의 내용 일부가 DB 에 영구 잔존한다 — `related_notes` 를 함께
    지우는 기존 사이드카 정리 규칙(web/backend/database.purge_session)과 같은 처리다.
    반환: 지운 행 수(두 테이블 합)."""
    c = _connect(db_path)
    if c is None:
        return 0
    n = 0
    try:
        with c:
            for table in ("facilitation_log", "facilitation_triage"):
                try:
                    cur = c.execute(
                        f"DELETE FROM {table} WHERE session_id = ?", (session_id,))
                    n += cur.rowcount or 0
                except sqlite3.Error:
                    pass          # 테이블 없는 구버전 DB — 지울 것도 없다
        return n
    except sqlite3.Error:
        return n
    finally:
        c.close()


def report(session_id: Optional[str] = None,
           db_path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
    """관찰 데이터 집계 — CLI `facilitation-report` 와 M2 게이트 판정의 입력.

    precision(정밀도) 자체는 여기서 내지 않는다. 그건 후보를 종료 후 finalize
    사실검증과 **사람이 대조**해 참/거짓을 라벨링한 뒤에만 나오는 수치이고, 근거 없는
    자동 판정을 넣으면 이 리포의 '실측 없는 휴리스틱 금지' 원칙에 걸린다. 여기서는
    그 라벨링에 필요한 분자·분모·중복·건너뜀을 정직하게 센다.
    """
    obs = observations(session_id, db_path)
    trg = triages(session_id, db_path)
    called = [t for t in trg if not (t.get("skip_reason") or "")]
    skipped = [t for t in trg if (t.get("skip_reason") or "")]
    per_persona: Dict[str, Dict[str, Any]] = {}
    for o in obs:
        k = str(o.get("persona") or "")
        d = per_persona.setdefault(k, {"candidates": 0, "repeats": 0,
                                       "provisional": 0, "need_search": 0})
        d["candidates"] += 1
        d["repeats"] += int(o.get("repeats") or 0)
        d["provisional"] += 1 if o.get("provisional") else 0
        d["need_search"] += 1 if o.get("need_search") else 0
    skip_counts: Dict[str, int] = {}
    for t in skipped:
        r = str(t.get("skip_reason") or "")
        skip_counts[r] = skip_counts.get(r, 0) + 1
    return {
        "sessions": sorted({str(t.get("session_id") or "") for t in trg}
                           | {str(o.get("session_id") or "") for o in obs}),
        "triage_attempts": len(trg),
        "triage_called": len(called),
        "triage_skipped": len(skipped),
        "triage_failed": sum(1 for t in called if not t.get("ok")),
        "triage_empty": sum(1 for t in called
                            if t.get("ok") and not int(t.get("candidates") or 0)),
        "cost_usd": round(sum(float(t.get("cost_usd") or 0.0) for t in trg), 6),
        "candidates": len(obs),
        "candidate_repeats": sum(int(o.get("repeats") or 0) for o in obs),
        "provisional_candidates": sum(1 for o in obs if o.get("provisional")),
        "by_persona": per_persona,
        "skip_reasons": skip_counts,
    }


def effective_triage_model(configured: str) -> str:
    """**실제로 과금될** 트리아지 모델. 설정에서 고른 값과 다를 수 있다.

    `llm_client.chat` 의 model 파라미터는 GPT 전용이다 — claude-* 를 고르면
    preferred="claude" 로 라우팅되지만 실제 모델은 `models.claude_model` 을 따른다
    (Claude 모델 오버라이드는 llm_client 확장이 필요해 M1 몫). 초기 구현은 이 대체를
    주석·설정 설명에만 적고 **단가는 고른 모델(haiku)로 계산**해서, 실제 opus 호출의
    1/12 로 추정했다 — 표시 금액과 실제 과금이 갈라지는 것은 CLAUDE.md 가 금지하는
    바로 그것이다. 추정·한도·기록·로그가 모두 이 함수의 값을 쓴다.

    남는 한계 `[미검증]`: `chat()` 은 한쪽 벤더가 실패하면 **다른 벤더로 조용히
    폴백**하고(gpt→claude, claude→gpt) 어느 쪽이 실제로 응답했는지 돌려주지 않는다.
    그 회차의 단가는 실제와 다를 수 있다. 폴백 감지는 llm_client 가 사용 벤더를
    반환하도록 바꿔야 하므로 별건이며, 상시 경로가 아니라 예외 경로다.
    """
    m = str(configured or "").strip()
    if m.lower().startswith("claude"):
        return str(_c("models.claude_model", "claude-opus-4-8")
                   or "claude-opus-4-8")
    return m


def _call_llm(model: str, system: str, user: str,
              max_tokens: Optional[int] = None) -> str:
    """트리아지 LLM 1회 호출 — 반드시 common.llm_client 경유(우회 금지, PRD §7).

    max_tokens 기본값은 pricing 의 추정 상한과 **같은 상수**다 — 호출 상한과 추정이
    갈라지면 비용이 조용히 추정을 넘는다.
    """
    from meeting_minutes_app.common import pricing
    from meeting_minutes_app.common.llm_client import LLMClient
    if max_tokens is None:
        max_tokens = pricing.FACILITATION_TRIAGE_MAX_OUTPUT_TOKENS
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
        # 설정값과 실제 과금 모델은 다를 수 있다 — 추정·한도·기록·로그는 전부
        # 실효 모델을 쓴다(effective_triage_model 독스트링 참조).
        self._configured_model = str(_c("facilitation.triage_model", "gpt-4o-mini")
                                     or "gpt-4o-mini")
        self._triage_model = effective_triage_model(self._configured_model)
        self._meeting_cap = float(_c("facilitation.max_cost_usd_per_meeting", 0.50)
                                  or 0.0)

        self._lock = threading.Lock()
        self._window: List[Utterance] = []
        self._last_triage: Optional[float] = None   # None = 첫 발화에서 즉시 1회
        self._session_cost = 0.0
        self._triage_count = 0
        self._observed_count = 0
        self._repeat_count = 0
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
            "repeat_count": self._repeat_count,
            "session_cost_usd": round(self._session_cost, 6),
            "skip_reason": self._skip_reason,
        }

    # ── 핫패스 API (RealtimeVaultSearcher 와 동일 계약) ────

    def offer_segment(self, text: str, t0: Optional[float] = None,
                      t1: Optional[float] = None,
                      provisional: bool = False) -> None:
        """세그먼트 확정 시 호출. 절대 블로킹하지 않고, 절대 예외를 전파하지 않는다.

        t0/t1(발화 구간 초)·provisional(보정 전 조각 여부)은 관찰 로그에 그대로
        남는다 — 종료 후 전사와 대조할 좌표가 없으면 실측이 인용 문자열 검색에
        의존한다. 넘기지 않아도 동작한다(구 호출부 호환).

        시간 게이트에 맞으면 트리아지를 풀에 제출한다. 게이트 시각은 제출 시점에
        선점한다 — 판정 완료를 기다리면 그 사이 세그먼트들이 중복 제출된다."""
        try:
            if self._pool is None or not text or not text.strip():
                return
            with self._lock:
                self._window.append(
                    Utterance(text.strip(), t0, t1, bool(provisional)))
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
                window = list(self._window)
                # 보낸 창은 비우고 마지막 1건만 문맥 다리로 남긴다 — 비우지 않으면
                # 같은 발화가 창에 남아 회차마다 다시 판정된다.
                self._window = window[-WINDOW_CARRYOVER:] if WINDOW_CARRYOVER else []
            self._pool.submit(self._triage_task, window, active)
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
        kept: List[Utterance] = []
        for u in reversed(self._window):
            total += len(u.text)
            if total > TRIAGE_WINDOW_CHARS and kept:
                break
            kept.append(u)
        self._window = list(reversed(kept))

    @staticmethod
    def _window_meta(window: List[Utterance]) -> Tuple[str, Optional[float],
                                                       Optional[float], bool]:
        """창 → (프롬프트 텍스트, 구간 시작, 구간 끝, provisional 포함 여부)."""
        text = "\n".join(u.text for u in window)
        t0s = [u.t0 for u in window if u.t0 is not None]
        t1s = [u.t1 for u in window if u.t1 is not None]
        return (text,
                min(t0s) if t0s else None,
                max(t1s) if t1s else None,
                any(u.provisional for u in window))

    def _triage_task(self, window: List[Utterance],
                     active: List[Persona]) -> None:
        """Tier 0 트리아지 1회 — 실패는 전부 무시(실시간 스트림 보호).

        비용 3관문(자동 일시정지 → 월 한도 → 회의당 캡)을 지나야만 LLM 을 부른다.
        어느 관문에 막혔든 `facilitation_triage` 에 사유가 남는다 — 조용히
        건너뛰면 나중에 데이터가 왜 비었는지 알 수 없다.

        회의당 캡은 **예약(reserve) 방식**이다: 검사와 누적을 락 안에서 한 번에 하고
        호출이 실패하면 환불한다. 풀이 2워커라 검사→호출→누적 순이면 두 워커가 같은
        잔액을 보고 함께 통과해 캡을 넘길 수 있다(트리아지가 주기보다 오래 걸릴 때 —
        이 리포엔 실시간 STT 타임아웃 전례가 있다)."""
        window_text, t0, t1, provisional = self._window_meta(window)
        est = 0.0
        reserved = False
        try:
            from meeting_minutes_app.common import pricing, spend_guard

            def _skip(reason: str) -> None:
                self._skip_reason = reason
                record_triage(self.session_id, model=self._triage_model,
                              personas=len(active),
                              window_chars=len(window_text), t0=t0, t1=t1,
                              provisional=provisional, skip_reason=reason)

            if spend_guard.automation_paused():
                _skip("자동 실행 일시정지로 트리아지 보류")
                return
            est = pricing.facilitation_triage_call_cost(self._triage_model)
            # per-item 한도는 '오디오 1건' 규칙 — 트리아지 1회는 그 대상이 아니다
            # (임베딩 _embedding_budget_blocked 와 같은 이유).
            reason = spend_guard.blocked(est, check_per_item=False)
            if reason:
                _skip(reason)
                return
            with self._lock:
                if (self._meeting_cap > 0
                        and self._session_cost + est > self._meeting_cap):
                    over = self._session_cost
                else:
                    self._session_cost += est          # 예약
                    reserved = True
                    over = None
            if not reserved:
                _skip(f"이 회의의 facilitation 비용 ${over:.4f}이 "
                      f"회의당 캡 ${self._meeting_cap:.2f}에 도달")
                return

            raw = _call_llm(self._triage_model, *self._build_triage_prompt(
                window_text, active))

            # 기록은 파싱 성공 여부와 무관 — LLM 은 이미 호출됐다(과금 발생).
            spend_guard.record(
                spend_guard.KIND_FACILITATION, est, model=self._triage_model,
                units=1, unit_kind="triage_call",
                note=spend_guard.session_note(self.session_id))
            with self._lock:
                self._triage_count += 1
            self._skip_reason = ""

            cands = self._parse_candidates(raw, active)
            for cand in cands:
                # M0 관찰모드: DB 로깅만. on_intervention(화면 채널)은 호출하지 않는다.
                r = record_observation(
                    self.session_id, cand["persona"],
                    trigger_type=cand.get("trigger_type", ""),
                    confidence=cand.get("confidence", 0.0),
                    span=cand.get("span", ""),
                    need_search=bool(cand.get("need_search")),
                    level=self.persona_level(cand["persona"]),
                    t0=t0, t1=t1, provisional=provisional)
                with self._lock:
                    if r == "new":
                        self._observed_count += 1
                    elif r == "repeat":
                        self._repeat_count += 1
            record_triage(self.session_id, model=self._triage_model, cost_usd=est,
                          candidates=len(cands), personas=len(active),
                          window_chars=len(window_text), t0=t0, t1=t1,
                          provisional=provisional, ok=True)
        except Exception as e:
            # LLM 실패(양 벤더 모두 실패 시 llm_client 가 raise)·파싱 예외 —
            # 시도 자체는 분모에 남긴다. ok=0 이 "호출했지만 결과 없음"을 뜻한다.
            # 예약분은 환불한다 — 실패한 호출로 캡을 소진시키면 남은 회의 내내
            # 트리아지가 막힌다(과금은 record() 를 지나지 않았으므로 집계에도 없다).
            if reserved:
                with self._lock:
                    self._session_cost = max(0.0, self._session_cost - est)
            try:
                record_triage(self.session_id, model=self._triage_model,
                              cost_usd=0.0, personas=len(active),
                              window_chars=len(window_text), t0=t0, t1=t1,
                              provisional=provisional, ok=False,
                              skip_reason="")
                self._skip_reason = f"트리아지 실패: {type(e).__name__}"
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
        """트리아지 응답 파싱 — 활성 페르소나 키가 아닌 항목(환각)은 버린다.

        confidence 임계 필터는 M0 에 넣지 않는다 — 관찰모드의 목적이 임계값을
        **실측으로 정하는 것**이고, 그 전에 임의 상수로 후보를 걸러내면 정할 근거가
        사라진다(이 리포의 '실측 없는 랭킹 휴리스틱 금지' 원칙). 임계는 M1 에서
        분포를 보고 페르소나별로 정한다."""
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


# ── CLI: 관찰 로그 리포트 (`meeting-minutes facilitation-report`) ──────────

def _print_report(session_id: Optional[str], detail: bool) -> None:
    from meeting_minutes_app.common import spend_guard
    r = report(session_id)
    path = _resolve_db_path()
    print("회의 진행 페르소나 관찰 로그 (M0 관찰모드)")
    print(f"  DB: {path}")
    if session_id:
        print(f"  세션: {session_id}")
    elif r["sessions"]:
        print(f"  세션 {len(r['sessions'])}건: "
              + ", ".join(s or "(없음)" for s in r["sessions"][:10])
              + (" …" if len(r["sessions"]) > 10 else ""))
    if not r["triage_attempts"] and not r["candidates"]:
        print("\n기록이 없습니다. 확인 순서:")
        print("  1) config.json  facilitation.enabled = true 인가")
        print("  2) 그 뒤에 실시간 녹음(웹 UI)을 했는가 — CLI 녹음은 대상이 아니다")
        print("  3) 발화가 있었는가(침묵 구간에는 트리아지가 돌지 않는다)")
        return
    print(f"\n[분모] 트리아지 시도 {r['triage_attempts']}회 "
          f"= 호출 {r['triage_called']} + 건너뜀 {r['triage_skipped']}")
    print(f"        호출 중 실패 {r['triage_failed']} · 후보 0건 {r['triage_empty']}")
    print(f"        비용 ${r['cost_usd']:.6f}  "
          f"(월 합계 kind={spend_guard.KIND_FACILITATION} 와 같은 금액)")
    if r["skip_reasons"]:
        print("        건너뜀 사유:")
        for reason, n in sorted(r["skip_reasons"].items(), key=lambda kv: -kv[1]):
            print(f"          {n:>4}회  {reason}")
    print(f"\n[분자] 후보 {r['candidates']}건 "
          f"(중복 재판정 {r['candidate_repeats']}회는 행에 합산) · "
          f"보정 전 조각 기반 {r['provisional_candidates']}건")
    if r["by_persona"]:
        print("        페르소나별:")
        for k, d in sorted(r["by_persona"].items(),
                           key=lambda kv: -kv[1]["candidates"]):
            print(f"          {k:<16} 후보 {d['candidates']:>3} · "
                  f"중복 {d['repeats']:>3} · 조각기반 {d['provisional']:>3} · "
                  f"검색요청 {d['need_search']:>3}")
    print("\n정밀도(precision)는 여기서 계산하지 않습니다 — 아래 후보를 종료 후 "
          "사실검증·회의록과\n사람이 대조해 참/거짓을 라벨링해야 나오는 수치입니다"
          "(PRD §15 M2 진입 게이트).")
    if detail:
        print("\n── 후보 상세 ──")
        for o in observations(session_id):
            t = ""
            if o.get("t0") is not None:
                t = f" [{float(o['t0']):.0f}~{float(o.get('t1') or 0):.0f}s]"
            flag = " (조각)" if o.get("provisional") else ""
            rep = f" ×{1 + int(o.get('repeats') or 0)}" if o.get("repeats") else ""
            print(f"  {o.get('ts','')} {o.get('persona','')}{rep}"
                  f" conf={float(o.get('confidence') or 0):.2f}{t}{flag}")
            print(f"      {o.get('trigger_type','')}: {o.get('span','')}")
    else:
        print("후보 인용문을 함께 보려면: --detail")


def _force_utf8_console() -> None:
    """한국어 콘솔(cp949)에서 '—' 같은 문자가 UnicodeEncodeError 를 내는 것을 막는다.

    이 모듈은 웹 서버에서도 import 되므로 다른 CLI 모듈들처럼 모듈 최상단에서 하지
    않고 main() 안에서만 만진다 — 라이브러리 import 가 프로세스 전역 스트림을
    바꾸면 안 된다(pythonw 에서는 stdout 이 없을 수도 있다)."""
    import sys
    for s in (sys.stdout, sys.stderr):
        enc = getattr(s, "encoding", None)
        if enc and enc.lower() in ("cp949", "euc-kr", "ansi"):
            try:
                s.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    _force_utf8_console()
    ap = argparse.ArgumentParser(
        prog="meeting-minutes facilitation-report",
        description="회의 진행 페르소나(M0 관찰모드) 관찰 로그 집계 — 오탐률 실측용")
    ap.add_argument("--session", default=None, help="세션 ID 로 한정")
    ap.add_argument("--detail", action="store_true", help="후보 인용문까지 출력")
    args = ap.parse_args(argv)
    _print_report(args.session, args.detail)
    return 0


if __name__ == "__main__":       # python -m meeting_minutes_app.wiki_core.facilitation
    raise SystemExit(main())
