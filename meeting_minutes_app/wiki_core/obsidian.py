"""
obsidian.py — Obsidian Local REST API 연동
===============================================
Obsidian "Local REST API (with MCP)" 플러그인을 통해 회의록을 사용자의
실제 볼트에 기록하고, 기존 노트를 검색해 참고자료로 링크합니다.

엔드포인트(플러그인 기본):
  GET    /               상태 확인
  GET    /vault/{path}   노트 읽기
  PUT    /vault/{path}   노트 생성/덮어쓰기
  POST   /vault/{path}   노트 이어쓰기(append)
  POST   /search/simple/ 단순 텍스트 검색 (?query=...&contextLength=...)

설정(config.json):
  "obsidian": {
    "enabled": true,
    "api_url": "https://127.0.0.1:27124",
    "api_key": "<plugin bearer token>",
    "notes_subdir": "00_Meetings",
    "refs_subdir":  "01_References",
    "project":      "",          ← 프로젝트명. 회의록·용어가 같은 도메인 폴더로 묶임. 비우면 기타/·공통/
    "project_domains": {         ← (선택) 여러 프로젝트를 한 도메인으로 묶는 매핑. 없으면 프로젝트명이 곧 폴더명
        "백서온톨로지": "GraphDB-온톨로지"
    },
    "verify_ssl": false
  }

폴더 규칙:
  - 회의록: obsidian.meetings_path 가 있으면 그 경로에 바로 저장
            (예: 도메인_아카이브/01_회의_세미나/회의별/260627 제목.md)
            없으면 00_Meetings/<도메인>/260627 제목.md
            (도메인 = project_domains 매핑 or project명, 없으면 기타)
  - 인물:   01_References/People/<이름>.md
  - 기업:   01_References/Companies/<이름>.md
  - 용어:   01_References/<도메인>/<용어>.md        (없으면 공통)

사용 예:
    from meeting_minutes_app.wiki_core.obsidian import ObsidianClient
    obs = ObsidianClient.from_config()
    if obs and obs.ping():
        obs.write_meeting_note(title="2025 양자 세미나", body_md=minutes, ...)

CLI:
    python run_meeting.py obsidian --ping
    python run_meeting.py obsidian --search "양자"
    python run_meeting.py obsidian --test-note
"""

from __future__ import annotations

import os
import re
import sys
import json
from datetime import datetime
from typing import Optional, List, Dict, Any
from urllib.parse import quote

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

try:
    from meeting_minutes_app.common import config_loader as _cfg
    _cfg_ok = True
except ImportError:
    _cfg = None  # type: ignore
    _cfg_ok = False


def _c(key: str, default: Any = None) -> Any:
    return _cfg.get(key, default) if _cfg_ok else default


def _sm_save(content: str, *, metadata: dict, container_tag: str) -> None:
    """회의록을 Supermemory에 저장 (config.supermemory.enabled=true 시). 실패 무시."""
    try:
        from meeting_minutes_app.wiki_core.supermemory_client import get_client as _sm_get  # type: ignore
        _sm_get().save(content, metadata=metadata, container_tag=container_tag)
    except ImportError:
        pass


# ── Windows Obsidian 자동 감지 ─────────────────────────────────
def _detect_obsidian_config(api_key_hint: str = "") -> dict:
    """%APPDATA%\\obsidian\\obsidian.json에서 Local REST API 플러그인이 활성화된 볼트 정보 탐지.
    반환: {"vault_path": str, "api_key": str, "port": int} 또는 {}
    """
    import platform
    if platform.system() != "Windows":
        return {}
    registry = os.path.expandvars(r"%APPDATA%\obsidian\obsidian.json")
    if not os.path.exists(registry):
        return {}
    try:
        data = json.load(open(registry, encoding="utf-8"))
    except Exception:
        return {}
    candidates = []
    for vault_info in data.get("vaults", {}).values():
        vpath = vault_info.get("path", "")
        if not os.path.isdir(vpath):
            continue
        plugins_json = os.path.join(vpath, ".obsidian", "community-plugins.json")
        if not os.path.exists(plugins_json):
            continue
        try:
            enabled = json.load(open(plugins_json, encoding="utf-8"))
        except Exception:
            continue
        if "obsidian-local-rest-api" not in enabled:
            continue
        data_json = os.path.join(vpath, ".obsidian", "plugins",
                                 "obsidian-local-rest-api", "data.json")
        port, detected_api_key = 27124, ""
        if os.path.exists(data_json):
            try:
                pd = json.load(open(data_json, encoding="utf-8"))
                port = int(pd.get("port", 27124))
                detected_api_key = pd.get("apiKey", "")
            except Exception:
                pass
        candidates.append({
            "vault_path": vpath,
            "api_key": detected_api_key,
            "port": port,
            "open": bool(vault_info.get("open")),
        })
    if not candidates:
        return {}
    wanted_key = str(api_key_hint or "").strip()
    if wanted_key:
        for c in candidates:
            if c.get("api_key") == wanted_key:
                return c
    for c in candidates:
        if c.get("open"):
            return c
    return candidates[0]


# ── 노트 포맷팅 유틸 (마크다운/frontmatter 조립은 note_builder.py로 분리) ──
from meeting_minutes_app.wiki_core.note_builder import (  # noqa: E402
    build_frontmatter, safe_filename, wikilink, build_references,
    render_transcript_note, build_meeting_note_content, build_recording_note_content,
    build_planned_note_merge_content, build_recording_into_plan_content,
)


# ── 프론트매터 파싱 / 매칭 유틸 ────────────────────────────────
def _unquote(v: str) -> str:
    """YAML 스칼라 따옴표 제거 + 이스케이프 복원."""
    v = (v or "").strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        v = v[1:-1]
    return v.replace('\\"', '"').strip()


def parse_frontmatter(content: str):
    """노트 문자열 → (meta: dict, body: str).
    build_frontmatter()가 쓰는 블록 리스트(`- item`)와 인라인 리스트(`[a, b]`) 둘 다 지원.
    프론트매터가 없으면 ({}, content) 반환."""
    if not content:
        return {}, ""
    if not content.lstrip().startswith("---"):
        return {}, content
    lines = content.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.strip() == "---"), None)
    if start is None:
        return {}, content
    end = next((i for i in range(start + 1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return {}, content
    meta: Dict[str, Any] = {}
    cur_key: Optional[str] = None
    for ln in lines[start + 1:end]:
        if re.match(r"^\s*-\s+", ln) and cur_key is not None:
            if not isinstance(meta.get(cur_key), list):
                meta[cur_key] = []
            meta[cur_key].append(_unquote(re.sub(r"^\s*-\s+", "", ln)))
            continue
        m = re.match(r"^([A-Za-z0-9_\-]+):\s*(.*)$", ln)
        if not m:
            continue
        k, v = m.group(1), m.group(2).strip()
        if v == "":
            meta[k] = ""          # 뒤따르는 `- ` 항목이 있으면 리스트로 승격
            cur_key = k
        elif v.startswith("[") and v.endswith("]"):
            inner = v[1:-1].strip()
            meta[k] = [_unquote(x) for x in inner.split(",") if x.strip()] if inner else []
            cur_key = None
        else:
            meta[k] = _unquote(v)
            cur_key = None
    body = "\n".join(lines[end + 1:])
    return meta, body


def date_key(s: str) -> str:
    """다양한 날짜 표기 → 'YYYY-MM-DD'. 추출 실패 시 ''.
    예: '2026년 06월 27일 09:00', '2026-06-27', '2026/6/27' → '2026-06-27'."""
    s = str(s or "")
    m = re.search(r"(\d{4})\s*[-/.년]\s*(\d{1,2})\s*[-/.월]\s*(\d{1,2})", s)
    if not m:
        m = re.search(r"(\d{4})(\d{2})(\d{2})", s)
    if m:
        try:
            return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        except ValueError:
            return ""
    return ""


def yymmdd_key(s: str) -> str:
    d = date_key(s)
    return d[2:4] + d[5:7] + d[8:10] if d else ""


def _norm_title(s: str) -> str:
    """제목 매칭용 정규화: 소문자 + 공백/구분기호 제거."""
    return re.sub(r"[\s_\-·:/().,]", "", (s or "").lower())


def title_match_score(a: str, b: str) -> int:
    """정규화 제목 두 개의 일치도. 3=완전일치, 2=포함관계, 1=토큰 겹침, 0=불일치."""
    na, nb = _norm_title(a), _norm_title(b)
    if not na or not nb:
        return 0
    if na == nb:
        return 3
    if na in nb or nb in na:
        return 2
    ta = set(re.split(r"[\s_\-·:/().,]+", (a or "").lower())) - {""}
    tb = set(re.split(r"[\s_\-·:/().,]+", (b or "").lower())) - {""}
    return 1 if (ta & tb) else 0


def _as_str_list(v) -> List[str]:
    """값을 문자열 리스트로 정규화(스칼라/None/리스트 모두 허용)."""
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    if v in (None, "", []):
        return []
    return [str(v).strip()]


def _year_from_date(date_str: str = "") -> str:
    """YYYY-MM-DD 또는 YYMMDD 형태에서 4자리 연도 추출."""
    text = str(date_str or "").strip()
    m = re.search(r"(20\d{2}|19\d{2})", text)
    if m:
        return m.group(1)
    m = re.match(r"(\d{2})(\d{2})(\d{2})", text)
    if m:
        yy = int(m.group(1))
        return str(2000 + yy if yy < 70 else 1900 + yy)
    return datetime.now().strftime("%Y")


def _expand_path_template(path: str, date_str: str = "") -> str:
    """볼트 상대경로 템플릿의 날짜 토큰을 치환."""
    if not path:
        return ""
    year = _year_from_date(date_str)
    short_year = year[-2:]
    month = "01"
    m = re.search(r"(?:19|20)\d{2}[-._/]?(\d{2})", str(date_str or ""))
    if m:
        month = m.group(1)
    return (
        str(path)
        .replace("{year}", year)
        .replace("{yyyy}", year)
        .replace("{yy}", short_year)
        .replace("{month}", month)
        .strip("/")
    )


def time_minutes(s) -> Optional[int]:
    """'16:30'/'9시 00분'/'1630' → 자정 기준 분. 없으면 None."""
    m = re.search(r"(\d{1,2})\s*[:시]\s*(\d{2})", str(s or ""))
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if 0 <= h < 24 and 0 <= mi < 60:
            return h * 60 + mi
    return None


# ── REST 클라이언트 ───────────────────────────────────────────
class ObsidianClient:
    """Obsidian Local REST API 래퍼."""

    def _project_domain(self) -> str:
        """현 project를 도메인 폴더명으로 해석 (회의록·용어 폴더에 공통 사용).
        config의 obsidian.project_domains 매핑 우선 → 없으면 project명 자체 → project 없으면 ''.
        여러 프로젝트를 한 도메인으로 묶고 싶을 때만 매핑을 쓰고, 아니면 프로젝트명이 곧 폴더명."""
        p = (self.project or "").strip()
        if not p:
            return ""
        pk = re.sub(r"[\s_\-]", "", p.lower())
        for key, domain in (self.project_domains or {}).items():
            kk = re.sub(r"[\s_\-]", "", str(key).lower())
            if kk and (kk in pk or pk in kk):
                return str(domain)
        return safe_filename(p)  # 매핑 없으면 프로젝트명 자체를 도메인으로

    def _refs_subfolder(self, category: str) -> str:
        """category → References 서브폴더.
        인물→People, 기업→Companies, 용어·기술→도메인 폴더(project 없으면 공통)."""
        c = category or ""
        if "인물" in c:
            return "People"
        if "기업" in c or "회사" in c or "기관" in c:
            return "Companies"
        return self._project_domain() or "공통"

    def __init__(
        self,
        api_url: str,
        api_key: str,
        notes_subdir: str = "00_Meetings",
        meetings_path: str = "",
        transcripts_path: str = "",
        refs_subdir: str = "01_References",
        project: str = "",
        project_domains: Optional[Dict[str, str]] = None,
        verify_ssl: bool = False,
        timeout: float = 15.0,
    ):
        if not HAS_HTTPX:
            raise ImportError("httpx 미설치 → pip install httpx")
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.notes_subdir = notes_subdir.strip("/")
        self.meetings_path = (meetings_path or "").strip("/")
        self.transcripts_path = (transcripts_path or "").strip("/")
        self.refs_subdir = refs_subdir.strip("/")
        self.project = (project or "").strip()
        self.project_domains = project_domains or {}
        self._refs_dirs: Optional[List[str]] = None   # 참고 서브폴더 목록 캐시(중복검사용)
        self._client = httpx.Client(
            base_url=self.api_url,
            verify=verify_ssl,
            timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    @classmethod
    def from_config(cls) -> Optional["ObsidianClient"]:
        """config.json 의 obsidian 섹션에서 생성. 비활성/미설정 시 None.
        api_key 가 없으면 Windows에서 Obsidian 설치 경로를 자동 탐지한다."""
        if not _c("obsidian.enabled", False):
            return None
        api_url = _c("obsidian.api_url", "")
        api_key = _c("obsidian.api_key", "")

        # api_key 미설정 시 Windows 자동 감지
        if not api_key:
            detected = _detect_obsidian_config()
            if detected:
                api_key = detected.get("api_key", "")
                if not api_url:
                    api_url = f"https://127.0.0.1:{detected.get('port', 27124)}"
                if api_key:
                    print(f"[obsidian] 자동 감지: {detected.get('vault_path')} (port {detected.get('port')})")

        if not api_url:
            api_url = "https://127.0.0.1:27124"
        if not api_key:
            return None
        if not HAS_HTTPX:
            return None
        return cls(
            api_url=api_url,
            api_key=api_key,
            notes_subdir=_c("obsidian.notes_subdir", "00_Meetings"),
            meetings_path=_c("obsidian.meetings_path", ""),
            transcripts_path=_c("obsidian.transcripts_path", ""),
            refs_subdir=_c("obsidian.refs_subdir", "01_References"),
            project=_c("obsidian.project", ""),
            project_domains=_c("obsidian.project_domains", {}) or {},
            verify_ssl=bool(_c("obsidian.verify_ssl", False)),
        )

    # ── 기본 동작 ─────────────────────────────────────────
    def ping(self) -> bool:
        """플러그인 연결 + 인증 확인."""
        try:
            r = self._client.get("/")
            if r.status_code == 200:
                data = r.json()
                return bool(data.get("authenticated", True))
            return False
        except Exception as e:
            print(f"[obsidian] ping 실패: {e}")
            return False

    def ensure_running(self, exe_path: str = "", timeout: int = 40) -> bool:
        """Obsidian이 꺼져 있으면 자동 실행하고 REST API가 준비될 때까지 대기.

        exe_path 미설정 시 config.obsidian.exe_path 를 사용한다.
        """
        import time
        import subprocess

        if self.ping():
            return True

        path = exe_path or _c("obsidian.exe_path", "")
        if not path:
            return False

        import os
        if not os.path.isfile(path):
            print(f"[obsidian] 실행 파일 없음: {path}")
            return False

        try:
            subprocess.Popen([path], creationflags=subprocess.DETACHED_PROCESS
                             if hasattr(subprocess, "DETACHED_PROCESS") else 0)
            print(f"[obsidian] Obsidian 자동 실행 중... (최대 {timeout}초 대기)")
        except Exception as e:
            print(f"[obsidian] 자동 실행 실패: {e}")
            return False

        for i in range(timeout // 2):
            time.sleep(2)
            try:
                if self.ping():
                    print(f"[obsidian] ✅ REST API 준비 완료 ({(i + 1) * 2}초)")
                    return True
            except Exception:
                pass

        print(f"[obsidian] ⚠️  {timeout}초 내 응답 없음 — TF-IDF 모드로 진행합니다")
        return False

    def _vault_path(self, path: str) -> str:
        # 경로 구분자는 유지하고 각 세그먼트만 인코딩
        return "/vault/" + quote(path.strip("/"), safe="/")

    def _meeting_folder(
        self,
        output_folder: str = "",
        *,
        use_watcher_fallback: bool = False,
        date_str: str = "",
    ) -> str:
        """회의록을 쓸 볼트 상대 폴더.

        우선순위:
        1. 호출자가 넘긴 output_folder
        2. obsidian.meetings_path
        3. 자동 녹음 처리에서만 vault_watcher.output_folder
        4. notes_subdir/<project-domain|기타>
        """
        if output_folder:
            return _expand_path_template(output_folder, date_str)
        if self.meetings_path:
            return _expand_path_template(self.meetings_path, date_str)
        try:
            cfg_meetings = str(_c("obsidian.meetings_path", "") or "").strip("/")
            if cfg_meetings:
                return _expand_path_template(cfg_meetings, date_str)
            watcher_folder = str(_c("vault_watcher.output_folder", "") or "").strip("/")
            if use_watcher_fallback and watcher_folder:
                return _expand_path_template(watcher_folder, date_str)
        except Exception:
            pass
        project_dir = self._project_domain() or "기타"
        return f"{self.notes_subdir}/{project_dir}".strip("/")

    def _transcript_folder(self, meeting_folder: str, date_str: str = "") -> str:
        """전체 STT 전사를 쓸 볼트 상대 폴더."""
        if self.transcripts_path:
            return _expand_path_template(self.transcripts_path, date_str)
        try:
            cfg_transcripts = str(_c("obsidian.transcripts_path", "") or "").strip("/")
            if cfg_transcripts:
                return _expand_path_template(cfg_transcripts, date_str)
        except Exception:
            pass
        return _expand_path_template(meeting_folder, date_str)

    def put_note(self, path: str, content: str) -> bool:
        """노트 생성/덮어쓰기. path 는 볼트 상대경로(.md 포함)."""
        try:
            r = self._client.put(
                self._vault_path(path),
                content=content.encode("utf-8"),
                headers={"Content-Type": "text/markdown; charset=utf-8"},
            )
            if r.status_code in (200, 201, 204):
                return True
            print(f"[obsidian] put 실패 ({r.status_code}): {path} — {r.text[:200]}")
            return False
        except Exception as e:
            print(f"[obsidian] put 예외: {path} — {e}")
            return False

    def get_note(self, path: str) -> Optional[str]:
        try:
            r = self._client.get(self._vault_path(path))
            return r.text if r.status_code == 200 else None
        except Exception:
            return None

    def exists(self, path: str) -> bool:
        try:
            r = self._client.get(self._vault_path(path))
            return r.status_code == 200
        except Exception:
            return False

    def delete_note(self, path: str) -> bool:
        try:
            r = self._client.delete(self._vault_path(path))
            return r.status_code in (200, 204, 404)
        except Exception:
            return False

    def _list_dirs(self, folder: str) -> List[str]:
        """folder 바로 아래 하위 폴더명 목록(1단계). 실패 시 []."""
        try:
            r = self._client.get(self._vault_path(folder) + "/")
            if r.status_code != 200:
                return []
            files = r.json().get("files", [])
            return [f.rstrip("/") for f in files if isinstance(f, str) and f.endswith("/")]
        except Exception:
            return []

    def _list_files(self, folder: str) -> List[str]:
        """folder 바로 아래 .md 파일명 목록(1단계). 실패 시 []."""
        try:
            r = self._client.get(self._vault_path(folder) + "/")
            if r.status_code != 200:
                return []
            files = r.json().get("files", [])
            return [f for f in files
                    if isinstance(f, str) and f.endswith(".md") and not f.endswith("/")]
        except Exception:
            return []

    def _ref_note_exists(self, base: str) -> bool:
        """References 하위(루트 + 모든 도메인/타입 서브폴더)에 같은 이름 노트가 있는지.
        서브폴더 목록은 최초 1회 캐시(런 중 동일 도메인 생성분은 직접 경로로 잡힘)."""
        if self.exists(f"{self.refs_subdir}/{base}.md"):
            return True
        if self._refs_dirs is None:
            self._refs_dirs = self._list_dirs(self.refs_subdir)
        # 캐시에 없을 수 있는 '현재 런에서 새로 만든 도메인'도 포함해 검사
        cur = self._refs_subfolder("용어·기술")
        subdirs = set(self._refs_dirs) | {cur, "People", "Companies"}
        for sub in subdirs:
            if self.exists(f"{self.refs_subdir}/{sub}/{base}.md"):
                return True
        return False

    def search_simple(self, query: str, context_length: int = 100,
                      limit: int = 10) -> List[Dict[str, Any]]:
        """단순 텍스트 검색. [{filename, score, matches}] 반환."""
        if not query or not query.strip():
            return []
        try:
            r = self._client.post(
                "/search/simple/",
                params={"query": query, "contextLength": context_length},
            )
            if r.status_code != 200:
                return []
            results = r.json()
            return results[:limit] if isinstance(results, list) else []
        except Exception as e:
            print(f"[obsidian] search 실패: {e}")
            return []

    # ── 고수준: 노트 작성 ─────────────────────────────────
    def write_meeting_note(
        self,
        title: str,
        body_md: str,
        doc_type: str = "meeting",
        topic: str = "",
        attendees: Optional[List[str]] = None,
        session_dt: str = "",
        tags: Optional[List[str]] = None,
        glossary_md: str = "",
        related_notes: Optional[List[str]] = None,
        external_refs: Optional[List[Dict[str, str]]] = None,
        summary_md: str = "",
        actions_md: str = "",
        extra_meta: Optional[Dict[str, Any]] = None,
        meeting_scope: str = "",
        web_sources_md: str = "",
        source_audio: str = "",
        processed_at: str = "",
        source_file_date: str = "",
        stt_meta: Optional[Dict[str, Any]] = None,
        transcript_md: str = "",
        review_status: str = "pending",
        confidence: str = "medium",
        source_type: str = "generated",
        evidence: Optional[List[str]] = None,
    ) -> Optional[str]:
        """
        회의록 노트를 볼트 상대 폴더에 작성. obsidian.meetings_path 가 있으면 그 경로를 우선한다.
        작성된 볼트 상대경로 반환(실패 시 None).
        extra_meta: 프론트매터에 추가할 키(예: 계획 매칭 보류 시 matched_plan).
        review_status/confidence/source_type/evidence: Personal Wiki Schema — 사람이 검토 후
        review_status를 "reviewed"로 직접 변경. evidence는 회의록 생성에 실제 주입된 근거
        ("[[노트#헤딩]]" 또는 "[[노트]]") 목록.
        """
        processed_iso = processed_at or datetime.now().isoformat(timespec="seconds")
        date_str = date_key(session_dt) or date_key(source_file_date) or datetime.now().strftime("%Y-%m-%d")
        file_date = yymmdd_key(date_str) or datetime.now().strftime("%y%m%d")
        base = safe_filename(f"{file_date} {title}" if title else f"{file_date} 회의록")
        path = f"{self._meeting_folder(date_str=date_str)}/{base}.md"

        meta = {
            "title": title or base,
            "date": date_str,
            "session_date": date_str,
            "session_dt": session_dt or "",
            "source_file_date": source_file_date or "",
            "type": doc_type,
            "project": self.project or "",
            "topic": topic,
            "attendees": attendees or [],
            "meeting_scope": meeting_scope or "",
            "source_audio": os.path.basename(source_audio) if source_audio else "",
            "tags": (tags or []) + ["회의록", doc_type],
            "created": processed_iso,
            "processed_at": processed_iso,
            "review_status": review_status,
            "confidence": confidence,
            "source_type": source_type,
            "evidence": evidence or [],
        }
        if stt_meta:
            meta.update({k: v for k, v in stt_meta.items() if v not in ("", None, [])})
        if extra_meta:
            meta.update(extra_meta)

        transcript_mode = str(_c("obsidian.transcript_mode", "separate") or "separate").lower()
        transcript_note_path = ""
        if transcript_md.strip() and transcript_mode in ("separate", "note", "file"):
            transcript_folder = self._transcript_folder(self._meeting_folder(date_str=date_str), date_str)
            transcript_note_path = f"{transcript_folder}/{base} - 전사.md"
            transcript_meta = {
                "title": f"{title or base} - 전사",
                "date": date_str,
                "session_date": date_str,
                "type": "transcript",
                "parent_note": path,
                "source_audio": os.path.basename(source_audio) if source_audio else "",
                "created": processed_iso,
                "processed_at": processed_iso,
                "tags": ["전사", doc_type],
            }
            transcript_content = render_transcript_note(
                transcript_meta, f"{title or base} - 전사", transcript_md,
            )
            if not self.put_note(transcript_note_path, transcript_content):
                transcript_note_path = ""
        if transcript_note_path:
            meta["transcript_note"] = transcript_note_path

        content = build_meeting_note_content(
            meta, title or base, body_md, summary_md, glossary_md, actions_md,
            related_notes, external_refs, web_sources_md, transcript_md, transcript_mode,
        )
        if not self.put_note(path, content):
            return None
        _sm_save(content, metadata={"date": date_str, "type": doc_type, "topic": topic}, container_tag=topic or title or "")
        return path

    def write_recording_note(
        self,
        title: str,
        body_md: str,
        doc_type: str = "meeting",
        topic: str = "",
        attendees: Optional[List[str]] = None,
        session_dt: str = "",
        tags: Optional[List[str]] = None,
        summary_md: str = "",
        actions_md: str = "",
        glossary_md: str = "",
        related_notes: Optional[List[str]] = None,
        external_refs: Optional[List[Dict[str, str]]] = None,
        # 녹음 자동 처리 전용 필드
        source_audio: str = "",
        processed_at: str = "",
        source_file_date: str = "",
        stt_meta: Optional[Dict[str, Any]] = None,
        duration: float = 0.0,
        status: str = "processed",
        key_points: Optional[List[str]] = None,
        decisions: Optional[List[str]] = None,
        open_questions: Optional[List[str]] = None,
        important_claims: Optional[List[str]] = None,
        transcript_md: str = "",
        output_folder: str = "",
        meeting_scope: str = "",
        web_sources_md: str = "",
        review_status: str = "pending",
        confidence: str = "medium",
        source_type: str = "generated",
        evidence: Optional[List[str]] = None,
    ) -> Optional[str]:
        """자동 처리된 녹음 파일을 Obsidian 노트로 저장.
        source_audio, processed_at, duration 등 확장 frontmatter 포함.
        body_md 대신 섹션별(key_points, decisions 등) 구조화된 출력도 지원.
        output_folder: vault 상대경로. 비우면 obsidian.meetings_path → vault_watcher.output_folder 순으로 사용.
        review_status/confidence/source_type/evidence: Personal Wiki Schema (write_meeting_note 참고)."""
        date_str = date_key(session_dt) or date_key(source_file_date) or datetime.now().strftime("%Y-%m-%d")
        out_folder = self._meeting_folder(output_folder, use_watcher_fallback=True, date_str=date_str)
        file_date = yymmdd_key(date_str) or datetime.now().strftime("%y%m%d")
        base = safe_filename(f"{file_date} {title}" if title else f"{file_date} 녹음 기록")
        path = f"{out_folder}/{base}.md"

        now_iso = processed_at or datetime.now().isoformat(timespec="seconds")
        meta: Dict[str, Any] = {
            "title": title or base,
            "date": date_str,
            "session_date": date_str,
            "session_dt": session_dt or "",
            "source_file_date": source_file_date or "",
            "type": doc_type,
            "project": self.project or "",
            "topic": topic,
            "attendees": attendees or [],
            "tags": list(dict.fromkeys((tags or []) + ["녹음처리", doc_type])),
            "status": status,
            "meeting_scope": meeting_scope or "",
            "source_audio": os.path.basename(source_audio) if source_audio else "",
            "created": session_dt or date_str,
            "processed_at": now_iso,
            "review_status": review_status,
            "confidence": confidence,
            "source_type": source_type,
            "evidence": evidence or [],
        }
        if stt_meta:
            meta.update({k: v for k, v in stt_meta.items() if v not in ("", None, [])})
        if duration > 0:
            mins, secs = divmod(int(duration), 60)
            meta["duration"] = f"{mins}분 {secs}초" if mins else f"{secs}초"

        transcript_mode = str(_c("obsidian.transcript_mode", "separate") or "separate").lower()
        transcript_note_path = ""
        if transcript_md.strip() and transcript_mode in ("separate", "note", "file"):
            transcript_folder = self._transcript_folder(out_folder, date_str)
            transcript_note_path = f"{transcript_folder}/{base} - 전사.md"
            transcript_meta = {
                "title": f"{title or base} - 전사",
                "date": date_str,
                "type": "transcript",
                "parent_note": path,
                "source_audio": os.path.basename(source_audio) if source_audio else "",
                "created": now_iso,
                "tags": ["전사", doc_type],
            }
            transcript_content = render_transcript_note(
                transcript_meta, f"{title or base} - 전사", transcript_md,
            )
            if not self.put_note(transcript_note_path, transcript_content):
                transcript_note_path = ""

        content = build_recording_note_content(
            meta, body_md, summary_md, key_points, decisions, actions_md,
            open_questions, important_claims, glossary_md, related_notes, external_refs,
            web_sources_md, transcript_md, transcript_mode, transcript_note_path,
        )
        if not self.put_note(path, content):
            return None
        _sm_save(content, metadata={"date": date_str, "type": doc_type, "topic": topic}, container_tag=topic or title or "")
        return path

    # ── 계획 회의 매칭 / 병합 ─────────────────────────────
    def find_planned_note(self, title: str, session_dt: str = "") -> Optional[Dict[str, Any]]:
        """notes_subdir(및 1단계 하위 폴더)에서 status: planned 노트 중
        날짜(date_key 동일) + 제목 유사도가 맞는 '예정 회의' 노트를 찾는다.
        반환: {"path","meta","body","score","reason"} (없으면 None).
        매칭 기준: 같은 날짜 + (제목 유사 OR 비슷한 시간대 ±window분). 최종 병합은 사용자 확인 필요."""
        target_date = date_key(session_dt) or datetime.now().strftime("%Y-%m-%d")
        rec_min = time_minutes(session_dt)
        window = int(_c("obsidian.match_time_window_min", 120) or 120)  # 시간 근접 허용(분)
        folders = [self.notes_subdir] + [
            f"{self.notes_subdir}/{d}" for d in self._list_dirs(self.notes_subdir)
        ]
        meeting_folder = self._meeting_folder()
        if meeting_folder and meeting_folder not in folders:
            folders.insert(0, meeting_folder)
        best: Optional[Dict[str, Any]] = None
        for folder in folders:
            for fname in self._list_files(folder):
                path = f"{folder}/{fname}"
                content = self.get_note(path)
                if not content:
                    continue
                meta, body = parse_frontmatter(content)
                if str(meta.get("status", "")).strip().lower() != "planned":
                    continue
                d = date_key(meta.get("date", ""))
                # 양쪽 날짜가 다 있는데 다르면 제외(다른 날 회의 오매칭 방지)
                if target_date and d and d != target_date:
                    continue
                note_title = meta.get("title", "") or fname[:-3]
                score = title_match_score(title, note_title)
                # 시간 근접: 제목이 안 맞아도 같은 날 비슷한 시간이면 후보(사용자 확인용)
                plan_min = time_minutes(meta.get("time", ""))
                tdiff = (abs(rec_min - plan_min)
                         if (rec_min is not None and plan_min is not None) else None)
                time_close = (tdiff is not None and tdiff <= window)
                if score <= 0 and not time_close:
                    continue
                if score >= 2:
                    reason = "제목 일치"
                elif score == 1 and time_close:
                    reason = "제목 일부 + 비슷한 시간"
                elif score == 1:
                    reason = "제목 일부 일치"
                else:
                    reason = "비슷한 시간대"
                # 우선순위: 제목점수 > 시간근접 > 시간차 작은 순
                rank = (score, 1 if time_close else 0,
                        -(tdiff if tdiff is not None else 99999))
                if best is None or rank > best["_rank"]:
                    best = {"path": path, "meta": meta, "body": body,
                            "score": score, "reason": reason, "_rank": rank}
        if best:
            best.pop("_rank", None)
        return best

    def update_planned_note(
        self,
        match: Dict[str, Any],
        *,
        title: str,
        body_md: str,
        doc_type: str = "meeting",
        topic: str = "",
        attendees: Optional[List[str]] = None,
        session_dt: str = "",
        glossary_md: str = "",
        related_notes: Optional[List[str]] = None,
        external_refs: Optional[List[Dict[str, str]]] = None,
        summary_md: str = "",
        actions_md: str = "",
        meeting_scope: str = "",
        web_sources_md: str = "",
        source_audio: str = "",
        processed_at: str = "",
        source_file_date: str = "",
        stt_meta: Optional[Dict[str, Any]] = None,
        transcript_md: str = "",
    ) -> Optional[str]:
        """매칭된 계획 노트(match)에 회의록을 '병합'한다.
        - 사용자가 미리 적어둔 본문(사전 조사/안건/메모 등)은 그대로 보존
        - 프론트매터의 계획 필드(date/time/topic/project/company)는 유지, status → done
        - 참석자는 계획값 ∪ 감지값으로 합집합
        - 본문 아래에 '## 회의 기록' 구분선과 요약/본문/용어·배경/액션/참고를 덧붙임
        성공 시 노트 경로 반환, 실패 시 None."""
        path = match["path"]
        pmeta: Dict[str, Any] = dict(match.get("meta") or {})
        pbody = (match.get("body") or "").strip()

        merged_att: List[str] = []
        for x in _as_str_list(pmeta.get("attendees")) + list(attendees or []):
            x = str(x).strip()
            if x and x not in merged_att:
                merged_att.append(x)
        merged_tags: List[str] = []
        for x in _as_str_list(pmeta.get("tags")) + ["회의록", doc_type]:
            if x and x not in merged_tags:
                merged_tags.append(x)

        now_iso = processed_at or datetime.now().isoformat(timespec="seconds")
        date_str = date_key(pmeta.get("date")) or date_key(session_dt) or date_key(source_file_date) or datetime.now().strftime("%Y-%m-%d")
        meta = {
            "title": pmeta.get("title") or title,
            "date": date_str,
            "session_date": date_str,
            "session_dt": session_dt or "",
            "source_file_date": source_file_date or "",
            "time": pmeta.get("time", ""),
            "type": pmeta.get("type") or doc_type,
            "project": pmeta.get("project") or (self.project or ""),
            "topic": pmeta.get("topic") or topic,
            "attendees": merged_att,
            "company": pmeta.get("company", ""),
            "meeting_scope": pmeta.get("meeting_scope", "") or meeting_scope or "",
            "status": "done",
            "source_audio": os.path.basename(source_audio) if source_audio else "",
            "tags": merged_tags,
            "created": pmeta.get("created", "") or now_iso,
            "recorded": now_iso,
            "processed_at": now_iso,
        }
        if stt_meta:
            meta.update({k: v for k, v in stt_meta.items() if v not in ("", None, [])})

        transcript_mode = str(_c("obsidian.transcript_mode", "separate") or "separate").lower()
        if transcript_md.strip() and transcript_mode in ("separate", "note", "file"):
            file_date = yymmdd_key(date_str) or datetime.now().strftime("%y%m%d")
            base = safe_filename(f"{file_date} {title}" if title else f"{file_date} 회의록")
            transcript_folder = self._transcript_folder(self._meeting_folder(date_str=date_str), date_str)
            transcript_note_path = f"{transcript_folder}/{base} - 전사.md"
            transcript_meta = {
                "title": f"{title or base} - 전사",
                "date": date_str,
                "session_date": date_str,
                "type": "transcript",
                "parent_note": path,
                "source_audio": os.path.basename(source_audio) if source_audio else "",
                "created": now_iso,
                "processed_at": now_iso,
                "tags": ["전사", doc_type],
            }
            transcript_content = render_transcript_note(
                transcript_meta, f"{title or base} - 전사", transcript_md,
            )
            if self.put_note(transcript_note_path, transcript_content):
                meta["transcript_note"] = transcript_note_path

        now_header = datetime.now().strftime('%Y-%m-%d %H:%M')
        content = build_planned_note_merge_content(
            meta, pbody, summary_md, body_md, glossary_md, actions_md,
            related_notes, external_refs, web_sources_md, transcript_md, transcript_mode, now_header,
        )
        if not self.put_note(path, content):
            return None
        _sm_save(content, metadata={"date": date_str, "type": meta.get("type") or doc_type, "topic": meta.get("topic") or topic}, container_tag=meta.get("topic") or topic or title or "")
        return path

    def merge_recording_into_plan(self, recording_path: str,
                                  plan_path: str = "", delete_recording: bool = False
                                  ) -> Optional[str]:
        """이미 생성된 '녹음 노트'를 계획 노트에 병합(Cowork 확인 후 호출).
        plan_path 미지정 시 녹음 노트 프론트매터의 matched_plan 을 사용.
        성공 시 계획 노트 경로 반환."""
        rec = self.get_note(recording_path)
        if not rec:
            return None
        rmeta, rbody = parse_frontmatter(rec)
        plan_path = plan_path or rmeta.get("matched_plan", "")
        if not plan_path:
            return None
        plan = self.get_note(plan_path)
        if not plan:
            return None
        pmeta, pbody = parse_frontmatter(plan)
        merged_att = []
        for x in _as_str_list(pmeta.get("attendees")) + _as_str_list(rmeta.get("attendees")):
            if x and x not in merged_att:
                merged_att.append(x)
        pmeta = dict(pmeta)
        now = datetime.now()
        pmeta["status"] = "done"
        pmeta["attendees"] = merged_att
        pmeta["recorded"] = now.isoformat(timespec="seconds")
        pmeta["merged_from"] = recording_path
        content = build_recording_into_plan_content(
            pmeta, pbody, rbody, now.strftime('%Y-%m-%d %H:%M'),
        )
        if not self.put_note(plan_path, content):
            return None
        if delete_recording:
            self.delete_note(recording_path)
        return plan_path

    def create_reference_note(
        self, term: str, description: str,
        sources: Optional[List[Dict[str, str]]] = None,
        category: str = "",
    ) -> Optional[str]:
        """
        용어/인물/기업 설명 노트를 refs_subdir 에 작성(이미 있으면 건너뜀).
        위키링크용 basename 반환(실패해도 basename 은 반환해 링크는 유지).
        """
        base = safe_filename(term)
        # category + project → 도메인 서브폴더 결정
        sub = self._refs_subfolder(category)
        path = f"{self.refs_subdir}/{sub}/{base}.md"
        # 같은 이름 노트가 References 하위 '어느 폴더에든' 이미 있으면 재사용 (중복 방지)
        if self._ref_note_exists(base):
            return base

        meta = {
            "title": term,
            "type": "reference",
            "category": category,
            "tags": ["용어집"] + ([category] if category else []),
            "created": datetime.now().isoformat(timespec="seconds"),
        }
        parts = [build_frontmatter(meta), "", f"# {term}\n", description.strip() + "\n"]
        if sources:
            parts.append("## 출처\n")
            for s in sources:
                u = s.get("url", "")
                t = s.get("title", u)
                if u:
                    parts.append(f"- [{t}]({u})")
        content = "\n".join(parts)
        self.put_note(path, content)
        return base

    # ── 볼트 초기 구조 스캐폴딩 ───────────────────────────
    def init_vault(self, force_index: bool = False) -> List[str]:
        """
        회의록 자동화용 표준 폴더 구조를 볼트에 생성.
        Obsidian은 빈 폴더를 REST로 못 만들므로 각 폴더에 _index(MOC) 노트를 둠.
        이미 있으면 덮어쓰지 않음(멱등). force_index=True면 _index MOC만 최신 문구로 갱신
        (사용자 콘텐츠인 99_Templates 등은 덮지 않음). 생성/갱신/확인된 경로 목록 반환.
        """
        created: List[str] = []
        # 용어가 실제로 저장되는 도메인 폴더(현 project 기준: 공통 또는 프로젝트 도메인)
        term_domain = self._refs_subfolder("용어·기술")
        scaffold = {
            f"{self.notes_subdir}/_index.md": (
                "---\ntype: moc\ntags:\n  - MOC\n---\n\n"
                "# 📅 회의록 (Meetings)\n\n"
                "회의·세미나·강의 기록이 프로젝트별 하위 폴더에 생성됩니다.\n\n"
                "```dataview\nTABLE type, project, topic, date\nFROM \"" + self.notes_subdir + "\"\n"
                "WHERE type\nSORT date DESC\n```\n"
            ),
            f"{self.refs_subdir}/_index.md": (
                "---\ntype: moc\ntags:\n  - MOC\n---\n\n"
                "# 📚 참고·용어 (References)\n\n"
                "회의록에서 추출된 설명 노트가 분류 저장됩니다:\n"
                "- **People/** — 인물\n- **Companies/** — 기업·기관\n"
                "- **용어·기술** — 프로젝트 도메인 폴더(예: 공통, GraphDB-온톨로지, 퀀텀)\n"
            ),
            f"{self.refs_subdir}/People/_index.md": (
                "---\ntype: moc\ntags:\n  - MOC\n---\n\n# 👤 인물 (People)\n"
            ),
            f"{self.refs_subdir}/Companies/_index.md": (
                "---\ntype: moc\ntags:\n  - MOC\n---\n\n# 🏢 기업·기관 (Companies)\n"
            ),
            f"{self.refs_subdir}/{term_domain}/_index.md": (
                "---\ntype: moc\ntags:\n  - MOC\n---\n\n# 🧩 용어·기술 — " + term_domain + "\n"
            ),
            "02_Sources/_index.md": (
                "---\ntype: moc\ntags:\n  - MOC\n---\n\n"
                "# 🗂️ 원본 (Sources)\n\n"
                "원본 스크립트·요약 등 부속 자료(선택).\n"
            ),
            "99_Templates/Meeting.md": (
                "---\ntitle: \"\"\ndate: \"\"\ntime: \"\"\ntype: meeting\n"
                "project: \"\"\ntopic: \"\"\nattendees: []\ncompany: \"\"\n"
                "status: planned\ntags:\n  - 회의록\n---\n\n# {{title}}\n\n"
                "## 사전 조사\n\n## 안건\n\n- \n\n## 메모\n\n- \n"
            ),
        }
        for path, content in scaffold.items():
            is_index = path.endswith("_index.md")
            if self.exists(path) and not (force_index and is_index):
                created.append(f"(이미 있음) {path}")
                continue
            ok = self.put_note(path, content)
            verb = "갱신" if (force_index and is_index and ok) else ("생성" if ok else "실패")
            created.append(f"{verb} {path}")
        return created

    def close(self):
        try:
            self._client.close()
        except Exception:
            pass


# ── CLI ───────────────────────────────────────────────────────
def _cli():
    # Windows 콘솔(cp949)에서 유니코드 기호/한글 출력 안전화
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")  # py3.7+
        except Exception:
            pass

    import argparse
    p = argparse.ArgumentParser(description="Obsidian Local REST API 연동 테스트")
    p.add_argument("--ping", action="store_true", help="연결/인증 확인")
    p.add_argument("--where", action="store_true", help="현재 Obsidian/회의록 저장 경로 진단")
    p.add_argument("--init-vault", action="store_true", help="표준 폴더 구조 생성")
    p.add_argument("--refresh-index", action="store_true", help="기존 _index(MOC) 문구를 최신으로 갱신")
    p.add_argument("--search", metavar="QUERY", help="볼트 단순 검색")
    p.add_argument("--test-note", action="store_true", help="테스트 노트 작성")
    args = p.parse_args()

    obs = ObsidianClient.from_config()
    if obs is None:
        print("✗ obsidian 설정 없음/비활성 — config.json 의 obsidian 섹션을 확인하세요.")
        print("  (enabled:true, api_url, api_key 필요. httpx 설치 필요.)")
        sys.exit(1)

    if args.ping or args.where or not (args.search or args.test_note or args.init_vault or args.refresh_index or args.where):
        ok = obs.ping()
        print(f"{'✓ 연결 성공' if ok else '✗ 연결 실패'} — {obs.api_url}")
        if not ok:
            sys.exit(1)

    if args.where:
        configured_vault = _c("obsidian.vault_path", "")
        indexed_vault = _c("indexing.vault_path", "") or configured_vault
        detected = _detect_obsidian_config(obs.api_key)
        print("\n경로 진단:")
        print(f"  - config.obsidian.vault_path : {configured_vault or '(미설정)'}")
        print(f"  - config.indexing.vault_path : {indexed_vault or '(미설정)'}")
        print(f"  - detected vault by api key   : {detected.get('vault_path', '(감지 실패)')}")
        if detected.get("open") is not None:
            print(f"  - detected vault is open      : {detected.get('open')}")
        print(f"  - notes_subdir                : {obs.notes_subdir}")
        print(f"  - meetings_path               : {obs.meetings_path or '(미설정)'}")
        print(f"  - transcripts_path            : {obs.transcripts_path or '(미설정)'}")
        today = datetime.now().strftime("%Y-%m-%d")
        meeting_folder = obs._meeting_folder(date_str=today)
        print(f"  - effective meeting folder    : {meeting_folder}")
        print(f"  - effective transcript folder : {obs._transcript_folder(meeting_folder, today)}")

    if args.init_vault or args.refresh_index:
        print("\n볼트 구조 " + ("갱신" if args.refresh_index else "생성") + ":")
        for line in obs.init_vault(force_index=args.refresh_index):
            print(f"  - {line}")

    if args.search:
        results = obs.search_simple(args.search)
        print(f"\n검색 결과 {len(results)}건 (query={args.search!r}):")
        for r in results:
            print(f"  - {r.get('filename')}  (score={r.get('score')})")

    if args.test_note:
        path = obs.write_meeting_note(
            title="연동 테스트 노트",
            body_md="## 본문\n\n이 노트는 obsidian.py --test-note 로 생성되었습니다.",
            doc_type="meeting",
            topic="연동 점검",
            attendees=["테스터"],
            glossary_md="- **테스트**: 연결 확인용 더미 용어.",
            external_refs=[{"title": "Obsidian Local REST API",
                            "url": "https://github.com/coddingtonbear/obsidian-local-rest-api"}],
        )
        print(f"\n{'✓ 노트 작성: ' + path if path else '✗ 노트 작성 실패'}")

    obs.close()


if __name__ == "__main__":
    _cli()
