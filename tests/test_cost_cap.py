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
