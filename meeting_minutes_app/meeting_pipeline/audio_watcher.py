"""
audio_watcher.py — 오디오 파일 폴더 감시 + 자동 처리
======================================================
지정 폴더를 감시하다가 새 오디오 파일이 안정화되면 콜백(처리 파이프라인)을 호출한다.
상태 파일(JSON)로 처리된 파일을 추적해 중복 처리를 방지한다.

watchdog 라이브러리가 있으면 FS 이벤트 기반으로, 없으면 폴링으로 동작한다.

단독 실행 (watch 데몬):
    python run_meeting.py audio-watcher --folders "D:\\Recordings" "C:\\Vault\\recordings"
    python run_meeting.py audio-watcher --reprocess "D:\\Recordings\\file.m4a"

(참고: 파일별로 --type/--translate/--notify 등 세부 옵션이 필요하면
 watcher.py / "legacy-watcher" 명령을 사용하세요.)
"""

from __future__ import annotations

import os
import json
import time
import threading
from pathlib import Path
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set

try:
    from meeting_minutes_app.common import config_loader as _cfg
    _cfg_ok = True
except ImportError:
    _cfg = None  # type: ignore
    _cfg_ok = False


def _c(key: str, default: Any = None) -> Any:
    return _cfg.get(key, default) if _cfg_ok else default


#: 지원 오디오 확장자의 **단일 소스**. 워처와 웹 업로드 검증이 같은 목록을 본다 —
#: 두 곳에 적으면 "워처는 받는데 업로드는 거부"처럼 갈라진다.
DEFAULT_AUDIO_EXTS: Set[str] = {"mp3", "m4a", "wav", "webm", "mp4", "ogg", "flac", "mpga"}


class AudioWatcher:
    """오디오 파일 폴더 감시 + 중복 처리 방지 상태 저장소."""

    def __init__(
        self,
        watch_folders: List[str],
        state_path: str,
        callback: Callable[[str], Any],
        supported_exts: Optional[Set[str]] = None,
        poll_interval: float = 10.0,
        stability_checks: int = 3,
        stability_interval: float = 2.0,
        min_size_mb: float = 0.5,
    ):
        self.watch_folders = [str(f) for f in watch_folders if f]
        self.state_path = state_path
        self.callback = callback
        self.supported_exts = supported_exts or DEFAULT_AUDIO_EXTS
        self.poll_interval = poll_interval
        self.stability_checks = stability_checks
        self.stability_interval = stability_interval
        self.min_size_bytes = int(min_size_mb * 1024 * 1024)
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        #: 지금 이 프로세스에서 처리 중인 파일. 상태 파일의 `processing` 만으로는
        #: 중복 처리를 막을 수 없다 — 그 상태는 **터미널이 아니라서**(크래시 후
        #: 재시도되어야 한다) 다음 스캔이 같은 파일을 다시 집어 든다. 실제로 폴링
        #: 모드에서는 60분짜리 파일 하나를 처리하는 동안 10초마다 재제출돼
        #: **STT 가 중복 과금**됐다. 프로세스 메모리에 두는 것이 맞다: 크래시하면
        #: 자연히 비워져 재시도가 다시 열린다.
        self._inflight: Set[str] = set()
        #: 진행 중인 안전 재스캔 스레드(watchdog 모드) — 중첩 방지용.
        self._rescan_thread: Optional[threading.Thread] = None

    # ── 상태 파일 ──────────────────────────────────────────
    def _load_state(self) -> Dict[str, Any]:
        if not os.path.exists(self.state_path):
            return {}
        try:
            return json.load(open(self.state_path, encoding="utf-8"))
        except Exception:
            return {}

    def _save_state(self, state: Dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.state_path)), exist_ok=True)
        tmp = self.state_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.state_path)
        except Exception as e:
            print(f"[watcher] 상태 저장 실패: {e}")

    #: 더 이상 자동으로 손대지 않는 상태. `queued` 가 여기 든 것은 의도적이다 —
    #: 한도를 넘어 대기열에 넣은 파일을 매 폴링(기본 10초)마다 다시 재보면
    #: ffprobe 를 반복 호출하고 로그를 채우면서도 결과는 늘 같다. 사용자가
    #: 승인(reprocess)하면 상태가 지워져 다시 후보가 된다.
    _TERMINAL_STATUSES = ("done", "skipped", "queued")

    def _is_processed(self, abs_path: str) -> bool:
        with self._lock:
            state = self._load_state()
        entry = state.get(abs_path)
        if not entry:
            return False
        return entry.get("status") in self._TERMINAL_STATUSES

    def pending(self) -> List[Dict[str, Any]]:
        """확인 대기열 — 한도를 넘어 자동 처리하지 않은 파일 목록(FR-011).

        사용자가 승인하면 `reprocess(path)` 로 상태를 지워 다시 후보가 되게 한다.
        """
        with self._lock:
            state = self._load_state()
        out: List[Dict[str, Any]] = []
        for path, entry in (state or {}).items():
            if not isinstance(entry, dict) or entry.get("status") != "queued":
                continue
            out.append({
                "path": path,
                "name": Path(path).name,
                "queued_at": entry.get("processed_at", ""),
                "reason": entry.get("error", ""),
                "est_cost_usd": entry.get("est_cost_usd", 0.0),
                "duration_sec": entry.get("duration_sec", 0.0),
                # 대기 사유 구분 — 화면 문구가 달라야 한다.
                #   preexisting:  감시를 켜기 전부터 있던 파일(전량 과금 방지)
                #   failed_final: N회 시도 후 자동 재시도를 멈춘 파일
                #   그 외:        지출 한도 초과
                "preexisting": bool(entry.get("preexisting", False)),
                "failed_final": bool(entry.get("failed_final", False)),
                "attempts": int(entry.get("attempts") or 0),
            })
        out.sort(key=lambda e: e.get("queued_at") or "")
        return out

    def _mark_processed(self, abs_path: str, status: str,
                        note_path: str = "", error: str = "",
                        extra: Optional[Dict[str, Any]] = None) -> None:
        with self._lock:
            state = self._load_state()
            prev = state.get(abs_path) or {}
            entry = {
                "processed_at": datetime.now().isoformat(timespec="seconds"),
                "status": status,
                "note_path": note_path,
                "error": error,
            }
            # 시도 횟수는 상태가 바뀌어도 이어받는다. 이 항목은 매번 새로 만들어지므로
            # 이어받지 않으면 처리 시작(`processing`) 표시가 카운터를 지워 재시도
            # 상한이 영원히 걸리지 않는다. `reprocess()` 는 항목째 지우므로 그때만
            # 초기화된다(= 사용자가 승인하면 다시 3회).
            if isinstance(prev.get("attempts"), int):
                entry["attempts"] = prev["attempts"]
            if extra:
                entry.update(extra)
            state[abs_path] = entry
            self._save_state(state)

    #: 같은 파일을 자동으로 다시 시도하는 최대 횟수. 넘으면 확인 대기열로 보낸다.
    #:
    #: 무한 재시도가 위험한 이유: 실패가 **STT 이후**(회의록 생성·발행)에서 나면
    #: 재시도마다 STT 가 다시 과금된다. 상태 `failed` 는 터미널이 아니므로 폴링
    #: 모드에서는 10초마다 되돌아왔다 — 60분 녹음이면 시간당 수십 달러가 될 수 있다.
    #: 3회로 두는 것은 일시적 오류(네트워크 순단·429)는 자동 복구하되 영구 오류는
    #: 사람에게 넘기기 위해서다. 승인(reprocess)하면 카운터가 지워져 다시 3회 열린다.
    MAX_PROCESS_ATTEMPTS = 3

    def _claim(self, abs_path: str) -> bool:
        """이 파일의 처리를 선점한다. 이미 이 프로세스에서 처리 중이면 False.
        (중복 제출 방지 — `_inflight` 주석 참조)"""
        with self._lock:
            if abs_path in self._inflight:
                return False
            self._inflight.add(abs_path)
            return True

    def _release(self, abs_path: str) -> None:
        with self._lock:
            self._inflight.discard(abs_path)

    def _mark_failed(self, abs_path: str, error: str, note_path: str = "") -> None:
        """실패 기록 — 시도 횟수를 세고, 한도에 닿으면 확인 대기열로 보낸다.

        대기열(`queued`)로 보내는 것은 터미널 상태라 자동 재시도가 멈추고, 사용자가
        기존 [승인] 버튼으로 다시 돌릴 수 있다 — 새 UI 를 만들지 않고 이미 있는
        확인 흐름을 그대로 쓴다(한도 초과·기존 파일과 같은 자리)."""
        with self._lock:
            prev = self._load_state().get(abs_path) or {}
        attempts = int(prev.get("attempts") or 0) + 1
        if attempts >= self.MAX_PROCESS_ATTEMPTS:
            self._mark_processed(
                abs_path, "queued", note_path=note_path,
                error=f"{attempts}회 시도했지만 실패했습니다: {error}",
                extra={"attempts": attempts, "failed_final": True},
            )
            print(f"[watcher] {attempts}회 실패 — 자동 재시도를 멈추고 확인 대기열로 "
                  f"보냅니다: {Path(abs_path).name}")
            return
        self._mark_processed(abs_path, "failed", note_path=note_path, error=error,
                             extra={"attempts": attempts})

    def reprocess(self, abs_path: str) -> None:
        """상태를 초기화해 재처리를 허용한다(시도 횟수도 함께 지워진다)."""
        with self._lock:
            state = self._load_state()
            state.pop(abs_path, None)
            self._save_state(state)
        print(f"[watcher] 재처리 상태 초기화: {Path(abs_path).name}")

    # ── 파일 안정성 체크 ──────────────────────────────────
    def _is_audio(self, path: str) -> bool:
        ext = Path(path).suffix.lstrip(".").lower()
        return ext in self.supported_exts

    def _is_stable(self, path: str) -> bool:
        """파일 크기가 stability_checks 회 연속으로 변하지 않으면 True."""
        if not os.path.isfile(path):
            return False
        if os.path.getsize(path) < self.min_size_bytes:
            return False
        try:
            prev_size = os.path.getsize(path)
        except OSError:
            return False
        for _ in range(self.stability_checks):
            time.sleep(self.stability_interval)
            try:
                size = os.path.getsize(path)
            except OSError:
                return False
            if size != prev_size or size == 0:
                return False
            prev_size = size
        return True

    # ── 자동 실행 관문 ────────────────────────────────────
    def _paused(self) -> bool:
        """전역 일시정지(automation.paused)면 아무 파일도 처리하지 않는다.

        스레드를 죽이지 않고 여기서 막는 이유: 사용자가 스위치를 되돌리면 다음
        스캔부터 곧바로 다시 동작해야 하고, 감시 자체를 멈췄다 켜면 첫 스캔 규칙
        (기존 파일 대기열)이 다시 적용돼 혼란스럽다.
        """
        try:
            from meeting_minutes_app.common import spend_guard
            return spend_guard.automation_paused()
        except Exception:
            return False

    # ── 지출 한도 관문 ────────────────────────────────────
    def _spend_gate(self, abs_path: str) -> bool:
        """한도를 넘으면 확인 대기열에 넣고 True(=처리하지 않음).

        워처는 지금까지 한도 검사를 전혀 받지 않았다. 업로드 경로에만 있던
        `cost.per_file_cap_usd` / `cost.monthly_cap_usd` 를 같은 판정 함수로 적용한다.
        길이를 못 재면(duration=0) 검사를 건너뛴다 — 업로드 경로와 같은 규칙이다.
        """
        try:
            from meeting_minutes_app.common import spend_guard
        except Exception:
            return False                  # 판정 모듈이 없으면 막지 않는다
        duration, est = spend_guard.estimate_audio_cost(abs_path)
        if duration <= 0:
            return False
        reason = spend_guard.blocked(est)
        if not reason:
            return False
        # 콘솔 인코딩이 cp949 인 환경(한국어 Windows 콘솔)에서는 em-dash 가
        # UnicodeEncodeError 를 낸다. 이 print 는 start() 첫 스캔 경로에서도 불리므로
        # 여기서 터지면 감시가 아예 켜지지 않는다. ASCII 구분자만 쓴다.
        print(f"[watcher] 지출 한도로 자동 처리 보류: {Path(abs_path).name} ({reason})")
        self._mark_processed(
            abs_path, "queued", error=reason,
            extra={"est_cost_usd": round(est, 4), "duration_sec": round(duration, 1)},
        )
        return True

    def _record_spend(self, abs_path: str) -> None:
        """처리한 파일의 과금을 usage_log 에 남긴다.

        워처 경로는 DB 세션을 만들지 않는다(`ingestion_pipeline` 이 `web.backend`
        를 import 하지 않는다). 그래서 이 기록이 없으면 워처가 태운 돈이 월 합계에서
        **영구히 보이지 않고**, 합계가 실제보다 작게 나와 다른 경로의 한도 판정까지
        느슨해진다. 추정치이지만 세션의 `cost_estimate` 도 추정치이므로 성질은 같다.
        """
        try:
            from meeting_minutes_app.common import spend_guard
            duration, est = spend_guard.estimate_audio_cost(abs_path)
            if duration <= 0 or est <= 0:
                return
            spend_guard.record(
                spend_guard.KIND_WATCHER, est,
                units=round(duration / 60.0, 2), unit_kind="min",
                note=f"폴더 자동 감시: {Path(abs_path).name}",
            )
        except Exception:
            pass                          # 기록 실패가 처리 결과를 뒤집지 않는다

    # ── 처리 호출 ─────────────────────────────────────────
    def _handle_file(self, abs_path: str) -> None:
        if self._is_processed(abs_path):
            return
        # 선점하지 못했으면 이미 이 프로세스가 처리 중이다 — 조용히 물러난다.
        # (watchdog 이벤트와 안전 재스캔이 같은 파일을 동시에 집을 수 있다)
        if not self._claim(abs_path):
            return
        try:
            self._process_claimed(abs_path)
        finally:
            self._release(abs_path)

    def _process_claimed(self, abs_path: str) -> None:
        if self._paused():
            return
        if not self._is_stable(abs_path):
            # 아직 쓰이는 중(녹음기가 직접 쓰거나 복사 중)이다. **여기서 끝내면 안
            # 된다** — watchdog 은 on_created 만 듣고 on_modified 는 듣지 않으므로,
            # 안전 재스캔(start 참조)이 없으면 이 파일은 다시 볼 기회가 없다.
            print(f"[watcher] 파일 불안정(복사·녹음 중?), 다음 스캔에서 다시 확인: "
                  f"{Path(abs_path).name}")
            return
        # 한도 검사는 안정성 확인 뒤에 한다 — 복사 중인 파일은 길이가 엉뚱하게 나온다.
        if self._spend_gate(abs_path):
            return
        print(f"[watcher] 새 파일 처리 시작: {Path(abs_path).name}")
        self._mark_processed(abs_path, "processing")
        try:
            callback_result = self.callback(abs_path)
            if isinstance(callback_result, dict):
                status = str(callback_result.get("status") or "done")
                note_path = str(callback_result.get("note_path") or "")
                error = str(callback_result.get("error") or "")
                if status in ("done", "skipped"):
                    self._mark_processed(abs_path, status, note_path=note_path, error=error)
                    # skipped 는 STT 를 부르지 않았으므로 과금이 없다.
                    if status == "done":
                        self._record_spend(abs_path)
                else:
                    self._mark_failed(abs_path, error or status, note_path=note_path)
            else:
                self._mark_processed(abs_path, "done")
                self._record_spend(abs_path)
        except Exception as e:
            self._mark_failed(abs_path, str(e))
            print(f"[watcher] 처리 실패: {e}")

    # ── 폴링 스캔 ─────────────────────────────────────────
    def _queue_preexisting(self, candidates: List[str]) -> None:
        """감시를 켜기 전부터 있던 파일을 처리하지 않고 확인 대기열에 넣는다.

        과거에는 첫 스캔이 이 파일들을 4-worker 로 즉시 병렬 처리했다. 즉 감시
        폴더를 처음 지정하는 순간 **과거 녹음 전체가 한꺼번에 과금**됐다.
        1건당 한도로도 막히지 않는다 — 각 파일이 한도 이하이면 전부 통과한다.
        그래서 건수와 총액을 사용자에게 보여주고 승인을 받는다(FR-011 확인 대기열).
        """
        total = 0.0
        for path in candidates:
            est = 0.0
            duration = 0.0
            try:
                from meeting_minutes_app.common import spend_guard
                duration, est = spend_guard.estimate_audio_cost(path)
            except Exception:
                pass
            total += est
            self._mark_processed(
                path, "queued",
                error="감시를 켜기 전부터 폴더에 있던 파일입니다. 승인하면 처리합니다.",
                extra={"est_cost_usd": round(est, 4),
                       "duration_sec": round(duration, 1),
                       "preexisting": True},
            )
        print(f"[watcher] 기존 파일 {len(candidates)}건은 자동 처리하지 않았습니다 "
              f"(예상 ${total:.2f}). [설정] > 폴더 자동 감시 > 확인 대기열 에서 "
              f"승인하면 처리합니다.")

    def _scan_once(self, first_scan: bool = False) -> None:
        import glob
        from concurrent.futures import ThreadPoolExecutor
        candidates = []
        for folder in self.watch_folders:
            if not os.path.isdir(folder):
                continue
            for fpath in glob.glob(os.path.join(folder, "**", "*"), recursive=True):
                if os.path.isfile(fpath) and self._is_audio(fpath):
                    abs_path = os.path.abspath(fpath)
                    if not self._is_processed(abs_path):
                        candidates.append(abs_path)
        if not candidates:
            return
        if self._paused():
            # 대기열에 넣지도 않는다 — 일시정지는 '아무것도 하지 마라'는 뜻이고,
            # 스위치를 되돌리면 이 파일들이 그대로 다시 후보가 되어야 한다.
            return
        # 첫 스캔에서 발견된 것은 '감시 중에 새로 생긴 파일'이 아니라 '원래 있던 파일'이다.
        # 기본값은 처리하지 않고 대기열에 넣는 것 — 옛 동작이 필요하면 설정으로 켠다.
        if first_scan and not bool(_c("vault_watcher.process_existing", False)):
            self._queue_preexisting(candidates)
            return
        with ThreadPoolExecutor(max_workers=4) as ex:
            for path in candidates:
                ex.submit(self._handle_file, path)

    def _spawn_rescan(self) -> None:
        """안전 재스캔을 **별도 스레드**로 돌린다(watchdog 대기 루프 보호).

        `_scan_once()` 는 ThreadPoolExecutor 를 `with` 로 열어 제출한 작업이 전부
        끝날 때까지 블로킹한다. 대기 루프에서 직접 부르면 60분짜리 파일 하나를
        처리하는 동안 `stop()` 이 먹지 않아 서버 종료가 그만큼 늦어진다.
        직전 재스캔이 아직 돌고 있으면 새로 띄우지 않는다(중첩 방지 — 중복 처리는
        `_claim()` 이 막지만 스레드가 쌓이는 것은 별개 문제다)."""
        t = self._rescan_thread
        if t is not None and t.is_alive():
            return

        def _run() -> None:
            try:
                self._scan_once()
            except Exception as e:
                print(f"[watcher] 안전 재스캔 실패(무시): {e}")

        self._rescan_thread = threading.Thread(
            target=_run, name="watcher-rescan", daemon=True)
        self._rescan_thread.start()

    def _polling_loop(self) -> None:
        print(f"[watcher] 폴링 모드 (interval={self.poll_interval}s)")
        while not self._stop_event.is_set():
            self._scan_once()
            self._stop_event.wait(self.poll_interval)

    # ── 감시 시작/중지 ────────────────────────────────────
    #: watchdog 모드에서도 주기적으로 폴더를 다시 훑는 간격의 하한(초).
    #:
    #: FS 이벤트만으로는 부족하다 — `on_created` 는 파일이 **만들어지는 순간** 오는데,
    #: 그때는 아직 쓰이는 중이라 `_is_stable()` 이 False 를 준다. 이 클래스는
    #: `on_modified` 를 듣지 않으므로 그대로 두면 그 파일을 다시 볼 기회가 영영 없다
    #: (앱을 재시작해야 첫 스캔이 줍는데, 그때는 '기존 파일'로 분류돼 승인이 필요하다).
    #: 녹음기가 감시 폴더에 직접 쓰거나 네트워크 드라이브에서 복사하는 경우가 정확히
    #: 이 조건이다. 재스캔은 이벤트를 대체하지 않고 **놓친 것만 줍는 안전망**이라
    #: 하한을 두어 큰 폴더에서 glob 이 자주 돌지 않게 한다.
    SAFETY_RESCAN_MIN_SEC = 60.0

    def start(self) -> None:
        """감시를 시작한다. watchdog 있으면 FS 이벤트 기반(+안전 재스캔), 없으면 폴링."""
        # 시작 시 기존 파일 스캔 — 처리하지 않고 대기열에 넣는다(_scan_once 참조).
        print(f"[watcher] 감시 폴더: {self.watch_folders}")
        self._scan_once(first_scan=True)

        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler

            watcher = self

            class _Handler(FileSystemEventHandler):
                def on_created(self, event):
                    if event.is_directory:
                        return
                    path = os.path.abspath(event.src_path)
                    if watcher._is_audio(path):
                        threading.Thread(
                            target=watcher._handle_file,
                            args=(path,),
                            daemon=True,
                        ).start()

                def on_moved(self, event):
                    # 이동으로 완료된 파일(다운로드 등)
                    if not event.is_directory:
                        path = os.path.abspath(event.dest_path)
                        if watcher._is_audio(path):
                            threading.Thread(
                                target=watcher._handle_file,
                                args=(path,),
                                daemon=True,
                            ).start()

            observer = Observer()
            handler = _Handler()
            for folder in self.watch_folders:
                if os.path.isdir(folder):
                    observer.schedule(handler, folder, recursive=True)
            observer.start()
            rescan_every = max(self.poll_interval, self.SAFETY_RESCAN_MIN_SEC)
            print(f"[watcher] watchdog FS 이벤트 모드 시작 "
                  f"(안전 재스캔 {rescan_every:.0f}초)")
            try:
                next_scan = time.monotonic() + rescan_every
                while not self._stop_event.is_set():
                    self._stop_event.wait(1)
                    if self._stop_event.is_set():
                        break
                    if time.monotonic() < next_scan:
                        continue
                    next_scan = time.monotonic() + rescan_every
                    self._spawn_rescan()
            finally:
                observer.stop()
                observer.join()

        except ImportError:
            self._polling_loop()

    def stop(self) -> None:
        self._stop_event.set()

    # ── 팩토리 ────────────────────────────────────────────
    @classmethod
    def from_config(cls, callback: Callable[[str], Any]) -> "AudioWatcher":
        folders = list(_c("vault_watcher.watch_folders", []) or [])
        state_path = _c("vault_watcher.processed_state_path", "data/processed_audio.json")
        exts = set(_c("vault_watcher.supported_extensions", list(DEFAULT_AUDIO_EXTS)))
        poll = float(_c("vault_watcher.poll_interval", 10))
        checks = int(_c("vault_watcher.stability_checks", 3))
        interval = float(_c("vault_watcher.stability_interval", 2.0))
        min_mb = float(_c("vault_watcher.min_size_mb", 0.5))
        return cls(
            watch_folders=folders,
            state_path=state_path,
            callback=callback,
            supported_exts=exts,
            poll_interval=poll,
            stability_checks=checks,
            stability_interval=interval,
            min_size_mb=min_mb,
        )


def _default_callback(audio_path: str) -> Dict[str, Any]:
    """기본 콜백: ingestion_pipeline.ingest_file() 호출."""
    from meeting_minutes_app.meeting_pipeline.ingestion_pipeline import ingest_file
    result = ingest_file(audio_path)
    status = result.get("status", "?")
    note_path = result.get("note_path", "")
    error = result.get("error", "")
    if status == "done":
        print(f"[watcher] ✅ 완료: {note_path}")
    else:
        print(f"[watcher] ❌ {status}: {error}")
    return result


def main():
    import argparse
    ap = argparse.ArgumentParser(description="오디오 폴더 감시 데몬")
    ap.add_argument("--folders", nargs="+", default=[], help="감시할 폴더 경로들")
    ap.add_argument("--state", default="", help="상태 파일 경로")
    ap.add_argument("--interval", type=float, default=0, help="폴링 간격(초). 0=config값")
    ap.add_argument("--reprocess", default="", help="특정 파일 상태 초기화 후 재처리")
    args = ap.parse_args()

    folders = args.folders or list(_c("vault_watcher.watch_folders", []) or [])
    if not folders:
        print("[watcher] 오류: --folders 로 감시 폴더를 지정하거나 config.vault_watcher.watch_folders 설정")
        return 1

    state_path = args.state or _c("vault_watcher.processed_state_path", "data/processed_audio.json")

    watcher = AudioWatcher.from_config(_default_callback)
    watcher.watch_folders = folders
    watcher.state_path = state_path
    if args.interval > 0:
        watcher.poll_interval = args.interval

    if args.reprocess:
        watcher.reprocess(os.path.abspath(args.reprocess))

    try:
        watcher.start()
    except KeyboardInterrupt:
        print("\n[watcher] 중지")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main() or 0)
