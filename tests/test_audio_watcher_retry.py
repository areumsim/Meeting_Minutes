"""폴더 자동 감시의 재시도·중복 처리 회귀 테스트.

이 경로는 **무인으로 돌면서 STT 를 부른다** — 여기서 같은 파일을 두 번 처리하거나
실패를 무한히 재시도하면 사용자가 모르는 사이에 돈이 나간다. 이 파일이 고정하는 것:

  1. 처리 중인 파일은 다시 집어 들지 않는다(중복 STT 과금 방지).
     상태 파일의 `processing` 은 터미널이 아니라서(크래시 후 재시도되어야 한다)
     그것만으로는 못 막는다 — 폴링 모드에서 실제로 10초마다 재제출됐다.
  2. 실패는 유한 횟수만 자동 재시도하고, 넘으면 확인 대기열로 보낸다.
     실패가 STT **이후** 단계에서 나면 재시도마다 STT 가 다시 과금된다.
  3. 아직 쓰이는 중인 파일은 버리지 않는다 — 다음 스캔에서 다시 본다.
"""

import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meeting_minutes_app.meeting_pipeline.audio_watcher import AudioWatcher  # noqa: E402


def src_of_spawn(src: str) -> str:
    """`_spawn_rescan` 메서드 본문만 잘라낸다(다음 메서드 정의 전까지)."""
    body = src.split("def _spawn_rescan", 1)[1]
    return body.split("\n    def ", 1)[0]


@pytest.fixture
def watch_dir(tmp_path):
    d = tmp_path / "recordings"
    d.mkdir()
    return d


def _make_audio(folder: Path, name: str = "a.mp3", mb: float = 1.0) -> Path:
    p = folder / name
    p.write_bytes(b"\0" * int(mb * 1024 * 1024))
    return p


def _watcher(tmp_path, watch_dir, callback, **kw) -> AudioWatcher:
    w = AudioWatcher(
        watch_folders=[str(watch_dir)],
        state_path=str(tmp_path / "state.json"),
        callback=callback,
        # 테스트에서 안정성 대기로 6초를 쓰지 않는다(판정 로직이 아니라 재시도가 대상)
        stability_checks=1, stability_interval=0.0, min_size_mb=0.1,
        **kw,
    )
    # 지출 한도·일시정지 관문은 이 테스트의 대상이 아니다(각자 전용 테스트가 있다).
    w._spend_gate = lambda p: False          # type: ignore[method-assign]
    w._paused = lambda: False                # type: ignore[method-assign]
    w._record_spend = lambda p: None         # type: ignore[method-assign]
    return w


def _state(w: AudioWatcher) -> dict:
    return json.loads(Path(w.state_path).read_text(encoding="utf-8"))


class TestNoDuplicateProcessing:
    def test_concurrent_handles_run_the_callback_once(self, tmp_path, watch_dir):
        """watchdog 이벤트와 안전 재스캔이 같은 파일을 동시에 집어도 1회만 처리한다."""
        audio = _make_audio(watch_dir)
        started = threading.Event()
        calls = []

        def _slow(path):
            calls.append(path)
            started.set()
            time.sleep(0.4)            # 처리 중 — 이 사이에 두 번째 호출이 들어온다
            return {"status": "done"}

        w = _watcher(tmp_path, watch_dir, _slow)
        t = threading.Thread(target=w._handle_file, args=(str(audio),))
        t.start()
        assert started.wait(2.0)
        w._handle_file(str(audio))     # 처리 중에 다시 집어 든다
        t.join(5.0)

        assert len(calls) == 1, "처리 중인 파일이 다시 제출됐다(중복 STT 과금)"
        assert _state(w)[str(audio)]["status"] == "done"

    def test_claim_is_released_even_when_callback_raises(self, tmp_path, watch_dir):
        """예외로 끝나도 선점이 풀려야 한다 — 안 풀리면 그 파일은 영영 재시도 불가."""
        audio = _make_audio(watch_dir)
        w = _watcher(tmp_path, watch_dir, lambda p: (_ for _ in ()).throw(RuntimeError("x")))
        w._handle_file(str(audio))
        assert w._inflight == set()


class TestBoundedRetry:
    def _always_fail(self, calls):
        def _cb(path):
            calls.append(path)
            return {"status": "failed", "error": "회의록 생성 실패"}
        return _cb

    def test_retries_are_capped_then_queued(self, tmp_path, watch_dir):
        """N회 실패하면 자동 재시도를 멈추고 확인 대기열로 보낸다.

        실패가 STT 이후에서 나면 재시도마다 STT 가 다시 과금된다 — 무한 재시도는
        시간당 수십 달러가 될 수 있다."""
        audio = _make_audio(watch_dir)
        calls = []
        w = _watcher(tmp_path, watch_dir, self._always_fail(calls))

        for _ in range(6):             # 상한(3)보다 많이 돌려 본다
            w._handle_file(str(audio))

        assert len(calls) == AudioWatcher.MAX_PROCESS_ATTEMPTS
        entry = _state(w)[str(audio)]
        assert entry["status"] == "queued"          # 터미널 — 더 이상 자동 재시도 없음
        assert entry["attempts"] == AudioWatcher.MAX_PROCESS_ATTEMPTS
        assert entry["failed_final"] is True
        assert "3회 시도했지만 실패" in entry["error"]

    def test_queued_failure_shows_up_for_approval(self, tmp_path, watch_dir):
        """새 UI 를 만들지 않고 기존 확인 대기열에 뜬다 — 승인하면 다시 3회 열린다."""
        audio = _make_audio(watch_dir)
        calls = []
        w = _watcher(tmp_path, watch_dir, self._always_fail(calls))
        for _ in range(4):
            w._handle_file(str(audio))

        items = w.pending()
        assert [i["path"] for i in items] == [str(audio)]
        assert items[0]["failed_final"] is True and items[0]["preexisting"] is False

        w.reprocess(str(audio))        # 사용자가 [승인]
        calls.clear()
        for _ in range(6):
            w._handle_file(str(audio))
        assert len(calls) == AudioWatcher.MAX_PROCESS_ATTEMPTS   # 카운터가 초기화됐다

    def test_transient_failure_then_success_clears_the_counter(self, tmp_path, watch_dir):
        """일시적 실패는 자동 복구되어야 한다(그래서 상한이 1이 아니다)."""
        audio = _make_audio(watch_dir)
        calls = []

        def _flaky(path):
            calls.append(path)
            if len(calls) == 1:
                return {"status": "failed", "error": "일시적 네트워크 오류"}
            return {"status": "done", "note_path": "n.md"}

        w = _watcher(tmp_path, watch_dir, _flaky)
        w._handle_file(str(audio))
        assert _state(w)[str(audio)]["status"] == "failed"
        w._handle_file(str(audio))
        assert _state(w)[str(audio)]["status"] == "done"
        assert len(calls) == 2


class TestUnstableFileIsRetriedLater:
    def test_unstable_file_is_not_marked_and_is_picked_up_next_scan(
            self, tmp_path, watch_dir):
        """아직 쓰이는 중인 파일은 상태를 남기지 않는다 — 다음 스캔에서 다시 본다.

        watchdog 은 on_created 만 듣고 on_modified 는 듣지 않는다. 여기서 상태를
        남겨 버리면(또는 안전 재스캔이 없으면) 녹음기가 폴더에 직접 쓰는 파일은
        영영 처리되지 않는다."""
        audio = _make_audio(watch_dir)
        calls = []
        w = _watcher(tmp_path, watch_dir, lambda p: calls.append(p) or {"status": "done"})

        unstable = {"v": True}
        w._is_stable = lambda p: not unstable["v"]   # type: ignore[method-assign]

        w._handle_file(str(audio))
        assert calls == []
        assert not Path(w.state_path).exists() or str(audio) not in _state(w)

        unstable["v"] = False                        # 쓰기가 끝났다
        w._handle_file(str(audio))
        assert calls == [str(audio)]

    def test_watchdog_mode_declares_a_safety_rescan(self):
        """안전 재스캔 간격이 실재해야 한다 — 없으면 위 시나리오가 복구되지 않는다."""
        assert AudioWatcher.SAFETY_RESCAN_MIN_SEC >= 30
        src = Path("meeting_minutes_app/meeting_pipeline/audio_watcher.py").read_text(
            encoding="utf-8")
        # watchdog 분기 안에서 _scan_once 를 부르는 코드가 있어야 한다
        wd = src.split("from watchdog.observers import Observer", 1)[1]
        assert "self._spawn_rescan()" in wd
        # 재스캔은 대기 루프를 막지 않아야 한다(stop() 응답성) —
        # _scan_once 는 풀이 끝날 때까지 블로킹한다
        assert "daemon=True" in src_of_spawn(src)
