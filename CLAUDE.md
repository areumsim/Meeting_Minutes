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
python -m pytest                       # 테스트 (2026-08-03: 893 collected, 1 skipped) ← 수치 정본
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
    반드시 넘긴다 — 안 넘기면 STT 과금을 한 번만 계산해 **신규 설치 기준 실제의 1/3**이 된다
    (`is_two_pass_source()`가 어느 출처에 적용되는지 판정한다).
  - **한도**: `spend_guard.blocked()`. 업로드·재생성·폴더 감시·계획 자동화·임베딩이 모두 이
    함수를 지난다. **표시 금액과 한도 판정이 같은 함수에서 나와야 한다.**
  - **집계**: 세션이 있으면 `sessions.cost_estimate`(누적은 `db.add_session_cost()` — 
    `update_session_status`는 덮어쓰기라 부적합), 세션이 없으면 `spend_guard.record()`.
    `ingestion_pipeline`은 `web.backend.database`를 import하지 않아 **DB 세션이 안 생긴다** —
    그래서 워처 과금이 월 합계에서 영구히 안 보였다. 세션 없는 경로는 반드시 후자를 쓴다.
- **자동 실행 경로는 두 관문을 지난다** — `spend_guard.automation_paused()`(전역 일시정지,
  설정값이라 재시작에도 유지된다)와 `spend_guard.blocked()`(한도). 워처는 한도 초과·기존 파일을
  `status="queued"` 확인 대기열에 넣고, `queued`는 **터미널 상태**다(매 폴링 재판정 방지).
  승인은 `reprocess()`로 상태를 지우는 것이며, 그 뒤 한도 검사를 **다시** 지난다 —
  승인이 한도를 우회하는 뒷문이 되면 안 된다.
