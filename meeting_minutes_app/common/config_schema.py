"""
config_schema.py — 설정 스키마 단일 소스
============================================================
웹 Settings 화면의 자동 렌더링과 config_loader.migrate()의
누락 키 기본값 주입에 함께 쓰이는 선언적 스키마.

여기 정의된 필드는 웹 [설정]에 "친절한 폼"으로 노출된다. 여기에 없는
항목(맵/리스트/중첩 설정 등)도 [설정] → "고급: 전체 설정(JSON)"에서 직접
편집할 수 있으므로 config.json의 어떤 값도 웹에서 수정 가능하다.

새 설정을 추가하려면:
  1) config.example.json 에 기본값을 추가(마이그레이션·시드용 전체 기본값 소스).
  2) 여기 해당 그룹의 fields 에 한 줄 추가(웹 노출용 UI 메타).

field 키: section, key, label, type(text|password|bool|select|number),
  default, desc, options([str|{value,label}]), sensitive, mirror, placeholder
============================================================
"""

from typing import Any, Dict, Iterator, List

CONFIG_VERSION = 1


SCHEMA: List[Dict[str, Any]] = [
    {
        "id": "api",
        "label": "API 키",
        "desc": "키는 이 PC의 config.json 에만 저장되며 화면에는 마스킹되어 표시됩니다.",
        "fields": [
            {"section": "api", "key": "openai_api_key", "label": "OpenAI API 키 (필수)", "type": "password", "sensitive": True, "default": "", "placeholder": "sk-proj-...", "desc": "음성 인식(STT)과 회의록 생성에 사용."},
            {"section": "api", "key": "anthropic_api_key", "label": "Anthropic(Claude) API 키 (선택)", "type": "password", "sensitive": True, "default": "", "placeholder": "sk-ant-...", "desc": "회의록 생성 AI를 Claude로 쓸 때 필요."},
            {"section": "ssl", "key": "verify", "label": "SSL 인증서 검증", "type": "bool", "default": False, "desc": "회사/학교망에서 SSL 오류가 나면 끄세요."},
        ],
    },
    {
        "id": "models",
        "label": "모델",
        "desc": "회의록/요약 생성 AI와 음성 인식 모델. 비용은 100만 토큰당 대략치(입력/출력)이며 변동될 수 있습니다.",
        "fields": [
            {"section": "models", "key": "llm", "label": "회의록 생성 AI", "type": "select", "default": "claude", "options": [{"value": "gpt", "label": "GPT (OpenAI)"}, {"value": "claude", "label": "Claude (Anthropic)"}], "desc": "Claude 선택 시 Anthropic 키 필요."},
            {"section": "models", "key": "stt", "label": "음성 인식(STT) 모델", "type": "select", "default": "gpt-4o-mini-transcribe", "options": [{"value": "gpt-4o-mini-transcribe", "label": "gpt-4o-mini-transcribe — 저렴·빠름 (추천)"}, {"value": "gpt-4o-transcribe", "label": "gpt-4o-transcribe — 고정확·비쌈"}, {"value": "gpt-4o-transcribe-diarize", "label": "gpt-4o-transcribe-diarize — 화자분리"}, {"value": "whisper-1", "label": "whisper-1 — 구형·안정"}]},
            {"section": "models", "key": "claude_model", "label": "Claude 모델", "type": "select", "default": "claude-opus-4-8", "options": [{"value": "claude-opus-4-8", "label": "Opus 4.8 — 최고 성능 (약 $5/$25)"}, {"value": "claude-sonnet-5", "label": "Sonnet 5 — 균형·빠름 (약 $3/$15)"}, {"value": "claude-haiku-4-5", "label": "Haiku 4.5 — 저렴·빠름 (약 $1/$5)"}, {"value": "claude-opus-4-6", "label": "Opus 4.6 — 구버전"}]},
            {"section": "models", "key": "gpt_model", "label": "GPT 모델", "type": "select", "default": "gpt-4o-mini", "options": [{"value": "gpt-4o-mini", "label": "gpt-4o-mini — 저렴·빠름 (추천)"}, {"value": "gpt-4o", "label": "gpt-4o — 고품질·비쌈"}, {"value": "o1", "label": "o1 — 추론(느림·고비용)"}, {"value": "o3-mini", "label": "o3-mini — 추론(경량)"}]},
            {"section": "models", "key": "minutes_model", "label": "회의록 생성 모델(GPT, 선택)", "type": "text", "default": "gpt-4o", "desc": "미설정 시 GPT 모델 사용. 상세 기록이라 고성능 권장."},
            {"section": "models", "key": "summary_model", "label": "요약 생성 모델(GPT, 선택)", "type": "text", "default": "gpt-4o", "desc": "미설정 시 GPT 모델 사용."},
            {"section": "models", "key": "translate_model", "label": "번역 모델", "type": "select", "default": "gpt-4o-mini", "options": [{"value": "gpt-4o-mini", "label": "gpt-4o-mini — 저렴·빠름 (추천)"}, {"value": "gpt-4o", "label": "gpt-4o — 고품질"}]},
        ],
    },
    {
        "id": "storage",
        "label": "저장 위치",
        "desc": "결과물이 저장되는 폴더입니다. output_dir 는 앱 데이터 폴더(MeetingMinutesData) 기준 상대경로이거나 절대경로입니다.",
        "fields": [
            {"section": "output_dir", "key": "", "label": "결과 저장 폴더(output)", "type": "text", "default": "./output", "scalar": True, "desc": "회의록/요약/전사 파일이 저장되는 폴더. 예: ./output 또는 D:\\Minutes\\output"},
            {"section": "analysis", "key": "templates_dir", "label": "프롬프트 템플릿 폴더", "type": "text", "default": "prompts", "desc": "분석 프롬프트(.md) 폴더."},
            {"section": "analysis", "key": "default_type", "label": "기본 문서 유형", "type": "select", "default": "meeting", "options": ["meeting", "seminar", "lecture", "memo"]},
        ],
    },
    {
        "id": "obsidian",
        "label": "Obsidian 연동 (선택)",
        "desc": "볼트 폴더만 지정하면 REST API 없이도 회의록이 그 폴더의 .md로 저장되고 위키 검색에 쓰입니다. Local REST API는 Obsidian 앱에 실시간 반영이 필요할 때만 켜세요.",
        "fields": [
            {"section": "obsidian", "key": "enabled", "label": "Obsidian REST 저장 사용", "type": "bool", "default": False, "desc": "켜면 Local REST API로 Obsidian 앱에 직접 기록. 꺼도 볼트 폴더가 있으면 .md로 저장됨."},
            {"section": "obsidian", "key": "vault_path", "label": "Obsidian 볼트 폴더", "type": "text", "default": "", "placeholder": r"D:\Obsidian\MyVault", "desc": "회의록을 저장할 볼트 루트. 검색 인덱스 경로에도 함께 반영됩니다.", "mirror": [["indexing", "vault_path"]]},
            {"section": "obsidian", "key": "api_url", "label": "Local REST API 주소", "type": "text", "default": "https://127.0.0.1:27124"},
            {"section": "obsidian", "key": "api_key", "label": "Local REST API 키", "type": "password", "sensitive": True, "default": "", "desc": "Obsidian → 설정 → Local REST API 에서 발급."},
            {"section": "obsidian", "key": "verify_ssl", "label": "REST SSL 검증", "type": "bool", "default": False},
            {"section": "obsidian", "key": "exe_path", "label": "Obsidian 실행파일 경로(선택)", "type": "text", "default": "", "placeholder": r"C:\...\Obsidian.exe", "desc": "설정 시 처리 전 Obsidian 자동 실행."},
            {"section": "obsidian", "key": "notes_subdir", "label": "회의록 기본 폴더", "type": "text", "default": "00_Meetings"},
            {"section": "obsidian", "key": "meetings_path", "label": "회의록 저장 경로(선택)", "type": "text", "default": "", "desc": "비우면 회의록 기본 폴더/<프로젝트>에 저장. {year}/{project} 토큰 사용 가능."},
            {"section": "obsidian", "key": "transcripts_path", "label": "전사 저장 경로(선택)", "type": "text", "default": "", "desc": "비우면 회의록과 같은 폴더."},
            {"section": "obsidian", "key": "transcript_mode", "label": "전체 전사 저장 방식", "type": "select", "default": "separate", "options": [{"value": "separate", "label": "separate — 별도 노트"}, {"value": "append", "label": "append — 회의록에 포함"}, {"value": "off", "label": "off — 저장 안 함"}]},
            {"section": "obsidian", "key": "refs_subdir", "label": "참조노트 폴더", "type": "text", "default": "01_References"},
            {"section": "obsidian", "key": "planning_path", "label": "회의 준비 저장 경로", "type": "text", "default": "Planning/Prep Briefs"},
            {"section": "obsidian", "key": "papers_path", "label": "논문 노트 저장 경로(선택)", "type": "text", "default": ""},
            {"section": "obsidian", "key": "project", "label": "프로젝트/도메인(선택)", "type": "text", "default": "", "desc": "저장 폴더 분류에 사용."},
            {"section": "obsidian", "key": "auto_route_enabled", "label": "회의 자동 분류 저장", "type": "bool", "default": False, "desc": "제목/주제로 저장 폴더 자동 결정."},
            {"section": "obsidian", "key": "auto_register_categories", "label": "새 카테고리 자동 등록", "type": "bool", "default": True},
        ],
    },
    {
        "id": "realtime",
        "label": "실시간 녹취",
        "desc": "실시간 녹음/전사 기본값입니다.",
        "fields": [
            {"section": "realtime", "key": "mode", "label": "전사 방식", "type": "select", "default": "http", "options": [{"value": "http", "label": "http — 청크(안정)"}, {"value": "ws", "label": "ws — WebSocket 스트리밍(지연↓·비용↑)"}, {"value": "auto", "label": "auto — 자동"}]},
            {"section": "realtime", "key": "language", "label": "기본 언어", "type": "select", "default": "en", "options": ["en", "ko", "auto"]},
            {"section": "realtime", "key": "type", "label": "기본 문서 유형", "type": "select", "default": "meeting", "options": ["meeting", "seminar", "lecture"]},
            {"section": "realtime", "key": "translate", "label": "번역 사용", "type": "bool", "default": False},
            {"section": "realtime", "key": "audio_backup", "label": "오디오 백업(PCM)", "type": "bool", "default": True, "desc": "크래시 복구용. 약 115MB/시간."},
            {"section": "realtime", "key": "chunk_duration", "label": "청크 길이(초)", "type": "number", "default": 3.0},
            {"section": "realtime", "key": "ws_vad_type", "label": "WS VAD 방식", "type": "select", "default": "server_vad", "options": ["server_vad", "semantic_vad"]},
            {"section": "realtime", "key": "ws_vad_eagerness", "label": "WS 발화종료 민감도", "type": "select", "default": "medium", "options": ["low", "medium", "high", "auto"]},
            {"section": "realtime", "key": "ws_noise_reduction", "label": "WS 노이즈 리덕션", "type": "select", "default": "near_field", "options": ["near_field", "far_field"]},
            {"section": "realtime", "key": "email_on_finish", "label": "종료 후 이메일 자동발송", "type": "bool", "default": False},
        ],
    },
    {
        "id": "features",
        "label": "기능 토글",
        "desc": "부가 기능을 켜고 끕니다.",
        "fields": [
            {"section": "wiki", "key": "vault_enrich", "label": "회의록 볼트 연관 노트 링크", "type": "bool", "default": True},
            {"section": "wiki", "key": "claim_verify", "label": "주장 사실 검증", "type": "bool", "default": True},
            {"section": "wiki", "key": "realtime_vault_search", "label": "실시간 볼트 검색", "type": "bool", "default": False},
            {"section": "wiki", "key": "online_search_enabled", "label": "온라인 검색 보완", "type": "bool", "default": False, "desc": "볼트에 없는 내용을 웹으로 보완(비용 발생)."},
            {"section": "wiki_knowledge", "key": "graph_enabled", "label": "지식 그래프", "type": "bool", "default": True},
            {"section": "wiki_knowledge", "key": "graph_retrieval_expand_enabled", "label": "그래프 확장 검색", "type": "bool", "default": True},
            {"section": "wiki_knowledge", "key": "embedding_enabled", "label": "임베딩 하이브리드 검색", "type": "bool", "default": False},
            {"section": "wiki_knowledge", "key": "prep_brief_enabled", "label": "회의 준비 브리핑", "type": "bool", "default": True},
            {"section": "wiki_knowledge", "key": "action_registry_enabled", "label": "액션 레지스트리", "type": "bool", "default": True},
            {"section": "wiki_knowledge", "key": "decision_registry_enabled", "label": "결정 레지스트리", "type": "bool", "default": True},
            {"section": "wiki_knowledge", "key": "update_proposals_enabled", "label": "위키 업데이트 제안", "type": "bool", "default": True},
            {"section": "wiki_knowledge", "key": "section_index_enabled", "label": "섹션 단위 인덱싱", "type": "bool", "default": True},
            {"section": "vault_watcher", "key": "enabled", "label": "폴더 자동 감시 처리", "type": "bool", "default": False},
            {"section": "supermemory", "key": "enabled", "label": "Supermemory 연동", "type": "bool", "default": False},
        ],
    },
    {
        "id": "wiki_detail",
        "label": "위키/검증 세부",
        "desc": "위키 Q&A·사실검증 세부 설정.",
        "fields": [
            {"section": "wiki", "key": "citation_required", "label": "출처 필수", "type": "bool", "default": True},
            {"section": "wiki", "key": "max_context_notes", "label": "최대 참고 노트 수", "type": "number", "default": 8},
            {"section": "wiki", "key": "context_max_chars", "label": "노트당 최대 글자수", "type": "number", "default": 6000, "desc": "위키 답변에 넣는 노트별 본문 길이. 크게 하면 근거가 풍부해지나 비용↑."},
            {"section": "wiki", "key": "online_search_trigger", "label": "온라인 검색 시점", "type": "select", "default": "technical", "options": [{"value": "technical", "label": "technical — 기술용어 감지 시"}, {"value": "always", "label": "always — 항상"}, {"value": "never", "label": "never — 사용 안 함"}]},
            {"section": "wiki", "key": "realtime_search_interval", "label": "실시간 검색 간격(세그먼트)", "type": "number", "default": 3},
            {"section": "wiki", "key": "claim_verify_max", "label": "검증할 최대 주장 수", "type": "number", "default": 8},
            {"section": "wiki", "key": "claim_web_verify", "label": "주장 웹 검증", "type": "bool", "default": False},
            {"section": "wiki_knowledge", "key": "proposal_llm_enabled", "label": "제안 LLM 생성", "type": "bool", "default": False},
            {"section": "wiki_knowledge", "key": "max_context_chars", "label": "위키 컨텍스트 최대 글자", "type": "number", "default": 12000},
            {"section": "wiki_knowledge", "key": "embedding_model", "label": "임베딩 모델", "type": "text", "default": "text-embedding-3-small"},
        ],
    },
    {
        "id": "indexing",
        "label": "검색 인덱스",
        "desc": "볼트 .md 키워드 인덱스 설정.",
        "fields": [
            {"section": "indexing", "key": "enabled", "label": "인덱싱 사용", "type": "bool", "default": True},
            {"section": "indexing", "key": "index_path", "label": "인덱스 파일 경로", "type": "text", "default": "data/vault_index.json"},
            {"section": "indexing", "key": "auto_reindex_on_start", "label": "시작 시 자동 재빌드", "type": "bool", "default": False},
            {"section": "indexing", "key": "auto_reindex_after_write", "label": "저장 직후 자동 재빌드", "type": "bool", "default": False},
        ],
    },
    {
        "id": "email",
        "label": "이메일 (선택)",
        "desc": "회의록 이메일 발송 설정. smtp_host 비우면 발신 도메인으로 자동 추정.",
        "fields": [
            {"section": "email", "key": "sender", "label": "보내는 메일", "type": "text", "default": ""},
            {"section": "email", "key": "password", "label": "메일 앱 비밀번호", "type": "password", "sensitive": True, "default": ""},
            {"section": "email", "key": "recipient", "label": "받는 메일", "type": "text", "default": ""},
            {"section": "email", "key": "smtp_host", "label": "SMTP 호스트(선택)", "type": "text", "default": "", "placeholder": "smtp.office365.com"},
            {"section": "email", "key": "smtp_port", "label": "SMTP 포트(선택)", "type": "number", "default": 0, "placeholder": "587"},
            {"section": "email", "key": "markdown_attachment", "label": "첨부 형식", "type": "select", "default": "txt", "options": [{"value": "txt", "label": "txt — UTF-8 텍스트(한글 안전)"}, {"value": "markdown", "label": "markdown — .md 유지"}]},
        ],
    },
    {
        "id": "notify",
        "label": "알림 (선택)",
        "desc": "처리 완료 시 알림 채널. Slack/Teams Webhook URL 은 아래 '고급: 전체 설정'에서 입력하세요.",
        "fields": [
            {"section": "notify", "key": "on_finish", "label": "완료 알림 채널", "type": "select", "default": "email", "options": [{"value": "email", "label": "email"}, {"value": "slack", "label": "slack"}, {"value": "teams", "label": "teams"}, {"value": "none", "label": "none — 끔"}]},
        ],
    },
    {
        "id": "supermemory",
        "label": "Supermemory (선택)",
        "desc": "Supermemory 연동을 켠 경우에만 필요.",
        "fields": [
            {"section": "supermemory", "key": "api_key", "label": "Supermemory API 키", "type": "password", "sensitive": True, "default": ""},
            {"section": "supermemory", "key": "base_url", "label": "Supermemory 주소", "type": "text", "default": "https://api.supermemory.ai"},
        ],
    },
]


def get_schema() -> List[Dict[str, Any]]:
    return SCHEMA


def iter_fields() -> Iterator[Dict[str, Any]]:
    for group in SCHEMA:
        for field in group["fields"]:
            yield field


def sensitive_paths() -> List[str]:
    return [f"{f['section']}.{f['key']}" for f in iter_fields() if f.get("sensitive")]
