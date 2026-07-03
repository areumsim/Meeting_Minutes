"""
scripts/resend_seminar_email.py
개선된 내용으로 세미나 이메일 재발송 (STT 없이 기존 파일 사용)
"""
import sys
import os
import re

# 저장소 루트 경로 추가 (meeting_minutes_app 패키지 임포트용)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pathlib import Path
from meeting_minutes_app.common.date_utils import iso_to_yymmdd, parse_iso_date_from_text
from meeting_minutes_app.common.notifier import Notifier
from meeting_minutes_app.wiki_core.obsidian import safe_filename

def load_config():
    import json
    cfg_path = Path(__file__).parent.parent / "config.json"
    with open(cfg_path, encoding="utf-8") as f:
        return json.load(f)

def _c(cfg, key, default=""):
    parts = key.split(".")
    v = cfg
    for p in parts:
        if isinstance(v, dict):
            v = v.get(p, default)
        else:
            return default
    return v or default

def resend(output_dir: str, title: str, obsidian_path: str = "", doc_type: str = "seminar"):
    cfg = load_config()
    folder = Path(output_dir)

    # 이메일 본문: summary.md (요약)
    if not (folder / "summary.md").exists():
        print(f"  오류: summary.md 없음 ({folder})")
        return
    body_path = str(folder / "summary.md")
    print(f"  이메일 본문: summary.md ({(folder/'summary.md').stat().st_size:,} bytes)")

    # 첨부: minutes.md(상세 분석), script_refined.txt(교정 전사본) 우선 / 없으면 script.md
    # summary.md는 본문이므로 첨부에서 제외
    attach = []
    for fname in ("minutes.md",):
        p = folder / fname
        if p.exists():
            attach.append(str(p))
            print(f"  첨부: {fname} ({p.stat().st_size:,} bytes)")

    # 교정 전사본
    refined = folder / "script_refined.txt"
    raw_script = folder / "script.md"
    if refined.exists():
        attach.append(str(refined))
        print(f"  첨부: script_refined.txt ({refined.stat().st_size:,} bytes)")
    elif raw_script.exists():
        attach.append(str(raw_script))
        print(f"  첨부(원본): script.md ({raw_script.stat().st_size:,} bytes)")

    email_cfg = {
        "sender":     _c(cfg, "email.sender"),
        "password":   _c(cfg, "email.password"),
        "recipients": [r.strip() for r in _c(cfg, "email.recipient", "").split(",") if r.strip()],
        "smtp_host":  _c(cfg, "email.smtp_host"),
        "smtp_port":  int(_c(cfg, "email.smtp_port") or 0),
    }
    notifier = Notifier()
    notifier.add_email(**email_cfg)
    if not notifier.has_channels:
        print("  오류: 이메일 설정 없음")
        return

    results = notifier.send(
        title=title,
        summary_path=body_path,
        files=attach,
        obsidian_path=obsidian_path,
        doc_type=doc_type,
    )
    for r in results:
        status = "완료" if r["success"] else f"실패: {r.get('error', '')}"
        print(f"  이메일 발송 {status}")


def _find_output_folder(base: Path, keyword: str) -> Path | None:
    """output/ 폴더에서 키워드를 포함하는 가장 최신 폴더를 찾습니다."""
    candidates = [d for d in base.iterdir() if d.is_dir() and keyword in d.name]
    if not candidates:
        return None
    return max(candidates, key=lambda d: d.stat().st_mtime)


def _format_path_template(template: str, iso_date: str) -> str:
    if not template:
        return ""
    year, month = iso_date[:4], iso_date[5:7]
    return (
        template.replace("{year}", year)
        .replace("{yyyy}", year)
        .replace("{yy}", year[2:])
        .replace("{month}", month)
    ).strip("/")


def _meeting_folder(cfg: dict, iso_date: str) -> str:
    meetings_path = _c(cfg, "obsidian.meetings_path", "")
    if meetings_path:
        return _format_path_template(meetings_path, iso_date)
    return str(_c(cfg, "obsidian.notes_subdir", "00_Meetings") or "00_Meetings").strip("/")


def _source_title_from_folder(folder: Path) -> str:
    name = folder.name
    patterns = (
        r"^\d{4}-\d{2}-\d{2}_(.+)$",
        r"^\d{8}_\d{6}_(.+)$",
        r"^\d{8}_(.+)$",
    )
    for pattern in patterns:
        m = re.match(pattern, name)
        if m:
            return m.group(1).strip()
    return name


def infer_obsidian_path(cfg: dict, folder: Path) -> str:
    """현재 Obsidian 발행 규칙과 같은 yymmdd 파일명을 계산합니다."""
    title = _source_title_from_folder(folder)
    iso_date = parse_iso_date_from_text(title, default_today=False) or parse_iso_date_from_text(folder.name, default_today=True)
    prefix = iso_to_yymmdd(iso_date)
    base = safe_filename(f"{prefix} {title}" if prefix else title)
    return f"{_meeting_folder(cfg, iso_date)}/{base}.md"


if __name__ == "__main__":
    base = Path(__file__).parent.parent / "output"

    seminars = [
        {
            "keyword": "남우진교수",
            "title": "남우진교수 퀀텀 세미나 (양자 머신러닝)",
            "doc_type": "seminar",
        },
        {
            "keyword": "서지훈교수",
            "title": "서지훈교수 퀀텀 세미나 (퀀텀 어닐링 + 생성모델)",
            "doc_type": "seminar",
        },
    ]

    for s in seminars:
        print(f"\n{'='*60}")
        folder = _find_output_folder(base, s["keyword"])
        if not folder:
            print(f"  오류: '{s['keyword']}' 포함 폴더 없음 (output/ 확인)")
            continue
        title = s["title"]
        obs_path = infer_obsidian_path(load_config(), folder)
        print(f"발송: {title}")
        print(f"폴더: {folder.name}")
        print(f"Obsidian: {obs_path}")
        resend(str(folder), title, obs_path, s["doc_type"])
    print("\n완료!")
