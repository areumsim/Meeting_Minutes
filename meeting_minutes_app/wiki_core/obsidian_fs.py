"""
obsidian_fs.py — REST 플러그인 없이 vault 폴더(.md)에 직접 파일로 기록하는 클라이언트.

ObsidianClient 를 상속해 **저수준 I/O(put/get/exists/list/ping)만** 파일시스템으로
오버라이드한다. write_meeting_note / update_planned_note / create_reference_note /
find_planned_note / init_vault 등 상위 로직과 경로 계산(_meeting_folder 등)은
그대로 상속되므로 REST 모드와 동일한 노트가 폴더에 생성된다.

사용: obsidian.vault_path(또는 indexing.vault_path)만 설정되어 있으면 Local REST API
없이도 회의록이 볼트 폴더에 저장된다. publish.enrich_and_publish 가 REST 클라이언트를
얻지 못했을 때 이 클래스로 폴백한다.
"""

import os
from pathlib import Path
from typing import Dict, List, Optional

from meeting_minutes_app.wiki_core.obsidian import ObsidianClient
from meeting_minutes_app.common import config_loader as _cfg


class FilesystemObsidianClient(ObsidianClient):
    def __init__(
        self,
        vault_path: str,
        notes_subdir: str = "00_Meetings",
        meetings_path: str = "",
        transcripts_path: str = "",
        refs_subdir: str = "01_References",
        project: str = "",
        project_domains: Optional[Dict[str, str]] = None,
        ref_domains: Optional[Dict[str, str]] = None,
    ):
        # 부모 __init__(httpx 세션 생성)을 호출하지 않고 동일 속성만 세팅
        self.vault_root = Path(vault_path)
        self.api_url = ""
        self.api_key = ""
        self.notes_subdir = (notes_subdir or "00_Meetings").strip("/")
        self.meetings_path = (meetings_path or "").strip("/")
        self.transcripts_path = (transcripts_path or "").strip("/")
        self.refs_subdir = (refs_subdir or "01_References").strip("/")
        self.project = (project or "").strip()
        self.project_domains = project_domains or {}
        self.ref_domains = ref_domains or {}
        self._refs_dirs: Optional[List[str]] = None
        self._client = None

    @classmethod
    def from_config(cls, project_override: str = "") -> Optional["FilesystemObsidianClient"]:
        """obsidian.vault_path(없으면 indexing.vault_path)가 실제 폴더면 생성. 아니면 None.
        REST 토글(obsidian.enabled)과 무관 — 폴더만 있으면 폴더-only 기록을 허용한다."""
        vault = (_cfg.get("obsidian.vault_path", "") or _cfg.get("indexing.vault_path", "") or "").strip()
        if not vault or not Path(vault).is_dir():
            return None
        return cls(
            vault_path=vault,
            notes_subdir=_cfg.get("obsidian.notes_subdir", "00_Meetings"),
            meetings_path=_cfg.get("obsidian.meetings_path", ""),
            transcripts_path=_cfg.get("obsidian.transcripts_path", ""),
            refs_subdir=_cfg.get("obsidian.refs_subdir", "01_References"),
            project=project_override or _cfg.get("obsidian.project", ""),
            project_domains=_cfg.get("obsidian.project_domains", {}) or {},
            ref_domains=_cfg.get("obsidian.ref_domains", {}) or {},
        )

    def _abs(self, path: str) -> Path:
        return self.vault_root / path.strip("/")

    # ── 저수준 I/O 오버라이드 ─────────────────────────
    def ping(self) -> bool:
        return self.vault_root.is_dir()

    def ensure_running(self, *args, **kwargs) -> bool:
        return self.vault_root.is_dir()

    def close(self) -> None:
        pass

    def put_note(self, path: str, content: str) -> bool:
        try:
            fp = self._abs(path)
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content, encoding="utf-8")
            return True
        except Exception as e:
            print(f"[obsidian-fs] 기록 실패: {path} — {e}")
            return False

    def get_note(self, path: str) -> Optional[str]:
        try:
            fp = self._abs(path)
            return fp.read_text(encoding="utf-8") if fp.is_file() else None
        except Exception:
            return None

    def exists(self, path: str) -> bool:
        return self._abs(path).exists()

    def delete_note(self, path: str) -> bool:
        try:
            fp = self._abs(path)
            if fp.is_file():
                fp.unlink()
            return True
        except Exception:
            return False

    def _list_dirs(self, folder: str) -> List[str]:
        base = self._abs(folder)
        if not base.is_dir():
            return []
        try:
            return [p.name for p in base.iterdir() if p.is_dir()]
        except Exception:
            return []

    def _list_files(self, folder: str) -> List[str]:
        base = self._abs(folder)
        if not base.is_dir():
            return []
        try:
            return [p.name for p in base.iterdir() if p.is_file() and p.suffix == ".md"]
        except Exception:
            return []

    def search_simple(self, query: str, context_length: int = 100):
        # REST 전문검색 대체. folder-only 에서는 로컬 인덱스(vault_indexer)가 검색을 담당하므로
        # 여기서는 빈 결과를 반환한다(상위 로직은 결과 없으면 조용히 건너뜀).
        return []
