# CLI 레퍼런스

**이 문서가 CLI 옵션의 정본이다.** [`../Readme.md`](../Readme.md) 에 있던 CLI 절 9개를 그대로
옮긴 것이며(2026-08-05, Readme 가 1,453줄로 커져 랜딩 문서 역할을 못 하게 된 것이 이유다),
내용은 한 줄도 고치지 않았다.

- 화면(웹 UI) 사용법 → [`USER_GUIDE.md`](USER_GUIDE.md)
- 코드 구조·파이프라인 → [`ARCHITECTURE.md`](ARCHITECTURE.md)
- 설정 항목 전체 → 앱 **[설정]** 화면과 [`../config.example.json`](../config.example.json)
  (각 키에 `_comment` 주석이 있다). 금액·단가의 계산 정본은
  `meeting_minutes_app/common/pricing.py` 다.

> 명령 진입점은 `python run_meeting.py <cmd>` (또는 `run_meeting.bat`) 하나다 —
> `meeting_minutes_app/cli.py` 가 디스패처다.

---

## 사용법 (`run_meeting.py batch`)

### 기본

```bash
python run_meeting.py batch meeting.mp4
```

### 제목 지정

```bash
python run_meeting.py batch meeting.mp4 --title "2025 Q1 정기회의"
```

### 문서 타입

```bash
python run_meeting.py batch seminar.webm --type seminar     # 세미나
python run_meeting.py batch lecture.mp4  --type lecture     # 강의
```

### 다중 파일

```bash
python run_meeting.py batch file1.mp4 file2.webm file3.mp3
python run_meeting.py batch *.webm --type seminar
python run_meeting.py batch *.mp4 --title "시리즈강의"       # → 시리즈강의_01_xxx, ...
```

### 영어 → 한국어

```bash
python run_meeting.py batch talk_en.mp4 --translate
python run_meeting.py batch talk_en.mp4 --translate --translate-script   # 스크립트도
```

### 프로필 적용

```bash
python run_meeting.py batch meeting.mp4 --profile meeting_ko      # 한국어 회의
python run_meeting.py batch seminar.webm --profile seminar         # 세미나 (영→한)
python run_meeting.py batch lecture.mp4  --profile lecture         # 강의 (영→한)
python run_meeting.py profiles list                                          # 프로필 목록
```

### 화자 수정 (캐시 연동)

```bash
# 1차 실행 후 화자명 변경 → 자동 저장
python run_meeting.py batch meeting.mp4 --edit-speakers
# 동일 회의 재실행 시 저장된 매핑 자동 재사용
python run_meeting.py batch meeting.mp4 --reuse-speakers
```

### 메모 반영

```bash
python run_meeting.py batch meeting.mp4 --memo notes.txt
```

### LLM에 추가 지시

```bash
python run_meeting.py batch seminar.webm --type seminar --custom-prompt "NVIDIA GPU 기술 중심으로 정리"
```

### 완료 알림

```bash
python run_meeting.py batch meeting.mp4 --notify email    # 이메일
python run_meeting.py batch meeting.mp4 --notify slack    # Slack
python run_meeting.py batch meeting.mp4 --notify teams    # Teams
```

### 비용 추정 (실행 안 함)

```bash
python run_meeting.py batch big_file.mp4 --estimate-cost
```

### 이어서 처리 (STT 건너뜀)

```bash
# STT는 완료됐는데 LLM 단계에서 실패한 경우
python run_meeting.py batch meeting.mp4 --resume
```

`--resume`은 기존 출력 폴더에서 `segments.json` 또는 `transcript.md`를 찾은 경우에만 STT를 건너뜁니다.
기존 STT가 없으면 새 STT를 몰래 실행하지 않고 중단합니다. 새 전사가 필요하면 명시적으로 실행합니다.

```bash
python run_meeting.py batch meeting.mp4 --force-stt
```

### SSL 문제 (회사/학교)

```bash
python run_meeting.py batch meeting.mp4 --ssl-no-verify
# 또는 config.json: "ssl": { "verify": false }
```

### 디버그 (콘솔 상세 출력)

```bash
python run_meeting.py batch meeting.mp4 --debug
# --debug 시 output/debug.log 생성 (상세 로그 + 중간 파일 저장)
```

---

## 전체 옵션 (meeting_minutes.py)

| 옵션 | 설명 | 기본값 |
| --- | --- | --- |
| `input` | 파일 경로 (여러 개, glob 가능) | - |
| `--title` | 제목 (출력 폴더명·문서 제목) | 원본 파일명 |
| `--type` | meeting / seminar / lecture | meeting |
| `--profile` | 저장된 프로필 이름 적용 | - |
| `--model` | STT 모델 | config `models.stt` 값 (코드 fallback: gpt-4o-mini-transcribe) |
| `--llm` | gpt / claude | gpt |
| `--language` | STT 언어 (ko, en) | ko |
| `--translate` | 영→한 번역 | OFF |
| `--translate-script` | 스크립트 번역본도 생성 | OFF |
| `--memo` | 메모 파일 | - |
| `--topic` | 회의 주제/맥락. 관련 노트 검색과 회의록 생성에 반영 | - |
| `--speakers` | 화자 이름 (쉼표구분) | 자동 |
| `--custom-prompt` | LLM 추가 지시 | - |
| `--resume` | 기존 `segments.json`/`transcript.md`가 있을 때만 STT 재사용 | OFF |
| `--force-stt` | 기존 STT/전사 결과가 있어도 새 STT 수행 | OFF |
| `--edit-speakers` | 화자 수정 모드 (캐시 저장) | OFF |
| `--reuse-speakers` | 화자 캐시 자동 적용 | OFF |
| `--estimate-cost` | 비용 추정만 | OFF |
| `--notify` | email / slack / teams 완료 알림 | - |
| `--no-notify` | config 자동 알림까지 포함해 이번 실행 알림 생략 | OFF |
| `--output-dir` | 출력 디렉토리 | ./output |
| `--ssl-no-verify` | SSL 우회 | OFF |
| `--debug` | 콘솔 상세 출력 | OFF |

---

## 명명 프로필 (profiles.py)

자주 쓰는 옵션 조합을 이름으로 저장해 재사용합니다.

**내장 프로필:**

| 프로필 | STT 모델 | 설명 |
| --- | --- | --- |
| `meeting_ko` | `gpt-4o-transcribe-diarize` | 한국어 회의 → 한국어 회의록 (배치 화자 분리) |
| `meeting_en2ko` | `gpt-4o-transcribe-diarize` | 영어 회의 → 한국어 번역 회의록 (배치 화자 분리) |
| `seminar` | `gpt-4o-transcribe-diarize` | 영어 세미나 → 한국어 세미나 기록 (배치 화자 분리) |
| `lecture` | `gpt-4o-transcribe` | 영어 강의 → 한국어 강의 노트 |

> `meeting_ko` / `meeting_en2ko` / `seminar` 프로필은 배치 파일 전사에서 `gpt-4o-transcribe-diarize` 모델을 사용하여 화자 분리 품질을 높입니다. Realtime 모드는 기본 화자분리 없음입니다.

```bash
python run_meeting.py profiles list                    # 전체 프로필 목록
python run_meeting.py profiles show meeting_ko         # 프로필 상세
python run_meeting.py profiles create my_profile      # 대화형 생성
python run_meeting.py profiles delete my_profile      # 삭제
```

커스텀 프로필은 `profiles.json`에 저장됩니다.
CLI 옵션이 프로필보다 항상 우선합니다.

---

## 화자 캐시 (speaker_cache.py)

`--edit-speakers` 로 입력한 화자 이름 매핑을 자동 저장하고,
같은 회의 재실행 시 제목 기반 퍼지 매칭으로 불러옵니다.

```bash
python run_meeting.py speaker-cache list               # 저장된 매핑 목록
python run_meeting.py speaker-cache delete "주간회의"   # 특정 매핑 삭제
```

매핑 파일 위치: `output/speaker_map.json`

**동작 순서:**

1. `--edit-speakers` 실행 → 화자명 입력
2. 매핑이 회의 제목 키로 `speaker_map.json`에 저장
3. 같은 제목의 회의 재실행 시 `--reuse-speakers` 로 자동 적용

**화자 이름 자동 추론 (`infer_speaker_names`):**

`gpt-4o-transcribe-diarize` 모델로 전사 시 화자가 "Speaker A", "Speaker B" 등으로 표기되는 경우,
LLM이 발화 내용·맥락을 분석해 실명 또는 역할명으로 자동 변환합니다.
명확하게 추론 불가능한 화자는 "화자 A" 등 한국어 임시명을 유지합니다.

---

## 알림 설정 (notifier.py)

회의록 생성 완료 후 Email / Slack / Teams 로 자동 공유합니다.

### 이메일

`config.json`에 설정:

```json
"email": {
  "sender":    "sender@naver.com",
  "password":  "앱 비밀번호",
  "recipient": "recipient@company.com"
}
```

또는 환경변수:

```bash
EMAIL_SENDER     = sender@naver.com
EMAIL_PASSWORD   = 앱비밀번호
EMAIL_RECIPIENTS = recipient@company.com   # 쉼표로 여러 명 가능
```

### Slack / Teams

```bash
SLACK_WEBHOOK_URL = https://hooks.slack.com/services/...
TEAMS_WEBHOOK_URL = https://...webhook.office.com/...
```

또는 `config.json`:

```json
"notify": {
  "slack": { "webhook_url": "https://hooks.slack.com/..." },
  "teams": { "webhook_url": "https://...webhook.office.com/..." }
}
```

```bash
# 단독 테스트
python run_meeting.py notifier
```

---

## 폴더 자동 감시 (watcher.py)

지정 폴더에 음성/영상 파일이 들어오면 자동으로 `meeting_minutes.py`를 실행합니다.

```bash
pip install watchdog        # 최초 1회

python run_meeting.py legacy-watcher ./recordings                           # 기본 감시
python run_meeting.py legacy-watcher ./recordings --profile seminar         # 프로필 적용
python run_meeting.py legacy-watcher ./recordings --notify slack            # 완료 시 Slack 알림
python run_meeting.py legacy-watcher ./recordings --no-move                 # 처리 후 파일 이동 안 함
python run_meeting.py legacy-watcher ./recordings --type seminar            # 문서 타입 지정
python run_meeting.py legacy-watcher ./recordings --translate               # 영→한 번역 활성화
python run_meeting.py legacy-watcher ./recordings --ssl-no-verify           # SSL 우회
```

**전체 옵션:**

| 옵션 | 설명 | 기본값 |
| --- | --- | --- |
| `folder` | 감시할 폴더 경로 | - |
| `--profile` | 저장된 프로필 이름 적용 | - |
| `--notify` | email / slack / teams 완료 알림 | - |
| `--no-move` | 처리 후 파일 이동 안 함 | OFF |
| `--type` | meeting / seminar / lecture | meeting |
| `--translate` | 영→한 번역 | OFF |
| `--ssl-no-verify` | SSL 우회 | OFF |
| `--script` | 내부 배치 처리 스크립트 경로 | 자동 탐색 |

**동작:**

- 새 파일 감지 → 5초 안정화 대기 (대용량 복사 완료 대기) → `meeting_minutes.py` 실행
- 처리 완료 파일은 `_processed/` 하위 폴더로 이동
- 실패 시 `파일명.error.txt` 생성
- 시작 시 기존 미처리 파일도 일괄 처리 여부 선택 가능

---

## 실시간 녹취 (realtime_transcription.py)

마이크 입력을 실시간으로 전사하고 완료 후 자동으로 회의록을 생성합니다.

### 터미널 UI 레이아웃

녹음 중 화면은 3개 영역으로 고정 배치됩니다:

```text
┌─────────────────────────────────────────────────────────────────┐  ← Row 1 [고정]
│  🤝 실시간 회의록 녹취   ⬤ 03:21  │  ~$0.032  │  gpt-4o-transcribe  │
├─────────────────────────────────────────────────────────────────┤  ← Row 2 [고정]
│                                                                 │
│  [00:04] Good morning everyone, let's get started.             │
│  [00:04] 안녕하세요, 시작하겠습니다.                              │
│  [00:18] Today we'll review Q1 results and discuss strategy.   │  ← 중간 [스크롤]
│  [00:18] 오늘은 Q1 실적을 검토하고 전략을 논의하겠습니다.           │
│  ...                                                           │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤  ← Row N [고정]
│  ⠋ 녹음 중...  ▐████░░░░▌  q→종료   p→일시정지   s→스크롤잠금  │
└─────────────────────────────────────────────────────────────────┘
```

| 영역 | 내용 | 갱신 주기 |
| --- | --- | --- |
| **Row 1** | 제목 · 이모지 · 경과시간 · 예상비용 · STT 모델명 · 발화건수 | ~0.6초 |
| **Row 2** | 구분선 | 고정 |
| **Row 3~N-1** | 실시간 전사 텍스트 (스크롤 가능) | 발화 즉시 |
| **Row N** | 녹음 상태 · 오디오 레벨 바 · 명령어 안내 | ~0.12초 |

### 키보드 명령어

| 입력 | 동작 |
| --- | --- |
| `q` + Enter | 녹음 종료 → 회의록 생성 시작 |
| `p` + Enter | 녹음 일시정지 |
| `r` + Enter | 일시정지 해제 (재개) |
| `s` + Enter | **스크롤 잠금 토글** (아래 참고) |
| Ctrl+C | 강제 종료 (회의록 생성 시작) |

#### 스크롤 잠금 (`s` 명령어)

녹음 중 이전 대화를 확인하고 싶을 때 사용합니다.

1. `s` + Enter → 🔒 스크롤 잠금 활성화
   - 새 전사 텍스트가 화면에 출력되지 않고 내부 버퍼에 저장됩니다
   - 터미널 마우스 휠 또는 스크롤바로 위쪽 대화를 자유롭게 확인할 수 있습니다
   - 하단 인디케이터에 버퍼된 발화 건수가 표시됩니다
2. `s` + Enter → 🔓 스크롤 잠금 해제
   - 버퍼에 쌓인 텍스트가 한 번에 출력됩니다
   - 실시간 출력이 재개됩니다

### 클래스 구조

**공통 클래스** (`realtime_transcription.py`):

| 클래스 | 역할 |
| --- | --- |
| `AudioBackup` | 전체 세션 오디오를 PCM 파일로 연속 백업 (HTTP: 16kHz, WS: 24kHz) |
| `SessionLogger` | 세그먼트를 JSONL + os.fsync로 즉시 디스크 기록 |
| `RecordingIndicator` | 고정 헤더(2줄) + 스크롤 영역 + 하단 인디케이터 관리. 스크롤 잠금 지원 |
| `RealtimeSession` | 전체 흐름 조율 — HTTP/WS 모드 자동 분기 → 종료 시 STT 교정 → 회의록·요약본 생성 |

**HTTP 모드 전용** (`realtime_transcription.py`):

| 클래스 | 역할 |
| --- | --- |
| `AudioRecorder` | sounddevice로 마이크 캡처 → N초 단위 청크로 큐에 적재 |
| `VADAudioRecorder` | AudioRecorder + webrtcvad — 침묵 감지 즉시 전송 |
| `RealtimeTranscriber` | 청크 → STT API (HTTP POST) → 타임스탬프 출력 → 번역(선택). 스크롤 잠금 중 버퍼링 처리 |

**WebSocket 모드 전용** (`ws_transcriber.py`):

| 클래스 | 역할 |
| --- | --- |
| `WebSocketAudioStreamer` | 마이크 24kHz 캡처 → base64 → queue → sender 스레드 → WebSocket 전송 |
| `WebSocketTranscriber` | 서버 이벤트 루프 — 서버 VAD 기반 전사 delta/completed 처리 + 번역 |

### 사용법

```bash
pip install sounddevice numpy websockets    # 최초 1회

# 기본 실행 — HTTP 모드 (영어 → 영어 회의록)
python run_meeting.py realtime-raw

# 한국어 회의
python run_meeting.py realtime-raw --language ko

# 영어 → 한국어 실시간 번역 + 한국어 회의록
python run_meeting.py realtime-raw --translate

# ★ WebSocket 모드 — ~1초 지연 실시간 전사
python run_meeting.py realtime-raw --mode ws --translate

# WebSocket + 세미나 모드
python run_meeting.py realtime-raw --mode ws --type seminar --translate

# HTTP 모드 — 청크 5초 (API 호출 횟수 줄여 비용 절감)
python run_meeting.py realtime-raw --chunk-duration 5

# 이전 세션 이어서 (타임스탬프 자동 연속)
python run_meeting.py realtime-raw --prev-session output/session_20250220_143022.jsonl

# 이전 세션 로그로 회의록 재생성 (재녹음 없이)
python run_meeting.py realtime-raw --recover output/session_20250220_143022.jsonl

# 완료 후 이메일 발송
python run_meeting.py realtime-raw --email
```

### 실행 흐름 — HTTP 모드 (기본)

1. 마이크에서 N초(기본 3초) 단위로 오디오 캡처 (16kHz)
2. 동시에 세션 오디오 전체를 `session_TS_audio.pcm`으로 백업 (크래시 대비)
3. 각 청크를 STT API로 HTTP POST 전송 → 전사 텍스트 수신 (3회 재시도)
4. 스크롤 잠금 해제 상태면 즉시 출력, 잠금 상태면 버퍼에 저장
5. `--translate` 시: 즉시 한국어로 번역하여 아래 줄에 출력
6. 세그먼트마다 JSONL + os.fsync로 즉시 디스크 기록
7. q+Enter 또는 Ctrl+C → 녹음 종료
8. PCM 파일을 WAV로 변환 후 삭제
9. **환각·반복 정화** — `text_filters.sanitize_transcript()`: 되풀이 축약·중복 제거 +
   이질 문자(중국어·일본어·키릴 등) `[불명]` 표시. 모든 경로가 거치는
   `finalize.run_post_session()` 진입부에서 1회 적용
10. **화자 이름 추론** — diarize 모델 사용 시 `infer_speaker_names()` 로 "Speaker A/B" → 실명/역할명 변환
11. **`refine_script()`** 로 전체 맥락·주제 기반 교정 스크립트 생성 (교정본이 회의록 입력으로 사용됨)
12. `generate_minutes()` / `generate_summary()` 로 회의록 + 요약 생성 (요약은 `.md` + `.txt` 이중 저장)
13. `build_script_md()` 로 화자 구분 정리 스크립트 생성 (`*_script.md`, 번역 시 `*_script_ko.md` 추가)

### 실행 흐름 — WebSocket 모드 (`--mode ws`)

1. OpenAI Realtime Transcription API에 WebSocket 연결
2. `transcription_session.update()`로 모델/언어/VAD/노이즈 리덕션 설정
3. 마이크에서 24kHz 오디오 연속 캡처 → base64 인코딩 → WebSocket으로 스트리밍
4. 동시에 `session_TS_audio.pcm`으로 백업
5. 서버 VAD가 발화를 감지하면 전사 이벤트 수신:
   - `speech_started` → 발화 시작 시간 기록
   - `transcription.delta` → 실시간 부분 텍스트를 즉시 화면에 스트리밍 출력
   - `transcription.completed` → 최종 텍스트 확정, 세그먼트 생성 (환각·반복 자동 필터)
6. `--translate` 시: 확정된 영어 텍스트를 즉시 한국어로 번역하여 아래 줄에 출력
7. 세그먼트마다 JSONL + os.fsync로 즉시 디스크 기록
8. q+Enter 또는 Ctrl+C → 녹음 종료 → WebSocket 연결 종료
9. PCM 파일을 WAV로 변환 후 삭제
10. **화자 이름 추론** — diarize 모델 사용 시 `infer_speaker_names()` 실행
11. **`refine_script()`** 로 전체 맥락·주제 기반 교정 스크립트 생성 (교정본이 회의록 입력으로 사용됨)
12. `generate_minutes()` / `generate_summary()` 로 회의록 + 요약 생성 (요약은 `.md` + `.txt` 이중 저장)
13. `build_script_md()` 로 화자 구분 정리 스크립트 생성

> WebSocket 연결 실패 시 자동으로 HTTP 모드로 전환합니다.

### 출력 파일 (실시간)

세션마다 타임스탬프 서브폴더에 저장됩니다.

```text
output/
├── .active_session                         # 크래시 감지 마커 (bat 전용)
└── realtime_20250220_143022/               # 세션 서브폴더
    ├── session_20250220_143022.jsonl               # 세션 로그 (크래시 복구용)
    ├── realtime_20250220_143022_minutes.md         # 회의록
    ├── realtime_20250220_143022_summary.md         # 요약본 (마크다운)
    ├── realtime_20250220_143022_summary.txt        # 요약본 (텍스트 — 이메일 첨부용)
    ├── realtime_20250220_143022_script.md          # 화자 구분 정리 스크립트
    ├── realtime_20250220_143022_script_ko.md       # 번역 스크립트 (--translate 시)
    ├── realtime_20250220_143022_transcript.txt     # 화자 포함 타임스탬프 전사 원문
    ├── realtime_20250220_143022_refined_script.txt # 맥락 기반 교정 스크립트
    ├── realtime_20250220_143022_meta.json          # 세션 메타데이터 + 비용 추정
    └── session_20250220_143022_audio.wav           # 오디오 백업 (정상 종료 시)
```

> **응답 지연 (HTTP 모드)**: 청크 길이(기본 3초) + STT API 처리 시간(~2-3초) = 약 5-6초.
> `--chunk-duration 5` 로 늘리면 API 호출 횟수가 줄어들지만 응답이 느려집니다.
>
> **응답 지연 (WebSocket 모드)**: 서버 VAD가 발화 종료를 감지하면 즉시 전사 → ~1초 이내 텍스트 표시.
> delta 이벤트로 발화 중에도 부분 텍스트가 실시간 스트리밍됩니다.

### 전체 옵션

| 옵션 | 설명 | 기본값 |
| --- | --- | --- |
| `--language` | ko / en / auto | 설정 `realtime.language`(기본 ko) |
| `--type` | meeting / seminar / lecture | meeting |
| `--topic` | 회의 주제 (번역·회의록·요약 프롬프트에 맥락으로 반영) | - |
| `--model` | STT 모델 (아래 표 참고) | config `models.stt` 값 |
| `--llm` | gpt / claude | gpt |
| `--translate` | 실시간 영→한 번역 | OFF |
| `--translate-model` | 번역 모델 (gpt-4o-mini / gpt-4o) | gpt-4o-mini |
| `--mode` | 전송 모드 (http / ws / auto) | config.json (기본: http) |
| `--chunk-duration` | 청크 길이, 초 — HTTP 모드 전용 | config.json (기본: 3.0) |
| `--vad` | VAD 동적 청크 — HTTP 모드 전용 (webrtcvad 필요) | OFF |
| `--memo` | 메모/노트 파일 (txt, md). 회의록·요약 생성 시 LLM에 반영 | - |
| `--email` | 완료 후 이메일 발송 | OFF |
| `--output-dir` | 출력 디렉토리 | ./output |
| `--recover` | JSONL로 회의록 재생성 | - |
| `--prev-session` | 이전 세션 이어붙이기 | - |
| `--ssl-no-verify` | SSL 우회 | OFF |

### 크래시 복구 (3중 보호)

| 보호 계층 | 저장 내용 | 복구 방법 |
| --- | --- | --- |
| **JSONL + fsync** | 전사 텍스트 (세그먼트 단위) | `--recover session_*.jsonl` |
| **`.active_session`** | 크래시 감지 마커 | bat 파일이 자동 감지 → 복구 메뉴 |
| **PCM 오디오 백업** | 전체 세션 원본 오디오 | ffmpeg 변환 → 재전사 가능 |

PCM 수동 변환:

```bash
# HTTP 모드 (16kHz)
ffmpeg -f s16le -ar 16000 -ac 1 -i output/session_TS_audio.pcm output/session_TS_audio.wav
# WebSocket 모드 (24kHz)
ffmpeg -f s16le -ar 24000 -ac 1 -i output/session_TS_audio.pcm output/session_TS_audio.wav
```

---

## scripts/windows/run_batch.bat (Windows 전용)

더블클릭 또는 파일 드래그앤드롭으로 실행합니다. 인자가 있으면 `run_meeting.py batch %*`를 통해
`meeting_minutes.py`에 바로 위임하고, 인자 없이 실행하면 기존 `run_batch.py` 인터랙티브 메뉴를 엽니다.
따라서 `--resume`, `--force-stt`, `--topic`, `--no-notify` 같은 batch 옵션을 그대로 사용할 수 있습니다.

### 실행 방법

| 방법 | 설명 |
| --- | --- |
| 더블클릭 | 인터랙티브 메인 메뉴 → 파일 경로 직접 입력 |
| 파일 드래그앤드롭 | bat 파일 위에 미디어 파일을 끌어놓으면 자동 감지 |
| 커맨드라인 | `scripts/windows/run_batch.bat file1.mp4 file2.webm` |

### 배치 메인 메뉴

```text
F  파일 경로 입력
     (또는 bat 위에 파일을 드래그)

D  폴더 선택  →  모든 미디어 파일 일괄 처리

W  감시 모드  →  폴더 모니터링 (자동 처리)

H  도움말
O  출력 폴더 열기
0  종료
```

### 처리 모드 선택 (파일/폴더 입력 후 표시)

```text
1  한국어 회의  →  한국어 회의록
2  영어 회의  →  한국어 회의록  (번역)  ★ 추천
3  영어 회의  →  영어 회의록
4  세미나  (영어 → 한국어 번역)
5  강의  (영어 → 한국어 번역)
6  한국어 세미나  →  한국어 기록
7  한국어 강의  →  한국어 강의 노트
0  취소
```

### 지원 파일 형식

음성: `.mp3` `.wav` `.m4a` `.ogg` `.flac` `.aac` `.wma`
영상: `.mp4` `.webm` `.mkv` `.avi` `.mov`

> 영상 파일은 오디오 트랙만 추출하여 처리합니다.

---

## scripts/windows/run_realtime.bat (Windows 전용)

더블클릭으로 실행. 시작 시 크래시 상태를 자동 감지합니다.

### 시작 시 자동 감지

| 감지 항목 | 설명 |
| --- | --- |
| `.active_session` | 이전 세션이 비정상 종료됨 → 복구 메뉴 표시 |
| `session_*_audio.pcm` | 오디오 백업이 변환되지 않고 남아있음 → PCM 복구 메뉴 표시 |

### 크래시 복구 메뉴

```text
1  이어서 녹취 후 하나의 회의록으로 합치기  ← 권장
2  이전 세션만으로 회의록 생성 (복구)
3  이전 세션 무시하고 새로 시작
```

### PCM 오디오 복구 메뉴

```text
1  ffmpeg으로 자동 변환 (전체)
2  output 폴더 열기
3  건너뛰고 계속 (나중에 수동 변환)
```

### 메인 메뉴

```text
1  한국어 회의  →  한국어 회의록                    $0.43/hr
2  영어 회의    →  한국어 회의록  (실시간 번역)     $0.44/hr  ★ 권장
3  영어 회의    →  영어 회의록                      $0.43/hr
4  세미나 / 발표  (영어 → 한국어, 실시간 번역)      $0.44/hr
5  강의  (영어 → 한국어, 실시간 번역)               $0.44/hr
6  한국어 세미나 / 발표  →  한국어 기록             $0.43/hr
7  한국어 강의  →  한국어 강의 노트                 $0.43/hr
H  도움말 / 설치 가이드
R  이전 세션 복구
O  출력 폴더 열기
0  종료
```

### 녹음 방식 선택 (모드 선택 후 표시)

```text
1  Standard   —  3초 고정 청크 (안정적)
     지연: 영어 4~6초  |  한국어 5~7초
2  VAD        —  침묵 감지 즉시 전송 (빠름)
     지연: 짧은 응답 2~3초  |  긴 문장 4~5초
3  WebSocket  —  실시간 스트리밍 (가장 빠름)
     지연: ~1초  |  서버 VAD + 노이즈 리덕션 내장
     비용: STT ~$0.01/min (Standard의 ~3배)
```

### 회의 주제 입력 (녹음 방식 선택 후 표시)

```text
  주제를 입력하면 번역·회의록·요약 품질이 향상됩니다.
  Enter만 누르면 건너뜁니다.

  주제 >>
```

입력한 주제는 실시간 번역 시스템 프롬프트, 회의록 생성, 요약본 생성 모두에 맥락으로 자동 반영됩니다.
CLI에서 직접 실행 시에는 `--topic "주제"` 옵션으로 지정할 수 있습니다.
