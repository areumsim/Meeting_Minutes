#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
seminar_paper_research.py
세미나 분석 결과에서 논문을 추출하고 Obsidian에 개별 논문 노트를 생성합니다.

사용법:
  python scripts/seminar_paper_research.py --date 2026-06-29
  python scripts/seminar_paper_research.py --minutes output/20260629_141032_남우진교수/minutes.md
  python scripts/seminar_paper_research.py --date 2026-06-29 --output-dir ./output --dry-run
"""

import os
import sys
import json
import re
import argparse
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional

# meeting_minutes_app 경로 추가
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "meeting_minutes_app"))
sys.path.insert(0, str(_HERE.parent))

try:
    from meeting_minutes_app.common import config_loader as _cfg
    def cfg(key, default=None):
        return _cfg.get(key, default)
except ImportError:
    def cfg(key, default=None):
        return default

from meeting_minutes_app.common.llm_client import LLMClient
from meeting_minutes_app.wiki_core.obsidian import ObsidianClient, safe_filename, build_frontmatter


# ── 설정 ────────────────────────────────────────
PAPERS_PATH_TPL = cfg("obsidian.papers_path", "도메인_아카이브/02_논문/{year}")
OUTPUT_DIR = cfg("output_dir", "./output")


def _expand(tpl: str, year: str) -> str:
    return tpl.replace("{year}", year).replace("{yyyy}", year)


# ── 유틸 ─────────────────────────────────────────
def find_output_folders(date_str: str, output_dir: str = None) -> List[Path]:
    """날짜에 해당하는 output 폴더 목록 (오래된 순)"""
    base = Path(output_dir or OUTPUT_DIR)
    compact = date_str.replace("-", "")  # "20260629"
    if not base.exists():
        return []
    return sorted(
        [p for p in base.iterdir() if p.is_dir() and p.name.startswith(compact)],
        key=lambda p: p.name,
    )


def read_best_text(folder: Path) -> str:
    """minutes.md → transcript.md → summary.md → script_refined.txt → script.md 순으로 읽기"""
    for fname in ("minutes.md", "transcript.md", "summary.md", "script_refined.txt", "script.md"):
        f = folder / fname
        if f.exists():
            return f.read_text(encoding="utf-8", errors="replace")
    return ""


def infer_presenter_from_folder(folder: Path) -> str:
    """폴더명에서 발표자 추론: '20260629_141032_남우진교수' → '남우진교수'"""
    parts = folder.name.split("_")
    if len(parts) >= 3:
        return " ".join(parts[2:]).replace("교수", " 교수").strip()
    return ""


# ── 논문 추출 ────────────────────────────────────
_PAPER_BLOCK_RE = re.compile(
    r"---PAPER---\s*(.*?)\s*---END---", re.DOTALL | re.IGNORECASE
)
_FIELD_RE = re.compile(r"^(.+?):\s*(.+)$", re.MULTILINE)


def parse_papers_from_structured(text: str, presenter: str = "") -> List[Dict]:
    """프롬프트 구조 (---PAPER--- 블록) 파싱"""
    papers = []
    for m in _PAPER_BLOCK_RE.finditer(text):
        block = m.group(1)
        fields = dict(_FIELD_RE.findall(block))
        title = fields.get("제목", "").strip()
        if not title:
            continue
        authors_raw = fields.get("저자", "").strip()
        authors = [a.strip() for a in re.split(r"[,，、]", authors_raw) if a.strip()]
        pub_raw = fields.get("연도/학술지", "").strip()
        year_m = re.search(r"\b(19|20)\d{2}\b", pub_raw)
        year = year_m.group(0) if year_m else ""
        arxiv_m = re.search(r"arXiv[:\s]*(\d{4}\.\d{4,5})", pub_raw, re.IGNORECASE)
        arxiv = arxiv_m.group(1) if arxiv_m else ""
        journal = re.sub(r"\b(19|20)\d{2}\b|arXiv[:\s]*\S+", "", pub_raw).strip(" /·-")
        papers.append({
            "title": title,
            "title_ko": "",
            "authors": authors,
            "year": year,
            "journal": journal,
            "arxiv": arxiv,
            "doi": "",
            "context": fields.get("발표자 설명", "").strip(),
            "keywords": [k.strip() for k in
                         fields.get("핵심 개념", "").split(",") if k.strip()],
            "presenter": presenter or fields.get("발표자", "").strip(),
        })
    return papers


def extract_papers_via_llm(text: str, llm: LLMClient, presenter: str = "") -> List[Dict]:
    """LLM에게 전사본/분석 텍스트에서 논문 목록을 JSON으로 추출 요청"""
    system = """당신은 세미나 기록 분석 전문가입니다.
주어진 세미나 텍스트에서 언급된 모든 학술 논문을 찾아 JSON 배열로만 반환하세요.
논문이 없으면 [] 반환. 코드 블록, 설명 없이 JSON만 출력하세요.

각 항목 형식:
{
  "title": "논문 원제목",
  "title_ko": "한국어 제목 (없으면 null)",
  "authors": ["저자1", "저자2"],
  "year": "2024 또는 null",
  "journal": "학술지/arXiv 등 또는 null",
  "arxiv": "arXiv ID (예: 2401.12345) 또는 null",
  "doi": "DOI 또는 null",
  "context": "발표자가 소개한 내용 1-2문장",
  "keywords": ["키워드1", "키워드2"],
  "presenter": "발표자 이름"
}"""
    prompt = f"발표자: {presenter or '미상'}\n\n텍스트 (앞부분):\n{text[:7000]}"
    try:
        raw = llm.chat(system, prompt, temp=0.1, max_tokens=4000)
    except Exception:
        raw = None
    if not raw:
        return []
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if not m:
        return []
    try:
        papers = json.loads(m.group(0))
        return [p for p in papers if isinstance(p, dict) and p.get("title")]
    except Exception:
        return []


# ── 논문 조사 ────────────────────────────────────
def research_paper(paper: Dict, llm: LLMClient) -> Dict:
    """web_research로 논문 상세 정보 보완"""
    title = paper.get("title", "")
    authors = ", ".join(paper.get("authors") or [])
    year = paper.get("year") or ""
    arxiv = paper.get("arxiv") or ""

    query = f"""다음 양자 물리/컴퓨팅 논문의 상세 정보를 찾아주세요:
논문 제목: "{title}"
{"저자: " + authors if authors else ""}
{"연도: " + year if year else ""}
{"arXiv: " + arxiv if arxiv else ""}

다음 내용을 한국어로 제공해 주세요:
1. 정확한 제목 (원제 + 한국어)
2. 전체 저자 및 소속 기관
3. 발행 정보 (연도, 학술지, DOI 또는 arXiv ID)
4. 핵심 연구 내용 요약 (3-5문장)
5. 주요 기여 및 혁신점 (3-5가지 bullet)
6. 양자 기술 분야에서의 의의
"""
    result = llm.web_research(query, max_uses=3, max_tokens=2000)
    paper["research_text"] = result.get("text", "")
    paper["sources"] = result.get("sources", [])
    paper["web_searched"] = result.get("searched", False)
    return paper


# ── Obsidian 노트 생성 ───────────────────────────
def make_paper_note(paper: Dict, seminar_title: str, seminar_date: str) -> str:
    """논문 노트 마크다운 생성"""
    title = paper.get("title") or "Unknown Paper"
    title_ko = (paper.get("title_ko") or "").strip()
    authors = paper.get("authors") or []
    year = paper.get("year") or ""
    journal = paper.get("journal") or ""
    arxiv = paper.get("arxiv") or ""
    doi = paper.get("doi") or ""
    presenter = paper.get("presenter") or ""
    context = paper.get("context") or ""
    keywords = paper.get("keywords") or []
    research_text = paper.get("research_text") or ""
    sources = paper.get("sources") or []
    web_searched = paper.get("web_searched", False)

    tags = ["quantum", "paper"]
    if not web_searched:
        tags.append("검토필요")

    meta = {
        "title": title_ko or title,
        "title_en": title,
        "type": "paper",
        "authors": authors or None,
        "year": year or None,
        "journal": journal or None,
        "arxiv": arxiv or None,
        "doi": doi or None,
        "presenter": presenter or None,
        "seminar_date": seminar_date,
        "seminar": f"[[{seminar_title}]]",
        "tags": tags,
        "created": datetime.now().isoformat(timespec="seconds"),
        "web_verified": web_searched,
    }
    meta = {k: v for k, v in meta.items() if v is not None}
    fm = build_frontmatter(meta)

    author_str = ", ".join(authors) if authors else "저자 미상"
    pub_parts = [p for p in [year, journal] if p]
    if arxiv:
        pub_parts.append(f"arXiv:{arxiv}")
    if doi:
        pub_parts.append(f"DOI:{doi}")
    pub_info = " · ".join(pub_parts) if pub_parts else "출판 정보 미상"

    display_h = f"{title_ko}\n*({title})*" if title_ko else title

    lines = [
        fm, "",
        f"# {display_h}", "",
        f"> **{author_str}** ({pub_info})", "",
        "## 세미나 맥락", "",
    ]
    if presenter:
        lines.append(f"**발표자**: {presenter}")
    lines += [f"**세미나**: [[{seminar_title}]]", ""]
    if context:
        lines += [context, ""]
    else:
        lines += ["*세미나에서 소개된 맥락 — 추가 정리 필요*", ""]

    if research_text:
        lines += ["## 논문 개요", "", research_text, ""]
    else:
        lines += [
            "## 논문 개요", "",
            "> [!warning] 검토 필요",
            "> 웹 검색으로 상세 정보를 찾지 못했습니다. 수동으로 보완해주세요.",
            "",
        ]

    if keywords:
        kw_links = " · ".join(f"[[{k}]]" for k in keywords if k)
        lines += ["## 관련 개념", "", kw_links, ""]

    if sources:
        lines += ["## 출처", ""]
        for s in sources:
            url = s.get("url", "")
            ttl = s.get("title", url)
            if url:
                lines.append(f"- [{ttl}]({url})")
        lines.append("")

    if not web_searched:
        lines += [
            "## 검토 필요 사항", "",
            "- [ ] 논문 원문 확인 및 abstract 한국어 요약 추가",
            "- [ ] 정확한 DOI 또는 arXiv ID 확인",
            "- [ ] 주요 실험 결과 및 기여 보완",
            "- [ ] 관련 후속 연구 조사",
            "",
        ]

    return "\n".join(str(l) for l in lines)


def save_paper_note(obs: ObsidianClient, paper: Dict,
                    seminar_title: str, seminar_date: str,
                    dry_run: bool = False) -> Optional[str]:
    """Obsidian에 논문 노트 저장. 저장된 파일명(stem) 반환."""
    year = paper.get("year") or seminar_date[:4]
    papers_folder = _expand(PAPERS_PATH_TPL, year)
    note_name = safe_filename(paper.get("title_ko") or paper.get("title") or "Unknown")
    note_path = f"{papers_folder}/{note_name}.md"
    content = make_paper_note(paper, seminar_title, seminar_date)

    if dry_run:
        print(f"  [DRY-RUN] Would write: {note_path}")
        return note_name

    ok = obs.put_note(note_path, content)
    if ok:
        print(f"  ✓ 논문 노트 저장: {note_path}")
        return note_name
    else:
        print(f"  ✗ 저장 실패: {note_path}")
        return None


def append_paper_links_to_seminar(obs: ObsidianClient, seminar_note_path: str,
                                   paper_names: List[str], dry_run: bool = False):
    """세미나 노트에 논문 wikilink 섹션을 추가/업데이트"""
    if not paper_names:
        return
    content = obs.get_note(seminar_note_path)
    if not content:
        print(f"  ⚠ 세미나 노트를 찾을 수 없음: {seminar_note_path}")
        return

    links_block = "\n".join(f"- [[{n}]]" for n in paper_names)
    new_section = f"\n\n## 📄 논문 노트\n\n{links_block}\n"

    if "## 📄 논문 노트" in content:
        content = re.sub(
            r"## 📄 논문 노트\n.*?(?=\n## |\Z)",
            f"## 📄 논문 노트\n\n{links_block}\n",
            content, flags=re.DOTALL,
        )
    else:
        content = content.rstrip() + new_section

    if dry_run:
        print(f"  [DRY-RUN] Would update seminar note: {seminar_note_path}")
        return
    if obs.put_note(seminar_note_path, content):
        print(f"  ✓ 세미나 노트 논문 링크 업데이트: {seminar_note_path}")
    else:
        print(f"  ✗ 세미나 노트 업데이트 실패")


# ── 메인 ─────────────────────────────────────────
def process_folder(folder: Path, llm: LLMClient, obs: Optional[ObsidianClient],
                   seminar_note_path: Optional[str], dry_run: bool = False) -> List[str]:
    """단일 output 폴더 처리 → 저장된 논문 파일명 목록 반환"""
    presenter = infer_presenter_from_folder(folder)
    date_str = folder.name[:8]
    seminar_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    seminar_title = seminar_note_path.replace(".md", "").split("/")[-1] if seminar_note_path else folder.name

    print(f"\n📂 처리 중: {folder.name}")
    print(f"   발표자: {presenter or '(미상)'}")

    text = read_best_text(folder)
    if not text:
        print("   ⚠ 텍스트 파일 없음, 건너뜀")
        return []

    # 1) 구조화 파싱 시도
    papers = parse_papers_from_structured(text, presenter)
    print(f"   구조화 파싱: {len(papers)}편 발견")

    # 2) LLM 보완 추출 (구조화 파싱 결과가 적거나 없을 때)
    if len(papers) < 2:
        llm_papers = extract_papers_via_llm(text, llm, presenter)
        print(f"   LLM 추출: {len(llm_papers)}편 발견")
        # 중복 제거 (제목 기준)
        existing_titles = {p["title"].lower() for p in papers}
        for lp in llm_papers:
            if lp.get("title", "").lower() not in existing_titles:
                papers.append(lp)
                existing_titles.add(lp["title"].lower())

    if not papers:
        print("   논문 없음 — 건너뜀")
        return []

    print(f"   총 {len(papers)}편 처리 시작")
    saved_names = []

    for i, paper in enumerate(papers, 1):
        title = paper.get("title", "?")
        print(f"\n   [{i}/{len(papers)}] {title[:60]}")

        # 웹 조사
        paper = research_paper(paper, llm)
        status = "✓ 웹검색" if paper.get("web_searched") else "⚠ 폴백(검색 불가)"
        print(f"   {status}")

        if obs:
            name = save_paper_note(obs, paper, seminar_title, seminar_date, dry_run)
            if name:
                saved_names.append(name)
        else:
            # Obsidian 없으면 output 폴더에 저장
            note_content = make_paper_note(paper, seminar_title, seminar_date)
            out_file = folder / f"paper_{safe_filename(title)[:50]}.md"
            if not dry_run:
                out_file.write_text(note_content, encoding="utf-8")
                print(f"   ✓ 로컬 저장: {out_file.name}")
            else:
                print(f"   [DRY-RUN] Would write: {out_file}")
            saved_names.append(safe_filename(title)[:50])

    # 세미나 노트에 논문 링크 역삽입
    if obs and seminar_note_path and saved_names:
        append_paper_links_to_seminar(obs, seminar_note_path, saved_names, dry_run)

    return saved_names


def main():
    ap = argparse.ArgumentParser(description="세미나 논문 추출·조사·Obsidian 저장")
    ap.add_argument("--date", help="처리할 날짜 (YYYY-MM-DD). 해당 날짜 output 폴더 자동 탐색")
    ap.add_argument("--minutes", help="특정 minutes.md 파일 경로")
    ap.add_argument("--output-dir", default=None, help="output 폴더 경로 (기본: config output_dir)")
    ap.add_argument("--seminar-note", default=None,
                    help="세미나 노트 vault 상대경로 (예: 도메인_아카이브/.../2026-06-29 남우진교수.md)")
    ap.add_argument("--dry-run", action="store_true", help="실제 쓰기 없이 시뮬레이션")
    ap.add_argument("--llm", default="claude", choices=["claude", "gpt"], help="우선 LLM")
    args = ap.parse_args()

    if not args.date and not args.minutes:
        ap.error("--date 또는 --minutes 중 하나 필요")

    print("=" * 60)
    print("  세미나 논문 연구 스크립트")
    print(f"  실행 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if args.dry_run:
        print("  [DRY-RUN 모드]")
    print("=" * 60)

    llm = LLMClient(preferred=args.llm)

    obs: Optional[ObsidianClient] = None
    if cfg("obsidian.enabled", False):
        try:
            obs = ObsidianClient.from_config()
            if obs and obs.ping():
                print(f"✓ Obsidian 연결됨: {cfg('obsidian.vault_path', '')}")
            else:
                obs = None
                print("⚠ Obsidian REST API 응답 없음 → 로컬 output/ 에 저장")
        except Exception as e:
            obs = None
            print(f"⚠ Obsidian 연결 실패: {e} → 로컬 저장")
    else:
        print("ℹ Obsidian 비활성화 → 로컬 output/ 에 저장")

    output_dir = args.output_dir or OUTPUT_DIR

    if args.minutes:
        folder = Path(args.minutes).parent
        seminar_note = args.seminar_note
        process_folder(folder, llm, obs, seminar_note, args.dry_run)
    else:
        folders = find_output_folders(args.date, output_dir)
        if not folders:
            print(f"\n⚠ {args.date} 날짜의 output 폴더를 찾을 수 없습니다.")
            print(f"   탐색 위치: {output_dir}")
            print("   배치 처리 먼저 실행해주세요:")
            print(f'   python run_meeting.py batch "input/파일명.webm" --type seminar --language ko')
            sys.exit(1)
        print(f"\n발견된 폴더 {len(folders)}개:")
        for f in folders:
            print(f"  - {f.name}")
        for folder in folders:
            seminar_note = args.seminar_note
            process_folder(folder, llm, obs, seminar_note, args.dry_run)

    print("\n" + "=" * 60)
    print("  완료!")
    print("=" * 60)


if __name__ == "__main__":
    main()
