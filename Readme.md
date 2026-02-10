# 🎙️ Meeting Minutes Generator

음성/영상 파일에서 자동으로 **스크립트 + 기록문서 + 요약본**을 생성

## 주요 기능

| 기능 | 설명 |
|------|------|
| **다중 파일 배치** | `*.webm` 글로빙, 여러 파일 한번에 처리 |
| **3가지 문서 타입** | 회의록 / 세미나 기록 / 강의 노트 |
| **제목 지정** | `--title`로 출력 폴더명·문서 제목 지정 |
| **영→한 번역** | `--translate` 로 영어 음성 → 한국어 문서 |
| **GPT + Claude 폴백** | GPT-4o 실패 시 Claude 자동 전환 |
| **자동 재시도** | API 에러 시 3회 자동 재시도 |
| **이어서 처리** | `--resume` 으로 STT 건너뛰고 문서만 재생성 |
| **화자 사후 수정** | `--edit-speakers` 로 화자명 변경 → 재생성 |
| **비용 사전 추정** | `--estimate-cost` 로 실행 전 API 비용 확인 |
| **항상 로그** | `run.log` 자동 기록 (에러 추적용) |
| **설정 파일** | `config.json` 으로 반복 옵션 저장 |
| **SSL 우회** | 회사/학교 네트워크 지원 |
| **대용량 처리** | 170MB+ 영상도 자동 압축·분할 |
| **긴 스크립트** | LLM 컨텍스트 초과 시 자동 분할→통합 |
| **LLM 추가 지시** | `--custom-prompt`로 정리 방향 지정 |

## 출력 구조

실행할 때마다 **날짜+제목** 폴더가 자동 생성

### 단일 파일
```bash
python meeting_minutes.py nvidia.webm --title "NVIDIA세미나" --type seminar
```
```
output/
└── 2025-02-10_NVIDIA세미나/
    ├── script.md                 # 스크립트
    ├── script_ko.md              # 한국어 번역 (--translate-script)
    ├── minutes.md                # 세미나 기록
    ├── summary.md                # 요약본
    ├── segments.json             # STT 원본 (재사용/디버깅)
    ├── segments_translated.json  # 번역 세그먼트 (--translate)
    └── run.log                   # 실행 로그
```

### 단일 파일 (제목 미지정 → 파일명이 제목)
```bash
python meeting_minutes.py nvidia_세미나_1_발표.webm
```
```
output/
└── 2025-02-10_nvidia_세미나_1_발표/
    ├── script.md
    ├── minutes.md
    ├── summary.md
    └── ...
```

### 다중 파일 + 제목 → 하나의 폴더
```bash
python meeting_minutes.py part1.mp4 part2.mp4 --title "프로젝트킥오프"
```
```
output/
└── 2025-02-10_프로젝트킥오프/
    ├── 01_part1_script.md
    ├── 01_part1_minutes.md
    ├── 01_part1_summary.md
    ├── 01_part1_segments.json
    ├── 02_part2_script.md
    ├── 02_part2_minutes.md
    ├── 02_part2_summary.md
    ├── 02_part2_segments.json
    └── run.log
```

### 다중 파일 (제목 없음) → 파일별 폴더
```bash
python meeting_minutes.py *.webm --type seminar
```
```
output/
├── 2025-02-10_seminar_part1/
│   ├── script.md
│   ├── minutes.md
│   └── ...
└── 2025-02-10_seminar_part2/
    ├── script.md
    ├── minutes.md
    └── ...
```

## 설치

```bash
pip install -r requirements.txt
```

ffmpeg 설치:
- **Windows**: https://www.gyan.dev/ffmpeg/builds/ → PATH에 추가
- **macOS**: `brew install ffmpeg`
- **Linux**: `sudo apt install ffmpeg`

## API 키 설정

### 방법 1: 코드에 직접 입력
```python
OPENAI_API_KEY = "sk-proj-..."       # 필수
ANTHROPIC_API_KEY = "sk-ant-..."     # 선택 (폴백용)
```

### 방법 2: 환경변수
```bash
# Windows (PowerShell)
$env:OPENAI_API_KEY = "sk-proj-..."

# macOS/Linux
export OPENAI_API_KEY="sk-proj-..."
```

### 방법 3: 설정 파일
```bash
python meeting_minutes.py --init-config   # config.json 생성
# config.json 편집 후:
python meeting_minutes.py input.mp4 --config config.json
```

## 사용법

### 기본
```bash
python meeting_minutes.py meeting.mp4
# → output/2025-02-10_meeting/
```

### 제목 지정
```bash
python meeting_minutes.py meeting.mp4 --title "2025 Q1 정기회의"
# → output/2025-02-10_2025 Q1 정기회의/
```

### 문서 타입
```bash
python meeting_minutes.py seminar.webm --type seminar     # 세미나
python meeting_minutes.py lecture.mp4 --type lecture       # 강의
```

### 다중 파일
```bash
python meeting_minutes.py file1.mp4 file2.webm file3.mp3
python meeting_minutes.py *.webm --type seminar
python meeting_minutes.py *.mp4 --title "시리즈강의"
```

### 영어 → 한국어
```bash
python meeting_minutes.py talk_en.mp4 --translate
python meeting_minutes.py talk_en.mp4 --translate --translate-script
```

### 메모 반영
```bash
python meeting_minutes.py meeting.mp4 --memo notes.txt
```

### LLM에 추가 지시
```bash
python meeting_minutes.py seminar.webm --type seminar --custom-prompt "NVIDIA GPU 기술 중심으로 정리"
```

### 비용 추정 (실행 안 함)
```bash
python meeting_minutes.py big_file.mp4 --estimate-cost
```

### 이어서 처리 (STT 건너뜀)
```bash
# STT 완료 후 LLM 단계에서 실패한 경우 → 기존 폴더 자동 탐색, STT 비용 절약
python meeting_minutes.py meeting.mp4 --title "Q1회의" --resume
```

### 화자 수정
```bash
# 1차 실행 후 → 기존 폴더 자동 탐색
python meeting_minutes.py meeting.mp4 --title "Q1회의" --edit-speakers
# → Speaker 1 → 김팀장  등 대화형 수정 → 문서 재생성
```

### SSL 문제 (회사/학교)
```bash
python meeting_minutes.py meeting.mp4 --ssl-no-verify
# 또는 코드 상단: SSL_VERIFY = False
```

### 디버그 (콘솔 상세 출력)
```bash
python meeting_minutes.py meeting.mp4 --debug
# run.log는 항상 기록되므로, 에러 시 --debug 없이도 로그 확인 가능
```

## 전체 옵션

| 옵션 | 설명 | 기본값 |
|------|------|--------|
| `input` | 파일 경로 (여러 개, glob 가능) | - |
| `--title` | 제목 (출력 폴더명·문서 제목) | 원본 파일명 |
| `--type` | meeting / seminar / lecture | meeting |
| `--model` | STT 모델 | gpt-4o-mini-transcribe |
| `--llm` | gpt / claude | gpt |
| `--language` | STT 언어 힌트 (ko, en...) | 자동 |
| `--translate` | 영→한 번역 | OFF |
| `--translate-script` | 스크립트 번역본도 생성 | OFF |
| `--memo` | 메모 파일 | - |
| `--speakers` | 화자 이름 (쉼표구분, 최대 4명) | 자동 |
| `--custom-prompt` | LLM 추가 지시 | - |
| `--resume` | 기존 STT 재사용 (기존 폴더 자동 탐색) | OFF |
| `--edit-speakers` | 화자 수정 모드 (기존 폴더 자동 탐색) | OFF |
| `--estimate-cost` | 비용 추정만 | OFF |
| `--output-dir` | 출력 베이스 디렉토리 | ./output |
| `--config` | 설정 파일 | - |
| `--init-config` | config.json 생성 | - |
| `--ssl-no-verify` | SSL 우회 | OFF |
| `--debug` | 콘솔 상세 출력 | OFF |

## STT 모델 비교

| 모델 | 화자 분리 | 타임스탬프 | 비용/분 | 참고 |
|------|:---------:|:---------:|:-------:|------|
| `gpt-4o-transcribe-diarize` | ✅ | ✅ | $0.006 | 최고 품질 |
| `gpt-4o-transcribe` | ❌ | ❌ | $0.006 | |
| `gpt-4o-mini-transcribe` | ❌ | ❌ | $0.003 | **가성비 (기본)** |
| `whisper-1` | ❌ | ✅ | $0.006 | 타임스탬프 필요 시 |

## 트러블슈팅

| 증상 | 해결 |
|------|------|
| `Connection error` / `SSL CERTIFICATE` | `--ssl-no-verify` 추가 |
| `APIConnectionError` | 네트워크 확인, VPN 끄기 |
| `AuthenticationError` | API 키 확인 |
| STT 후 LLM 실패 | `--resume` 으로 이어서 (STT 비용 절약) |
| 화자 이름이 틀림 | `--edit-speakers` 로 수정 |
| 170MB+ 대용량 | 자동 mp3 압축 (별도 조치 불필요) |
| LLM 컨텍스트 초과 | 자동 분할 처리 (별도 조치 불필요) |
| 에러 원인 모를 때 | 출력 폴더 내 `run.log` 확인 |
| 콘솔에서 상세 보기 | `--debug` 추가 |
