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

# 회사망 SSL 검사(사내 프록시의 인증서 교체) 대응: 파이썬 내장 인증서 목록(certifi)
# 대신 Windows 인증서 저장소를 신뢰한다 — 회사 PC에는 보통 회사 루트 인증서가
# 설치돼 있어, SSL 검증을 끄지 않고도 OpenAI/Anthropic 연결이 된다.
# (모든 후속 ssl 컨텍스트에 전역 적용되므로 가장 먼저 주입)
try:
    import truststore
    truststore.inject_into_ssl()
except Exception as _ts_err:
    print(f"[ssl] truststore 주입 생략(무시): {_ts_err}")

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
    # 지식 그래프 스키마 보장(멱등) — 새 데이터 폴더에서 nodes/edges 테이블이 없어
    # /api/graph/* 조회가 500(no such table)나던 문제 방지. 데이터가 없으면 빈 결과 반환.
    try:
        from meeting_minutes_app.wiki_core import graph_db
        graph_db.init_graph_db()
    except Exception as e:
        print(f"[graph] 그래프 스키마 초기화 경고: {e}")
    try:
        from web.backend.session_scanner import scan_output_dir
        scan_output_dir()
    except Exception as e:
        print(f"[scanner] 초기 스캔 실패: {e}")
    # 설정에 켜져 있는 백그라운드 자동화(폴더 감시·계획 자동화)를 자동 재개.
    # 버튼으로 켠 감시가 앱 재시작 후 사라지면 exe 사용자는 원인을 알기 어렵다.
    try:
        from web.backend.api.watcher import autostart_from_config
        autostart_from_config()
    except Exception as e:
        print(f"[startup] 자동화 자동 시작 경고: {e}")
    # indexing.auto_reindex_on_start — 설정 화면에 노출된 플래그인데 지금까지
    # 웹 앱 lifespan 이 무시했다. 부팅을 막지 않도록 백그라운드 스레드로 수행.
    try:
        from meeting_minutes_app.common import config_loader as _cfg
        if bool(_cfg.get("indexing.auto_reindex_on_start", False)):
            import threading

            def _reindex_bg():
                try:
                    from meeting_minutes_app.wiki_core.vault_indexer import VaultIndexer
                    idx = VaultIndexer.from_config()
                    if idx:
                        n = idx.build(verbose=False)
                        print(f"[indexing] 시작 시 자동 재인덱스 완료 — 노트 {n}개")
                except Exception as e:
                    print(f"[indexing] 시작 시 자동 재인덱스 실패(무시): {e}")

            threading.Thread(target=_reindex_bg, name="auto-reindex", daemon=True).start()
    except Exception as e:
        print(f"[startup] 자동 재인덱스 확인 경고: {e}")
    # 폴더-only 위키(검색 인덱스): 노트 폴더가 있는데 검색 인덱스(vault_index.json)가 아직
    # 없으면 백그라운드로 1회 자동 빌드한다 — Obsidian 앱/REST 없이 .md 폴더만 연결한 사용자가
    # [검색 인덱스 재빌드] 버튼을 누르지 않아도 '위키 질문'·관련 노트 검색이 바로 동작하게 한다
    # (지식 그래프 자동 백필과 동일 철학). auto_reindex_on_start가 켜져 있으면 위에서 이미
    # 재빌드하므로 건너뛴다(중복 빌드 방지). load()가 False면 인덱스 없음/폴더 변경 → 재빌드 대상.
    try:
        from meeting_minutes_app.common import config_loader as _cfg
        _idx_on = bool(_cfg.get("indexing.enabled", True))
        _idx_auto = bool(_cfg.get("indexing.auto_reindex_on_start", False))
        _idx_vault = _cfg.get("indexing.vault_path", "") or _cfg.get("obsidian.vault_path", "")
        if _idx_on and _idx_vault and not _idx_auto:
            from meeting_minutes_app.wiki_core.vault_indexer import VaultIndexer
            _idx = VaultIndexer.from_config()
            if _idx and not _idx.load():
                import threading

                def _auto_index_bg():
                    try:
                        n = _idx.build(verbose=False)
                        print(f"[indexing] 폴더 자동 인덱스 생성 완료 — 노트 {n}개 (위키 질문 사용 가능)")
                    except Exception as e:
                        print(f"[indexing] 폴더 자동 인덱스 생성 실패(무시): {e}")

                threading.Thread(target=_auto_index_bg, name="auto-index", daemon=True).start()
    except Exception as e:
        print(f"[startup] 폴더 자동 인덱스 확인 경고: {e}")
    # 폴더-only 위키: 노트 폴더가 지정돼 있고 지식 그래프가 아직 비어 있으면(최초 실행)
    # 배경 스레드로 1회 자동 백필한다 — 사용자가 scripts/graph_backfill.py를 수동 실행하지
    # 않아도 지식 그래프가 "그냥" 채워진다. 그래프가 이미 있으면 건너뛰고(멱등·중복 작업 방지),
    # 이후 갱신은 [검색 인덱스·그래프 재빌드] 버튼이나 세션 finalize 동기화가 담당한다.
    try:
        from meeting_minutes_app.common import config_loader as _cfg
        _graph_on = bool(_cfg.get("wiki_knowledge.graph_enabled", True))
        _vault = _cfg.get("indexing.vault_path", "") or _cfg.get("obsidian.vault_path", "")
        if _graph_on and _vault:
            from meeting_minutes_app.wiki_core import graph_db as _gdb
            if not _gdb.list_nodes(limit=1):
                import threading

                def _graph_backfill_bg():
                    try:
                        from meeting_minutes_app.wiki_core import graph_sync
                        graph_sync.backfill_from_registries()
                        vc = graph_sync.backfill_from_vault()
                        print(f"[graph] 폴더 자동 백필 완료 — 노트 {vc.get('notes_found', 0)}, "
                              f"노드 {vc.get('nodes_would_add', 0)}, 엣지 {vc.get('edges_would_add', 0)}")
                    except Exception as e:
                        print(f"[graph] 폴더 자동 백필 실패(무시): {e}")

                threading.Thread(target=_graph_backfill_bg, name="graph-autobackfill", daemon=True).start()
    except Exception as e:
        print(f"[startup] 그래프 자동 백필 확인 경고: {e}")
    # Obsidian REST가 가리키는 볼트와 설정한 .md 폴더가 다르면, 저장은 REST 볼트로 가고
    # 검색 인덱스는 설정 폴더를 읽어 서로 갈라진다(새 노트가 검색에 안 잡힘). 조용한 사고라
    # 시작 시 1회 경고만 남긴다(파일 기반 감지 — API 호출 없음). 감지 실패는 무시.
    try:
        from meeting_minutes_app.common import config_loader as _cfg
        if bool(_cfg.get("obsidian.enabled", False)):
            _vp = str(_cfg.get("obsidian.vault_path", "") or _cfg.get("indexing.vault_path", "") or "").strip()
            if _vp:
                from meeting_minutes_app.wiki_core.obsidian import _detect_obsidian_config
                _det = str((_detect_obsidian_config() or {}).get("vault_path", "") or "").strip()
                import os as _os
                if _det and _os.path.normcase(_os.path.abspath(_det)) != _os.path.normcase(_os.path.abspath(_vp)):
                    print(f"[obsidian] ⚠ REST 볼트({_det})와 설정 노트 폴더({_vp})가 다릅니다 — "
                          "저장(REST)과 검색 인덱스(폴더)가 갈라질 수 있습니다. 같은 볼트를 가리키게 하세요.")
    except Exception as e:
        print(f"[startup] Obsidian 볼트 경로 점검 경고(무시): {e}")

    if _mcp_app is not None:
        async with _mcp_app.lifespan(app):
            yield
    else:
        yield


app = FastAPI(title="AI Meeting Minutes", lifespan=lifespan)

if _mcp_app is not None:
    app.mount("/mcp", _mcp_app)

# 단일 오리진(localhost) 데스크톱 앱이라 CORS 는 사실상 불필요하지만, dev(Vite:5173)에서
# 백엔드(8501)로 직접 붙는 경우와, 같은 WiFi의 iOS/Android 앱(Capacitor)이 서버 모드로
# 붙는 경우를 위해 localhost 계열 + Capacitor 앱 오리진을 허용한다.
#   - iOS(capacitor.config iosScheme:'https', hostname:'localhost') → Origin: https://localhost
#   - capacitor://localhost / ionic://localhost (플랫폼/버전별 스킴)
# 참고: allow_origins=["*"] + allow_credentials=True 는 CORS 명세상 무효 조합이라 쓰지 않는다.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(https?://(localhost|127\.0\.0\.1)(:\d+)?|capacitor://localhost|ionic://localhost)$",
    allow_credentials=False,
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
from web.backend.api.watcher import router as watcher_router
from web.backend.api.assistant import router as assistant_router

app.include_router(sessions_router, prefix="/api")
app.include_router(batch_router, prefix="/api")
app.include_router(realtime_router)
app.include_router(profiles_router, prefix="/api")
app.include_router(settings_router, prefix="/api")
app.include_router(graph_router, prefix="/api")
app.include_router(wiki_router, prefix="/api")
app.include_router(tools_router, prefix="/api")
app.include_router(watcher_router, prefix="/api")
app.include_router(assistant_router, prefix="/api")


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
