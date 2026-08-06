# CLAUDE.md — 리포 작업 가이드 (AI/신규 기여자용)

Obsidian 기반 **회의록 자동화 + LLM Wiki 지식순환** 시스템. 오디오(파일/실시간)를 STT→분석→
회의록/노트로 만들고, 노트 폴더(.md)를 인덱싱해 위키 Q&A·지식그래프·사전 브리핑을 제공한다.
사내 배포용이며 기본 UI 언어는 한국어. 플랫폼은 Windows(PowerShell) 중심.

## 구조 (핵심 진입점)

- `meeting_minutes_app/` — 코어 파이썬 패키지
  - `cli.py` — 명령 디스패처(`meeting_minutes_app.cli:main`). `python run_meeting.py <cmd>` 또는
    `run_meeting.bat`로 호출. 주요 cmd: `realtime`/`record`, `batch`, `audio-watcher`,
    `vault-indexer`, `wiki-ask`, `prep-brief`, `reindex`.
  - `common/` — `config_loader`(설정+reload 훅), `config_schema`, `llm_client`(OpenAI/Anthropic),
    `pricing`(단가·비용 추정), `spend_guard`(지출 한도 판정+세션 밖 과금 기록),
    `usage_log`(세션 없는 사용량 테이블), `app_paths`(경로 해석).
  - `meeting_pipeline/` — STT·실시간 전사·파이프라인·watcher·finalize·date_utils 등.
  - `wiki_core/` — `vault_indexer`(TF-IDF+임베딩 인덱스), `wiki_ask`(위키 Q&A), `wiki_knowledge`
    (prep-brief/레지스트리), `graph_db`/`graph_sync`(지식그래프), `obsidian`/`obsidian_fs`(볼트 접근).
- `web/` — `backend/`(FastAPI `app.py` + `api/*.py`) + `frontend/`(React/Vite → `frontend/dist`로 빌드).
- `scripts/build/` — 배포 빌드. `tests/` — 회귀 테스트. `docs/` — 아키텍처·사용 가이드.

## 실행 / 테스트 / 빌드

```bash
pip install -e .                       # 개발 설치
python run_meeting.py <cmd> [args]     # CLI (run_meeting.bat 도 동일)
webUI_실행.bat                          # 웹 UI 로컬 실행 (데이터 = 리포 루트)
python -m pytest                       # 테스트 (2026-08-06: 1110 passed, 1 skipped) ← 수치 정본
cd web/frontend && npx vitest run      # 프런트 테스트 (2026-08-05: 105 passed) ← 정본 명령
python run_meeting.py reindex          # 위키/그래프 인덱스 재빌드
```

- **두 실행 방식은 데이터 폴더가 다르다**(`common/app_paths.get_base_dir`) — 개발 중 가장
  자주 걸리는 함정: `webUI_실행.bat`(소스)은 **리포 루트**의 `config.json`·`data/`·`output/`을
  쓰고, `MeetingMinutes.bat`(포터블)은 `MM_DATA_DIR`로 지정된 **자기 폴더의
  `MeetingMinutesData/`**를 쓴다(개인 키가 배포본에 섞이지 않게 한 의도된 격리).
  같은 PC 에서 둘을 동시에 켜면 8501 을 나눠 갖게 되어 브라우저가 다른 앱을 보여줄 수 있다 —
  런처가 포트를 옮기고 안내하지만(`common/server_launch.py`), 화면이 어느 쪽인지는
  [설정] → Obsidian 전체 진단의 "데이터 폴더" 항목으로 확인한다.
- **배포(포터블)**: `scripts/build/build_portable.ps1` → `dist/MeetingMinutesPortable.zip`.
  사용자는 압축 해제 후 `MeetingMinutes.bat` 실행(임베디드 파이썬 + pythonw). 이것이 **정본 배포 방식**.
  구형 PyInstaller exe(`build_exe.bat`)는 원격 MCP(`/mcp`)가 필요할 때만 쓰는 대체 경로.
- **fastmcp**: `/mcp`(원격 MCP) 전용 의존성. 포터블 배포본은 pywin32 문제로 의도적 제외 →
  미설치 환경에서 `tests/test_mcp_server.py`는 자동 skip(나머지 스위트는 정상).

## 설정 / 데이터

- `config.json` — 실제 설정(API 키·Gmail 등 **비밀 포함**). `.gitignore`로 커밋 차단됨. 절대 커밋 금지.
- `config.example.json` — 템플릿(각 키에 `_comment` 주석). 스키마는 `common/config_schema.py`.
- `data/vault_index.json` — 볼트 검색 인덱스(사용자별, gitignore).
- `input/`·`output/`·`web/meeting_assistant.db` — **사용자 데이터**. 재생성 산출물이 아니므로 삭제 금지.

## 작업 시 주의

- **`_tmp_*.txt` / `tmp_*.txt` 는 개인 스크래치 파일**(예: `_tmp_key.txt`, `_tmp_prompt.txt`). 개인 키/메모가
  들어있을 수 있고 `.gitignore`로 제외돼 있다. **삭제·커밋·내용 인용 금지** — 있는 그대로 둔다.
- `TODO.md` 도 gitignore 대상 개인 파일(코드 TODO + 개인 볼트 정리 로그 혼재).
- 재생성 가능 산출물(`build/`, `dist/MeetingMinutesPortable/`, `__pycache__/`, `.pytest_cache/`,
  `*.log`)만 정리 대상이다. 사용자 데이터(위 참조)는 건드리지 않는다.
- 위키 인덱싱과 지식그래프 백필은 **같은 노트 판정**을 쓴다 — `vault_indexer.iter_note_files()`
  (`*.md` 중 `_` 접두·그림자 사본(`*.txt.md` 등)·`indexing.exclude_dirs` 제외). 새로 볼트를
  스캔하는 코드는 이 함수를 쓴다(규칙을 복제하면 갈라진다 — 실제로 갈라진 적 있음).
  이전 필터로 들어온 그래프 노드는 `graph_sync.prune_shadow_note_nodes()`가 정리하며
  웹 [검색 인덱스·그래프 재빌드]가 백필 직전에 자동 실행한다.
  볼트 내용이 바뀌면 `reindex`로 재빌드해야 위키 검색에 반영된다.
  - **그림자 사본 규칙의 유일한 예외**: `indexing.reference_dirs`(기본 `[]`, 옵트인)에 적은
    폴더 **안의 문서형 확장자**(`.pdf/.pptx/.docx/.xlsx/.hwp` 등) 추출본은 회의 자료로 편입한다
    (`is_reference_note()`). 폴더는 **경로 세그먼트 정확 일치**이고, 코드·데이터
    (`.py/.ipynb/.json/.txt/.md/.sh`)는 그 폴더 안에 있어도 계속 제외된다 — 경로만으로 열면
    실볼트에서 비문서 170건이 함께 들어와 인덱스가 474→약 780으로 부푼다.
    편입분은 노트 메타에 `source: "reference"`가 붙어 `recent_notes()`의 '회의' 구제에서 빠진다
    (`01_회의_세미나`가 `meeting_dirs`의 `"회의"`에 substring으로 걸려 발표자료가 '최근 회의'로
    승격되던 자리).
- **비용에 관한 세 가지 단일 소스.** 같은 규칙이 복사돼 갈라진 전례가 이미 여러 번이다
  (단가 표 4곳, 노트 판정 2곳). 새 과금 경로를 추가할 때 이 셋을 우회하면 안 된다.
  - **추정**: `pricing.estimate_session_cost()`. 표를 직접 `.get` 하지 말고
    `stt_rate_per_min()`/`minutes_cost()`를 쓴다. 실시간은 `two_pass=`/`revise_model=`을
    반드시 넘긴다 — 안 넘기면 STT 과금을 한 번만 계산해 **신규 설치 기준 실제의 1/3**이 된다.
  - **2단계 보정(two_pass) 여부는 설정이 아니라 기록으로 판정한다.** 녹음 종료 시
    `sessions.stt_two_pass`에 **실제로 보정 워커가 돌았는지**를 남기고
    (`api/realtime.py`의 `self._two_pass` — 보정은 HTTP 청크 경로에만 있어 순수 WS
    세션은 0이다), 지난 세션을 다시 계산하는 쪽은 `pricing.resolve_two_pass()`를 쓴다.
    **`sessions.source`로 실시간을 판정하면 안 된다** — 웹 실시간과 웹 업로드가 둘 다
    `"web"`이라 구분이 불가능하고, 실제로 그래서 같은 회의를 대시보드는 $0.009/분,
    상세 화면은 $0.003/분으로 보여줬다. 구분자는 `mode`(`realtime_*`)이며 그 판정도
    `pricing.is_realtime_session()` 한 곳에만 둔다.
  - **한도**: `spend_guard.blocked()`. 업로드·재생성·폴더 감시·계획 자동화·임베딩이 모두 이
    함수를 지난다. **표시 금액과 한도 판정이 같은 함수에서 나와야 한다.**
  - **집계**: 세션이 있으면 `sessions.cost_estimate`(누적은 `db.add_session_cost()` — 
    `update_session_status`는 덮어쓰기라 부적합), 세션이 없으면 `spend_guard.record()`.
    `ingestion_pipeline`은 `web.backend.database`를 import하지 않아 **DB 세션이 안 생긴다** —
    그래서 워처 과금이 월 합계에서 영구히 안 보였다. 세션 없는 경로는 반드시 후자를 쓴다.
  - **한 과금을 두 곳에 적지 않는다.** `usage_log.month_to_date_spend()`는 `sessions` 합계와
    `usage_log` 합계를 **더한다**. 그래서 회의 중 도는 경로(`facilitation` 트리아지,
    `web_research` 웹 보완)는 세션이 있어도 `record()`만 쓰고 `cost_estimate`에는 넣지
    않는다 — 넣으면 월 합계가 두 번 센다. 대신 `note`에 세션 키를
    `spend_guard.session_note()` 규약으로 남기고, 회의별 금액은 `usage_log.session_spend()`
    로 되찾아 화면에 **실측값**으로 보여준다. 같은 이유로 `estimate_session_cost(facilitation=)`
    는 **사전 추정 경로(러닝 미터)에서만** 켠다 — finalize 기록 경로에서 켜면 이중 집계다.
  - **모델 이름은 '설정값'이 아니라 '실제 과금될 모델'을 쓴다.** `llm_client.chat`의 `model`
    인자는 GPT 전용이라 claude 계열을 고르면 `models.claude_model`이 대신 호출된다
    (`facilitation.effective_triage_model()`이 이 해석의 단일 소스). haiku 를 골랐는데
    opus 로 불리면서 추정은 haiku 단가였던 전례가 있다(실제의 1/12).
  - **테스트는 사용자 실제 DB에 과금을 기록하지 않는다** — `tests/conftest.py`의 autouse
    격리가 `usage_log` 기본 경로를 임시 DB로 돌린다. 이게 없던 동안 전체 스위트 1회당 가짜
    워처 지출이 개발 DB에 쌓여(발견 시 361행 ≈ $112.5) **한도 판정을 왜곡**했다.
- **경로는 CWD 가 아니라 데이터 베이스 기준으로 해석한다.** 두 웹 런처가 시작 시
  데이터 폴더로 `os.chdir` 하므로(`run_ui_exe.setup_paths`), DB·설정에 담긴 **상대 경로를
  CWD 로 풀면 엔트리포인트에 따라 다른 곳을 가리킨다.** `api/batch.py` 는
  `app_paths.get_output_dir()`, `web/backend/trash.py` 는 `_resolve()` 로 같은 규칙을 쓴다 —
  실제로 갈라져서, 완전 삭제가 폴더가 있는데도 "없다"고 판정해 **고아 폴더를 남기면서
  성공을 보고**했다(2026-08-03 포터블 실기 검증에서 발견).
- **세션 사이드카 테이블은 완전 삭제에서 함께 지운다.** `purge_session()`이 `segments`·
  `documents`·`related_notes`·`facilitation_log`·`facilitation_triage`를 지운다. 뒤의 두 개는
  core(`wiki_core.facilitation`)가 같은 sqlite 파일에 만드는 테이블이라 이 목록에서 빠져
  있었는데, `span` 컬럼에 **발화 원문 인용(≤500자)**이 들어간다 — 회의를 완전 삭제했는데
  회의 내용이 DB에 남았다. 새 사이드카를 만들면 이 목록에 추가한다(core 쪽은
  `delete_session_observations()`처럼 정리 함수를 노출하고, purge 는 **트랜잭션을 닫은 뒤**
  호출한다 — 별도 커넥션이라 트랜잭션 안에서 부르면 SQLITE_BUSY 로 30초 매달린다).
- **삭제는 두 단계다.** `DELETE /api/sessions/{id}` = 휴지통(soft delete, `deleted_at` 만
  세우고 전사·문서·관련노트는 남긴다), `.../purge` = 완전 삭제. purge 는 **폴더를 OS
  휴지통으로 옮긴 뒤에** DB 행을 지운다 — 순서를 바꾸면 이동 실패 시 고아 폴더가 남는다.
  재부활 방지의 핵심은 `session_scanner` 가 `db.known_output_dirs()`(**삭제분 포함**)를
  보는 것이다. `list_sessions()` 를 쓰면 지운 회의가 재시작 후 되살아난다.
- **중복 실행은 데이터 폴더 단위 락으로 막는다**(`server_launch.acquire_instance_lock`).
  포트로 판정할 수 없다 — `find_free_port` 가 점유 시 다른 포트로 옮기므로 첫 인스턴스가
  랜덤 포트에 있을 수 있다. 두 서버가 같은 폴더에 뜨면 워처가 둘이 되어 중복 과금하고,
  두 번째 `init_db()` 가 첫 인스턴스의 진행 중 세션을 `error` 로 바꾼다.
- **자동 실행 경로는 두 관문을 지난다** — `spend_guard.automation_paused()`(전역 일시정지,
  설정값이라 재시작에도 유지된다)와 `spend_guard.blocked()`(한도). 워처는 한도 초과·기존 파일을
  `status="queued"` 확인 대기열에 넣고, `queued`는 **터미널 상태**다(매 폴링 재판정 방지).
  승인은 `reprocess()`로 상태를 지우는 것이며, 그 뒤 한도 검사를 **다시** 지난다 —
  승인이 한도를 우회하는 뒷문이 되면 안 된다.
- **워처의 재시도 계약**(무인 경로라 셋 다 돈과 직결된다):
  - `processing`은 터미널이 **아니다**(크래시 후 재시도되어야 한다). 그래서 상태만으로는
    중복 제출을 못 막는다 — 같은 프로세스 안의 중복은 `_claim()`/`_inflight` 선점이 막는다.
    이게 없던 동안 60분짜리 파일 하나가 처리되는 내내 매 스캔마다 재제출돼 **STT가 중복
    과금**됐다. 새 트리거(이벤트·재스캔)를 추가할 때 반드시 `_handle_file()`을 거친다.
  - `failed`는 **유한 재시도**다(`MAX_PROCESS_ATTEMPTS`). 실패가 STT *이후* 단계에서 나면
    재시도마다 STT를 다시 태우기 때문이다. 상한을 넘으면 `queued`(확인 대기열)로 보내
    사람이 승인하게 한다. `_mark_processed()`는 항목을 새로 만들므로 `attempts`를
    **이어받아야** 한다 — 안 그러면 `processing` 표시가 카운터를 지워 상한이 안 걸린다.
  - watchdog 모드에도 **안전 재스캔**이 필요하다(`SAFETY_RESCAN_MIN_SEC`). `on_created`는
    파일이 만들어지는 순간 와서 아직 쓰이는 중이고(`_is_stable()`=False), `on_modified`는
    듣지 않는다 — 재스캔이 없으면 녹음기가 폴더에 직접 쓰는 파일을 영영 놓친다.
    재스캔은 `_scan_once()`가 풀을 `with`로 열어 블로킹하므로 **별도 스레드**에서 돈다
    (대기 루프에서 직접 부르면 처리 중에 `stop()`이 먹지 않는다).
- **화면 표시를 끄는 조작은 서버 생성·검색·과금까지 멈춘다.** 페르소나 카드
  (`facilitation.mute()`)와 관련 노트(`realtime_search.mute()`)가 같은 계약을 쓴다.
  프런트에서 목록만 숨기면 서버는 회의 끝까지 만들고 검색한다 — 아무도 볼 수 없는
  산출물에 돈을 쓰는 것이고, 게다가 화면의 러닝 미터는 버려진 카드 금액을 더하지 않아
  **표시 금액이 실제 과금보다 작아진다**(이 리포가 금지하는 갈라짐). 관련 노트 쪽은
  볼트 검색만 막으면 **웹 보완(검색 1,000회당 $10)만 남아 더 나빠진다** — `mute` 는
  `_maybe_web_research()` guard 까지 세트다. 새 "끄기"를 만들 땐 이 셋(생성·검색·웹)을
  전부 지나는지 확인한다. 이미 모인 결과는 지우지 않는다(이미 지불했다).
  WS 메시지는 **두 수신 루프 모두**에 배선한다(WS 경로 + HTTP 폴백 경로) — 한쪽만
  넣으면 폴백 세션에서 조용히 안 먹는다.
- **실시간 개입의 재료는 종료 후 경로와 같은 소스를 쓴다.** 지난 결정·미완료 액션은
  `wiki_knowledge.recent_decisions_for()` / `open_actions_for()` 하나만 부른다
  (회의 준비 브리핑·회의록 맥락 조립도 같은 함수다). registry 로딩·주제 필터를
  `facilitation` 안에 복제하면 "회의 후에는 잡히는데 회의 중에는 안 잡히는" 갈라짐이
  생긴다 — 실제로 실시간 경로의 registry 참조가 **0건**이라 "이전 회의와 다르다"를
  판정할 입력 자체가 없었다. 볼트 검색도 같다: 개입 근거는 `RealtimeVaultSearcher`
  의 `search_now()` 를 쓴다(랭킹 함수를 새로 만들지 않는다 —
  docs/검색랭킹_이론과근거.md 의 실측으로 고른 규칙이다).
  - **그 재료를 '근거'로 쓸 거면 `evidence` 목록에 담는다.** 프롬프트에만 넣으면
    코드 가드(`_generate` 의 "근거 없으면 개입 금지")와 화면 카드가 그 존재를 모른다 —
    실제로 registry 를 별도 블록으로만 주는 동안 볼트 히트 0인 회의에서 팩트체커·
    도메인 전문가의 개입이 **통째로 버려졌고**(볼트 없는 사용자는 영구 침묵), 카드는
    무엇과 대조했는지 못 보여줬다. 가드·프롬프트·카드가 한 목록을 본다.
  - **필터 기준이 없으면 재료를 주입하지 않는다.** `_filter_*_by_topic` 은 "기준이
    없으면 전체 반환" 계약이다(브리핑에서는 사용자가 요청한 것이라 맞다). 주제가 빈
    회의에 그대로 쓰면 다른 프로젝트의 결정으로 "어긋남"을 판정하게 된다.
  - **재료 로딩은 워커 스레드에서 1회.** 오케스트레이터는 이벤트 루프 스레드에서
    만들어지므로 생성자에서 파일을 읽으면 '녹음 시작'이 그만큼 늦어진다.
- **'끄기'와 '취소'는 두 수신 루프 모두에 배선하고, 취소는 새 과금을 만들지 않는다.**
  WS 경로에 `cancel` 이 없던 동안 [취소]는 '연결 끊김'으로 보여 **정상 종료** 경로로
  갔고, 버린 회의에 마지막 정리(LLM)와 회의록 생성이 그대로 돌았다. 취소 정리는
  `_cancel_session()` 한 곳이며 관찰 로그(발화 인용)까지 지운다 — 사용자가 "저장하지
  않는다"를 고른 회의의 내용이 DB 에 남으면 안 된다.
- **자동 생성 경로는 그 기능의 '끔' 설정을 우회하지 않는다.** 종료 시 마지막 요약은
  사용자가 누른 것이 아니라 자동 생성이므로 주기 0(자동 요약 끔)이면 만들지 않는다.
  버튼(`brief_now`)만 설정과 무관하게 동작한다 — 그건 사용자가 그때 누른 것이다.
- **병렬로 끝나는 산출물의 순서는 완료 순서가 아니라 제출 순번으로 정한다.** 요약 풀은
  워커가 2개라 나중 구간이 먼저 끝날 수 있고, 완료 순서로 번호를 매기면 '중간 정리'
  문서가 회의 흐름을 거꾸로 보여준다. 발화 타임스탬프가 없는 호출자(CLI·테스트)도
  있어 `t0` 는 정렬 키가 되지 못한다.
- **컬럼·필드 이름은 값의 뜻과 같아야 한다.** `facilitation_log.span_confirmed` 는
  "보정이 인용을 바꾸지 않았다"는 뜻이고, 초기 이름 `revised` 는 `1` 이 '바뀌었다'로
  읽혀 **정반대**였다(§15 오탐률 해석에 직결되는 필드였다). 파생 수치도 같은 규칙:
  `provisional_unconfirmed` 는 "바뀐 것"이 아니라 **모르는 것**이며 CLI 출력도 그렇게
  적는다(대조가 안 돌았을 수도 있다).
