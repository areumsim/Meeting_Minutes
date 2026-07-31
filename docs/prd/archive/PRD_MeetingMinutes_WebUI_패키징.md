# PRD — Meeting Minutes Generator WebUI 패키징 & 확장

> ⚠️ **상태(2026-07-28)**: 이 문서는 2026-07-15 시점의 요구사항 정의(초안)로, 당시엔 PyInstaller
> `.exe` onedir 배포를 전제로 작성됐다. 이후 실제 배포는 백신 스캔 지연 문제로 **임베디드 파이썬 +
> `MeetingMinutes.bat` 포터블 방식**으로 전환됐다(`scripts/build/build_portable.ps1` →
> `dist/MeetingMinutesPortable.zip`). 아래 본문의 `build_exe.bat`/PyInstaller 서술은 역사적 맥락이며,
> 현재 배포 절차는 `docs/SETUP_NEW_TEAM.md` §7-A와 `CLAUDE.md`를 참고할 것. PyInstaller exe는 원격
> MCP(`/mcp`)가 필요할 때의 대체 경로로만 유지된다.

문서 버전: v0.1 (초안)
작성일: 2026-07-15
대상 제품: Meeting Minutes Generator v2.1 (음성/영상 → 회의록·요약·사실검증·Wiki)
문서 목적: webUI를 **비개발자가 바로 쓸 수 있는 Windows 배포본**으로 패키징하고,
앞으로 기능이 늘어날 것을 전제로 **확장 가능한 구조**를 정의한다.

---

## 1. 배경 & 문제 정의

현재 이 도구는 강력하지만 **개발자만 실행 가능**하다.
- 실행 전 `pip install`, `ffmpeg` PATH 등록, `config.json` 수기 편집, node/Vite 설치가 필요하다.
- webUI가 있지만 `python run_meeting.py web` 또는 dev 모드(Vite+FastAPI)로 떠서 비개발자 진입장벽이 높다.
- 설정(API 키, Obsidian 폴더)이 JSON 파일 편집이라 비개발자가 손대기 어렵다.

기능은 계속 늘어나는 중이다(실시간 STT, Wiki Knowledge Graph, Supermemory, prep-brief, iOS 앱 등).
따라서 이번 작업은 단순 "한 번 exe로 묶기"가 아니라, **앞으로 기능이 붙어도 재배포·재설정이 쉬운 뼈대**를 만드는 것이 핵심이다.

---

## 2. 목표 (Goals) / 비목표 (Non-Goals)

### 2.1 목표
- **G1.** 비개발자가 exe를 더블클릭하면 브라우저에서 webUI가 자동으로 열린다. (node/pip/파이썬 설치 불필요)
- **G2.** API 키·Obsidian 폴더 등 모든 설정을 **웹 Settings 화면에서만** 입력·저장한다. (config.json 직접 편집 없음)
- **G3.** 회의록·설정·인덱스 등 데이터가 재실행 후에도 유지된다. (쓰기 가능한 데이터 폴더 분리)
- **G4.** 개발자가 기능을 추가해도 **재빌드 한 번(build_exe.bat)** 으로 새 배포본이 나온다.
- **G5.** 설정 항목·기능이 늘어나도 UI/스키마가 깨지지 않게 **확장 가능한 설정·모듈 구조**를 갖춘다.

### 2.2 비목표 (이번 범위 밖)
- Mac / Linux 네이티브 패키징 (Windows 전용)
- 코드 서명(Authenticode) 인증서 구매 — SmartScreen 경고는 안내문으로 대응
- 멀티유저 서버/클라우드 SaaS화 (단, 향후 로드맵에는 포함)
- 소스 난독화 (유지보수를 위해 명시적으로 하지 않음)

---

## 3. 사용자 & 페르소나

| 페르소나 | 설명 | 핵심 니즈 |
|---|---|---|
| **실무 사용자 (비개발자)** | 회의 녹음/파일을 회의록으로 만들고 싶은 팀원 | 더블클릭 실행, 화면에서 키·폴더만 넣으면 끝 |
| **팀 관리자 (준-기술)** | 팀에 배포본을 나눠주는 사람 | zip 하나로 배포, 설정 안내가 쉬움, 문제 시 로그 확인 |
| **개발자 (유지보수)** | 기능을 추가/수정하는 사람 | 소스 접근·재빌드 용이, dev 모드 유지, 기능 추가가 국소적 |

---

## 4. 현재 기능 스냅샷 (패키징 대상)

- 입력: 파일 배치 처리, 마이크 실시간(HTTP/WebSocket), Obsidian 임베드 녹음, 폴더 감시
- 처리: STT → 화자추론/교정 → 회의록/요약/액션 → 사실검증 → Wiki Context/Proposal
- 지식: Obsidian 발행, Vault Q&A, Wiki Knowledge Graph, Supermemory
- webUI 페이지: Dashboard / Recorder / File Upload / Text Analysis / Settings / Session Detail
- 스택: FastAPI + SQLite (백엔드), React 19 + Vite 6 + TS + Tailwind (프론트)

---

## 5. 요구사항

### 5.1 P0 — 이번 릴리스 필수

**FR-1 정적 프론트 서빙**
프론트를 `vite build`로 정적화하고 FastAPI가 직접 서빙(SPA fallback). 런타임에 Vite/node 불필요.

**FR-2 단일 실행 exe (onedir)**
PyInstaller onedir 배포. `launcher.py`가 빈 포트 선택 → uvicorn 기동 → 기본 브라우저 자동 오픈.

**FR-3 ffmpeg 번들**
`ffmpeg.exe`를 함께 포함, 앱이 번들 경로 우선 사용(PATH fallback).

**FR-4 쓰기 가능한 데이터 폴더 분리**
`config.json`, `output/`, `data/`(DB·레지스트리·인덱스), PCM 백업, 로그를 사용자 쓰기 가능 위치
(exe 옆 `MeetingMinutesData/` 또는 `%APPDATA%\MeetingMinutes\`)에 저장. `_MEIPASS` 읽기전용 문제 회피.

**FR-5 웹 Settings = 유일한 설정 창구**
- 최초 실행 시 API 키 또는 Obsidian 경로가 비면 Settings로 자동 유도.
- Settings에서 OpenAI/Anthropic 키, Obsidian 볼트 경로(indexing 경로 동시 반영), 저장 위치, 알림 편집.
- 저장 시 config.json 반영 + "연결 테스트"(키 유효성/폴더 존재)로 성공·실패를 한국어로 표시.
- 키는 데이터 폴더의 config.json에만 저장, 프론트 번들/로그 노출 금지, 화면 마스킹.

**FR-6 비개발자 문서**
배포 폴더에 `사용법.txt`(쉬운 한국어): 실행법, 최초 설정, SmartScreen 경고 대응, 데이터 위치.

**FR-7 재빌드 스크립트**
`build_exe.bat`: 프론트 빌드 → PyInstaller → dist 정리 → 배포 zip 생성.

### 5.2 P1 — 확장성 대비 (이번에 뼈대만이라도)

**FR-8 설정 스키마 버전 관리**
config에 `config_version` 필드. 앱 시작 시 구버전 config를 최신 스키마로 **자동 마이그레이션**(누락 키 기본값 주입). 기능이 늘어 설정 항목이 추가돼도 기존 사용자 config가 깨지지 않게.

**FR-9 기능 플래그 (Feature Flags)**
`wiki`, `supermemory`, `graph`, 온라인검색 등 이미 on/off가 많음 → Settings에 **기능 토글 섹션**으로 통합. 새 기능은 플래그로 추가돼 기본 off로 안전하게 배포.

**FR-10 설정 UI 자동 생성 지향**
Settings 항목을 하드코딩 나열 대신 **스키마(그룹/키/타입/설명/기본값) 기반 렌더링**으로 리팩터. 새 설정 추가 시 스키마만 늘리면 UI가 따라오게.

**FR-11 백엔드 모듈 경계 유지**
`meeting_minutes_app`의 common/wiki_core/meeting_pipeline 서브패키지 경계를 지키고, 신규 기능은 라우터·서비스 단위로 추가(웹/CLI 공용 로직은 서비스층에).

### 5.3 P2 — 향후 로드맵 (이번 릴리스 아님, 방향만)

- 자동 업데이트(새 배포본 알림/교체), 코드 서명
- 앱 내 설정 백업/복원(내보내기·가져오기)
- 다국어 UI(ko/en), 접근성
- known-speaker 등록 UI, 로컬 diarization(pyannote/WhisperX) 후처리 옵션
- ~~사용량/비용 대시보드(월별 API 비용 집계)~~ → **출고됨(2026-07-30, `15847a1`)**. 월 지출 한도
  서버 강제(`cost.monthly_cap_usd`)와 대시보드 비용 요약 카드가 함께 들어갔다. 단 추정값 자체의
  정합성 결함은 별건으로 남아 있다(`PRD_v1.0.0.md` FR-014)
- 플러그인/커넥터 구조(알림 채널·STT provider·저장 백엔드 교체형)
- (별도 트랙) 멀티유저 서버 배포, 사내망 중앙 로깅(FastAPI /ws/realtime 경로)

---

## 6. 확장성 설계 원칙 (기능이 많아지는 것 대비)

1. **설정은 스키마가 진실의 원천.** UI·검증·마이그레이션·기본값을 모두 한 스키마에서 파생. (FR-8/10)
2. **새 기능은 기본 off 플래그로.** 배포 안정성 우선, 사용자가 Settings에서 켠다. (FR-9)
3. **웹/CLI 공용 로직은 서비스층 1곳.** UI가 늘어도 핵심 파이프라인은 중복 안 됨. (FR-11)
4. **데이터/코드 분리 유지.** 재빌드해도 사용자 데이터·설정 보존(FR-4)이 항상 성립.
5. **빌드는 원버튼.** 기능 추가 후에도 `build_exe.bat` 한 번. (FR-7)
6. **하위호환.** config_version 마이그레이션으로 구버전 사용자가 새 배포본을 덮어써도 동작.

---

## 7. 비기능 요구 (NFR)

- **보안:** API 키는 데이터 폴더 config.json에만. 프론트 번들·로그·git에 노출 금지. Settings에서 마스킹.
- **호환:** Windows 10/11 64bit. 인터넷 없이도 UI는 뜨되, 처리 시 OpenAI 접속 필요 안내.
- **성능:** 콜드 스타트(더블클릭→브라우저 오픈) 목표 15초 이내.
- **복원력:** 기존 크래시 복구(JSONL/PCM 백업) 유지. 데이터 폴더 손상 시 안내.
- **유지보수:** 난독화 금지, dev 모드(`web --dev`) 유지, 기존 CLI 무손상.

---

## 8. 릴리스 & 검증 기준 (Acceptance)

이번 릴리스는 아래를 모두 만족해야 통과:
1. 소스와 분리된 깨끗한 폴더에서 exe 더블클릭 → 브라우저 자동 오픈.
2. Settings에서 OpenAI 키 + Obsidian 폴더 입력·저장 → "연결 테스트" 성공.
3. 샘플 오디오 배치 처리 1건이 데이터 폴더 output에 정상 생성.
4. 앱 재실행 시 설정·이전 회의록 유지.
5. node/파이썬 미설치 PC 가정에서 런타임 pip/npm 호출 없음(코드로 확인).
6. 구버전 config.json을 덮어써도 자동 마이그레이션되어 실행됨(FR-8 스모크).
7. `build_exe.bat` 한 번으로 재빌드·zip 생성 성공.

---

## 9. 오픈 이슈 / 결정 필요

- 데이터 폴더 위치: `exe 옆 폴더` vs `%APPDATA%` — (권장: exe 옆, 포터블/공유 유리)
- exe 콘솔 창 표시 여부: 표시(문제 진단 쉬움) vs 숨김(깔끔) — (권장: 최소 콘솔 표시)
- SmartScreen: 안내문 대응 vs 향후 코드서명 도입 시점