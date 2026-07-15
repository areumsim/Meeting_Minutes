"""
app.py — FastAPI 메인 애플리케이션
"""

import sys
from pathlib import Path
from contextlib import asynccontextmanager

for _s in (sys.stdout, sys.stderr):
    if getattr(_s, "encoding", None) and _s.encoding.lower() in ("cp949", "euc-kr", "ansi"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from web.backend.paths import AR_ROOT, EXE_DIR  # noqa: F401 — import 시 sys.path 셋업 side effect
from web.backend.database import init_db

# Wiki Knowledge Graph를 원격 MCP 서버로 노출(/mcp) — Claude Cowork 커스텀 커넥터용.
# fastmcp가 없으면(구버전 설치, 아직 `pip install -e .` 안 함) MCP 없이도 나머지 앱은 정상 동작해야 한다.
try:
    from meeting_minutes_app.wiki_core.mcp_server import get_mcp_asgi_app
    _mcp_app = get_mcp_asgi_app(path="/")  # app.mount("/mcp", ...) 아래서 최종 경로가 /mcp가 되도록
except Exception as _mcp_import_err:
    _mcp_app = None
    print(f"[mcp] Wiki Graph MCP 서버 비활성화(무시): {_mcp_import_err}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 데이터 폴더 보장(run_ui_exe 외 경로로 기동될 때 대비) + config 마이그레이션
    try:
        from meeting_minutes_app.common import app_paths
        app_paths.ensure_base_dir()
    except Exception as e:
        print(f"[startup] 데이터 폴더 초기화 경고: {e}")
    try:
        from meeting_minutes_app.common import config_loader
        config_loader.migrate()
    except Exception as e:
        print(f"[startup] config 마이그레이션 경고: {e}")
    init_db()
    try:
        from web.backend.session_scanner import scan_output_dir
        scan_output_dir()
    except Exception as e:
        print(f"[scanner] 초기 스캔 실패: {e}")
    if _mcp_app is not None:
        async with _mcp_app.lifespan(app):
            yield
    else:
        yield


app = FastAPI(title="AI Meeting Minutes", lifespan=lifespan)

if _mcp_app is not None:
    app.mount("/mcp", _mcp_app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── API 라우터 등록 ──
from web.backend.api.sessions import router as sessions_router
from web.backend.api.batch import router as batch_router
from web.backend.api.realtime import router as realtime_router
from web.backend.api.profiles import router as profiles_router
from web.backend.api.settings import router as settings_router
from web.backend.api.graph import router as graph_router
from web.backend.api.wiki import router as wiki_router
from web.backend.api.tools import router as tools_router

app.include_router(sessions_router, prefix="/api")
app.include_router(batch_router, prefix="/api")
app.include_router(realtime_router)
app.include_router(profiles_router, prefix="/api")
app.include_router(settings_router, prefix="/api")
app.include_router(graph_router, prefix="/api")
app.include_router(wiki_router, prefix="/api")
app.include_router(tools_router, prefix="/api")


@app.post("/api/shutdown")
def shutdown():
    """웹에서 앱(서버)을 종료. 콘솔 창이 없는 배포(windowed)에서 깔끔히 끄기 위한 용도."""
    import threading, os, time

    def _die():
        time.sleep(0.4)
        os._exit(0)

    threading.Thread(target=_die, daemon=True).start()
    return {"ok": True}


@app.get("/api/health")
def health():
    ffmpeg_ok = False
    try:
        from meeting_minutes_app.common import app_paths
        ffmpeg_ok = app_paths.ffmpeg_available()
    except Exception:
        pass
    return {"status": "ok", "ffmpeg_available": ffmpeg_ok}


# ── 프론트엔드 정적 파일 서빙 (프로덕션) ──
if getattr(sys, 'frozen', False):
    FRONTEND_DIST = Path(sys._MEIPASS) / "web" / "frontend" / "dist"
else:
    FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="assets")

    from fastapi import HTTPException

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        # API/WS/MCP 경로는 SPA fallback이 가로채지 않는다(매칭 실패 시 404).
        if full_path.startswith(("api/", "ws/", "mcp", "assets/")):
            raise HTTPException(status_code=404)
        file_path = FRONTEND_DIST / full_path
        if file_path.exists() and file_path.is_file():
            return FileResponse(str(file_path))
        return FileResponse(str(FRONTEND_DIST / "index.html"))
