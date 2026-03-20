# AI Meeting Minutes - Mobile App (Capacitor)

기존 React 웹 기반의 UI를 활용하여 **iPhone, iPad, Android** 기기에 설치 가능한 완전히 독립적인 네이티브 앱(Standalone App)으로 빌드합니다.

---

## 앱 구조 개요 (Serverless Architecture)

```text
┌──────────────────────────────────────────────────┐
│              iPhone / iPad / Android             │
│  ┌────────────────────────────────────────────┐  │
│  │           Capacitor WebView Shell          │  │
│  │  ┌──────────────────────────────────────┐  │  │
│  │  │        React 웹 앱 (기존 코드)        │  │  │
│  │  │  Dashboard · Recorder · Upload ·     │  │  │
│  │  │  TextInput · Settings · Detail       │  │  │
│  │  └──────────────────────────────────────┘  │  │
│  │  ┌──────────────────────────────────────┐  │  │
│  │  │      Capacitor 네이티브 플러그인       │  │  │
│  │  │  마이크 · 로컬 DB(IndexedDB) · 알림   │  │  │
│  │  └──────────────────────────────────────┘  │  │
│  └───────────────────▼──▲─────────────────────┘  │
│                      │  │                        │
│             Direct 인터넷 연결 (Wi-Fi/LTE)       │
│                      │  │                        │
│       ┌──────────────▼──▲─────────────────┐      │
│       │        외부 AI 클라우드 API         │      │
│       │   OpenAI API (Realtime STT, GPT)  │      │
│       │   Anthropic API (Claude Summary)  │      │
│       └───────────────────────────────────┘      │
└──────────────────────────────────────────────────┘
```

**핵심 원칙**: 완벽한 "Standalone(독립형)" 앱입니다.
- 사용자의 PC나 별도의 백엔드 서버(FastAPI)가 **전혀 필요하지 않습니다**.
- 앱 설정에 본인의 **OpenAI API Key**를 입력해두면, 앱이 직접 OpenAI와 통신하여 STT(음성 인식), 번역, 요약을 수행합니다.
- 생성된 회의록과 녹음 데이터는 중간 서버를 거치지 않고 **오직 사용자의 스마트폰 내부(로컬 스토리지)에만 안전하게 평생 보관**됩니다. (완벽한 프라이버시)

---

## 시스템 요구 사항

| 항목 | 요구 사항 |
| --- | --- |
| iOS | 16.0 이상 (iPhone 8 이후) |
| iPadOS | 16.0 이상 |
| 인터넷 | Wi-Fi 또는 LTE/5G (API 통신용) |
| **API 키** | **OpenAI API 키 필수** (앱 최초 실행 시 입력) |

---

## 반응형 UI/UX 설계

이 앱은 iPhone(모바일)과 iPad(태블릿) 화면 크기에 맞춰 자동으로 최적화됩니다.

### iPhone (< 768px) — 모바일 레이아웃

- **하단 탭 바**: Home / Record / Upload / Text / Settings (5탭)
- **Safe Area 대응**: 노치·Dynamic Island 영역 자동 회피
- **실시간 번역**: 원문(영어)과 번역문(한국어)을 세로 말풍선(Stack)으로 분리 표시하여 모바일 독해력 극대화

### iPad (≥ 768px) — 태블릿 레이아웃

- **좌측 고정 사이드바**: 화면을 넓게 사용하는 데스크톱풍 네비게이션
- **실시간 번역 2컬럼**: 좌측엔 원문, 우측엔 번역문을 나란히 배치하여 동시 비교
- **Split View 지원**: 다른 앱과 멀티태스킹 최적화

---

## 핵심 화면 가이드

### 1. Settings (설정 및 API 키)
- **가장 먼저 해야 할 일**: OpenAI API Key를 발급받아 붙여넣습니다. (이 키는 기기를 절대 벗어나지 않고 스마트폰 내부 보안 영역에만 저장됩니다.)
- 사용할 STT/LLM 모델(gpt-4o, gpt-4o-mini 등)과 번역 옵션(한국어 출력 등)을 선택합니다.

### 2. Recorder (실시간 녹음 및 분석)
- 마이크 버튼을 누르면 스마트폰의 마이크로 수음된 오디오가 실시간으로 OpenAI Realtime API로 전송됩니다.
- 1초 내외의 매우 짧은 지연시간(Low Latency)으로 실시간 전사 및 번역 텍스트가 화면에 폭포수처럼 떨어집니다.
- **WebSocket 끊김 시 자동 재연결** (최대 3회) — 네트워크 불안정해도 녹음이 이어집니다.
- **백그라운드 녹음 유지** — 화면 잠금이나 앱 전환 시에도 무음 오실레이터로 오디오 세션을 유지합니다. (Xcode에서 Background Modes > Audio 설정 필요)
- **원본 음질 유지** — 노이즈 억제/자동 게인 비활성으로 원음 그대로 전송합니다.
- 녹음 종료 시, 스크립트를 바탕으로 즉시 전체 회의 요약과 액션 아이템이 자동 정리됩니다.

### 3. Dashboard (세션 목록)
- 과거에 저장한 모든 회의록을 타임라인 형태로 봅니다.
- 데이터는 모두 오프라인(스마트폰 기기 자체)에 저장되므로 인터넷이 끊겨도 과거 회의록은 언제든 조회 가능합니다.
- 스와이프로 삭제하거나, 외부 메신저(카톡, 이메일)로 회의록 결과를 즉석 공유(iOS Share Sheet)할 수 있습니다.

### 4. File Upload & Text Analysis
- 이미 녹음된 음성 파일(mp3, m4a 등)을 앱으로 불러와서 문서화합니다.
- **대용량 파일 자동 분할**: 25MB 초과 파일은 자동으로 청크 분할 후 순차 STT 처리됩니다. 파일 크기 제한 없이 사용 가능합니다.
- 다른 앱(카톡, 메모장)에서 복사한 장문의 텍스트를 "붙여넣기" 하여 AI 회의록 포맷으로 깔끔하게 자동 요약합니다.
- iOS Safari 클립보드 API 미지원 시 수동 붙여넣기 안내 폴백이 작동합니다.

---

## 보안 및 데이터 프라이버시

본 구조의 최고 장점은 폭로 우려가 없는 **데이터 완전 통제권**입니다.
- PC 서버를 쓰지 않기 때문에 방화벽, 포트포워딩, 외부 해킹의 위협이 없습니다.
- 사용자의 민감한 회의 원본 음성 및 텍스트는 Apple/Google 기기의 **내부 저장소(IndexedDB)**에 묶여 있으며 그 누구도 열람할 수 없습니다.
- STT와 LLM을 위해 단 한 번 OpenAI의 암호화된 API 채널로만 전송되며, OpenAI 정책상 API 데이터는 AI 학습에 활용되지 않습니다.

---

## 필요 Capacitor 플러그인 모음

앱 구동을 위해 다음 네이티브 플러그인들을 사용합니다:

```bash
npm install @capacitor/core @capacitor/cli
npm install @capacitor/ios @capacitor/android

# UI & UX 보조
npm install @capacitor/haptics         # 터치 시 사운드/진동 피드백
npm install @capacitor/keyboard        # 가상 키보드 가림 현상 방지 
npm install @capacitor/status-bar      # 상단 배터리/시간 표시줄 제어
npm install @capacitor/share           # iOS 카카오톡/메일 공유 메뉴 호출
npm install @capacitor-community/keep-awake  # 녹음 도중 화면 꺼짐 방지
```

## 빌드 방법 (Mac 필요)

1. `web/frontend/` 디렉토리에서 React 앱을 빌드합니다: `npm install && npm run build`
2. `npx cap add ios` — iOS 프로젝트 폴더 생성 (최초 1회)
3. `npx cap sync ios` — 빌드된 웹 앱을 iOS 프로젝트에 복사
4. `npx cap open ios` — Xcode 열기
5. Xcode에서 필수 설정:
   - **Signing**: Apple ID 로그인 + Team 선택 + Bundle ID 변경
   - **Info.plist**: `Privacy - Microphone Usage Description` 추가 (마이크 권한)
   - **Background Modes**: `Audio, AirPlay, and Picture in Picture` 체크 (백그라운드 녹음)
6. iPhone USB 연결 후 [▶ Run] → 앱 설치 완료

> 상세 내용은 [BuildGuide.md](BuildGuide.md) 참조
