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


class TestFirstScanQueue:
    """감시 시작 시 폴더에 이미 있던 파일을 전량 처리하지 않는가.

    배경 — `_scan_once` 가 4-worker ThreadPool 로 기존 파일 전량을 즉시 병렬 처리했다.
    즉 감시 폴더를 처음 지정하는 순간 **과거 녹음 전체가 한꺼번에 과금**됐다.
    1건당 한도로는 막히지 않는다 — 각 파일이 한도 이하면 전부 통과하기 때문이다.
    """

    @pytest.fixture
    def folder(self, tmp_path):
        for i in range(3):
            (tmp_path / f"old{i}.m4a").write_bytes(b"0" * 4096)
        return tmp_path

    def _watcher(self, folder, calls):
        from meeting_minutes_app.meeting_pipeline.audio_watcher import AudioWatcher
        return AudioWatcher(
            watch_folders=[str(folder)],
            state_path=str(folder / "state.json"),
            callback=lambda p: calls.append(p) or {"status": "done"},
            stability_checks=0, stability_interval=0, min_size_mb=0,
        )

    @pytest.fixture(autouse=True)
    def _cheap_estimate(self, monkeypatch):
        """개별 파일은 한도 이하다 — 그래도 첫 스캔은 막혀야 한다는 것이 요점."""
        monkeypatch.setattr(spend_guard, "estimate_audio_cost",
                            lambda p, **kw: (1800.0, 0.35))
        monkeypatch.setattr(spend_guard, "blocked", lambda est, **kw: "")

    def test_first_scan_queues_instead_of_processing(self, folder, monkeypatch):
        import meeting_minutes_app.meeting_pipeline.audio_watcher as aw
        monkeypatch.setattr(aw, "_c", lambda k, d=None: d)   # process_existing=False
        calls = []
        w = self._watcher(folder, calls)
        w._scan_once(first_scan=True)
        assert calls == []
        pend = w.pending()
        assert len(pend) == 3
        assert all(p["preexisting"] is True for p in pend)
        # 총액을 사용자에게 보여줄 수 있어야 한다(3 × 0.35).
        assert sum(p["est_cost_usd"] for p in pend) == pytest.approx(1.05, rel=1e-6)

    def test_opt_in_restores_old_behaviour(self, folder, monkeypatch):
        import meeting_minutes_app.meeting_pipeline.audio_watcher as aw
        monkeypatch.setattr(
            aw, "_c",
            lambda k, d=None: True if k == "vault_watcher.process_existing" else d)
        calls = []
        w = self._watcher(folder, calls)
        w._scan_once(first_scan=True)
        assert len(calls) == 3

    def test_normal_scan_still_processes_new_files(self, folder, monkeypatch):
        import meeting_minutes_app.meeting_pipeline.audio_watcher as aw
        monkeypatch.setattr(aw, "_c", lambda k, d=None: d)
        calls = []
        w = self._watcher(folder, calls)
        w._scan_once()                       # first_scan=False
        assert len(calls) == 3

    def test_approval_then_processed(self, folder, monkeypatch):
        import meeting_minutes_app.meeting_pipeline.audio_watcher as aw
        monkeypatch.setattr(aw, "_c", lambda k, d=None: d)
        calls = []
        w = self._watcher(folder, calls)
        w._scan_once(first_scan=True)
        target = w.pending()[0]["path"]
        w.reprocess(target)                  # 사용자 승인
        assert len(w.pending()) == 2
        w._scan_once()
        assert calls == [target]             # 승인한 1건만 처리

    def test_start_uses_first_scan(self, folder, monkeypatch):
        """start() 가 first_scan=True 로 부르는지 — 여기가 실제 전량 과금 지점이었다."""
        import meeting_minutes_app.meeting_pipeline.audio_watcher as aw
        monkeypatch.setattr(aw, "_c", lambda k, d=None: d)
        calls = []
        w = self._watcher(folder, calls)
        seen = {}
        monkeypatch.setattr(w, "_scan_once",
                            lambda first_scan=False: seen.update(first=first_scan))
        # watchdog Observer 는 띄우지 않는다 — 첫 스캔 인자만 확인한다.
        monkeypatch.setattr(w, "_polling_loop", lambda: None)
        monkeypatch.setitem(__import__("sys").modules, "watchdog", None)
        try:
            w.start()
        except Exception:
            pass
        assert seen.get("first") is True

    def test_queue_print_survives_cp949_console(self, folder, monkeypatch, capsys):
        """한국어 Windows 콘솔(cp949)에서 인코딩 오류로 첫 스캔이 죽지 않아야 한다.

        이 print 는 start() 경로에 있어 여기서 터지면 감시가 아예 켜지지 않는다.
        """
        import meeting_minutes_app.meeting_pipeline.audio_watcher as aw
        monkeypatch.setattr(aw, "_c", lambda k, d=None: d)
        calls = []
        w = self._watcher(folder, calls)
        w._scan_once(first_scan=True)
        out = capsys.readouterr().out
        out.encode("cp949")                  # 예외가 나면 테스트 실패
        assert "기존 파일 3건" in out


class TestAutomationPause:
    """전역 일시정지(automation.paused) — 개별 중지와 달리 재시작에도 유지된다."""

    @pytest.fixture
    def folder(self, tmp_path):
        (tmp_path / "new.m4a").write_bytes(b"0" * 4096)
        return tmp_path

    def _watcher(self, folder, calls):
        from meeting_minutes_app.meeting_pipeline.audio_watcher import AudioWatcher
        return AudioWatcher(
            watch_folders=[str(folder)],
            state_path=str(folder / "state.json"),
            callback=lambda p: calls.append(p) or {"status": "done"},
            stability_checks=0, stability_interval=0, min_size_mb=0,
        )

    def test_paused_blocks_handle_file(self, folder, monkeypatch):
        monkeypatch.setattr(spend_guard, "automation_paused", lambda: True)
        calls = []
        w = self._watcher(folder, calls)
        w._handle_file(str(folder / "new.m4a"))
        assert calls == []

    def test_paused_does_not_queue(self, folder, monkeypatch):
        """일시정지는 '아무것도 하지 마라'다 — 대기열에 쌓아 두지도 않는다."""
        import meeting_minutes_app.meeting_pipeline.audio_watcher as aw
        monkeypatch.setattr(aw, "_c", lambda k, d=None: d)
        monkeypatch.setattr(spend_guard, "automation_paused", lambda: True)
        calls = []
        w = self._watcher(folder, calls)
        w._scan_once(first_scan=True)
        assert w.pending() == []
        assert calls == []

    def test_resuming_processes_again(self, folder, monkeypatch):
        import meeting_minutes_app.meeting_pipeline.audio_watcher as aw
        monkeypatch.setattr(aw, "_c", lambda k, d=None: d)
        monkeypatch.setattr(spend_guard, "estimate_audio_cost",
                            lambda p, **kw: (600.0, 0.05))
        monkeypatch.setattr(spend_guard, "blocked", lambda est, **kw: "")
        calls = []
        w = self._watcher(folder, calls)

        paused = {"v": True}
        monkeypatch.setattr(spend_guard, "automation_paused", lambda: paused["v"])
        w._scan_once()
        assert calls == []
        paused["v"] = False                  # 사용자가 해제
        w._scan_once()
        assert len(calls) == 1

    def test_reads_config_key(self, monkeypatch):
        monkeypatch.setattr(spend_guard, "_c",
                            lambda k, d=None: True if k == "automation.paused" else d)
        assert spend_guard.automation_paused() is True
        monkeypatch.setattr(spend_guard, "_c", lambda k, d=None: d)
        assert spend_guard.automation_paused() is False

    def test_plan_watcher_respects_pause(self, monkeypatch, tmp_path):
        """planned 노트를 찾아도 리서치(LLM 과금)를 부르지 않아야 한다."""
        from meeting_minutes_app.meeting_pipeline import plan_watcher as pw
        from meeting_minutes_app.meeting_pipeline import plan_research
        note = tmp_path / "n.md"
        note.write_text("status: planned\n본문", encoding="utf-8")
        called = []
        monkeypatch.setattr(plan_research, "research_planned_note",
                            lambda *a, **k: called.append(1) or "새 본문")

        monkeypatch.setattr(pw, "_automation_paused", lambda: True)
        assert pw._process_file(note, llm=object(), obs=None) is False
        assert called == []                  # 일시정지 중에는 호출 0

        # 해제하면 다시 부른다 — 관문이 영구 차단이 아님을 확인한다.
        monkeypatch.setattr(pw, "_automation_paused", lambda: False)
        monkeypatch.setattr(pw, "_budget_blocked", lambda est=0.0: "")
        assert pw._process_file(note, llm=object(), obs=None) is True
        assert len(called) == 1


class TestGroqFallbackToggle:
    """Groq 는 다른 벤더다 — 키만 있으면 자동 편입되던 것을 명시 토글로 바꿨다."""

    def test_off_by_default_excludes_groq(self, monkeypatch):
        from meeting_minutes_app.meeting_pipeline import stt
        monkeypatch.setattr(stt, "GROQ_FALLBACK_ENABLED", False)
        monkeypatch.setattr(stt, "GROQ_API_KEY", "gsk_test")
        monkeypatch.setattr(stt, "get_api_key", lambda *a, **k: "gsk_test")
        client, model = stt.groq_fallback()
        assert client is None and model == ""

    def test_on_with_key_includes_groq(self, monkeypatch):
        from meeting_minutes_app.meeting_pipeline import stt
        monkeypatch.setattr(stt, "GROQ_FALLBACK_ENABLED", True)
        monkeypatch.setattr(stt, "get_api_key", lambda *a, **k: "gsk_test")
        monkeypatch.setattr(stt, "make_groq_client", lambda *a, **k: object())
        monkeypatch.setattr(stt, "GROQ_STT_MODEL", "whisper-large-v3-turbo")
        client, model = stt.groq_fallback()
        assert client is not None
        assert model == "whisper-large-v3-turbo"

    def test_on_without_key_excludes_groq(self, monkeypatch):
        from meeting_minutes_app.meeting_pipeline import stt
        monkeypatch.setattr(stt, "GROQ_FALLBACK_ENABLED", True)
        monkeypatch.setattr(stt, "get_api_key", lambda *a, **k: "")
        client, model = stt.groq_fallback()
        assert client is None and model == ""

    def test_chain_omits_groq_when_off(self, monkeypatch):
        """체인 조립 단계에서도 빠져야 한다(로컬 단계와 같은 규칙)."""
        from meeting_minutes_app.meeting_pipeline import stt
        monkeypatch.setattr(stt, "get_api_key", lambda name, default="": "")
        monkeypatch.setattr(stt, "GROQ_FALLBACK_ENABLED", False)
        monkeypatch.setattr(stt, "LOCAL_STT_ENABLED", False)
        chain = stt._build_stt_provider_chain("gpt-4o-mini-transcribe")
        assert all(p != "Groq" for p, _m, _c in chain)


class TestRegenerateCost:
    """재생성 비용이 세션에 누적되고 한도를 지나는가."""

    def test_add_session_cost_accumulates(self, tmp_path, monkeypatch):
        from web.backend import database as db
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "a.db")
        db.init_db()
        sid = db.create_session(title="t", source="web", mode="1")
        db.update_session_status(sid, "completed", cost_estimate=0.20)
        db.add_session_cost(sid, 0.08)
        db.add_session_cost(sid, 0.08)
        assert db.get_session(sid)["cost_estimate"] == pytest.approx(0.36, rel=1e-6)

    def test_add_session_cost_ignores_zero_and_negative(self, tmp_path, monkeypatch):
        from web.backend import database as db
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "b.db")
        db.init_db()
        sid = db.create_session(title="t", source="web", mode="1")
        db.update_session_status(sid, "completed", cost_estimate=0.20)
        db.add_session_cost(sid, 0.0)
        db.add_session_cost(sid, -5.0)
        assert db.get_session(sid)["cost_estimate"] == pytest.approx(0.20, rel=1e-6)

    def test_regenerate_cost_is_minutes_only(self):
        """전사는 재사용하므로 STT 과금이 없다 — 회의록 생성 LLM 비용만."""
        from web.backend.api import tools
        from meeting_minutes_app.common import pricing, config_loader as cfg
        m = pricing.current_models(cfg)
        assert tools._regenerate_cost_usd() == pytest.approx(
            pricing.minutes_cost(m["llm"], m["minutes_model"]), rel=1e-6)


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
