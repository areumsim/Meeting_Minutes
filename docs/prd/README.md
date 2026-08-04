# PRD 색인

이 폴더는 요구사항 문서와 그 검토 자료를 모아 둔 곳이다. 코드 구조는 [`../ARCHITECTURE.md`](../ARCHITECTURE.md),
사용법은 [`../USER_GUIDE.md`](../USER_GUIDE.md), 전체 문서 지도는 [`../README.md`](../README.md)를 본다.

## 1. 무엇을 먼저 보는가

| 문서 | 성격 | 상태 |
|---|---|---|
| [`PRD_v1.0.0.md`](PRD_v1.0.0.md) | **v1.0.0 사내 릴리즈 정본.** 기준 PRD + v1.2 델타 + v1.3 소스 검증판을 하나로 통합 | Draft · 검토 요청 |
| [`PRD_Natively_적용_20260730.md`](PRD_Natively_적용_20260730.md) | 동의·녹취 정책 / Reference Files 인덱싱 / 로컬·BYOK 참조 아키텍처 | v1.0 — **트랙 A·C 완료, FR-B3만 잔여** |
| [`PRD_실시간관련정보_임베딩_PageIndex.md`](PRD_실시간관련정보_임베딩_PageIndex.md) | 실시간 관련 노트 검색 + 누적 검토 | v1.1 — **Phase 0 완료, Phase 1 미착수, FR-2 라이브 검증 미실시** |
| [`PRD_회의중_음성브리핑_20260803.md`](PRD_회의중_음성브리핑_20260803.md) | 회의 **중간** 요약 · 이견 지적 · 음성(TTS) 안내 | v0.1 — **트랙 A(중간 요약) 구현 완료**(2026-08-04). 별도 모듈이 아니라 페르소나 PRD 의 오케스트레이터에 `summarizer` 주기 페르소나 1종으로 합쳤다(미결 #3 결정). **트랙 C(음성) 미착수** — 참견도 4·5 이고 `facilitation.max_level` 3 이 막는다. **트랙 B(회의 중 오류 지적) 보류** |
| [`PRD_회의진행_페르소나에이전트_20260803.md`](PRD_회의진행_페르소나에이전트_20260803.md) | 회의 진행 보조 페르소나 8종 + 참견도 0~5 축 + 하이브리드 계층형(트리아지→생성) | v0.2 — **M0(관찰) + M1(앰비언트 옆 카드) 구현 완료**(2026-08-03~04, 기능 자체는 기본 꺼짐). 위험 2종(팩트체커·비판자)은 `hard_cap=2` 로 화면 미개방 — **M2 는 오탐률 실측 통과가 전제**. 라이브 웹검색(M2)·음성(M3) 미착수 |
| [`archive/PRD_MeetingMinutes_WebUI_패키징.md`](archive/PRD_MeetingMinutes_WebUI_패키징.md) | 2026-07-15 패키징 요구사항 | **이력** — PyInstaller 전제. 현재 배포는 포터블 |

**검토 자료** (요구사항이 아니라 근거)

| 파일 | 무엇인가 |
|---|---|
| `UI_개선안_v1.2.html` | 화면 11종 정적 목업. `PRD_v1.0.0.md` §11 화면별 사양 표의 탭 번호와 1:1 대응한다 — 수용 기준의 일부다 |
| `외부시안_gemini_20260731.html` | 외부 제안 시안 원본. 채택/불채택 판단은 `PRD_v1.0.0.md` 부록 B |
| `webui_current_snapshot.html` | v1.2 가 근거로 쓴 456 KB DOM 덤프. **커밋하지 않는다**(재생성 가능한 파생물이고 git diff 가 무의미). 갭 근거는 부록 E 에서 실제 소스 `file:line` 으로 이전됐다 |

## 2. 읽는 규칙

- 통합본 §0 이 **근거 등급 표기**를 정의한다. `[소스 검증]`(파일:줄 확인) · `[스냅샷 추정]`
  (P0 승격 금지) · `[문서 근거]`(정책 결정용). **부록 A 의 G-01~G-36 은 전부 `[스냅샷 추정]`이었고,
  부록 E 가 그중 반박된 것과 확인된 것을 가른다.** 부록 A 만 읽고 판단하면 안 된다.
- 개정 조항은 원문 원칙 문단을 남기고 그 아래 `**개정 (2026-07-31)**` 로 확정 사양을 붙였다.
  상충하면 개정 쪽이 우선한다.
- 증거는 **부록 E 단일 소스**다. 본문 조항은 부록 E 의 N-* 번호를 가리키고 수용 기준만 갖는다.

## 3. 요구사항 ID 크로스워크

통합본의 유니크 ID **129건**. 원본 합집합 126 + v1.3 신규 3(FR-014·SEC-009·UX-015).

| 계열 | 범위 | 정의 위치 | 비고 |
|---|---|---|---|
| UX-001~005 | 5 | §5 | UX-002·003·005 는 개정됨 |
| UX-006~015 | 10 | §5 | 신규. **UX-013 은 "3계층 도입"이 아니라 "비스키마 편입 + 검색"으로 축소**(부록 E.1) |
| FR-001~007 | 7 | §6 | FR-001·004 개정(P0) |
| FR-008~014 | 7 | §6 | 신규. **FR-008·013 은 이미 구현 — 신설 철회**(부록 E.1). FR-009·011 개정. FR-014 신규 P0 |
| SEC-001~005 | 5 | §7 | SEC-003 개정(비밀 **8종**) |
| SEC-006~009 | 4 | §7 | 신규. SEC-006 은 빌드 프로파일 2종(`packaged`/`standalone`)으로 재정의. SEC-009 신규 P0 |
| OPS-001~005 | 5 | §8 | OPS-004 는 현재 코드에 `MM-*` 0건이라 신규 구축 |
| AI-001~003 | 3 | §10 | AI-001 우선순위 상향(단가 하드코딩 확인) |
| G-01~G-36 | 36 | 부록 A | 스냅샷 갭 분석. **판정은 부록 E.1~E.2** |
| B0/B1/B2/B3/F3/Q4-* | 47 | 부록 E.5 · §15.2 | v1.2 WBS. Batch A~D 로 교체됨 |
| N-1~N-30 | 30 | 부록 E.3 | 소스 검증 결함. ID 계열이 달라 위 129 에 포함되지 않는다 |

**계열별 상태가 바뀐 항목만 따로 본다**

| ID | v1.2 주장 | 확정 |
|---|---|---|
| FR-008 사용량과 한도 | 신규 신설 | **이미 전 구간 구현** — 0 MD |
| FR-013 녹취 고지·출처 | 신규 신설 | **이미 구현** — DB 기록 + `--resume` 공백만 잔여, 1 MD |
| UX-013 / F3-13 설정 3계층 | 도입 필요 (5 MD) | **이미 구현** — 비스키마 편입 + 검색만, 2 MD |
| §11 화면 8 / F3-12 회의록 상세 | 신규 화면 (6 MD) | **`SessionDetail.tsx` 676줄 기존** — 보강 2~3 MD |
| G-24 prep-brief 과금 | P0 | **오판** — prep-brief 는 LLM 을 쓰지 않는다 |
| G-04 `unsafe-eval` 의존성 | 교체 위험 등재 | **의존성 0건** — 순수 잉여, 즉시 삭제 가능 |
| G-12 상태 어휘 | 3종뿐 → 늘려야 | **어휘가 갈라져 있음** — 통일이 과제 |

## 4. 결정 대장

### 4.1 확정 (통합본 §17 · §0.2)

사내 Windows 전용 v1 · Portable ZIP 기본 · 미서명 EXE 조건부 보조 · 코드 서명 안 함 ·
수동 업데이트 · 삭제는 휴지통 기본 · 로컬 전용 단일 사용자 · 대형 프레임워크 전면 교체 안 함 ·
**v1 범위 = 안 B(10화면 전부)** · **단독/LAN 모드 유지 + 빌드 격리** · **로컬 STT 가중치 미포함**

### 4.2 가정 (통합본 §17)

관리형 Windows 계정 실행 · 외부 AI 전송의 정책 충족 또는 별도 승인 · 접근 통제된 사내 배포 위치 ·
평가 데이터의 개인정보 제거·승인 · SmartScreen 차단 시 ZIP 대체

### 4.3 미결 — 착수를 막는 것

| # | 질문 | 왜 막는가 |
|---|---|---|
| 1 | `realtime.two_pass` 기본값 — 켜둔 채 정직하게 2배로 표시할지, 기본 끄고 옵트인으로 돌릴지 | 구현은 어느 쪽이든 같지만 **사용자가 보게 되는 금액이 갈린다**. (2026-08-04: 표시 자체의 정확성 문제는 해소됐다 — 보정 패스가 실제로 돌았는지를 `sessions.stt_two_pass` 에 기록하고 상세 화면이 그 값을 읽는다. 남은 것은 순수 정책 결정이다) |
| 2 | Groq 폴백 유지 여부 | 유지 시 회의 음성 **국외 이전 검토**가 선행되어야 한다 |
| 3 | **API 키를 조직이 발급하는가, 개인 계정으로 발급하는가** | 도움말이 개인 결제수단 등록을 안내한다(G-26). keyring·도움말 문구·비용 정산·DPA·퇴사자 키 회수가 전부 여기 걸린다. **이 결정 없이는 릴리즈할 수 없다** |
| 4 | 워처 첫 스캔 대기열 상한 | 폴더에 500건이 있으면 전량 대기열 vs 상한 + 안내 |

> ⚠️ **문서 결함 1건 (미해결)**: 통합본 §17 확정 결정 목록에 "API 키는 조직이 사용자별로 발급한다"가
> 아직 **확정으로** 적혀 있다. 위 #3 과 정면으로 모순된다. 합의 안 된 것을 확정으로 적어 둔 문장이라
> **가정으로 강등**해야 한다(삭제가 아니라 강등 — 이 리포는 판단 근거를 남기는 관행이다).

### 4.4 미결 — 이관 가능

승인 벤더 범위 · 익명 집계 수집 허용 여부 · 접근성 목표 범위(WCAG AA 전체 vs 핵심 흐름 6개,
단 프런트 테스트 인프라가 없으면 어느 쪽도 측정 불가) · 이메일 자동 발송 v1 유지 시 문구 정정 ·
파일럿 규모 · Natively FR-A3 사내 가이드 실물 확인 · Natively FR-B3 추출 의존성 포함 여부

## 5. 문서-코드 정합 검사

문서가 주장하면 코드가 뒷받침해야 한다. 문서를 고칠 때 아래를 돌려 드리프트를 다시 쌓지 않는다.

> ⚠️ **이 표는 "그때 결과"의 기록이다.** 아래 두 블록은 2026-07-31(커밋 `acf3c58`) 실행분이라
> 수치가 낡았다 — 특히 테스트 수는 그 뒤 905 → **1038** 으로 늘었다(정본은 `CLAUDE.md`).
> 표를 인용하기 전에 반드시 다시 돌린다. 3주 묵은 정합 검사표는 정합을 보장하지 않는다.

**2026-08-04 재실행분** (이 날 리뷰에서 확인한 항목만)

| 문서 주장 | 확인 명령 | 결과 |
|---|---|---|
| 테스트 수 | `python -m pytest -q` 마지막 줄 | **1038 passed, 1 skipped** — `CLAUDE.md` 와 일치 |
| 프런트엔드 테스트 수 | `cd web/frontend && npm test` 마지막 줄 | **82** (2026-07-31 의 62 에서 증가) |
| 배포 의존성 고정(파이썬) | `grep -c '==' scripts/build/constraints-web.txt` | **52** |
| 페르소나 화면 개입이 실제로 나가는가 | `grep -n 'on_intervention' meeting_minutes_app/wiki_core/facilitation.py` | 존재·호출됨 → "M0 관찰모드(화면 없음)" 표기는 폐기됐다 |
| 실시간 세션 two_pass 판정 | `grep -n 'resolve_two_pass' meeting_minutes_app/common/pricing.py web/backend/api/sessions.py` | 존재 → `sessions.source` 기반 판정은 폐기(웹 실시간·업로드가 같은 `"web"`) |
| 세션 과금이 kind 를 빠뜨리지 않는가 | `grep -n 'session_spend_by_kind' meeting_minutes_app/common/usage_log.py` | 존재 → kind 열거 방식은 폐기(`web_research` 누락 전례) |

**2026-07-31 실행분** (기준 커밋 `acf3c58` · 수치는 그때 값)

| 문서 주장 | 확인 명령 | 그때 결과 |
|---|---|---|
| 로컬 전용 / loopback 전용 | `grep -rn 'lan_access' meeting_minutes_app/` | 비지 않음 → "기본 loopback, `lan_access` 옵트인"으로 써야 한다 |
| 외부 호출은 백엔드 경유 | `grep -rn 'api.openai.com' web/frontend/src/` | **4줄 = 주석 1 + 실제 호출 3**(`api.ts` 665·976·1025) → 단독 모드 존재를 인정해야 한다. 줄 수만 세면 호출 수와 어긋난다 |
| TLS 검증 비활성 경로 0건 | `grep -n 'verify_ssl\|"verify"' meeting_minutes_app/common/config_schema.py` | **2건, 그중 1건 기본 False** |
| PDF/PPTX 추출 파이프라인 있음 | `grep -n 'pypdf\|python-pptx' pyproject.toml requirements*.txt` | **0건** → Natively FR-B3 ❌ 표기가 정당 |
| 테스트 수 | `python -m pytest --collect-only -q` 마지막 줄 | **905** — 낡음, 위 재실행분 참조(수치 정본은 CLAUDE.md) |
| 배포 의존성이 고정돼 있음(파이썬) | `grep -c '==' scripts/build/constraints-web.txt` | 50 — 비어 있으면 버전이 빌드 시점에 결정된다(재현 불가) |
| 배포 의존성이 고정돼 있음(프런트) | `grep -c "@('install')" scripts/build/build_portable.ps1` | **0** — `npm install` 이 남아 있으면 lockfile 이 갱신돼 같은 커밋에서 다른 번들이 나온다 |
| 프런트엔드 테스트 수 | `cd web/frontend && npm test` 마지막 줄 | **62** — 0건이면 UX-015 가 미해소다 |
| 프런트 E2E·접근성 게이트 | `grep -c 'playwright\|axe-core' web/frontend/package.json` | **0** → §13.3 의 해당 게이트는 "미실행"으로 표기해야 한다 |
| 월 한도 서버 강제 | `grep -rn 'monthly_cap_usd' meeting_minutes_app/` | 존재 → G-13 "PRD에 요구사항 없음" 표기는 폐기됐다 |
| `MM-*` 오류 코드 | `grep -rn 'MM-[A-Z]*-[0-9]' web/backend/` | **0건** → OPS-004 는 신규 구축 |

Batch A(비용 정합·자동 실행 안전장치) 착수 후 추가된 확인 항목:

| 문서 주장 | 확인 명령 | 그때 결과 |
|---|---|---|
| two_pass 가 비용 추정에 반영됨 | `grep -n 'two_pass' meeting_minutes_app/common/pricing.py` | 존재 → FR-014 의 N-1 은 해소됐다(미해소로 쓰면 틀린다) |
| 표시값과 한도가 같은 함수 | `grep -rn 'estimate_session_cost' web/backend/api/` | 4곳 모두 `pricing.estimate_session_cost` 경유 |
| 자동 실행 과금이 합계에 잡힘 | `grep -n 'KIND_WATCHER\|KIND_PLAN_AUTOMATION' meeting_minutes_app/common/spend_guard.py` | 존재 → N-2 해소. 대시보드는 `automationUsd` 로 조회 |
| 한도 판정이 한 곳인지 | `grep -rn 'spend_guard.blocked' meeting_minutes_app/ web/backend/` | 워처·계획자동화·임베딩·재생성이 모두 같은 함수 |
| Groq 가 기본 꺼짐인지 | `grep -n 'groq_fallback' meeting_minutes_app/common/config_schema.py` | `default: False` → N-5 해소(결정: 유지·기본 꺼짐) |
| 워처 첫 스캔이 전량 처리하지 않는지 | `grep -n 'first_scan' meeting_minutes_app/meeting_pipeline/audio_watcher.py` | 존재 → N-3 해소. 대기열은 `GET /watcher/pending` |
| 전역 일시정지 존재 | `grep -n 'automation_paused' meeting_minutes_app/common/spend_guard.py` | 존재 → N-6 해소 |
| 벤더 전환이 세션에 남는지 | `grep -n 'stt_fallback_used' web/backend/database.py` | 컬럼 존재 → N-25 는 배치 경로까지 해소 |
| WS Origin 검증 존재 | `grep -n 'ws_reject_foreign_origin' web/backend/api/realtime.py` | 존재 → N-8 해소. `accept()` **전에** 검사한다 |
| Origin 허용 목록이 한 곳인지 | `grep -rn 'ALLOWED_ORIGIN_REGEX' web/backend/` | `security.py` 정의 + `app.py` CORS 가 그 값을 쓴다 |
| shutdown 이 graceful 인지 | `grep -n 'register_shutdown_handle' meeting_minutes_app/` | 두 런처가 같은 함수로 Server 핸들 등록 → N-7 해소 |
| 실행 세션 토큰(SEC-002) | `grep -rn 'session_token\|bootstrap' web/backend/` | **아직 없음** — Origin 검증이 임시 방어다. 있다고 쓰면 틀린다 |

## 6. archive/ 와 이력 주의

`archive/` 는 통합에 흡수된 원본과 역사화된 문서를 보존하는 곳이다. **본문을 수정하지 않는다** —
통합에서 잃은 것이 있는지 다투게 될 때 대조할 대상이기 때문이다.

| 파일 | 왜 남기는가 |
|---|---|
| `PRD_사내프로덕션_릴리즈_계획.md` | 통합 원본(기준 PRD) |
| `PRD_보완_v1.2.md` | 통합 원본(스냅샷 기반 델타) |
| `PRD_MeetingMinutes_WebUI_패키징.md` | FR-1~11 이 현재 포터블 배포의 원래 근거다. 폐기하면 "왜 이렇게 만들었나"의 출처가 사라진다 |

> ⚠️ **`PRD_보완_v1.3.md`(소스 검증판) 원본은 보존되지 않았다.** 내용은 통합본의
> **부록 E · FR-014 · SEC-009 · UX-015 · §0.1~0.2** 로 전량 흡수됐지만, 파일 자체는 git 에 커밋되기
> 전에 사라져 복구할 수 없다. 통합본 §0 의 이력 표가 이 파일을 `archive/` 로 가리키고 있으므로
> **표기를 정정해야 한다.** 원본 사본을 아직 갖고 있다면 `archive/` 에 넣어 주기 바란다.
