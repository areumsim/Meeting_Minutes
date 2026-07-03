# 옵시디언 녹음 → 요약 → 메일 자동화

옵시디언 코어 **"음성 녹음"** 플러그인은 오디오 파일만 저장합니다(전사·요약 없음).
이 도구가 그 뒤를 이어 **STT → 회의록·요약·액션 → 사실 검증 → Wiki Context/Proposal → 메일 발송**까지 처리합니다.

세 가지 경로가 있습니다. 모두 같은 `config.json` email 설정을 사용하지만, 저장/병합 방식은 다릅니다.

> 아래 예시는 모두 `python run_meeting.py ...` 형태입니다 — `pip install -e .`로 설치했다면
> `meeting-minutes ...`로 동일하게 실행할 수 있습니다(예: `meeting-minutes batch ...`).

---

## 경로 A — vault-audio (옵시디언 녹음에 권장)

노트에 임베드된 녹음(`![[녹음.m4a]]`)을 찾아 **그 노트에 회의록을 병합** + 메일 발송.

```bat
python run_meeting.py vault-audio --vault "D:\Claude\QC" --notify email
```

또는 `scripts/windows/run_vault_audio_email.bat` 더블클릭.

- 노트 frontmatter에 `audio_processed` 표시 → **중복 처리 방지**
- ⚠ **전제: 녹음이 노트 안에 임베드돼 있어야 함.** 옵시디언에서 회의 노트를 연 상태로 녹음하면
  자동으로 `![[...m4a]]`가 삽입됩니다. 볼트 루트에 그냥 떨어진 파일은 매칭되지 않습니다(경로 B 사용).

## 경로 B — batch/process (낱개 파일)

임베드 여부와 무관하게 **특정 파일 하나**를 처리 + 메일 발송.

```bat
python run_meeting.py batch "D:\Claude\QC\2026-06-18 주간보고.m4a" --notify email
```

→ `output/` 폴더에 회의록·요약 저장 + (옵션) Obsidian 발행 + 메일 발송.
`python run_meeting.py process ...`도 호환 명령으로 유지되며, `--resume`, `--force-stt`, `--debug` 같은 batch 옵션을 하위 파이프라인으로 전달합니다.

STT가 이미 있고 회의록/요약만 다시 만들 때:

```bat
python run_meeting.py batch "D:\Claude\QC\2026-06-18 주간보고.m4a" --resume
```

`--resume`은 기존 `segments.json` 또는 `transcript.md`가 있을 때만 STT를 건너뜁니다. 새 전사가 필요하면 `--force-stt`를 명시합니다.

> 옵시디언 녹음이 노트에 임베드 안 돼서 A가 안 잡히면 → B로 그 파일을 직접 넣으면 됩니다.

## 경로 C — ingest/watch (자동 수집)

녹음 폴더를 계속 감시하거나, 특정 파일을 자동 파이프라인과 같은 방식으로 처리합니다.

```bat
python run_meeting.py ingest "D:\Recordings\회의.m4a" --title "주간보고" --no-email
python run_meeting.py watch --folders "D:\Recordings"
```

- `ingest`는 관련 Obsidian 노트를 찾아 위키링크를 붙이고, Obsidian 연결 실패 시 `output/`에 저장합니다.
- `watch`는 처리 상태를 `data/processed_audio.json`에 기록해 중복 처리를 막습니다.
- 실패 파일은 `python run_meeting.py audio-watcher --folders "D:\Recordings" --reprocess "D:\Recordings\회의.m4a"`로 재처리 상태를 초기화합니다.

---

## 메일 설정 (config.json `email`)

현재는 Gmail로 발송 중입니다. **회사 Outlook(Microsoft 365)** 로 보내려면:

```json
"email": {
  "sender":    "you@example.com",
  "password":  "<Outlook 앱 비밀번호 또는 계정 비밀번호>",
  "recipient": "you@example.com",
  "smtp_host": "smtp.office365.com",
  "smtp_port": 587
}
```

- `smtp_host`를 비우면 발신자 도메인으로 자동 추정합니다(gmail / naver / outlook 인식).
- ⚠ 회사 테넌트가 **SMTP AUTH(기본 인증)** 를 막아두면 로그인이 실패합니다.
  이 경우 IT에 **"SMTP AUTH 허용"** 또는 **"앱 비밀번호 발급"** 을 요청하세요.
  (막혀 있으면 지금처럼 Gmail 발신 → 회사메일 수신으로 두는 것도 방법입니다.)

---

## 전체 스크립트(축어록)는 어디에 저장되나

처리하면 파일별로 `output/<날짜>_<제목>/` 폴더에 다음이 저장됩니다:

| 파일 | 내용 |
| --- | --- |
| `script.md` | **전체 축어록**(verbatim, 타임스탬프 단위 세그먼트) |
| `refined_script.txt` 또는 `script_refined.txt` | 오탈자·고유명사 교정본(처리 경로에 따라 파일명이 다를 수 있음) |
| `segments.json` | STT 원시 세그먼트(start/end/speaker/text) |
| `minutes.md` / `summary.md` / `actions.md` | 회의록 / 요약 / 액션 |
| `fact_check.md` 또는 `*_fact_check.md` | Vault 근거 기반 사실 검증 결과(경로별 파일명 차이 있음) |
| `wiki_context.json` | 회의록 생성에 사용된 관련 노트·근거·레지스트리 컨텍스트 |
| `* wiki_proposal.md/json` | 관련 Wiki 노트 업데이트 후보(자동 반영 안 함) |

옵시디언 노트에는 **한눈에 보는 요약·회의록·액션·사실 검증**이 들어갑니다. 전체 STT는 기본적으로 회의록 본문에 붙이지 않고, `obsidian.transcripts_path/yymmdd 제목 - 전사.md` 별도 노트/파일로 보관합니다.
회의록 파일명도 `yymmdd 제목.md` 형식입니다. 예: `260627 260627_5.md`.
batch/process 회의록 frontmatter에는 `session_date`, `source_audio`, `source_file_date`, `processed_at`, `stt_source`, `stt_segment_count`, `refined_ratio` 같은 처리 메타데이터가 기록됩니다.
ingest recording note에는 `source_audio`, `source_file_date`, `processed_at`, `stt_source`, `stt_segment_count`가 기록됩니다. `vault-audio`는 녹음이 들어 있던 기존 노트에 병합하므로 기존 frontmatter를 보존하고 `audio_processed`로 중복 처리를 막습니다.
`wiki_proposal.md/json`은 사람이 검토한 뒤 Obsidian 원본 Wiki에 반영할 후보입니다. 시스템이 원본 Wiki를 자동 수정하지 않습니다.
본문에 직접 붙이고 싶으면 `config.json`의 `obsidian.transcript_mode`를 `"append"`로 바꾸고, 저장하지 않으려면 `"off"`로 둡니다.

저장 경로 기준:
- QC 아카이브 회의록 정식 위치: `D:\Claude\QC\도메인_아카이브\01_회의_세미나\회의별\<연도>`
- QC 아카이브 전사 정식 위치: `D:\Claude\QC\도메인_아카이브\01_회의_세미나\전사\<연도>`
- 현재 설정 확인: `python run_meeting.py obsidian --where`
- 자세한 기준: [`출력_구조_저장경로_요약회의록.md`](출력_구조_저장경로_요약회의록.md)

메일 첨부 기준:
- `.md` 파일은 메일 클라이언트에서 한글이 깨질 수 있어 기본적으로 UTF-8 `.txt`로 변환 첨부합니다.
- 상세 회의록, 요약본, 액션, STT 원본(`script.md`/`transcript.md`/`segments.json`), STT 교정본, `wiki_context.json`, `wiki_proposal.md/json`, 사실검증 파일을 가능한 한 모두 첨부합니다.
- 설정: `email.markdown_attachment = "txt"`
- Markdown 확장자를 꼭 유지하려면 `"markdown"`으로 바꿀 수 있습니다.

## 지어내지 않기 — "모르면 미정" 원칙

회의록·요약 생성 프롬프트를 다음과 같이 고정했습니다:

- 참석자·담당자는 **노트 속성(frontmatter)에 입력된 명단만** 사용
- 명단에 없거나 화자를 특정할 수 없으면 **"미정"** — `발언자 A/B/C`·가상 팀명·역할을 **만들지 않음**
- 스크립트·메모에 없는 사실/수치/기한은 생성 금지
- `한눈에 보는 요약`은 결론·결정·리스크·다음 액션만 짧게 작성
- 본문 `회의록`은 안건별 상세 논의·근거·미정 사항을 기록

**중요**: 그러므로 회의 전에 노트 속성에 참석자를 넣어두고, **그 노트에 녹음을 임베드**해서 처리해야 실명이 들어갑니다.
명단 없이 처리하면 참석자는 전부 "미정"으로만 나옵니다(지어내지 않음).

화자별 구분이 필요하면 `config.json` 의 `models.stt` 를 `gpt-4o-transcribe-diarize` 로 바꾸세요
(참석자 명단이 자동으로 화자 이름 힌트로 전달됩니다). Realtime API 자체는 기본 화자분리를 제공하지 않으므로, 실시간 경로는 종료 후 추론/후처리로 보강합니다.

## 자동 반복 (Windows 작업 스케줄러)

매일/매주 자동 실행하려면:

1. `Win + R` → `taskschd.msc` 실행
2. **작업 만들기** → 트리거: 원하는 시간(예: 매주 월 09:30)
3. 동작: **프로그램 시작** → `scripts/windows/run_vault_audio_email.bat` 선택
4. "가장 높은 권한으로 실행" 체크 권장

그러면 옵시디언에서 녹음만 해두면, 정해진 시각에 자동으로 요약되어 메일로 도착합니다.
