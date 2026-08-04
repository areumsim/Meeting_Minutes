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

field 키: section, key, label, type(text|password|bool|select|number|list|textarea),
  default, desc, options([str|{value,label}]), sensitive, mirror, placeholder,
  picker(bool: 폴더 선택 '찾아보기' 버튼 표시), required(bool: 필수값 * 표시/경고)
그룹 키: id, label, desc, tier(core|common|advanced), advanced(bool: 웹에서 기본 접힘)

화면 배치(tier) — 스키마 배열 순서 = 화면 표시 순서(위→아래, 중요도 내림차순):
  core    "꼭 확인"       : 시작에 반드시 확인/입력 (API 키·모델·저장 위치)
  common  "자주 쓰는 선택" : 자주 쓰지만 선택 (이메일·노트 폴더)
  advanced"고급"          : 필요할 때만. 기본 접힘.

key 에 점(.)이 있으면 중첩 경로로 해석된다(예: "slack.webhook_url" → notify.slack.webhook_url).
============================================================
"""

from typing import Any, Dict, Iterator, List

CONFIG_VERSION = 1


SCHEMA: List[Dict[str, Any]] = [
    # ═══════════ core — 꼭 확인 ═══════════
    {
        "id": "api",
        "tier": "core",
        "label": "API 키",
        "desc": "키는 이 PC의 config.json 에만 저장되며 화면에는 마스킹되어 표시됩니다.",
        "fields": [
            {"section": "api", "key": "openai_api_key", "label": "OpenAI API 키 (필수)", "type": "password", "sensitive": True, "required": True, "default": "", "placeholder": "sk-proj-...", "desc": "음성 인식(STT)과 회의록 생성에 사용."},
            {"section": "api", "key": "anthropic_api_key", "label": "Anthropic(Claude) API 키 (선택)", "type": "password", "sensitive": True, "default": "", "placeholder": "sk-ant-...", "desc": "회의록 생성 AI를 Claude로 쓸 때 필요."},
            {"section": "api", "key": "groq_api_key", "label": "Groq API 키 (선택·STT 폴백)", "type": "password", "sensitive": True, "default": "", "placeholder": "gsk_...", "desc": "OpenAI STT 실패 시 자동으로 Groq(Whisper large-v3)로 폴백합니다. 다른 벤더라 OpenAI 장애 시 진짜 백업이 됩니다. console.groq.com/keys 에서 무료 발급. 비워두면 폴백에서 제외."},
            {"section": "ssl", "key": "verify", "label": "SSL 인증서 검증", "type": "bool", "default": True, "desc": "기본값 켜짐(권장). 회사/학교망 SSL 검사로 인증서 오류가 나면 이 옵션을 끄세요 — 단, MITM 노출 위험이 있으니 필요할 때만."},
        ],
    },
    {
        "id": "models",
        "tier": "core",
        "label": "모델",
        "desc": "회의록/요약 생성 AI와 음성 인식 모델. 비용은 100만 토큰당 대략치(입력/출력)이며 변동될 수 있습니다.",
        "fields": [
            {"section": "models", "key": "llm", "label": "회의록 생성 AI", "type": "select", "default": "gpt", "options": [{"value": "gpt", "label": "GPT (OpenAI)"}, {"value": "claude", "label": "Claude (Anthropic)"}], "desc": "기본 GPT(OpenAI 키만 필요). Claude 선택 시 Anthropic 키 별도 필요."},
            {"section": "models", "key": "stt", "label": "음성 인식(STT) 모델", "type": "select", "default": "gpt-4o-mini-transcribe", "options": [{"value": "gpt-4o-mini-transcribe", "label": "gpt-4o-mini-transcribe — 저렴·빠름 ($0.003/분)"}, {"value": "gpt-4o-transcribe", "label": "gpt-4o-transcribe — 고정확 ($0.006/분)"}, {"value": "gpt-4o-transcribe-diarize", "label": "gpt-4o-transcribe-diarize — 화자분리(배치 전용)"}, {"value": "whisper-1", "label": "whisper-1 — 구형·안정"}], "desc": "실시간 화면에 먼저 뜨는 전사와 배치 처리가 이 모델을 씁니다. 2단계 보정이 켜져 있으면 확정본은 '보정 전사 모델'(기본 gpt-4o-transcribe)이 다시 만듭니다. 실시간 인식 정확도가 아쉬우면 gpt-4o-transcribe로 올리세요."},
            {"section": "models", "key": "stt_fallback", "label": "STT 1차 폴백 모델(OpenAI)", "type": "select", "default": "gpt-4o-transcribe", "options": [{"value": "gpt-4o-transcribe", "label": "gpt-4o-transcribe — 고정확"}, {"value": "gpt-4o-mini-transcribe", "label": "gpt-4o-mini-transcribe — 저렴"}, {"value": "whisper-1", "label": "whisper-1 — 구형·안정"}], "desc": "기본 STT 모델이 실패하면 같은 OpenAI 내에서 먼저 이 모델로 재시도합니다."},
            {"section": "stt", "key": "groq_fallback", "label": "Groq 대체 전사 사용(다른 벤더)", "type": "bool", "default": False, "desc": "켜면 OpenAI가 통째로 실패했을 때 회의 음성이 **다른 회사(Groq)로 전송**됩니다. 전송이 일어나면 회의록 출처에 기록되고 화면에도 표시됩니다. 사내 데이터 정책상 국외 이전·처리 위탁 검토가 끝난 뒤에 켜세요. 기본 꺼짐입니다."},
            {"section": "models", "key": "stt_groq", "label": "STT 2차 폴백 모델(Groq)", "type": "select", "default": "whisper-large-v3-turbo", "options": [{"value": "whisper-large-v3-turbo", "label": "whisper-large-v3-turbo — 빠름·저렴 ($0.04/시간)"}, {"value": "whisper-large-v3", "label": "whisper-large-v3 — 고정확 ($0.111/시간)"}], "desc": "위 'Groq 대체 전사 사용'을 켰을 때 쓰는 모델. 'Groq API 키'도 있어야 동작합니다. 가격은 전사 1시간당(변동 가능)."},
            {"section": "models", "key": "stt_local", "label": "STT 최종 백업 모델(로컬)", "type": "select", "default": "base", "options": [{"value": "tiny", "label": "tiny — 가장 빠름·저정확"}, {"value": "base", "label": "base — 균형(권장)"}, {"value": "small", "label": "small — 느림·정확"}, {"value": "medium", "label": "medium — 매우 느림·고정확"}, {"value": "large-v3", "label": "large-v3 — 가장 느림·최고정확"}], "desc": "아래 '로컬 STT 최종 백업'을 켰을 때 쓰는 faster-whisper 모델 크기. 인터넷·API가 모두 죽어도 이 PC에서 전사합니다. 클수록 정확하지만 느립니다(CPU 실행)."},
            {"section": "models", "key": "claude_model", "label": "Claude 모델", "type": "select", "default": "claude-opus-4-8", "options": [{"value": "claude-opus-4-8", "label": "Opus 4.8 — 최고 성능 (약 $5/$25)"}, {"value": "claude-sonnet-5", "label": "Sonnet 5 — 균형·빠름 (약 $3/$15)"}, {"value": "claude-haiku-4-5", "label": "Haiku 4.5 — 저렴·빠름 (약 $1/$5)"}, {"value": "claude-opus-4-7", "label": "Opus 4.7 — 구버전"}, {"value": "claude-opus-4-6", "label": "Opus 4.6 — 구버전"}]},
            {"section": "models", "key": "gpt_model", "label": "GPT 모델", "type": "select", "default": "gpt-4o-mini", "options": [{"value": "gpt-4o-mini", "label": "gpt-4o-mini — 저렴·빠름 (추천)"}, {"value": "gpt-4o", "label": "gpt-4o — 고품질·비쌈"}, {"value": "o1", "label": "o1 — 추론(느림·고비용)"}, {"value": "o3-mini", "label": "o3-mini — 추론(경량)"}], "desc": "기본 gpt-4o-mini(저렴). 상세 회의록엔 gpt-4o 권장. o1/o3 계열은 추론 모델(느림·고비용)."},
            {"section": "models", "key": "minutes_model", "label": "회의록 생성 모델(GPT, 선택)", "type": "text", "default": "gpt-4o", "desc": "미설정 시 GPT 모델 사용. 상세 기록이라 고성능 권장."},
            {"section": "models", "key": "summary_model", "label": "요약 생성 모델(GPT, 선택)", "type": "text", "default": "gpt-4o", "desc": "미설정 시 GPT 모델 사용."},
            {"section": "models", "key": "translate_model", "label": "번역 모델", "type": "select", "default": "gpt-4o-mini", "options": [{"value": "gpt-4o-mini", "label": "gpt-4o-mini — 저렴·빠름 (추천)"}, {"value": "gpt-4o", "label": "gpt-4o — 고품질"}]},
        ],
    },
    {
        "id": "cost",
        "tier": "common",
        "label": "지출 한도",
        "desc": "API 지출 한도(USD). 0이면 무제한(끔). 업로드 시 예상 비용을 계산해 한도를 넘으면 처리를 거절합니다. 예상치는 대략값이라 실제 청구액과 다를 수 있습니다.",
        "fields": [
            {"section": "cost", "key": "monthly_cap_usd", "label": "월 지출 한도 ($)", "type": "number", "default": 0, "desc": "이번 달 예상 지출 합계가 이 값을 넘는 업로드를 거절합니다. 0이면 제한 없음."},
            {"section": "cost", "key": "per_file_cap_usd", "label": "파일당 지출 한도 ($)", "type": "number", "default": 0, "desc": "파일 한 건의 예상 비용이 이 값을 넘으면 거절합니다(실수로 올린 초장시간 녹음 방지). 0이면 제한 없음."},
        ],
    },
    {
        "id": "automation",
        "tier": "common",
        "label": "자동 실행",
        "desc": "내가 화면을 보고 있지 않을 때 앱이 스스로 하는 일(폴더 자동 감시, 계획 자동화)을 한 번에 제어합니다.",
        "fields": [
            {"section": "automation", "key": "paused", "label": "모든 자동 실행 일시 정지", "type": "bool", "default": False, "desc": "켜면 폴더 자동 감시와 계획 자동화가 아무것도 처리하지 않습니다. 개별 '중지'와 달리 이 설정은 앱을 다시 켜도 유지되므로, 자리를 비우는 동안 예상 못 한 비용이 발생하지 않게 하는 데 씁니다. 되돌리면 다음 확인 주기부터 다시 동작합니다."},
        ],
    },
    {
        "id": "audio",
        "tier": "advanced",
        "advanced": True,
        "label": "오디오 전처리 (업로드·배치)",
        "desc": "업로드한 오디오 파일을 STT 전에 다듬어 인식 정확도를 높입니다. 실시간 녹음에는 적용되지 않습니다.",
        "fields": [
            {"section": "stt", "key": "preprocess_audio", "label": "음량 정규화", "type": "bool", "default": True, "desc": "마이크 입력이 작거나 들쭉날쭉한 녹음의 음량을 고르게 맞춰 인식률을 높입니다(loudnorm). 재생 길이·타임스탬프는 그대로라 안전합니다. 기본 켜짐 권장."},
            {"section": "stt", "key": "trim_silence", "label": "무음 구간 제거 (실험)", "type": "bool", "default": False, "desc": "긴 무음을 잘라 처리 비용·오인식(환각)을 줄입니다. 다만 무음을 지워 전사 타임스탬프가 실제 녹음 시각과 어긋날 수 있어 기본 꺼짐입니다."},
            {"section": "stt", "key": "translation_review", "label": "번역 검수 패스", "type": "bool", "default": True, "desc": "번역(영어→한국어) 후 원문과 번역을 나란히 놓고 주제 맥락으로 오역·누락을 다시 점검·교정합니다. 번역과 별도의 AI 호출이라 비용이 조금 늘지만 번역 품질이 좋아집니다. 업로드·배치 처리에 적용."},
            {"section": "stt", "key": "local_fallback", "label": "로컬 STT 최종 백업 (오프라인)", "type": "bool", "default": False, "desc": "켜면 OpenAI·Groq가 모두 실패했을 때 이 PC에서 faster-whisper로 전사합니다(네트워크·API 완전 무관). 포터블 배포본에는 라이브러리가 이미 포함 — 아래 [로컬 백업 모델 준비] 버튼으로 모델 가중치만 미리 내려받으세요. 회의 처리 중에는 다운로드하지 않으므로 준비 전에는 이 단계가 그냥 건너뛰어집니다. 적용 범위는 파일 업로드와 폴더 자동 감시 처리입니다(실시간 녹음 화면에는 적용되지 않습니다 — 실시간은 Groq까지만 자동 전환)."},
        ],
    },
    {
        "id": "storage",
        "tier": "core",
        "label": "저장 위치 · 회의록 형식",
        "desc": "만들어진 회의록 파일이 어디에 저장될지, 어떤 형식으로 만들지 정합니다. 노트 폴더를 연결했다면 회의록은 그 폴더에도 저장되며, 아래 폴더에는 항상 사본이 남습니다. 잘 모르겠으면 그대로 두세요.",
        "fields": [
            {"section": "output_dir", "key": "", "label": "결과물 저장 폴더", "type": "text", "default": "./output", "scalar": True, "picker": True, "desc": "회의록·요약·전사(.md/.txt) 결과 파일이 저장되는 폴더입니다. 기본값 ./output 은 프로그램 옆 MeetingMinutesData\\output 폴더를 뜻합니다. 특정 위치에 모으려면 절대경로를 넣으세요(예: D:\\Minutes)."},
            {"section": "analysis", "key": "custom_minutes_instructions", "label": "회의록 맞춤 지시 (선택)", "type": "textarea", "default": "", "placeholder": "예: 회의록 맨 위에 '핵심 3줄 요약'을 넣어줘. 액션 아이템은 반드시 표로 정리하고, 각 결정에는 담당자를 표시해줘.", "desc": "원하는 회의록 형식·내용·강조점을 자유롭게 적으면 회의록 생성 AI가 이를 우선 반영합니다. 비우면 기본 형식을 사용합니다. (메일로 받는 회의록도 이 형식이 적용됩니다.)"},
            {"section": "analysis", "key": "default_type", "label": "기본 문서 유형", "type": "select", "default": "meeting", "options": ["meeting", "seminar", "lecture", "memo"], "desc": "유형을 따로 고르지 않았을 때 적용되는 기본값입니다(회의/세미나/강의/메모)."},
            {"section": "analysis", "key": "minutes_vault_context", "label": "회의록 본문에 이전 노트 내용 참고", "type": "boolean", "default": False, "desc": "끄면(기본) 회의록 본문은 이번 녹음 내용만으로 씁니다 — 이전 회의 노트·지난 결정·사전 자료가 회의록 문장에 섞이지 않습니다. 관련 노트 목록('🔗 관련 노트')과 사실 검증은 켜져 있든 꺼져 있든 그대로 동작합니다. 켜면 이전 노트 내용을 배경으로 함께 넣어 용어·맥락이 풍부해지지만, 이번 회의에서 다뤄지지 않은 내용이 회의록에 들어갈 수 있습니다."},
            {"section": "analysis", "key": "templates_dir", "label": "AI 프롬프트 폴더 (고급)", "type": "text", "default": "prompts", "desc": "회의록을 만들 때 쓰는 AI 지시문(.md) 폴더입니다. 문구를 직접 바꾸고 싶은 게 아니면 기본값(prompts) 그대로 두세요."},
        ],
    },

    # ═══════════ common — 자주 쓰는 선택 ═══════════
    {
        "id": "email",
        "tier": "common",
        "label": "이메일 자동 발송 (선택)",
        "desc": "회의록이 완성되면 자동으로 메일로 보내는 기능입니다. 안 쓰면 비워 두세요. 쓰려면 '보내는 메일'과 그 메일의 '앱 비밀번호'가 필요합니다.",
        "fields": [
            {"section": "email", "key": "sender", "label": "보내는 메일 주소", "type": "text", "default": "", "placeholder": "myid@gmail.com", "desc": "회의록을 보낼 내 메일 계정(Gmail/네이버/아웃룩 등)."},
            {"section": "email", "key": "password", "label": "메일 앱 비밀번호", "type": "password", "sensitive": True, "default": "", "desc": "주의: 평소 로그인 비밀번호가 아닙니다. 메일 서비스 보안설정에서 '앱 비밀번호'를 따로 발급해 넣으세요. (Gmail: Google 계정→보안→2단계 인증 켠 뒤 '앱 비밀번호' / 네이버: 메일 환경설정→POP3·SMTP→'앱 비밀번호 설정' / 아웃룩: 계정 보안→앱 암호) 보통 공백 없는 16자리입니다."},
            {"section": "email", "key": "recipient", "label": "받는 메일 주소", "type": "text", "default": "", "placeholder": "team@company.com", "desc": "회의록을 받을 주소. 비우면 보내는 주소로 자기 자신에게 보냅니다."},
            {"section": "email", "key": "smtp_host", "label": "SMTP 서버 (보통 비워둠)", "type": "text", "default": "", "placeholder": "자동 감지", "desc": "비워 두면 보내는 메일 도메인으로 자동 설정됩니다(gmail/naver/outlook 인식). 회사 자체 메일서버면 여기에 주소를 넣으세요(예: smtp.office365.com)."},
            {"section": "email", "key": "smtp_port", "label": "SMTP 포트 (보통 비워둠)", "type": "number", "default": 0, "placeholder": "자동(587)", "desc": "비워 두거나 0이면 자동(대개 587). 회사 서버가 다른 포트를 쓰면 지정."},
            {"section": "email", "key": "markdown_attachment", "label": "첨부 파일 형식", "type": "select", "default": "txt", "options": [{"value": "txt", "label": "txt — 텍스트(한글 안전, 추천)"}, {"value": "markdown", "label": "markdown — .md 원본 유지"}], "desc": "회의록을 어떤 파일로 첨부할지. 대부분 txt 권장."},
        ],
    },
    {
        "id": "obsidian",
        "tier": "common",
        "label": "노트 폴더 (내부 위키) · Obsidian 연동",
        "desc": "회의록·검색·지식 그래프의 바탕이 되는 .md 노트 폴더입니다. Obsidian 앱이 없어도 폴더만 지정하면 됩니다 — 회의록이 그 폴더에 .md로 저장되고, 위키 검색·지식 그래프가 그 폴더의 노트([[위키링크]] 포함)에서 자동으로 만들어집니다. 아래 REST API 항목은 Obsidian 앱에 실시간 반영이 필요할 때만 켜세요.",
        "fields": [
            {"section": "obsidian", "key": "vault_path", "label": "노트 폴더 (.md) — 위키·그래프의 원천", "type": "text", "default": "", "placeholder": r"D:\Notes  또는  D:\Obsidian\MyVault", "picker": True, "desc": "Markdown(.md) 노트가 들어 있는 폴더를 지정하세요. Obsidian 볼트든 일반 폴더든 상관없습니다. 지정하면 회의록 저장·위키 질문·지식 그래프가 이 폴더만으로 동작합니다(REST API 불필요). 저장 후 [검색 인덱스·그래프 재빌드]를 한 번 눌러 최신화하세요.", "mirror": [["indexing", "vault_path"]]},
            {"section": "obsidian", "key": "enabled", "label": "Obsidian REST 저장 사용 (선택)", "type": "bool", "default": False, "desc": "대부분 꺼 두면 됩니다. 켜면 Local REST API로 실행 중인 Obsidian 앱에 실시간 기록합니다. 꺼도 위 노트 폴더가 있으면 .md로 저장·검색·그래프가 모두 동작합니다."},
            {"section": "obsidian", "key": "api_url", "label": "Local REST API 주소", "type": "text", "default": "https://127.0.0.1:27124"},
            {"section": "obsidian", "key": "api_key", "label": "Local REST API 키", "type": "password", "sensitive": True, "default": "", "desc": "Obsidian → 설정 → Local REST API 에서 발급."},
            {"section": "obsidian", "key": "verify_ssl", "label": "REST SSL 검증", "type": "bool", "default": False},
            {"section": "obsidian", "key": "exe_path", "label": "Obsidian 실행파일 경로(선택)", "type": "text", "default": "", "placeholder": r"C:\...\Obsidian.exe", "picker": True, "desc": "설정 시 처리 전 Obsidian 자동 실행. (파일 선택 후 폴더가 아닌 Obsidian.exe 경로로 조정하세요.)"},
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

    # ═══════════ advanced — 고급 (기본 접힘) ═══════════
    {
        "id": "realtime",
        "tier": "advanced",
        "advanced": True,
        "label": "실시간 녹취",
        "desc": "실시간 녹음/전사 기본값입니다.",
        "fields": [
            {"section": "realtime", "key": "mode", "label": "전사 방식", "type": "select", "default": "http", "desc": "http=청크 전사(안정·저비용, 표시까지 2~6초). auto/ws=OpenAI GA 실시간 WebSocket(표시 ~1초, 비용 높음) — 표시가 느리다고 느끼면 auto를 권장(실패 시 http로 자동 폴백).", "options": [{"value": "http", "label": "http — 청크(안정·저비용)"}, {"value": "auto", "label": "auto — WS 실시간 우선·실패 시 http 폴백(저지연)"}, {"value": "ws", "label": "ws — WebSocket 전용(~1초 지연)"}]},
            {"section": "realtime", "key": "language", "label": "기본 언어", "type": "select", "default": "ko", "options": [{"value": "ko", "label": "ko — 한국어(권장)"}, {"value": "en", "label": "en — 영어"}, {"value": "auto", "label": "auto — 자동판정(비권장)"}], "desc": "auto는 짧은 조각마다 언어를 다시 판정해 무음·잡음 구간이 엉뚱한 언어(러시아어 등)로 잘못 전사될 수 있습니다. 회의 언어를 직접 지정하세요."},
            {"section": "realtime", "key": "type", "label": "기본 문서 유형", "type": "select", "default": "meeting", "options": ["meeting", "seminar", "lecture"]},
            {"section": "realtime", "key": "translate", "label": "번역 사용", "type": "bool", "default": False},
            {"section": "realtime", "key": "audio_backup", "label": "오디오 백업(PCM)", "type": "bool", "default": True, "desc": "크래시 복구용. 약 115MB/시간."},
            {"section": "realtime", "key": "chunk_duration", "label": "청크 길이(초)", "type": "number", "default": 3.0},
            {"section": "realtime", "key": "two_pass", "label": "2단계 전사 보정", "type": "bool", "default": True, "desc": "실시간 조각 전사를 일정 구간마다 다시 전사해 온전한 문장으로 교체합니다(화면·회의록 모두). STT 비용이 약 2배가 되지만 품질이 크게 좋아집니다."},
            {"section": "realtime", "key": "revise_window_sec", "label": "보정 구간 길이(초)", "type": "number", "default": 25, "desc": "이 길이만큼 쌓이면 다시 전사해 문장으로 교체. 짧을수록 빨리 확정되지만 문맥이 줄어듭니다."},
            {"section": "realtime", "key": "revise_model", "label": "보정 전사 모델", "type": "select", "default": "gpt-4o-transcribe", "options": [{"value": "gpt-4o-transcribe", "label": "gpt-4o-transcribe — 고정확(권장)"}, {"value": "gpt-4o-mini-transcribe", "label": "gpt-4o-mini-transcribe — 저렴"}], "desc": "최종 품질을 결정하는 모델. 실시간 표시는 STT 모델, 확정본은 이 모델."},
            {"section": "realtime", "key": "fast_max_chunk_sec", "label": "실시간 청크 최대 길이(초)", "type": "number", "default": 5.0, "desc": "무음이 없어도 이 길이에서 잘라 표시합니다. 짧을수록 빨리 뜨고 조각납니다(조각은 보정 패스가 정리)."},
            {"section": "realtime", "key": "silence_rms", "label": "무음 판정 임계값(RMS)", "type": "number", "default": 300, "desc": "HTTP 모드 발화 경계 감지. 마이크 입력이 작아 전사가 잘게 끊기면 100~200으로 낮추고, 시끄러운 환경에서 항상 최대 길이로 잘리면 500~800으로 올리세요."},
            {"section": "realtime", "key": "stt_concurrency", "label": "실시간 STT 동시 호출 수", "type": "number", "default": 2, "desc": "HTTP 모드 빠른 패스 병렬 전사(1~4). STT 응답이 느린 네트워크에서 표시 지연이 누적되는 것을 막습니다. 표시 순서는 항상 유지됩니다."},
            {"section": "realtime", "key": "drop_silent_chunks", "label": "무음 구간 전사 건너뛰기", "type": "bool", "default": True, "desc": "발화 에너지가 없는 구간(정적·잡음)은 STT에 보내지 않습니다. 무음을 전사시키면 모델이 없는 말을 만들어내(외국어 조각·같은 문장 반복) 전사가 오염됩니다. 조용히 말해도 전사가 안 되면 위 '무음 판정 임계값'을 낮추세요."},
            {"section": "realtime", "key": "prompt_context", "label": "전사 문맥 전달 방식", "type": "select", "default": "static", "options": [{"value": "static", "label": "static — 주제·참석자만 전달(권장)"}, {"value": "tail", "label": "tail — 직전 전사 문장까지 전달"}, {"value": "off", "label": "off — 문맥 전달 안 함"}], "desc": "tail은 경계 단어 인식에 유리하지만, 모델이 직전 문장을 되풀이하면 그 결과가 다시 문맥이 되어 같은 문장이 계속 반복되는 문제가 있습니다."},
            {"section": "realtime", "key": "hallucination_filter", "label": "환각·반복 자동 정화", "type": "bool", "default": True, "desc": "같은 문장의 되풀이를 축약하고, 회의 언어에 맞지 않는 이질 문자(키릴 등)는 [불명]으로 표시합니다. 내용을 지우지는 않습니다."},
            {"section": "realtime", "key": "ws_vad_type", "label": "WS VAD 방식", "type": "select", "default": "server_vad", "options": ["server_vad", "semantic_vad"]},
            {"section": "realtime", "key": "ws_vad_eagerness", "label": "WS 발화종료 민감도", "type": "select", "default": "medium", "options": ["low", "medium", "high", "auto"]},
            {"section": "realtime", "key": "ws_noise_reduction", "label": "WS 노이즈 리덕션", "type": "select", "default": "near_field", "options": ["near_field", "far_field"]},
            {"section": "realtime", "key": "email_on_finish", "label": "종료 후 이메일 자동발송", "type": "bool", "default": False},
        ],
    },
    {
        "id": "features",
        "tier": "advanced",
        "advanced": True,
        "label": "기능 토글",
        "desc": "부가 기능을 켜고 끕니다.",
        "fields": [
            {"section": "wiki", "key": "vault_enrich", "label": "회의록 볼트 연관 노트 링크", "type": "bool", "default": True},
            {"section": "wiki", "key": "claim_verify", "label": "주장 사실 검증", "type": "bool", "default": True},
            {"section": "wiki", "key": "realtime_vault_search", "label": "실시간 관련 노트 검색", "type": "bool", "default": True, "desc": "녹음 중 발화와 관련된 내부 노트(섹션·논문 포함)를 찾아 화면 상단에 조용히 표시하고, 종료 후 회의록·회의 상세에 근거와 함께 남깁니다. 노트 폴더와 검색 인덱스가 필요하며, 없으면 화면에 사유가 표시됩니다."},
            {"section": "wiki", "key": "online_search_enabled", "label": "온라인 검색 보완", "type": "bool", "default": False, "desc": "볼트에 없는 내용을 웹으로 보완(비용 발생). 녹음 중 실시간 웹 보완은 웹 UI 녹음에서만 동작하며(터미널 CLI 녹음은 내부 노트 검색만), 아래 '위키/검증 세부'의 실시간 웹검색 간격도 함께 켜야 합니다."},
            {"section": "wiki_knowledge", "key": "graph_enabled", "label": "지식 그래프", "type": "bool", "default": True},
            {"section": "wiki_knowledge", "key": "graph_retrieval_expand_enabled", "label": "그래프 확장 검색", "type": "bool", "default": True},
            {"section": "wiki_knowledge", "key": "embedding_enabled", "label": "임베딩 하이브리드 검색(의미 검색)", "type": "bool", "default": True},
            {"section": "wiki_knowledge", "key": "prep_brief_enabled", "label": "회의 준비 브리핑", "type": "bool", "default": True},
            {"section": "wiki_knowledge", "key": "action_registry_enabled", "label": "액션 레지스트리", "type": "bool", "default": True},
            {"section": "wiki_knowledge", "key": "decision_registry_enabled", "label": "결정 레지스트리", "type": "bool", "default": True},
            {"section": "wiki_knowledge", "key": "update_proposals_enabled", "label": "위키 업데이트 제안", "type": "bool", "default": True},
            {"section": "wiki_knowledge", "key": "section_index_enabled", "label": "섹션 단위 인덱싱", "type": "bool", "default": True},
            {"section": "vault_watcher", "key": "enabled", "label": "폴더 자동 감시 처리", "type": "bool", "default": False, "desc": "켜 두면 앱 시작 시 폴더 감시를 자동으로 시작합니다. 감시할 폴더는 [설정] 하단 '폴더 자동 감시' 카드에서 추가하세요."},
            {"section": "vault_watcher", "key": "process_existing", "label": "감시 시작 시 기존 파일도 처리", "type": "bool", "default": False, "desc": "기본값(꺼짐)에서는 감시를 켜기 전부터 폴더에 있던 파일을 자동 처리하지 않고 확인 대기열에 넣습니다. 켜면 폴더에 이미 있던 녹음 전체가 즉시 처리되며 그만큼 비용이 한꺼번에 발생합니다."},
            {"section": "plan_watcher", "key": "enabled", "label": "계획 자동화 자동 시작", "type": "bool", "default": False, "desc": "켜 두면 앱 시작 시 planned 노트 사전 리서치·첨부 녹음 자동 처리를 자동으로 시작합니다(Obsidian 볼트 필요)."},
            {"section": "supermemory", "key": "enabled", "label": "Supermemory 연동", "type": "bool", "default": False},
        ],
    },
    {
        "id": "server",
        "tier": "advanced",
        "advanced": True,
        "label": "서버/네트워크",
        "desc": "아이폰·태블릿 앱에서 이 PC에 접속하려면 'LAN 접속 허용'을 켜세요. 켜면 같은 WiFi의 다른 기기가 이 PC의 회의록 서버에 연결할 수 있습니다.",
        "fields": [
            {"section": "server", "key": "lan_access", "label": "LAN 접속 허용 (모바일 앱 연결용)", "type": "bool", "default": False, "desc": "끄면 이 PC에서만(localhost) 접속 가능(기본, 안전). 켜면 0.0.0.0으로 바인딩해 같은 WiFi의 기기가 접속할 수 있습니다 — 신뢰된 네트워크(집·사무실)에서만 켜세요. 변경 후 앱을 재시작해야 적용됩니다."},
            {"section": "server", "key": "max_upload_mb", "label": "업로드 최대 크기 (MB)", "type": "number", "default": 2048, "desc": "이보다 큰 파일은 거절합니다(기본 2048MB). 3시간 mp3가 약 150MB, 같은 길이 wav가 약 1GB입니다 — 디스크가 가득 차는 것을 막는 안전장치이고, 더 큰 파일을 올려야 하면 값을 늘리세요."},
            {"section": "server", "key": "max_duration_hours", "label": "업로드 최대 길이 (시간)", "type": "number", "default": 8, "desc": "녹음 길이가 이보다 길면 거절합니다(기본 8시간). 0이면 제한 없음. 크기 상한만으로는 고압축 장시간 파일이 통과해 STT 비용이 커집니다."},
        ],
    },
    {
        "id": "notify",
        "tier": "advanced",
        "advanced": True,
        "label": "알림 (선택)",
        "desc": "처리 완료 시 알림 채널.",
        "fields": [
            {"section": "notify", "key": "on_finish", "label": "완료 알림 채널", "type": "select", "default": "none", "options": [{"value": "none", "label": "none — 끔"}, {"value": "email", "label": "email"}, {"value": "slack", "label": "slack"}, {"value": "teams", "label": "teams"}], "desc": "기본 꺼짐. 이메일/Slack/Teams는 해당 설정을 채운 뒤 선택."},
            {"section": "notify", "key": "slack.webhook_url", "label": "Slack Webhook URL", "type": "password", "sensitive": True, "default": "", "placeholder": "https://hooks.slack.com/services/...", "desc": "알림 채널을 slack 으로 쓸 때 필요. Slack 채널 → 앱 → Incoming Webhooks 에서 발급."},
            {"section": "notify", "key": "teams.webhook_url", "label": "Teams Webhook URL", "type": "password", "sensitive": True, "default": "", "placeholder": "https://outlook.office.com/webhook/...", "desc": "알림 채널을 teams 로 쓸 때 필요. Teams 채널 → 커넥터 → Incoming Webhook 에서 발급."},
        ],
    },
    {
        "id": "indexing",
        "tier": "advanced",
        "advanced": True,
        "label": "검색 인덱스",
        "desc": "볼트 .md 키워드 인덱스 설정.",
        "fields": [
            {"section": "indexing", "key": "enabled", "label": "인덱싱 사용", "type": "bool", "default": True},
            {"section": "indexing", "key": "index_path", "label": "인덱스 파일 경로", "type": "text", "default": "data/vault_index.json"},
            {"section": "indexing", "key": "auto_reindex_on_start", "label": "시작 시 자동 재빌드", "type": "bool", "default": False},
            {"section": "indexing", "key": "auto_reindex_after_write", "label": "저장 직후 자동 재빌드(인덱스+그래프)", "type": "bool", "default": True},
        ],
    },
    {
        "id": "wiki_detail",
        "tier": "advanced",
        "advanced": True,
        "label": "위키/검증 세부",
        "desc": "위키 Q&A·사실검증 세부 설정.",
        "fields": [
            {"section": "wiki", "key": "citation_required", "label": "출처 필수", "type": "bool", "default": True},
            {"section": "wiki", "key": "max_context_notes", "label": "최대 참고 노트 수", "type": "number", "default": 10},
            {"section": "wiki", "key": "context_max_chars", "label": "노트당 최대 글자수", "type": "number", "default": 6000, "desc": "위키 답변에 넣는 노트별 본문 길이. 크게 하면 근거가 풍부해지나 비용↑."},
            {"section": "wiki", "key": "online_search_trigger", "label": "온라인 검색 시점", "type": "select", "default": "technical", "options": [{"value": "technical", "label": "technical — 기술용어 감지 시"}, {"value": "always", "label": "always — 항상"}, {"value": "never", "label": "never — 사용 안 함"}]},
            {"section": "wiki", "key": "realtime_search_interval", "label": "실시간 검색 간격(발화)", "type": "number", "default": 1, "desc": "관련 노트를 찾을 발화 간격입니다. 여기서 세는 발화는 '찾아볼 거리가 있는' 발화만입니다(아래 항목) — 인사말·군더더기는 세지 않습니다. 기본 1은 그런 발화마다 검색합니다. 느리게 느껴지면 2~3으로 올리세요. 0이나 빈 값은 1로 처리됩니다."},
            {"section": "wiki", "key": "realtime_min_terms", "label": "검색할 발화 판단 기준(단어 수)", "type": "number", "default": 3, "desc": "노트 폴더에 실제로 등장하는 단어가 이 개수 이상인 발화만 검색합니다. 기본 3은 '안녕하세요', '다음 회의는 화요일입니다' 같은 발화를 건너뛰고 고유명사·용어가 들어간 발화만 찾습니다(예전에는 발화 순서만 보고 골라서, 정작 중요한 발화는 건너뛰고 인사말로 엉뚱한 노트를 띄웠습니다). 너무 많이 걸러진다고 느끼면 2로 낮추고, 0으로 두면 이 판단을 끕니다."},
            {"section": "wiki", "key": "realtime_note_candidates", "label": "실시간 노트 후보 수", "type": "number", "default": 10, "desc": "발화별로 모을 노트 후보 개수(키워드+의미 검색 융합). 넉넉히 모으고 화면에는 상위 몇 개만 보여줍니다(나머지는 종료 후 '참조된 관련 노트'에서 확인). 영어 발화로 한국어 노트를 찾는 교차언어 검색도 이 경로가 담당합니다."},
            {"section": "wiki", "key": "realtime_paper_candidates", "label": "실시간 논문 후보 수", "type": "number", "default": 4, "desc": "위 후보에 더해 논문/이론 폴더만 따로 검색해 추가할 개수. 로컬 논문이 일반 노트에 밀려 후보에서 빠지는 것을 막습니다. 점수를 올려주지는 않습니다 — 폴더 소속으로 우대하면 관련도가 오히려 나빠진다는 실측 결과가 있습니다."},
            {"section": "wiki", "key": "realtime_paper_dirs", "label": "논문/이론 폴더", "type": "list", "default": ["02_이론_학습", "01_References", "원문추출"], "desc": "로컬 논문·원문추출이 있는 폴더 이름을 한 줄에 하나씩. 노트 폴더 구조에 맞게 바꾸세요. 폴더가 하위에 묻혀 있어도 됩니다 — 이름만 맞으면 경로 중간에서도 찾습니다(예: Archive/양자아카이브/02_이론_학습)."},
            {"section": "wiki", "key": "realtime_display_count", "label": "실시간 표시 개수", "type": "number", "default": 3, "desc": "녹음 화면 상단 칩으로 한 번에 표시할 관련 노트 수. 전사 화면을 방해하지 않도록 작게 두세요."},
            {"section": "wiki", "key": "realtime_query_chars", "label": "실시간 검색 쿼리 길이(자)", "type": "number", "default": 180, "desc": "발화의 앞 몇 글자를 검색어로 쓸지. 길수록 의미·교차언어 회수에 유리합니다(실측: 60자→180자에서 상위 3개 회수율 +0.17). 웹 보완 검색도 같은 길이를 씁니다."},
            {"section": "wiki", "key": "related_notes_max_rank", "label": "회의록에 실을 관련 노트 순위 상한", "type": "number", "default": 0, "desc": "0=제한 없음. 예: 5로 두면 녹음 중 검색에서 상위 5위 안에 든 적이 있는 노트만 회의록 '🔗 관련 노트'에 싣습니다(노이즈 컷). 순위는 검색 종류별로 따로 셉니다(일반 노트 검색과 논문/이론 폴더 검색이 각각 1위부터) — 그래서 이 값을 낮게 둬도 논문이 통째로 빠지지 않습니다. 녹음 화면 표시와 회의 상세의 누적 목록에는 영향이 없습니다."},
            {"section": "wiki", "key": "realtime_search_backend", "label": "실시간 검색 백엔드", "type": "select", "default": "auto", "options": [{"value": "auto", "label": "auto — 로컬 인덱스 우선, 실패 시 Obsidian"}, {"value": "index", "label": "index — 로컬 인덱스만"}, {"value": "rest", "label": "rest — Obsidian REST만"}], "desc": "특별한 이유가 없으면 auto 를 두세요. 로컬 인덱스가 훨씬 빠르고 Obsidian 앱을 켜지 않아도 됩니다."},
            {"section": "wiki", "key": "realtime_web_search_interval", "label": "실시간 웹검색 간격(세그먼트)", "type": "number", "default": 0, "desc": "0=사용 안 함(권장). '온라인 검색 보완'이 켜져 있을 때만 동작하고, 웹 UI 녹음 전용입니다(터미널 CLI 녹음에는 적용되지 않음). 내부 노트 결과 뒤에 보완재로만 표시됩니다."},
            {"section": "wiki", "key": "realtime_web_only_if_no_vault_hit", "label": "웹검색은 내부 미발견 시만", "type": "bool", "default": True, "desc": "켜 두면(권장) 내부 노트에서 관련 자료를 찾은 구간에서는 웹 호출을 건너뜁니다 — 웹은 보완재. 끄면 간격마다 항상 웹도 조회합니다(비용 증가)."},
            {"section": "wiki", "key": "claim_verify_max", "label": "검증할 최대 주장 수", "type": "number", "default": 8},
            {"section": "wiki", "key": "claim_web_verify", "label": "주장 웹 검증", "type": "bool", "default": False},
            {"section": "wiki_knowledge", "key": "proposal_llm_enabled", "label": "제안 LLM 생성", "type": "bool", "default": False},
            {"section": "wiki_knowledge", "key": "max_context_chars", "label": "위키 컨텍스트 최대 글자", "type": "number", "default": 12000},
            {"section": "wiki_knowledge", "key": "embedding_model", "label": "임베딩 모델", "type": "text", "default": "text-embedding-3-small"},
        ],
    },
    {
        "id": "facilitation",
        "tier": "advanced",
        "advanced": True,
        "label": "회의 진행 페르소나 (실험 · 기본 꺼짐)",
        "desc": "실시간 녹음 중 여러 관점의 페르소나(촉진자·서기·팩트체커 등)가 놓친 논점·모호한 정의·사실 오류 후보를 판정하는 보조 기능입니다. 현재는 관찰모드(M0) — 켜도 화면에는 아무것도 뜨지 않고 판정을 기록만 합니다(기록으로 오탐률을 실측한 뒤에 화면 개입을 엽니다). 기록은 `meeting-minutes facilitation-report` 로 봅니다. 트리아지 호출마다 소액의 LLM 비용이 발생하며 지출 한도를 지납니다. 페르소나별 참견도(0=금지~5)는 config.json 의 facilitation.personas 에서 조정합니다.",
        "fields": [
            {"section": "facilitation", "key": "enabled", "label": "회의 진행 페르소나 사용", "type": "bool", "default": False, "desc": "기본 꺼짐. 켜면 녹음 중 일정 주기로 경량 LLM이 페르소나 개입 후보를 판정해 기록합니다(관찰모드 — 화면 표시 없음)."},
            {"section": "facilitation", "key": "max_level", "label": "참견도 전역 상한", "type": "number", "default": 3, "desc": "개별 페르소나 참견도가 아무리 높아도 이 값을 넘지 못합니다. 4(알림음)·5(음성)는 관리자가 열어야 합니다. 사내 배포 기본 3(무음)."},
            {"section": "facilitation", "key": "triage_model", "label": "트리아지(1차 선별) 모델", "type": "select", "default": "gpt-4o-mini", "options": [{"value": "gpt-4o-mini", "label": "gpt-4o-mini — 저렴·빠름 (권장)"}, {"value": "claude-haiku-4-5", "label": "claude-haiku-4-5 — Anthropic 키 필요"}], "desc": "매 주기 후보 판정에 쓰는 경량 모델 — 이 기능의 상시 비용을 결정합니다. claude 를 고르면 현재는 Claude 모델 설정(models.claude_model)이 대신 호출되며, 예상 비용·지출 한도도 그 모델 단가로 계산됩니다(기본값 claude-opus-4-8 이면 gpt-4o-mini 의 수십 배)."},
            {"section": "facilitation", "key": "triage_period_sec", "label": "트리아지 주기(초)", "type": "number", "default": 25, "desc": "이 간격마다 최대 1회 판정합니다(발화가 없으면 0회). 시간 기반이라 1시간 회의의 호출 수 상한이 고정됩니다(기본 25초 = 최대 ~144회)."},
            # 아래 4개는 **일부러 여기에 없다**: max_interventions_per_session ·
            # voice_enabled · web_search_enabled · web_search_interval.
            # 읽는 코드가 아직 0줄(M1~M3 몫)이라 설정 화면에 올리면 "켰는데 아무 일도
            # 안 일어나는 토글"이 된다 — 이 리포가 반복해서 없애온 UX 함정이다.
            # 기본값·의미는 config.example.json 주석에만 남기고, 해당 마일스톤에서
            # 코드와 함께 여기로 올린다.
            {"section": "facilitation", "key": "max_cost_usd_per_meeting", "label": "회의당 비용 캡 ($)", "type": "number", "default": 0.5, "desc": "이 기능이 한 회의에서 쓸 수 있는 상한. 월 지출 한도(cost.monthly_cap_usd)가 0(무제한)이어도 이 캡은 동작합니다. 0이면 캡 없음."},
        ],
    },
    {
        "id": "supermemory",
        "tier": "advanced",
        "advanced": True,
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


def field_for(section: str, key: str) -> Dict[str, Any] | None:
    """(section, key) 에 해당하는 스키마 필드를 반환(없으면 None).

    최상위 스칼라(예: output_dir)는 key="" 로 등록돼 있으므로 그대로 조회된다.
    """
    for f in iter_fields():
        if f.get("section") == section and f.get("key") == key:
            return f
    return None
