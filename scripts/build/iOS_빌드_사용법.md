# AI 회의록 — iOS 앱 빌드 & 사용 가이드

이 문서는 iOS 앱을 **빌드**하고 **사용**하는 방법을 설명합니다.

> **가장 중요한 전제**: iOS 앱의 실제 빌드(기기 설치·App Store 업로드)는 **Mac + Xcode**에서만 가능합니다.
> Windows에서는 여기까지(프로젝트 준비) 끝났고, 아래 빌드 단계는 Mac에서 진행해야 합니다.
> Windows PC에서 코드를 GitHub 등으로 올리고, Mac에서 받아 빌드하면 됩니다.

---

## 0. 앱의 두 가지 동작 모드

앱은 두 방식으로 쓸 수 있고, **[설정] 화면에서 언제든 전환**됩니다.

| 모드 | 구조 | 되는 것 | 필요 조건 |
|---|---|---|---|
| **단독 모드** | 아이폰 → OpenAI 직접 | 파일 업로드 전사, 텍스트→회의록, 요약, 세션 보기. 실시간 받아쓰기(직접 WS) | 기기에 OpenAI API 키 입력 |
| **PC 연결 모드** | 아이폰 → 같은 WiFi의 PC(exe) → OpenAI | exe와 **동일한 고품질**(2단계 보정·위키·그래프·워처) | PC에서 exe 실행 + LAN 접속 허용 + 같은 WiFi |

- 기본은 단독 모드입니다. [설정] 상단 **"PC 서버 연결"** 카드에 PC 주소를 넣고 "연결"하면 PC 연결 모드가 됩니다.
- PC 연결 모드가 exe와 완전히 같은 경험이라 회의 품질이 중요하면 이 모드를 권장합니다.

---

## 1. 빌드 (Mac에서)

### 준비물
- macOS + **Xcode**(App Store에서 설치)
- **Node.js 18+** (`brew install node` 또는 nodejs.org)
- **Apple ID**(무료로 충분 — 본인 기기에 설치용). App Store 배포는 유료 개발자 계정($99/년) 필요.

### 빌드 순서
```bash
# 1) 이 저장소를 Mac으로 가져온 뒤
cd web/frontend

# 2) 의존성 설치 (최초 1회)
npm install

# 3) 웹 빌드 + iOS 프로젝트 동기화 + Xcode 열기 (한 방)
npm run ios:build
#   (= npm ci && npm run build:standalone && npx cap sync ios && npx cap open ios)
```

`npm run ios:build`가 Xcode에서 App 프로젝트를 자동으로 엽니다.
(이 프로젝트는 CocoaPods가 아닌 Swift Package Manager를 쓰므로 `.xcworkspace` 없이
`App.xcodeproj`가 열립니다. `pod install`은 필요 없습니다.)

### Xcode에서 (최초 1회 서명 설정)
1. 왼쪽 파일 트리에서 최상단 **App** 프로젝트 클릭 → **Signing & Capabilities** 탭.
2. **Team**: 본인 Apple ID 선택(없으면 "Add an Account"로 로그인).
3. **Bundle Identifier**: 기본 `com.meetingminutes.app` — 다른 사람과 겹치면 `com.본인이름.meetingminutes` 등으로 변경.
4. 상단에서 실행 대상 기기 선택:
   - **실제 아이폰**: USB로 연결 → 기기 이름 선택. (아이폰에서 "개발자 신뢰" 필요: 설정 → 일반 → VPN 및 기기 관리 → 개발자 앱 신뢰)
   - **시뮬레이터**: 마이크·실기기 테스트엔 부적합. 실제 기기 권장.
5. **▶ (Run)** 버튼 → 앱이 빌드되어 기기에 설치·실행됩니다.

> 무료 Apple ID로 설치한 앱은 **7일 후 만료**되어 다시 실행하려면 재설치(재빌드)해야 합니다.
> 유료 개발자 계정이면 1년, TestFlight/App Store 배포도 가능합니다.

### 코드/설정을 바꾼 뒤 다시 빌드할 때
```bash
cd web/frontend
npm run ios:sync     # 웹 다시 빌드 + iOS에 반영
# 그 후 Xcode에서 ▶ Run  (또는 npm run ios:open 으로 Xcode 열기)
```

---

## 2. 사용법 — 단독 모드 (PC 없이 아이폰만)

1. 앱 실행 → **[설정]** 탭.
2. **OpenAI API 키** 입력(sk-... ) → 저장. (키는 이 기기에만 저장됩니다.)
3. 뒤로 나가서:
   - **녹음**: 파일 업로드 또는 실시간 받아쓰기.
   - **텍스트**: 회의 메모를 붙여넣어 회의록 생성.
4. 완료되면 세션 목록에서 회의록·요약을 확인하고 공유할 수 있습니다.

> 단독 모드의 실시간 받아쓰기는 OpenAI에 직접 연결하며, 위키/그래프/2단계 보정 같은
> 서버 전용 기능은 없습니다. 가장 안정적인 건 **파일 업로드**와 **텍스트** 경로입니다.

---

## 3. 사용법 — PC 연결 모드 (권장, PC 서버와 동일 품질)

### PC(Windows)에서 준비
1. 포터블 배포본의 `MeetingMinutes.bat` 실행(더블클릭). (구형 MCP 대체 빌드라면 `MeetingMinutes.exe`)
2. 웹 화면 **[설정] → 고급 설정 → 서버/네트워크 → "LAN 접속 허용"** 켜기 → 저장.
3. **앱(`MeetingMinutes.bat`)을 완전히 종료했다가 다시 실행**(바인딩 변경 적용).
4. 다시 켜지면 콘솔/브라우저에 접속 주소가 표시됩니다. 또는 PC에서 명령 프롬프트에 `ipconfig` → **IPv4 주소**(예: `192.168.0.10`) 확인.
   - 접속 주소 = `http://<그 IPv4>:8501`

> ⚠ "LAN 접속 허용"은 같은 WiFi의 다른 기기가 이 PC의 회의록 서버에 접속하게 합니다.
> **집·사무실 등 신뢰된 네트워크에서만** 켜세요. 카페 등 공용 WiFi에서는 끄는 걸 권장합니다.
> (Windows 방화벽이 처음 접속 시 허용을 물으면 "허용"하세요.)

### 아이폰에서
1. 아이폰이 **PC와 같은 WiFi**에 연결돼 있는지 확인.
2. 앱 → **[설정] → "PC 서버 연결"** 카드에 `http://192.168.0.10:8501`(위에서 확인한 주소) 입력.
3. **"테스트"** → "연결됨"이 나오면 **"연결"**.
   - 처음 연결 시 "로컬 네트워크 접근 허용" 팝업이 뜨면 **허용**.
4. 상태가 **"연결됨 (서버 모드)"** 로 바뀌면, 이제 녹음·업로드·회의록이 모두 PC에서 처리됩니다
   (exe와 동일하게 2단계 보정·위키·그래프 적용, OpenAI 키도 PC 것을 사용 → 아이폰엔 키 불필요).
5. 연결을 끊으려면 같은 카드에서 **"해제"** → 단독 모드로 돌아갑니다.

---

## 4. 자주 묻는 문제

| 증상 | 확인 |
|---|---|
| Xcode에서 서명 오류 | Signing & Capabilities에서 Team 선택했는지, Bundle ID가 남과 겹치지 않는지 |
| 아이폰에 "신뢰되지 않은 개발자" | 아이폰 설정 → 일반 → VPN 및 기기 관리 → 개발자 앱 신뢰 |
| 앱이 7일 후 안 열림 | 무료 계정의 제약 — 다시 빌드해 설치(유료 계정이면 1년) |
| PC 연결 "연결 실패" | ① PC exe 켜져 있나 ② LAN 접속 허용 켜고 **재시작**했나 ③ 같은 WiFi인가 ④ 주소·포트(:8501) 정확한가 ⑤ Windows 방화벽 허용했나 |
| 연결은 됐는데 마이크가 안 됨 | 아이폰 설정 → 개인정보 보호 → 마이크 → 이 앱 허용 |
| 마이크 권한 팝업이 안 뜸 | 앱 삭제 후 재설치(권한 상태 초기화) |

---

## 5. 참고: 프로젝트 구조
- iOS 프로젝트: `web/frontend/ios/` (Capacitor + Swift Package Manager)
- 앱 표시 이름: **AI 회의록** / Bundle ID(기본): `com.meetingminutes.app`
- 마이크 권한·백그라운드 오디오·로컬 네트워크 권한은 `ios/App/App/Info.plist`에 이미 설정됨.
- 웹 자산은 `npm run build:standalone`의 `dist/`를 `npx cap sync ios`가 `ios/App/App/public/`로 복사합니다.

### 왜 iOS 는 `build:standalone` 인가 (CSP 빌드 프로파일, SEC-006)

CSP 는 빌드 프로파일 2종으로 갈라져 있다(`web/frontend/vite.config.ts`).

| 프로파일 | 쓰는 곳 | `connect-src` |
|---|---|---|
| `packaged`(기본) | PC 포터블·exe — FastAPI 가 같은 오리진에서 프런트를 서빙 | `'self'` 만 |
| `standalone` | **아이폰 앱 번들** — 서버 없이 OpenAI 직접 호출 + 사용자가 입력한 LAN 주소 | OpenAI + LAN 허용 |

아이폰 앱을 `npm run build`(packaged)로 만들면 **단독 모드가 통째로 죽는다**(외부 호출이
CSP 에 막힌다). 반대로 PC 배포본을 standalone 으로 만들면 좁혀 둔 보안이 풀린다.
그래서 `ios:*` 스크립트는 항상 `build:standalone` 을 쓴다 — 손으로 `npm run build` 를
부르지 말 것.

### 왜 `ios:build` 는 `npm ci` 이고 `ios:sync` 는 아닌가

`ios:build` 는 **배포용 산출물**을 만드는 경로라 lockfile 을 그대로 설치해 재현성을
맞춘다(`npm install` 은 package.json 의 `^` 범위를 다시 해석해 lockfile 을 갱신할 수 있다).
`ios:sync` 는 개발 중 반복 동기화용이고 `npm ci` 는 매번 `node_modules` 를 지워 느리므로
설치를 하지 않는다 — 의존성을 바꿨다면 `npm install` 을 먼저 직접 돌린다.
