"""
app.py — FastAPI 메인 애플리케이션
"""

import sys
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from web.backend.paths import AR_ROOT, EXE_DIR
from web.backend.database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    try:
        from web.backend.session_scanner import scan_output_dir
        scan_output_dir()
    except Exception as e:
        print(f"[scanner] 초기 스캔 실패: {e}")
    yield


app = FastAPI(title="AI Meeting Minutes", lifespan=lifespan)

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

app.include_router(sessions_router, prefix="/api")
app.include_router(batch_router, prefix="/api")
app.include_router(realtime_router)
app.include_router(profiles_router, prefix="/api")
app.include_router(settings_router, prefix="/api")


@app.get("/api/health")
def health():
    return {"status": "ok"}


# ── 프론트엔드 정적 파일 서빙 (프로덕션) ──
if getattr(sys, 'frozen', False):
    FRONTEND_DIST = Path(sys._MEIPASS) / "web" / "frontend" / "dist"
else:
    FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        file_path = FRONTEND_DIST / full_path
        if file_path.exists() and file_path.is_file():
            return FileResponse(str(file_path))
        return FileResponse(str(FRONTEND_DIST / "index.html"))
