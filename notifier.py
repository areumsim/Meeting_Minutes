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
    from notifier import Notifier
    notifier = Notifier.from_config({"notify": "email"})
    notifier.send(title="2025 Q2 주간회의",
                  summary_path="output/summary.md",
                  files=["output/minutes.md"])

    python notifier.py          # 테스트 발송
"""

from __future__ import annotations

import json
import os
import smtplib
import ssl as ssl_module
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from typing import Optional
from pathlib import Path

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

# config_loader 가 있으면 config.json 에서 이메일 설정 로드
try:
    import config_loader as _cfg_mod
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
        smtp_port: int = 587,
        **kwargs,
    ) -> "Notifier":
        sender = sender or os.getenv("EMAIL_SENDER", "") or _c("email.sender")
        password = password or os.getenv("EMAIL_PASSWORD", "") or _c("email.password")
        recip_str = _c("email.recipient", "")
        recipients = recipients or [
            r.strip() for r in os.getenv("EMAIL_RECIPIENTS", recip_str).split(",")
            if r.strip()
        ]
        if not smtp_host:
            if "naver" in sender:
                smtp_host = "smtp.naver.com"
            elif "gmail" in sender:
                smtp_host = "smtp.gmail.com"
            else:
                domain = sender.split("@")[-1] if "@" in sender else ""
                smtp_host = f"smtp.{domain}" if domain else "smtp.gmail.com"
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
                    self._send_email(ch, title, summary_text, files or [])
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

    @property
    def has_channels(self) -> bool:
        """유효한 채널이 있는지 확인."""
        for ch in self._channels:
            if ch["type"] == "email" and ch.get("sender") and ch.get("password"):
                return True
            if ch["type"] in ("slack", "teams") and ch.get("webhook_url"):
                return True
        return False

    # ── 이메일 발송 ───────────────────────────────────────

    def _send_email(self, cfg: dict, title: str, summary: str, files: list[str]) -> None:
        if not cfg.get("password"):
            raise ValueError("이메일 비밀번호 없음 (config.json email.password 또는 EMAIL_PASSWORD 환경변수)")

        msg = MIMEMultipart()
        msg["From"] = cfg["sender"]
        msg["To"] = ", ".join(cfg["recipients"])
        msg["Subject"] = f"[회의록] {title}"
        body = f"## {title}\n\n{summary}" if summary else f"{title} 처리가 완료되었습니다."
        msg.attach(MIMEText(body, "plain", "utf-8"))

        for fpath in files:
            if os.path.exists(fpath):
                with open(fpath, "rb") as f:
                    part = MIMEApplication(f.read(), Name=os.path.basename(fpath))
                    part["Content-Disposition"] = f'attachment; filename="{os.path.basename(fpath)}"'
                    msg.attach(part)

        with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"], timeout=20) as server:
            server.ehlo()
            server.starttls()
            server.login(cfg["sender"], cfg["password"])
            server.sendmail(cfg["sender"], cfg["recipients"], msg.as_string())

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
        return {
            "sender": sender,
            "password": os.getenv("EMAIL_PASSWORD", "") or _c("email.password"),
            "recipients": [r.strip() for r in
                           os.getenv("EMAIL_RECIPIENTS", recip).split(",") if r.strip()],
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
