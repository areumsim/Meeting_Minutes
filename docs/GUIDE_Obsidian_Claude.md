# 회의록 자동화 — 쉬운 사용 설명서

녹음만 하면 **Claude가 회의록을 쓰고 → 관련 Wiki를 찾아 반영하고 → 사실 검증과 Wiki 업데이트 후보를 남기고 → Obsidian에 정리하고 → 메일로 보냅니다.**

```
🎙️ 녹음/업로드 ──▶ 📝 회의록(Claude) ──▶ 🔎 Wiki 컨텍스트 ──▶ ✅ 사실 검증 ──▶ 📚 Obsidian 저장 ──▶ ✉️ 메일
```

---

## ✅ 사용 전 확인

아래 항목이 설정되어 있으면 녹음 파일 처리 후 Obsidian 저장과 메일 발송까지 자동으로 이어집니다.

| 기능 | 확인할 설정 |
|------|------|
| Claude로 회의록 작성 | `models.llm = "claude"` 및 Anthropic API 키 |
| 용어·인물·기업 자동 검색·설명 | Obsidian 연결 및 후처리 설정 |
| Vault 사실 검증 | `wiki.claim_verify = true` |
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

주의: 현재 웹/모바일 Recorder의 direct OpenAI 경로는 기기 로컬 저장 중심이라 Obsidian/Wiki/사실검증 파이프라인을 우회합니다. 운영 기록으로 남길 회의는 `batch`, `ingest`, CLI 실시간 또는 서버 `/ws/realtime` 경로를 사용하세요.

### 방법 C — 실시간 마이크 녹음
```bash
python run_meeting.py realtime --language ko
```
→ 말이 끝나고 `q`+Enter 누르면 회의록이 만들어집니다.

설정이 켜져 있으면 batch/ingest/CLI 실시간/server realtime 경로는 처리 후 Obsidian 저장, 사실검증, Wiki Context/Proposal, 메일 발송을 시도합니다. Obsidian 연결이 없으면 파일 출력은 유지되고, 저장 단계만 건너뜁니다.

---

## 2. 결과는 어디로 가나

1. **Obsidian 볼트** (가장 중요)
   - `obsidian.meetings_path/yymmdd 제목.md` — 회의록. QC 아카이브 예: `도메인_아카이브/01_회의_세미나/회의별/{year}/260627 제목.md`
   - `obsidian.transcripts_path/yymmdd 제목 - 전사.md` — 전체 STT 전사. QC 아카이브 예: `도메인_아카이브/01_회의_세미나/전사/{year}/260627 제목 - 전사.md`
   - `obsidian.meetings_path`가 비어 있으면 `00_Meetings/<도메인>/yymmdd 제목.md` — 회의록 (프로젝트 미설정 시 `기타/`)
   - batch/process 회의록 frontmatter에는 `session_date`, `session_dt`, `source_file_date`, `source_audio`, `processed_at`, `stt_source`, `stt_segment_count`, `refined_ratio`가 기록됩니다.
   - ingest recording note도 `source_audio`, `source_file_date`, `processed_at`, `stt_source`, `stt_segment_count`를 기록합니다. vault-audio는 기존 노트에 병합하므로 기존 frontmatter를 우선 보존합니다.
   - Vault 근거 검증이 켜져 있으면 본문에 `## 사실 검증` 섹션이 추가되고 판정·신뢰도·근거 노트가 표시됩니다.
   - 처리 폴더에는 `wiki_context.json`, 관련 노트가 있을 때 `wiki_proposal.md/json`, 실시간/서버 경로에서는 `*_fact_check.md` 또는 웹 `Fact Check` 탭이 남습니다.
   - `01_References/People|Companies/이름.md` — 인물·기업 설명 노트 (종류별, 프로젝트 무관 공유)
   - `01_References/<도메인>/용어.md` — 용어·기술 노트 (프로젝트 미설정 시 `공통/`)
   - **도메인** = `config.json`의 `obsidian.project` (여러 프로젝트를 묶으려면 `obsidian.project_domains` 매핑). 단, `meetings_path`가 있으면 회의록은 그 경로를 우선 사용합니다.
   - Obsidian 앱의 **그래프 뷰**에서 회의록 ↔ 용어가 이어진 걸 볼 수 있습니다.
2. **메일** — 설정된 주소로 회의록·요약이 첨부되어 발송
3. **로컬 폴더** `output/날짜_제목/` — 회의록·요약·전사·액션·사실검증·Wiki Context/Proposal 파일 (백업/검토용)

---

## 3. Claude Cowork로 더 활용하기

**Claude Cowork**는 Claude 데스크톱 앱의 비서입니다(개발용 Claude Code와 다름). 볼트에 쌓인 회의록을 **사람처럼 뒤지고 정리**해 줍니다.

**연결**: Claude 데스크톱 앱 → Cowork → "폴더 접근 권한"에서 **Obsidian 볼트 폴더**를 선택. (끝)

**시킬 수 있는 일** (그냥 말로):
- "지난달 그래프DB 회의들 결정사항만 모아줘"
- "내일 OO 회의 사전 브리핑 노트 만들어줘"
- "미완료 액션아이템 담당자별로 정리해줘"
- "비슷한 용어 노트끼리 링크 걸어줘"

> 요약: **파이프라인이 회의록을 만들어 볼트에 넣고**, **Cowork가 볼트를 관리**합니다.

---

## 4. 켜고 끄기 / 메일 주소 바꾸기

모두 `config.json` 한 파일에서 바꿉니다.

```jsonc
"models":   { "llm": "claude" },          // "gpt"로 바꾸면 GPT로 작성
"email":    { "recipient": "받는사람@회사.com" },
"notify":   { "on_finish": "email" },      // null 로 바꾸면 메일 자동발송 끔
"obsidian": {
  "enabled": true,
  "vault_path": "D:\\Claude\\QC",
  "meetings_path": "도메인_아카이브/01_회의_세미나/회의별/{year}",
  "transcripts_path": "도메인_아카이브/01_회의_세미나/전사/{year}"
}, // QC 아카이브 구조에 회의록/전사 저장. meetings_path를 비우면 00_Meetings/<도메인>/ 사용
// 여러 프로젝트를 한 폴더로 묶으려면: "project_domains": { "백서온톨로지": "GraphDB-온톨로지" }
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

1. **패키지**: `pip install -r requirements.txt` (httpx 포함)
2. **키 입력**: `config.example.json` → `config.json` 복사 후 OpenAI·Anthropic 키 입력
3. **Obsidian 플러그인**: Obsidian → 설정 → 커뮤니티 플러그인 → **"Local REST API"** 설치·활성화 → **API Key 복사**
4. **연결**: `config.json` 의 `obsidian.api_key` 에 그 키 붙여넣기 (`.mcp.json` 에도 — Claude Code용)
5. **볼트 폴더 생성**: `python run_meeting.py obsidian --init-vault`

확인: `python run_meeting.py obsidian --ping`
</details>

---

## 6. 안 될 때

| 증상 | 해결 |
|------|------|
| `✗ 연결 실패` | Obsidian 실행 중인지 + Local REST API 플러그인 켜졌는지 확인 |
| 회의록이 비거나 401 | `config.json` API 키 확인 |
| Claude 404(model) | `config.json`의 `claude_model`을 `claude-sonnet-4-6`로 |
| 메일 안 옴 | `config.json` `email` 의 sender/password(앱 비밀번호)/recipient 확인 |
| 한글 깨짐(Windows) | 명령 앞에 `set PYTHONUTF8=1` |
| Obsidian에 저장 안 됨 | `python run_meeting.py obsidian --ping` 먼저 확인 |

---

**비밀 파일**(`config.json`, `.mcp.json`)은 git에 안 올라갑니다. 새 PC엔 `.example` 파일을 복사해 키만 채우세요.
