"""mcp_server.py — Wiki Knowledge Graph를 원격 MCP 서버로 노출한다 (Claude Cowork 연동용).

Cowork의 커스텀 커넥터는 원격(HTTP) MCP 서버만 지원한다(로컬 stdio MCP는 Claude Desktop
자체 기능이고 Cowork에서는 별개로 지원되지 않는다). 이 모듈은 web/backend/app.py에 ASGI
서브앱으로 마운트되어, 기존 웹 서버 하나로 REST(/api/graph/*)와 MCP(/mcp)를 함께 서빙한다.

읽기 전용 — graph_db.py 조회 함수를 그대로 감싼 도구만 노출하며, 쓰기/수정 도구는 없다
(REST API(web/backend/api/graph.py)와 동일한 안전 원칙).

인증: Bearer 토큰. config.json의 `mcp.allowed_tokens`(문자열 리스트)에 있는 토큰만 허용한다.
새 토큰 발급은 `meeting-minutes mcp-token` 커맨드(meeting_minutes_app/cli.py)를 사용한다.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# ── fastmcp 버전 메타데이터 폴백 (PyInstaller 동결 실행 대응) ──
# fastmcp/__init__ 는 importlib.metadata.version("fastmcp-slim"/"fastmcp") 로 __version__ 을
# 읽는데, PyInstaller 가 fastmcp 의 .dist-info 를 번들에서 제외해(datas/런타임훅으로도 못 살림)
# 동결 실행 시 PackageNotFoundError → fastmcp import 실패 → MCP 비활성화된다.
# fastmcp import '직전에' version() 을 감싸 폴백을 제공한다(순서 보장). dev 환경(실제 메타데이터
# 존재)에서는 폴백이 발동하지 않아 무해하다.
def _install_fastmcp_metadata_fallback() -> None:
    import importlib.metadata as _im
    if getattr(_im.version, "_mm_patched", False):
        return
    _orig = _im.version
    _fallback = {"fastmcp": "0.0.0", "fastmcp-slim": "0.0.0"}

    def _version(name, *args, **kwargs):
        try:
            return _orig(name, *args, **kwargs)
        except _im.PackageNotFoundError:
            if name in _fallback:
                return _fallback[name]
            raise

    _version._mm_patched = True
    _im.version = _version


_install_fastmcp_metadata_fallback()

from fastmcp import FastMCP
from fastmcp.server.auth import AccessToken, TokenVerifier

from meeting_minutes_app.wiki_core import graph_db, graph_sync


class ConfigTokenVerifier(TokenVerifier):
    """config.json의 mcp.allowed_tokens 리스트에 대해 Bearer 토큰을 검증한다.

    OAuth/JWT 같은 별도 인증 서버를 두지 않는다 — 1~2인 규모의 사내 배포에 맞춘
    가장 단순한 형태(고정 토큰 목록)이며, 팀이 커지면 fastmcp의 OAuth 프로바이더로
    교체할 수 있다(이 클래스만 바꾸면 됨, 도구 정의는 그대로 재사용).
    """

    async def verify_token(self, token: str) -> Optional[AccessToken]:
        from meeting_minutes_app.common import config_loader

        allowed = config_loader.get("mcp.allowed_tokens", []) or []
        for entry in allowed:
            # 항목은 {"token": "...", "name": "..."} 또는 순수 문자열 둘 다 허용한다.
            entry_token = entry.get("token") if isinstance(entry, dict) else entry
            if entry_token and token == entry_token:
                name = entry.get("name", "user") if isinstance(entry, dict) else "user"
                return AccessToken(token=token, client_id=str(name), scopes=[])
        return None


mcp = FastMCP(
    name="meeting-minutes-wiki-graph",
    instructions=(
        "회의록 자동화 파이프라인이 쌓은 Wiki Knowledge Graph(회의·사람·조직·주제·결정·액션 "
        "노드/관계)를 조회하는 도구 모음. 특정 프로젝트/주제의 현황이나 특정 인물이 언급된 "
        "회의를 물을 때는 상위 편의 도구(get_topic_status, find_meetings_mentioning)를 "
        "먼저 시도하고, 더 정밀한 탐색이 필요하면 원시 그래프 도구를 조합해 쓴다."
    ),
    auth=ConfigTokenVerifier(),
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  원시 그래프 도구 — graph_db.py를 그대로 감싼다
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@mcp.tool()
def list_graph_nodes(
    type: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """그래프 노드를 검색한다. type: person|organization|topic|meeting|decision|action|note.
    q가 있으면 라벨에 부분 일치하는 노드만 반환한다."""
    return graph_db.list_nodes(type=type, q=q, limit=limit)


@mcp.tool()
def get_graph_node(node_id: str) -> Optional[Dict[str, Any]]:
    """노드 ID로 단일 노드를 조회한다."""
    return graph_db.get_node(node_id)


@mcp.tool()
def get_graph_neighbors(
    node_id: str,
    depth: int = 1,
    relation_type: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    """노드에서 depth(최대 3)만큼 연결된 이웃 노드/엣지를 반환한다.
    relation_type을 주면 해당 관계(MENTIONED/DECIDED/CREATED/ASSIGNED_TO/AFFECTS/USED_CONTEXT)만 따라간다."""
    return graph_db.get_neighbors(node_id, depth=depth, relation_type=relation_type, limit=limit)


@mcp.tool()
def list_graph_edges(
    relation_type: Optional[str] = None,
    from_node_id: Optional[str] = None,
    to_node_id: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """엣지(관계) 목록을 조회/필터링한다."""
    return graph_db.list_edges(
        relation_type=relation_type, from_node_id=from_node_id, to_node_id=to_node_id, limit=limit
    )


@mcp.tool()
def find_graph_path(from_id: str, to_id: str, max_depth: int = 4) -> Optional[List[Dict[str, Any]]]:
    """두 노드 사이의 최단 경로(BFS)를 찾는다. 경로가 없으면 null."""
    return graph_db.find_path(from_id, to_id, max_depth=max_depth)


@mcp.tool()
def get_session_graph(session_id: str) -> Dict[str, Any]:
    """특정 회의 세션이 생성한 노드/엣지만 타입별로 묶어 반환한다."""
    return graph_db.get_session_subgraph(session_id)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  상위 편의 도구 — 자연어 질의에 바로 답하기 좋은 형태로 조립
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_ENTITY_TYPES = ("person", "organization", "topic")


def _find_node_by_label(label: str, types: tuple = _ENTITY_TYPES) -> Optional[Dict[str, Any]]:
    """라벨로 노드를 찾는다 — 우선 각 타입에서 정규화된 canonical_key 정확 일치를 시도하고,
    실패하면 부분 일치(list_nodes q=) 검색으로 폴백한다."""
    for type_ in types:
        key = graph_sync.resolve_canonical_key(type_, label)
        node = graph_db.get_node_by_key(type_, key)
        if node:
            return node
    for type_ in types:
        hits = graph_db.list_nodes(type=type_, q=label, limit=1)
        if hits:
            return hits[0]
    return None


@mcp.tool()
def get_topic_status(topic: str) -> Dict[str, Any]:
    """특정 프로젝트/주제의 현재 상태를 한 번에 조회한다 — 연결된 미해결 액션, 최근 결정사항,
    관련 회의를 모아서 반환한다. topic 이름이 정확히 일치하지 않아도(표기 변형) 최대한 찾는다.
    반환: {"topic": 노드 or null, "open_actions": [...], "decisions": [...], "meetings": [...]}"""
    node = _find_node_by_label(topic, types=("topic",))
    if not node:
        return {"topic": None, "open_actions": [], "decisions": [], "meetings": [],
                "note": f"'{topic}'에 해당하는 topic 노드를 찾지 못했습니다."}

    result = graph_db.get_neighbors(node["id"], depth=1, limit=100)
    open_actions = [
        n for n in result["neighbors"]
        if n["type"] == "action" and str(n.get("attributes", {}).get("status", "")).lower() not in ("done", "closed", "완료")
    ]
    decisions = [n for n in result["neighbors"] if n["type"] == "decision"]
    meetings = [n for n in result["neighbors"] if n["type"] == "meeting"]

    return {
        "topic": node,
        "open_actions": open_actions,
        "decisions": decisions,
        "meetings": meetings,
    }


@mcp.tool()
def find_meetings_mentioning(entity_name: str) -> Dict[str, Any]:
    """특정 인물/조직/주제가 언급된 모든 회의를 찾는다. entity(사람/조직/주제) -[:MENTIONED]- note
    -[:USED_CONTEXT]- meeting 의 2-hop 관계를 따라간다(직접 연결이 아니라 근사치이므로 일부
    회의는 노트 연결이 없어 누락될 수 있다).
    반환: {"entity": 노드 or null, "meetings": [...]}"""
    node = _find_node_by_label(entity_name)
    if not node:
        return {"entity": None, "meetings": [],
                "note": f"'{entity_name}'에 해당하는 노드를 찾지 못했습니다."}

    result = graph_db.get_neighbors(node["id"], depth=2, limit=200)
    meetings = [n for n in result["neighbors"] if n["type"] == "meeting"]
    return {"entity": node, "meetings": meetings}


def get_mcp_asgi_app(path: str = "/"):
    """web/backend/app.py에서 `app.mount("/mcp", ...)`으로 마운트할 ASGI 앱을 반환한다
    (path="/" 이므로 마운트 후 최종 외부 경로는 /mcp 하나). 반환된 앱의 .lifespan을 부모
    FastAPI 앱의 lifespan 안에서 함께 열어야 MCP 세션 상태가 초기화된다 — web/backend/app.py의
    lifespan()이 이미 그렇게 처리한다."""
    return mcp.http_app(path=path)
