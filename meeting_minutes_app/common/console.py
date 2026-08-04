"""console.py — Windows 콘솔(cp949)에서 한국어·이모지 출력이 깨지지 않게 하는 공통 처리.

이 6줄이 리포 안 **8개 파일에 세 가지 철자로** 복제돼 있었다(`_s` / `_stream` /
stdout·stderr 를 각각 푼 형태). 같은 규칙이 복사되면 갈라진다는 이 리포의 반복 패턴
(단가 표 4곳·노트 판정 2곳, CLAUDE.md)이고, 실제로 이미 갈라져 있었다:

  `meeting_pipeline/meeting_minutes.py` 만 `getattr` 가드 없이 `sys.stdout.encoding`
  을 직접 읽어서, **`sys.stdout` 이 None 이면 import 단계에서 AttributeError** 로
  죽는다. 포터블 배포는 `pythonw.exe` 로 뜨고(출력 리다이렉션 없음) 그때 stdout 은
  None 이다 — 지금 배포본이 멀쩡한 것은 `run_ui_exe._fill_std_streams()` 가 devnull
  로 먼저 채워 주기 때문일 뿐이다. 방어가 모듈이 아니라 런처에 있어서, 다른
  엔트리포인트(pythonw 로 CLI 를 부르는 스크립트 등)에서는 여전히 터진다.

그래서 판정을 여기 하나로 모으고 **가장 방어적인 형태**로 통일한다.

라이브러리 모듈에서 import 시점에 부르는 것은 원래 바람직하지 않다(프로세스 전역
스트림을 라이브러리가 바꾼다). 다만 이 리포의 해당 모듈들은 전부 CLI 엔트리를 겸하고
있고 기존 동작이 그러하므로 **행동을 바꾸지 않는다** — 새 모듈은 `main()` 안에서만
부르는 쪽을 택한다(`wiki_core/facilitation.py` 선례).

`web/backend/app.py` 는 **일부러 이 모듈을 쓰지 않는다.** 그 파일의 블록은
`meeting_minutes_app` 이 sys.path 에 올라오기 전에 실행될 수 있는 자리라
의존성 0 을 유지한다(같은 이유로 `sqlite_util` 이 `graph_db` 를 흡수하지 않는다).
"""

from __future__ import annotations

import sys

#: 재설정 대상 인코딩. 이 목록이 판정의 단일 소스다.
_LEGACY_ENCODINGS = ("cp949", "euc-kr", "ansi")


def force_utf8_console() -> None:
    """stdout/stderr 가 레거시 한국어 코드페이지면 UTF-8 로 재설정한다.

    안전 규칙 — 어떤 상황에서도 예외를 올리지 않는다:
      - `sys.stdout` 이 **None** 일 수 있다(pythonw: 콘솔 없음). `getattr` 로 받는다.
      - `reconfigure` 가 없을 수 있다(감싼 스트림·구버전 객체).
      - 이미 UTF-8 이면 아무것도 하지 않는다.
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        enc = getattr(stream, "encoding", None)
        if not enc or enc.lower() not in _LEGACY_ENCODINGS:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
