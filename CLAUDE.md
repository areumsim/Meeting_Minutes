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
    `pricing`(비용), `app_paths`(경로 해석).
  - `meeting_pipeline/` — STT·실시간 전사·파이프라인·watcher·finalize·date_utils 등.
  - `wiki_core/` — `vault_indexer`(TF-IDF+임베딩 인덱스), `wiki_ask`(위키 Q&A), `wiki_knowledge`
    (prep-brief/레지스트리), `graph_db`/`graph_sync`(지식그래프), `obsidian`/`obsidian_fs`(볼트 접근).
- `web/` — `backend/`(FastAPI `app.py` + `api/*.py`) + `frontend/`(React/Vite → `frontend/dist`로 빌드).
- `scripts/build/` — 배포 빌드. `tests/` — 회귀 테스트. `docs/` — 아키텍처·사용 가이드.

## 실행 / 테스트 / 빌드

```bash
pip install -e .                       # 개발 설치
python run_meeting.py <cmd> [args]     # CLI (run_meeting.bat 도 동일)
webUI_실행.bat                          # 웹 UI 로컬 실행
python -m pytest                       # 테스트 (2026-07-30: 534 passed, 1 skipped)
python run_meeting.py reindex          # 위키/그래프 인덱스 재빌드
```

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
