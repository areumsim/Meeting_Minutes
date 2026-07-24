"""
api/settings.py — 설정 읽기/쓰기 API
"""

import json
import copy
import sys
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from web.backend.paths import EXE_DIR

router = APIRouter(tags=["settings"])

CONFIG_PATH = Path(EXE_DIR) / "config.json"

# 허용 최상위 섹션은 config.example.json 의 실제 최상위 키에서 자동 도출한다.
# (과거엔 이 목록을 손으로 유지하다 "output" 오타·wiki_knowledge/vault_watcher 누락으로
#  해당 섹션 저장이 422로 막히는 버그가 반복됐다. 새 기능 섹션은 example 에만 추가하면 됨.)
_ALLOWED_FALLBACK = {
    "api", "models", "realtime", "email", "obsidian",
    "indexing", "wiki", "wiki_knowledge", "notify", "ssl", "server",
    "output_dir", "vault_watcher", "mcp", "supermemory", "analysis",
}


def _allowed_sections() -> set:
    """config.example.json 최상위 키(_접두·config_version 제외)에서 허용 섹션 도출."""
    try:
        from meeting_minutes_app.common import app_paths
        p = app_paths.get_example_config_path()
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            derived = {k for k in data if not k.startswith("_") and k != "config_version"}
            if derived:
                return derived
    except Exception:
        pass
    return set(_ALLOWED_FALLBACK)


def _coerce_value(field: dict, value):
    """스키마 필드 타입에 맞춰 값 변환/검증. 실패 시 ValueError(한국어 메시지).

    프론트 폼은 이미 올바른 타입을 보내므로 이 함수는 주로 '고급: 전체 설정(JSON)'
    편집기·API 직접 호출에 대한 안전망이다. 스키마에 없는 키는 이 함수를 거치지 않는다.
    """
    ftype = field.get("type", "text")
    label = field.get("label", field.get("key", ""))
    if value is None:
        return value
    if ftype == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1", "yes", "on", "y")
        raise ValueError(f"'{label}' 값은 참/거짓이어야 합니다.")
    if ftype == "number":
        if isinstance(value, bool):
            raise ValueError(f"'{label}' 값은 숫자여야 합니다.")
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            s = value.strip()
            if s == "":
                raise ValueError(f"'{label}' 값은 숫자여야 합니다(비어 있음).")
            try:
                return int(s)
            except ValueError:
                try:
                    return float(s)
                except ValueError:
                    raise ValueError(f"'{label}' 값은 숫자여야 합니다: {value!r}")
        raise ValueError(f"'{label}' 값은 숫자여야 합니다.")
    if ftype == "select":
        valid = set()
        for o in field.get("options") or []:
            valid.add(o["value"] if isinstance(o, dict) else o)
        if valid and value not in valid:
            raise ValueError(f"'{label}' 에 허용되지 않는 값: {value!r} (허용: {sorted(valid)})")
        return value
    if ftype == "list":
        # 폼은 줄바꿈 구분 문자열로 보낼 수 있다(예: 감시할 폴더 목록 textarea).
        # 빈 줄/공백은 걸러 문자열 리스트로 정규화한다.
        if isinstance(value, list):
            return [str(x).strip() for x in value if str(x).strip()]
        if isinstance(value, str):
            return [p.strip() for p in value.replace("\r", "").split("\n") if p.strip()]
        raise ValueError(f"'{label}' 값은 목록(또는 줄바꿈 구분 문자열)이어야 합니다.")
    return value


def _mask_key(key: str) -> str:
    if not key or len(key) < 8:
        return "***"
    return key[:8] + "..." + key[-4:]


def _sensitive_paths() -> list:
    """스키마가 표시한 비밀 값 경로 목록. 스키마 로드 실패 시 최소 기본값으로 폴백."""
    try:
        from meeting_minutes_app.common import config_schema
        return config_schema.sensitive_paths()
    except Exception:
        return [
            "api.openai_api_key", "api.anthropic_api_key",
            "obsidian.api_key", "supermemory.api_key", "email.password",
        ]


def _dget(container: dict, dotted: str):
    """'a.b.c' 점 경로로 중첩 값 조회(없으면 None). 단일 키도 처리."""
    node = container
    for p in dotted.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(p)
    return node


def _dset(container: dict, dotted: str, val) -> None:
    """'a.b.c' 점 경로로 중첩 값 설정(중간 dict 자동 생성). 단일 키도 처리."""
    parts = dotted.split(".")
    node = container
    for p in parts[:-1]:
        if not isinstance(node.get(p), dict):
            node[p] = {}
        node = node[p]
    node[parts[-1]] = val


@router.get("/config")
def get_config():
    if not CONFIG_PATH.exists():
        return {"error": "config.json not found"}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    safe = copy.deepcopy(cfg)

    # 스키마가 지정한 모든 비밀 값 마스킹(키/비밀번호가 브라우저로 평문 전송되지 않도록).
    # 경로는 점 표기(중첩) 지원: 예) notify.slack.webhook_url
    for path in _sensitive_paths():
        v = _dget(safe, path)
        if isinstance(v, str) and v:
            _dset(safe, path, _mask_key(v))

    # 방어적: api.* 안의 'key' 포함 필드 + email.password 는 스키마와 무관하게 항상 마스킹
    if isinstance(safe.get("api"), dict):
        for k, v in safe["api"].items():
            if "key" in k.lower() and isinstance(v, str) and v:
                safe["api"][k] = _mask_key(v)
    if isinstance(safe.get("email"), dict) and safe["email"].get("password"):
        safe["email"]["password"] = "***"

    return safe


def _is_local_client(request: Request) -> bool:
    """요청이 이 PC(localhost)에서 왔는지 — LAN/모바일 클라이언트와 구분."""
    host = (request.client.host if request.client else "") or ""
    return host in ("127.0.0.1", "::1", "localhost")


@router.get("/config/reveal")
def reveal_secret(path: str, request: Request):
    """민감 값(키·비번)의 실제 평문을 반환 — 단, 이 PC(localhost)에서만.

    같은 WiFi의 iOS/태블릿 등 LAN 클라이언트에는 거부한다(폰이 PC의 실제 키를
    빼가지 못하도록). 웹 [설정]의 '보이기'가 이 엔드포인트로 실제 값을 가져온다.
    """
    if not _is_local_client(request):
        raise HTTPException(status_code=403,
                            detail="실제 키는 이 PC에서만 볼 수 있습니다.")
    if path not in set(_sensitive_paths()):
        raise HTTPException(status_code=400, detail="허용되지 않은 경로입니다.")
    if not CONFIG_PATH.exists():
        return {"value": ""}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    v = _dget(cfg, path)
    return {"value": v if isinstance(v, str) else ""}


@router.get("/config/schema")
def get_config_schema():
    """웹 Settings 자동 렌더링용 스키마(그룹/필드) 반환."""
    try:
        from meeting_minutes_app.common import config_schema
        return {"version": config_schema.CONFIG_VERSION, "groups": config_schema.get_schema()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"스키마 로드 실패: {e}")


@router.put("/config")
def update_config(data: dict):
    if not CONFIG_PATH.exists():
        return {"error": "config.json not found"}

    # 주석(_readme 등)·config_version 은 전체 설정 저장(고급 JSON 편집) 시 함께 넘어올 수 있으므로
    # 검증 대상에서 제외하고 무시한다.
    data = {k: v for k, v in data.items() if not k.startswith("_") and k != "config_version"}
    allowed = _allowed_sections()
    unknown = [s for s in data if s not in allowed]
    if unknown:
        raise HTTPException(status_code=422, detail=f"허용되지 않는 섹션: {unknown}")

    try:
        from meeting_minutes_app.common import config_schema
    except Exception:
        config_schema = None

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    sensitive = set(_sensitive_paths())

    def _set(sec: str, key: str, val):
        """섹션/키에 값 기록. 최상위 스칼라(output_dir 등)는 key='' 로 처리."""
        if key:
            if not isinstance(cfg.get(sec), dict):
                cfg[sec] = {}
            cfg[sec][key] = val
        else:
            cfg[sec] = val

    for section, values in data.items():
        # output_dir은 config.json에서 유일하게 중첩 dict가 아닌 스칼라(문자열)
        # 최상위 키라서, 나머지 섹션과 같은 "dict여야 함" 규칙을 적용할 수 없다.
        if section == "output_dir":
            if not isinstance(values, str):
                raise HTTPException(status_code=422, detail="output_dir 값은 문자열이어야 합니다.")
            cfg["output_dir"] = values
            continue
        if not isinstance(values, dict):
            raise HTTPException(status_code=422, detail=f"섹션 '{section}'의 값은 dict여야 합니다.")
        if section not in cfg:
            cfg[section] = {}
        for k, v in values.items():
            # 마스킹된 비밀값(GET에서 가려져 온 값)이 되돌아오면 실제 값을 덮지 않는다.
            # 마스크 형식은 'xxxxxxxx...yyyy' 또는 '***' — '...' 가 중간에 있으므로
            # endswith 가 아니라 포함 여부로 판별하되, 비밀 필드에만 적용한다.
            if f"{section}.{k}" in sensitive and isinstance(v, str) and ("***" in v or "..." in v):
                continue
            # 스키마 필드가 있으면 타입 검증/변환(안전망). 없는 키는 그대로 통과.
            field = config_schema.field_for(section, k) if config_schema else None
            if field is not None:
                try:
                    v = _coerce_value(field, v)
                except ValueError as e:
                    raise HTTPException(status_code=422, detail=str(e))
            # k 에 점이 있으면 중첩 경로(예: notify."slack.webhook_url" → notify.slack.webhook_url)
            _dset(cfg[section], k, v)
            # 서버측 mirror: obsidian.vault_path → indexing.vault_path 등 동시 반영.
            for mt in (field.get("mirror") if field else None) or []:
                if isinstance(mt, (list, tuple)) and len(mt) == 2:
                    _set(mt[0], mt[1], v)

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

    try:
        from meeting_minutes_app.common import config_loader
        config_loader.reload()
    except Exception:
        pass

    return {"success": True}


def _conn_fail_message(e: Exception) -> str:
    """연결 테스트 실패 메시지 — SSL 검증 실패면 비개발자용 해결 안내를 덧붙인다."""
    msg = str(e)
    if "CERTIFICATE_VERIFY_FAILED" in msg or "certificate verify failed" in msg.lower():
        return ("연결 실패: 회사망 SSL 검사로 인증서 확인이 실패했습니다. "
                "PC에 회사 인증서가 설치돼 있으면 앱을 재시작해 보세요. "
                "그래도 안 되면 [설정] → API 키 → 'SSL 인증서 검증'을 끄고 다시 시도하세요. "
                f"(상세: {msg[:120]})")
    return f"연결 실패: {msg}"


@router.post("/config/test/openai")
def test_openai():
    """저장된 OpenAI 키의 유효성을 가볍게 확인(모델 목록 조회). 키는 응답에 포함하지 않음."""
    try:
        from meeting_minutes_app.common import config_loader
        key = config_loader.get_api_key("api.openai_api_key", "OPENAI_API_KEY")
        verify = config_loader.get("ssl.verify", True)
    except Exception as e:
        return {"ok": False, "message": f"설정 로드 실패: {e}"}

    if not key:
        return {"ok": False, "message": "OpenAI API 키가 설정되지 않았습니다."}

    try:
        import httpx
        resp = httpx.get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=10.0,
            verify=bool(verify),
        )
        if resp.status_code == 200:
            return {"ok": True, "message": "OpenAI 연결 성공 — 키가 유효합니다."}
        if resp.status_code == 401:
            return {"ok": False, "message": "API 키가 유효하지 않습니다 (401 인증 실패)."}
        return {"ok": False, "message": f"OpenAI 응답 오류 ({resp.status_code})."}
    except Exception as e:
        return {"ok": False, "message": _conn_fail_message(e)}


@router.post("/config/test/anthropic")
def test_anthropic():
    """저장된 Anthropic(Claude) 키의 유효성을 가볍게 확인. 키는 응답에 포함하지 않음."""
    try:
        from meeting_minutes_app.common import config_loader
        key = config_loader.get_api_key("api.anthropic_api_key", "ANTHROPIC_API_KEY")
        verify = config_loader.get("ssl.verify", True)
    except Exception as e:
        return {"ok": False, "message": f"설정 로드 실패: {e}"}

    if not key:
        return {"ok": False, "message": "Anthropic API 키가 설정되지 않았습니다."}

    try:
        import httpx
        # /v1/models 는 x-api-key + anthropic-version 헤더만으로 조회 가능(과금 없음)
        resp = httpx.get(
            "https://api.anthropic.com/v1/models",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
            timeout=10.0,
            verify=bool(verify),
        )
        if resp.status_code == 200:
            return {"ok": True, "message": "Anthropic 연결 성공 — 키가 유효합니다."}
        if resp.status_code == 401:
            return {"ok": False, "message": "API 키가 유효하지 않습니다 (401 인증 실패)."}
        return {"ok": False, "message": f"Anthropic 응답 오류 ({resp.status_code})."}
    except Exception as e:
        return {"ok": False, "message": _conn_fail_message(e)}


@router.post("/config/test/email")
def test_email():
    """저장된 이메일 설정으로 SMTP 로그인 후 테스트 메일을 1통 보낸다.

    notifier.Notifier.add_email() 을 재사용해 SMTP 호스트 자동감지(gmail/naver/outlook)를
    실제 발송과 동일하게 적용한다. 비밀번호는 응답에 포함하지 않는다.
    """
    try:
        from meeting_minutes_app.common.notifier import Notifier
    except Exception as e:
        return {"ok": False, "message": f"메일 모듈 로드 실패: {e}"}

    try:
        n = Notifier()
        n.add_email()  # config.json/환경변수에서 sender·password·host·port 로드 + 자동감지
        ch = n._channels[0] if n._channels else {}
    except Exception as e:
        return {"ok": False, "message": f"메일 설정 로드 실패: {e}"}

    sender = ch.get("sender", "")
    password = ch.get("password", "")
    host = ch.get("smtp_host", "")
    port = int(ch.get("smtp_port", 0) or 0)
    recipients = ch.get("recipients") or ([sender] if sender else [])

    if not sender:
        return {"ok": False, "message": "보내는 메일 주소가 설정되지 않았습니다."}
    if not password:
        return {"ok": False, "message": "메일 앱 비밀번호가 설정되지 않았습니다. (로그인 비밀번호가 아니라 '앱 비밀번호'여야 합니다.)"}
    if not recipients:
        return {"ok": False, "message": "받는 메일 주소가 없습니다."}

    import smtplib
    from email.mime.text import MIMEText

    msg = MIMEText("Meeting Minutes 메일 연결 테스트입니다. 이 메일이 보이면 설정이 정상입니다.", "plain", "utf-8")
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = "[회의록] 메일 연결 테스트"

    try:
        with smtplib.SMTP(host, port, timeout=20) as server:
            server.ehlo()
            server.starttls()
            server.login(sender, password)
            server.sendmail(sender, recipients, msg.as_string())
        return {"ok": True, "message": f"테스트 메일을 보냈습니다 → {', '.join(recipients)} (받은 편지함을 확인하세요. 서버 {host}:{port})"}
    except smtplib.SMTPAuthenticationError:
        return {"ok": False, "message": "로그인 실패 — 보내는 주소나 '앱 비밀번호'가 올바른지 확인하세요. (평소 로그인 비밀번호가 아니라 메일 보안설정에서 발급한 앱 비밀번호여야 합니다.)"}
    except (OSError, smtplib.SMTPException) as e:
        return {"ok": False, "message": f"SMTP 연결/발송 실패 ({host}:{port}): {e}"}
    except Exception as e:
        return {"ok": False, "message": f"메일 테스트 실패: {e}"}


def _webhook_test(kind: str, cfg_key: str, env_var: str, payload: dict):
    """Slack/Teams Incoming Webhook 으로 테스트 메시지 발송."""
    try:
        from meeting_minutes_app.common import config_loader
        import os as _os
        url = _os.environ.get(env_var, "") or config_loader.get(cfg_key, "")
    except Exception as e:
        return {"ok": False, "message": f"설정 로드 실패: {e}"}

    if not url:
        return {"ok": False, "message": f"{kind} Webhook URL이 설정되지 않았습니다."}
    try:
        import httpx
        resp = httpx.post(url, json=payload, timeout=10.0)
        if 200 <= resp.status_code < 300:
            return {"ok": True, "message": f"{kind} 테스트 메시지를 보냈습니다. 채널을 확인하세요."}
        return {"ok": False, "message": f"{kind} 응답 오류 ({resp.status_code}). Webhook URL을 확인하세요."}
    except Exception as e:
        return {"ok": False, "message": f"{kind} 연결 실패: {e}"}


@router.post("/config/test/slack")
def test_slack():
    return _webhook_test(
        "Slack", "notify.slack.webhook_url", "SLACK_WEBHOOK_URL",
        {"text": "✅ Meeting Minutes 연결 테스트 메시지입니다. 이 메시지가 보이면 설정이 정상입니다."},
    )


@router.post("/config/test/teams")
def test_teams():
    return _webhook_test(
        "Teams", "notify.teams.webhook_url", "TEAMS_WEBHOOK_URL",
        {"text": "✅ Meeting Minutes 연결 테스트 메시지입니다. 이 메시지가 보이면 설정이 정상입니다."},
    )


@router.post("/config/test/obsidian")
def test_obsidian():
    """Obsidian 볼트 경로 존재/디렉터리 여부 확인."""
    try:
        from meeting_minutes_app.common import config_loader
        vault = (config_loader.get("obsidian.vault_path", "")
                 or config_loader.get("indexing.vault_path", ""))
    except Exception as e:
        return {"ok": False, "message": f"설정 로드 실패: {e}"}

    if not vault:
        return {"ok": False, "message": "Obsidian 볼트 경로가 설정되지 않았습니다."}
    p = Path(vault)
    if not p.exists():
        return {"ok": False, "message": f"경로가 존재하지 않습니다: {vault}"}
    if not p.is_dir():
        return {"ok": False, "message": f"폴더가 아닙니다: {vault}"}
    return {"ok": True, "message": f"볼트 폴더 확인됨: {vault}"}


# 폴더 선택 다이얼로그를 띄우는 PowerShell 스크립트.
# tkinter 는 exe 빌드에서 제외되므로 Windows 기본 .NET FolderBrowserDialog 를 사용한다.
# 선택 경로만 stdout 으로 출력, 취소 시 아무것도 출력하지 않는다.
_PICK_FOLDER_PS = (
    "Add-Type -AssemblyName System.Windows.Forms | Out-Null; "
    "$f = New-Object System.Windows.Forms.FolderBrowserDialog; "
    "$f.Description = '폴더를 선택하세요'; "
    "$f.ShowNewFolderButton = $true; "
    "if ($env:MM_PICK_INIT) { $f.SelectedPath = $env:MM_PICK_INIT }; "
    "$top = New-Object System.Windows.Forms.Form; "
    "$top.TopMost = $true; "
    "if ($f.ShowDialog($top) -eq [System.Windows.Forms.DialogResult]::OK) "
    "{ [Console]::Out.Write($f.SelectedPath) }"
)


def _is_loopback(host: str | None) -> bool:
    return host in ("127.0.0.1", "::1", "localhost", "testclient")


@router.post("/system/pick-folder")
async def pick_folder(request: Request, body: dict | None = None):
    """네이티브 폴더 선택 다이얼로그를 서버(=이 PC)에서 띄우고 선택 경로를 반환.

    exe 는 로컬(localhost)에서 브라우저로 접속하므로 다이얼로그가 사용자 화면에 뜬다.
    원격 접속 시 서버 머신에 창이 떠 버리는 것을 막기 위해 loopback 접속만 허용한다.
    실패/취소/비Windows 는 {ok:false} 로 안전하게 폴백(프론트는 텍스트 입력을 유지).
    """
    host = request.client.host if request.client else None
    if not _is_loopback(host):
        return {"ok": False, "message": "폴더 선택은 이 PC(로컬)에서만 사용할 수 있습니다. 경로를 직접 입력하세요."}
    if sys.platform != "win32":
        return {"ok": False, "message": "이 환경에서는 폴더 선택 창을 열 수 없습니다. 경로를 직접 입력하세요."}

    import os
    env = dict(os.environ)
    initial = (body or {}).get("initial") if isinstance(body, dict) else None
    if isinstance(initial, str) and initial.strip():
        env["MM_PICK_INIT"] = initial.strip()

    import asyncio

    def _run_dialog():
        # 타임아웃 120초로 단축 — 사용자가 창을 방치해도 5분이 아닌 2분 뒤 해제.
        return subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-STA", "-Command", _PICK_FOLDER_PS],
            capture_output=True, text=True, timeout=120, env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    try:
        # 동기 subprocess를 스레드로 넘겨 이벤트 루프(다른 API 요청)를 막지 않는다.
        proc = await asyncio.to_thread(_run_dialog)
    except subprocess.TimeoutExpired:
        return {"ok": False, "message": "폴더 선택 시간이 초과되었습니다(2분). 다시 시도하거나 경로를 직접 입력하세요."}
    except Exception as e:
        return {"ok": False, "message": f"폴더 선택 창을 열지 못했습니다: {e}"}

    path = (proc.stdout or "").strip()
    if not path:
        return {"ok": False, "message": "선택이 취소되었습니다.", "cancelled": True}
    return {"ok": True, "path": path}
