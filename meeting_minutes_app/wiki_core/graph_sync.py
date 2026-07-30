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

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from meeting_minutes_app.wiki_core import graph_db
from meeting_minutes_app.wiki_core import wiki_knowledge as wk
from meeting_minutes_app.wiki_core.wiki_knowledge import (
    _norm_key, _feature_enabled, _decision_summary_rationale,
)

# 주의: DATA_DIR을 별도 이름으로 import하지 않고 항상 wk.DATA_DIR로 참조한다.
# (테스트가 monkeypatch.setattr(wk, "DATA_DIR", tmp_path)로 경로를 바꿔치기 할 수 있도록 —
#  `from ... import DATA_DIR`로 바인딩하면 이 모듈의 로컬 이름이 고정되어 몽키패치가 반영되지 않는다.)


def canonical_key(label: str) -> str:
    """중복 판정용 정규화 키(타입 무관 버전). resolve_canonical_key(None, label)와 동일."""
    return resolve_canonical_key(None, label)


# 흔한 한국어 직함/존칭 접미사 — person 노드의 표기 변형("홍길동 팀장" vs "홍길동")을 흡수한다.
# 실제 동명이인 구분이나 오탈자 교정까지는 하지 않는다 — 규칙 기반 v1의 명시적 한계.
_PERSON_TITLE_SUFFIXES = [
    "팀장", "부장", "차장", "과장", "대리", "사원", "대표", "이사", "실장", "본부장",
    "매니저", "담당자", "담당", "책임", "수석", "선임", "연구원", "교수", "박사",
    "PM", "PL", "PO", "님", "씨",
]


def resolve_canonical_key(type_: Optional[str], label: str) -> str:
    """엔티티 정규화(Entity Resolver) v1 — 규칙 기반 정확 일치 + 가벼운 표기 변형 흡수.

    - `_`를 공백으로 통일해 wiki_knowledge._norm_key()가 놓치는 케이스를 보완한다
      (`_norm_key`는 [\\s\\W]+ 로 지우는데 언더스코어는 \\W가 아니라 \\w라 그대로 남는다 —
      "260627_5"와 "260627 5"가 실제로 다른 노드로 남는 걸 테스트 중 직접 확인했다).
    - type_ == "person" 이면 흔한 직함/존칭 접미사를 제거한다 — "홍길동 팀장"과 "홍길동"이
      같은 사람 노드로 합쳐진다.

    동일 인물의 약어/오탈자 통합이나 LLM 기반 병합은 여전히 범위 밖이다(향후 과제) —
    여기서 하는 건 순수 규칙 기반의 가벼운 보정뿐이다.
    """
    text = str(label or "").replace("_", " ")
    if type_ == "person":
        for suffix in _PERSON_TITLE_SUFFIXES:
            text = re.sub(rf'\s*{re.escape(suffix)}\s*$', '', text)
    return _norm_key(text)


def strip_wikilink(v: str) -> str:
    """"[[홍길동]]" -> "홍길동", "[[Corp|약칭]]" -> "Corp" (별칭 제거)."""
    s = re.sub(r'^\[\[|\]\]$', '', str(v or "").strip())
    return s.split('|')[0].strip()


def _upsert_entity(
    type_: str,
    label: str,
    attributes: Optional[dict] = None,
    *,
    conn=None,
    db_path: Optional[Path] = None,
) -> str:
    """graph_db.upsert_node()를 resolve_canonical_key()로 계산한 키와 함께 호출하는
    유일한 창구. backfill_from_registries/backfill_from_vault/sync_session_graph가
    모두 이 함수를 통해서만 노드를 만든다 — 세 경로에서 만든 "같은 사람/회의" 노드가
    서로 다른 canonical_key로 흩어지지 않게 하기 위함."""
    key = resolve_canonical_key(type_, label)
    return graph_db.upsert_node(type_, label, attributes, canonical_key=key, conn=conn, db_path=db_path)


_ENTITY_TYPES_FOR_NOTE_RESOLUTION = ("person", "organization", "topic")


def _resolve_or_create_note_node(
    title: str, attributes: Optional[dict] = None, *, conn=None, db_path: Optional[Path] = None,
) -> str:
    """title로 이미 존재하는 person/organization/topic 노드가 있으면 그 id를 재사용
    (attrs 병합)하고, 없으면 "note" 타입으로 새로 upsert한다.

    호출부(예: sync_session_graph의 related_note_titles)가 참조 노트 제목을
    그냥 "note" 타입으로 새로 만들면 backfill_from_vault가 만든 엔티티 노드와
    별개로 분리된다 — 이 헬퍼로 그 이중 정체성을 방지한다.
    """
    for entity_type in _ENTITY_TYPES_FOR_NOTE_RESOLUTION:
        key = resolve_canonical_key(entity_type, title)
        if graph_db.get_node_by_key(entity_type, key, conn=conn, db_path=db_path):
            return _upsert_entity(entity_type, title, attributes, conn=conn, db_path=db_path)
    return _upsert_entity("note", title, attributes, conn=conn, db_path=db_path)


def merge_note_duplicates_into_entities(
    dry_run: bool = False, *, db_path: Optional[Path] = None,
) -> Dict[str, int]:
    """일회성 마이그레이션: `backfill_from_vault()`의 note/entity 이중 정체성 수정 이전에
    만들어진 그래프에 남아있는, 참조 노트가 여전히 "note" 타입 행으로 존재하는 중복을
    같은 canonical_key의 person/organization/topic 노드로 병합한다.

    이 함수를 실행하지 않으면(단순 재백필만으로는) 예전 "note" 행이 그대로 남아있는 채로
    새 backfill이 올바른 타입의 노드를 "추가로" 만들어 중복이 오히려 늘어난 것처럼 보인다 —
    재백필은 새로 upsert되는 노드에만 적용되고 기존 잘못된 타입의 행을 지우지 않기 때문이다.

    병합 시 엣지를 살아남는 엔티티 노드로 재연결한 뒤 note 행을 삭제한다(attrs는
    엔티티 쪽 값을 우선하되 note에만 있던 키는 보존). dry_run=True면 병합 대상 개수만
    센 뒤 rollback한다.
    """
    graph_db.init_graph_db(db_path)
    merged = 0
    with graph_db._conn(db_path) as conn:
        note_rows = conn.execute("SELECT id, canonical_key, attributes FROM nodes WHERE type='note'").fetchall()
        for note_row in note_rows:
            entity_row = None
            for entity_type in _ENTITY_TYPES_FOR_NOTE_RESOLUTION:
                entity_row = conn.execute(
                    "SELECT id, attributes FROM nodes WHERE type=? AND canonical_key=?",
                    (entity_type, note_row["canonical_key"]),
                ).fetchone()
                if entity_row:
                    break
            if not entity_row:
                continue
            merged += 1
            if dry_run:
                continue
            note_attrs = json.loads(note_row["attributes"] or "{}")
            entity_attrs = json.loads(entity_row["attributes"] or "{}")
            merged_attrs = {**note_attrs, **entity_attrs}
            conn.execute("UPDATE nodes SET attributes = ? WHERE id = ?",
                        (json.dumps(merged_attrs, ensure_ascii=False), entity_row["id"]))
            conn.execute("UPDATE OR IGNORE edges SET from_node_id = ? WHERE from_node_id = ?",
                        (entity_row["id"], note_row["id"]))
            conn.execute("UPDATE OR IGNORE edges SET to_node_id = ? WHERE to_node_id = ?",
                        (entity_row["id"], note_row["id"]))
            # 유니크 제약 충돌로 재연결되지 못하고 남은(entity 쪽에 이미 동일 엣지가 있던) 잔여 엣지 정리
            conn.execute("DELETE FROM edges WHERE from_node_id = ? OR to_node_id = ?",
                        (note_row["id"], note_row["id"]))
            conn.execute("DELETE FROM nodes WHERE id = ?", (note_row["id"],))

        if dry_run:
            conn.rollback()
        else:
            conn.commit()
    return {"merged": merged}


def prune_shadow_note_nodes(
    dry_run: bool = False, *, db_path: Optional[Path] = None,
) -> Dict[str, int]:
    """일회성 마이그레이션: `_iter_vault_notes()`가 그림자 사본을 걸러내기 전에 만들어진
    note 노드를 지운다.

    그 수정 이전의 스캔은 `_` 접두만 걸러서, 텍스트추출 부산물(`발표자료.pptx.md`,
    `data_loader.py.md`, `README.md.md` 등)까지 회의 노트와 같은 `note` 타입으로
    그래프에 넣었다. 실제 볼트에서 note 노드 577개 중 257개(45%)가 이것이었다 —
    지식그래프 탐색 화면에서 노이즈로만 보인다.

    재백필만으로는 사라지지 않는다(`merge_note_duplicates_into_entities`와 같은 이유 —
    백필은 새로 upsert되는 노드에만 작용하고 기존 행을 지우지 않는다).

    판정은 인덱서와 같은 단일 소스(`vault_indexer._SHADOW_EXTS`)를 쓴다. 라벨의 확장자가
    거기 있으면 그림자 사본이다. 엣지가 붙어 있으면 **지우지 않는다** — 실제 볼트에서는
    전부 엣지 0건인 고아였지만, 엣지가 있다면 그래프에 실질적으로 참여하고 있다는
    뜻이므로 사용자 확인 없이 관계를 끊지 않는다(그 수는 `skipped_with_edges`로 돌려준다).
    """
    from meeting_minutes_app.wiki_core.vault_indexer import _SHADOW_EXTS

    graph_db.init_graph_db(db_path)
    pruned = 0
    skipped = 0
    with graph_db._conn(db_path) as conn:
        rows = conn.execute("SELECT id, label FROM nodes WHERE type='note'").fetchall()
        for row in rows:
            label = str(row["label"] or "")
            if Path(label).suffix.lower() not in _SHADOW_EXTS:
                continue
            n_edges = conn.execute(
                "SELECT COUNT(*) FROM edges WHERE from_node_id = ? OR to_node_id = ?",
                (row["id"], row["id"]),
            ).fetchone()[0]
            if n_edges:
                skipped += 1
                continue
            pruned += 1
            if not dry_run:
                conn.execute("DELETE FROM nodes WHERE id = ?", (row["id"],))

        if dry_run:
            conn.rollback()
        else:
            conn.commit()
    return {"pruned": pruned, "skipped_with_edges": skipped}


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
            key = (type_, resolve_canonical_key(type_, label))
            if key not in seen_node_keys:
                seen_node_keys.add(key)
                counts["nodes_would_add"] += 1
            node_id = _upsert_entity(type_, label, attrs, conn=conn)
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
                "rationale": d.get("rationale", ""),
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
    """vault_indexer.VaultIndexer.build()와 **같은 필터**로 vault .md 파일을 순회한다.

    같은 판정 규칙을 두 곳에 따로 쓰면 갈라진다 — 실제로 갈라져 있었다. 인덱서는
    그림자 사본(`*.txt.md` 등)과 `indexing.exclude_dirs`를 제외해 473개를 인덱싱하는데
    여기는 `_` 접두만 걸러 805개를 스캔했다. 그 차이(약 330개)만큼 **위키 검색에는
    없는 노트가 그래프에는 노드로 들어갔다** — 그림자 사본이 회의로 오인용되던 문제와
    같은 뿌리다. 그래서 파일 목록 자체를 `vault_indexer.iter_note_files()`에서 받는다
    (규칙을 복제하지 않는다 — 복제하면 기본값 하나만 바뀌어도 다시 갈라진다)."""
    from meeting_minutes_app.common import config_loader
    from meeting_minutes_app.wiki_core.vault_indexer import iter_note_files

    vault_path = config_loader.get("indexing.vault_path") or config_loader.get("obsidian.vault_path", "")
    if not vault_path:
        return
    for fpath in iter_note_files(vault_path):
        try:
            content = open(fpath, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        yield fpath, vault_path, content


# 01_References 노트의 category 프론트매터 → 그래프 노드 타입.
# enrichment.py의 _CATEGORIES와 동일한 한국어 라벨을 쓴다(둘 다 이 값을 생성/소비).
_REFERENCE_CATEGORY_TO_TYPE = {
    "인물": "person",
    "기업·기관": "organization",
    "용어·기술": "topic",
}

_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")

# attendees/authors 프론트매터에 흔히 들어가는 자리표시자 — 실제 인명이 아니므로 노드화하지 않는다.
_GENERIC_SPEAKER_RE = re.compile(r"^speaker\b", re.IGNORECASE)


def _extract_wikilink_titles(body: str) -> List[str]:
    """본문의 [[제목]] / [[제목|별칭]] / [[제목#헤딩]]에서 기본 제목만 추출(중복 제거, 순서 유지)."""
    out: List[str] = []
    seen = set()
    for raw in _WIKILINK_RE.findall(body or ""):
        title = raw.split("|")[0].split("#")[0].strip()
        if title and title not in seen:
            seen.add(title)
            out.append(title)
    return out


def backfill_from_vault(dry_run: bool = False) -> Dict[str, int]:
    """Obsidian vault → 그래프 (note + person/organization/topic + MENTIONED 엣지).

    엔티티 추출 소스 (전부 최선 조합, 실패해도 서로 영향 없음):
    1. 본문의 [[위키링크]] — 링크 대상이 01_References의 person/organization/topic
       참조 노트(category 프론트매터로 판정)와 일치하면 note→entity MENTIONED 엣지.
       이 vault의 실제 지식은 frontmatter 배열이 아니라 본문 위키링크로 표현되므로
       (구현 시 635개 노트 전수 조사 결과 people/organizations/topics 배열 사용 0건),
       이 경로가 그래프 엣지의 주 소스다.
    2. people/organizations/topics 프론트매터 배열 — 향후/외부 도구가 이 스키마를
       쓸 경우를 위해 계속 지원(현재 이 vault에는 해당 데이터 없음).
    3. attendees/authors 프론트매터 — 회의 참석자·논문 저자를 person으로 (제네릭
       "Speaker"/"Speaker A" 자리표시자는 제외).
    """
    from meeting_minutes_app.wiki_core.obsidian import parse_frontmatter

    graph_db.init_graph_db()
    counts = {"nodes_would_add": 0, "edges_would_add": 0, "nodes_upserted": 0, "edges_upserted": 0}

    # ── 1차 패스: 파일을 한 번만 읽어 메모리에 올리고, 참조 노트 title→type 색인 구축 ──
    all_notes: List[Tuple[str, str, str, dict, str]] = []  # (fpath, vault_path, rel_path, meta, body)
    title_to_type: Dict[str, str] = {}
    for fpath, vault_path, content in _iter_vault_notes():
        meta, body = parse_frontmatter(content)
        rel_path = os.path.relpath(fpath, vault_path).replace("\\", "/")
        title = str(meta.get("title") or Path(fpath).stem)
        all_notes.append((fpath, vault_path, rel_path, meta, body))

        if meta.get("type") == "reference":
            node_type = _REFERENCE_CATEGORY_TO_TYPE.get(str(meta.get("category", "")))
            if node_type:
                title_to_type[resolve_canonical_key(None, title)] = node_type
    notes_found = len(all_notes)

    with graph_db._conn() as conn:
        seen_node_keys: set = set()
        seen_edge_keys: set = set()

        def _node(type_: str, label: str, attrs: Optional[dict] = None) -> str:
            key = (type_, resolve_canonical_key(type_, label))
            if key not in seen_node_keys:
                seen_node_keys.add(key)
                counts["nodes_would_add"] += 1
            node_id = _upsert_entity(type_, label, attrs, conn=conn)
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

        # ── 2차 패스: note 노드 생성 + 엣지 연결 ──
        for fpath, vault_path, rel_path, meta, body in all_notes:
            title = str(meta.get("title") or Path(fpath).stem)
            node_attrs = {
                "path": rel_path,
                "note_type": meta.get("type", ""),
                "review_status": meta.get("review_status", ""),
                "confidence": meta.get("confidence", ""),
                "source_type": meta.get("source_type", ""),
            }
            # 이 노트 자신이 참조 노트(인물/기업·기관/용어·기술을 설명)면 별도 "note"
            # 노드를 만들지 않고 그 엔티티 타입으로 직접 upsert한다 — 다른 노트가
            # 이 제목을 위키링크할 때 만들어지는 엔티티 노드와 canonical_key가 일치해
            # 하나로 합쳐진다(과거엔 note/entity 두 노드로 분리돼 서로 연결이 안 됐음).
            self_entity_type = title_to_type.get(resolve_canonical_key(None, title))
            note_id = _node(self_entity_type or "note", title, node_attrs)

            # 소스 1: 본문 위키링크 → 참조 노트 색인과 대조
            for link_title in _extract_wikilink_titles(body):
                node_type = title_to_type.get(resolve_canonical_key(None, link_title))
                if not node_type:
                    continue  # 참조 노트가 아닌 일반 노트 링크는 건너뜀 (person/org/topic만 추적)
                entity_id = _node(node_type, link_title)
                _edge(note_id, entity_id, "MENTIONED", source_note=rel_path)

            # 소스 2: people/organizations/topics 프론트매터 배열 (있으면)
            for field, node_type in (("people", "person"), ("organizations", "organization"), ("topics", "topic")):
                for raw in meta.get(field, []) or []:
                    clean = strip_wikilink(raw)
                    if not clean:
                        continue
                    entity_id = _node(node_type, clean)
                    _edge(note_id, entity_id, "MENTIONED", source_note=rel_path)

            # 소스 3: attendees/authors 프론트매터 → person (제네릭 Speaker 자리표시자 제외)
            for field in ("attendees", "authors"):
                for raw in meta.get(field, []) or []:
                    clean = strip_wikilink(raw)
                    if not clean or _GENERIC_SPEAKER_RE.match(clean):
                        continue
                    entity_id = _node("person", clean)
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
            meeting_id = _upsert_entity("meeting", title, conn=conn)

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
                        action_id = _upsert_entity("action", task, {
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
                            person_id = _upsert_entity("person", assignee, conn=conn)
                            graph_db.upsert_edge(
                                action_id, person_id, "ASSIGNED_TO",
                                source_session_id=session_id, source_note=source_note or None,
                                conn=conn,
                            )

            if decisions:
                for decision_item in decisions:
                    summary, rationale = _decision_summary_rationale(decision_item)
                    if not summary:
                        continue
                    decision_id = _upsert_entity(
                        "decision", summary,
                        {"rationale": rationale} if rationale else None,
                        conn=conn,
                    )
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
                    note_id = _resolve_or_create_note_node(note_title, conn=conn)
                    graph_db.upsert_edge(
                        meeting_id, note_id, "USED_CONTEXT",
                        source_session_id=session_id, source_note=source_note or None,
                        evidence=evidence or None,
                        conn=conn,
                    )

            conn.commit()
    except Exception as e:
        print(f"[graph_sync] sync_session_graph 실패 (무시): {e}")
