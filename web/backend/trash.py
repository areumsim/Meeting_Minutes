"""trash.py — 결과 폴더를 지우지 않고 되돌릴 수 있게 치운다.

세션 삭제는 두 단계다.

1. **휴지통(soft delete)** — DB 의 `deleted_at` 만 세운다. 파일은 손대지 않는다.
2. **완전 삭제(purge)** — DB 행을 지우고, 결과 폴더를 여기서 OS 휴지통으로 보낸다.

왜 `shutil.rmtree` 가 아닌가 — 폴더 안에는 회의록·전사·오디오가 들어 있다. 사용자가
'완전 삭제'를 눌렀다 해도 그것이 **회복 불가**여야 할 이유는 없고, PRD §17 은 "삭제는
휴지통 기본"을 확정 결정으로 두고 있다.

`Send2Trash` 가 없으면(포터블 빌드에서 빠지는 등) 데이터 폴더 안의 `.trash/` 로 옮긴다.
조용히 rmtree 로 떨어지지 않는다 — 배포본에서만 복구 불가가 되는 것이 최악이다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional


def _resolve(path: str | Path) -> Path:
    """상대 경로를 **데이터 베이스 기준**으로 해석한다(CWD 기준이 아니다).

    이 리포에 이미 있는 규칙이다 — `api/batch.py` 가 새 출력 폴더를 만들 때
    `app_paths.get_output_dir()` 를 쓰는 이유가 "엔트리포인트에 따라 CWD 가 달라지면
    산출물·스캐너 위치가 어긋난다"이다. 여기서 그 규칙을 쓰지 않아 실제로 갈라졌다:

    포터블은 `run_ui_exe.setup_paths()` 가 데이터 폴더로 `os.chdir` 하므로, DB 에
    상대 `output_dir` 이 들어 있으면 CWD 기준으로 풀려 **폴더가 있는데도 "없다"** 고
    판정했다. 그러면 purge 가 그대로 진행돼 **고아 폴더가 남고** 응답은
    `folder_removed: true` 로 거짓 보고를 했다 — FR-001 이 없애려던 결함 그 자체다.
    """
    p = Path(path)
    if p.is_absolute():
        return p
    try:
        from meeting_minutes_app.common import app_paths
        return app_paths.get_base_dir() / p
    except Exception:
        return p


def move_to_trash(path: str | Path) -> tuple[bool, str]:
    """(성공?, 사람이 읽을 결과 설명). 경로가 없으면 성공으로 본다(치울 것이 없다)."""
    p = _resolve(path)
    if not p.exists():
        return True, "폴더가 이미 없습니다."

    try:
        from send2trash import send2trash
        send2trash(str(p))
        return True, "결과 폴더를 휴지통으로 보냈습니다."
    except ImportError:
        pass
    except Exception as e:
        # 네트워크 드라이브·권한 등으로 OS 휴지통이 실패할 수 있다 → 폴백으로 계속.
        print(f"[trash] OS 휴지통 실패, 로컬 보관으로 대체: {e}")

    return _move_to_local_trash(p)


def _move_to_local_trash(p: Path) -> tuple[bool, str]:
    """데이터 폴더 안 `.trash/<타임스탬프>_<이름>` 으로 옮긴다."""
    from datetime import datetime
    try:
        from meeting_minutes_app.common import app_paths
        base = app_paths.get_data_dir() / ".trash"
    except Exception:
        base = p.parent / ".trash"
    try:
        base.mkdir(parents=True, exist_ok=True)
        dst = base / f"{datetime.now():%Y%m%d_%H%M%S}_{p.name}"
        p.replace(dst)
        return True, f"결과 폴더를 {dst} 로 옮겼습니다."
    except OSError as e:
        return False, f"결과 폴더를 옮기지 못했습니다: {e}"
