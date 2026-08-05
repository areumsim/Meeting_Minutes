"""facilitation.py — 회의 진행 페르소나 오케스트레이터 (M1 — 관찰 + 옆 카드 개입)
=====================================================================
실시간 전사 세그먼트 위에서 페르소나(personas.py) 후보 판정을 도는 오케스트레이터.
PRD: docs/prd/PRD_회의진행_페르소나에이전트_20260803.md (§5 파이프라인, §10 비용, §13).

현재 범위 — **참견도(level)가 무엇이 화면에 나가는지를 정한다.**
  - Tier 0 트리아지(경량 모델 1회)는 참견도 1 이상인 모든 페르소나에 대해 돌고,
    판정은 **참견도와 무관하게 항상** DB 에 기록된다. 종료 후 finalize 사실검증과
    대조해 페르소나별 오탐률을 실측하기 위한 데이터다(§15).
  - Tier 1 생성(`_generate`)과 WS `facilitation` 이벤트는 참견도 2 이상에서 돈다.
    3 이상이면 옆 카드로 자동 표시하고, 2 는 [지금 점검]을 누를 때 모아서 낸다.
    주기 페르소나(`summarizer`)의 중간 요약도 같은 경계를 쓴다.
  - **위험 페르소나는 여전히 화면에 열려 있지 않다.** 팩트체커·비판자는
    `hard_cap=2` 라 설정으로도 자동 표시(3)까지 올릴 수 없다 — 오탐률 실측(§15)을
    통과해야 M2 에서 연다. 이 상한은 코드가 강제한다(`persona_level`).
  - **아직 없는 것**: 라이브 웹검색 근거(개입의 `searched` 는 항상 False, M2) ·
    음성/알림음(참견도 4·5, `facilitation.max_level` 기본 3 으로 막힘, M3).

  M0(관찰 전용) 시절의 "화면에 아무것도 내보내지 않는다"는 이 문서 자리에 오래
  남아 있었다. 코드가 M1 로 간 뒤에도 헤더만 M0 였고, 그 사이 이 파일을 처음 여는
  사람은 정반대로 이해했다 — 문서-코드 불일치는 이 리포가 버그와 같은 급으로
  다루는 결함이다.

**관찰 로그는 두 테이블이다 — 이유가 있다.**
  - `facilitation_log`  = 후보(candidate) 1건 = 오탐률의 **분자**.
  - `facilitation_triage` = 트리아지 시도 1건 = **분모**. 후보 0건이었던 회차와
    비용 관문에 막힌 회차(`skip_reason`)까지 남긴다.
  분모가 없으면 "기능이 안 돌았다"와 "돌았지만 후보가 없었다"를 구분할 수 없어 precision
  을 계산할 수 없다 — 이 기능의 M2 진입 게이트가 그 수치인데 초기 구현이 후보만
  남겨 계산이 불가능했다. 조회는 `report()` / CLI `facilitation-report`.

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
    대조할 키가 없고, 키 없는 관찰 로그는 오탐률 대조 실측에 쓸 수 없다.
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
    import 하지 않는 구조를 유지하고, (b) kind 별 분리 실측이 필요하며,
    (c) 두 곳에 다 적으면 월 합계에 이중 집계된다. 대신 `note` 에 세션 키를
    `spend_guard.session_note()` 규약으로 남겨 세션별 실제 금액을 되찾을 수 있게 한다.
  - 개입 생성(`_generate`)·중간 요약(`_brief_task`)도 **같은 3관문**을 지난다.
    트리아지보다 비싼 모델을 쓰므로 여기가 빠지면 회의당 캡이 무의미해진다.
  - **호출이 끝났으면 결과와 무관하게 기록한다.** 파싱 실패·빈 응답이어도 돈은
    나갔다 — 그때 환불하면 실제 지출이 월 합계에서 사라진다(세 경로 모두 이 순서).

**돈을 쓰기 전에 "이 산출물을 볼 사람이 있는가"를 먼저 묻는다.** 같은 판정이 네 곳에
있고 전부 같은 이유다 — 아무도 볼 수 없는 개입에 Tier 1 비용을 쓰는 것은 순손실이다:
  화면 채널 없음(리플레이·헤드리스) · 사용자가 껐음(`mute()`) · 개입 예산 소진 ·
  참견도 미달. 새 생성 경로를 추가할 때 이 네 게이트를 지나게 한다 — 요약 경로가
  '채널 없음'을 빠뜨려 리플레이 견적을 넘긴 전례가 있다.

참견도(§4) — 실효값은 config `facilitation.personas.<key>.level` 이 정본.
  0(금지)  = 트리아지 입력에서 제외 → 진짜 0 비용(테스트 고정).
  1(관찰)  = 판정을 DB에만 기록, 화면에 내지 않는다. config 키가 **없을 때의 폴백**
             이기도 하다(`OBSERVE_LEVEL`). 다만 `config.example.json` 은 저위험
             4종 + 중간 요약을 3 으로 시드하므로, 정상 설치의 기본은 1 이 아니다.
  2(소극)  = 개입을 생성해 두고 [지금 점검]을 누를 때 방출.
  3(표준)  = 옆 카드로 자동 표시(무음).
  4·5      = 알림음·음성 — **미구현**. `facilitation.max_level`(기본 3)이 막는다.
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

from meeting_minutes_app.wiki_core.personas import (
    BRIEF_PERSONA, EV_REGISTRY, EV_VAULT, EV_WEB, Persona, all_personas,
    get_persona, triage_personas)

try:
    from meeting_minutes_app.common import config_loader as _cfg
    _cfg_ok = True
except ImportError:
    _cfg = None  # type: ignore
    _cfg_ok = False


def _c(key: str, default: Any = None) -> Any:
    return _cfg.get(key, default) if _cfg_ok else default


def _int_conf(key: str, default: int) -> int:
    """정수 설정값 — 잘못 적힌 값(빈 문자열·문자)은 기본값으로 흡수한다.

    설정 파일은 사람이 손으로 고치는 파일이라 타입이 틀릴 수 있고, 그때 실시간
    스트림이 죽으면 안 된다."""
    try:
        return int(_c(key, default))
    except (TypeError, ValueError):
        return default


#: 관찰(silent) 참견도 — **config 키가 없을 때의 폴백**.
#: PRD §3 로스터 권장값(personas.Persona.default_level)이 아니라 이 값으로 떨어지는
#: 것은 의도다 — 설정에 적히지 않은 페르소나를 화면에 열지 않는다(보수적 기본).
#: 정상 설치의 실제 기본값은 이 값이 아니라 `config.example.json` 의 시드다
#: (저위험 4종 + 중간 요약 = 3, 위험·중위험 4종 = 1). 두 값을 혼동하면
#: "기본이 전원 관찰"이라는 틀린 전제로 비용·표시를 판단하게 된다.
OBSERVE_LEVEL = 1

#: 트리아지 프롬프트에 넣는 최근 발화 창의 최대 길이(자).
#: PRD §10 비용 추정의 근거 값 — 늘리면 pricing 의
#: FACILITATION_TRIAGE_INPUT_TOKENS 도 같이 늘려야 추정과 실사용이 안 갈라진다.
TRIAGE_WINDOW_CHARS = 2000

#: 트리아지 후 창에 남기는 최근 세그먼트 수(문맥 다리). 0 이면 문장이 창 경계에서
#: 잘려 후보를 놓치고, 크게 잡으면 같은 발화를 반복 판정한다 — 1 이 그 절충이며
#: 남은 중복은 span_hash dedup 이 흡수한다.
WINDOW_CARRYOVER = 1

#: 화면 개입이 시작되는 참견도. 0=금지, 1=관찰(기록만), 2=소극([지금 점검] 때 모아서),
#: 3 이상=표준(자동 옆 카드). PRD §4 의 채널 표가 정본이고 이 상수는 그 경계다.
DISPLAY_LEVEL = 3
COLLECT_LEVEL = 2

#: 트리아지 1회가 만들 수 있는 화면 개입 수 상한. 한 번에 여러 장이 쏟아지면
#: '흘깃 보고 넘긴다'(§19.1)가 성립하지 않는다 — 나머지는 기록만 남는다.
MAX_INTERVENTIONS_PER_TRIAGE = 2

#: 중간 요약(summarizer)에 넣는 '마지막 요약 이후 발화' 상한. 전체 전사를 매번 넣지
#: 않는 이유는 비용이 회의 길이에 제곱으로 늘기 때문이다(음성브리핑 PRD FR-A2) —
#: 대신 이전 요약 1개를 이어받아 누적한다.
BRIEF_WINDOW_CHARS = 4000

#: [지금 정리] 연타로 과금이 쌓이는 것을 막는 최소 간격(초). 주기 요약과 달리
#: 사용자가 직접 누르는 경로라 게이트가 없으면 클릭 수 = 과금 수가 된다.
BRIEF_MIN_GAP_SEC = 20.0

#: 중간 요약을 만든 계기 — 관찰 로그 `trigger_type` 에 그대로 남는다(실측에서
#: 요약 행을 개입 후보와 분리해 세는 근거).
TRIGGER_BRIEF_PERIODIC = "periodic_brief"
TRIGGER_BRIEF_ON_DEMAND = "brief_now"


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
    skip_reason  TEXT,
    note         TEXT
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
    "feedback": "TEXT",
    "feedback_ts": "TEXT",
}

#: 사람이 카드에 남기는 라벨(M1). 이 두 값이 §15 오탐률 실측의 **사람 라벨링 채널**이다 —
#: 회의가 끝난 뒤 따로 라벨링 작업을 하지 않아도, 회의 중 누른 버튼이 데이터가 된다.
#: "확인"은 도움이 됐다(참), "닫기"는 필요 없었다(오탐 후보)로 읽는다. 다만 닫기는
#: '틀렸다'와 '맞지만 지금은 불필요하다'가 섞여 있어 그 자체로 precision 이 아니다 —
#: report() 가 비율만 세고 판정하지 않는 이유다(실측 없는 자동 판정 금지).
FEEDBACK_ACK = "ack"
FEEDBACK_DISMISS = "dismiss"
FEEDBACK_LABELS = (FEEDBACK_ACK, FEEDBACK_DISMISS)
#: facilitation_triage 에 나중에 추가된 컬럼(리플레이 표시용).
_TRIAGE_COLUMNS = {"note": "TEXT"}

#: 리플레이로 만들어진 행의 `note` 값. 실측에서 **라이브 판정과 섞어 세면 안 된다** —
#: 리플레이는 보정이 끝난 확정 전사를 보므로 조각 전사를 보는 라이브보다 유리하다
#: (그래서 리플레이 precision 은 상한이다).
NOTE_REPLAY = "replay"


def _resolve_db_path(db_path: Optional[Union[str, Path]] = None) -> Optional[Path]:
    """경로 해석은 `common.sqlite_util` 하나만 쓴다(이 함수가 두 모듈에 복제돼 있던
    자리). 래퍼를 남기는 이유는 테스트가 이 이름을 monkeypatch 해 임시 DB 로
    돌리기 때문이다 — 없애면 그 주입 지점이 사라진다(tests/conftest.py 격리)."""
    from meeting_minutes_app.common import sqlite_util
    return sqlite_util.resolve_db_path(db_path)


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
    sqlite_util.ensure_columns(c, "facilitation_triage", _TRIAGE_COLUMNS)


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


#: 중간 요약 절 제목 — 화면 카드와 회의록·로그 텍스트가 같은 말을 쓰게 한다.
BRIEF_SECTIONS = (("points", "논점"), ("decisions", "결정"),
                  ("actions", "액션"), ("open_questions", "미결 질문"))


def _brief_to_text(brief: Dict[str, List[str]]) -> str:
    """요약 dict → 사람이 읽는 한 덩어리(관찰 로그 span·카드 접힘 줄·다음 요약 입력).

    화면은 `brief` 를 절별로 렌더하지만, 로그·프롬프트·구버전 프런트는 문자열이
    필요하다 — 두 표현이 갈라지지 않게 여기 한 곳에서 만든다."""
    lines: List[str] = []
    for key, label in BRIEF_SECTIONS:
        vals = brief.get(key) or []
        if vals:
            lines.append(f"[{label}] " + " / ".join(vals))
    return "\n".join(lines)


def record_feedback(session_id: str, persona: str, span_hash: str, label: str,
                    db_path: Optional[Union[str, Path]] = None) -> bool:
    """카드에 남긴 사람 라벨을 그 후보 행에 적는다(확인/닫기). 반환: 기록됐는지.

    새 테이블을 만들지 않고 후보 행을 갱신하는 이유: 라벨은 후보 1건의 속성이고,
    별도 테이블로 두면 완전 삭제(purge)에서 빠지는 사이드카가 하나 더 생긴다
    (이 리포에서 실제로 겪은 프라이버시 구멍이다 — CLAUDE.md 사이드카 규칙).

    같은 카드를 두 번 누르면 마지막 값이 남는다. 실패해도 예외를 올리지 않는다 —
    라벨은 부수 효과이고, 실시간 스트림이 이것 때문에 끊기면 안 된다."""
    if label not in FEEDBACK_LABELS:
        return False
    c = _connect(db_path)
    if c is None:
        return False
    try:
        with c:
            _ensure(c)
            cur = c.execute(
                "UPDATE facilitation_log SET feedback = ?, feedback_ts = ? "
                "WHERE session_id = ? AND persona = ? AND span_hash = ?",
                (label, datetime.now().isoformat(timespec="seconds"),
                 session_id, persona, span_hash))
            return bool(cur.rowcount)
    except sqlite3.Error:
        return False
    finally:
        c.close()


def record_triage(session_id: str, *, model: str = "", cost_usd: float = 0.0,
                  candidates: int = 0, personas: int = 0,
                  window_chars: int = 0, t0: Optional[float] = None,
                  t1: Optional[float] = None, provisional: bool = False,
                  ok: bool = False, skip_reason: str = "", note: str = "",
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
                " window_chars, t0, t1, provisional, ok, skip_reason, note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (datetime.now().isoformat(timespec="seconds"), session_id, model,
                 float(cost_usd or 0.0), int(candidates), int(personas),
                 int(window_chars), t0, t1, 1 if provisional else 0,
                 1 if ok else 0, skip_reason, note),
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
    all_obs = observations(session_id, db_path)
    # 중간 요약 행은 **개입 후보가 아니다** — 오탐률의 분자에 섞으면 수치가 오염된다
    # (요약은 판정이 아니라 정리이고, 주기로 반드시 생성된다).
    obs = [o for o in all_obs if str(o.get("persona") or "") != BRIEF_PERSONA]
    brief_obs = [o for o in all_obs if str(o.get("persona") or "") == BRIEF_PERSONA]
    trg = triages(session_id, db_path)
    called = [t for t in trg if not (t.get("skip_reason") or "")]
    skipped = [t for t in trg if (t.get("skip_reason") or "")]
    per_persona: Dict[str, Dict[str, Any]] = {}
    for o in obs:
        k = str(o.get("persona") or "")
        d = per_persona.setdefault(k, {"candidates": 0, "repeats": 0,
                                       "provisional": 0, "need_search": 0,
                                       "ack": 0, "dismiss": 0})
        d["candidates"] += 1
        d["repeats"] += int(o.get("repeats") or 0)
        d["provisional"] += 1 if o.get("provisional") else 0
        d["need_search"] += 1 if o.get("need_search") else 0
        fb = str(o.get("feedback") or "")
        if fb == FEEDBACK_ACK:
            d["ack"] = d.get("ack", 0) + 1
        elif fb == FEEDBACK_DISMISS:
            d["dismiss"] = d.get("dismiss", 0) + 1
    skip_counts: Dict[str, int] = {}
    for t in skipped:
        r = str(t.get("skip_reason") or "")
        skip_counts[r] = skip_counts.get(r, 0) + 1
    # 라이브(조각 전사)와 리플레이(확정 전사) 판정은 품질 조건이 달라 **섞어 세면
    # 안 된다** — 합계와 함께 출처별 수치를 같이 돌려준다.
    replay_obs = [o for o in obs if (o.get("note") or "") == NOTE_REPLAY]
    replay_trg = [t for t in trg if (t.get("note") or "") == NOTE_REPLAY]
    return {
        "replay": {"triage_attempts": len(replay_trg),
                   "candidates": len(replay_obs)},
        "live": {"triage_attempts": len(trg) - len(replay_trg),
                 "candidates": len(obs) - len(replay_obs)},
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
        # 중간 요약(summarizer)은 후보와 분리해 센다 — 같은 테이블에 있지만 성질이 다르다.
        "briefs": len(brief_obs),
        "briefs_on_demand": sum(1 for o in brief_obs
                                if (o.get("trigger_type") or "")
                                == TRIGGER_BRIEF_ON_DEMAND),
        "candidate_repeats": sum(int(o.get("repeats") or 0) for o in obs),
        "provisional_candidates": sum(1 for o in obs if o.get("provisional")),
        # 회의 중 카드에 남긴 사람 라벨(§19.4). 화면에 뜬 개입만 라벨될 수 있으므로
        # 분모는 candidates 가 아니라 'shown' 이다 — 여기서는 세지 않고(오케스트레이터
        # 메모리 값이라 DB 에 없다) 라벨 수만 정직하게 센다.
        # 라벨은 요약 카드에도 붙을 수 있으므로 전체 행(all_obs)에서 센다.
        "feedback_ack": sum(1 for o in all_obs
                            if (o.get("feedback") or "") == FEEDBACK_ACK),
        "feedback_dismiss": sum(1 for o in all_obs
                                if (o.get("feedback") or "") == FEEDBACK_DISMISS),
        "by_persona": per_persona,
        "skip_reasons": skip_counts,
    }


def persona_level(key: str) -> int:
    """실효 참견도 — config 값(기본 1 관찰)을 hard_cap·전역 max_level 로 클램프.

    위험 페르소나는 설정만으로 hard_cap 을 넘길 수 없다(PRD §4 수용 기준).
    오케스트레이터 밖(설정 화면의 참견도 매트릭스)에서도 이 함수를 쓴다 — 화면이
    클램프를 따로 계산하면 "3으로 올렸는데 안 뜬다"가 되고, 그건 이 리포가 반복해서
    없애온 갈라짐이다(단가 표 4곳·노트 판정 2곳 전례)."""
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


def effective_persona_model(key: str) -> str:
    """페르소나 Tier 1 생성 모델(실효값). config > personas.py 기본 > 실효 해석 순.

    claude 계열은 `llm_client` 가 `models.claude_model` 로 호출하므로 그 대체를 여기서
    한 번에 반영한다 — 추정·한도·기록이 실제 과금 모델을 보게 하려면 이 함수뿐이다
    (트리아지의 `effective_triage_model` 과 같은 이유)."""
    p = get_persona(key)
    fallback = p.model if p else "gpt-4o-mini"
    configured = str(_c(f"facilitation.personas.{key}.model", fallback) or fallback)
    return effective_triage_model(configured)


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


# ── 리플레이: 지난 회의의 전사로 관찰 데이터를 만든다 ────────────────────────

def session_segments(session_id: str,
                     db_path: Optional[Union[str, Path]] = None
                     ) -> List[Utterance]:
    """지난 회의의 확정 전사 세그먼트. provisional=False — 보정이 끝난 텍스트다.

    두 곳을 본다. **둘 다 필요하다**:
      1. DB `segments` — 웹 녹음·업로드 세션. `web.backend.database` 를 import 하지 않고
         같은 sqlite 파일을 직접 읽는다(usage_log·graph_db 와 같은 방식).
      2. 없으면 `sessions.output_dir` 의 `*.jsonl` — **CLI 실시간 녹음 산출물**.
         `session_scanner` 가 폴더에서 임포트한 세션은 DB 에 행만 만들고 세그먼트는
         남기지 않으므로(실측: 실볼트 5세션 전부 segments 0건) 1번만 보면 "리플레이할
         전사가 없다"가 된다. 파일에는 `{"type":"segment","start","end","text"}` 로
         남아 있다.
    """
    c = _connect(db_path)
    if c is None:
        return []
    rows: List[Any] = []
    out_dir = ""
    try:
        try:
            rows = c.execute(
                "SELECT text, start_time, end_time FROM segments "
                "WHERE session_id = ? ORDER BY start_time, rowid", (session_id,)
            ).fetchall()
        except sqlite3.Error:
            rows = []
        if not rows:
            try:
                r = c.execute("SELECT output_dir FROM sessions WHERE id = ?",
                              (session_id,)).fetchone()
                out_dir = (r[0] or "") if r else ""
            except sqlite3.Error:
                out_dir = ""
    finally:
        c.close()

    out: List[Utterance] = []
    for text, t0, t1 in rows:
        t = (text or "").strip()
        if t:
            out.append(Utterance(t, float(t0 or 0.0), float(t1 or 0.0), False))
    if out or not out_dir:
        return out
    return _segments_from_jsonl(out_dir)


def _resolve_output_dir(raw: str) -> Optional[Path]:
    """DB 의 output_dir 을 **데이터 베이스 기준**으로 해석한다.

    상대 경로를 CWD 로 풀면 엔트리포인트(웹 런처는 데이터 폴더로 chdir 한다)에 따라
    다른 곳을 가리킨다 — 실제로 갈라져서 완전 삭제가 고아 폴더를 남긴 전례가 있다
    (CLAUDE.md, `api/batch.py`·`web/backend/trash.py` 와 같은 규칙)."""
    if not raw:
        return None
    p = Path(raw)
    if not p.is_absolute():
        try:
            from meeting_minutes_app.common.app_paths import get_output_dir
            base = get_output_dir()
            # output_dir 값이 이미 'output/...' 로 시작할 수 있어 두 후보를 본다.
            for cand in (base / p.name, base.parent / p):
                if cand.exists():
                    return cand
        except Exception:
            return None
    return p if p.exists() else None


def _segments_from_jsonl(output_dir: str) -> List[Utterance]:
    """CLI 실시간 녹음 산출물(`session_*.jsonl`)에서 세그먼트를 읽는다.

    번역이 켜진 회의는 `text` 가 한국어 번역, `text_original` 이 원문이다. 페르소나
    판정은 회의 언어(한국어 UI 기준)로 하므로 `text` 를 우선하고 없으면 원문을 쓴다."""
    import json
    d = _resolve_output_dir(output_dir)
    if d is None or not d.is_dir():
        return []
    out: List[Utterance] = []
    for f in sorted(d.glob("*.jsonl")):
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict) or row.get("type") != "segment":
                    continue
                t = str(row.get("text") or row.get("text_original") or "").strip()
                if not t:
                    continue
                out.append(Utterance(t, float(row.get("start") or 0.0),
                                     float(row.get("end") or 0.0), False))
        except OSError:
            continue
    out.sort(key=lambda u: (u.t0 or 0.0))
    return out


def replay_estimate(segments: List[Utterance], period_sec: float,
                    model: str) -> Dict[str, Any]:
    """리플레이 예상 트리아지 횟수·비용. 실행 **전에** 사용자에게 보여줄 값이다.

    단가는 라이브와 같은 함수(`pricing.facilitation_triage_call_cost`)를 쓴다 —
    리플레이용 추정을 따로 만들면 또 갈라진다.

    트리아지만 세는 것이 맞다: 리플레이는 화면 채널을 주지 않으므로 개입 생성
    (`_dispatch` → `no_channel`)도 중간 요약(`brief_enabled` → 채널 없음)도 돌지
    않는다. 그 두 게이트 중 하나라도 빠지면 이 금액이 실제보다 작아진다 — 실제로
    요약 게이트가 없던 동안 사용자가 승인한 금액보다 더 썼다."""
    from meeting_minutes_app.common import pricing
    if not segments:
        return {"triages": 0, "cost_usd": 0.0, "duration_sec": 0.0}
    span = max(0.0, float(segments[-1].t1 or 0.0) - float(segments[0].t0 or 0.0))
    # 첫 발화에서 1회 + 이후 주기마다 1회(상한). 발화가 드문 구간은 실제로 더 적다.
    triages = 1 + int(span // max(period_sec, 1.0))
    per_call = pricing.facilitation_triage_call_cost(model)
    return {"triages": triages, "cost_usd": round(triages * per_call, 6),
            "duration_sec": round(span, 1)}


def delete_replay_rows(session_id: str,
                       db_path: Optional[Union[str, Path]] = None) -> int:
    """그 세션의 **리플레이 행만** 삭제(라이브 관찰 기록은 건드리지 않는다)."""
    c = _connect(db_path)
    if c is None:
        return 0
    n = 0
    try:
        with c:
            _ensure(c)
            for table in ("facilitation_log", "facilitation_triage"):
                try:
                    n += c.execute(
                        f"DELETE FROM {table} WHERE session_id = ? AND note = ?",
                        (session_id, NOTE_REPLAY)).rowcount or 0
                except sqlite3.Error:
                    pass
    except sqlite3.Error:
        pass
    finally:
        c.close()
    return n


def replay_session(session_id: str, *, db_path: Optional[Union[str, Path]] = None,
                   reset: bool = False,
                   on_progress: Optional[Callable[[int, int], None]] = None
                   ) -> Dict[str, Any]:
    """지난 회의의 전사를 오케스트레이터에 흘려 관찰 데이터를 만든다.

    **왜 필요한가**: 라이브 수집은 새 회의를 5건 녹음할 때까지 기다려야 하는데,
    지난 회의에는 대조 정답(종료 후 finalize 사실검증·회의록)이 **이미 있다**. 오디오를
    다시 전사하지 않으므로 STT 재과금도 없다. **트리아지 LLM 비용만 든다** — 화면
    채널을 주지 않으므로 참견도가 3 이어도 Tier 1 개입 생성(`_dispatch` → `no_channel`)
    도 중간 요약(`brief_enabled` → 채널 없음)도 돌지 않는다. 이 계약이 곧 실행 전에
    보여주는 `replay_estimate()` 금액의 정의다 — 새 생성 경로를 추가할 때 이 게이트를
    빠뜨리면 사용자가 승인한 금액을 넘는다(요약 경로에서 실제로 그랬다).

    **측정상 주의(중요)**: 리플레이는 보정이 끝난 확정 전사를 보므로, 조각 전사를 보는
    라이브보다 유리하다. 여기서 나온 precision 은 **상한**이고 "페르소나 판정 자체의
    품질"을 재는 값이다. 라이브 품질(= STT 노이즈 포함)과 섞어 세면 안 된다 — 그래서
    모든 행에 `note='replay'` 를 남기고 report() 가 분리해 보여준다.

    라이브 경로와 게이트·비용·기록을 **같은 코드**로 지난다. 다른 것은 시계뿐이다
    (clock 주입 — 세그먼트가 즉시 도착하므로 실제 시계로는 트리아지가 1회만 돈다).
    """
    segs = session_segments(session_id, db_path)
    if not segs:
        return {"ok": False, "message": "이 세션에 전사 세그먼트가 없습니다",
                "session_id": session_id, "segments": 0}
    if reset:
        delete_replay_rows(session_id, db_path)

    now = {"t": float(segs[0].t0 or 0.0)}
    orch = FacilitationOrchestrator(
        session_id=session_id, clock=lambda: now["t"], note=NOTE_REPLAY,
        enabled_override=True, db_path=db_path)
    total = len(segs)
    for i, u in enumerate(segs, 1):
        now["t"] = float(u.t1 if u.t1 is not None else u.t0 or 0.0)
        orch.offer_segment(u.text, t0=u.t0, t1=u.t1, provisional=False)
        if on_progress and (i % 20 == 0 or i == total):
            on_progress(i, total)
    orch.shutdown(wait=True)
    st = orch.status()
    return {"ok": True, "session_id": session_id, "segments": total,
            "triages": st["triage_count"], "candidates": st["observed_count"],
            "repeats": st["repeat_count"],
            "cost_usd": st["session_cost_usd"],
            "skip_reason": st["skip_reason"]}


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
    """세그먼트 확정 훅에서 페르소나 트리아지·개입 생성을 도는 논블로킹 헬퍼.

    `on_intervention(dict)` 은 화면 채널이다. 참견도 3 이상이면 생성 즉시,
    2 면 `check_now()`([지금 점검])에서 호출된다. 참견도 1(관찰) 후보는 DB 에만
    남고 이 콜백을 부르지 않는다. 콜백을 주지 않으면(리플레이·헤드리스 측정)
    개입·중간 요약을 **생성하지도 않는다** — 모듈 독스트링의 네 게이트 참조.
    """

    def __init__(self, *, session_id: str = "", topic: str = "",
                 attendees: Optional[List[str]] = None,
                 on_intervention: Optional[Callable[[Dict[str, Any]], None]] = None,
                 clock: Optional[Callable[[], float]] = None,
                 note: str = "", enabled_override: Optional[bool] = None,
                 db_path: Optional[Union[str, Path]] = None,
                 evidence_provider: Optional[Callable[[], List[Dict[str, Any]]]] = None,
                 search_provider: Optional[
                     Callable[[str, int], List[Dict[str, Any]]]] = None,
                 web_provider: Optional[Callable[[], List[Dict[str, Any]]]] = None,
                 on_status: Optional[Callable[[Dict[str, Any]], None]] = None):
        """clock/note/enabled_override 는 **리플레이 전용 주입점**이다(replay_session).

        - clock: 시간 게이트가 읽는 시계. 라이브는 `time.monotonic`, 리플레이는 "지금
          처리 중인 세그먼트의 끝 시각"을 돌려준다 — 게이트 구현을 두 벌로 만들지 않기
          위해서다(리플레이는 세그먼트가 즉시 도착하므로 실제 시계로는 트리아지가
          회의 전체에 1회만 돈다).
        - note: 기록에 붙는 표시. 리플레이 행은 `NOTE_REPLAY` 로 라이브와 구분한다.
        - enabled_override: config 게이트를 무시한다. 리플레이는 사용자가 명시적으로
          부른 측정 명령이므로 기능이 꺼져 있어도 돌아야 한다. **라이브 경로는 절대
          쓰지 않는다**(기본 None → config 게이트 그대로, 테스트로 고정).
        """
        self.session_id = str(session_id or "")
        self.topic = (topic or "").strip()
        self.on_intervention = on_intervention
        #: 볼트 근거 공급자 — `RealtimeVaultSearcher.collected_evidence` 를 그대로
        #: 주입받는다(새 검색기를 만들지 않는다, PRD §6·§7 "절대 새로 만들지 말 것").
        #: wiki_core 가 realtime 세션을 모르게 하려고 호출부에서 넣는다.
        self.evidence_provider = evidence_provider
        #: 후보 발화 1건에 **맞춘** 근거 검색 — `RealtimeVaultSearcher.search_now`.
        #: evidence_provider(세션 전체 누적 상위 N)와 목적이 다르다. 팩트체커에게
        #: "이 수치와 대조할 근거"가 아니라 "요즘 자주 뜬 노트"를 주면 근거처럼
        #: 보이는 무관한 문단으로 검증을 흉내내게 된다. 없으면 누적분으로 폴백.
        self.search_provider = search_provider
        #: 회의 중 이미 나간 웹 검색 결과(`_web_findings`). 근거 소스에 "web" 을 적은
        #: 페르소나(팩트체커)에게만 붙는다. **여기서 웹을 새로 부르지 않는다** —
        #: 웹 호출은 호출부가 이미 한도·기록 3관문을 지나 수행했고, 개입 생성이
        #: 몰래 유료 검색을 한 번 더 하는 경로를 만들면 안 된다.
        self.web_provider = web_provider
        #: 상태 변화(건너뜀 사유·예산 소진)를 화면에 알리는 콜백. 조용히 꺼지면
        #: 기능이 없는 것처럼 보인다(이 리포 반복 규칙).
        self.on_status = on_status
        self._clock = clock or time.monotonic
        self._note = note
        #: 기록 대상 DB. None 이면 모듈 기본(app_paths.get_db_path()). 리플레이가
        #: 다른 데이터 폴더를 가리킬 때만 쓴다.
        self._db_path = Path(db_path) if db_path else None

        self._gate = (bool(_c("facilitation.enabled", False))
                      if enabled_override is None else bool(enabled_override))
        self._period = max(float(_c("facilitation.triage_period_sec", 25) or 25), 1.0)
        # 설정값과 실제 과금 모델은 다를 수 있다 — 추정·한도·기록·로그는 전부
        # 실효 모델을 쓴다(effective_triage_model 독스트링 참조).
        self._configured_model = str(_c("facilitation.triage_model", "gpt-4o-mini")
                                     or "gpt-4o-mini")
        self._triage_model = effective_triage_model(self._configured_model)
        self._meeting_cap = float(_c("facilitation.max_cost_usd_per_meeting", 0.50)
                                  or 0.0)
        #: 세션당 화면 개입 예산(PRD §4). 초과분은 관찰(기록)로 강등된다 —
        #: 회의를 시끄럽게 만들지 않기 위한 장치.
        try:
            self._budget = int(_c("facilitation.max_interventions_per_session", 12))
        except (TypeError, ValueError):
            self._budget = 12
        #: 후보 신뢰도 하한. **페르소나별 상수를 임의로 만들지 않는다** — 실측 전에
        #: 8개 숫자를 지어내면 이 리포가 금지한 '근거 없는 랭킹 휴리스틱'이 된다.
        #: 전역 1개만 두고 사용자가 조절하게 하며, 모든 후보의 confidence 는 관찰
        #: 로그에 남으므로 나중에 분포를 보고 페르소나별로 나눌 수 있다. `[미검증]`
        try:
            self._min_conf = float(_c("facilitation.min_confidence", 0.6))
        except (TypeError, ValueError):
            self._min_conf = 0.6

        self._lock = threading.Lock()
        self._window: List[Utterance] = []
        self._last_triage: Optional[float] = None   # None = 첫 발화에서 즉시 1회
        self._session_cost = 0.0
        self._triage_count = 0
        self._observed_count = 0
        self._repeat_count = 0
        self._skip_reason = ""
        self._shown_count = 0                    # 화면에 낸 개입 수(예산 대비)
        self._pending: List[Dict[str, Any]] = []  # 참견도 2(소극) 대기 — [지금 점검]에서 방출
        #: 사용자가 이번 회의의 카드 표시를 껐는가(`mute()`). 표시만이 아니라
        #: **생성을 멈춘다** — 이유는 mute() 독스트링 참조.
        self._muted = False

        #: 중간 요약(summarizer) 상태. 주기·내용 게이트는 트리아지와 **같은 이유**로
        #: 둔다: 시간만 보면 침묵 구간에 빈 요약이 나가고, 내용만 보면 발화량이 비용을
        #: 정한다(RealtimeVaultSearcher 도 같은 2단 게이트를 쓴다).
        self._brief_period = float(_c("facilitation.brief_period_sec", 600) or 0.0)
        try:
            self._brief_min_chars = int(
                _c("facilitation.brief_min_new_chars", 600))
        except (TypeError, ValueError):
            self._brief_min_chars = 600
        self._brief_buf: List[Utterance] = []    # 마지막 요약 이후 발화
        self._brief_chars = 0                    # 그 글자 수(내용 게이트)
        self._last_brief: Optional[float] = None  # None = 첫 주기가 아직 안 지났음
        self._brief_count = 0
        self._last_brief_text = ""               # 이전 요약(누적 압축용, FR-A2)

        #: 이전 회의 재료(지난 결정·미완료 액션) — 세션 시작 시 **1회** 로드.
        #: 이게 없던 동안 "이전 회의와 다른 내용"을 짚을 **입력 자체가 없었다**:
        #: 종료 후 경로(`meeting_workflow.build_meeting_context`)는 볼트·그래프·registry·
        #: 웹을 전부 조립해 회의록을 쓰는데, 실시간 경로는 registry 참조가 0건이었다.
        #: 모델이 아무리 좋아도 모르는 것을 대조할 수는 없다.
        #: 로드는 JSON 파일 읽기라 비용이 0이고, 실패는 빈 목록으로 흡수한다
        #: (실시간 스트림 보호 — 재료가 없다고 개입 전체가 멈추면 안 된다).
        self._prior_decisions: List[Dict[str, Any]] = []
        self._prior_actions: List[Dict[str, Any]] = []
        if self._gate:
            self._load_prior_context(attendees)

        # 게이트가 꺼져 있으면 풀 자체를 만들지 않는다 — LLM 호출 0회가 구조적으로
        # 보장된다(수용 기준 §14 첫 항목, 테스트 고정).
        self._pool: Optional[ThreadPoolExecutor] = None
        if self._gate:
            self._pool = ThreadPoolExecutor(
                max_workers=2, thread_name_prefix="facilitation")

    # ── 이전 회의 재료 ────────────────────────────────────

    def _load_prior_context(self, attendees: Optional[List[str]]) -> None:
        """지난 결정·미완료 액션을 주제로 걸러 세션 시작 시 1회 담아 둔다.

        로딩·필터는 `wiki_knowledge` 의 공개 진입점 하나만 쓴다 — 회의 준비
        브리핑(prep-brief)과 **같은 함수**다. 여기에 필터를 다시 구현하면 두 경로가
        갈라진다(이 리포가 반복해서 대가를 치른 패턴)."""
        try:
            from meeting_minutes_app.wiki_core import wiki_knowledge as wk
            n_dec = _int_conf("facilitation.context_decisions", 5)
            n_act = _int_conf("facilitation.context_actions", 5)
            self._prior_decisions = wk.recent_decisions_for(self.topic, limit=n_dec)
            self._prior_actions = wk.open_actions_for(
                self.topic, attendees=attendees, limit=n_act)
        except Exception as e:
            # 재료가 없다고 개입 전체가 멈추면 안 된다 — 대조만 못 할 뿐이다.
            print(f"[facilitation] 이전 회의 재료 로드 건너뜀: {e}")
            self._prior_decisions = []
            self._prior_actions = []

    def prior_context_block(self) -> str:
        """트리아지·생성 프롬프트에 넣는 대조용 블록. 재료가 없으면 빈 문자열.

        같은 문자열을 두 프롬프트가 공유한다 — 한쪽만 고치면 "트리아지는 아는데
        생성은 모르는" 상태가 되어 근거 없는 개입이 나간다."""
        parts: List[str] = []
        if self._prior_decisions:
            lines = []
            for d in self._prior_decisions:
                when = str(d.get("created_at", ""))[:10]
                summary = str(d.get("summary", "")).strip()[:160]
                if summary:
                    lines.append(f"- [{when}] {summary}" if when else f"- {summary}")
            if lines:
                parts.append("## 이전 회의에서 정해진 것 (대조용)\n" + "\n".join(lines))
        if self._prior_actions:
            lines = []
            for a in self._prior_actions:
                owner = str(a.get("owner", "")).strip() or "담당 미상"
                title = str(a.get("title", "")).strip()[:160]
                due = str(a.get("due", "")).strip()
                if title:
                    lines.append(f"- {owner}: {title}" + (f" (기한 {due})" if due else ""))
            if lines:
                parts.append("## 아직 끝나지 않은 액션\n" + "\n".join(lines))
        return "\n\n".join(parts)

    # ── 상태 ──────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return self._pool is not None

    def persona_level(self, key: str) -> int:
        """실효 참견도 — 판정과 화면이 같은 함수를 쓴다(`persona_level` 참조)."""
        return persona_level(key)

    def active_personas(self) -> List[Persona]:
        """트리아지 대상 중 참견도 > 0 인 것만 — 0(금지)은 입력에서 제외돼 비용이 0이다.

        주기 페르소나(중간 요약)는 여기 등장하지 않는다(`personas.triage_personas`) —
        후보 판정이 아니라 자체 주기로 돌기 때문이다."""
        return [p for p in triage_personas() if self.persona_level(p.key) > 0]

    def brief_level(self) -> int:
        """중간 요약의 실효 참견도. 2 미만이면 요약을 **만들지 않는다**.

        관찰(1)에 '요약을 기록만 한다'는 상태를 두지 않는 이유: 요약은 오탐 판정의
        대상이 아니라 사용자가 읽어야 의미가 있는 산출물이고, 아무도 보지 않는 요약에
        Tier 1 비용을 쓰는 것은 예산 소진 시 생성을 멈추는 것과 같은 이유로 손실이다."""
        return persona_level(BRIEF_PERSONA)

    def brief_enabled(self) -> bool:
        """중간 요약을 만들 조건 — 화면 채널이 있어야 한다는 조건이 포함된다.

        `on_intervention` 이 없는 호출자(리플레이·헤드리스 측정)에서는 요약을 만들지
        않는다. `_dispatch` 가 개입에 대해 이미 쓰는 판정과 **같은 것**인데
        (`no_channel`), 요약은 `_dispatch` 를 지나지 않아 이 규칙에서 빠져 있었다 —
        그 결과 리플레이가 "트리아지 비용만 든다"는 계약을 어기고, 실행 전에 보여준
        `replay_estimate()` 금액보다 더 쓰면서 아무에게도 보이지 않는 요약을 만들었다.
        """
        return (self.enabled and not self._muted
                and self.on_intervention is not None
                and self._brief_period > 0
                and self.brief_level() >= COLLECT_LEVEL)

    @property
    def muted(self) -> bool:
        return self._muted

    def mute(self) -> None:
        """이번 회의의 화면 개입을 끈다 — **생성 자체를 멈춰 과금을 끊는다**.

        표시만 끄면 안 되는 이유는 이 클래스가 이미 두 번 인정한 것과 같다:
        `_dispatch` 는 화면 채널이 없을 때("no_channel")도, 예산이 소진됐을 때
        ("budget_exhausted")도 **생성을 하지 않는다** — 아무도 볼 수 없는 개입에
        Tier 1 모델 비용을 쓰는 것은 순손실이기 때문이다. 사용자가 끈 회의도 정확히
        그 상태인데, 초기 M1 구현은 프런트에서만 카드를 버려서 서버가 회의 끝까지
        계속 생성하고 과금했다(게다가 러닝 미터는 버려진 카드의 금액을 더하지 않아
        **표시 금액이 실제 과금보다 작아졌다** — 이 리포가 금지하는 바로 그것).

        멈추는 것: Tier 1 개입 생성 · 중간 요약 생성 · 대기 중인 개입.
        계속하는 것: Tier 0 트리아지와 관찰 로그 기록. 오탐률 실측(§15)이 이 기능의
        목적이고, 트리아지는 시간 게이트·회의당 캡으로 이미 상한이 잡혀 있으며
        사용자가 끈 것은 '화면 개입'이지 '측정'이 아니다. 이 구분은 화면 문구에도
        그대로 적는다 — 다르게 적으면 끈 줄 알았던 비용이 남는다.

        되돌리는 함수는 두지 않는다. 껐다 켜기는 새 녹음에서 하면 되고, 세션 중
        토글은 "껐는데 왜 또 뜨냐"를 만든다(§19.4 업계 교훈).
        """
        with self._lock:
            self._muted = True
            # 이미 생성된(=돈이 나간) 대기분도 버린다. 화면이 muted 라 방출해도
            # 보이지 않으므로 들고 있을 이유가 없다.
            self._pending = []
        self._notify(
            "muted",
            "이번 회의에는 페르소나 카드를 띄우지 않습니다 — 개입·중간 정리 생성을 "
            "멈췄습니다(판정 기록은 계속됩니다).")

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
            # M1 화면 채널 상태 — 프런트 배지·안내에 그대로 쓴다.
            "muted": self._muted,
            "shown_count": self._shown_count,
            "pending_count": self.pending_count(),
            "budget": self._budget,
            "budget_remaining": (self.budget_remaining() if self._budget > 0 else 0),
            "display_personas": [p.key for p in self.active_personas()
                                 if self.persona_level(p.key) >= DISPLAY_LEVEL],
            # 중간 요약(주기 페르소나) — 프런트의 [지금 정리] 버튼 표시 조건이다.
            "brief_on": self.brief_enabled(),
            "brief_count": self._brief_count,
            "brief_period_sec": self._brief_period,
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
            u = Utterance(text.strip(), t0, t1, bool(provisional))
            with self._lock:
                self._window.append(u)
                self._trim_window_locked()
                # 중간 요약 버퍼는 트리아지 창과 **따로** 쌓는다 — 트리아지는 매 회차
                # 창을 비우므로(WINDOW_CARRYOVER) 그것만 보면 요약이 마지막 25초만
                # 보게 된다. 요약의 입력 단위는 '마지막 요약 이후'다.
                self._brief_buf.append(u)
                self._brief_chars += len(u.text)
                self._trim_brief_locked()
            now = float(self._clock())
            self._maybe_brief(now)
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

    # ── 중간 요약 (주기 페르소나 summarizer, 음성브리핑 PRD 트랙 A) ──────────

    def _trim_brief_locked(self) -> None:
        """요약 입력 버퍼를 BRIEF_WINDOW_CHARS 로 유지 (호출부가 락을 든다).

        상한을 넘으면 **오래된 쪽을 버린다** — 이전 요약을 이어받으므로(FR-A2) 버린
        부분이 통째로 사라지지는 않는다."""
        while self._brief_chars > BRIEF_WINDOW_CHARS and len(self._brief_buf) > 1:
            self._brief_chars -= len(self._brief_buf.pop(0).text)

    def _maybe_brief(self, now: float) -> None:
        """주기 게이트 — 시간과 내용 **둘 다** 맞을 때만 요약 1회를 제출한다."""
        if not self.brief_enabled() or self._pool is None:
            return
        with self._lock:
            if self._last_brief is None:
                # 첫 요약은 '녹음 시작 + 1주기' 뒤다 — 시작 직후 요약할 내용이 없다.
                self._last_brief = now
                return
            if now - self._last_brief < self._brief_period:
                return
            if self._brief_chars < self._brief_min_chars:
                return                            # 침묵·짧은 발화 구간은 건너뛴다
            buf, self._last_brief = self._take_brief_buf_locked(), now
        self._pool.submit(self._brief_task, buf, TRIGGER_BRIEF_PERIODIC)

    def _take_brief_buf_locked(self) -> List[Utterance]:
        buf, self._brief_buf, self._brief_chars = self._brief_buf, [], 0
        return buf

    def brief_now(self) -> str:
        """[지금 정리] — 주기를 기다리지 않고 요약 1회. 반환: '' 또는 건너뜀 사유.

        [지금 점검](대기분 방출)과 달리 **이 버튼은 새 과금을 만든다** — 그래서
        (a) 최소 간격(BRIEF_MIN_GAP_SEC)으로 연타를 막고 (b) 새로 쌓인 발화가 없으면
        돌지 않으며 (c) 사유를 항상 화면에 돌려준다(조용히 실패 금지)."""
        if self._pool is None:
            return "회의 진행 페르소나가 꺼져 있습니다"
        if self._muted:
            return "이번 회의는 페르소나를 껐습니다 — 새 녹음에서 다시 켜집니다"
        if self.on_intervention is None:
            return "표시할 화면이 없어 정리를 만들지 않습니다"
        if self.brief_level() < COLLECT_LEVEL:
            return "중간 요약 참견도가 0·1(관찰)이라 요약을 만들지 않습니다"
        now = float(self._clock())
        with self._lock:
            if (self._last_brief is not None
                    and now - self._last_brief < BRIEF_MIN_GAP_SEC):
                left = BRIEF_MIN_GAP_SEC - (now - self._last_brief)
                return f"방금 정리했습니다 — {left:.0f}초 뒤에 다시 시도하세요"
            if not self._brief_buf:
                return "지난 정리 이후 새로 쌓인 발화가 없습니다"
            buf, self._last_brief = self._take_brief_buf_locked(), now
        self._pool.submit(self._brief_task, buf, TRIGGER_BRIEF_ON_DEMAND)
        return ""

    def _brief_task(self, buf: List[Utterance], trigger: str) -> None:
        """중간 요약 1회 — 개입 생성과 **같은 3관문**을 지난다(개입보다 입력이 크다).

        예산(max_interventions_per_session)은 소모하지 않는다: 요약은 후보 기반이 아니라
        주기 기반이라 횟수가 이미 시간으로 묶여 있고(트리아지와 같은 성질), 기회주의적
        개입 카드가 사용자가 기대하는 주기 요약을 굶기면 안 된다. 상한은 주기와 회의당
        비용 캡이 맡는다."""
        from meeting_minutes_app.common import pricing, spend_guard
        window_text, t0, t1, provisional = self._window_meta(buf)
        if not window_text.strip():
            return
        p = get_persona(BRIEF_PERSONA)
        if p is None:
            return
        model = effective_persona_model(BRIEF_PERSONA)
        est = pricing.facilitation_brief_cost(model)
        reserved = False
        try:
            if spend_guard.automation_paused():
                self._notify("blocked", "자동 실행 일시정지로 중간 정리를 건너뜀")
                return
            reason = spend_guard.blocked(est, check_per_item=False)
            if reason:
                self._skip_reason = reason
                self._notify("blocked", f"지출 한도로 중간 정리를 건너뜀: {reason}")
                return
            with self._lock:
                if (self._meeting_cap > 0
                        and self._session_cost + est > self._meeting_cap):
                    over = self._session_cost
                else:
                    self._session_cost += est      # 예약
                    reserved = True
                    over = None
            if not reserved:
                msg = (f"이 회의의 facilitation 비용 ${over:.4f}이 회의당 캡 "
                       f"${self._meeting_cap:.2f}에 도달 — 중간 정리 보류")
                self._skip_reason = msg
                self._notify("capped", msg)
                return

            raw = _call_llm(model, p.system_prompt,
                            self._build_brief_prompt(window_text),
                            max_tokens=pricing.FACILITATION_BRIEF_MAX_OUTPUT_TOKENS)
        except Exception:
            # 호출 자체가 실패했다(양 벤더 모두 실패 시 llm_client 가 raise) —
            # 과금이 없으므로 예약을 환불한다. **사유는 반드시 화면에 돌려준다**:
            # [지금 정리]는 눌린 순간 '정리 중…'으로 잠기고 그것을 푸는 것은 요약
            # 카드나 상태 이벤트뿐이라, 조용히 return 하면 버튼이 회의 내내 잠긴다.
            if reserved:
                with self._lock:
                    self._session_cost = max(0.0, self._session_cost - est)
            self._notify("empty", "중간 정리를 만들지 못했습니다 — 잠시 후 다시 시도하세요")
            return

        # 호출이 끝났으면 **파싱 성공 여부와 무관하게 과금은 발생했다.** 트리아지가
        # 쓰는 것과 같은 규칙이다(_triage_task 참조). 기록을 파싱 뒤로 미뤘던 초기
        # 구현은 파싱 실패분을 환불까지 해서, 실제로 나간 돈이 월 합계에서 사라졌다.
        spend_guard.record(spend_guard.KIND_FACILITATION, est, model=model,
                           units=1, unit_kind="brief",
                           note=spend_guard.session_note(self.session_id))
        brief = self._parse_brief(raw)
        if brief is None:
            self._notify("empty",
                         "중간 정리 결과를 읽지 못했습니다 — 다음 정리 때 다시 시도합니다")
            return
        text = _brief_to_text(brief)
        with self._lock:
            self._brief_count += 1
            self._last_brief_text = text[:1200]
            n = self._brief_count
        # 요약도 관찰 로그에 남긴다 — 완전 삭제(purge)가 함께 지우고, 카드의 확인/닫기
        # 라벨로 '이 요약이 쓸모 있었나'를 실측할 수 있다. trigger_type 으로 개입
        # 후보와 구분되며 report() 가 분모를 섞지 않는다.
        record_observation(self.session_id, BRIEF_PERSONA, trigger_type=trigger,
                           confidence=1.0, span=text[:500], level=self.brief_level(),
                           t0=t0, t1=t1, provisional=provisional, note=self._note,
                           db_path=self._db_path)
        item = {
            "type": "facilitation",
            "id": f"brief_{n}_{int((self._clock() or 0) * 1000)}",
            "persona": BRIEF_PERSONA,
            "personaLabel": p.label,
            "level": self.brief_level(),
            "kind": p.kind,                          # "brief"
            "risk": p.risk,
            "text": text[:1200],
            "brief": brief,                          # 카드가 절별로 렌더한다
            "evidence": [],
            "span": ({"t0": round(float(t0), 2), "t1": round(float(t1 or t0), 2)}
                     if t0 is not None else {}),
            "quote": "",
            "confidence": 1.0,
            "searched": False,
            "draft": True,
            "costUsd": round(est, 6),
            "spanHash": span_key(BRIEF_PERSONA, text[:500], trigger),
            "onDemand": trigger == TRIGGER_BRIEF_ON_DEMAND,
        }
        if self.brief_level() >= DISPLAY_LEVEL or trigger == TRIGGER_BRIEF_ON_DEMAND:
            # 사용자가 직접 누른 [지금 정리]는 참견도 2 여도 바로 보여준다 —
            # 눌렀는데 대기열로 들어가면 버튼이 고장난 것처럼 보인다.
            self._emit(item)
        else:
            with self._lock:
                self._pending.append(item)
            self._notify("pending", "정리한 내용이 있습니다",
                         pending=self.pending_count())

    def _build_brief_prompt(self, window_text: str) -> str:
        parts = []
        if self.topic:
            parts.append(f"## 회의 주제\n{self.topic}")
        if self._last_brief_text:
            # 이전 요약을 이어받는다 — 전체 전사를 매번 넣지 않는 이유(FR-A2).
            parts.append(f"## 이전 요약(이어받아 갱신하세요)\n{self._last_brief_text}")
        parts.append(f"## 지난 정리 이후 발화\n{window_text}")
        parts.append("위 내용을 지정된 JSON 형식으로만 정리하세요.")
        return "\n\n".join(parts)

    @staticmethod
    def _parse_brief(raw: str) -> Optional[Dict[str, List[str]]]:
        """요약 JSON 파싱 — 4개 키만 남기고 항목 수·길이를 자른다. 전부 비면 None
        (빈 카드를 화면에 내지 않는다)."""
        from meeting_minutes_app.meeting_pipeline.json_utils import parse_json_loose
        obj = parse_json_loose(raw, expect="dict", default=None)
        if not isinstance(obj, dict):
            return None
        out: Dict[str, List[str]] = {}
        for key in ("points", "decisions", "actions", "open_questions"):
            vals = obj.get(key)
            items = [str(v).strip()[:300] for v in vals
                     if isinstance(vals, list) and str(v).strip()][:4] \
                if isinstance(vals, list) else []
            out[key] = items
        return out if any(out.values()) else None

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
                              provisional=provisional, skip_reason=reason,
                              note=self._note, db_path=self._db_path)

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
            shown_here = 0
            for cand in cands:
                level = self.persona_level(cand["persona"])
                # 기록은 참견도와 무관하게 **항상** 먼저 한다 — 화면에 뜬 개입도
                # 오탐률 실측의 대상이다(분자에서 빠지면 측정이 왜곡된다).
                r = record_observation(
                    self.session_id, cand["persona"],
                    trigger_type=cand.get("trigger_type", ""),
                    confidence=cand.get("confidence", 0.0),
                    span=cand.get("span", ""),
                    need_search=bool(cand.get("need_search")),
                    level=level,
                    t0=t0, t1=t1, provisional=provisional, note=self._note,
                    db_path=self._db_path)
                with self._lock:
                    if r == "new":
                        self._observed_count += 1
                    elif r == "repeat":
                        self._repeat_count += 1
                # 같은 후보가 재판정된 것(repeat)은 화면에 다시 내지 않는다 —
                # 창이 겹치는 동안 같은 카드가 반복 등장하면 그게 소음이다.
                if r != "new":
                    continue
                if shown_here >= MAX_INTERVENTIONS_PER_TRIAGE:
                    continue                     # 한 회차에 여러 장 쏟지 않는다
                if self._dispatch(cand, level, window_text, t0, t1) in (
                        "shown", "pending"):
                    shown_here += 1
            record_triage(self.session_id, model=self._triage_model, cost_usd=est,
                          candidates=len(cands), personas=len(active),
                          window_chars=len(window_text), t0=t0, t1=t1,
                          provisional=provisional, ok=True, note=self._note,
                          db_path=self._db_path)
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
                              skip_reason="", note=self._note,
                              db_path=self._db_path)
                self._skip_reason = f"트리아지 실패: {type(e).__name__}"
            except Exception:
                pass

    # ── Tier 1: 개입 생성 (M1) ────────────────────────────

    def budget_remaining(self) -> int:
        """남은 화면 개입 예산(0 이면 이후 후보는 관찰로 강등). 0 설정이면 무제한.

        분모는 '생성된 개입'이다 — 자동 표시분과 [지금 점검] 대기분을 함께 센다."""
        if self._budget <= 0:
            return 10 ** 6
        with self._lock:
            return max(0, self._budget - self._shown_count)

    def pending_count(self) -> int:
        """참견도 2(소극) 대기 중인 개입 수 — [지금 점검] 버튼 배지용."""
        with self._lock:
            return len(self._pending)

    def check_now(self) -> List[Dict[str, Any]]:
        """[지금 점검] — 참견도 2(소극) 대기분을 방출한다.

        여기서 새 LLM 호출을 하지 않는 것은 의도다: 대기분은 **이미 생성된** 개입이라
        추가 과금이 없고, 버튼 한 번이 새 과금을 일으키면 사용자가 비용을 예측할 수
        없다. 새 판정이 필요하면 다음 트리아지 주기에 자연히 돈다."""
        with self._lock:
            # 예산은 생성 시점에 이미 소모했다(_dispatch) — 여기서 다시 세면 이중 차감.
            out, self._pending = self._pending, []
        for item in out:
            self._emit(item)
        return out

    def feedback(self, persona: str, span_hash: str, label: str) -> bool:
        """카드의 [✓ 확인]/[✕ 닫기] 라벨을 관찰 로그에 남긴다(§19.4).

        회의 중 누른 버튼이 그대로 §15 오탐률 실측의 사람 라벨이 된다 — 종료 후 별도
        라벨링 작업을 요구하면 데이터가 모이지 않는다."""
        if not self.session_id:
            return False
        return record_feedback(self.session_id, persona, span_hash, label,
                               db_path=self._db_path)

    def _emit(self, item: Dict[str, Any]) -> None:
        """화면 채널로 1건 방출 — 콜백 예외가 실시간 스트림을 깨지 않게 감싼다."""
        cb = self.on_intervention
        if cb is None:
            return
        try:
            cb(item)
        except Exception:
            pass

    def _notify(self, kind: str, message: str, **extra: Any) -> None:
        if self.on_status is None:
            return
        try:
            self.on_status({"kind": kind, "message": message, **extra})
        except Exception:
            pass

    def _persona_evidence(self, p: Persona,
                          cand: Optional[Dict[str, Any]] = None
                          ) -> List[Dict[str, Any]]:
        """이 개입에 쓸 볼트 근거 — **지금 판정 중인 발화**로 검색한 결과를 우선한다.

        예전에는 `collected_evidence()`(세션 전체 누적 상위 5)만 썼다. 그건 "요즘 자주
        뜬 노트"이지 "이 주장과 대조할 근거"가 아니다 — 팩트체커가 30분 전 발화에서
        올라온 노트를 근거로 방금 나온 수치를 검증하는 모양이 된다. 그래서 후보의
        `span`(그 발화 원문)으로 한 번 더 검색한다.

        검색 1회 = 쿼리 임베딩 1회(약 $0.0000018) + 0.3~0.5초. 개입은 회의당 예산
        상한(기본 12건)이 있고 이미 LLM 생성으로 수 초를 쓰므로 상대적으로 작다.
        **트리아지(상시 경로)에는 넣지 않는다** — 그쪽은 25초마다 무조건 돈다.
        전사 스트림은 무영향(개입 생성은 별도 풀 스레드).

        검색기가 없거나(터미널·리플레이) 0건이면 누적분으로 폴백한다 — 근거 필수
        페르소나가 검색 실패만으로 침묵하면 기능이 조용히 사라진 것처럼 보인다.
        근거 소스에 "vault" 가 없는 페르소나(촉진자·서기·주니어)는 대화만 본다.

        근거 소스에 "web" 을 적은 페르소나에게는 **회의 중 이미 나간** 웹 검색 결과가
        내부 자료 뒤에 붙는다(FR-10 내부 우선). 그 전까지 웹 결과는 회의록 memo 로만
        갔고, 정작 팩트체커 프롬프트는 "라이브 검색 근거가 없으면 개입하지 마세요"
        라고 적혀 있었다 — 프롬프트·코드 가드·실제 데이터가 3중으로 어긋나 있었다."""
        out = self._vault_evidence(p, cand)
        out.extend(self._web_evidence(p))
        return out

    def _vault_evidence(self, p: Persona,
                        cand: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if EV_VAULT not in (p.evidence or ()):
            return []
        notes: List[Dict[str, Any]] = []
        span = str((cand or {}).get("span") or "").strip()
        if span and self.search_provider is not None:
            try:
                notes = list(self.search_provider(span, 3) or [])
            except Exception:
                notes = []
        if not notes and self.evidence_provider is not None:
            try:
                notes = list(self.evidence_provider() or [])
            except Exception:
                notes = []
        out: List[Dict[str, Any]] = []
        for n in notes[:3]:
            if not isinstance(n, dict):
                continue
            out.append({
                # source_type 은 검색기가 note/paper 로 구분해 준다 — 카드가 "논문에
                # 따르면"과 "지난 회의록에 따르면"을 구분해 보여줄 수 있어야 한다.
                "source": str(n.get("source_type") or "note"),
                "title": str(n.get("title") or n.get("filename") or "")[:120],
                "url": str(n.get("filename") or ""),
                "score": float(n.get("rank_score") or n.get("score") or 0.0),
                "snippet": str(n.get("snippet") or "")[:300],
            })
        return out

    def _web_evidence(self, p: Persona) -> List[Dict[str, Any]]:
        """이미 나간 웹 검색 결과 중 최근 2건 — 새 검색은 하지 않는다(비용 0).

        최근 것을 쓰는 이유: 웹 보완은 발화별로 나가므로 뒤쪽일수록 지금 논의에
        가깝다. 볼트 근거처럼 후보 발화로 재검색하지 않는 것은, 그러려면 유료
        웹 검색을 개입마다 한 번 더 해야 하기 때문이다(회의당 비용이 예측 불가가
        된다). 라이브 재검색은 M2 몫이다."""
        if EV_WEB not in (p.evidence or ()) or self.web_provider is None:
            return []
        try:
            found = list(self.web_provider() or [])
        except Exception:
            return []
        out: List[Dict[str, Any]] = []
        for f in found[-2:]:
            if not isinstance(f, dict):
                continue
            srcs = f.get("sources") or []
            out.append({
                "source": "web",
                "title": str((srcs[0] if srcs else "") or "웹 검색 결과")[:120],
                "url": str((srcs[0] if srcs else "") or ""),
                "score": 0.0,
                "snippet": str(f.get("result") or "")[:300],
            })
        return out

    def _generate(self, cand: Dict[str, Any], window_text: str,
                  t0: Optional[float], t1: Optional[float],
                  level: int) -> Optional[Dict[str, Any]]:
        """후보 1건 → 개입 1건(Tier 1). 실패·근거 부족이면 None(개입을 만들지 않는다).

        비용은 트리아지와 같은 3관문 + 회의당 캡 예약을 지난다 — 개입은 트리아지보다
        비싼 모델을 쓰므로(§5 티어) 여기가 빠지면 캡이 무의미해진다."""
        from meeting_minutes_app.common import pricing, spend_guard
        p = get_persona(cand["persona"])
        if p is None:
            return None
        evidence = self._persona_evidence(p, cand)
        # 근거가 필수인 페르소나(도메인·팩트체커)는 근거 없이 개입하지 않는다
        # ("추측 금지" — system_prompt 와 PRD §6 의 게이트를 코드로도 막는다).
        # **웹 근거만 요구하지는 않는다.** 팩트체커 프롬프트에 있던 "라이브 검색
        # 근거가 없으면 개입하지 마세요"는 이 제품의 설계(FR-10 내부 자료 우선 —
        # 볼트에서 찾으면 웹을 아예 부르지 않는다)와 어긋나 있었고, 코드 가드는
        # 볼트만 봐서 프롬프트·가드·데이터가 3중으로 갈라져 있었다. 사내 노트·논문·
        # 지난 회의 결정도 대조 근거로 충분하다 — 프롬프트를 그 사실에 맞췄고,
        # 웹 근거의 유무는 카드의 `searched`(⚠ 미검증 배지)로 구분해 보여준다.
        if p.key in ("domain_expert", "fact_checker") and not evidence:
            return None

        model = effective_persona_model(p.key)
        est = pricing.facilitation_intervention_cost(model)
        if spend_guard.automation_paused():
            return None
        reason = spend_guard.blocked(est, check_per_item=False)
        if reason:
            self._skip_reason = reason
            self._notify("blocked", f"지출 한도로 개입 보류: {reason}")
            return None
        reserved = False
        with self._lock:
            if self._meeting_cap > 0 and self._session_cost + est > self._meeting_cap:
                over = self._session_cost
            else:
                self._session_cost += est
                reserved = True
                over = None
        if not reserved:
            msg = (f"이 회의의 facilitation 비용 ${over:.4f}이 회의당 캡 "
                   f"${self._meeting_cap:.2f}에 도달 — 개입 보류")
            self._skip_reason = msg
            self._notify("capped", msg)
            return None

        try:
            user = self._build_generate_prompt(p, cand, window_text, evidence)
            text = _call_llm(model, p.system_prompt, user,
                             max_tokens=pricing.FACILITATION_INTERVENTION_MAX_OUTPUT_TOKENS)
        except Exception:
            with self._lock:                     # 호출 실패 = 과금 없음 → 예약 환불
                self._session_cost = max(0.0, self._session_cost - est)
            return None

        # 호출이 끝났으면 **결과가 비어도 과금은 발생했다** — 트리아지·중간 요약과
        # 같은 규칙으로 먼저 기록한다. 빈 응답을 예외로 처리해 환불하던 초기 구현은
        # 실제로 나간 돈을 월 합계에서 지웠다.
        spend_guard.record(spend_guard.KIND_FACILITATION, est, model=model,
                           units=1, unit_kind="intervention",
                           note=spend_guard.session_note(self.session_id))
        text = (text or "").strip()
        if not text:
            return None                          # 카드는 만들지 않는다(빈 개입 금지)
        span = {}
        if t0 is not None:
            span = {"t0": round(float(t0), 2), "t1": round(float(t1 or t0), 2)}
        return {
            "type": "facilitation",
            "id": f"fac_{int((self._clock() or 0) * 1000)}_{p.key}",
            "persona": p.key,
            "personaLabel": p.label,
            "level": level,
            "kind": p.kind,
            "risk": p.risk,
            "text": text[:1200],
            "evidence": evidence,
            "span": span,
            "quote": str(cand.get("span") or "")[:200],
            # 이 개입이 어느 관찰 행인지 — 카드의 [✓ 확인]/[✕ 닫기]가 이 키로 라벨을
            # 그 행에 적는다(§19.4). 행 id 대신 dedup 키를 쓰는 이유는 record_observation
            # 의 반환 계약("new"/"repeat")을 바꾸지 않아도 되기 때문이다.
            "spanHash": span_key(p.key, str(cand.get("span") or ""),
                                 str(cand.get("trigger_type") or "")),
            "confidence": float(cand.get("confidence") or 0.0),
            # 이 개입 1건의 금액(실효 모델 단가). 러닝 미터가 이 값을 합산한다 —
            # 개입은 시간에 비례하지 않아 분당 요율로는 표현할 수 없고, 추정 대신
            # **실제 발생 건수**로 보여주기 위해 여기서 함께 보낸다(pricing 주석 참조).
            "costUsd": round(est, 6),
            # 이 개입이 **웹 근거를 실제로 달고 나가는가**. 카드의 "⚠ 미검증" 배지가
            # 이 값으로 갈린다. 종전엔 무조건 False 라 배지가 상수였고, 그래서
            # 아무것도 말해 주지 않았다. 웹 근거는 회의 중 이미 나간 검색 결과이며
            # 여기서 새로 검색하지는 않는다(라이브 재검색은 M2).
            "searched": any(e.get("source") == "web" for e in evidence),
            "draft": True,      # 항상 true — "초안/보조" 고정 라벨(§8)
        }

    def _build_generate_prompt(self, p: Persona, cand: Dict[str, Any],
                               window_text: str,
                               evidence: List[Dict[str, Any]]) -> str:
        parts = [f"## 최근 발화\n{window_text}"]
        if self.topic:
            parts.append(f"## 회의 주제\n{self.topic}")
        if cand.get("span"):
            parts.append(f"## 이 발화가 근거입니다\n{cand['span']}")
        if cand.get("trigger_type"):
            parts.append(f"## 감지된 트리거\n{cand['trigger_type']}")
        # 이전 회의 재료는 **그것을 근거로 쓰는 페르소나에게만** 넣는다
        # (`Persona.evidence` 에 "registry"). 촉진자·주니어까지 주면 대조와 무관한
        # 카드에 토큰만 늘고, 이 리포가 경계하는 '근거처럼 보이는 배경'이 된다.
        if EV_REGISTRY in (p.evidence or ()):
            prior = self.prior_context_block()
            if prior:
                parts.append(prior)
        if evidence:
            # 출처를 함께 적는다 — 카드가 "이전과 다르다"고 말하면 무엇과 대조했는지
            # 보여야 한다(PRD_실시간관련정보 §6-5 추적 가능성).
            lines = [f"- [{e.get('source','note')}] {e['title']}: {e.get('snippet','')}"
                     for e in evidence]
            parts.append("## 참고 근거(이것만 사실로 인용하세요)\n" + "\n".join(lines))
        parts.append("위 내용에 대해 당신의 역할대로 2~4문장으로 한 가지만 말하세요.")
        return "\n\n".join(parts)

    def _dispatch(self, cand: Dict[str, Any], level: int, window_text: str,
                  t0: Optional[float], t1: Optional[float]) -> str:
        """참견도 채널 판정 → 생성·표시·보류·강등. 반환: 처리 결과 라벨(로그용).

        예산이 소진되면 **생성 자체를 하지 않는다** — 화면에 못 낼 개입을 만드는 것은
        돈만 쓰는 일이다(관찰 기록은 이미 남아 있다)."""
        if self.on_intervention is None:
            # 화면 채널이 없는 호출자(리플레이·헤드리스 측정)는 개입을 만들지 않는다.
            # 같은 이유의 연장이다 — 아무도 볼 수 없는 개입에 Tier 1 모델 비용을 쓰는
            # 것은 순손실이고, 리플레이는 "트리아지 비용만 든다"가 계약이다.
            return "no_channel"
        if self._muted:
            # 사용자가 이번 회의를 껐다 = 채널이 있어도 아무것도 표시되지 않는다.
            # 위와 **같은 상태**이므로 같은 판정을 한다(mute() 독스트링 참조).
            return "muted"
        if level < COLLECT_LEVEL:
            return "observe"                     # 0·1 은 기록만(이미 record 됨)
        if float(cand.get("confidence") or 0.0) < self._min_conf:
            return "low_confidence"
        if self.budget_remaining() <= 0:
            self._notify("budget",
                         f"이번 회의 개입 예산 {self._budget}건을 모두 썼습니다 — "
                         f"이후는 기록만 남습니다")
            return "budget_exhausted"
        item = self._generate(cand, window_text, t0, t1, level)
        if item is None:
            return "not_generated"
        if level >= DISPLAY_LEVEL:
            with self._lock:
                self._shown_count += 1
            self._emit(item)
            return "shown"
        with self._lock:                         # level == 2(소극) → [지금 점검] 대기
            self._pending.append(item)
            # 대기분도 **생성 시점에** 예산을 소모한다. 방출 시점에 세면 참견도 2 만
            # 쓰는 회의에서 예산이 영원히 남아 있어 "회의당 12건" 이 무력해진다
            # (대기 항목은 이미 생성됐으므로 돈은 이미 나갔다 — 세지 않으면 상한이
            #  회의당 비용 캡뿐이라 기본값에서 1000건까지 열린다).
            self._shown_count += 1
        self._notify("pending", "점검할 항목이 있습니다",
                     pending=self.pending_count())
        return "pending"

    def _build_triage_prompt(self, window_text: str,
                             active: List[Persona]) -> tuple:
        """(system, user) — 활성 페르소나만 넣는다(0=금지는 여기 등장하지 않는다)."""
        # 대조를 실제로 할 수 있는 **활성** 페르소나만 추린다. 키를 하드코딩하면
        # 참견도 0(금지)으로 꺼 둔 페르소나 이름이 프롬프트에 들어가 "0 = 트리아지
        # 입력에서 제외, 진짜 0 비용" 계약이 깨진다(기존 회귀 테스트가 잡아낸 결함).
        # 아무도 대조를 못 하면 재료도 넣지 않는다 — 쓸 수 없는 토큰은 낭비다.
        contrast_keys = [p.key for p in active if EV_REGISTRY in (p.evidence or ())]
        prior = self.prior_context_block() if contrast_keys else ""
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
        if prior:
            # 이 지시가 없으면 대조 재료를 넣어도 모델이 '배경 정보'로만 읽고
            # 어긋남을 후보로 올리지 않는다(재료 공급과 사용 지시는 별개다).
            system += (
                " 이전 회의 자료가 함께 주어지면, 최근 발화가 그 결정·액션과 "
                "**어긋나거나 이미 정해진 것을 다시 논의**하는지도 함께 보세요 — "
                f"그런 경우 {' 또는 '.join(contrast_keys)} 후보로 올리고, span 에 그 "
                "발화를 인용하세요. 어긋남이 없으면 굳이 만들어내지 마세요."
            )
        lines = [
            f"- {p.key}: {p.role} (트리거: {', '.join(p.triggers)})"
            for p in active
        ]
        topic = f"\n회의 주제: {self.topic}" if self.topic else ""
        user = ("## 활성 페르소나\n" + "\n".join(lines)
                + topic
                + (f"\n\n{prior}" if prior else "")
                + "\n\n## 최근 발화\n" + window_text)
        return system, user

    def _parse_candidates(self, raw: str,
                          active: List[Persona]) -> List[Dict[str, Any]]:
        """트리아지 응답 파싱 — 활성 페르소나 키가 아닌 항목(환각)은 버린다.

        confidence 임계 필터는 **여기에 두지 않는다** — 관찰모드의 목적이 임계값을
        실측으로 정하는 것이고, 파싱 단계에서 걸러내면 그 후보가 로그에서도 사라져
        분포를 볼 수 없다(이 리포의 '실측 없는 랭킹 휴리스틱 금지' 원칙).
        M1 은 임계를 **표시 단계**(`_dispatch`)로 옮겼다: 모든 후보를 기록한 뒤
        `facilitation.min_confidence`(전역 1개, 기본 0.6) 미달은 화면에만 내지 않는다.
        페르소나별 8개 상수를 지어내지 않은 것도 같은 이유다 — 나눌 근거는 로그에
        쌓이는 confidence 분포이며, 그때 `Persona` 에 필드를 추가한다."""
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
    print("회의 진행 페르소나 관찰 로그")
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
    if r["replay"]["triage_attempts"] and r["live"]["triage_attempts"]:
        print(f"\n⚠ 라이브 {r['live']['triage_attempts']}회 / 리플레이 "
              f"{r['replay']['triage_attempts']}회가 섞여 있습니다 — 리플레이는 보정된 "
              f"확정 전사를 보므로\n  판정 조건이 유리합니다. precision 은 따로 계산하세요"
              f"(--session 으로 분리).")
    elif r["replay"]["triage_attempts"]:
        print("\n출처: 리플레이(지난 회의 확정 전사). 라이브(조각 전사)보다 조건이 "
              "유리해 precision 은 **상한**입니다.")
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
                  f"검색요청 {d['need_search']:>3} · "
                  f"확인 {d.get('ack', 0):>3} / 닫기 {d.get('dismiss', 0):>3}")
    if r["briefs"]:
        print(f"\n[중간 요약] {r['briefs']}건 "
              f"(그중 [지금 정리] 버튼 {r['briefs_on_demand']}건) — 주기 산출물이라 "
              f"위 후보 수에는 포함하지 않습니다")
    labeled = r["feedback_ack"] + r["feedback_dismiss"]
    if labeled:
        print(f"\n[사람 라벨] 확인 {r['feedback_ack']} · 닫기 "
              f"{r['feedback_dismiss']} (회의 중 카드에서 누른 값, 총 {labeled}건)")
        print("        화면에 뜬 개입만 라벨될 수 있습니다 — 참견도 1(관찰) 후보는 "
              "라벨이 비어 있는 게 정상입니다.")
        print("        '닫기'는 '틀렸다'와 '맞지만 지금은 불필요하다'가 섞여 있어 "
              "그대로 오탐률이 아닙니다.")
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
            fb = {FEEDBACK_ACK: " ✓확인",
                  FEEDBACK_DISMISS: " ✕닫기"}.get(o.get("feedback") or "", "")
            print(f"  {o.get('ts','')} {o.get('persona','')}{rep}"
                  f" conf={float(o.get('confidence') or 0):.2f}{t}{flag}{fb}")
            print(f"      {o.get('trigger_type','')}: {o.get('span','')}")
    else:
        print("후보 인용문을 함께 보려면: --detail")


def _force_utf8_console() -> None:
    """한국어 콘솔(cp949)에서 '—' 같은 문자가 UnicodeEncodeError 를 내는 것을 막는다.

    판정은 `common.console` 하나만 쓴다(같은 6줄이 8개 파일에 세 철자로 복제돼
    있던 자리). 다만 **호출 시점**은 이 모듈만 다르다 — 웹 서버에서도 import 되므로
    다른 CLI 모듈들처럼 최상단이 아니라 main() 안에서만 부른다. 라이브러리 import 가
    프로세스 전역 스트림을 바꾸면 안 된다."""
    from meeting_minutes_app.common.console import force_utf8_console
    force_utf8_console()


def _run_replay(session_ids: List[str], reset: bool, assume_yes: bool) -> int:
    """지난 회의 리플레이 — **비용이 발생하므로** 예상 금액을 먼저 보여주고 확인받는다."""
    from meeting_minutes_app.common import pricing, spend_guard
    period = max(float(_c("facilitation.triage_period_sec", 25) or 25), 1.0)
    model = effective_triage_model(
        str(_c("facilitation.triage_model", "gpt-4o-mini") or "gpt-4o-mini"))

    plan = []
    for sid in session_ids:
        segs = session_segments(sid)
        if not segs:
            print(f"  건너뜀 {sid}: 전사 세그먼트가 없습니다")
            continue
        est = replay_estimate(segs, period, model)
        plan.append((sid, len(segs), est))
    if not plan:
        print("리플레이할 세션이 없습니다.")
        return 1

    total = round(sum(p[2]["cost_usd"] for p in plan), 6)
    print(f"리플레이 계획 (모델 {model}, 주기 {period:.0f}초)")
    for sid, n, est in plan:
        print(f"  {sid}  세그먼트 {n:>4} · 길이 {est['duration_sec']:>7.0f}s · "
              f"트리아지 최대 {est['triages']:>3}회 · 예상 ${est['cost_usd']:.4f}")
    print(f"  합계 예상 ${total:.4f}  "
          f"(STT 재과금 없음 — 이미 있는 전사를 다시 판정만 한다)")
    blocked = spend_guard.blocked(total, check_per_item=False)
    if blocked:
        print(f"\n지출 한도에 걸립니다: {blocked}")
        return 2
    if spend_guard.automation_paused():
        print("\n자동 실행이 일시정지 상태입니다(automation.paused) — 해제 후 다시 실행하세요.")
        return 2
    if not assume_yes:
        try:
            if input("\n진행할까요? [y/N] ").strip().lower() not in ("y", "yes"):
                print("취소했습니다.")
                return 1
        except (EOFError, KeyboardInterrupt):
            print("\n취소했습니다.")
            return 1

    for sid, n, _est in plan:
        print(f"\n▶ {sid} 리플레이 …")
        res = replay_session(
            sid, reset=reset,
            on_progress=lambda i, t: print(f"    세그먼트 {i}/{t}", end="\r"))
        print(" " * 40, end="\r")
        if not res.get("ok"):
            print(f"  실패: {res.get('message')}")
            continue
        print(f"  트리아지 {res['triages']}회 · 후보 {res['candidates']}건"
              f"(중복 {res['repeats']}) · 비용 ${res['cost_usd']:.4f}")
        if res.get("skip_reason"):
            print(f"  마지막 건너뜀 사유: {res['skip_reason']}")
    print(f"\n집계: meeting-minutes facilitation-report --detail"
          f"{' --session ' + plan[0][0] if len(plan) == 1 else ''}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    _force_utf8_console()
    ap = argparse.ArgumentParser(
        prog="meeting-minutes facilitation-report",
        description=("회의 진행 페르소나(관찰모드) 관찰 로그 집계 — 오탐률 실측용. "
                     "--replay 를 주면 지난 회의의 전사로 관찰 데이터를 만든다."))
    ap.add_argument("--session", default=None, help="세션 ID 로 한정")
    ap.add_argument("--detail", action="store_true", help="후보 인용문까지 출력")
    ap.add_argument("--replay", nargs="*", metavar="SESSION_ID",
                    help=("지난 회의 전사를 리플레이해 관찰 데이터를 만든다. "
                          "ID 를 안 주면 --session 값을 쓴다. 트리아지 LLM 비용이 "
                          "발생하며(STT 재과금은 없다) 실행 전 예상 금액을 확인한다."))
    ap.add_argument("--reset", action="store_true",
                    help="리플레이 전에 그 세션의 기존 **리플레이** 행을 지운다(라이브 기록은 유지)")
    ap.add_argument("--yes", action="store_true", help="확인 없이 진행")
    args = ap.parse_args(argv)

    if args.replay is not None:
        ids = list(args.replay) or ([args.session] if args.session else [])
        if not ids:
            print("리플레이할 세션 ID 가 필요합니다: --replay <SESSION_ID> "
                  "(목록은 웹 [회의 목록] 또는 sessions 테이블)")
            return 1
        return _run_replay(ids, args.reset, args.yes)

    _print_report(args.session, args.detail)
    return 0


if __name__ == "__main__":       # python -m meeting_minutes_app.wiki_core.facilitation
    raise SystemExit(main())
