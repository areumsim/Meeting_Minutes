"""
app_paths.py — 애플리케이션 경로 단일 소스 (frozen/dev 공통)
==================================================================
쓰기 가능한 사용자 데이터와 읽기전용 번들 리소스를 명확히 분리한다.

- 개발(dev): 데이터 베이스 = 저장소 루트 (config.json·data/·output/ 이 기존 그대로).
  → 기존 CLI/기능 무손상.
- 배포(frozen, PyInstaller): 데이터 베이스 = exe 옆 MeetingMinutesData/.
  번들 리소스(_MEIPASS)는 읽기전용 임시폴더라 여기에 쓰지 않는다.

모든 쓰기 경로는 get_base_dir()에서 파생된다:
    MeetingMinutesData/
      ├─ config.json            (get_config_path)
      ├─ output/                (get_output_dir)
      ├─ data/                  (get_data_dir: wiki_graph.db, *_registry.json, vault_index.json, logs/)
      └─ web/                   (uploads/, meeting_assistant.db)

번들 리소스(읽기전용):
    _MEIPASS/config.example.json   (get_example_config_path)
    _MEIPASS/vendor/ffmpeg/        (get_ffmpeg_path / get_ffprobe_path)

주의: 순환 import 방지를 위해 이 모듈은 config_loader를 최상위에서 import하지 않는다
(get_output_dir 안에서만 지연 import). config_loader가 이 모듈을 import한다.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

_DATA_FOLDER_NAME = "MeetingMinutesData"


def is_frozen() -> bool:
    """PyInstaller 등으로 번들된 실행 파일인지."""
    return bool(getattr(sys, "frozen", False))


def _repo_root() -> Path:
    """dev 모드의 저장소 루트. app_paths.py = meeting_minutes_app/common/ 아래."""
    return Path(__file__).resolve().parent.parent.parent


def get_resource_dir() -> Path:
    """번들된 읽기전용 리소스 루트 (_MEIPASS 또는 저장소 루트).

    포터블(임베디드 파이썬 + bat) 배포에서는 MM_RESOURCE_DIR 환경변수로
    리소스 루트(config.example.json·prompts·vendor/ffmpeg·web/frontend/dist)를
    명시 지정할 수 있다. 미설정 시 frozen→_MEIPASS, 그 외→저장소 루트.
    """
    env = os.environ.get("MM_RESOURCE_DIR")
    if env:
        return Path(env).expanduser().resolve()
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS"))
    return _repo_root()


def get_base_dir() -> Path:
    """쓰기 가능한 데이터 베이스 디렉토리.

    - MM_DATA_DIR 환경변수가 있으면 최우선(포터블/임베디드 파이썬 배포에서
      설정·회의록·DB를 소스 폴더와 분리하기 위해 _start.bat 이 지정).
    - frozen → exe 옆 MeetingMinutesData/, dev → 저장소 루트.
    """
    env = os.environ.get("MM_DATA_DIR")
    if env:
        return Path(env).expanduser().resolve()
    if is_frozen():
        return Path(sys.executable).resolve().parent / _DATA_FOLDER_NAME
    return _repo_root()


def get_config_path() -> Path:
    return get_base_dir() / "config.json"


def get_example_config_path() -> Path:
    """번들/저장소의 config.example.json 경로 (읽기전용 시드 원본)."""
    return get_resource_dir() / "config.example.json"


def get_data_dir() -> Path:
    return get_base_dir() / "data"


def get_logs_dir() -> Path:
    return get_data_dir() / "logs"


def get_web_dir() -> Path:
    """웹 백엔드 쓰기 데이터(업로드, meeting_assistant.db) 베이스."""
    return get_base_dir() / "web"


def get_uploads_dir() -> Path:
    return get_web_dir() / "uploads"


def get_db_path() -> Path:
    return get_web_dir() / "meeting_assistant.db"


def get_output_dir() -> Path:
    """회의록 출력 폴더. config의 output_dir(기본 './output')을 베이스 기준으로 해석."""
    output_cfg = "output"
    try:
        # 지연 import: config_loader ↔ app_paths 순환 방지
        from meeting_minutes_app.common import config_loader as _cfg
        output_cfg = str(_cfg.get("output_dir", "output") or "output")
    except Exception:
        pass
    p = Path(output_cfg)
    if p.is_absolute():
        return p
    # './output' 같은 상대경로는 데이터 베이스 기준
    return (get_base_dir() / p).resolve()


# ── ffmpeg 번들 ────────────────────────────────────────────────
def _vendor_ffmpeg_dir() -> Path:
    return get_resource_dir() / "vendor" / "ffmpeg"


def get_ffmpeg_path() -> str:
    """번들 ffmpeg.exe 우선, 없으면 PATH의 'ffmpeg'."""
    exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    bundled = _vendor_ffmpeg_dir() / exe
    if bundled.exists():
        return str(bundled)
    return "ffmpeg"


def get_ffprobe_path() -> str:
    """번들 ffprobe.exe 우선, 없으면 PATH의 'ffprobe'."""
    exe = "ffprobe.exe" if os.name == "nt" else "ffprobe"
    bundled = _vendor_ffmpeg_dir() / exe
    if bundled.exists():
        return str(bundled)
    return "ffprobe"


def ffmpeg_available() -> bool:
    """ffmpeg 실행 가능 여부 (번들 존재 또는 PATH에서 발견)."""
    path = get_ffmpeg_path()
    if os.path.sep in path or (os.altsep and os.altsep in path):
        return Path(path).exists()
    return shutil.which(path) is not None


# ── 데이터 폴더 초기화/시드 ────────────────────────────────────
def ensure_base_dir() -> Path:
    """데이터 베이스 디렉토리와 하위 폴더를 만들고, config.json이 없으면
    config.example.json에서 시드한다. 앱/런처 시작 시 1회 호출.

    반환: 데이터 베이스 경로.
    """
    base = get_base_dir()
    try:
        base.mkdir(parents=True, exist_ok=True)
        get_data_dir().mkdir(parents=True, exist_ok=True)
        get_logs_dir().mkdir(parents=True, exist_ok=True)
        get_uploads_dir().mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"[paths] ⚠  데이터 폴더 생성 실패: {e}", file=sys.stderr)

    cfg = get_config_path()
    if not cfg.exists():
        example = get_example_config_path()
        if example.exists():
            try:
                shutil.copyfile(example, cfg)
                print(f"[paths] config.json 시드 생성: {cfg}")
            except Exception as e:
                print(f"[paths] ⚠  config.json 시드 실패: {e}", file=sys.stderr)
    return base
