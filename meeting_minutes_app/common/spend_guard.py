"""spend_guard.py — 지출 한도 검사의 단일 지점.

한도(`cost.per_file_cap_usd` / `cost.monthly_cap_usd`)는 지금까지 **두 곳에서만**
강제됐다: 파일 업로드(`web/backend/api/batch.py`)와 위키 임베딩
(`wiki_core.vault_indexer._embedding_budget_blocked`). 나머지 과금 경로 —
폴더 자동 감시, 계획 자동화, 회의록 재생성 — 는 검사를 통째로 비켜 갔다.

폴더 감시는 그보다 나빴다. `ingestion_pipeline` 이 `web.backend.database` 를
import 하지 않아 **DB 세션이 생기지 않고** `cost_estimate` 도 기록되지 않는다.
즉 워처가 태운 돈은 `usage_log.month_to_date_spend()` 합계에서 영구히 보이지 않았고,
그 결과 **다른 경로의 한도 판정까지 왜곡**했다(합계가 실제보다 작게 나오므로).

그래서 이 모듈은 두 가지를 한 곳에 둔다.
  1. 한도 판정 — `blocked()`
  2. 세션 없는 경로의 과금 기록 — `record()` (usage_log 위임)

판정 규칙을 호출부에 복사하지 않는다. 이 리포는 같은 규칙이 복사돼 갈라진 전례가
여러 번 있다(단가 표 4곳, 노트 판정 2곳).

core 가 `web.backend` 를 import 하지 않는 구조는 유지한다 — 합계는 `usage_log` 가
같은 sqlite 파일을 직접 열어 계산한다.
"""

from __future__ import annotations

from typing import Any

#: usage_log.kind 값. 대시보드가 kind 별로 합산해 보여주므로(그리고 "자동 실행분만
#: 따로 조회"가 FR-011 수용 기준이므로) 자동 경로는 서로 구분되는 값을 쓴다.
KIND_WATCHER = "watcher"
KIND_PLAN_AUTOMATION = "plan_automation"
KIND_REGENERATE = "regenerate"
#: 회의 진행 페르소나(트리아지) — 실시간 녹음 중 도는 상시 LLM 경로
#: (wiki_core.facilitation). 사용자가 녹음을 직접 시작·주시하므로 AUTOMATION_KINDS
#: (무인 실행 집계)에는 넣지 않지만, 상시 호출이라 오케스트레이터가
#: automation_paused() 관문을 별도로 지난다(PRD §10).
KIND_FACILITATION = "facilitation"

#: 사용자가 화면을 보고 있지 않을 때 돌아가는 경로. FR-011 의 "자동 실행분 별도 집계"가
#: 이 목록을 쓴다. 재생성은 사용자가 버튼을 눌러 시작하므로 자동 실행이 아니다.
AUTOMATION_KINDS = frozenset({KIND_WATCHER, KIND_PLAN_AUTOMATION})


def _c(key: str, default: Any = None) -> Any:
    try:
        from meeting_minutes_app.common import config_loader as cfg
        return cfg.get(key, default)
    except Exception:
        return default


def month_to_date() -> float:
    """이번 달 지출 합계(USD) — 판정 정본. 실패 시 0.0."""
    try:
        from meeting_minutes_app.common import usage_log
        return usage_log.month_to_date_spend()
    except Exception:
        return 0.0


def blocked(est_cost: float, *, check_per_item: bool = True) -> str:
    """한도를 넘기면 사람이 읽는 사유 문자열, 아니면 ''.

    두 한도를 모두 본다(0 = 무제한, 기본값).
      - `cost.per_file_cap_usd` : 이 작업 1건의 예상 비용 상한
      - `cost.monthly_cap_usd`  : 이번 달 누적 + 이 작업의 상한

    판정에 실패하면(설정·DB 접근 불가) **막지 않는다** — 검사 실패가 정상 작업을
    차단하면 사용자는 이유를 알 수 없는 고장을 겪는다. 한도는 안전장치이지
    필수 경로가 아니다.
    """
    est = max(0.0, float(est_cost or 0.0))
    try:
        if check_per_item:
            per_item = float(_c("cost.per_file_cap_usd", 0) or 0)
            if per_item > 0 and est > per_item:
                return (f"이 작업의 예상 비용 ${est:.4f}이 1건당 한도 "
                        f"${per_item:.2f}를 초과합니다")
        cap = float(_c("cost.monthly_cap_usd", 0) or 0)
        if cap > 0:
            mtd = month_to_date()
            if mtd + est > cap:
                return (f"이번 달 ${mtd:.2f} + 예상 ${est:.4f}이 월 한도 "
                        f"${cap:.2f}를 초과합니다")
    except Exception:
        pass
    return ""


def session_note(session_id: str) -> str:
    """세션에 딸린 과금을 usage_log 에 남길 때의 `note` 규약.

    `usage_log` 에는 session_id 컬럼이 없다(그 테이블은 원래 '세션 없는 사용량'을
    위한 것이다). 그런데 facilitation 처럼 **세션 중에 발생하지만 세션 비용
    (sessions.cost_estimate)에는 넣지 않는** 과금이 생겼다 — 넣으면 월 합계가
    이중 집계된다(usage_log.month_to_date_spend 가 둘을 더한다).

    그래서 note 에 세션 키를 남겨 세션별 실제 금액을 되찾는다. 쓰는 쪽과 읽는 쪽이
    포맷 문자열을 각자 갖고 있으면 한쪽만 바뀌어 조회가 조용히 0 을 돌려주므로,
    규약을 이 함수 하나로 고정한다(`usage_log.session_spend()` 가 짝이다)."""
    return f"session={str(session_id or '').strip()}"


def record(kind: str, cost_usd: float, *, model: str = "", units: float = 0.0,
           unit_kind: str = "", note: str = "") -> bool:
    """세션 없는 경로의 과금 1건을 기록해 월 합계에 잡히게 한다.

    실패해도 예외를 올리지 않는다(usage_log.record 와 같은 정책) — 기록 실패가
    본 작업을 멈추게 하면 안 된다.
    """
    try:
        from meeting_minutes_app.common import usage_log
        return usage_log.record(kind=kind, model=model, units=units,
                                unit_kind=unit_kind, cost_usd=cost_usd, note=note)
    except Exception:
        return False


def estimate_audio_cost(path: str, *, translate: bool = False,
                        include_minutes: bool = True,
                        two_pass: bool = False) -> tuple[float, float]:
    """오디오 파일 1건의 (길이 초, 예상 비용 USD).

    길이를 못 재면 `(0.0, 0.0)` — 업로드 경로와 같은 규칙이다(길이를 모르면 한도
    검사를 건너뛴다). 여기서 0 을 돌려주는 것과 "비용이 0" 은 다른 뜻이므로
    호출부는 duration 이 0 인지로 판단해야 한다.
    """
    duration = 0.0
    try:
        from meeting_minutes_app.meeting_pipeline import meeting_minutes as _mm
        duration = float(_mm.audio_duration(path) or 0.0)
    except Exception:
        return (0.0, 0.0)
    if duration <= 0:
        return (0.0, 0.0)
    try:
        from meeting_minutes_app.common import pricing
        from meeting_minutes_app.common import config_loader as cfg
        m = pricing.current_models(cfg)
        est = pricing.estimate_session_cost(
            duration, m["stt_model"], translate=translate,
            include_minutes=include_minutes, llm=m["llm"],
            minutes_model=m["minutes_model"],
            two_pass=two_pass, revise_model=m["revise_model"],
        )
        return (duration, float(est["total"]))
    except Exception:
        return (duration, 0.0)


def automation_paused() -> bool:
    """모든 자동 실행을 한 번에 멈추는 전역 스위치(FR-011 '일시 정지').

    개별 중지(`watcher/stop`, `watcher/plan/stop`)는 있었지만 "지금 앱이 스스로 하는
    일을 다 멈춰라"를 한 번에 하는 수단이 없었다. 개별 중지는 켜진 것을 하나씩 찾아
    꺼야 하고, 앱을 재시작하면 `autostart_from_config()` 가 다시 켠다.

    이 스위치는 설정값이므로 재시작에도 유지된다 — 그 점이 '중지'와 다르다.
    """
    return bool(_c("automation.paused", False))
