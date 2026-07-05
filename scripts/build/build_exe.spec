# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec — Meeting Minutes Web UI (run_ui.exe)
=====================================================
빌드: pyinstaller build_exe.spec
결과: dist/MeetingMinutes/ 폴더에 exe + 의존성 생성
"""

import os
from pathlib import Path

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

    # OpenAI
    'openai',
    'openai.beta',
    'openai.beta.realtime',

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
    'meeting_minutes_app.meeting_pipeline.ws_transcriber',
    'meeting_minutes_app.meeting_pipeline.realtime_transcription',

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

a = Analysis(
    [os.path.join(APP, 'meeting_pipeline', 'run_ui_exe.py')],
    pathex=[ROOT],
    binaries=[],
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
        'watchdog',     # CLI watcher 전용
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
    console=True,  # 콘솔 창 표시 (로그 확인용)
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
