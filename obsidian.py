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
  - 회의록: 00_Meetings/<도메인>/<날짜 제목>.md   (도메인 = project_domains 매핑 or project명, 없으면 기타)
  - 인물:   01_References/People/<이름>.md
  - 기업:   01_References/Companies/<이름>.md
  - 용어:   01_References/<도메인>/<용어>.md        (없으면 공통)

사용 예:
    from obsidian import ObsidianClient
    obs = ObsidianClient.from_config()
    if obs and obs.ping():
        obs.write_meeting_note(title="2025 양자 세미나", body_md=minutes, ...)

CLI:
    python obsidian.py --ping
    python obsidian.py --search "양자"
    python obsidian.py --test-note
"""

from __future__ import annotations

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
    import config_loader as _cfg
    _cfg_ok = True
except ImportError:
    _cfg = None  # type: ignore
    _cfg_ok = False


def _c(key: str, default: Any = None) -> Any:
    return _cfg.get(key, default) if _cfg_ok else default


# ── 파일명/링크 유틸 ──────────────────────────────────────────
# Obsidian/OS 양쪽에서 안전하지 않은 문자
_UNSAFE = re.compile(r'[\\/:*?"<>|#^\[\]]')


def safe_filename(name: str, max_len: int = 80) -> str:
    """노트 파일명으로 안전한 문자열로 정리(확장자 제외)."""
    name = (name or "").strip()
    name = _UNSAFE.sub(" ", name)
    name = re.sub(r"\s+", " ", name).strip()
    if not name:
        name = "untitled"
    return name[:max_len].strip()


def _yaml_escape(v: str) -> str:
    """YAML 스칼라 값에 안전하도록 따옴표 처리."""
    v = str(v).replace('"', '\\"')
    return f'"{v}"'


def build_frontmatter(meta: Dict[str, Any]) -> str:
    """dict → YAML frontmatter 블록. list 값은 YAML 시퀀스로."""
    lines = ["---"]
    for k, v in meta.items():
        if v is None or v == "" or v == []:
            continue
        if isinstance(v, (list, tuple)):
            lines.append(f"{k}:")
            for item in v:
                lines.append(f"  - {_yaml_escape(item)}")
        elif isinstance(v, bool):
            lines.append(f"{k}: {'true' if v else 'false'}")
        elif isinstance(v, (int, float)):
            lines.append(f"{k}: {v}")
        else:
            lines.append(f"{k}: {_yaml_escape(v)}")
    lines.append("---")
    return "\n".join(lines)


def wikilink(basename: str, alias: Optional[str] = None) -> str:
    """[[노트]] 또는 [[노트|별칭]] 형식 위키링크."""
    base = safe_filename(basename)
    return f"[[{base}|{alias}]]" if alias and alias != base else f"[[{base}]]"


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
        """config.json 의 obsidian 섹션에서 생성. 비활성/미설정 시 None."""
        if not _c("obsidian.enabled", False):
            return None
        api_url = _c("obsidian.api_url", "https://127.0.0.1:27124")
        api_key = _c("obsidian.api_key", "")
        if not api_key:
            return None
        if not HAS_HTTPX:
            return None
        return cls(
            api_url=api_url,
            api_key=api_key,
            notes_subdir=_c("obsidian.notes_subdir", "00_Meetings"),
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

    def _vault_path(self, path: str) -> str:
        # 경로 구분자는 유지하고 각 세그먼트만 인코딩
        return "/vault/" + quote(path.strip("/"), safe="/")

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
    ) -> Optional[str]:
        """
        회의록 노트를 볼트 notes_subdir 에 작성. 작성된 볼트 상대경로 반환(실패 시 None).
        """
        date_str = datetime.now().strftime("%Y-%m-%d")
        base = safe_filename(f"{date_str} {title}" if title else f"{date_str} 회의록")
        # 회의록 폴더 = 용어 폴더와 동일한 도메인 (project 없으면 기타/)
        project_dir = self._project_domain() or "기타"
        path = f"{self.notes_subdir}/{project_dir}/{base}.md"

        meta = {
            "title": title or base,
            "date": session_dt or date_str,
            "type": doc_type,
            "project": self.project or "",
            "topic": topic,
            "attendees": attendees or [],
            "tags": (tags or []) + ["회의록", doc_type],
            "created": datetime.now().isoformat(timespec="seconds"),
        }

        parts = [build_frontmatter(meta), ""]
        parts.append(f"# {title or base}\n")

        if summary_md.strip():
            parts.append("## 요약\n")
            parts.append(summary_md.strip() + "\n")

        parts.append(body_md.strip() + "\n")

        if glossary_md.strip():
            parts.append("## 용어·배경\n")
            parts.append(glossary_md.strip() + "\n")

        if actions_md.strip():
            parts.append("## 액션 아이템\n")
            parts.append(actions_md.strip() + "\n")

        ref_lines = self._build_references(related_notes, external_refs)
        if ref_lines:
            parts.append("## 참고 자료\n")
            parts.append(ref_lines + "\n")

        content = "\n".join(parts)
        return path if self.put_note(path, content) else None

    def _build_references(
        self,
        related_notes: Optional[List[str]],
        external_refs: Optional[List[Dict[str, str]]],
    ) -> str:
        lines: List[str] = []
        for note in related_notes or []:
            if note:
                lines.append(f"- {wikilink(note)}")
        for ref in external_refs or []:
            t = ref.get("title", ref.get("url", ""))
            u = ref.get("url", "")
            if u:
                lines.append(f"- [{t}]({u})")
            elif t:
                lines.append(f"- {t}")
        return "\n".join(lines)

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
                "---\ntitle: \ndate: \ntype: meeting\ntopic: \nattendees: []\n"
                "tags:\n  - 회의록\n---\n\n# {{title}}\n\n## 요약\n\n## 본문\n\n"
                "## 용어·배경\n\n## 액션 아이템\n\n## 참고 자료\n"
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

    if args.ping or not (args.search or args.test_note or args.init_vault or args.refresh_index):
        ok = obs.ping()
        print(f"{'✓ 연결 성공' if ok else '✗ 연결 실패'} — {obs.api_url}")
        if not ok:
            sys.exit(1)

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
