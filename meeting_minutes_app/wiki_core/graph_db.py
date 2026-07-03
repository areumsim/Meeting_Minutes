"""
graph_db.py — Wiki Knowledge Graph SQLite 저장소
=====================================================
회의/사람/조직/주제/결정/액션/노트 간의 관계를 그래프(노드/엣지)로 저장한다.

독립 모듈 — web/backend 에 대한 의존성이 전혀 없다(다른 팀이 wiki_core만
재사용할 수 있도록). DB 파일은 wiki_knowledge.DATA_DIR/wiki_graph.db
(web/backend가 쓰는 meeting_assistant.db와는 별개의 SQLite 파일).

읽기 전용 조회 함수(get_node/list_nodes/get_neighbors/...)는 web/backend/api/graph.py
가 그대로 얇게 감싸서 노출한다. 쓰기는 graph_sync.py를 통해서만 이뤄진다.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from meeting_minutes_app.wiki_core.wiki_knowledge import DATA_DIR

DB_PATH = DATA_DIR / "wiki_graph.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    id            TEXT PRIMARY KEY,
    type          TEXT NOT NULL,
    label         TEXT NOT NULL,
    canonical_key TEXT NOT NULL,
    attributes    TEXT DEFAULT '{}',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_nodes_canonical ON nodes(type, canonical_key);
CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type);
CREATE INDEX IF NOT EXISTS idx_nodes_label ON nodes(label);

CREATE TABLE IF NOT EXISTS edges (
    id                TEXT PRIMARY KEY,
    from_node_id      TEXT NOT NULL,
    to_node_id        TEXT NOT NULL,
    relation_type     TEXT NOT NULL,
    source_session_id TEXT,
    source_note       TEXT,
    confidence        TEXT DEFAULT 'medium',
    evidence          TEXT DEFAULT '[]',
    created_at        TEXT NOT NULL,
    FOREIGN KEY(from_node_id) REFERENCES nodes(id) ON DELETE CASCADE,
    FOREIGN KEY(to_node_id)   REFERENCES nodes(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_edges_from ON edges(from_node_id);
CREATE INDEX IF NOT EXISTS idx_edges_to ON edges(to_node_id);
CREATE INDEX IF NOT EXISTS idx_edges_relation ON edges(relation_type);
CREATE UNIQUE INDEX IF NOT EXISTS idx_edges_dedup
    ON edges(from_node_id, to_node_id, relation_type, COALESCE(source_session_id,''), COALESCE(source_note,''));
"""


@contextmanager
def _conn(db_path: Optional[Path] = None):
    """web/backend/database.py의 _conn()과 동일한 스타일(WAL, Row factory, FK on).
    db_path를 넘기면 그 파일을 쓴다 (테스트에서 tmp_path DB를 쓰기 위함)."""
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(path), check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    try:
        yield c
    finally:
        c.close()


def init_graph_db(db_path: Optional[Path] = None) -> None:
    """멱등 — 반복 호출 안전."""
    with _conn(db_path) as c:
        c.executescript(_SCHEMA)
        c.commit()


def _new_id() -> str:
    return str(uuid.uuid4())


def _row_to_node(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    try:
        d["attributes"] = json.loads(d.get("attributes") or "{}")
    except Exception:
        d["attributes"] = {}
    return d


def _row_to_edge(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    try:
        d["evidence"] = json.loads(d.get("evidence") or "[]")
    except Exception:
        d["evidence"] = []
    return d


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  쓰기 — upsert
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def upsert_node(
    type: str,
    label: str,
    attributes: Optional[Dict[str, Any]] = None,
    *,
    conn: Optional[sqlite3.Connection] = None,
    db_path: Optional[Path] = None,
) -> str:
    """(type, canonical_key) 로 조회 → 없으면 INSERT, 있으면 attributes 병합 후 UPDATE.
    conn이 주어지면 그 커넥션을 재사용(트랜잭션/드라이런 제어용), 아니면 자체 커넥션+commit."""
    from meeting_minutes_app.wiki_core.wiki_knowledge import _norm_key

    canonical = _norm_key(label or "")
    now = datetime.now().isoformat(timespec="seconds")
    attrs = attributes or {}

    def _do(c: sqlite3.Connection) -> str:
        row = c.execute(
            "SELECT * FROM nodes WHERE type = ? AND canonical_key = ?",
            (type, canonical),
        ).fetchone()
        if row is None:
            node_id = _new_id()
            c.execute(
                """INSERT INTO nodes (id, type, label, canonical_key, attributes, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (node_id, type, label, canonical, json.dumps(attrs, ensure_ascii=False), now, now),
            )
            return node_id
        node_id = row["id"]
        try:
            existing_attrs = json.loads(row["attributes"] or "{}")
        except Exception:
            existing_attrs = {}
        existing_attrs.update(attrs)
        c.execute(
            "UPDATE nodes SET attributes = ?, updated_at = ?, label = ? WHERE id = ?",
            (json.dumps(existing_attrs, ensure_ascii=False), now, label or row["label"], node_id),
        )
        return node_id

    if conn is not None:
        return _do(conn)
    with _conn(db_path) as c:
        node_id = _do(c)
        c.commit()
        return node_id


def upsert_edge(
    from_node_id: str,
    to_node_id: str,
    relation_type: str,
    *,
    source_session_id: Optional[str] = None,
    source_note: Optional[str] = None,
    confidence: str = "medium",
    evidence: Optional[List[Any]] = None,
    conn: Optional[sqlite3.Connection] = None,
    db_path: Optional[Path] = None,
) -> str:
    """INSERT OR IGNORE (유니크 dedup 인덱스 존중). 충돌로 무시됐으면 기존 row id를 조회해 반환."""
    now = datetime.now().isoformat(timespec="seconds")
    evidence_json = json.dumps(evidence or [], ensure_ascii=False)

    def _do(c: sqlite3.Connection) -> str:
        edge_id = _new_id()
        c.execute(
            """INSERT OR IGNORE INTO edges
               (id, from_node_id, to_node_id, relation_type, source_session_id,
                source_note, confidence, evidence, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (edge_id, from_node_id, to_node_id, relation_type,
             source_session_id, source_note, confidence, evidence_json, now),
        )
        row = c.execute(
            """SELECT id FROM edges WHERE from_node_id = ? AND to_node_id = ? AND relation_type = ?
               AND COALESCE(source_session_id,'') = COALESCE(?, '')
               AND COALESCE(source_note,'') = COALESCE(?, '')""",
            (from_node_id, to_node_id, relation_type, source_session_id, source_note),
        ).fetchone()
        return row["id"] if row else edge_id

    if conn is not None:
        return _do(conn)
    with _conn(db_path) as c:
        edge_id = _do(c)
        c.commit()
        return edge_id


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  읽기
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_node(node_id: str, *, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    with _conn(db_path) as c:
        row = c.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
    return _row_to_node(row) if row else None


def list_nodes(
    type: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 50,
    *,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM nodes WHERE 1=1"
    params: list = []
    if type:
        sql += " AND type = ?"
        params.append(type)
    if q:
        sql += " AND label LIKE ?"
        params.append(f"%{q}%")
    sql += " ORDER BY updated_at DESC LIMIT ?"
    params.append(int(limit))
    with _conn(db_path) as c:
        rows = c.execute(sql, params).fetchall()
    return [_row_to_node(r) for r in rows]


def list_edges(
    relation_type: Optional[str] = None,
    from_node_id: Optional[str] = None,
    to_node_id: Optional[str] = None,
    limit: int = 100,
    *,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM edges WHERE 1=1"
    params: list = []
    if relation_type:
        sql += " AND relation_type = ?"
        params.append(relation_type)
    if from_node_id:
        sql += " AND from_node_id = ?"
        params.append(from_node_id)
    if to_node_id:
        sql += " AND to_node_id = ?"
        params.append(to_node_id)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(int(limit))
    with _conn(db_path) as c:
        rows = c.execute(sql, params).fetchall()
    return [_row_to_edge(r) for r in rows]


def get_neighbors(
    node_id: str,
    depth: int = 1,
    relation_type: Optional[str] = None,
    limit: int = 50,
    *,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """BFS(순수 파이썬, edges 테이블을 hop마다 질의). depth는 서버 측에서 최대 3으로 캡."""
    depth = max(1, min(int(depth), 3))
    with _conn(db_path) as c:
        center_row = c.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
        if center_row is None:
            return {"node": None, "edges": [], "neighbors": []}
        center = _row_to_node(center_row)

        visited_nodes: Dict[str, Dict[str, Any]] = {node_id: center}
        seen_edge_ids: set = set()
        collected_edges: List[Dict[str, Any]] = []
        frontier = [node_id]

        for _ in range(depth):
            if not frontier or len(collected_edges) >= limit:
                break
            placeholders = ",".join("?" for _ in frontier)
            sql = f"""SELECT * FROM edges
                      WHERE (from_node_id IN ({placeholders}) OR to_node_id IN ({placeholders}))"""
            params: list = list(frontier) + list(frontier)
            if relation_type:
                sql += " AND relation_type = ?"
                params.append(relation_type)
            sql += " LIMIT ?"
            params.append(int(limit))
            rows = c.execute(sql, params).fetchall()

            next_frontier: List[str] = []
            for row in rows:
                edge = _row_to_edge(row)
                if edge["id"] in seen_edge_ids:
                    continue
                seen_edge_ids.add(edge["id"])
                collected_edges.append(edge)

                for nid in (edge["from_node_id"], edge["to_node_id"]):
                    if nid not in visited_nodes:
                        nrow = c.execute("SELECT * FROM nodes WHERE id = ?", (nid,)).fetchone()
                        if nrow:
                            visited_nodes[nid] = _row_to_node(nrow)
                            next_frontier.append(nid)
                if len(collected_edges) >= limit:
                    break
            frontier = next_frontier

        neighbors = [n for nid, n in visited_nodes.items() if nid != node_id]
        return {"node": center, "edges": collected_edges[:limit], "neighbors": neighbors[:limit]}


def find_path(
    from_id: str,
    to_id: str,
    max_depth: int = 4,
    *,
    db_path: Optional[Path] = None,
) -> Optional[List[Dict[str, Any]]]:
    """단순 BFS 최단 경로. 반환: [{"node":...}, {"edge":..., "node":...}, ...] 형태의 체인, 없으면 None."""
    max_depth = max(1, min(int(max_depth), 10))
    if from_id == to_id:
        node = get_node(from_id, db_path=db_path)
        return [{"node": node}] if node else None

    with _conn(db_path) as c:
        start_row = c.execute("SELECT * FROM nodes WHERE id = ?", (from_id,)).fetchone()
        end_row = c.execute("SELECT * FROM nodes WHERE id = ?", (to_id,)).fetchone()
        if start_row is None or end_row is None:
            return None

        # 각 노드에서 (edge, neighbor_id) 목록을 얻기 위해 전체 edges를 한 번만 로드
        all_edges = [_row_to_edge(r) for r in c.execute("SELECT * FROM edges").fetchall()]
        adjacency: Dict[str, List[tuple]] = {}
        for e in all_edges:
            adjacency.setdefault(e["from_node_id"], []).append((e, e["to_node_id"]))
            adjacency.setdefault(e["to_node_id"], []).append((e, e["from_node_id"]))

        # BFS
        prev: Dict[str, tuple] = {}  # node_id -> (edge, parent_node_id)
        visited = {from_id}
        queue = [(from_id, 0)]
        found = False
        while queue:
            cur, dist = queue.pop(0)
            if cur == to_id:
                found = True
                break
            if dist >= max_depth:
                continue
            for edge, nxt in adjacency.get(cur, []):
                if nxt in visited:
                    continue
                visited.add(nxt)
                prev[nxt] = (edge, cur)
                queue.append((nxt, dist + 1))

        if not found and to_id not in prev and to_id != from_id:
            return None

        # 경로 역추적
        chain_ids: List[str] = [to_id]
        cur = to_id
        edges_in_path: List[Dict[str, Any]] = []
        while cur != from_id:
            if cur not in prev:
                return None
            edge, parent = prev[cur]
            edges_in_path.append(edge)
            chain_ids.append(parent)
            cur = parent
        chain_ids.reverse()
        edges_in_path.reverse()

        result: List[Dict[str, Any]] = []
        node_cache = {n["id"]: n for n in [_row_to_node(r) for r in
                      c.execute(f"SELECT * FROM nodes WHERE id IN ({','.join('?' for _ in chain_ids)})", chain_ids).fetchall()]}
        for i, nid in enumerate(chain_ids):
            entry: Dict[str, Any] = {"node": node_cache.get(nid)}
            if i > 0:
                entry["edge"] = edges_in_path[i - 1]
            result.append(entry)
        return result


def get_session_subgraph(source_session_id: str, *, db_path: Optional[Path] = None) -> Dict[str, Any]:
    """edges.source_session_id = ? 인 모든 노드/엣지를 노드 타입별로 그룹화해 반환.
    반환: {"nodes": {type: [node,...]}, "edges": [edge,...], "node_count": N, "edge_count": N}"""
    with _conn(db_path) as c:
        edge_rows = c.execute(
            "SELECT * FROM edges WHERE source_session_id = ?", (source_session_id,)
        ).fetchall()
        edges = [_row_to_edge(r) for r in edge_rows]
        node_ids = set()
        for e in edges:
            node_ids.add(e["from_node_id"])
            node_ids.add(e["to_node_id"])
        nodes_by_type: Dict[str, List[Dict[str, Any]]] = {}
        if node_ids:
            placeholders = ",".join("?" for _ in node_ids)
            node_rows = c.execute(
                f"SELECT * FROM nodes WHERE id IN ({placeholders})", list(node_ids)
            ).fetchall()
            for r in node_rows:
                n = _row_to_node(r)
                nodes_by_type.setdefault(n["type"], []).append(n)
        total_nodes = sum(len(v) for v in nodes_by_type.values())
        return {
            "nodes": nodes_by_type,
            "edges": edges,
            "node_count": total_nodes,
            "edge_count": len(edges),
        }
