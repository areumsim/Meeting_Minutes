"""지출 한도(cost cap) 핵심 로직 회귀 테스트.

돈과 직결되는 기능이므로 최소한의 안전망을 둔다:
  - database.month_to_date_spend: 이번 달 합계·error 제외·이전 달 제외
  - pricing.current_models / estimate_session_cost: 설정→예상비용 산출
"""

from datetime import datetime

import pytest

from web.backend import database as db
from meeting_minutes_app.common import pricing


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    dbp = tmp_path / "meeting_assistant.db"
    monkeypatch.setattr(db, "DB_PATH", dbp)
    db.init_db()
    return dbp


def _insert(session_id: str, date_iso: str, cost: float, status: str = "completed"):
    with db._conn() as c:
        c.execute(
            "INSERT INTO sessions (id, title, date, status, cost_estimate) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, "t", date_iso, status, cost),
        )
        c.commit()


class TestMonthToDateSpend:
    def test_empty_is_zero(self, fresh_db):
        assert db.month_to_date_spend() == 0.0

    def test_sums_current_month(self, fresh_db):
        now = datetime.now()
        _insert("a", now.replace(day=1, hour=1).isoformat(), 0.30)
        _insert("b", now.replace(day=2, hour=1).isoformat(), 0.45)
        assert db.month_to_date_spend(now) == pytest.approx(0.75)

    def test_excludes_error_sessions(self, fresh_db):
        now = datetime.now()
        _insert("ok", now.replace(day=1, hour=1).isoformat(), 0.50)
        _insert("bad", now.replace(day=1, hour=2).isoformat(), 9.99, status="error")
        assert db.month_to_date_spend(now) == pytest.approx(0.50)

    def test_includes_in_flight_processing(self, fresh_db):
        """동시 업로드가 한도를 우회하지 못하도록 processing 도 합산."""
        now = datetime.now()
        _insert("p", now.replace(day=1, hour=1).isoformat(), 0.40, status="processing")
        assert db.month_to_date_spend(now) == pytest.approx(0.40)

    def test_excludes_previous_month(self, fresh_db):
        now = datetime(2026, 7, 15, 10, 0, 0)
        _insert("this", "2026-07-01T09:00:00", 0.20)
        _insert("prev", "2026-06-30T23:59:59", 5.00)
        assert db.month_to_date_spend(now) == pytest.approx(0.20)


class TestConfirmFlow:
    """비용 확인 대기(pending) → 확인/취소 흐름."""

    def _pending(self, tmp_path):
        from web.backend.api import batch
        f = tmp_path / "clip.mp3"
        f.write_bytes(b"x")
        args = batch._build_args(mode=2, title="t")
        pid = "pend123"
        batch._PENDING[pid] = {
            "file_path": str(f), "args": args, "title": "t", "topic": "",
            "speakers": "", "mode": 2, "est_total": 0.42, "duration_sec": 600.0,
        }
        return batch, pid, f

    def test_confirm_creates_session_and_records_cost(self, fresh_db, tmp_path):
        from fastapi import BackgroundTasks
        batch, pid, _ = self._pending(tmp_path)
        out = batch.confirm_upload(pid, BackgroundTasks())
        assert out["status"] == "processing"
        s = db.get_session(out["sessionId"])
        assert s is not None and s["cost_estimate"] == pytest.approx(0.42)
        assert pid not in batch._PENDING  # 소비됨
        assert db.month_to_date_spend() == pytest.approx(0.42)

    def test_cancel_removes_file_and_pending(self, fresh_db, tmp_path):
        batch, pid, f = self._pending(tmp_path)
        out = batch.cancel_pending_upload(pid)
        assert out["ok"] is True
        assert not f.exists()
        assert pid not in batch._PENDING

    def test_confirm_missing_is_404(self, fresh_db):
        from fastapi import BackgroundTasks, HTTPException
        from web.backend.api import batch
        with pytest.raises(HTTPException) as ei:
            batch.confirm_upload("nope", BackgroundTasks())
        assert ei.value.status_code == 404


class TestRetry:
    """실패 세션 재시도 — 같은 출력 폴더 재사용(재과금 차단)."""

    def test_retry_reuses_output_dir_with_cached_stt(self, fresh_db, tmp_path):
        from fastapi import BackgroundTasks
        from web.backend.api import batch
        out = tmp_path / "out"
        out.mkdir()
        (out / "segments.json").write_text("[]", encoding="utf-8")
        sid = db.create_session(title="t", file_path="", source="web", mode="2")
        db.update_session_status(sid, "error", output_dir=str(out))
        bt = BackgroundTasks()
        r = batch.retry_session(sid, bt)
        assert r["status"] == "processing" and r["reusedStt"] is True
        # 재시도는 기존 output_dir 을 재사용하는 태스크를 예약한다(새 폴더 생성 X).
        assert any(str(out) in (getattr(t, "args", ()) or ()) for t in bt.tasks)
        assert db.get_session(sid)["status"] == "processing"

    def test_retry_rejects_when_nothing_left(self, fresh_db, tmp_path):
        from fastapi import BackgroundTasks, HTTPException
        from web.backend.api import batch
        sid = db.create_session(title="t", file_path=str(tmp_path / "gone.mp3"),
                                 source="web", mode="2")
        db.update_session_status(sid, "error")  # output_dir 없음, 원본 파일 없음
        with pytest.raises(HTTPException) as ei:
            batch.retry_session(sid, BackgroundTasks())
        assert ei.value.status_code == 400

    def test_retry_blocks_processing_session(self, fresh_db):
        from fastapi import BackgroundTasks, HTTPException
        from web.backend.api import batch
        sid = db.create_session(title="t", source="web", mode="2")  # status=processing
        with pytest.raises(HTTPException) as ei:
            batch.retry_session(sid, BackgroundTasks())
        assert ei.value.status_code == 400


class TestEstimate:
    def test_current_models_gpt(self):
        class Cfg:
            def get(self, k, d=None):
                return {"models.stt": "gpt-4o-transcribe", "models.llm": "gpt",
                        "models.minutes_model": "gpt-4o"}.get(k, d)
        m = pricing.current_models(Cfg())
        assert m == {"stt_model": "gpt-4o-transcribe", "llm": "gpt", "minutes_model": "gpt-4o"}

    def test_current_models_claude(self):
        class Cfg:
            def get(self, k, d=None):
                return {"models.llm": "claude", "models.claude_model": "claude-opus-4-8"}.get(k, d)
        m = pricing.current_models(Cfg())
        assert m["llm"] == "claude" and m["minutes_model"] == "claude-opus-4-8"

    def test_longer_audio_costs_more(self):
        short = pricing.estimate_session_cost(60, "gpt-4o-transcribe", include_minutes=True)
        long = pricing.estimate_session_cost(3600, "gpt-4o-transcribe", include_minutes=True)
        assert long["total"] > short["total"] > 0


class TestCliRunningCostEstimate:
    """CLI 실시간의 시간당 비용 표시(realtime_transcription.estimate_cost).

    표시 전용이다 — 월 지출 한도는 pricing.estimate_session_cost(웹)가 판정한다."""

    def _rt(self, monkeypatch, values):
        # 마이크 의존 모듈 스텁은 test_stt_fallback 의 헬퍼를 재사용한다(같은 스텁을
        # 두 파일에 복사하지 않는다).
        from test_stt_fallback import _import_rt
        rt = _import_rt(monkeypatch)

        class _Cfg:
            def get(self, k, d=None):
                return values.get(k, d)
        monkeypatch.setattr(rt, "_cfg_mod", _Cfg())
        monkeypatch.setattr(rt, "_cfg_ok", True)
        return rt

    def test_minutes_cost_follows_configured_llm(self, monkeypatch):
        """Claude 설정이면 Claude 단가로 계산한다 — 과거엔 항상 gpt-4o 하드코딩이라
        같은 세션에서 CLI 와 웹이 다른 값을 보여줬다."""
        rt = self._rt(monkeypatch, {"models.llm": "claude",
                                    "models.claude_model": "claude-opus-4-8"})
        got = rt.estimate_cost("gpt-4o-mini-transcribe", False, "gpt-4o-mini")
        assert got["minutes"] == pytest.approx(
            round(pricing.minutes_cost("claude", "claude-opus-4-8"), 4))
        gpt = self._rt(monkeypatch, {"models.llm": "gpt",
                                     "models.minutes_model": "gpt-4o"}).estimate_cost(
            "gpt-4o-mini-transcribe", False, "gpt-4o-mini")
        assert got["minutes"] > gpt["minutes"]      # Claude 가 더 비싸다

    def test_stt_named_translate_model_does_not_crash(self, monkeypatch):
        """단위가 다른 두 단가표를 합쳐 쓰던 흔적 — STT 표의 키(whisper-1)가 번역 모델로
        들어오면 float 를 dict 처럼 구독하려다 터졌다. 이제 LLM 표만 본다."""
        rt = self._rt(monkeypatch, {"models.llm": "gpt"})
        got = rt.estimate_cost("gpt-4o-mini-transcribe", True, "whisper-1")
        assert got["translate"] == 0.0 and got["total"] > 0


# ━━━━━━━━ 임베딩 비용 — 한도 밖에서 과금되던 구멍 ━━━━━━━━

class TestEmbeddingPricing:
    def test_known_and_unknown_models(self):
        assert pricing.embedding_rate_per_1m("text-embedding-3-small") == 0.02
        assert pricing.embedding_rate_per_1m("text-embedding-3-large") == 0.13
        # 미등록 모델은 기본 단가 — 표를 직접 .get 하면 호출부마다 기본값이 갈린다
        assert (pricing.embedding_rate_per_1m("아무거나")
                == pricing.DEFAULT_EMBEDDING_PRICE_PER_1M)

    def test_cost_from_tokens_is_exact(self):
        assert pricing.embedding_cost_from_tokens(1_000_000, "text-embedding-3-small") == 0.02

    def test_cost_from_chars_uses_conservative_ratio(self):
        """한도 판정에서 과소평가는 한도를 넘겨 버린다 — 크게 잡는 쪽이 안전."""
        chars = 1_000_000
        est = pricing.embedding_cost_from_chars(chars, "text-embedding-3-small")
        assert est == pricing.embedding_cost_from_tokens(
            chars / pricing.EMBEDDING_CHARS_PER_TOKEN, "text-embedding-3-small")

    def test_negative_inputs_are_clamped(self):
        assert pricing.embedding_cost_from_tokens(-5, "text-embedding-3-small") == 0.0


class TestUsageLogInMonthlyTotal:
    """[실전 구멍] 월 한도는 sessions 합계만 봤는데, 위키 임베딩은 세션이 아니다.

    재빌드 버튼·폴더 연결·시작 시 자동 인덱싱·CLI reindex 가 전부 세션 없이
    임베딩 API 를 불러서 그 과금이 한도 밖에 있었다."""

    def test_embedding_cost_counts_toward_monthly_total(self, fresh_db):
        from meeting_minutes_app.common import usage_log
        _insert("a", datetime.now().replace(day=1, hour=1).isoformat(), 0.30)
        usage_log.record(kind="embedding", model="text-embedding-3-small",
                         units=1_000_000, unit_kind="tokens", cost_usd=0.02,
                         db_path=fresh_db)
        assert db.month_to_date_spend() == pytest.approx(0.32)

    def test_previous_month_usage_is_excluded(self, fresh_db):
        import sqlite3
        from meeting_minutes_app.common import usage_log
        usage_log.record(kind="embedding", cost_usd=0.05, db_path=fresh_db)
        with sqlite3.connect(str(fresh_db)) as c:   # 지난달로 밀어 놓는다
            c.execute("UPDATE usage_log SET ts = '2020-01-05T00:00:00'")
        assert db.month_to_date_spend() == 0.0

    def test_missing_usage_log_table_falls_back_to_sessions(self, fresh_db):
        """구버전 DB(포터블 배포본을 덮어쓴 사용자)에 usage_log 가 없어도
        한도 검사가 깨지면 안 된다."""
        _insert("a", datetime.now().replace(day=1, hour=1).isoformat(), 0.10)
        assert db.month_to_date_spend() == pytest.approx(0.10)

    def test_by_kind_breakdown(self, fresh_db):
        from meeting_minutes_app.common import usage_log
        usage_log.record(kind="embedding", cost_usd=0.02, db_path=fresh_db)
        usage_log.record(kind="embedding", cost_usd=0.03, db_path=fresh_db)
        assert usage_log.month_to_date_by_kind(db_path=fresh_db) == {
            "embedding": pytest.approx(0.05)}

    def test_record_never_raises_on_bad_path(self, tmp_path):
        from meeting_minutes_app.common import usage_log
        bad = tmp_path / "없는폴더" / "x" / "db.sqlite"
        assert usage_log.record(kind="embedding", cost_usd=1.0, db_path=bad) in (True, False)


class TestEmbeddingBudgetGate:
    """한도를 넘으면 임베딩만 건너뛰고 TF-IDF 인덱스는 정상 완료한다."""

    def test_no_cap_means_unlimited(self, monkeypatch):
        from meeting_minutes_app.wiki_core import vault_indexer as vi
        monkeypatch.setattr(vi, "_c", lambda k, d=None: 0 if k == "cost.monthly_cap_usd" else d)
        assert vi._embedding_budget_blocked(999.0) == ""

    def test_blocks_when_over_cap(self, monkeypatch):
        from meeting_minutes_app.wiki_core import vault_indexer as vi
        from meeting_minutes_app.common import usage_log
        monkeypatch.setattr(vi, "_c", lambda k, d=None: 1.0 if k == "cost.monthly_cap_usd" else d)
        monkeypatch.setattr(usage_log, "month_to_date_spend", lambda *a, **k: 0.99)
        assert vi._embedding_budget_blocked(0.50) != ""

    def test_allows_when_under_cap(self, monkeypatch):
        from meeting_minutes_app.wiki_core import vault_indexer as vi
        from meeting_minutes_app.common import usage_log
        monkeypatch.setattr(vi, "_c", lambda k, d=None: 1.0 if k == "cost.monthly_cap_usd" else d)
        monkeypatch.setattr(usage_log, "month_to_date_spend", lambda *a, **k: 0.10)
        assert vi._embedding_budget_blocked(0.01) == ""

    def test_gate_failure_does_not_block_indexing(self, monkeypatch):
        """판정이 터져도 인덱싱을 막지 않는다 — 비용 기록은 부수 효과다."""
        from meeting_minutes_app.wiki_core import vault_indexer as vi
        def boom(k, d=None):
            raise RuntimeError("config 없음")
        monkeypatch.setattr(vi, "_c", boom)
        assert vi._embedding_budget_blocked(5.0) == ""


class TestCostSummaryAggregates:
    """비용 대시보드 집계 — 화면만 없었고 백엔드는 거의 다 있었다."""

    def test_by_month_groups_and_excludes_errors(self, fresh_db):
        now = datetime.now()
        _insert("a", now.replace(day=1, hour=1).isoformat(), 0.30)
        _insert("b", now.replace(day=2, hour=1).isoformat(), 0.20)
        _insert("c", now.replace(day=3, hour=1).isoformat(), 9.99, status="error")
        rows = db.cost_by_month(6)
        this_month = [r for r in rows if r["month"] == now.strftime("%Y-%m")]
        assert this_month and this_month[0]["usd"] == pytest.approx(0.50)
        assert this_month[0]["count"] == 2

    def test_by_month_ignores_blank_dates(self, fresh_db):
        """과거 임포트분에 date 가 빈 행이 실제로 있다 — '' 월 버킷이 생기면 안 된다."""
        _insert("a", "", 1.00)
        assert all(r["month"] for r in db.cost_by_month(6))

    def test_by_month_empty_db(self, fresh_db):
        assert db.cost_by_month(6) == []

    def test_by_type_and_top(self, fresh_db):
        now = datetime.now()
        with db._conn() as c:
            for sid, typ, cost in (("a", "meeting", 0.40), ("b", "seminar", 0.10)):
                c.execute(
                    "INSERT INTO sessions (id, title, date, status, type, cost_estimate) "
                    "VALUES (?, ?, ?, 'completed', ?, ?)",
                    (sid, f"제목{sid}", now.replace(day=1, hour=1).isoformat(), typ, cost))
            c.commit()
        types = {r["type"]: r["usd"] for r in db.cost_by_type()}
        assert types == {"meeting": pytest.approx(0.40), "seminar": pytest.approx(0.10)}
        top = db.top_cost_sessions(5)
        assert [t["id"] for t in top] == ["a", "b"]

    def test_summary_endpoint_ok_on_empty_db(self, fresh_db, monkeypatch):
        from fastapi.testclient import TestClient
        from web.backend.app import app
        r = TestClient(app).get("/api/cost/summary")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        for key in ("monthToDateUsd", "monthlyCapUsd", "months", "byType", "top"):
            assert key in body
