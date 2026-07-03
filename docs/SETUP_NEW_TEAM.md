# 새 팀 설치 가이드

사내 다른 팀이 자기 Obsidian vault·API 키로 이 도구를 독립적으로 쓰기 위한 설치 절차입니다.
운영 중(day-2) 사용법은 이 문서가 아니라 [`docs/GUIDE_Obsidian_Claude.md`](GUIDE_Obsidian_Claude.md),
[`docs/회의비서_사용법.md`](회의비서_사용법.md), [`docs/GUIDE_녹음_요약_메일.md`](GUIDE_녹음_요약_메일.md)를 참고하세요.
아키텍처/모듈 구조는 [`docs/ARCHITECTURE.md`](ARCHITECTURE.md)에 있습니다.

---

## 배포 채널 선택

| 채널 | 대상 | 필요 사항 |
|---|---|---|
| **`.exe` 배포판 (권장, 비개발자용)** | 팀 내 실제 사용자 | Python 설치 불필요. 사내 파일 공유/릴리스 페이지에서 `MeetingMinutes` 폴더를 통째로 받음 |
| **`pip install -e .` (개발/셋업 담당자용)** | 팀 IT·파워유저가 처음 설치를 세팅할 때 | Python 3.10+, git |

두 채널 모두 아래 "2. Obsidian 연동 준비"와 "3. 설정"은 동일합니다. `.exe`를 쓰는 팀이라도
최초 설치는 보통 IT/파워유저 한 명이 `pip install -e .` 경로로 확인해보고, 실제 배포는 `.exe`로
하는 경우가 많습니다.

---

## 1. 사전 준비

- **Python 3.10 이상** (`.exe` 채널만 쓸 경우 불필요)
- **ffmpeg**: <https://www.gyan.dev/ffmpeg/builds/> 에서 다운로드 후 PATH에 추가 (Windows),
  또는 `brew install ffmpeg`(Mac) / `apt install ffmpeg`(Linux)
- **Obsidian** (Wiki/그래프 기능을 쓰려면 필요 — 없어도 회의록 생성 자체는 동작합니다. 회의록은
  Obsidian이 꺼져 있으면 로컬 `output/` 폴더에만 저장됩니다)
- **OpenAI 및/또는 Anthropic API 키** — 최소 하나 필요 (STT는 OpenAI 필수, 회의록 생성 LLM은
  둘 중 하나 선택 가능)

## 2. Obsidian 연동 준비 (선택이지만 권장)

Wiki 지식 순환(Registry, Wiki Context/Proposal)과 이번에 추가된 그래프 기능은 Obsidian이 있어야
의미가 있습니다. 없어도 회의록/요약/액션 추출은 정상 동작합니다.

1. Obsidian에서 팀의 새 vault를 만들거나 기존 vault를 엽니다.
2. 커뮤니티 플러그인 **"Local REST API (with MCP)"**를 설치하고 활성화합니다.
3. 플러그인 설정 화면에서 **API Key**를 복사해둡니다 (아래 `meeting-minutes init`에서 입력).
4. Obsidian을 실행 상태로 유지합니다 (플러그인이 `https://127.0.0.1:27124`로 로컬 서버를 띄웁니다).

> 여러 팀이 **같은 vault를 공유**해야 한다면, `config.json`의 `obsidian.project`(또는
> `obsidian.project_domains` 매핑)로 팀별 폴더를 분리할 수 있습니다 — vault 자체를 나눌 필요는
> 없습니다. 자세한 내용은 `config.example.json`의 `obsidian._project_comment`를 참고하세요.

## 3. 설치

### 3-A. `.exe` 채널 (비개발자용)

1. 배포된 `MeetingMinutes/` 폴더 전체를 받습니다.
2. `config.example.json`을 같은 폴더에 `config.json`으로 복사하거나, `MeetingMinutes.exe`를
   한 번 실행한 뒤 콘솔 안내에 따라 설정합니다.
3. `MeetingMinutes.exe`를 실행하면 브라우저에 웹 UI가 열립니다.

### 3-B. `pip install -e .` 채널 (개발/셋업 담당자용)

```bash
git clone <이 저장소 URL>
cd Meeting_Minutes
pip install -e .
meeting-minutes init
```

`meeting-minutes init`은 다음을 처리합니다:
- `config.example.json` → `config.json` 복사 (이미 있으면 건드리지 않음 — 재설정하려면 `--force`)
- Obsidian vault 경로, API Key, 팀/프로젝트 이름, OpenAI/Anthropic API 키를 대화형으로 입력
- 입력한 값으로 Obsidian 연결(`ping`)과 LLM API 키를 즉시 검증해 결과를 출력 (실패해도 설정은
  저장되며, 나중에 `config.json`을 직접 고칠 수 있습니다)

설정을 마치면 `run_meeting.bat`(Windows) 또는 `meeting-minutes web`으로 웹 UI를, `meeting-minutes
batch <파일>`로 CLI 배치 처리를 시작할 수 있습니다.

## 4. 격리 확인 (팀 간 데이터가 섞이지 않는 이유)

`config.json`과 `data/`(action/decision registry, vault 인덱스, 그래프 DB)는 모두 `.gitignore`
대상이고, 각자의 설치 디렉토리(또는 `.exe` 폴더) 기준 상대경로로 해석됩니다. 즉:

- 팀 A가 `C:\Teams\A\Meeting_Minutes`에, 팀 B가 `C:\Teams\B\Meeting_Minutes`에 각각
  `git clone` + `pip install -e .` + `meeting-minutes init`을 하면, 두 팀의 `config.json`/`data/`는
  완전히 독립적입니다 — 코드를 고칠 필요가 전혀 없습니다.
- `.exe` 채널도 마찬가지로 배포 폴더별로 격리됩니다.

## 5. 첫 실행 검증 체크리스트

1. `meeting-minutes batch <짧은 테스트 음성 파일>` (또는 웹 UI에서 파일 업로드) 실행.
2. `output/<날짜>_<제목>/`에 `*_minutes.md`, `*_summary.md`가 생성됐는지 확인.
3. Obsidian을 설정했다면, vault의 `obsidian.meetings_path`(또는 기본 `00_Meetings/`) 폴더에
   노트가 저장됐는지 확인.
4. (선택) `python scripts/graph_backfill.py --dry-run`으로 그래프 백필이 정상 동작하는지 확인 후,
   `--dry-run` 없이 실행해 `data/wiki_graph.db`를 채웁니다. 웹 UI의 세션 상세 화면에서 "Graph" 탭이
   보이면 정상입니다 (서버 경유 세션에서만 채워집니다 — standalone 모바일 녹음은 해당 없음).

## 6. 업데이트 받기

- **git 채널**: `git pull` (또는 새 태그로 `git checkout`) 후 의존성이 바뀐 경우에만
  `pip install -e .`를 다시 실행하면 됩니다. `config.json`/`data/`는 gitignore 대상이라 diff에
  걸리지 않으므로 안전합니다.
- **`.exe` 채널**: 새 배포 폴더를 받은 뒤 기존 폴더의 `config.json`과 `data/`를 새 폴더로
  복사해오세요 (exe 폴더를 통째로 교체하면 두 파일/폴더가 함께 사라지므로, 교체 전 반드시
  별도 위치에 백업했다가 복원하세요).

## 7. 유지보수자용 — `.exe` 재빌드

```bash
pip install -e .
python -m pip install pyinstaller
scripts\build\build_exe.bat
```

`scripts/build/build_exe.spec`이 `meeting_minutes_app`(common/wiki_core/meeting_pipeline
서브패키지 포함)과 `web/backend`, 빌드된 `web/frontend/dist`를 함께 패키징합니다. 결과물은
`dist/MeetingMinutes/`에 생성됩니다.
