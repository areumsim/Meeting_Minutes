"""
graph_sync.py — Registry/Vault/세션 산출물 → 그래프 DB 동기화
=====================================================================
graph_db.py 위에서, (registry 레코드 | frontmatter dict | 세션 산출물)을
노드/엣지 upsert로 변환하는 유일한 지점.

- backfill_from_registries()/backfill_from_vault(): 1회성 백필 (scripts/graph_backfill.py, CLI 직접 호출)
- sync_session_graph(): 세션 종료 시 실시간 동기화 (web/backend/api/realtime.py, batch.py에서 호출)

이 모듈은 data/action_registry.json, data/decision_registry.json, Obsidian 노트를
"읽기만" 한다 — 그래프 DB(wiki_graph.db)에만 쓰고, 원본 registry JSON이나
Obsidian 노트는 절대 수정하지 않는다.
"""

from __future__ import annotations

import glob
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from meeting_minutes_app.wiki_core import graph_db
from meeting_minutes_app.wiki_core import wiki_knowledge as wk
from meeting_minutes_app.wiki_core.wiki_knowledge import _norm_key, _feature_enabled

# 주의: DATA_DIR을 별도 이름으로 import하지 않고 항상 wk.DATA_DIR로 참조한다.
# (테스트가 monkeypatch.setattr(wk, "DATA_DIR", tmp_path)로 경로를 바꿔치기 할 수 있도록 —
#  `from ... import DATA_DIR`로 바인딩하면 이 모듈의 로컬 이름이 고정되어 몽키패치가 반영되지 않는다.)


def canonical_key(label: str) -> str:
    """중복 판정용 정규화 키. wiki_knowledge._norm_key()를 그대로 재사용한다."""
    return _norm_key(label or "")


def strip_wikilink(v: str) -> str:
    """"[[홍길동]]" -> "홍길동", "[[Corp|약칭]]" -> "Corp" (별칭 제거)."""
    s = re.sub(r'^\[\[|\]\]$', '', str(v or "").strip())
    return s.split('|')[0].strip()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Registry 백필
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def backfill_from_registries(dry_run: bool = False) -> Dict[str, int]:
    """data/action_registry.json + data/decision_registry.json → 그래프.

    decision 레코드: meeting -[:DECIDED]-> decision -[:AFFECTS]-> topic
    action 레코드:   meeting -[:CREATED]-> action -[:ASSIGNED_TO]-> person
                     action -[:AFFECTS]-> topic (decision과 대칭이 되도록 방향을 맞춤)

    dry_run=True 이면 트랜잭션을 rollback 해서 실제로는 아무것도 쓰지 않고,
    upsert를 "시도"한 노드/엣지 개수만 집계해 반환한다.
    """
    graph_db.init_graph_db()
    decision_reg = wk.load_decision_registry(wk.DATA_DIR / "decision_registry.json")
    action_reg = wk.load_action_registry(wk.DATA_DIR / "action_registry.json")

    counts = {"nodes_would_add": 0, "edges_would_add": 0, "nodes_upserted": 0, "edges_upserted": 0}

    with graph_db._conn() as conn:
        seen_node_keys: set = set()
        seen_edge_keys: set = set()

        def _node(type_: str, label: str, attrs: Optional[dict] = None) -> str:
            key = (type_, canonical_key(label))
            if key not in seen_node_keys:
                seen_node_keys.add(key)
                counts["nodes_would_add"] += 1
            node_id = graph_db.upsert_node(type_, label, attrs, conn=conn)
            counts["nodes_upserted"] += 1
            return node_id

        def _edge(frm: str, to: str, rel: str, *, source_note: str = "") -> str:
            key = (frm, to, rel, "", source_note)
            if key not in seen_edge_keys:
                seen_edge_keys.add(key)
                counts["edges_would_add"] += 1
            edge_id = graph_db.upsert_edge(frm, to, rel, source_note=source_note or None, conn=conn)
            counts["edges_upserted"] += 1
            return edge_id

        for d in decision_reg.get("decisions", []):
            if not isinstance(d, dict):
                continue
            meeting_title = str(d.get("source_meeting", "") or "").strip()
            summary = str(d.get("summary", "") or "").strip()
            if not meeting_title or not summary:
                continue
            meeting_id = _node("meeting", meeting_title)
            decision_id = _node("decision", summary, {
                "status": d.get("status", ""),
                "created_at": d.get("created_at", ""),
            })
            _edge(meeting_id, decision_id, "DECIDED", source_note=d.get("source_note", ""))
            for topic in d.get("topics", []) or []:
                topic = str(topic).strip()
                if not topic:
                    continue
                topic_id = _node("topic", topic)
                _edge(decision_id, topic_id, "AFFECTS")

        for a in action_reg.get("actions", []):
            if not isinstance(a, dict):
                continue
            meeting_title = str(a.get("source_meeting", "") or "").strip()
            title = str(a.get("title", "") or "").strip()
            if not meeting_title or not title:
                continue
            meeting_id = _node("meeting", meeting_title)
            action_id = _node("action", title, {
                "due_date": a.get("due_date", ""),
                "status": a.get("status", ""),
                "context": a.get("context", ""),
            })
            _edge(meeting_id, action_id, "CREATED", source_note=a.get("source_note", ""))
            owner = str(a.get("owner", "") or "").strip()
            if owner:
                owner_id = _node("person", owner)
                _edge(action_id, owner_id, "ASSIGNED_TO")
            for topic in a.get("topics", []) or []:
                topic = str(topic).strip()
                if not topic:
                    continue
                topic_id = _node("topic", topic)
                _edge(action_id, topic_id, "AFFECTS")

        if dry_run:
            conn.rollback()
        else:
            conn.commit()

    return {
        "nodes_would_add": counts["nodes_would_add"],
        "edges_would_add": counts["edges_would_add"],
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Vault 백필
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _iter_vault_notes():
    """vault_indexer.VaultIndexer.build()와 동일한 방식으로 vault .md 파일을 순회한다."""
    from meeting_minutes_app.common import config_loader

    vault_path = config_loader.get("indexing.vault_path") or config_loader.get("obsidian.vault_path", "")
    if not vault_path:
        return
    for fpath in glob.glob(os.path.join(vault_path, "**", "*.md"), recursive=True):
        if os.path.basename(fpath).startswith("_"):
            continue
        try:
            content = open(fpath, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        yield fpath, vault_path, content


def backfill_from_vault(dry_run: bool = False) -> Dict[str, int]:
    """Obsidian vault frontmatter → 그래프 (note + person/organization/topic + MENTIONED 엣지)."""
    from meeting_minutes_app.wiki_core.obsidian import parse_frontmatter

    graph_db.init_graph_db()
    counts = {"nodes_would_add": 0, "edges_would_add": 0, "nodes_upserted": 0, "edges_upserted": 0}
    notes_found = 0

    with graph_db._conn() as conn:
        seen_node_keys: set = set()
        seen_edge_keys: set = set()

        def _node(type_: str, label: str, attrs: Optional[dict] = None) -> str:
            key = (type_, canonical_key(label))
            if key not in seen_node_keys:
                seen_node_keys.add(key)
                counts["nodes_would_add"] += 1
            node_id = graph_db.upsert_node(type_, label, attrs, conn=conn)
            counts["nodes_upserted"] += 1
            return node_id

        def _edge(frm: str, to: str, rel: str, *, source_note: str = "") -> str:
            key = (frm, to, rel, "", source_note)
            if key not in seen_edge_keys:
                seen_edge_keys.add(key)
                counts["edges_would_add"] += 1
            edge_id = graph_db.upsert_edge(frm, to, rel, source_note=source_note or None, conn=conn)
            counts["edges_upserted"] += 1
            return edge_id

        for fpath, vault_path, content in _iter_vault_notes():
            notes_found += 1
            meta, _body = parse_frontmatter(content)
            rel_path = os.path.relpath(fpath, vault_path).replace("\\", "/")
            title = meta.get("title") or Path(fpath).stem

            note_id = _node("note", title, {
                "path": rel_path,
                "note_type": meta.get("type", ""),
                "review_status": meta.get("review_status", ""),
                "confidence": meta.get("confidence", ""),
                "source_type": meta.get("source_type", ""),
            })

            for field, node_type in (("people", "person"), ("organizations", "organization"), ("topics", "topic")):
                for raw in meta.get(field, []) or []:
                    clean = strip_wikilink(raw)
                    if not clean:
                        continue
                    entity_id = _node(node_type, clean)
                    _edge(note_id, entity_id, "MENTIONED", source_note=rel_path)

        if dry_run:
            conn.rollback()
        else:
            conn.commit()

    return {
        "nodes_would_add": counts["nodes_would_add"],
        "edges_would_add": counts["edges_would_add"],
        "notes_found": notes_found,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  세션 실시간 동기화
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def sync_session_graph(
    *,
    session_id: str,
    title: str,
    actions_json: Optional[str] = None,
    decisions: Optional[List[str]] = None,
    related_note_titles: Optional[List[str]] = None,
    evidence: Optional[List[Dict[str, Any]]] = None,
    source_note: str = "",
) -> None:
    """세션 종료(finalize) 시 호출하는 실시간 동기화 엔트리 포인트.

    registry JSON 파일은 건드리지 않는다 — 여기서는 그래프 DB에만 쓴다.
    (registry JSON 갱신은 wiki_knowledge.update_action_registry_from_actions() /
    update_decision_registry_from_minutes()가 이미 별도로 처리한다.)

    이 함수 자체도 방어적으로 전체를 try/except로 감싼다: 호출부(realtime.py/batch.py)가
    또 한 번 감싸긴 하지만, 그래프 동기화 실패가 다른 로그(registry 실패 로그)와
    뒤섞이지 않도록 여기서 한 번 더 잡아 self-contained 하게 만든다.
    """
    try:
        if not _feature_enabled("graph_enabled"):
            return
        if not title:
            return

        graph_db.init_graph_db()
        with graph_db._conn() as conn:
            meeting_id = graph_db.upsert_node("meeting", title, conn=conn)

            if actions_json:
                try:
                    items = json.loads(actions_json)
                except Exception:
                    items = []
                if isinstance(items, list):
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        task = str(item.get("task", "") or "").strip()
                        if not task:
                            continue
                        action_id = graph_db.upsert_node("action", task, {
                            "due_date": item.get("deadline") or "",
                            "context": item.get("context") or "",
                        }, conn=conn)
                        graph_db.upsert_edge(
                            meeting_id, action_id, "CREATED",
                            source_session_id=session_id, source_note=source_note or None,
                            conn=conn,
                        )
                        assignee = str(item.get("assignee", "") or "").strip()
                        if assignee:
                            person_id = graph_db.upsert_node("person", assignee, conn=conn)
                            graph_db.upsert_edge(
                                action_id, person_id, "ASSIGNED_TO",
                                source_session_id=session_id, source_note=source_note or None,
                                conn=conn,
                            )

            if decisions:
                for summary in decisions:
                    summary = str(summary or "").strip()
                    if not summary:
                        continue
                    decision_id = graph_db.upsert_node("decision", summary, conn=conn)
                    graph_db.upsert_edge(
                        meeting_id, decision_id, "DECIDED",
                        source_session_id=session_id, source_note=source_note or None,
                        conn=conn,
                    )

            if related_note_titles:
                for note_title in related_note_titles:
                    note_title = str(note_title or "").strip()
                    if not note_title:
                        continue
                    note_id = graph_db.upsert_node("note", note_title, conn=conn)
                    graph_db.upsert_edge(
                        meeting_id, note_id, "USED_CONTEXT",
                        source_session_id=session_id, source_note=source_note or None,
                        evidence=evidence or None,
                        conn=conn,
                    )

            conn.commit()
    except Exception as e:
        print(f"[graph_sync] sync_session_graph 실패 (무시): {e}")
