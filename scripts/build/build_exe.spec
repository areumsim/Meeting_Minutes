# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec — Meeting Minutes Web UI (run_ui.exe)
=====================================================
빌드: pyinstaller build_exe.spec
결과: dist/MeetingMinutes/ 폴더에 exe + 의존성 생성
"""

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, copy_metadata, collect_submodules

block_cipher = None
ROOT = os.path.abspath('.')
APP = os.path.join(ROOT, 'meeting_minutes_app')

# ── 데이터 파일 (번들에 포함) ──
datas = [
    # 빌드된 프론트엔드 정적 파일
    (os.path.join(ROOT, 'web', 'frontend', 'dist'), os.path.join('web', 'frontend', 'dist')),

    # 웹 백엔드 소스 (동적 import 대응)
    (os.path.join(ROOT, 'web', 'backend'), os.path.join('web', 'backend')),
    (os.path.join(ROOT, 'web', '__init__.py'), 'web'),

    # 구현 모듈들 (meeting_minutes, realtime 등)
    (APP, 'meeting_minutes_app'),
]

# config.example.json이 있으면 포함
if os.path.exists(os.path.join(ROOT, 'config.example.json')):
    datas.append((os.path.join(ROOT, 'config.example.json'), '.'))

# 외부 분석 프롬프트 템플릿 — analysis.templates_dir 기본값("prompts")이 frozen 에서
# _MEIPASS/prompts 를 가리키는데 지금까지 번들에 없어서 커스텀 템플릿이 조용히
# 무시되고 내장 기본 템플릿으로만 동작했다.
if os.path.isdir(os.path.join(ROOT, 'prompts')):
    datas.append((os.path.join(ROOT, 'prompts'), 'prompts'))

# ── 원격 MCP(fastmcp) 데이터 파일 ──
# fastmcp→mcp→jsonschema 포맷 검증이 rfc3987_syntax 를 쓰는데, 이 패키지는 문법
# 파일(syntax_rfc3987.lark)을 패키지 폴더 상대경로로 읽는다. PyInstaller가 .py만
# 수집하고 .lark 데이터파일은 빠뜨려서 exe 에서 MCP(/mcp)가 비활성화됐다.
# 해당 패키지(및 파서 lark)의 데이터파일을 명시 수집한다. 미설치여도 빌드는 계속.
for _pkg in ('rfc3987_syntax', 'lark'):
    try:
        datas += collect_data_files(_pkg)
    except Exception as _e:
        print(f"[spec] collect_data_files({_pkg}) 건너뜀: {_e}")

# fastmcp/mcp 패키지 메타데이터(.dist-info) — fastmcp __init__ 가 importlib.metadata.version()
# 으로 버전을 읽으므로 필요. copy_metadata 는 (src, dest) 2-튜플을 반환하며, 반드시 Analysis
# '전'의 datas 에 넣어야 한다(Analysis 가 정규화해 준다). Analysis '후' a.datas 에 넣으면
# a.datas 는 이미 3-튜플이라 COLLECT normalize_toc 가 터진다("expected 3, got 2").
for _pkg in ('fastmcp', 'fastmcp-slim', 'mcp'):
    try:
        datas += copy_metadata(_pkg)
    except Exception as _e:
        print(f"[spec] copy_metadata({_pkg}) 건너뜀: {_e}")

# ── ffmpeg 번들 (vendor/ffmpeg/*.exe 가 있으면 포함, 없으면 스킵) ──
# 런타임에 app_paths.get_ffmpeg_path()가 _MEIPASS/vendor/ffmpeg/ 를 먼저 찾는다.
binaries = []
_vendor_ffmpeg = os.path.join(ROOT, 'vendor', 'ffmpeg')
for _exe in ('ffmpeg.exe', 'ffprobe.exe'):
    _p = os.path.join(_vendor_ffmpeg, _exe)
    if os.path.exists(_p):
        binaries.append((_p, os.path.join('vendor', 'ffmpeg')))

# ── 숨겨진 import (PyInstaller가 자동 감지 못하는 것들) ──
hiddenimports = [
    # FastAPI / Uvicorn 에코시스템
    'uvicorn',
    'uvicorn.logging',
    'uvicorn.loops',
    'uvicorn.loops.auto',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.http.h11_impl',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.protocols.websockets.websockets_impl',
    'uvicorn.protocols.websockets.wsproto_impl',
    'uvicorn.lifespan',
    'uvicorn.lifespan.on',
    'uvicorn.lifespan.off',
    'fastapi',
    'starlette',
    'starlette.responses',
    'starlette.routing',
    'starlette.middleware',
    'starlette.middleware.cors',
    'anyio',
    'anyio._backends',
    'anyio._backends._asyncio',

    # Pydantic
    'pydantic',
    'pydantic.deprecated',
    'pydantic.deprecated.decorator',

    # HTTP / WebSocket
    'httpx',
    'httpcore',
    'h11',
    'wsproto',
    'websockets',

    # OpenAI — GA Realtime는 client.realtime 를 lazy attribute로 접근하므로
    # PyInstaller가 정적으로 못 잡는다. realtime 리소스/타입을 명시 포함한다.
    'openai',
    'openai.resources.realtime',
    'openai.resources.realtime.realtime',

    # Anthropic (폴백)
    'anthropic',

    # 웹 백엔드 모듈
    'web',
    'web.backend',
    'web.backend.app',
    'web.backend.database',
    'web.backend.schemas',
    'web.backend.session_scanner',
    'web.backend.api',
    'web.backend.api.sessions',
    'web.backend.api.batch',
    'web.backend.api.realtime',
    'web.backend.api.profiles',
    'web.backend.api.settings',
    'web.backend.api.graph',
    'web.backend.api.wiki',
    'web.backend.api.tools',
    'web.backend.api.watcher',
    'web.backend.api.assistant',

    # 회사망 SSL 검사 대응 — Windows 인증서 저장소 신뢰(app.py에서 inject)
    'truststore',

    # 폴더 자동 감시(vault_watcher) — watchdog FS 이벤트 모드 + 감시/처리 모듈
    'watchdog',
    'watchdog.observers',
    'watchdog.observers.polling',
    'watchdog.events',
    'meeting_minutes_app.meeting_pipeline.audio_watcher',

    # meeting_minutes_app 서브패키지 (common/wiki_core/meeting_pipeline)
    'meeting_minutes_app',
    'meeting_minutes_app.common',
    'meeting_minutes_app.common.config_loader',
    'meeting_minutes_app.meeting_pipeline.json_utils',
    'meeting_minutes_app.meeting_pipeline.date_utils',
    'meeting_minutes_app.common.llm_client',
    'meeting_minutes_app.common.notifier',
    'meeting_minutes_app.wiki_core',
    'meeting_minutes_app.wiki_core.obsidian',
    'meeting_minutes_app.wiki_core.obsidian_fs',
    'meeting_minutes_app.wiki_core.vault_indexer',
    'meeting_minutes_app.wiki_core.vault_retrieval',
    'meeting_minutes_app.wiki_core.wiki_knowledge',
    'meeting_minutes_app.wiki_core.wiki_ask',
    'meeting_minutes_app.wiki_core.supermemory_client',
    'meeting_minutes_app.meeting_pipeline',
    'meeting_minutes_app.meeting_pipeline.meeting_minutes',
    'meeting_minutes_app.meeting_pipeline.meeting_workflow',
    'meeting_minutes_app.meeting_pipeline.ingestion_pipeline',
    'meeting_minutes_app.meeting_pipeline.profiles',
    'meeting_minutes_app.meeting_pipeline.speaker_cache',
    # 회의 비서(계획비서/자동화/vault-audio) 웹 기능이 지연 import 하는 모듈
    'meeting_minutes_app.meeting_pipeline.plan_schedule',
    'meeting_minutes_app.meeting_pipeline.vault_audio',
    'meeting_minutes_app.meeting_pipeline.plan_watcher',
    'meeting_minutes_app.meeting_pipeline.plan_research',
    'meeting_minutes_app.meeting_pipeline.publish',
    'meeting_minutes_app.meeting_pipeline.people',
    # 주의: ws_transcriber / realtime_transcription 은 numpy·sounddevice 의존(CLI 실시간 전용)이며
    # 웹 서버 런타임에서 import되지 않으므로 hiddenimports 에 넣지 않는다(넣으면 dead weight).

    # 기타
    'multipart',
    'python_multipart',
    'sqlite3',
    'email',
    'email.mime',
    'email.mime.text',
    'email.mime.multipart',
    'email.mime.base',
]

# GA Realtime 전사 세션 param/이벤트 타입은 lazy 참조라 정적 감지가 어렵다 — 서브모듈 전체 포함.
hiddenimports += collect_submodules('openai.types.realtime')
# uvicorn의 WebSocket 프로토콜 구현이 런타임에 동적으로 불러오는 하위 모듈도
# 전부 포함한다. top-level 'websockets'만 적으면 빌드 환경/버전에 따라 일부가
# 분석에서 빠져 HTTP-only EXE가 만들어질 수 있다.
hiddenimports += collect_submodules('websockets')

a = Analysis(
    [os.path.join(APP, 'meeting_pipeline', 'run_ui_exe.py')],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 불필요한 대형 패키지 제외
        'tkinter',
        'matplotlib',
        'PIL',
        'scipy',
        'pandas',
        'torch',
        'tensorflow',
        'sounddevice',  # 웹 UI에서는 브라우저가 마이크 처리
        'numpy',        # 웹 UI 모드에서는 불필요 (HTTP 폴백용 wave만 사용)
        'webrtcvad',    # CLI 전용
        # watchdog 는 폴더 자동 감시(vault_watcher) 웹 기능에서 사용하므로 번들에 포함한다.
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MeetingMinutes',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # 콘솔 창 없이 실행(windowed). 로그는 MeetingMinutesData/data/logs/, 종료는 웹 [설정]→앱 종료
    icon=None,     # 아이콘 파일이 있으면 여기에 지정
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='MeetingMinutes',
)
