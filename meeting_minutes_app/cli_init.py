"""meeting-minutes init — 새 팀/새 설치를 위한 최초 설정 마법사.

config.example.json -> config.json 복사 후 핵심 항목만 대화형으로 입력받고,
Obsidian/LLM 연결을 확인한다. 기존 config_loader.py를 그대로 재사용하며,
새로운 설정 시스템은 만들지 않는다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict

for _s in (sys.stdout, sys.stderr):
    if getattr(_s, "encoding", None) and _s.encoding.lower() in ("cp949", "euc-kr", "ansi"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _project_root() -> Path:
    from meeting_minutes_app.common import config_loader
    return config_loader._PROJECT_ROOT


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        v = input(f"{prompt}{suffix} >> ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        raise SystemExit(130)
    return v or default


def _set_nested(cfg: Dict[str, Any], dotted_key: str, value: Any) -> None:
    parts = dotted_key.split(".")
    node = cfg
    for p in parts[:-1]:
        node = node.setdefault(p, {})
    node[parts[-1]] = value


def _get_nested(cfg: Dict[str, Any], dotted_key: str, default: Any = "") -> Any:
    node: Any = cfg
    for p in dotted_key.split("."):
        if not isinstance(node, dict):
            return default
        node = node.get(p)
        if node is None:
            return default
    return node


def _check_obsidian(api_url: str, api_key: str) -> None:
    if not api_url or not api_key:
        print("  [skip] Obsidian api_url/api_key 미입력 — 연결 확인 생략 (Obsidian 기록 없이도 회의록 생성은 동작합니다)")
        return
    try:
        from meeting_minutes_app.wiki_core.obsidian import ObsidianClient
        client = ObsidianClient(api_url=api_url, api_key=api_key)
        if client.ping():
            print("  [OK] Obsidian Local REST API 연결 확인됨")
        else:
            print("  [WARN] Obsidian 응답 없음 — Obsidian이 켜져 있고 'Local REST API (with MCP)' 플러그인이")
            print("         활성화되어 있는지, API Key가 맞는지 확인하세요. (지금 넘어가도 나중에 config.json에서")
            print("         obsidian.enabled/api_key를 다시 설정할 수 있습니다.)")
    except Exception as e:
        print(f"  [WARN] Obsidian 연결 확인 중 오류 (무시하고 진행): {e}")


def _check_llm_key(openai_key: str, anthropic_key: str) -> None:
    if openai_key:
        try:
            from openai import OpenAI
            OpenAI(api_key=openai_key).models.list()
            print("  [OK] OpenAI API 키 확인됨")
        except Exception as e:
            print(f"  [WARN] OpenAI API 키 확인 실패 (무시하고 진행): {e}")
    if anthropic_key:
        try:
            import anthropic as _ant
            _ant.Anthropic(api_key=anthropic_key).models.list()
            print("  [OK] Anthropic API 키 확인됨")
        except Exception as e:
            print(f"  [WARN] Anthropic API 키 확인 실패 (무시하고 진행): {e}")
    if not openai_key and not anthropic_key:
        print("  [WARN] OpenAI/Anthropic 키가 둘 다 비어 있습니다 — STT/회의록 생성이 동작하지 않습니다.")


def run_init(argv: list[str]) -> int:
    force = "--force" in argv

    root = _project_root()
    example_path = root / "config.example.json"
    config_path = root / "config.json"

    if not example_path.exists():
        print(f"[init] config.example.json을 찾을 수 없습니다: {example_path}")
        return 1

    if config_path.exists() and not force:
        print(f"[init] config.json이 이미 존재합니다: {config_path}")
        print("       기존 설정을 덮어쓰지 않습니다. 직접 편집하거나, 재작성하려면")
        print("       'meeting-minutes init --force'를 사용하세요 (기존 값은 모두 초기화됩니다).")
        return 1

    with open(example_path, encoding="utf-8") as f:
        cfg: Dict[str, Any] = json.load(f)

    print("=" * 60)
    print("  Meeting Minutes — 최초 설정")
    print("=" * 60)
    print()
    print("Enter만 누르면 기본값(비활성/빈 값)을 유지합니다. 나중에 config.json을")
    print("직접 편집해도 됩니다 — 여기서는 실행에 꼭 필요한 항목만 물어봅니다.")
    print()

    print("-- Obsidian (선택 — 비워두면 회의록은 로컬 output/ 폴더에만 저장됩니다) --")
    vault_path = _ask("Obsidian vault 경로 (예: D:\\Claude\\MyTeam)", _get_nested(cfg, "obsidian.vault_path"))
    api_key = _ask("Obsidian Local REST API Key (설정 > Local REST API에서 복사)", _get_nested(cfg, "obsidian.api_key"))
    api_url = _get_nested(cfg, "obsidian.api_url", "https://127.0.0.1:27124")
    project = _ask("프로젝트/팀 이름 (회의록 폴더 구분용, 선택)", _get_nested(cfg, "obsidian.project"))

    print()
    print("-- LLM API 키 (최소 하나 필요) --")
    openai_key = _ask("OpenAI API 키 (sk-proj-...)", _get_nested(cfg, "api.openai_api_key"))
    anthropic_key = _ask("Anthropic API 키 (sk-ant-..., 선택)", _get_nested(cfg, "api.anthropic_api_key"))

    _set_nested(cfg, "obsidian.vault_path", vault_path)
    _set_nested(cfg, "obsidian.api_key", api_key)
    _set_nested(cfg, "obsidian.project", project)
    _set_nested(cfg, "obsidian.enabled", bool(vault_path and api_key))
    _set_nested(cfg, "indexing.vault_path", vault_path)
    _set_nested(cfg, "api.openai_api_key", openai_key)
    _set_nested(cfg, "api.anthropic_api_key", anthropic_key)

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print()
    print(f"[init] config.json 저장됨: {config_path}")

    print()
    print("-- 연결 확인 --")
    _check_obsidian(api_url, api_key)
    _check_llm_key(openai_key, anthropic_key)

    print()
    print("=" * 60)
    print("  완료. `meeting-minutes batch <파일>` 로 첫 회의록을 만들어 보세요.")
    print(f"  data/, config.json은 이 설치({root})에만 속하며 다른 팀/설치와")
    print("  공유되지 않습니다 (git에도 올라가지 않습니다).")
    print("=" * 60)
    return 0


def run_prepare_local_stt(argv: list[str]) -> int:
    """로컬 STT 최종 백업(faster-whisper) 가중치를 미리 내려받는다.

    전사 경로는 절대 다운로드하지 않으므로(`stt._get_local_model` 은
    `local_files_only=True`), 장애 전에 한 번 준비해 두는 것이 이 기능의 전제다.
    준비되지 않은 로컬 단계는 폴백 체인에서 조용히 제외된다.

    웹 [설정]의 [로컬 백업 모델 준비] 버튼과 같은 일을 하지만, 그 버튼은 패키지
    모드에서만 보인다 — 소스 실행(개발·검증) 환경에서는 이 명령이 유일한 준비 수단이다.

    사용:
      meeting-minutes prepare-local-stt            # 설정된 모델 준비
      meeting-minutes prepare-local-stt --status   # 준비 상태만 확인
      meeting-minutes prepare-local-stt --model tiny
    """
    from meeting_minutes_app.meeting_pipeline import stt

    # 모델명은 stt 모듈의 전역을 쓴다 — config reload 훅이 갱신하는 단일 소스이고,
    # 폴백 체인이 실제로 쓰는 값과 같아야 상태 표시가 거짓말을 하지 않는다.
    model = stt.LOCAL_STT_MODEL
    if "--model" in argv:
        idx = argv.index("--model")
        if idx + 1 < len(argv):
            model = argv[idx + 1]

    st = stt.local_model_status(model)
    if not st["lib_available"]:
        print(f"[prepare-local-stt] {stt.LOCAL_LIB_MISSING_MSG}")
        return 1

    if "--status" in argv:
        mark = "준비됨" if st["installed"] else "미준비"
        print(f"[prepare-local-stt] 모델 '{model}': {mark}")
        print(f"                    저장 위치: {st['path']}")
        if st["installed"]:
            print(f"                    크기: {st['size_mb']}MB")
        else:
            print("                    → 준비하려면 --status 없이 다시 실행하세요.")
        return 0

    if st["installed"]:
        print(f"[prepare-local-stt] 모델 '{model}' 은 이미 준비됨 "
              f"({st['size_mb']}MB) — 받을 것이 없습니다.")
        return 0

    print(f"[prepare-local-stt] 모델 '{model}' 다운로드 시작 "
          f"(수십~수백 MB, 1~3분 걸릴 수 있습니다)")
    try:
        done = stt.prepare_local_model(model)
    except Exception as e:
        print(f"[prepare-local-stt] 준비 실패: {e}")
        return 1
    print(f"[prepare-local-stt] 준비 완료 — {model} "
          f"({done['size_mb']}MB, {done['elapsed_sec']}초)")
    print(f"                    저장 위치: {done['path']}")
    print("                    config.json 의 stt.local_fallback 이 true 여야 "
          "폴백 체인에 들어갑니다.")
    return 0


def run_mcp_token(argv: list[str]) -> int:
    """새 사용자용 Wiki Graph MCP Bearer 토큰을 발급해 config.json의
    mcp.allowed_tokens에 추가한다. 토큰은 이 실행 화면에만 한 번 출력된다(재조회 불가 —
    API 키처럼 다뤄야 한다). --name으로 누구 것인지 표시용 이름을 붙일 수 있다."""
    import secrets

    name = "user"
    if "--name" in argv:
        idx = argv.index("--name")
        if idx + 1 < len(argv):
            name = argv[idx + 1]

    root = _project_root()
    config_path = root / "config.json"
    if not config_path.exists():
        print(f"[mcp-token] config.json이 없습니다: {config_path}")
        print("            먼저 'meeting-minutes init'을 실행하세요.")
        return 1

    with open(config_path, encoding="utf-8") as f:
        cfg: Dict[str, Any] = json.load(f)

    token = secrets.token_urlsafe(32)
    tokens = _get_nested(cfg, "mcp.allowed_tokens", [])
    if not isinstance(tokens, list):
        tokens = []
    tokens.append({"token": token, "name": name})
    _set_nested(cfg, "mcp.allowed_tokens", tokens)

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print("=" * 60)
    print(f"  MCP 토큰 발급됨 (name={name})")
    print("=" * 60)
    print()
    print(f"  {token}")
    print()
    print("  이 토큰은 다시 조회할 수 없습니다 — API 키처럼 안전하게 보관하세요.")
    print("  Claude Desktop → Customize → Connectors → Add custom connector에서")
    print("  <서버 URL>/mcp 를 등록할 때 Authorization 헤더(Bearer)로 사용합니다.")
    print("  config.json의 mcp.allowed_tokens에 저장됐습니다 (git에 올라가지 않음).")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(run_init(sys.argv[1:]))
