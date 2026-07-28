# 새 팀 설치 가이드

사내 다른 팀이 자기 Obsidian vault·API 키로 이 도구를 독립적으로 쓰기 위한 설치 절차입니다.
운영 중(day-2) 사용법은 이 문서가 아니라 [`docs/GUIDE_Obsidian_Claude.md`](GUIDE_Obsidian_Claude.md),
[`docs/회의비서_사용법.md`](회의비서_사용법.md), [`docs/GUIDE_녹음_요약_메일.md`](GUIDE_녹음_요약_메일.md)를 참고하세요.
아키텍처/모듈 구조는 [`docs/ARCHITECTURE.md`](ARCHITECTURE.md)에 있습니다.

---

## 배포 채널 선택

| 채널 | 대상 | 필요 사항 |
|---|---|---|
| **포터블 배포판 (권장, 비개발자용)** | 팀 내 실제 사용자 | Python 설치 불필요(임베디드 파이썬 동봉). 사내 파일 공유/릴리스 페이지에서 `MeetingMinutesPortable.zip`을 받아 풀고 `MeetingMinutes.bat` 실행 |
| **`pip install -e .` (개발/셋업 담당자용)** | 팀 IT·파워유저가 처음 설치를 세팅할 때 | Python 3.10+, git |

> MCP(`/mcp`) 원격 서버가 꼭 필요한 팀만 대체 경로로 PyInstaller `.exe` 빌드를 씁니다(아래 "7. 유지보수자용 — 재빌드" 참고). 포터블 배포판에는 `fastmcp`가 포함되지 않습니다.

두 채널 모두 아래 "2. Obsidian 연동 준비"와 "3. 설정"은 동일합니다. 포터블 배포판을 쓰는 팀이라도
최초 설치는 보통 IT/파워유저 한 명이 `pip install -e .` 경로로 확인해보고, 실제 배포는 포터블 zip으로
하는 경우가 많습니다.

---

## 1. 사전 준비

- **Python 3.10 이상** (포터블 배포판만 쓸 경우 불필요 — 임베디드 파이썬이 동봉됩니다)
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

### 3-A. 포터블 배포판 (비개발자용)

1. 배포된 `MeetingMinutesPortable.zip`을 원하는 폴더에 전부 풉니다.
2. 처음 `MeetingMinutes.bat`를 실행하면 브라우저에 설정 마법사가 뜹니다. 수동으로 하려면
   `config.example.json`을 같은 폴더에 `config.json`으로 복사해 둡니다.
3. `MeetingMinutes.bat`를 실행하면 브라우저에 웹 UI가 열립니다(진단은 `Troubleshoot.bat`).
   비개발자용 상세 설치 안내는 배포본 동봉 `사용법.txt`([`scripts/build/사용법_포터블.txt`](../scripts/build/사용법_포터블.txt))를 참고하세요.

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
- 포터블 배포판도 마찬가지로 배포 폴더별로 격리됩니다.

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
- **포터블 배포판**: 새 `MeetingMinutesPortable.zip`을 새 폴더에 푼 뒤 기존 폴더의 `config.json`과
  `data/`(또는 `MeetingMinutesData/`)를 새 폴더로 복사해오세요 (폴더를 통째로 교체하면 이 파일/폴더가
  함께 사라지므로, 교체 전 반드시 별도 위치에 백업했다가 복원하세요).

## 7. 유지보수자용 — 재빌드

### 7-A. 포터블 배포판 (기본 배포 경로)

```powershell
scripts\build\build_portable.ps1
```

(더블클릭용 래퍼는 `scripts\build\build_portable.bat`.) 임베디드 파이썬 런타임에
`meeting_minutes_app`·`web/backend`·빌드된 `web/frontend/dist`를 함께 담아 `MeetingMinutes.bat`
(pythonw 런처)·`Troubleshoot.bat`·`사용법.txt`를 포함한 배포본을 만듭니다. 결과물은
`dist/MeetingMinutesPortable.zip`이며, 사용자는 이 zip을 풀어 `MeetingMinutes.bat`를 실행합니다.

### 7-B. PyInstaller `.exe` (MCP(`/mcp`) 필요 시 대체 경로)

```bash
pip install -e .
python -m pip install pyinstaller
scripts\build\build_exe.bat
```

`scripts/build/build_exe.spec`이 `meeting_minutes_app`(common/wiki_core/meeting_pipeline
서브패키지 포함)과 `web/backend`, 빌드된 `web/frontend/dist`를 함께 패키징합니다. 결과물은
`dist/MeetingMinutes/`에 생성됩니다.

> 이 exe 경로는 **원격 MCP 서버(`/mcp`, fastmcp)가 필요한 경우에만** 씁니다. 포터블 배포판은
> 임베디드 파이썬/`pywin32` 호환 문제로 `fastmcp`를 의도적으로 제외하므로, `/mcp`를 서빙해야 하면
> 이 PyInstaller 빌드를 대체로 사용하세요. 일반 배포는 7-A(포터블)를 기본으로 합니다.
