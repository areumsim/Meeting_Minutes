"""version.py — 앱 버전·빌드 식별자를 읽는 단일 창구.

`importlib.metadata.version("meeting-minutes")` 은 **정본 배포본에서 쓸 수 없다**.
포터블 배포(scripts/build/build_portable.ps1)는 앱을 `pip install` 하지 않고 소스를
그대로 복사하므로 dist-info 가 없어 항상 PackageNotFoundError 로 떨어진다. 그래서
버전 리터럴은 `meeting_minutes_app/__init__.py` 에 두고 pyproject 가 그걸 읽어간다.

여기서 읽는 값은 회의록 frontmatter 의 녹취 출처 메타(tool_version/tool_build)와
CLI `--version` 이 함께 쓴다 — "이 회의록을 어느 빌드가 만들었나"를 남기기 위한 것이라
개발 실행과 배포본에서 **둘 다** 값이 나와야 의미가 있다.
"""

from __future__ import annotations

import re
from typing import Optional

#: BUILD_INFO.txt 의 `commit   : 9dbf5f8` 줄. 빌드 스크립트가 배포본 루트에 쓴다.
_COMMIT_LINE = re.compile(r"^\s*commit\s*:\s*(\S+)", re.MULTILINE)
_DIRTY_LINE = re.compile(r"^\s*dirty\s*:\s*(\S+)", re.MULTILINE)


def app_version() -> str:
    """앱 버전 문자열(예: "0.1.0"). 어떤 실행 형태에서도 비어 있지 않다."""
    try:
        from meeting_minutes_app import __version__
        return str(__version__ or "").strip() or "0"
    except ImportError:      # pragma: no cover - 패키지가 깨진 경우
        return "0"


def build_commit() -> str:
    """포터블 배포본의 빌드 커밋(예: "9dbf5f8"), 소스 실행이면 ''.

    미커밋 변경이 섞인 빌드는 `9dbf5f8-dirty` 로 돌려준다 — 문제 신고를 받았을 때
    '릴리스 커밋 그대로인가'를 회의록만 보고 구분할 수 있어야 한다.
    """
    info = _read_build_info()
    if not info:
        return ""
    m = _COMMIT_LINE.search(info)
    if not m or m.group(1).lower() in ("unknown", ""):
        return ""
    commit = m.group(1)
    d = _DIRTY_LINE.search(info)
    if d and d.group(1).upper().startswith("YES"):
        commit += "-dirty"
    return commit


def _read_build_info() -> Optional[str]:
    try:
        from meeting_minutes_app.common.app_paths import get_resource_dir
        path = get_resource_dir() / "BUILD_INFO.txt"
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8-sig", errors="replace")
    except Exception:
        return None


def version_label() -> str:
    """사람에게 보여줄 한 줄(예: "meeting-minutes 0.1.0 (build 9dbf5f8)")."""
    commit = build_commit()
    return f"meeting-minutes {app_version()}" + (f" (build {commit})" if commit else "")
