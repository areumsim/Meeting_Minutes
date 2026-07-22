"""
notifier.py – 알림 훅 (Email / Slack / Teams)
===============================================
회의록 생성 완료 후 자동으로 결과를 공유합니다.

지원 채널:
  - Email (Gmail / Naver / 기타 SMTP)
  - Slack  (Incoming Webhook)
  - Teams  (Incoming Webhook)

이메일 설정은 환경변수 또는 config.json 의 email 섹션에서 자동 로드됩니다.

사용 예:
    from meeting_minutes_app.common.notifier import Notifier
    notifier = Notifier.from_config({"notify": "email"})
    notifier.send(title="2025 Q2 주간회의",
                  summary_path="output/summary.md",
                  files=["output/minutes.md"])

    python run_meeting.py notifier          # 테스트 발송
"""

from __future__ import annotations

import os
import smtplib
from email import encoders
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional
from pathlib import Path

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

# config_loader 가 있으면 config.json 에서 이메일 설정 로드
try:
    from meeting_minutes_app.common import config_loader as _cfg_mod
    _cfg_ok = True
except ImportError:
    _cfg_mod = None  # type: ignore
    _cfg_ok = False


def _c(key: str, default=""):
    return _cfg_mod.get(key, default) if _cfg_ok else default


class Notifier:
    """알림 채널 통합 관리. 여러 채널을 동시에 등록하고 한 번에 발송."""

    def __init__(self):
        self._channels: list[dict] = []

    # ── 팩토리 ────────────────────────────────────────────

    @classmethod
    def from_config(cls, config: dict) -> "Notifier":
        """
        config dict 또는 문자열("email"/"slack"/"teams")에서 Notifier 생성.

        config 예시:
        {
            "notify": {
                "email": {"sender": "...", "password": "...", "recipients": ["..."]},
                "slack": {"webhook_url": "https://hooks.slack.com/..."}
            }
        }
        또는 단순 문자열:
        {"notify": "email"}
        """
        inst = cls()
        notify_cfg = config.get("notify", {})

        if isinstance(notify_cfg, str):
            # 단순 채널 이름 → 환경변수 + config.json 에서 설정 로드
            if notify_cfg == "email":
                notify_cfg = {"email": cls._email_from_env_and_config()}
            elif notify_cfg == "slack":
                notify_cfg = {"slack": cls._slack_from_env()}
            elif notify_cfg == "teams":
                notify_cfg = {"teams": cls._teams_from_env()}

        if "email" in notify_cfg and notify_cfg["email"]:
            inst.add_email(**notify_cfg["email"])
        if "slack" in notify_cfg and notify_cfg["slack"]:
            inst.add_slack(**notify_cfg["slack"])
        if "teams" in notify_cfg and notify_cfg["teams"]:
            inst.add_teams(**notify_cfg["teams"])

        return inst

    # ── 채널 등록 ─────────────────────────────────────────

    def add_email(
        self,
        sender: str = "",
        password: str = "",
        recipients: Optional[list[str]] = None,
        smtp_host: str = "",
        smtp_port: int = 0,
        **kwargs,
    ) -> "Notifier":
        sender = sender or os.getenv("EMAIL_SENDER", "") or _c("email.sender")
        password = password or os.getenv("EMAIL_PASSWORD", "") or _c("email.password")
        recip_str = _c("email.recipient", "")
        recipients = recipients or [
            r.strip() for r in os.getenv("EMAIL_RECIPIENTS", recip_str).split(",")
            if r.strip()
        ]
        # 받는 주소가 비어 있으면 보내는 주소로 자기 자신에게 발송(스키마 설명과 일치).
        if not recipients and sender:
            recipients = [sender]
        # SMTP 서버: 인자 > config(email.smtp_host) > 발신자 도메인 자동추정
        smtp_host = smtp_host or os.getenv("EMAIL_SMTP_HOST", "") or _c("email.smtp_host", "")
        smtp_port = smtp_port or int(os.getenv("EMAIL_SMTP_PORT", "") or _c("email.smtp_port", 0) or 0)
        if not smtp_host:
            low = sender.lower()
            if "naver" in low:
                smtp_host = "smtp.naver.com"
            elif "gmail" in low or "googlemail" in low:
                smtp_host = "smtp.gmail.com"
            elif any(k in low for k in ("outlook", "hotmail", "live.", "office365", "onmicrosoft")):
                # Outlook 개인 / Microsoft 365
                smtp_host = "smtp.office365.com"
            else:
                domain = sender.split("@")[-1] if "@" in sender else ""
                smtp_host = f"smtp.{domain}" if domain else "smtp.gmail.com"
        if not smtp_port:
            smtp_port = 587
        self._channels.append({
            "type": "email",
            "sender": sender,
            "password": password,
            "recipients": recipients,
            "smtp_host": smtp_host,
            "smtp_port": smtp_port,
        })
        return self

    def add_slack(self, webhook_url: str = "", **kwargs) -> "Notifier":
        self._channels.append({
            "type": "slack",
            "webhook_url": webhook_url or os.getenv("SLACK_WEBHOOK_URL", ""),
        })
        return self

    def add_teams(self, webhook_url: str = "", **kwargs) -> "Notifier":
        self._channels.append({
            "type": "teams",
            "webhook_url": webhook_url or os.getenv("TEAMS_WEBHOOK_URL", ""),
        })
        return self

    # ── 발송 ──────────────────────────────────────────────

    def send(
        self,
        title: str,
        summary_path: Optional[str] = None,
        files: Optional[list[str]] = None,
        extra_message: str = "",
        obsidian_path: str = "",
        doc_type: str = "meeting",
    ) -> list[dict]:
        """등록된 모든 채널로 알림 발송. Returns [{"channel": ..., "success": ...}]"""
        summary_text = ""
        if summary_path and os.path.exists(summary_path):
            with open(summary_path, "r", encoding="utf-8") as f:
                summary_text = f.read()

        results = []
        for ch in self._channels:
            try:
                if ch["type"] == "email":
                    self._send_email(ch, title, summary_text, files or [],
                                     obsidian_path, doc_type)
                    results.append({"channel": "email", "success": True})
                elif ch["type"] == "slack":
                    self._send_slack(ch, title, summary_text, extra_message)
                    results.append({"channel": "slack", "success": True})
                elif ch["type"] == "teams":
                    self._send_teams(ch, title, summary_text)
                    results.append({"channel": "teams", "success": True})
            except Exception as e:
                results.append({"channel": ch["type"], "success": False, "error": str(e)})

        return results

    # ── 첨부 파일 이름·설명 헬퍼 ───────────────────────────

    @staticmethod
    def _auto_label(fpath: str, title: str) -> str:
        """내부 파일명 → 이메일 첨부 표시 이름 (한눈에 용도 파악 가능)."""
        import re as _re
        safe = _re.sub(r'[<>:"/\\|?*\n\r]', '', title).strip()[:40]
        p = Path(fpath)
        stem = p.stem.lower()
        if stem == "minutes":
            return f"{safe}_분석노트.md"
        if "script_refined" in stem:
            return f"{safe}_전사본.txt"
        if "script" in stem:
            return f"{safe}_전사본{p.suffix}"
        if stem == "summary":
            return f"{safe}_요약.md"
        # Obsidian note 등 나머지는 원본 이름 유지
        return p.name

    @staticmethod
    def _attach_description(fpath: str) -> str:
        """첨부 파일 설명 문자열."""
        stem = Path(fpath).stem.lower()
        if stem == "minutes":
            return "상세 분석 기록 · Q&A · 검토 권고사항"
        if "script_refined" in stem or "script" in stem:
            return "STT 교정 전사본 (발표·회의 원문)"
        if stem == "summary":
            return "간략 요약본"
        return "참고 문서"

    @property
    def has_channels(self) -> bool:
        """유효한 채널이 있는지 확인."""
        for ch in self._channels:
            if (
                ch["type"] == "email"
                and ch.get("sender")
                and ch.get("password")
                and ch.get("recipients")
            ):
                return True
            if ch["type"] in ("slack", "teams") and ch.get("webhook_url"):
                return True
        return False

    # ── 이메일 발송 ───────────────────────────────────────

    @staticmethod
    def _build_html_body(title: str, summary: str, obsidian_path: str = "",
                         attach_info: list = None, doc_type: str = "meeting") -> str:
        """요약 마크다운을 이메일 본문 HTML로 변환 (심플 스타일).

        attach_info: [(display_filename, description), ...] — 첨부 파일 안내 박스 렌더링.
        """
        import re as _re

        # ── 섹션 파싱 (## / ### 모두 동일하게) ──────────
        secs: dict = {}
        sec_order: list = []
        cur_key: str = ""
        cur_lines: list = []
        for line in (summary or "").split("\n"):
            h = _re.match(r"^#{2,3}\s+(.+)", line)
            if h:
                if cur_key:
                    secs[cur_key] = "\n".join(cur_lines).strip()
                cur_key = h.group(1).strip()
                if cur_key not in secs:
                    sec_order.append(cur_key)
                cur_lines = []
            elif cur_key:
                cur_lines.append(line)
        if cur_key:
            secs[cur_key] = "\n".join(cur_lines).strip()

        def md_line(text: str) -> str:
            t = _re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
            return t

        def md_to_ul(text: str, ordered: bool = False) -> str:
            """마크다운 목록을 HTML ul/ol로 변환.
            들여쓰기 2칸 이상 = 서브불릿 (중첩 ul), ○ / ◦ = 서브불릿 기호로 처리.
            ────────── 구분선 = <hr>로 변환."""
            tag = "ol" if ordered else "ul"
            # 구분선 먼저 처리
            text = _re.sub(r"^─{4,}\s*$", "---DIVIDER---", text, flags=_re.MULTILINE)
            items = []
            sub_items: list = []

            def flush_sub():
                if sub_items:
                    inner = "".join(
                        f"<li style='margin:2px 0;line-height:1.6;color:#444'>{s}</li>"
                        for s in sub_items
                    )
                    items.append(
                        f"<ul style='margin:2px 0 6px 16px;padding:0;list-style:disc'>{inner}</ul>"
                    )
                    sub_items.clear()

            for raw_ln in text.split("\n"):
                if raw_ln.strip() == "---DIVIDER---":
                    flush_sub()
                    items.append("<hr style='border:none;border-top:1px solid #e0e0e0;margin:8px 0'>")
                    continue
                stripped = raw_ln.strip()
                indent = len(raw_ln) - len(raw_ln.lstrip())
                # 서브불릿 감지: 들여쓰기 4칸 이상 OR ○ / ◦ 로 시작
                is_sub = indent >= 4 or _re.match(r"^[○◦]\s", stripped)
                c = _re.sub(r"^\d+\.\s*", "", stripped)
                c = _re.sub(r"^[○◦\-\*]\s*", "", c).strip()
                c = md_line(c)
                if not c:
                    continue
                if is_sub:
                    sub_items.append(c)
                else:
                    flush_sub()
                    items.append(f"<li style='margin:5px 0;line-height:1.65'>{c}</li>")

            flush_sub()
            if not items:
                return ""
            html_items = "".join(items)
            return (f"<{tag} style='margin:6px 0 12px 20px;padding:0'>"
                    + html_items + f"</{tag}>")

        def sec_block(key: str, text: str, ordered: bool = False) -> str:
            body = md_to_ul(text, ordered=ordered)
            if not body:
                c = md_line(text.replace("\n", "<br>").strip())
                if not c:
                    return ""
                body = f"<p style='margin:6px 0 12px;line-height:1.65'>{c}</p>"
            return (
                f"<h3 style='font-size:14px;font-weight:600;color:#111;"
                f"margin:20px 0 4px;padding-bottom:4px;"
                f"border-bottom:1px solid #ddd'>{key}</h3>"
                + body
            )

        # ── 회의 정보 파싱 (일시·참석자·안건) ─────────────
        meeting_info_text = secs.get("회의 정보", "")
        date_from_info = ""
        attendees_str = ""
        agenda_items: list = []
        if meeting_info_text:
            dm2 = _re.search(r"\*\*일시\*\*.*?[:：]\s*(.+)", meeting_info_text)
            if dm2:
                date_from_info = dm2.group(1).strip()
            am = _re.search(r"\*\*참석자\*\*.*?[:：]\s*(.+)", meeting_info_text)
            if am:
                attendees_str = am.group(1).strip()
            in_agenda = False
            for ln in meeting_info_text.split("\n"):
                if _re.search(r"\*\*안건\*\*", ln):
                    in_agenda = True
                elif in_agenda and _re.match(r"^\d+\.", ln.strip()):
                    agenda_items.append(_re.sub(r"^\d+\.\s*", "", ln.strip()))

        # 회의 정보 테이블
        info_rows = ""
        if date_from_info:
            info_rows += (f"<tr><td style='color:#555;white-space:nowrap;padding:4px 14px 4px 0;"
                          f"vertical-align:top;font-weight:600'>일시</td>"
                          f"<td style='padding:4px 0'>{date_from_info}</td></tr>")
        if attendees_str:
            info_rows += (f"<tr><td style='color:#555;white-space:nowrap;padding:4px 14px 4px 0;"
                          f"vertical-align:top;font-weight:600'>참석자</td>"
                          f"<td style='padding:4px 0'>{attendees_str}</td></tr>")
        if agenda_items:
            items_html = "".join(f"<li style='margin:2px 0'>{a}</li>" for a in agenda_items)
            info_rows += (f"<tr><td style='color:#555;white-space:nowrap;padding:4px 14px 4px 0;"
                          f"vertical-align:top;font-weight:600'>안건</td>"
                          f"<td style='padding:4px 0'><ol style='margin:2px 0;padding-left:18px'>"
                          f"{items_html}</ol></td></tr>")
        meeting_info_html = ""
        if info_rows:
            meeting_info_html = (
                f"<table style='border-collapse:collapse;font-size:14px;"
                f"margin-bottom:16px'>{info_rows}</table>"
                f"<hr style='border:none;border-top:1px solid #ddd;margin:0 0 16px'>"
            )

        # ── 본문 섹션 렌더링 ──────────────────────────
        # 우선순위 섹션 먼저, 나머지는 순서대로
        SKIP_KEYS = {"회의 정보", "한눈에 보는 요약"}
        CONCLUSION_KEYS = {"한눈에 보는 결론", "결론"}

        body_html = meeting_info_html

        # 결론 단락 (박스 없이 일반 단락)
        conclusion = next((secs[k] for k in CONCLUSION_KEYS if k in secs), "")
        if conclusion:
            c = md_line(conclusion.replace("\n", " ").strip())
            body_html += f"<p style='margin:0 0 16px;line-height:1.75;color:#222'>{c}</p>"

        rendered = SKIP_KEYS | CONCLUSION_KEYS
        # 주요 섹션 고정 순서
        for key_group, ordered in [
            (["결정/합의", "결정사항", "결정 사항"], True),
            (["리스크/주의", "리스크", "주의사항"], False),
            (["다음 액션", "액션 아이템", "Action Item"], False),
        ]:
            for k in key_group:
                if k in secs and k not in rendered:
                    body_html += sec_block(k, secs[k], ordered=ordered)
                    rendered.update(key_group)
                    break

        # 나머지 섹션들도 순서대로
        for k in sec_order:
            if k not in rendered and secs.get(k, "").strip():
                body_html += sec_block(k, secs[k])
                rendered.add(k)

        if not body_html.strip():
            safe = (summary or "").replace("<", "&lt;").replace("\n", "<br>")
            body_html = f"<p style='line-height:1.75'>{safe}</p>"

        # Obsidian 경로 박스
        obs_html = ""
        if obsidian_path:
            obs_html = (
                f"<div style='background:#f0f4ff;border:1px solid #c5d0f0;border-radius:6px;"
                f"padding:10px 14px;margin:0 0 12px;font-size:13px'>"
                f"<span style='color:#555;font-weight:600'>📁 Obsidian 노트 위치:</span> "
                f"<code style='background:#e8ecff;padding:2px 6px;border-radius:3px;font-size:12px;"
                f"color:#2d4aa8'>{obsidian_path}</code>"
                f"</div>"
            )

        # 첨부 파일 안내 박스
        attach_guide_html = ""
        if attach_info:
            type_label = {"seminar": "세미나 분석노트", "lecture": "강의 분석노트"}.get(
                doc_type, "상세 회의록")
            rows = ""
            for fname, desc in attach_info:
                rows += (
                    f"<tr>"
                    f"<td style='padding:3px 14px 3px 0;white-space:nowrap;vertical-align:top'>"
                    f"<code style='background:#eef0f5;padding:2px 7px;border-radius:3px;"
                    f"font-size:12px;color:#2d4aa8'>{fname}</code></td>"
                    f"<td style='padding:3px 0;color:#555;font-size:13px;line-height:1.5'>{desc}</td>"
                    f"</tr>"
                )
            attach_guide_html = (
                f"<div style='background:#f8f9fb;border:1px solid #e2e5eb;border-radius:6px;"
                f"padding:10px 14px;margin:0 0 20px;font-size:13px'>"
                f"<div style='font-weight:600;color:#333;margin-bottom:7px'>📎 첨부 파일</div>"
                f"<table style='border-collapse:collapse'>{rows}</table>"
                f"<div style='margin-top:8px;font-size:12px;color:#888'>"
                f"이 이메일 본문은 <strong>요약</strong>입니다. "
                f"전체 내용은 첨부된 <strong>{type_label}</strong>을 열어 주세요.</div>"
                f"</div>"
            )

        subtype_label = {"seminar": "세미나 자동 기록", "lecture": "강의 자동 기록"}.get(
            doc_type, "회의록 자동 생성")

        return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
</head>
<body style="margin:0;padding:20px;background:#fff;
  font-family:'맑은 고딕','Noto Sans KR',Apple SD Gothic Neo,Arial,sans-serif;
  font-size:14px;color:#222;max-width:680px">
<h2 style="margin:0 0 4px;font-size:18px;font-weight:700;color:#111">{title}</h2>
<p style="margin:0 0 16px;font-size:12px;color:#888">{subtype_label}</p>
{obs_html}{attach_guide_html}{body_html}
<hr style="border:none;border-top:1px solid #ddd;margin:24px 0 12px">
<p style="margin:0;font-size:11px;color:#aaa;line-height:1.7">
  상세 분석 및 원문 전사본은 첨부 파일을 참고해 주세요.
</p>
</body></html>"""

    def _send_email(self, cfg: dict, title: str, summary: str, files: list[str],
                    obsidian_path: str = "", doc_type: str = "meeting") -> None:
        if not cfg.get("password"):
            raise ValueError("이메일 비밀번호 없음 (config.json email.password 또는 EMAIL_PASSWORD 환경변수)")

        # 첨부 파일별 표시 이름 + 설명 계산 (이메일 본문 안내 박스용)
        attach_info: list = []
        display_names: dict = {}  # fpath → display_name (실제 첨부 시 사용)
        for fpath in files:
            if not fpath or not os.path.exists(fpath):
                continue
            dlabel = self._auto_label(fpath, title)
            ddesc = self._attach_description(fpath)
            attach_info.append((dlabel, ddesc))
            display_names[fpath] = dlabel

        # 이메일 제목 — 문서 타입에 맞게
        subj_bracket = {"seminar": "세미나", "lecture": "강의"}.get(doc_type, "회의록")
        outer = MIMEMultipart("mixed")
        outer["From"] = cfg["sender"]
        outer["To"] = ", ".join(cfg["recipients"])
        outer["Subject"] = f"[{subj_bracket}] {title}"

        # plain + HTML 대안 파트
        plain_lines = [title]
        if obsidian_path:
            plain_lines.append(f"Obsidian: {obsidian_path}")
        if attach_info:
            plain_lines.append("\n[첨부 파일]")
            for fname, desc in attach_info:
                plain_lines.append(f"  · {fname} — {desc}")
        plain_lines.append("")
        plain_lines.append(summary if summary else f"{title} 처리가 완료되었습니다.")
        plain_body = "\n".join(plain_lines)

        alternative = MIMEMultipart("alternative")
        alternative.attach(MIMEText(plain_body, "plain", "utf-8"))
        alternative.attach(MIMEText(
            self._build_html_body(title, summary, obsidian_path, attach_info, doc_type),
            "html", "utf-8"
        ))
        outer.attach(alternative)

        for fpath in files:
            dname = display_names.get(fpath)
            part = self._build_attachment(fpath, display_name=dname)
            if part is not None:
                outer.attach(part)

        with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"], timeout=20) as server:
            server.ehlo()
            server.starttls()
            server.login(cfg["sender"], cfg["password"])
            server.sendmail(cfg["sender"], cfg["recipients"], outer.as_string())

    @staticmethod
    def _build_attachment(fpath: str, display_name: str = None):
        """Attach files with RFC-compliant UTF-8 filenames.

        display_name: \uc774\uba54\uc77c\uc5d0 \ud45c\uc2dc\ub420 \ud30c\uc77c\uba85 (\uc5c6\uc73c\uba74 \uc6d0\ubcf8 \ud30c\uc77c\uba85 \uc0ac\uc6a9).
        .md \ud30c\uc77c\uc740 config email.markdown_attachment \uc124\uc815\uc5d0 \ub530\ub77c text/markdown \ub610\ub294
        text/plain(.txt)\uc73c\ub85c \uc804\uc1a1\ub429\ub2c8\ub2e4.
        """
        if not fpath or not os.path.exists(fpath):
            return None
        path = Path(fpath)
        mode = str(_c("email.markdown_attachment", "txt") or "txt").lower()

        if path.suffix.lower() == ".md":
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                text = path.read_text(encoding="utf-8-sig", errors="replace")
            if mode in ("markdown", "md"):
                payload = text.encode("utf-8")
                part = MIMEBase("text", "markdown", charset="utf-8")
                # display_name\uc774 .md\uac00 \uc544\ub2c8\uba74 .md \uac15\uc81c (\ud655\uc7a5\uc790 \uc720\uc9c0)
                if display_name:
                    filename = display_name if display_name.endswith(".md") else display_name + ".md"
                else:
                    filename = path.name
            else:
                # BOM helps Outlook/Windows clients open UTF-8 text correctly.
                payload = ("\ufeff" + text).encode("utf-8")
                part = MIMEBase("text", "plain", charset="utf-8")
                if display_name:
                    filename = display_name if display_name.endswith(".txt") else Path(display_name).stem + ".txt"
                else:
                    filename = path.with_suffix(".txt").name
            part.set_payload(payload)
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", "attachment", filename=filename)
            return part

        # \ud14d\uc2a4\ud2b8/\ubc14\uc774\ub108\ub9ac \ud30c\uc77c
        with open(path, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        filename = display_name or path.name
        part.add_header("Content-Disposition", "attachment", filename=filename)
        return part

    # ── Slack 발송 ────────────────────────────────────────

    def _send_slack(self, cfg: dict, title: str, summary: str, extra: str = "") -> None:
        if not HAS_REQUESTS:
            raise ImportError("pip install requests 가 필요합니다")
        if len(summary) > 3000:
            summary = summary[:2950] + "\n\n... (전문은 첨부 파일 참고)"

        blocks = [
            {"type": "header",
             "text": {"type": "plain_text", "text": f"[회의록] {title}", "emoji": True}},
            {"type": "section",
             "text": {"type": "mrkdwn", "text": summary or "회의록이 생성되었습니다."}},
        ]
        if extra:
            blocks.append({"type": "context",
                           "elements": [{"type": "mrkdwn", "text": extra}]})

        resp = requests.post(cfg["webhook_url"], json={"blocks": blocks}, timeout=10)
        resp.raise_for_status()

    # ── Teams 발송 ────────────────────────────────────────

    def _send_teams(self, cfg: dict, title: str, summary: str) -> None:
        if not HAS_REQUESTS:
            raise ImportError("pip install requests 가 필요합니다")
        if len(summary) > 5000:
            summary = summary[:4950] + "\n\n... (전문은 파일 참고)"

        payload = {
            "type": "message",
            "attachments": [{
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard", "version": "1.4",
                    "body": [
                        {"type": "TextBlock", "text": f"[회의록] {title}",
                         "size": "Large", "weight": "Bolder"},
                        {"type": "TextBlock", "text": summary or "회의록이 생성되었습니다.",
                         "wrap": True},
                    ],
                },
            }],
        }
        resp = requests.post(cfg["webhook_url"], json=payload, timeout=10)
        resp.raise_for_status()

    # ── 환경변수 + config.json 헬퍼 ──────────────────────

    @staticmethod
    def _email_from_env_and_config() -> dict:
        sender = os.getenv("EMAIL_SENDER", "") or _c("email.sender")
        recip = _c("email.recipient", "")
        recipients = [r.strip() for r in
                      os.getenv("EMAIL_RECIPIENTS", recip).split(",") if r.strip()]
        # 받는 주소가 비면 보내는 주소로 자기 자신에게 발송.
        if not recipients and sender:
            recipients = [sender]
        return {
            "sender": sender,
            "password": os.getenv("EMAIL_PASSWORD", "") or _c("email.password"),
            "recipients": recipients,
        }

    @staticmethod
    def _slack_from_env() -> dict:
        return {"webhook_url": os.getenv("SLACK_WEBHOOK_URL", "")}

    @staticmethod
    def _teams_from_env() -> dict:
        return {"webhook_url": os.getenv("TEAMS_WEBHOOK_URL", "")}


# ── CLI 테스트 ────────────────────────────────────────────
if __name__ == "__main__":
    print("알림 테스트")
    print("=" * 40)

    slack_url = os.getenv("SLACK_WEBHOOK_URL")
    if slack_url:
        n = Notifier()
        n.add_slack(slack_url)
        results = n.send(title="테스트 회의록", extra_message="알림 테스트입니다.")
        print(f"Slack: {results}")
    else:
        print("SLACK_WEBHOOK_URL 환경변수 또는 config.json 을 설정하면 Slack 테스트 가능")

    sender = _c("email.sender") or os.getenv("EMAIL_SENDER")
    if sender:
        n = Notifier()
        n.add_email()
        if n.has_channels:
            results = n.send(title="테스트 회의록")
            print(f"Email: {results}")
        else:
            print("이메일 비밀번호가 설정되지 않았습니다 (config.json email.password)")
    else:
        print("config.json email.sender 또는 EMAIL_SENDER 환경변수를 설정하면 이메일 테스트 가능")
