<#
build_portable.ps1 — 포터블(임베디드 파이썬 + bat) 배포본 조립
==============================================================
PyInstaller exe 를 대체하는 배포 방식. python.org 임베디드 파이썬(PSF 서명된
python.exe/pythonw.exe)에 소스와 웹 런타임 의존성을 얹어, 백신 스캔에 걸려
느리던 exe 첫 실행 문제를 없앤다.

산출물: dist\MeetingMinutesPortable\  (+ dist\MeetingMinutesPortable.zip)
  ├─ MeetingMinutes.bat / Troubleshoot.bat / 사용법.txt
  ├─ python-embed\            (임베디드 파이썬, python313._pth 구성됨)
  ├─ Lib\site-packages\       (pip install --target 웹 런타임 의존성)
  └─ app\                     (소스: 리포 구조 부분집합)

전제: 빌드 PC 에 CPython 3.13(임베디드와 동일 계열) + Node/npm 설치.
환경변수:
  SKIP_FFMPEG=1   ffmpeg 없이 빌드(영상 변환 비활성 감수)
#>
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest 진행바 비활성(속도)

# ── 경로 ───────────────────────────────────────────────────────────
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root      = (Resolve-Path (Join-Path $ScriptDir '..\..')).Path
$DistDir   = Join-Path $Root 'dist'
$OutDir    = Join-Path $DistDir 'MeetingMinutesPortable'
$AppDir    = Join-Path $OutDir 'app'
$EmbedDir  = Join-Path $OutDir 'python-embed'
$SiteDir   = Join-Path $OutDir 'Lib\site-packages'

function Step($n, $msg) { Write-Host "`n [$n] $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host " [ERROR] $msg" -ForegroundColor Red; exit 1 }

# 네이티브 exe(npm/pip/python) 호출 헬퍼.
# Windows PowerShell 5.1 은 $ErrorActionPreference='Stop' 에서 네이티브 프로그램이
# stderr 에 한 줄이라도 쓰면(pip/npm 은 진행상황·경고를 stderr 로 보냄) 이를
# 치명적 오류로 바꿔 exit 0 이어도 스크립트를 죽인다. EAP 를 잠시 Continue 로
# 낮추고 종료코드(exitCode)만으로 성공/실패를 판정한다.
function Invoke-Native {
    param([Parameter(Mandatory)][string]$What,
          [Parameter(Mandatory)][string]$File,
          [string[]]$Arguments = @())
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $File @Arguments 2>&1 | ForEach-Object { Write-Host $_ }
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $prev }
    if ($code -ne 0) { Fail "$What 실패 (exit $code)" }
}

Write-Host "==============================================" -ForegroundColor Green
Write-Host "  Meeting Minutes - Portable Build"            -ForegroundColor Green
Write-Host "==============================================" -ForegroundColor Green

# ── 빌드 파이썬 버전 확인(임베디드와 일치시켜야 컴파일 wheel ABI 맞음) ──
$pyVer = (& python -c "import sys;print('%d.%d.%d'%sys.version_info[:3])").Trim()
if ($LASTEXITCODE -ne 0 -or -not $pyVer) { Fail "python 을 찾을 수 없습니다. CPython 3.13 을 PATH 에 두세요." }
$verParts = $pyVer.Split('.')
if ("$($verParts[0]).$($verParts[1])" -ne '3.13') {
    Fail "빌드 파이썬이 $pyVer 입니다. 임베디드 배포(3.13)와 맞추려면 CPython 3.13 으로 빌드하세요."
}
$tag = "python$($verParts[0])$($verParts[1])"   # python313
Write-Host "  빌드 파이썬: $pyVer  (임베디드 태그: $tag)"

# ── 1. 프론트엔드 빌드 ────────────────────────────────────────────
Step '1/8' 'Building frontend...'
# `npm install` 이 아니라 `npm ci` 를 쓴다 — 파이썬 쪽을 constraints 로 고정한 것과 같은 이유다.
# `npm install` 은 package.json 의 범위(`^`)를 다시 해석해 **lockfile 을 갱신할 수 있고**,
# 그러면 같은 커밋에서 빌드해도 다른 번들이 나온다. `npm ci` 는 lockfile 을 그대로 설치하고
# package.json 과 어긋나면 실패한다(그 실패가 조용한 드리프트보다 낫다).
$lockFile = Join-Path $Root 'web\frontend\package-lock.json'
if (-not (Test-Path $lockFile)) {
    Fail @"
package-lock.json 이 없습니다: $lockFile
  릴리즈 빌드는 lockfile 을 그대로 설치해야 재현이 됩니다.
  `npm install` 로 lockfile 을 만들고 **커밋한 뒤** 다시 빌드하세요.
"@
}
$lockHash = (Get-FileHash $lockFile -Algorithm SHA256).Hash.Substring(0,12)
Write-Host "  package-lock sha256:$lockHash (npm ci 로 그대로 설치)"
Push-Location (Join-Path $Root 'web\frontend')
try {
    # npm ci 는 node_modules 를 지우고 새로 설치한다 — 이전 빌드의 잔재가 섞이지 않는다.
    Invoke-Native 'npm ci' 'npm.cmd' @('ci')
    Invoke-Native 'npm run build' 'npm.cmd' @('run','build')
} finally { Pop-Location }
if (-not (Test-Path (Join-Path $Root 'web\frontend\dist\index.html'))) { Fail '프론트엔드 빌드 결과(index.html)가 없습니다.' }

# ── 1.5 ffmpeg 확인 ───────────────────────────────────────────────
$ffmpeg = Join-Path $Root 'vendor\ffmpeg\ffmpeg.exe'
if (-not (Test-Path $ffmpeg)) {
    if ($env:SKIP_FFMPEG -eq '1') { Write-Host "  [WARN] vendor\ffmpeg\ffmpeg.exe 없음 - ffmpeg 없이 빌드" -ForegroundColor Yellow }
    else { Fail "vendor\ffmpeg\ffmpeg.exe 가 없습니다. vendor\ffmpeg\README.txt 참고 또는 SKIP_FFMPEG=1." }
}

# ── 2. 출력 폴더 초기화 (기존 MeetingMinutesData 는 보존) ─────────
Step '2/8' 'Preparing output folder...'
$dataBak = $null
if (Test-Path (Join-Path $OutDir 'MeetingMinutesData')) {
    # 기존 사용자 데이터(설정·회의록·API 키) 보존. 이 폴더는 실기 검증으로 배포본을
    # 실행하면 만들어지고, 그 안에 config.json(키)·meeting_assistant.db(회의)가 있다.
    $dataBak = Join-Path $env:TEMP ("MMP_DATA_" + [System.IO.Path]::GetRandomFileName())
    try {
        Move-Item (Join-Path $OutDir 'MeetingMinutesData') $dataBak -ErrorAction Stop
        Write-Host "  기존 데이터 백업: $dataBak"
    } catch {
        # **백업 실패는 빌드를 중단시킨다.** 종전엔 경고만 남기고 계속 진행했는데,
        # 바로 아래에서 OutDir 내용물을 지우므로 그 데이터가 **부분 파괴**됐다
        # (잠긴 파일은 남고, 잠기지 않은 config.json 은 삭제된다). 경고 문구도
        # "릴리즈 zip엔 미포함"이라 안심하고 넘기게 되어 있었다.
        # 원인은 거의 항상 '배포본이 실행 중'이다 — 닫고 다시 빌드하면 된다.
        $dataBak = $null
        Fail ("MeetingMinutesData 를 백업할 수 없습니다(잠김/사용중). " +
              "실행 중인 MeetingMinutes.bat / 서버를 닫고 다시 빌드하세요. " +
              "이 상태로 계속하면 그 폴더의 config.json(API 키)과 회의 DB 가 지워집니다. " +
              "정말 버려도 되면 해당 폴더를 직접 삭제한 뒤 다시 실행하세요.")
    }
}
if (Test-Path $OutDir) {
    # OutDir 자체가 다른 프로세스의 '현재 폴더(cwd)'로 잡혀 삭제가 거부되는 경우가 있다
    # (예: 폴더 안에서 백그라운드 서버를 띄운 잔재). 폴더 노드 삭제는 그런 경우 실패하므로
    # 노드는 두고 '내용물'만 지워 스테일 파일을 제거한다(견고화).
    Get-ChildItem $OutDir -Force | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
} else {
    New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
}
New-Item -ItemType Directory -Force -Path $AppDir  | Out-Null
New-Item -ItemType Directory -Force -Path $SiteDir | Out-Null

# ── 3. 임베디드 파이썬 준비 (vendor 캐시 우선, 없으면 다운로드) ───
Step '3/8' 'Preparing embeddable Python...'
$embedZipName = "python-$pyVer-embed-amd64.zip"
$vendorEmbed  = Join-Path $Root "vendor\python-embed\$embedZipName"
if (-not (Test-Path $vendorEmbed)) {
    New-Item -ItemType Directory -Force -Path (Split-Path $vendorEmbed) | Out-Null
    $url = "https://www.python.org/ftp/python/$pyVer/$embedZipName"
    Write-Host "  vendor 캐시 없음 → 다운로드: $url"
    try { Invoke-WebRequest -Uri $url -OutFile $vendorEmbed }
    catch { Fail "임베디드 파이썬 다운로드 실패: $url  (오프라인이면 이 zip 을 vendor\python-embed\ 에 직접 넣으세요)" }
}
Expand-Archive -Path $vendorEmbed -DestinationPath $EmbedDir -Force
if (-not (Test-Path (Join-Path $EmbedDir 'pythonw.exe'))) { Fail '임베디드 파이썬 전개 실패(pythonw.exe 없음).' }

# ── 3.5 python313._pth 구성: import site 활성 + app / site-packages 경로 ──
$pthFile = Join-Path $EmbedDir "$tag._pth"
if (-not (Test-Path $pthFile)) { Fail "$tag._pth 를 찾을 수 없습니다." }
# ._pth 경로는 실행파일(=python-embed) 폴더 기준 상대경로. 소스와 site-packages 를 sys.path 에 추가.
$pthLines = @(
    "$tag.zip"
    "."
    "..\app"
    "..\app\meeting_minutes_app"
    "..\Lib\site-packages"
    ""
    "# site 를 켜야 site-packages 의 .pth/메타데이터가 정상 처리됨"
    "import site"
)
Set-Content -Path $pthFile -Value $pthLines -Encoding ascii
Write-Host "  $tag._pth 구성 완료"

# ── 4. 웹 런타임 의존성 설치 (--target) ───────────────────────────
Step '4/8' 'Installing web runtime dependencies (pip --target)...'
$reqWeb = Join-Path $ScriptDir 'requirements-web.txt'
if (-not (Test-Path $reqWeb)) { Fail "requirements-web.txt 가 없습니다: $reqWeb" }

# constraints 로 **정확한 버전 조합**을 고정한다. requirements-web.txt 는 `>=` 범위라
# pip 이 빌드 시점에 버전을 정하고, 그래서 같은 커밋을 다시 빌드해도 다른 조합이 나왔다.
# 더 나쁜 것은 테스트가 검증한 조합과 배포 조합이 다르다는 점이다(2026-08-03 실측:
# uvicorn 0.41 vs 0.52, fastapi 0.135 vs 0.141, openai 2.46 vs 2.52 …).
# 파일이 없으면 **막지 않고 경고**한다 — 의존성을 의도적으로 올리는 갱신 절차가
# "constraints 를 지우고 빌드"이기 때문이다(constraints-web.txt 머리말 참고).
# « --no-warn-conflicts 를 쓰는 이유 »
# `--target` 은 격리 설치인데 pip 은 설치 후 **빌드 PC 의 환경**과 비교해 충돌을 보고한다.
# 그 메시지가 "ERROR:" 로 시작해서, 빌드 PC 에 개발 설치(`pip install -e .`)나 scipy 가
# 있으면 성공한 빌드가 실패처럼 보인다(실측: "meeting-minutes requires fastmcp ... not
# installed", "scipy requires numpy<2.5"). 배포본과 무관한 잡음이다.
# **target 내부의 진짜 충돌은 resolver 오류로 빌드를 멈추므로** 이 옵션으로 가려지지 않는다.
$pipArgs = @('-m','pip','install','--target',$SiteDir,'--no-warn-script-location',
             '--no-warn-conflicts','-r',$reqWeb)
$constraints = Join-Path $ScriptDir 'constraints-web.txt'
$conHash = 'none'
if (Test-Path $constraints) {
    $pipArgs += @('-c',$constraints)
    $conHash = (Get-FileHash $constraints -Algorithm SHA256).Hash.Substring(0,12)
    # 주석의 `=====` 구분선이 '==' 에 걸리므로 **실제 고정 줄만** 센다(예전엔 52로 표시됐다).
    $nPins = @(Get-Content $constraints |
               Where-Object { $_ -notmatch '^\s*#' -and $_ -match '\S==\S' }).Count
    Write-Host "  constraints 고정: $nPins 개 (sha256:$conHash)"
} else {
    Write-Host "  [경고] constraints-web.txt 가 없습니다 — 버전이 빌드 시점에 결정됩니다." -ForegroundColor Yellow
    Write-Host "         새 조합을 채택하려면 실기 검증 후 이 파일을 갱신하세요." -ForegroundColor Yellow
}
Invoke-Native 'pip install --target' 'python' $pipArgs

# ── 5. 소스 복사 (리포 구조 부분집합 → app\) ─────────────────────
Step '5/8' 'Copying application source...'
Copy-Item (Join-Path $Root 'meeting_minutes_app') (Join-Path $AppDir 'meeting_minutes_app') -Recurse -Force
New-Item -ItemType Directory -Force -Path (Join-Path $AppDir 'web') | Out-Null
Copy-Item (Join-Path $Root 'web\backend')      (Join-Path $AppDir 'web\backend') -Recurse -Force
Copy-Item (Join-Path $Root 'web\__init__.py')  (Join-Path $AppDir 'web\__init__.py') -Force
Copy-Item (Join-Path $Root 'web\frontend\dist') (Join-Path $AppDir 'web\frontend\dist') -Recurse -Force
Copy-Item (Join-Path $Root 'config.example.json') (Join-Path $AppDir 'config.example.json') -Force
if (Test-Path (Join-Path $Root 'prompts')) { Copy-Item (Join-Path $Root 'prompts') (Join-Path $AppDir 'prompts') -Recurse -Force }
if (Test-Path (Join-Path $Root 'vendor\ffmpeg')) { Copy-Item (Join-Path $Root 'vendor\ffmpeg') (Join-Path $AppDir 'vendor\ffmpeg') -Recurse -Force }
# 파이썬 캐시/테스트 잔재 제거(용량·혼선 방지)
Get-ChildItem $AppDir -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
# 개발 중 소스트리에 생긴 런타임 산출물(로그/출력/DB 등)이 딸려가지 않도록 제거.
# 실행 시 데이터는 항상 MeetingMinutesData\ 에 새로 만들어지므로 패키지에 있으면 안 된다.
foreach ($junk in @(
    (Join-Path $AppDir 'meeting_minutes_app\data'),
    (Join-Path $AppDir 'meeting_minutes_app\output')
)) { if (Test-Path $junk) { Remove-Item $junk -Recurse -Force -ErrorAction SilentlyContinue } }

# ── 6. 런처/문서 배치 ─────────────────────────────────────────────
Step '6/8' 'Placing launchers and docs...'
Copy-Item (Join-Path $ScriptDir 'MeetingMinutes.bat') (Join-Path $OutDir 'MeetingMinutes.bat') -Force
Copy-Item (Join-Path $ScriptDir 'Troubleshoot.bat')   (Join-Path $OutDir 'Troubleshoot.bat') -Force
Copy-Item (Join-Path $ScriptDir '사용법_포터블.txt')   (Join-Path $OutDir '사용법.txt') -Force

# 빌드 각인 — "이 zip 이 어느 커밋 빌드인가"를 배포본 안에서 확인할 수 있게 한다.
# (실제로 두 번 겪은 사고: ① 커밋 HEAD로 빌드해 미커밋 기능이 빠짐 ② 빌드 도중
#  들어온 커밋이 zip 에 안 들어감. dirty=True 면 미커밋 변경이 섞인 빌드다.)
# dirty 판정은 **패키징되는 경로**만 본다 — 리포에 굴러다니는 개인 메모(untracked)
# 까지 dirty 로 잡으면 정상 릴리즈 빌드에도 "미커밋 포함" 경고가 붙어 신호가 죽는다.
$packagedPaths = @('meeting_minutes_app/', 'web/backend/', 'web/__init__.py',
                   'web/frontend/src/', 'config.example.json', 'prompts/',
                   'scripts/build/')
$commit = ''; $dirty = ''
try {
    $prevEap2 = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    $commit = (& git -C $Root rev-parse --short HEAD 2>$null) -join ''
    $porcelain = @(& git -C $Root status --porcelain 2>$null)
    $ErrorActionPreference = $prevEap2
    $relevant = $porcelain | Where-Object {
        $line = $_
        ($packagedPaths | Where-Object { $line -match [regex]::Escape($_) }).Count -gt 0
    }
    $dirty = ($relevant -join "`n")
} catch { }
$buildInfo = @(
    "Meeting Minutes portable build",
    ("built_at : " + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')),
    ("commit   : " + ($(if ($commit) { $commit } else { 'unknown (git 없음)' }))),
    ("dirty    : " + ($(if ($dirty) { 'YES — 미커밋 변경이 포함된 빌드' } else { 'no' }))),
    ("python   : " + $pyVer),
    # 어느 의존성 조합으로 빌드됐는지. 'none' 이면 버전이 빌드 시점에 결정된 빌드이므로
    # 같은 커밋이라도 재현되지 않는다 — 문제 신고 시 이 값이 있어야 원인을 좁힐 수 있다.
    # 파이썬(constraints)과 프런트(package-lock) 양쪽을 남긴다 — 한쪽만 고정돼 있으면
    # "같은 커밋인데 화면이 다르다"의 원인을 좁힐 수 없다.
    ("deps     : constraints sha256:" + $conHash),
    ("web deps : package-lock sha256:" + $lockHash),
    "",
    "이 파일은 문제 신고 시 어느 빌드인지 확인하는 용도입니다."
) -join "`r`n"
Set-Content -Path (Join-Path $OutDir 'BUILD_INFO.txt') -Value $buildInfo -Encoding utf8
Write-Host ("  BUILD_INFO: commit=" + $commit + " dirty=" + $(if ($dirty) { 'YES' } else { 'no' }))

# ── 7. 스모크 체크 (임베디드 파이썬이 핵심 모듈을 import + 앱을 로드하는지) ──
# 주의: `python -c "..."` 는 Windows PowerShell 5.1 이 큰따옴표를 제거해 코드가 깨진다.
# 임시 .py 파일에 써서 실행한다(따옴표 문제 회피).
Step '7/8' 'Smoke check (embedded python imports)...'
$env:MM_DATA_DIR = Join-Path $OutDir 'MeetingMinutesData'   # import 시 데이터 폴더가 app\ 를 오염시키지 않도록
$smokeFile = Join-Path $env:TEMP ('mmp_smoke_' + [System.IO.Path]::GetRandomFileName() + '.py')
@'
import fastapi, uvicorn, starlette, openai, anthropic, websockets, watchdog, truststore
# 로컬 STT 최종 백업 — ctranslate2/onnxruntime 네이티브 DLL 이 임베디드 파이썬에서
# 로딩되는지 여기서 검증한다(깨지면 배포 후 최후 백업이 조용히 죽는다).
import faster_whisper
import meeting_minutes_app
from web.backend import app  # FastAPI 앱 로드(라우터/DB 초기화 경로까지 탐)
print("IMPORT_OK")
'@ | Set-Content -Path $smokeFile -Encoding utf8
# EAP 를 잠시 낮춘다 — 앱 로드 중 stderr 로그(예: [mcp] 비활성화)가 PS 5.1 에서
# 치명적 오류로 취급되지 않도록. 성공/실패는 exit code + IMPORT_OK 로만 판정.
$prevEap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
$smokeOut = & (Join-Path $EmbedDir 'python.exe') $smokeFile 2>&1
$smokeCode = $LASTEXITCODE
$ErrorActionPreference = $prevEap
Remove-Item $smokeFile -ErrorAction SilentlyContinue
if ($smokeCode -ne 0 -or ($smokeOut -join "`n") -notmatch 'IMPORT_OK') {
    Write-Host ($smokeOut -join "`n") -ForegroundColor Red
    Fail '스모크 import 실패 — ._pth 경로 또는 의존성 설치를 확인하세요.'
}
# (참고) '[mcp] ... 비활성화' 메시지는 정상 — fastmcp 는 의도적으로 미포함(requirements-web.txt 참고).
Write-Host "  $($smokeOut -join ' | ')"
# 스모크가 만든 빈 데이터 폴더는 zip 제외 대상이므로 정리
if (Test-Path $env:MM_DATA_DIR) { Remove-Item $env:MM_DATA_DIR -Recurse -Force -ErrorAction SilentlyContinue }

# 백업해둔 사용자 데이터 복원(있었다면)
if ($dataBak -and (Test-Path $dataBak)) { Move-Item $dataBak (Join-Path $OutDir 'MeetingMinutesData') }

# ── 8. 배포 zip (MeetingMinutesData 제외) ─────────────────────────
Step '8/8' 'Creating distribution zip (excluding user data)...'
$zipPath = Join-Path $DistDir 'MeetingMinutesPortable.zip'
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
$stage = Join-Path $env:TEMP ('MMP_STAGE_' + [System.IO.Path]::GetRandomFileName())
Copy-Item $OutDir $stage -Recurse -Force
$stageData = Join-Path $stage 'MeetingMinutesData'
if (Test-Path $stageData) { Remove-Item $stageData -Recurse -Force }
Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $zipPath -Force
Remove-Item $stage -Recurse -Force

$zipMB = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
Write-Host "`n============================================" -ForegroundColor Green
Write-Host "  BUILD SUCCESS!"                              -ForegroundColor Green
Write-Host "  Folder: $OutDir"
Write-Host "  Zip:    $zipPath  ($zipMB MB)"
Write-Host "  Run:    MeetingMinutes.bat (더블클릭)"
Write-Host "============================================" -ForegroundColor Green
Write-Host "  배포: dist\MeetingMinutesPortable.zip 하나만 전달하면 됩니다."
Write-Host "  설정(OpenAI 키 등)은 실행 후 웹 [설정]에서 입력합니다."
