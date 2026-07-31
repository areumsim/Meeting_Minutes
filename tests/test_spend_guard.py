"""자동 실행 경로의 지출 한도 관문과 과금 집계 회귀 테스트.

배경 — 한도(`cost.per_file_cap_usd` / `cost.monthly_cap_usd`)는 파일 업로드와 위키
임베딩 **두 곳에서만** 강제됐다. 폴더 자동 감시·계획 자동화는 검사를 통째로 비켜 갔고,
게다가 `ingestion_pipeline` 이 `web.backend.database` 를 import 하지 않아 DB 세션이
생기지 않는다 → 워처가 태운 돈이 월 합계에서 **영구히 보이지 않았고**, 합계가 실제보다
작게 나와 다른 경로의 한도 판정까지 느슨해졌다.

여기서 고정하는 것:
  1. spend_guard.blocked 가 두 한도를 보고, 판정 실패 시 막지 않는다
  2. 워처가 한도 초과 파일을 처리하지 않고 확인 대기열(queued)에 넣는다
  3. queued 는 매 폴링마다 재시도되지 않는다(터미널 상태)
  4. 처리한 파일의 과금이 usage_log 에 남아 월 합계에 잡힌다
"""

from datetime import datetime

import pytest

from meeting_minutes_app.common import spend_guard, usage_log


class _Cfg:
    """config_loader 대역 — get(path, default) 만 제공한다."""

    def __init__(self, values=None):
        self.values = values or {}

    def get(self, k, d=None):
        return self.values.get(k, d)


@pytest.fixture
def cfg_patch(monkeypatch):
    """spend_guard._c 가 읽는 설정을 테스트에서 갈아끼운다."""
    def _apply(values):
        monkeypatch.setattr(spend_guard, "_c",
                            lambda k, d=None: values.get(k, d))
    return _apply


@pytest.fixture
def usage_db(tmp_path, monkeypatch):
    """usage_log / spend_guard 가 임시 DB 를 쓰게 한다."""
    dbp = tmp_path / "meeting_assistant.db"
    monkeypatch.setattr(usage_log, "_resolve_db_path", lambda p=None: dbp)
    return dbp


class TestBlocked:
    def test_no_caps_never_blocks(self, cfg_patch):
        cfg_patch({})                      # 0 = 무제한(기본값)
        assert spend_guard.blocked(999.0) == ""

    def test_per_item_cap_blocks(self, cfg_patch):
        cfg_patch({"cost.per_file_cap_usd": 0.50})
        assert spend_guard.blocked(0.83) != ""
        assert spend_guard.blocked(0.49) == ""

    def test_per_item_cap_can_be_skipped(self, cfg_patch):
        """임베딩은 '오디오 1건'이 아니므로 1건당 한도의 대상이 아니다."""
        cfg_patch({"cost.per_file_cap_usd": 0.50})
        assert spend_guard.blocked(0.83, check_per_item=False) == ""

    def test_monthly_cap_uses_running_total(self, cfg_patch, usage_db, monkeypatch):
        cfg_patch({"cost.monthly_cap_usd": 1.00})
        monkeypatch.setattr(spend_guard, "month_to_date", lambda: 0.90)
        assert spend_guard.blocked(0.05) == ""       # 0.95 ≤ 1.00
        assert spend_guard.blocked(0.20) != ""       # 1.10 > 1.00

    def test_failure_does_not_block(self, monkeypatch):
        """판정이 깨지면 작업을 막지 않는다 — 안전장치가 고장으로 보이면 안 된다."""
        def _boom(k, d=None):
            raise RuntimeError("config unavailable")
        monkeypatch.setattr(spend_guard, "_c", _boom)
        assert spend_guard.blocked(999.0) == ""


class TestRecord:
    def test_record_lands_in_month_to_date(self, usage_db):
        assert spend_guard.record(spend_guard.KIND_WATCHER, 0.42, note="t") is True
        assert usage_log.month_to_date_spend() == pytest.approx(0.42, rel=1e-6)

    def test_by_kind_separates_automation(self, usage_db):
        spend_guard.record(spend_guard.KIND_WATCHER, 0.10)
        spend_guard.record(spend_guard.KIND_PLAN_AUTOMATION, 0.20)
        spend_guard.record("embedding", 0.05)
        by_kind = usage_log.month_to_date_by_kind()
        automation = sum(v for k, v in by_kind.items()
                         if k in spend_guard.AUTOMATION_KINDS)
        assert automation == pytest.approx(0.30, rel=1e-6)
        # 재생성은 사용자가 버튼을 눌러 시작하므로 자동 실행이 아니다.
        assert spend_guard.KIND_REGENERATE not in spend_guard.AUTOMATION_KINDS


class TestEstimateAudioCost:
    def test_unreadable_duration_returns_zero(self, monkeypatch, tmp_path):
        """길이를 못 재면 (0, 0) — 호출부는 duration 으로 판단해 검사를 건너뛴다."""
        dur, est = spend_guard.estimate_audio_cost(str(tmp_path / "nope.m4a"))
        assert (dur, est) == (0.0, 0.0)

    def test_two_pass_raises_estimate(self, monkeypatch, tmp_path):
        f = tmp_path / "a.m4a"
        f.write_bytes(b"x")
        import meeting_minutes_app.common.spend_guard as sg

        from meeting_minutes_app.meeting_pipeline import meeting_minutes as mm
        monkeypatch.setattr(mm, "audio_duration", lambda p: 600.0)
        one = sg.estimate_audio_cost(str(f), include_minutes=False)
        two = sg.estimate_audio_cost(str(f), include_minutes=False, two_pass=True)
        assert two[0] == one[0] == 600.0
        assert two[1] > one[1]


class TestWatcherSpendGate:
    """AudioWatcher 가 한도를 넘는 파일을 대기열에 넣는가."""

    def _watcher(self, tmp_path, calls):
        from meeting_minutes_app.meeting_pipeline.audio_watcher import AudioWatcher
        w = AudioWatcher(
            watch_folders=[str(tmp_path)],
            state_path=str(tmp_path / "state.json"),
            callback=lambda p: calls.append(p) or {"status": "done"},
            stability_checks=0, stability_interval=0, min_size_mb=0,
        )
        return w

    @pytest.fixture
    def audio_file(self, tmp_path):
        f = tmp_path / "meeting.m4a"
        f.write_bytes(b"0" * 2048)
        return f

    def test_over_cap_is_queued_not_processed(self, tmp_path, audio_file, monkeypatch):
        calls = []
        w = self._watcher(tmp_path, calls)
        monkeypatch.setattr(spend_guard, "estimate_audio_cost",
                            lambda p, **kw: (3600.0, 5.00))
        monkeypatch.setattr(spend_guard, "blocked",
                            lambda est, **kw: "월 한도 초과(테스트)")
        w._handle_file(str(audio_file))
        assert calls == []                             # 처리하지 않았다
        pend = w.pending()
        assert len(pend) == 1
        assert pend[0]["name"] == "meeting.m4a"
        assert pend[0]["est_cost_usd"] == 5.00
        assert "한도" in pend[0]["reason"]

    def test_queued_is_terminal_no_retry_loop(self, tmp_path, audio_file, monkeypatch):
        """대기열 파일을 매 폴링마다 다시 재보면 ffprobe 만 반복 호출하고 결과는 같다."""
        calls = []
        w = self._watcher(tmp_path, calls)
        monkeypatch.setattr(spend_guard, "estimate_audio_cost",
                            lambda p, **kw: (3600.0, 5.00))
        monkeypatch.setattr(spend_guard, "blocked", lambda est, **kw: "초과")
        w._handle_file(str(audio_file))
        assert w._is_processed(str(audio_file)) is True
        # 두 번째 호출은 즉시 반환 — 판정을 다시 하지 않는다.
        seen = []
        monkeypatch.setattr(spend_guard, "estimate_audio_cost",
                            lambda p, **kw: seen.append(p) or (3600.0, 5.00))
        w._handle_file(str(audio_file))
        assert seen == []
        assert calls == []

    def test_approval_clears_queue(self, tmp_path, audio_file, monkeypatch):
        calls = []
        w = self._watcher(tmp_path, calls)
        monkeypatch.setattr(spend_guard, "estimate_audio_cost",
                            lambda p, **kw: (3600.0, 5.00))
        monkeypatch.setattr(spend_guard, "blocked", lambda est, **kw: "초과")
        w._handle_file(str(audio_file))
        assert len(w.pending()) == 1
        w.reprocess(str(audio_file))                   # 사용자 승인
        assert w.pending() == []
        assert w._is_processed(str(audio_file)) is False

    def test_under_cap_processes_and_records(self, tmp_path, audio_file,
                                             monkeypatch, usage_db):
        calls = []
        w = self._watcher(tmp_path, calls)
        monkeypatch.setattr(spend_guard, "estimate_audio_cost",
                            lambda p, **kw: (600.0, 0.12))
        monkeypatch.setattr(spend_guard, "blocked", lambda est, **kw: "")
        w._handle_file(str(audio_file))
        assert calls == [str(audio_file)]
        # 워처 과금이 월 합계에 잡혀야 한다(이게 없어서 영구히 안 보였다).
        assert usage_log.month_to_date_spend() == pytest.approx(0.12, rel=1e-6)
        by_kind = usage_log.month_to_date_by_kind()
        assert by_kind.get(spend_guard.KIND_WATCHER) == pytest.approx(0.12, rel=1e-6)

    def test_skipped_is_not_charged(self, tmp_path, audio_file, monkeypatch, usage_db):
        """skipped 는 STT 를 부르지 않았으므로 과금이 없다."""
        from meeting_minutes_app.meeting_pipeline.audio_watcher import AudioWatcher
        w = AudioWatcher(
            watch_folders=[str(tmp_path)],
            state_path=str(tmp_path / "state.json"),
            callback=lambda p: {"status": "skipped"},
            stability_checks=0, stability_interval=0, min_size_mb=0,
        )
        monkeypatch.setattr(spend_guard, "estimate_audio_cost",
                            lambda p, **kw: (600.0, 0.12))
        monkeypatch.setattr(spend_guard, "blocked", lambda est, **kw: "")
        w._handle_file(str(audio_file))
        assert usage_log.month_to_date_spend() == 0.0

    def test_failure_is_not_charged(self, tmp_path, audio_file, monkeypatch, usage_db):
        """실패 세션을 합계에서 제외하는 기존 규칙(status != 'error')과 맞춘다."""
        from meeting_minutes_app.meeting_pipeline.audio_watcher import AudioWatcher

        def _boom(p):
            raise RuntimeError("stt down")

        w = AudioWatcher(
            watch_folders=[str(tmp_path)],
            state_path=str(tmp_path / "state.json"),
            callback=_boom,
            stability_checks=0, stability_interval=0, min_size_mb=0,
        )
        monkeypatch.setattr(spend_guard, "estimate_audio_cost",
                            lambda p, **kw: (600.0, 0.12))
        monkeypatch.setattr(spend_guard, "blocked", lambda est, **kw: "")
        w._handle_file(str(audio_file))
        assert usage_log.month_to_date_spend() == 0.0


class TestEmbeddingGuardDelegates:
    """vault_indexer 의 기존 판정이 공용 함수로 위임됐는지."""

    def test_delegates_to_spend_guard(self, monkeypatch):
        from meeting_minutes_app.wiki_core import vault_indexer
        seen = {}

        def _fake(est, check_per_item=True):
            seen["est"] = est
            seen["check_per_item"] = check_per_item
            return "한도 초과"

        monkeypatch.setattr(spend_guard, "blocked", _fake)
        assert vault_indexer._embedding_budget_blocked(0.33) == "한도 초과"
        assert seen == {"est": 0.33, "check_per_item": False}
