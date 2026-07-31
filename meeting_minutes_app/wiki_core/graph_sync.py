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
from typing import Any, Dict, List, Optional, Sequence, Tuple

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
    """`_iter_vault_notes()`가 인덱서와 같은 필터를 쓰기 전에 만들어진 note 노드를 지운다.

    그 수정 이전의 스캔은 `_` 접두만 걸러서, 텍스트추출 부산물(`발표자료.pptx.md`,
    `data_loader.py.md`, `README.md.md` 등)과 `indexing.exclude_dirs` 폴더의 파일까지
    회의 노트와 같은 `note` 타입으로 그래프에 넣었다. 실제 볼트에서 note 노드 577개 중
    257개(45%)가 그림자 사본이었다 — 지식그래프 탐색 화면에서 노이즈로만 보인다.

    재백필만으로는 사라지지 않는다(`merge_note_duplicates_into_entities`와 같은 이유 —
    백필은 새로 upsert되는 노드에만 작용하고 기존 행을 지우지 않는다). 그래서
    `tools._rebuild_graph_from_vault()`(웹 [검색 인덱스·그래프 재빌드])가 백필 **전에**
    이 함수를 호출한다 — 포터블 배포본에는 `scripts/`가 들어가지 않아 그 경로가 아니면
    비개발자에게 도달할 방법이 없다.

    **판정은 노드 `attributes.path`에 인덱서 술어(`_is_indexable_note`)를 그대로 적용한다.**
    라벨로 판정하면 안 된다 — 라벨은 `frontmatter.title or 파일 stem`이라, 인덱서가 정상
    인덱싱하는 노트가 `title: config.json` 같은 값을 갖고 있으면 삭제 대상이 된다.
    (path 가 없는 노드는 판정 불가 → 보존. 세션 관련노트에서 만들어진 노드가 그렇다.)

    엣지가 붙어 있으면 **지우지 않는다** — 실제 볼트에서는 전부 엣지 0건인 고아였지만,
    엣지가 있다면 그래프에 실질적으로 참여하고 있다는 뜻이므로 사용자 확인 없이 관계를
    끊지 않는다(그 수는 `skipped_with_edges`로 돌려준다).
    """
    from meeting_minutes_app.wiki_core.vault_indexer import (
        _is_indexable_note, default_exclude_dirs,
    )

    graph_db.init_graph_db(db_path)
    exclude_dirs = default_exclude_dirs()
    pruned = 0
    skipped = 0
    with graph_db._conn(db_path) as conn:
        rows = conn.execute(
            "SELECT id, attributes FROM nodes WHERE type='note'").fetchall()
        for row in rows:
            try:
                path = str((json.loads(row["attributes"] or "{}") or {}).get("path") or "")
            except Exception:
                path = ""
            if not path:
                continue      # 출처 경로를 모르는 노드는 판정하지 않는다(보존)
            rel = path.replace("\\", "/")
            # iter_note_files() 와 같은 두 조건(`_` 접두 + _is_indexable_note)을 본다 →
            # "인덱서가 인덱싱하지 않는 노트"가 정확히 삭제 대상이 된다.
            if not os.path.basename(rel).startswith("_") \
                    and _is_indexable_note(rel, exclude_dirs):
                continue      # 인덱서가 노트로 보는 파일 → 정상 노드
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
    그림자 사본(`*.txt.md` 등)과 `indexing.exclude_dirs`를 제외해 473개를 인덱싱했는데
    여기는 `_` 접두만 걸러 805개를 스캔했다(2026-07-30 실측). 그 차이(약 330개)만큼 **위키 검색에는
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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  엔티티 겹침 회수 — "같은 인물이 같은 주제로 얘기한 자료"
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#
# 왜 유사도 검색과 별도인가
# -------------------------
# 임베딩·TF-IDF 유사도는 이 볼트에서 관련 문서를 찾지 못한다 — 실측(2026-07-31,
# scripts/measure_retrieval_floor.py)에서 전사에 대해 그 전사 **자신의 회의록**이
# 1위로 회수되는 비율이 임베딩 0%(중위 15위) · TF-IDF 0%(중위 88위)였다. 그런 회수
# 결과를 회의록 본문 컨텍스트로 주입하면 무관한 이전 회의가 이번 회의록에 섞인다.
#
# 반면 그래프의 `note -[:MENTIONED]-> person|topic` 엣지는 **추정이 아니라 기록**이다.
# "이 노트가 남우진를 언급했다"는 사실이고, 두 노트가 같은 person 노드를 가리키면
# 그건 유사도가 아니라 동일성이다. 같은 실측에서 이 축의 회수량을 재 보면:
#
#   같은 인물 겹침        평균 1.2건 · 최대 4건
#   인물 ∩ 주제 겹침      1~3건
#
# 유사도가 무차별로 10건을 내던 자리에서 1~3건이 나오고, **왜 걸렸는지 문장으로 적을
# 수 있다**("같은 인물: 남우진 · 같은 주제: 양자 머신러닝"). 근거를 적을 수 있는 회수만
# 회의록 본문에 주입한다 — 그것이 '설명 보완'과 '과대해석'을 가르는 선이다.
#
# 한계: 그래프에 엣지가 있는 노트만 대상이다(현재 볼트 31/457건). 백필이 안 된 노트는
# 회수되지 않는다 — 못 찾는 것은 조용히 없는 것으로 두고, 있는 것만 근거와 함께 낸다.

#: 화자 특정 실패로 생긴 자리표시자 — 사람으로 취급하면 서로 다른 회의가 'A'로 묶인다.
_PLACEHOLDER_PERSON_KEYS = frozenset(
    resolve_canonical_key("person", n) for n in (
        "A", "B", "C", "발언자A", "발언자B", "발언자C", "발언자 A", "발언자 B",
        "화자1", "화자2", "미정", "CFO", "코롱측", "내부 수행사", "외부 업체",
    )
)

#: 주제만 겹칠 때 요구하는 최소 겹침 수. 1개는 'NISQ' 하나로 무관한 두 노트가 묶여
#: 유사도 검색과 같은 문제가 되고, 실측에서 겹침>=2 는 평균 3.9건으로 아직 다룰 만하다.
_MIN_TOPIC_ONLY_OVERLAP = 2


def _mentioned_note_labels(node_type: str, label: str) -> List[str]:
    """엔티티(person/topic) 를 **언급한** note 노드의 라벨 목록.

    그래프 위상은 항상 `note -[:MENTIONED]-> entity` 이므로 엔티티에서 보면 역방향
    엣지(to_node_id=엔티티)를 읽으면 된다.
    """
    try:
        key = resolve_canonical_key(node_type, label)
        node = graph_db.get_node_by_key(node_type, key)
        if not node:
            return []
        out: List[str] = []
        for e in graph_db.list_edges(relation_type="MENTIONED",
                                     to_node_id=node["id"], limit=200):
            src = graph_db.get_node(e.get("from_node_id") or "")
            if src and src.get("type") == "note" and src.get("label"):
                out.append(str(src["label"]))
        return out
    except Exception:
        return []


def notes_sharing_entities(people: Optional[Sequence[str]] = None,
                          topics: Optional[Sequence[str]] = None,
                          *,
                          exclude_titles: Optional[Sequence[str]] = None,
                          limit: int = 5) -> List[Dict[str, Any]]:
    """이번 회의의 인물·주제와 **같은 엔티티를 언급한** 노트를 근거와 함께 회수한다.

    반환: [{"title", "people": [...], "topics": [...], "reason": "같은 인물: … · 같은 주제: …"}]
    관련도 점수가 아니라 **겹친 엔티티 자체**를 근거로 돌려준다 — 회의록에 "왜 이 노트가
    관련 있는지"를 적을 수 있어야 주입할 자격이 생긴다.

    채택 규칙(둘 중 하나):
      - 인물이 1명이라도 겹친다 (자리표시자 화자는 제외 — `_PLACEHOLDER_PERSON_KEYS`)
      - 주제가 `_MIN_TOPIC_ONLY_OVERLAP` 개 이상 겹친다
    정렬은 (겹친 인물 수, 겹친 주제 수) 내림차순 — 인물 일치를 주제보다 강하게 본다.
    """
    if not _feature_enabled("graph_enabled"):   # sub_key 만 넘긴다(wiki_knowledge. 접두 자동)
        return []
    ppl = [p for p in (people or []) if str(p or "").strip()
           and resolve_canonical_key("person", p) not in _PLACEHOLDER_PERSON_KEYS]
    tps = [t for t in (topics or []) if str(t or "").strip()]
    if not ppl and not tps:
        return []

    excl = {_norm_key(t) for t in (exclude_titles or []) if t}
    by_note: Dict[str, Dict[str, set]] = {}

    def _collect(kind: str, names: Sequence[str]) -> None:
        for name in names:
            for note_label in _mentioned_note_labels(
                    "person" if kind == "people" else "topic", name):
                if _norm_key(note_label) in excl:
                    continue
                slot = by_note.setdefault(
                    note_label, {"people": set(), "topics": set()})
                slot[kind].add(str(name))

    try:
        _collect("people", ppl)
        _collect("topics", tps)
    except Exception as e:
        print(f"[graph_sync] 엔티티 겹침 회수 실패 (무시): {e}")
        return []

    rows: List[Dict[str, Any]] = []
    for title, hit in by_note.items():
        n_p, n_t = len(hit["people"]), len(hit["topics"])
        if not (n_p >= 1 or n_t >= _MIN_TOPIC_ONLY_OVERLAP):
            continue
        bits = []
        if n_p:
            bits.append("같은 인물: " + ", ".join(sorted(hit["people"])))
        if n_t:
            bits.append("같은 주제: " + ", ".join(sorted(hit["topics"])))
        rows.append({
            "title": title,
            "people": sorted(hit["people"]),
            "topics": sorted(hit["topics"]),
            "reason": " · ".join(bits),
        })
    rows.sort(key=lambda r: (-len(r["people"]), -len(r["topics"]), r["title"]))
    return rows[:max(0, int(limit))]
