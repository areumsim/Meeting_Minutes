"""
config_schema.py — 설정 스키마 단일 소스
============================================================
웹 Settings 화면의 자동 렌더링과 config_loader.migrate()의
누락 키 기본값 주입에 함께 쓰이는 선언적 스키마.

새 설정을 추가하려면:
  1) config.example.json 에 기본값을 추가한다(마이그레이션·시드용 전체 기본값 소스).
  2) 여기 해당 그룹의 fields 에 한 줄을 추가한다(웹 Settings 노출용 UI 메타).

각 field 의 의미:
  section    config.json 최상위 섹션 이름 (예: "api", "wiki")
  key        섹션 내부 키 (예: "openai_api_key")
  label      화면에 표시할 한글 라벨
  type       위젯 종류: text | password | bool | select | number
  default    기본값 (마이그레이션 belt-and-suspenders 및 UI 초기값)
  desc       (선택) 도움말 문구
  options    (선택) type=select 일 때 선택지 리스트
  sensitive  (선택) True 면 GET /api/config 에서 마스킹되는 비밀값(키/비밀번호)
  mirror     (선택) 저장 시 같은 값을 함께 반영할 [[section, key], ...]
  placeholder(선택) 입력창 placeholder
============================================================
"""

from typing import Any, Dict, Iterator, List

# config.json 스키마 버전. 구조/기본키가 바뀌면 올린다. migrate() 가 이 값으로 승격한다.
CONFIG_VERSION = 1


# ── 그룹/필드 정의 ─────────────────────────────────────────
SCHEMA: List[Dict[str, Any]] = [
    {
        "id": "api",
        "label": "API 키",
        "desc": "키는 이 PC의 config.json 에만 저장되며 화면에는 마스킹되어 표시됩니다.",
        "fields": [
            {
                "section": "api", "key": "openai_api_key",
                "label": "OpenAI API 키 (필수)", "type": "password", "sensitive": True,
                "default": "", "placeholder": "sk-proj-...",
                "desc": "음성 인식(STT)과 회의록 생성에 사용됩니다.",
            },
            {
                "section": "api", "key": "anthropic_api_key",
                "label": "Anthropic API 키 (선택)", "type": "password", "sensitive": True,
                "default": "", "placeholder": "sk-ant-...",
                "desc": "Claude로 회의록/요약을 생성할 때 필요합니다.",
            },
        ],
    },
    {
        "id": "obsidian",
        "label": "Obsidian 연동",
        "desc": "회의록을 Obsidian 볼트에 저장합니다. 사용하지 않으면 파일(output 폴더)로만 저장됩니다.",
        "fields": [
            {
                "section": "obsidian", "key": "enabled",
                "label": "Obsidian 저장 사용", "type": "bool", "default": False,
            },
            {
                "section": "obsidian", "key": "vault_path",
                "label": "Obsidian 볼트 폴더", "type": "text", "default": "",
                "placeholder": r"D:\Obsidian\MyVault",
                "desc": "회의록을 저장할 볼트 루트 폴더. 검색 인덱스 경로에도 함께 반영됩니다.",
                "mirror": [["indexing", "vault_path"]],
            },
            {
                "section": "obsidian", "key": "api_url",
                "label": "Local REST API 주소", "type": "text",
                "default": "https://127.0.0.1:27124",
            },
            {
                "section": "obsidian", "key": "api_key",
                "label": "Local REST API 키", "type": "password", "sensitive": True,
                "default": "", "desc": "Obsidian → 설정 → Local REST API 에서 발급한 키.",
            },
            {
                "section": "obsidian", "key": "meetings_path",
                "label": "회의록 저장 하위경로 (선택)", "type": "text", "default": "",
                "desc": "비우면 00_Meetings 아래에 저장됩니다.",
            },
            {
                "section": "obsidian", "key": "transcript_mode",
                "label": "전체 전사 저장 방식", "type": "select", "default": "separate",
                "options": ["separate", "append", "off"],
            },
            {
                "section": "obsidian", "key": "auto_route_enabled",
                "label": "회의 자동 분류 저장", "type": "bool", "default": False,
                "desc": "제목/주제로 저장 폴더를 자동 결정합니다.",
            },
        ],
    },
    {
        "id": "models",
        "label": "모델",
        "desc": "회의록/요약 생성에 사용할 LLM과 음성 인식 모델을 선택합니다.",
        "fields": [
            {
                "section": "models", "key": "llm",
                "label": "회의록 생성 LLM", "type": "select", "default": "claude",
                "options": ["gpt", "claude"],
            },
            {
                "section": "models", "key": "stt",
                "label": "음성 인식(STT) 모델", "type": "select",
                "default": "gpt-4o-mini-transcribe-2025-12-15",
                "options": [
                    "gpt-4o-mini-transcribe",
                    "gpt-4o-mini-transcribe-2025-12-15",
                    "gpt-4o-transcribe",
                    "gpt-4o-transcribe-diarize",
                    "whisper-1",
                ],
            },
            {"section": "models", "key": "gpt_model", "label": "GPT 모델", "type": "text", "default": "gpt-4o-mini"},
            {"section": "models", "key": "claude_model", "label": "Claude 모델", "type": "text", "default": "claude-opus-4-6"},
            {"section": "models", "key": "minutes_model", "label": "회의록 생성 모델(GPT)", "type": "text", "default": "gpt-4o"},
            {"section": "models", "key": "summary_model", "label": "요약 생성 모델(GPT)", "type": "text", "default": "gpt-4o"},
            {"section": "models", "key": "translate_model", "label": "번역 모델", "type": "text", "default": "gpt-4o-mini"},
        ],
    },
    {
        "id": "realtime",
        "label": "실시간 녹취",
        "desc": "실시간 녹음/전사 기본값입니다.",
        "fields": [
            {
                "section": "realtime", "key": "mode",
                "label": "전사 방식", "type": "select", "default": "http",
                "options": ["http", "ws", "auto"],
                "desc": "http=청크 방식, ws=WebSocket 스트리밍(지연 낮음·비용 높음), auto=자동.",
            },
            {
                "section": "realtime", "key": "language",
                "label": "기본 언어", "type": "select", "default": "en",
                "options": ["en", "ko", "auto"],
            },
            {"section": "realtime", "key": "translate", "label": "번역 사용", "type": "bool", "default": False},
            {
                "section": "realtime", "key": "audio_backup",
                "label": "오디오 백업(PCM)", "type": "bool", "default": True,
                "desc": "크래시 복구용. 약 115MB/시간.",
            },
        ],
    },
    {
        "id": "features",
        "label": "기능 토글",
        "desc": "부가 기능을 켜고 끕니다. 새 기능은 기본 꺼짐으로 추가됩니다.",
        "fields": [
            {"section": "wiki", "key": "vault_enrich", "label": "회의록 볼트 연관 노트 링크", "type": "bool", "default": True},
            {"section": "wiki", "key": "claim_verify", "label": "주장 사실 검증", "type": "bool", "default": True},
            {"section": "wiki", "key": "realtime_vault_search", "label": "실시간 볼트 검색", "type": "bool", "default": False},
            {
                "section": "wiki", "key": "online_search_enabled",
                "label": "온라인 검색 보완", "type": "bool", "default": False,
                "desc": "볼트에 없는 내용을 웹 검색으로 보완합니다(비용 발생).",
            },
            {"section": "wiki_knowledge", "key": "graph_enabled", "label": "지식 그래프", "type": "bool", "default": True},
            {"section": "wiki_knowledge", "key": "embedding_enabled", "label": "임베딩 하이브리드 검색", "type": "bool", "default": False},
            {"section": "wiki_knowledge", "key": "action_registry_enabled", "label": "액션 레지스트리", "type": "bool", "default": True},
            {"section": "wiki_knowledge", "key": "decision_registry_enabled", "label": "결정 레지스트리", "type": "bool", "default": True},
            {"section": "vault_watcher", "key": "enabled", "label": "폴더 자동 감시 처리", "type": "bool", "default": False},
            {"section": "supermemory", "key": "enabled", "label": "Supermemory 연동", "type": "bool", "default": False},
        ],
    },
    {
        "id": "supermemory",
        "label": "Supermemory (선택)",
        "desc": "Supermemory 연동을 켠 경우에만 필요합니다.",
        "fields": [
            {"section": "supermemory", "key": "api_key", "label": "Supermemory API 키", "type": "password", "sensitive": True, "default": ""},
            {"section": "supermemory", "key": "base_url", "label": "Supermemory 주소", "type": "text", "default": "https://api.supermemory.ai"},
        ],
    },
    {
        "id": "notify",
        "label": "알림 (선택)",
        "desc": "처리 완료 시 이메일/메신저로 알림을 보냅니다.",
        "fields": [
            {
                "section": "notify", "key": "on_finish",
                "label": "완료 알림 채널", "type": "select", "default": "email",
                "options": ["email", "slack", "teams", "none"],
                "desc": "none = 자동 알림 끔.",
            },
            {"section": "email", "key": "sender", "label": "보내는 메일 주소", "type": "text", "default": ""},
            {"section": "email", "key": "password", "label": "메일 앱 비밀번호", "type": "password", "sensitive": True, "default": ""},
            {"section": "email", "key": "recipient", "label": "받는 메일 주소", "type": "text", "default": ""},
        ],
    },
]


# ── 헬퍼 ───────────────────────────────────────────────────
def get_schema() -> List[Dict[str, Any]]:
    """웹 Settings 자동 렌더링용 스키마(그룹 리스트) 반환."""
    return SCHEMA


def iter_fields() -> Iterator[Dict[str, Any]]:
    """모든 그룹의 필드를 평탄하게 순회."""
    for group in SCHEMA:
        for field in group["fields"]:
            yield field


def sensitive_paths() -> List[str]:
    """민감(비밀) 값의 'section.key' 경로 목록 — 마스킹 대상 판별에 사용."""
    return [f"{f['section']}.{f['key']}" for f in iter_fields() if f.get("sensitive")]
