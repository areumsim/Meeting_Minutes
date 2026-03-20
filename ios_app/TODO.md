# Capacitor 기반 모바일 앱 개발 Todo

기존 React 웹 UI를 Capacitor로 감싸 iPhone/iPad 네이티브 앱으로 빌드하고,
모바일 환경에 맞는 **서버리스(Serverless) 단독 동작 앱**을 구현하기 위한 마스터 태스크 목록입니다.

> **핵심 아키텍처 (수정됨)**: 앱은 PC 백엔드를 거치지 않습니다.
> 앱 내부에서 API Key를 직접 관리하여 OpenAI API로 다이렉트 통신을 수행하며, 모든 회의록 데이터는 디바이스 자체 내장 DB에만 저장됩니다.

---

## Phase 1 — 독립 앱 기초 공사 (뼈대 세우기)

### 1-1. 서버 의존성 제거 및 로컬 스토리지 구축
- [x] PC 서버 통신(API 호출) 로직 완전 제거
- [x] 브라우저 내장 IndexedDB 또는 LocalStorage를 기반으로 한 로컬 DB 구축 (세션 목록 저장용)
- [x] 앱 최초 실행 시 기본 더미 데이터(또는 안내 페이지) 로드

### 1-2. Capacitor 프로젝트 및 권한 설정
- [x] iOS 프로젝트 초기화 (`npx cap add ios`)
- [x] Info.plist 권한 설정 (`NSMicrophoneUsageDescription`, 백그라운드 오디오 권한)

### 1-3. Safe Area & 모바일 레이아웃 최적화
- [x] CSS `env(safe-area-inset)` 적용하여 아이폰 노치/하단 바 겹침 해결
- [x] `100dvh` 적용 및 Viewport Cover 설정
- [x] 모바일 하단 탭 바(Tab Bar) 컴포넌트 구현
- [x] 아이패드용 좌측 사이드바(Sidebar) 반응형 컴포넌트 구현

### 1-4. API Key 관리 UX (기존 서버 URL 설정 대체)
- [x] Settings 화면: "서버 URL" 입력창 삭제
- [x] Settings 화면: "OpenAI API Key" 등 입력창 추가 (입력 마스킹 처리 `****`)
- [x] API 키가 입력되어 있지 않은 경우, 기능 진입 시 Settings 화면으로 자동 유도하는 Guard 로직 도입

---

## Phase 2 — 핵심 기능: 다이렉트 실시간 API 연동

### 2-1. 녹음 권한 및 Wake Lock
- [x] 마이크 권한 요청 플로우 (`navigator.mediaDevices.getUserMedia`) — Recorder.tsx에서 getUserMedia 호출로 구현 완료
- [x] 화면 꺼짐 방지 (`@capacitor-community/keep-awake` 플러그인 연동) — KeepAwake.keepAwake()/allowSleep() 구현 완료
- [x] iOS 백그라운드 진입 시 녹음 계속 유지되도록 처리 — 무음 오실레이터로 오디오 세션 유지 + visibilitychange resume + BuildGuide에 Background Modes 설정 안내 완료

### 2-2. OpenAI Realtime API 직결 (Direct Connection)
- [x] 브라우저 WebSocket으로 OpenAI `wss://api.openai.com/v1/realtime`에 직접 붙는 로직 구현 (기존 FastAPI 릴레이 제거) — api.ts createRealtimeWS()
- [x] 오디오 청크를 base64로 캡처하여 스트리밍 전송 — Recorder.tsx ScriptProcessor + base64 인코딩
- [x] OpenAI API Key를 헤더에 포함하여 인증 체계 구축 — WebSocket subprotocol 방식 인증

### 2-3. 실시간 번역 UI (모바일 컴팩트 포맷)
- [x] 세로 말풍선 레이아웃: 좁은 화면을 위한 [영어 작은 글씨 위에 + 한국어 큰 글씨 아래에] 구조 구현
- [x] 자동 아래로 스크롤 (Auto-scroll to bottom) 로직
- [x] WebSocket에서 쏟아지는 delta 이벤트를 가공하여 자연스러운 타이핑 애니메이션으로 출력

### 2-4. 녹음 완료 후 로컬 문서화
- [x] 녹음 정지 시, 최종 확정된 트랜스크립트를 다시 한번 GPT API(Chat Completions)로 던져서 요약+회의록 파일 생성
- [x] 완료된 모든 데이터를 스마트폰 로컬 저장소(IndexedDB)에 영구 기록

---

## Phase 3 — 파일 처리 및 텍스트 퀵 액션

### 3-1. File Upload 화면 모바일 최적화
- [x] 드래그 앤 드롭 문구를 없애고 화면 정중앙에 거대한 **[+ 터치하여 파일 선택]** 버튼 배치
- [x] 모바일 OS 네이티브 픽커 연동
- [x] 오디오 파일 선택 시 브라우저 단에서 청크 분할 후 OpenAI Whisper API(`v1/audio/transcriptions`)로 다이렉트 전송 구현

### 3-2. TextInput 화면 모바일 최적화
- [x] **[📋 붙여넣기]** 전용 퀵 버튼 배치 (스마트폰에서 복사해온 장문 텍스트 1초만에 붙이기)
- [x] 가상 키보드 팝업 시 화면 가림 현상 최소화를 위한 레이아웃 재배치
- [x] 텍스트 제출 시 곧바로 OpenAI API로 요약/문서 포맷팅 호출

---

## Phase 4 — 보관함(Dashboard) 및 네이티브 확장

### 4-1. 오프라인 Dashboard
- [x] 서버 폴링 삭제 -> 로컬 상태(`IndexedDB`) 실시간 동기화로 변경
- [x] 아이템 스와이프 해서 휴지통/삭제 기능 추가

### 4-2. 네이티브 기능 융합
- [x] 회의록 상세 화면(Session Detail)에 네이티브 **공유(Share) 버튼** 탑재 (Capacitor Share Plugin -> 즉시 카카오톡/메일로 회의록 발송)
- [x] 햅틱(Haptics) 피드백 적용: 녹음 켤 때 "징", 끌 때 "지징", 완료 시 "경쾌한 진동"
- [x] App Icon 설계 및 Splash Screen 설정 (문서화 완료)

---

## 작업 완료 여부 체크리스트

- [x] 리다이렉트 서버 제거 및 로컬 앱 컨셉 재설계
- [x] iPhone 전용 반응형 하단 탭바 & Settings(API Key) UI
- [x] 텍스트 입력과 파일 업로드 모바일 뷰 최적화
- [x] OpenAI Realtime API 직접 연동 (로깅 및 테스트 완료)
- [x] 로컬 indexedDB 저장소 로직 완전체 구축
