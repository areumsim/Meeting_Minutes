"""
ingestion_pipeline.py — 오디오 파일 → 전사 → 분석 → Obsidian 노트 파이프라인
==================================================================================
STT 결과를 먼저 만든 뒤, 발화 내용으로 Obsidian 관련 노트를 찾아
회의록 생성 메모에 주입하고 위키링크를 포함한다.

단독 실행:
    python ingestion_pipeline.py --file 회의.m4a
    python ingestion_pipeline.py --file 회의.m4a --type seminar --title "AI 세미나"
    python ingestion_pipeline.py --file 회의.m4a --force   # 이미 처리된 파일 재처리
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent.parent  # meeting_minutes_app/meeting_pipeline/ -> repo root

for _stream in (sys.stdout, sys.stderr):
    if getattr(_stream, "encoding", None) and _stream.encoding.lower() in ("cp949", "euc-kr", "ansi"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

try:
    from meeting_minutes_app.common import config_loader as _cfg
    _cfg_ok = True
except ImportError:
    _cfg = None  # type: ignore
    _cfg_ok = False


def _c(key: str, default: Any = None) -> Any:
    return _cfg.get(key, default) if _cfg_ok else default


# 파일명 패턴으로 문서 유형 추론
_TYPE_PATTERNS = {
    "seminar": re.compile(r'(seminar|세미나|발표|presentation|webinar|웨비나)', re.I),
    "lecture": re.compile(r'(lecture|강의|강좌|수업|class|course)', re.I),
    "memo":    re.compile(r'(memo|메모|note|voice|음성메모|아이디어)', re.I),
}


def _extract_date_from_path(audio_path: str) -> str:
    """파일명과 상위 폴더명에서 날짜를 추출한다.
    YYMMDD(6자리), YYYYMMDD, YYYY-MM-DD, YYYY_MM_DD 패턴을 지원.
    찾지 못하면 오늘 날짜를 반환한다.
    """
    try:
        from meeting_minutes_app.common.date_utils import parse_iso_date_from_text
        return parse_iso_date_from_text(audio_path, default_today=True)
    except Exception:
        return datetime.now().strftime("%Y-%m-%d")


def _detect_type(audio_path: str) -> str:
    """파일명에서 문서 유형을 추론한다. 기본값: config.analysis.default_type."""
    stem = Path(audio_path).stem.lower()
    for doc_type, pat in _TYPE_PATTERNS.items():
        if pat.search(stem):
            return doc_type
    return _c("analysis.default_type", "meeting")


def _detect_meeting_scope(title: str = "", topic: str = "") -> str:
    """내부/외부 회의 구분을 보수적으로 추론한다."""
    text = f"{title} {topic}".lower()
    external_terms = (
        "외부", "고객", "클라이언트", "파트너", "협력사", "벤더", "후원",
        "제안", "계약", "mou", "po", "견적", "미팅",
    )
    internal_terms = (
        "내부", "팀회의", "주간보고", "데일리", "사내", "1on1", "원온원",
    )
    if any(term in text for term in external_terms):
        return "external"
    if any(term in text for term in internal_terms):
        return "internal"
    return "unknown"


def _load_prompt_template(doc_type: str) -> Optional[str]:
    """prompts/{doc_type}_analysis.md 를 읽어 반환. 없으면 None."""
    templates_dir = _c("analysis.templates_dir", "prompts")
    path = PROJECT_ROOT / templates_dir / f"{doc_type}_analysis.md"
    if path.exists():
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            pass
    return None


def _format_related_notes(titles: List[str]) -> str:
    """위키링크 타이틀 리스트 → 쉼표 구분 문자열 (프롬프트 주입용)."""
    return ", ".join(f"[[{t}]]" for t in titles) if titles else "없음"


def _extract_sections(minutes: str) -> Dict[str, List[str]]:
    """LLM 생성 회의록에서 섹션별 내용을 파싱한다 (best-effort)."""
    sections: Dict[str, List[str]] = {
        "key_points": [],
        "decisions": [],
        "open_questions": [],
        "important_claims": [],
    }
    patterns = {
        "key_points":      re.compile(r'#{1,3}\s*(핵심\s*포인트|key\s*points?|주요\s*사항)', re.I),
        "decisions":       re.compile(r'#{1,3}\s*(결정\s*사항?|decisions?|확정)', re.I),
        "open_questions":  re.compile(r'#{1,3}\s*(미해결|open\s*questions?|추가\s*논의)', re.I),
        "important_claims": re.compile(r'#{1,3}\s*(중요\s*주장|important\s*claims?|핵심\s*주장)', re.I),
    }
    current_key: Optional[str] = None
    for line in minutes.splitlines():
        matched = False
        for key, pat in patterns.items():
            if pat.match(line.strip()):
                current_key = key
                matched = True
                break
        if matched:
            continue
        if re.match(r'#{1,3}\s', line) and current_key:
            current_key = None
        if current_key and line.strip().startswith("-"):
            item = line.strip().lstrip("-").strip()
            if item:
                sections[current_key].append(item)
    return sections


class IngestionPipeline:
    """오디오 파일을 전사·분석하고 Obsidian 노트로 저장한다."""

    def __init__(self, llm=None, obs=None, vault_indexer=None):
        self._llm = llm
        self._obs = obs
        self._indexer = vault_indexer
        self._llm_initialized = llm is not None

    def _ensure_llm(self):
        if self._llm is None:
            from meeting_minutes_app.meeting_pipeline import meeting_minutes as mm
            self._llm = mm.LLMClient(preferred=mm._c("models.llm", "gpt") or "gpt")
            self._llm_initialized = True

    def _ensure_obs(self):
        if self._obs is None:
            try:
                from meeting_minutes_app.wiki_core.obsidian import ObsidianClient
                obs = ObsidianClient.from_config()
                if obs:
                    if not obs.ping():
                        obs.ensure_running()  # exe_path는 config.obsidian.exe_path 사용
                    if obs.ping():
                        self._obs = obs
            except Exception:
                pass

    def _ensure_indexer(self):
        if self._indexer is None:
            try:
                from meeting_minutes_app.wiki_core.vault_indexer import VaultIndexer
                idx = VaultIndexer.from_config()
                if idx:
                    if not idx.load():
                        print("[pipeline] 인덱스 없음 — 관련 노트 링크 건너뜀")
                    self._indexer = idx
            except Exception:
                pass

    def ingest(
        self,
        audio_path: str,
        doc_type: str = "",
        title: str = "",
        topic: str = "",
        force: bool = False,
        send_email: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """오디오 파일을 전사·분석·노트화한다.

        Returns:
            {"status": "done"|"failed"|"skipped", "note_path": str,
             "title": str, "duration": float, "error": str}
        """
        result: Dict[str, Any] = {
            "status": "failed",
            "note_path": "",
            "title": title,
            "duration": 0.0,
            "error": "",
        }

        # 파일 검증
        if not os.path.exists(audio_path):
            result["error"] = f"파일 없음: {audio_path}"
            return result

        size_mb = os.path.getsize(audio_path) / (1024 * 1024)
        min_size = float(_c("vault_watcher.min_size_mb", 0.5))
        if size_mb < min_size:
            result["status"] = "skipped"
            result["error"] = f"파일 크기 {size_mb:.2f}MB < 최소 {min_size}MB"
            return result

        # 문서 유형 감지
        if not doc_type:
            doc_type = _detect_type(audio_path)

        # 제목 생성
        if not title:
            stem = Path(audio_path).stem
            title = re.sub(r'[_\-]+', ' ', stem).strip() or stem

        result["title"] = title
        meeting_scope = _detect_meeting_scope(title, topic)
        result["meeting_scope"] = meeting_scope
        print(f"[pipeline] 처리 시작: {Path(audio_path).name} (type={doc_type})")

        try:
            # 파일명 또는 상위 폴더명에서 날짜 추출 (YYMMDD / YYYY-MM-DD 패턴)
            session_dt = _extract_date_from_path(audio_path)
            expected_rel_path = _expected_recording_note_path(title, session_dt)
            self._ensure_obs()
            if self._obs and expected_rel_path and self._obs.exists(expected_rel_path) and not force:
                result["status"] = "skipped"
                result["note_path"] = expected_rel_path
                result["error"] = "이미 처리된 녹음 노트가 있음 (--force로 덮어쓰기 가능)"
                print(f"[pipeline] 이미 처리됨 → 건너뜀: {expected_rel_path}")
                return result

            # 오디오 길이 측정
            try:
                from meeting_minutes_app.meeting_pipeline import meeting_minutes as mm
                duration = mm.audio_duration(audio_path)
                result["duration"] = duration
            except Exception:
                duration = 0.0

            # STT를 먼저 수행하고, STT 발화 내용으로 Obsidian을 검색한 뒤 회의록 생성에 주입
            self._ensure_llm()

            from meeting_minutes_app.meeting_pipeline import meeting_minutes as mm
            work_dir = tempfile.mkdtemp(prefix="ingest_audio_")
            try:
                audio2 = mm.prepare_audio(audio_path, work_dir)
                segments = mm.run_stt(
                    audio2,
                    model=mm.DEFAULT_STT_MODEL,
                    language=mm._c("realtime.language", None),
                    speaker_names=None,
                    work_dir=work_dir,
                )
            finally:
                shutil.rmtree(work_dir, ignore_errors=True)

            if not segments:
                result["error"] = "STT 실패 또는 전사 내용 없음"
                return result

            # STT 내용 기반 관련 노트 찾기
            self._ensure_indexer()
            self._ensure_obs()
            base_memo = ""
            if meeting_scope != "unknown":
                scope_label = "외부 회의" if meeting_scope == "external" else "내부 회의"
                base_memo = f"[회의 구분]\n- {scope_label}로 분류해 기록하세요."
            from meeting_minutes_app.meeting_pipeline import meeting_workflow as mw
            final_memo, related_titles, context_flags = mw.build_generation_context_memo(
                llm=self._llm,
                title=title,
                topic=topic,
                segments_or_text=segments,
                base_memo=base_memo,
                indexer=self._indexer,
                obs=self._obs,
            )
            if context_flags.get("wiki"):
                print(f"[pipeline] 생성 전 Obsidian 컨텍스트 주입: {len(related_titles)}개 노트")
            if context_flags.get("web"):
                print("[pipeline] 웹 리서치 컨텍스트 주입")

            # wiki_context.json 저장은 note_path 확정 후 per-meeting 폴더에 수행

            try:
                if any(re.match(r'[Ss]peaker[\s_]?[A-Za-z0-9]', s.get("speaker", ""))
                       for s in segments):
                    inferred = mm.infer_speaker_names(segments, self._llm, known_names=None)
                    for seg in segments:
                        if seg.get("speaker") in (inferred or {}):
                            seg["speaker"] = inferred[seg["speaker"]]
            except Exception:
                print(f"[pipeline] ⚠ 화자 이름 추론 실패: {traceback.format_exc()}")

            minutes = mm.generate_minutes(
                segments,
                self._llm,
                doc_type,
                final_memo,
                None,
                topic=topic,
                session_dt=session_dt,
                title=title,
            )
            summary = mm.generate_summary(
                minutes,
                self._llm,
                doc_type,
                topic=topic,
                session_dt=session_dt,
            )
            aj: Optional[str] = None
            actions_md = ""
            try:
                aj = mm.extract_action_items(minutes, self._llm, doc_type)
                if aj:
                    actions_md = mm.format_actions_md(aj)
            except Exception:
                print(f"[pipeline] ⚠ 액션 아이템 추출 실패: {traceback.format_exc()}")
            speakers = sorted({s.get("speaker", "") for s in segments if s.get("speaker")})

            if not minutes:
                result["error"] = "회의록 생성 실패"
                return result

            # 전사 텍스트 구성 (타임스탬프 포함)
            transcript_md = _build_transcript_md_from_segments(segments)

            # 섹션 파싱
            sections = _extract_sections(minutes)

            # ── 오프라인 Vault Enrichment (config.wiki.vault_enrich, 기본 true) ──
            # 엔티티(용어/인물/기관) 추출 → vault에서 관련 노트 검색
            # web_research 호출 없음 — vault 인덱스와 Obsidian REST만 사용.
            glossary_md = ""
            entities: Dict = {}
            if _c("wiki.vault_enrich", True):
                try:
                    from meeting_minutes_app.meeting_pipeline import enrichment as _enr
                    entities = _enr.extract_entities(minutes, self._llm, topic=topic)
                    entity_vault_hits: List[str] = []
                    all_entity_names: List[str] = []
                    for _elist in entities.values():
                        all_entity_names.extend(_elist)

                    for entity_name in all_entity_names:
                        # 1) 로컬 TF-IDF 인덱스
                        if self._indexer and self._indexer.is_built:
                            for hit in self._indexer.search(entity_name, limit=2):
                                t = hit.get("wikilink_title") or hit.get("title", "")
                                if t and t not in related_titles and t not in entity_vault_hits:
                                    entity_vault_hits.append(t)
                        # 2) Obsidian REST
                        if self._obs:
                            try:
                                for t in mw.search_related_notes_rest(
                                    self._obs, title=entity_name, limit=2
                                ):
                                    if t not in related_titles and t not in entity_vault_hits:
                                        entity_vault_hits.append(t)
                            except Exception:
                                pass

                    if entity_vault_hits:
                        print(f"[pipeline] 엔티티 기반 추가 관련 노트: {entity_vault_hits}")
                        related_titles = related_titles + entity_vault_hits[:10]

                    # 용어 목록 + vault 링크를 회의록 후미에 추가
                    if all_entity_names:
                        link_lines = []
                        for cat_key, cat_label in [("terms", "용어"), ("people", "인물"), ("orgs", "기관")]:
                            names = entities.get(cat_key, [])
                            if names:
                                link_lines.append(f"**{cat_label}**: " + ", ".join(
                                    f"[[{n}]]" if n in related_titles else n
                                    for n in names
                                ))
                        if link_lines:
                            glossary_md = "## 주요 용어·인물\n" + "\n".join(f"- {l}" for l in link_lines)
                except Exception as _ee:
                    print(f"[pipeline] 엔티티 vault 검색 스킵: {_ee}")

            # ── 참석자 폴백: vault 관련 노트에서 참석자 복원 ────────────────
            # STT 화자분리 미작동(청크 분할 등)으로 speakers=[] 일 때만 실행.
            if not speakers and (self._indexer or self._obs):
                try:
                    import re as _re
                    attendees_from_vault: List[str] = []
                    # 1단계: 관련 노트(이전 회의록 등)에서 참석자: 라인 파싱
                    for ref_title in related_titles[:8]:
                        content = ""
                        if self._indexer and self._indexer.is_built:
                            hits = self._indexer.search(ref_title, limit=1)
                            if hits and hits[0].get("score", 0) > 0.05:
                                content = self._indexer.get_note_content(
                                    hits[0]["path"]) or ""
                        if not content and self._obs:
                            try:
                                content = self._obs.get_note(ref_title + ".md") or ""
                            except Exception:
                                pass
                        for line in (content or "").split("\n"):
                            m = _re.search(
                                r'(?:참석자|attendees?)\s*[:：]\s*(.+)',
                                line, _re.IGNORECASE)
                            if m:
                                for name in _re.split(r'[,，、·]', m.group(1)):
                                    name = name.strip().strip("*").strip()
                                    if name and name not in ("미정", "TBD") \
                                            and name not in attendees_from_vault:
                                        attendees_from_vault.append(name)
                        if len(attendees_from_vault) >= 6:
                            break
                    # 2단계: vault에 전용 노트가 있는 인물 엔티티 보완
                    for person in entities.get("people", []):
                        if (person in related_titles
                                and person not in attendees_from_vault):
                            attendees_from_vault.append(person)
                    if attendees_from_vault:
                        speakers = attendees_from_vault[:10]
                        print(f"[pipeline] 참석자 vault에서 복원: {speakers}")
                except Exception:
                    pass

            # ── 사실 검증 (config.wiki.claim_verify=true 일 때만) ──────────
            verify_md = ""
            claim_results: List[Dict] = []
            if _c("wiki.claim_verify", False):
                try:
                    print("[pipeline] 사실 검증 중 (vault 비교)...")
                    verify_md, claim_results = mw.claim_verify(
                        minutes,
                        self._llm,
                        indexer=self._indexer,
                        obs=self._obs,
                        topic=topic,
                        max_claims=int(_c("wiki.claim_verify_max", 8) or 8),
                        current_title=title,
                    )
                    if verify_md:
                        conflicts = verify_md.count("- ⚠️")
                        matches   = verify_md.count("- ✅")
                        unknowns  = verify_md.count("- ❓")
                        print(f"[pipeline] 검증 완료: 충돌 {conflicts}, 일치 {matches}, 확인불가 {unknowns}")
                except Exception as _ve:
                    print(f"[pipeline] 사실 검증 스킵: {_ve}")

            # Obsidian 노트 저장
            note_path: Optional[str] = None

            obs_vault_root: str = ""
            if self._obs:
                from meeting_minutes_app.wiki_core.obsidian import ObsidianClient
                stt_meta = {
                    "stt_segment_count": len(segments or []),
                    "stt_raw_chars": sum(len(str(s.get("text", ""))) for s in segments or []),
                    "stt_source": "new_stt",
                }
                _body = (minutes
                         + ("\n\n" + glossary_md if glossary_md else "")
                         + ("\n\n" + verify_md if verify_md else ""))
                _rel_path = self._obs.write_recording_note(
                    title=title,
                    body_md=_body,
                    doc_type=doc_type,
                    topic=topic,
                    attendees=speakers,
                    session_dt=session_dt,
                    summary_md=summary,
                    actions_md=actions_md,
                    related_notes=related_titles,
                    source_audio=audio_path,
                    source_file_date=_extract_date_from_path(audio_path),
                    processed_at=datetime.now().isoformat(timespec="seconds"),
                    stt_meta=stt_meta,
                    duration=duration,
                    key_points=sections.get("key_points"),
                    decisions=sections.get("decisions"),
                    open_questions=sections.get("open_questions"),
                    important_claims=sections.get("important_claims"),
                    transcript_md=transcript_md,
                    meeting_scope=meeting_scope,
                    evidence=mw.evidence_to_wikilinks(context_flags.get("evidence", [])),
                )
                if _rel_path:
                    # vault-relative → 풀 경로 변환 (이메일 첨부용)
                    obs_vault_root = _c("obsidian.vault_path", "") or ""
                    if not obs_vault_root:
                        try:
                            from meeting_minutes_app.wiki_core.obsidian import _detect_obsidian_config
                            obs_vault_root = _detect_obsidian_config().get("vault_path", "")
                        except Exception:
                            pass
                    note_path = (
                        os.path.join(obs_vault_root, _rel_path)
                        if obs_vault_root else _rel_path
                    )
                    print(f"[pipeline] Obsidian 노트 저장: {_rel_path}")
                else:
                    # Obsidian write 실패 → 로컬 output/ fallback (STT 결과 유실 방지)
                    print("[pipeline] Obsidian 저장 실패 → output/ 폴더에 로컬 저장")
                    _body_local = (minutes
                                   + ("\n\n" + glossary_md if glossary_md else "")
                                   + ("\n\n" + verify_md if verify_md else ""))
                    note_path = _save_to_output(
                        title=title, doc_type=doc_type,
                        minutes=_body_local,
                        summary=summary, actions_md=actions_md,
                        related_titles=related_titles, sections=sections,
                        transcript_md=transcript_md, audio_path=audio_path,
                        meeting_scope=meeting_scope,
                    )
                    if note_path:
                        print(f"[pipeline] 로컬 저장 완료: {note_path}")
            else:
                # Obsidian 없으면 output/ 폴더에 저장
                _body_local = (minutes
                               + ("\n\n" + glossary_md if glossary_md else "")
                               + ("\n\n" + verify_md if verify_md else ""))
                note_path = _save_to_output(
                    title=title, doc_type=doc_type,
                    minutes=_body_local,
                    summary=summary, actions_md=actions_md,
                    related_titles=related_titles, sections=sections,
                    transcript_md=transcript_md, audio_path=audio_path,
                    meeting_scope=meeting_scope,
                )
                if note_path:
                    print(f"[pipeline] 파일 저장: {note_path}")

            result["status"] = "done" if note_path else "failed"
            result["note_path"] = note_path or ""
            if not note_path:
                result["error"] = "노트 저장 실패"

            # notify.on_finish == "email" 이면 노트 저장 성공 후 자동 이메일 발송.
            # 테스트/수동 검증에서는 send_email=False 로 명시 차단할 수 있다.
            should_email = (
                note_path
                and send_email is not False
                and _c("notify.on_finish", "") == "email"
            )
            if should_email:
                try:
                    from meeting_minutes_app.meeting_pipeline.vault_audio import _send_email_summary
                    _send_email_summary(
                        title=f"[회의록] {title}",
                        summary=summary,
                        actions_md=actions_md,
                        attachment_paths=[note_path],
                        minutes_md=minutes,
                    )
                except Exception as _ne:
                    print(f"[pipeline] 이메일 발송 실패 (무시): {_ne}")

            # ── Wiki Registry 갱신 (노트 저장 성공 후, 실패해도 결과에 영향 없음) ──
            if result["status"] == "done":
                try:
                    from meeting_minutes_app.wiki_core.wiki_knowledge import (
                        update_action_registry_from_actions,
                        update_decision_registry_from_minutes,
                    )
                    source_note_path = result.get("note_path", "")
                    if aj and doc_type == "meeting":
                        update_action_registry_from_actions(
                            aj,
                            source_meeting=title,
                            source_note=source_note_path,
                        )
                    decision_list = sections.get("decisions", [])
                    if not decision_list:
                        # _extract_sections 미감지 시 직접 파싱 재시도
                        from meeting_minutes_app.wiki_core.wiki_knowledge import extract_decisions_from_minutes
                        decision_list = extract_decisions_from_minutes(minutes)
                    if decision_list and doc_type == "meeting":
                        update_decision_registry_from_minutes(
                            decision_list,
                            source_meeting=title,
                            source_note=source_note_path,
                        )
                except Exception as _wke:
                    print(f"[pipeline] Wiki Registry 갱신 실패 (무시): {_wke}")

                # ── Wiki Context Package 저장 (per-meeting 폴더 or output 루트) ──
                try:
                    from meeting_minutes_app.wiki_core.wiki_knowledge import (
                        build_wiki_context_package,
                        save_wiki_context_package,
                    )
                    _ctx_pkg = build_wiki_context_package(
                        related_titles=related_titles,
                        data_dir=Path(PROJECT_ROOT) / "data",
                        related_details=context_flags.get("evidence", []),
                    )
                    _out_base = Path(PROJECT_ROOT) / str(_c("output_dir", "output"))
                    _ctx_save_dir = (
                        Path(note_path).parent
                        if (note_path and not obs_vault_root)
                        else _out_base
                    )
                    save_wiki_context_package(_ctx_pkg, _ctx_save_dir)
                except Exception as _cpe:
                    print(f"[pipeline] wiki_context.json 저장 실패 (무시): {_cpe}")

                # ── Wiki Update Proposal (meeting 타입, 관련 노트 있을 때만) ──
                if doc_type == "meeting" and related_titles:
                    try:
                        from meeting_minutes_app.wiki_core.wiki_knowledge import (
                            build_wiki_update_proposal,
                            save_wiki_update_proposal,
                        )
                        _out_dir = Path(PROJECT_ROOT) / str(_c("output_dir", "output"))
                        _proposal = build_wiki_update_proposal(
                            meeting_title=title,
                            minutes_text=minutes,
                            related_titles=related_titles,
                            llm=self._llm,
                            claim_results=claim_results,
                        )
                        save_wiki_update_proposal(_proposal, _out_dir)
                    except Exception as _wpe:
                        print(f"[pipeline] Wiki Update Proposal 생성 실패 (무시): {_wpe}")

        except Exception as e:
            result["error"] = str(e)
            traceback.print_exc()

        return result


def _build_transcript_md_from_segments(segments: List[Dict]) -> str:
    """STT 세그먼트 리스트에서 타임스탬프 전사 마크다운을 생성한다."""
    if not segments:
        return ""
    lines = []
    for seg in segments:
        start = seg.get("start", 0)
        m, s = divmod(int(start), 60)
        ts = f"[{m:02d}:{s:02d}]"
        speaker = seg.get("speaker", "")
        text = seg.get("text", "").strip()
        if not text:
            continue
        if speaker:
            lines.append(f"{ts} **{speaker}**: {text}")
        else:
            lines.append(f"{ts} {text}")
    return "\n".join(lines)


def _has_markdown_section(content: str, *names: str) -> bool:
    if not content:
        return False
    return any(
        re.search(rf"(?mi)^#{{1,6}}\s*{re.escape(name)}\b", content)
        for name in names
    )


def _expected_recording_note_path(title: str, session_dt: str = "") -> str:
    """ObsidianClient.write_recording_note()가 사용할 기본 상대 경로를 미리 계산한다."""
    try:
        from meeting_minutes_app.wiki_core.obsidian import _expand_path_template, safe_filename
        from meeting_minutes_app.common.date_utils import iso_to_yymmdd
    except Exception:
        safe_filename = lambda s: re.sub(r'[\\/:*?"<>|]', "_", s).strip()[:80]  # noqa: E731
        _expand_path_template = lambda path, date_str="": str(path or "").strip("/")  # noqa: E731
        iso_to_yymmdd = lambda s: re.sub(r"\D", "", s)[:6]  # noqa: E731
    out_folder = (
        _c("obsidian.meetings_path", "")
        or _c("vault_watcher.output_folder", "Inbox/Processed Recordings")
    )
    date_str = (session_dt or datetime.now().strftime("%Y-%m-%d"))[:10]
    out_folder = _expand_path_template(out_folder, date_str)
    file_date = iso_to_yymmdd(date_str) or datetime.now().strftime("%y%m%d")
    base = safe_filename(f"{file_date} {title}" if title else f"{file_date} 녹음 기록")
    return f"{out_folder.strip('/')}/{base}.md"


def _save_to_output(title: str, doc_type: str, minutes: str, summary: str,
                    actions_md: str, related_titles: List[str], sections: Dict,
                    transcript_md: str, audio_path: str,
                    meeting_scope: str = "unknown") -> Optional[str]:
    """Obsidian 없을 때 output/ 폴더에 마크다운 파일로 저장."""
    import json as _json
    output_dir = _c("output_dir", "./output")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_title = re.sub(r'[\\/:*?"<>|]', '_', title)[:60]
    folder = os.path.join(output_dir, f"{ts}_{safe_title}")
    os.makedirs(folder, exist_ok=True)

    parts = [f"# {title}\n"]
    if meeting_scope and meeting_scope != "unknown":
        label = "외부 회의" if meeting_scope == "external" else "내부 회의"
        parts.append(f"> 회의 구분: {label}\n")
    if summary:
        parts.append("## 요약\n" + summary.strip() + "\n")
    if sections.get("key_points") and not _has_markdown_section(minutes, "핵심 포인트", "주요 논의 내용"):
        parts.append("## 핵심 포인트\n" +
                     "\n".join(f"- {k}" for k in sections["key_points"]) + "\n")
    parts.append(minutes.strip() + "\n")
    if sections.get("decisions") and not _has_markdown_section(minutes, "결정 사항", "결정 사항(합의/정리된 방향)"):
        parts.append("## 결정 사항\n" +
                     "\n".join(f"- {d}" for d in sections["decisions"]) + "\n")
    if actions_md and not _has_markdown_section(minutes, "Action Item", "액션 아이템"):
        parts.append("## 액션 아이템\n" + actions_md.strip() + "\n")
    if sections.get("open_questions") and not _has_markdown_section(minutes, "미해결 질문", "오픈 이슈"):
        parts.append("## 미해결 질문\n" +
                     "\n".join(f"- {q}" for q in sections["open_questions"]) + "\n")
    if related_titles:
        parts.append("## 관련 노트\n" +
                     "\n".join(f"- [[{t}]]" for t in related_titles) + "\n")
    transcript_mode = str(_c("obsidian.transcript_mode", "separate") or "separate").lower()
    if transcript_md and transcript_mode in ("separate", "note", "file"):
        parts.append("## 원문 전사\n- `transcript.md` 파일에 전체 STT 전사를 별도 보관\n")
    elif transcript_md and transcript_mode == "append":
        parts.append("## 전사 (Transcript)\n" + transcript_md + "\n")

    content = "\n".join(parts)
    out_path = os.path.join(folder, "recording_note.md")
    try:
        with open(out_path, "w", encoding="utf-8") as _f:
            _f.write(content)
        if transcript_md and transcript_mode in ("separate", "note", "file"):
            with open(os.path.join(folder, "transcript.md"), "w", encoding="utf-8") as _f:
                _f.write("# 전체 STT 전사\n\n## 전사 (Transcript)\n" + transcript_md.strip() + "\n")
        return out_path
    except Exception:
        print(f"[pipeline] ⚠ 로컬 노트 파일 저장 실패: {out_path}\n{traceback.format_exc()}")
        return None


def ingest_file(audio_path: str, **kwargs) -> Dict[str, Any]:
    """편의 함수 — IngestionPipeline 인스턴스 없이 단일 파일 처리."""
    pipeline = IngestionPipeline()
    return pipeline.ingest(audio_path, **kwargs)


def main():
    import argparse
    ap = argparse.ArgumentParser(description="오디오 파일 수동 처리")
    ap.add_argument("--file", required=True, help="처리할 오디오 파일 경로")
    ap.add_argument("--type", default="", help="문서 유형: meeting|seminar|lecture|memo")
    ap.add_argument("--title", default="", help="노트 제목")
    ap.add_argument("--topic", default="", help="주제")
    ap.add_argument("--force", action="store_true", help="이미 처리된 파일도 재처리")
    ap.add_argument("--no-email", action="store_true",
                    help="config notify.on_finish=email 이어도 이번 실행에서는 이메일을 보내지 않음")
    args = ap.parse_args()

    result = ingest_file(
        audio_path=args.file,
        doc_type=args.type,
        title=args.title,
        topic=args.topic,
        force=args.force,
        send_email=False if args.no_email else None,
    )
    status = result["status"]
    print(f"\n[pipeline] 결과: {status}")
    if result.get("note_path"):
        print(f"  노트: {result['note_path']}")
    if result.get("error"):
        print(f"  오류: {result['error']}")


if __name__ == "__main__":
    main()
