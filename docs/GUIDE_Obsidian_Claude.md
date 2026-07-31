# 회의록 자동화 — 쉬운 사용 설명서

녹음만 하면 **Claude가 회의록을 쓰고 → 관련 Wiki를 찾아 반영하고 → 노트 대조과 Wiki 업데이트 후보를 남기고 → Obsidian에 정리하고 → 메일로 보냅니다.**

```
🎙️ 녹음/업로드 ──▶ 📝 회의록(Claude) ──▶ 🔎 Wiki 컨텍스트 ──▶ ✅ 노트 대조 ──▶ 📚 Obsidian 저장 ──▶ ✉️ 메일
```

---

## ✅ 사용 전 확인

아래 항목이 설정되어 있으면 녹음 파일 처리 후 Obsidian 저장과 메일 발송까지 자동으로 이어집니다.

| 기능 | 확인할 설정 |
|------|------|
| Claude로 회의록 작성 | `models.llm = "claude"` 및 Anthropic API 키 |
| 용어·인물·기업 자동 검색·설명 | Obsidian 연결 및 후처리 설정 |
| Vault 노트 대조 | `wiki.claim_verify = true` |
| Wiki Context/Proposal 저장 | `wiki_knowledge.enabled = true` |
| Obsidian 볼트에 자동 저장 | `obsidian.enabled`, `obsidian.api_key`, `obsidian.vault_path` |
| 완료 후 메일 자동 발송 | `notify.on_finish = "email"` 및 `email` 섹션 |
| STT(음성→글) | OpenAI API 키 및 `models.stt` |

> 연결만 확인하고 싶으면: `python run_meeting.py obsidian --ping` → `✓ 연결 성공` 이면 끝.
> 실제 볼트와 회의록 저장 폴더까지 보려면: `python run_meeting.py obsidian --where`.

---

## 1. 어떻게 쓰나 (3가지 중 편한 것)

### 방법 A — 녹음 파일이 있을 때
```bash
python run_meeting.py batch 회의녹음.mp4
```

### 방법 B — 웹 화면에서 (마우스로)
```bash
python run_meeting.py web
```
→ 브라우저에서 **업로드** 또는 **실시간 녹음** 버튼을 사용할 수 있습니다.

주의: 현재 웹/모바일 Recorder의 direct OpenAI 경로는 기기 로컬 저장 중심이라 Obsidian/Wiki/노트 대조 파이프라인을 우회합니다. 운영 기록으로 남길 회의는 `batch`, `ingest`, CLI 실시간 또는 서버 `/ws/realtime` 경로를 사용하세요.

### 방법 C — 실시간 마이크 녹음
```bash
python run_meeting.py realtime --language ko
```
→ 말이 끝나고 `q`+Enter 누르면 회의록이 만들어집니다.

설정이 켜져 있으면 batch/ingest/CLI 실시간/server realtime 경로는 처리 후 Obsidian 저장, 노트 대조, Wiki Context/Proposal, 메일 발송을 시도합니다. Obsidian 연결이 없으면 파일 출력은 유지되고, 저장 단계만 건너뜁니다.

---

## 2. 결과는 어디로 가나

1. **Obsidian 볼트** (가장 중요)
   - `obsidian.meetings_path/yymmdd 제목.md` — 회의록. 양자 도메인 예: `Archive/도메인_아카이브/01_회의_세미나/회의별/{year}/260627 제목.md`
   - `obsidian.transcripts_path/yymmdd 제목 - 전사.md` — 전체 STT 전사. 양자 도메인 예: `Archive/도메인_아카이브/01_회의_세미나/전사/{year}/260627 제목 - 전사.md`
   - `obsidian.meetings_path`가 비어 있으면 `00_Meetings/<도메인>/yymmdd 제목.md` — 회의록 (프로젝트 미설정 시 `기타/`)
   - batch/process 회의록 frontmatter에는 `session_date`, `session_dt`, `source_file_date`, `source_audio`, `processed_at`, `stt_source`, `stt_segment_count`, `refined_ratio`가 기록됩니다.
   - ingest recording note도 `source_audio`, `source_file_date`, `processed_at`, `stt_source`, `stt_segment_count`를 기록합니다. vault-audio는 기존 노트에 병합하므로 기존 frontmatter를 우선 보존합니다.
   - Vault 노트 대조가 켜져 있으면 본문에 `## 노트 대조 (자동 · 사람 확인 필요)` 섹션이 추가되고 판정·신뢰도·대조 노트가 표시됩니다(참고용 — 확정된 검증이 아닙니다).
   - 처리 폴더에는 `wiki_context.json`, 관련 노트가 있을 때 `wiki_proposal.md/json`, 실시간/서버 경로에서는 `*_fact_check.md` 또는 웹 `Fact Check` 탭이 남습니다.
   - `01_References/People|Companies/이름.md` — 인물·기업 설명 노트 (종류별, 프로젝트 무관 공유)
   - `01_References/<도메인>/용어.md` — 용어·기술 노트 (프로젝트 미설정 시 `공통/`)
   - **도메인** = `config.json`의 `obsidian.project` (여러 프로젝트를 묶으려면 `obsidian.project_domains` 매핑). 단, `meetings_path`가 있으면 회의록은 그 경로를 우선 사용합니다.
   - Obsidian 앱의 **그래프 뷰**에서 회의록 ↔ 용어가 이어진 걸 볼 수 있습니다.
2. **메일** — 설정된 주소로 회의록·요약이 첨부되어 발송
3. **로컬 폴더** `output/날짜_제목/` — 회의록·요약·전사·액션·노트 대조·Wiki Context/Proposal 파일 (백업/검토용)

---

## 3. Claude Cowork로 더 활용하기

**Claude Cowork**는 Claude 데스크톱 앱의 비서입니다(개발용 Claude Code와 다름). 볼트에 쌓인 회의록을 **사람처럼 뒤지고 정리**해 줍니다. 연결 방법은 두 가지입니다 — 로컬 PC에서 쓸 거면 A, 팀원과 공유하거나 웹/모바일 Claude에서 쓸 거면 B.

### 3-A. 폴더 접근 (같은 PC에서, 가장 쉬움)

**연결**: Claude 데스크톱 앱 → Cowork → "폴더 접근 권한"에서 **Obsidian 볼트 폴더**를 선택. (끝)

**시킬 수 있는 일** (그냥 말로):
- "지난달 그래프DB 회의들 결정사항만 모아줘"
- "내일 OO 회의 사전 브리핑 노트 만들어줘"
- "미완료 액션아이템 담당자별로 정리해줘"
- "비슷한 용어 노트끼리 링크 걸어줘"

폴더 안 파일을 통째로 읽는 방식이라 빠르고 별도 설정이 없지만, **이 파이프라인을 실행 중인 PC에서만** 되고 관계(누가 어느 회의에서 뭘 결정했는지 등)는 Claude가 매번 텍스트에서 다시 추론해야 합니다.

### 3-B. Wiki Graph MCP 커넥터 (원격, 관계 조회 전용)

파이프라인이 회의 처리 때마다 인물·조직·주제·결정·액션 사이의 관계를 그래프 DB(`wiki_graph.db`)에 쌓아 둡니다. 이 그래프를 **원격 MCP 서버**(`/mcp`)로 노출해서, 볼트 폴더에 직접 접근하지 못하는 환경(다른 PC, 팀원 계정, Claude.ai 웹)에서도 "누가/어느 프로젝트가 지금 어떤 상태인지"를 바로 조회할 수 있습니다. 볼트 원문 대신 구조화된 관계만 보므로 3-A보다 빠르고 정확하지만, 노트 본문 자체를 읽거나 수정하지는 못합니다(읽기 전용, 그래프 조회만).

**1) 토큰 발급** (서버를 띄우는 PC에서 한 번만):
```bash
meeting-minutes mcp-token --name 홍길동
```
출력되는 토큰은 **이 화면에만 한 번** 나옵니다 — API 키처럼 보관하세요. `config.json`의 `mcp.allowed_tokens`에 저장되며, 여기 없는 토큰은 전부 거부됩니다(기본값은 빈 목록 = 아무도 접근 불가).

**2) 서버 노출**: `python run_meeting.py web` 실행 시(또는 패키징된 `MeetingMinutes.exe` 실행 시에도) `/mcp`가 함께 서빙됩니다. 팀 외부에서 접근하려면 Cloudflare Tunnel 등으로 `/mcp`만(다른 API는 제외) 외부에 노출하는 걸 권장합니다.

> `/mcp` 서빙은 **exe 대체 빌드**에서만 지원됩니다(`fastmcp` 포함). 기본 포터블 배포판(`MeetingMinutes.bat`)은 UI/API는 동일하게 띄우지만 `fastmcp`를 제외하므로, `/mcp` 원격 커넥터가 필요하면 PyInstaller exe 빌드를 쓰세요.

**3) Claude 쪽 등록**: Claude 데스크톱 앱 → Customize → Connectors → **Add custom connector** → `<서버 주소>/mcp` 입력 → Authorization에 `Bearer <발급받은 토큰>` 입력.

**시킬 수 있는 일** (그냥 말로):
- "GraphDB-온톨로지 프로젝트 지금 상태 어때?" (미해결 액션·최근 결정·관련 회의 한 번에)
- "서지훈 교수 언급된 회의 다 찾아줘"
- "이 결정사항이랑 저 주제가 어떻게 연결돼있어?"

> 요약: **3-A는 볼트 원문을 통째로 넘겨서 Claude가 알아서 뒤지게** 하고, **3-B는 이미 구조화된 관계를 도구로 직접 조회**합니다. 둘 다 켜놔도 무방합니다(용도가 다름).

---

## 4. 켜고 끄기 / 메일 주소 바꾸기

모두 `config.json` 한 파일에서 바꿉니다.

```jsonc
"models":   { "llm": "claude" },          // "gpt"로 바꾸면 GPT로 작성
"email":    { "recipient": "받는사람@회사.com" },
"notify":   { "on_finish": "email" },      // null 로 바꾸면 메일 자동발송 끔
"obsidian": {
  "enabled": true,
  "vault_path": "D:\\Obsidian\\MyVault",
  "meetings_path": "{project}/01_회의_세미나/회의별/{year}",
  "transcripts_path": "{project}/01_회의_세미나/전사/{year}",
  "project_domains": { "양자": "Archive/도메인_아카이브" }
}, // 도메인별 아카이브 구조에 회의록/전사 저장. meetings_path를 비우면 00_Meetings/<도메인>/ 사용
// auto_route_enabled=true면 --project 없이도 제목/내용으로 도메인이 자동 결정됨
"realtime": { "email_on_finish": true }    // 실시간 녹음 후 메일 자동발송
```

> Obsidian을 꺼도(`enabled:false`) 회의록 파일·메일은 그대로 동작합니다.

요약과 회의록은 분리됩니다.
- `한눈에 보는 요약`: 결론·결정·리스크·다음 액션만 짧게
- `회의록`: 안건별 상세 논의·근거·수치·미정 사항

자세한 기준: [`출력_구조_저장경로_요약회의록.md`](출력_구조_저장경로_요약회의록.md)

---

## 5. 새 PC에서 처음 설치할 때만 (접어두기)

<details>
<summary>처음 세팅 5단계 (이미 된 PC는 무시)</summary>

1. **패키지**: `pip install -e .` (pyproject.toml 기반, `meeting-minutes` 커맨드 등록됨) — 저장소만
   clone해서 쓰려면 `pip install -r requirements.txt`도 가능
2. **최초 설정**: `meeting-minutes init` (대화형 — vault 경로/API 키 입력 + 연결 확인까지 자동),
   또는 수동으로 `config.example.json` → `config.json` 복사 후 OpenAI·Anthropic 키 입력
3. **Obsidian 플러그인**: Obsidian → 설정 → 커뮤니티 플러그인 → **"Local REST API"** 설치·활성화 → **API Key 복사**
4. **연결**: (수동 설정 시) `config.json` 의 `obsidian.api_key` 에 그 키 붙여넣기 (`.mcp.json` 에도 — Claude Code용).
   `meeting-minutes init`을 썼다면 이미 물어봤을 것.
5. **볼트 폴더 생성**: `meeting-minutes obsidian --init-vault` (또는 `python run_meeting.py obsidian --init-vault`)

확인: `meeting-minutes obsidian --ping` (또는 `python run_meeting.py obsidian --ping`)

새 팀/새 PC 설치 절차 전체(배포 채널, 격리 확인, 체크리스트)는 [`docs/SETUP_NEW_TEAM.md`](SETUP_NEW_TEAM.md) 참고.
</details>

---

## 6. 안 될 때

| 증상 | 해결 |
|------|------|
| `✗ 연결 실패` | Obsidian 실행 중인지 + Local REST API 플러그인 켜졌는지 확인 |
| 회의록이 비거나 401 | `config.json` API 키 확인 |
| Claude 404(model) | `config.json`의 `claude_model`을 유효한 값(예: `claude-opus-4-8`·`claude-sonnet-5`·`claude-haiku-4-5`)으로 변경 |
| 메일 안 옴 | `config.json` `email` 의 sender/password(앱 비밀번호)/recipient 확인 |
| 한글 깨짐(Windows) | 명령 앞에 `set PYTHONUTF8=1` |
| Obsidian에 저장 안 됨 | `python run_meeting.py obsidian --ping` 먼저 확인 |
| MCP 커넥터 401/거부 | 토큰이 `config.json`의 `mcp.allowed_tokens`에 있는지, `Bearer ` 접두사 포함해서 넣었는지 확인 |

---

**비밀 파일**(`config.json`, `.mcp.json`)은 git에 안 올라갑니다. 새 PC엔 `.example` 파일을 복사해 키만 채우세요.
