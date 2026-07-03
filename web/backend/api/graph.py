"""
api/graph.py — Wiki Knowledge Graph 조회 API (읽기 전용)
=====================================================================
쓰기/수정 엔드포인트 없음(v1) — 그래프 DB에 대한 쓰기는 graph_sync.py를 통해서만
이뤄진다(세션 finalize 훅, scripts/graph_backfill.py). 여기는 얇은 GET 래퍼만 제공.

graph_db.py의 함수들이 이미 dict/list[dict]를 반환하므로, profiles.py와 마찬가지로
별도 Pydantic 스키마 래핑 없이 그대로 반환한다(중첩된 attributes/evidence 필드가
가변적이라 얇은 값 그대로 노출하는 편이 실용적).
"""

from typing import Optional

from fastapi import APIRouter, HTTPException

from meeting_minutes_app.wiki_core import graph_db

router = APIRouter(prefix="/graph", tags=["graph"])


@router.get("/nodes")
def list_nodes(type: Optional[str] = None, q: Optional[str] = None, limit: int = 50):
    return graph_db.list_nodes(type=type, q=q, limit=limit)


@router.get("/nodes/{node_id}")
def get_node(node_id: str):
    node = graph_db.get_node(node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="node not found")
    return node


@router.get("/nodes/{node_id}/neighbors")
def get_node_neighbors(
    node_id: str,
    depth: int = 1,
    relation_type: Optional[str] = None,
    limit: int = 50,
):
    result = graph_db.get_neighbors(node_id, depth=depth, relation_type=relation_type, limit=limit)
    if result.get("node") is None:
        raise HTTPException(status_code=404, detail="node not found")
    return result


@router.get("/edges")
def list_edges(
    relation_type: Optional[str] = None,
    from_node_id: Optional[str] = None,
    to_node_id: Optional[str] = None,
    limit: int = 100,
):
    return graph_db.list_edges(
        relation_type=relation_type, from_node_id=from_node_id, to_node_id=to_node_id, limit=limit
    )


@router.get("/path")
def get_path(from_id: str, to_id: str, max_depth: int = 4):
    path = graph_db.find_path(from_id, to_id, max_depth=max_depth)
    if path is None:
        raise HTTPException(status_code=404, detail="no path found")
    return {"path": path}


@router.get("/sessions/{session_id}")
def get_session_subgraph(session_id: str):
    return graph_db.get_session_subgraph(session_id)
