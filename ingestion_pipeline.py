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

for _stream in (sys.stdout, sys.stderr):
    if getattr(_stream, "encoding", None) and _stream.encoding.lower() in ("cp949", "euc-kr", "ansi"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

try:
    import config_loader as _cfg
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
    path = HERE / templates_dir / f"{doc_type}_analysis.md"
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
            import meeting_minutes as mm
            self._llm = mm.LLMClient(preferred=mm._c("models.llm", "gpt") or "gpt")
            self._llm_initialized = True

    def _ensure_obs(self):
        if self._obs is None:
            try:
                from obsidian import ObsidianClient
                obs = ObsidianClient.from_config()
                if obs and obs.ping():
                    self._obs = obs
            except Exception:
                pass

    def _ensure_indexer(self):
        if self._indexer is None:
            try:
                from vault_indexer import VaultIndexer
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
            session_dt = datetime.now().strftime("%Y-%m-%d")
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
                import meeting_minutes as mm
                duration = mm.audio_duration(audio_path)
                result["duration"] = duration
            except Exception:
                duration = 0.0

            # STT를 먼저 수행하고, STT 발화 내용으로 Obsidian을 검색한 뒤 회의록 생성에 주입
            self._ensure_llm()

            # 온라인 배경 조사 (wiki.online_search_enabled=true 시)
            web_memo = ""
            if meeting_scope != "unknown":
                scope_label = "외부 회의" if meeting_scope == "external" else "내부 회의"
                web_memo = f"[회의 구분]\n- {scope_label}로 분류해 기록하세요.\n\n"
            if _c("wiki.online_search_enabled", False):
                research_query = topic or title
                if research_query:
                    print(f"[pipeline] 웹 리서치 중: '{research_query}'")
                    try:
                        res = self._llm.web_research(research_query)
                        if res.get("text"):
                            src_lines = ""
                            if res.get("sources"):
                                src_lines = "\n" + "\n".join(
                                    f"  - [{s.get('title', s['url'])}]({s['url']})"
                                    for s in res["sources"][:3]
                                )
                            web_memo += (
                                f"[웹 리서치: '{research_query}']\n"
                                f"{res['text']}"
                                f"{src_lines}"
                            )
                            searched = "(웹검색 실시)" if res.get("searched") else "(모델 지식 기반)"
                            print(f"[pipeline] 웹 리서치 완료 {searched}: {len(res['text'])}자")
                    except Exception as _e:
                        print(f"[pipeline] 웹 리서치 실패 (무시): {_e}")

            import meeting_minutes as mm
            work_dir = tempfile.mkdtemp(prefix="ingest_audio_")
            try:
                audio2 = mm.prepare_audio(audio_path, work_dir)
                segments = mm.run_stt(
                    audio2,
                    model=mm._c("models.stt", None),
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
            related_titles: List[str] = []
            if self._indexer and self._indexer._built:
                # 세그먼트 실제 발화 내용 기반 검색 (레이블보다 정확도 높음)
                seg_texts = " ".join(s.get("text", "") for s in segments[:30])
                related_titles = self._indexer.find_related(seg_texts, limit=5)
                if related_titles:
                    print(f"[pipeline] 관련 노트 {len(related_titles)}개: "
                          f"{', '.join(related_titles[:3])}")
            if self._obs:
                try:
                    rest_titles = _search_related_notes_rest(
                        self._obs,
                        title=title,
                        topic=topic,
                        segments=segments,
                        limit=5,
                    )
                    for rn in rest_titles:
                        if rn not in related_titles:
                            related_titles.append(rn)
                    related_titles = related_titles[:5]
                    if related_titles:
                        print(f"[pipeline] 관련 노트(Obsidian REST) {len(related_titles)}개: "
                              f"{', '.join(related_titles[:3])}")
                except Exception as _se:
                    print(f"[pipeline] Obsidian REST 검색 실패 (무시): {_se}")

            offline_memo = _build_related_notes_memo(
                self._indexer,
                self._obs,
                related_titles,
                max_chars_per_note=int(_c("wiki.context_max_chars", 2000) or 2000),
            )
            final_memo = "\n\n".join(x for x in [web_memo.strip(), offline_memo.strip()] if x)
            if offline_memo:
                print(f"[pipeline] STT 기반 Obsidian 컨텍스트 주입: {len(related_titles)}개 노트")

            try:
                if any(re.match(r'[Ss]peaker[\s_]?[A-Za-z0-9]', s.get("speaker", ""))
                       for s in segments):
                    inferred = mm.infer_speaker_names(segments, self._llm, known_names=None)
                    for seg in segments:
                        if seg.get("speaker") in (inferred or {}):
                            seg["speaker"] = inferred[seg["speaker"]]
            except Exception:
                pass

            minutes = mm.generate_minutes(
                segments,
                self._llm,
                doc_type,
                final_memo or None,
                None,
                topic=topic,
                session_dt=session_dt,
            )
            summary = mm.generate_summary(
                minutes,
                self._llm,
                doc_type,
                topic=topic,
                session_dt=session_dt,
            )
            actions_md = ""
            try:
                aj = mm.extract_action_items(minutes, self._llm, doc_type)
                if aj:
                    actions_md = mm.format_actions_md(aj)
            except Exception:
                pass
            speakers = sorted({s.get("speaker", "") for s in segments if s.get("speaker")})

            if not minutes:
                result["error"] = "회의록 생성 실패"
                return result

            # 전사 텍스트 구성 (타임스탬프 포함)
            transcript_md = _build_transcript_md_from_segments(segments)

            # 섹션 파싱
            sections = _extract_sections(minutes)

            # Obsidian 노트 저장
            note_path: Optional[str] = None

            obs_vault_root: str = ""
            if self._obs:
                from obsidian import ObsidianClient
                _rel_path = self._obs.write_recording_note(
                    title=title,
                    body_md=minutes,
                    doc_type=doc_type,
                    topic=topic,
                    attendees=speakers,
                    session_dt=session_dt,
                    summary_md=summary,
                    actions_md=actions_md,
                    related_notes=related_titles,
                    source_audio=audio_path,
                    processed_at=datetime.now().isoformat(timespec="seconds"),
                    duration=duration,
                    key_points=sections.get("key_points"),
                    decisions=sections.get("decisions"),
                    open_questions=sections.get("open_questions"),
                    important_claims=sections.get("important_claims"),
                    transcript_md=transcript_md,
                    meeting_scope=meeting_scope,
                )
                if _rel_path:
                    # vault-relative → 풀 경로 변환 (이메일 첨부용)
                    obs_vault_root = _c("obsidian.vault_path", "") or ""
                    if not obs_vault_root:
                        try:
                            from obsidian import _detect_obsidian_config
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
                    note_path = _save_to_output(
                        title=title, doc_type=doc_type, minutes=minutes,
                        summary=summary, actions_md=actions_md,
                        related_titles=related_titles, sections=sections,
                        transcript_md=transcript_md, audio_path=audio_path,
                        meeting_scope=meeting_scope,
                    )
                    if note_path:
                        print(f"[pipeline] 로컬 저장 완료: {note_path}")
            else:
                # Obsidian 없으면 output/ 폴더에 저장
                note_path = _save_to_output(
                    title=title, doc_type=doc_type, minutes=minutes,
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
                    from vault_audio import _send_email_summary
                    _send_email_summary(
                        title=f"[회의록] {title}",
                        summary=summary,
                        actions_md=actions_md,
                        attachment_paths=[note_path],
                    )
                except Exception as _ne:
                    print(f"[pipeline] 이메일 발송 실패 (무시): {_ne}")

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


def _search_related_notes_rest(obs, *, title: str, topic: str,
                               segments: List[Dict], limit: int = 5) -> List[str]:
    """Obsidian REST 검색 fallback.

    로컬 vault_indexer가 없을 때도 기존 노트를 찾기 위해 여러 짧은 질의를 병합한다.
    현재 생성 중인 노트는 제외해 자기 자신이 관련 노트로 붙지 않게 한다.
    """
    current_title = (title or "").strip()
    current_norm = _norm_title(current_title)
    early_text = " ".join(s.get("text", "") for s in (segments or [])[:10])
    terms = _keyword_terms(" ".join([title or "", topic or "", early_text]))

    queries: List[str] = []
    for q in (title, topic, early_text[:220], " ".join(terms[:6])):
        q = (q or "").strip()
        if q and q not in queries:
            queries.append(q[:500])

    ranked: Dict[str, float] = {}
    for q in queries:
        try:
            for r in obs.search_simple(q, context_length=120, limit=max(limit * 2, 8)) or []:
                fname = str(r.get("filename", "")).replace("\\", "/")
                note_title = Path(fname).stem
                note_norm = _norm_title(note_title)
                if (
                    not note_title
                    or note_norm == current_norm
                    or (current_norm and current_norm in note_norm)
                    or (note_norm and note_norm in current_norm)
                ):
                    continue
                # Local REST API 점수는 설치/버전에 따라 음수일 수 있으므로
                # 검색 등장 횟수 + 제목 키워드 일치로 자체 랭킹한다.
                bonus = sum(2.0 for term in terms[:10] if _norm_title(term) in note_norm)
                ranked[note_title] = ranked.get(note_title, 0.0) + 1.0 + bonus
        except Exception:
            continue
    return [t for t, _ in sorted(ranked.items(), key=lambda x: (-x[1], x[0]))[:limit]]


def _keyword_terms(text: str) -> List[str]:
    """검색 질의 확장용 간단 키워드 추출."""
    stop = {
        "회의", "검토", "진행", "기존", "데이터", "참조", "제공", "가능",
        "구분", "내용", "관련", "그리고", "offline", "online",
    }
    seen = set()
    out: List[str] = []
    for term in re.findall(r"[가-힣A-Za-z0-9]{2,}", text or ""):
        low = term.lower()
        if low in stop or low in seen:
            continue
        seen.add(low)
        out.append(term)
    return out


def _norm_title(text: str) -> str:
    return re.sub(r"[\s_\-./\\]+", "", (text or "").lower())


def _build_related_notes_memo(indexer, obs, titles: List[str],
                              max_chars_per_note: int = 2000) -> str:
    """관련 Obsidian 노트 본문 일부를 회의록 생성용 메모로 만든다."""
    if not titles:
        return ""
    blocks: List[str] = []
    for title in titles:
        content = _get_related_note_content(indexer, obs, title)
        if not content:
            continue
        blocks.append(
            f"### [[{title}]]\n"
            f"{_strip_frontmatter(content).strip()[:max_chars_per_note]}"
        )
    if not blocks:
        return ""
    return (
        "[STT 기반 Obsidian 관련 노트]\n"
        "아래는 전사 내용으로 검색한 기존 노트입니다. 회의록 작성 시 사실 확인과 배경 연결에만 사용하고, "
        "새 회의에서 직접 언급되지 않은 내용은 '참고 배경'으로 구분하세요.\n\n"
        + "\n\n".join(blocks)
    )


def _get_related_note_content(indexer, obs, title: str) -> str:
    norm = _norm_title(title)
    if indexer and getattr(indexer, "_notes", None):
        for rel, note in indexer._notes.items():
            candidates = [
                note.get("title", ""),
                note.get("wikilink_title", ""),
                Path(rel).stem,
            ]
            if any(_norm_title(c) == norm for c in candidates):
                return indexer.get_note_content(rel) or ""
    if obs:
        try:
            hits = obs.search_simple(title, context_length=80, limit=5) or []
            for h in hits:
                fname = str(h.get("filename", ""))
                if _norm_title(Path(fname.replace("\\", "/")).stem) == norm:
                    return obs.get_note(fname) or ""
        except Exception:
            return ""
    return ""


def _strip_frontmatter(content: str) -> str:
    return re.sub(r"^---\s*\n.*?\n---\s*\n", "", content or "", flags=re.DOTALL)


def _expected_recording_note_path(title: str, session_dt: str = "") -> str:
    """ObsidianClient.write_recording_note()가 사용할 기본 상대 경로를 미리 계산한다."""
    try:
        from obsidian import safe_filename
    except Exception:
        safe_filename = lambda s: re.sub(r'[\\/:*?"<>|]', "_", s).strip()[:80]  # noqa: E731
    out_folder = _c("vault_watcher.output_folder", "Inbox/Processed Recordings")
    date_str = (session_dt or datetime.now().strftime("%Y-%m-%d"))[:10]
    base = safe_filename(f"{date_str} {title}" if title else f"{date_str} 녹음 기록")
    return f"{out_folder}/{base}.md"


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
    if sections.get("key_points"):
        parts.append("## 핵심 포인트\n" +
                     "\n".join(f"- {k}" for k in sections["key_points"]) + "\n")
    parts.append(minutes.strip() + "\n")
    if sections.get("decisions"):
        parts.append("## 결정 사항\n" +
                     "\n".join(f"- {d}" for d in sections["decisions"]) + "\n")
    if actions_md:
        parts.append("## 액션 아이템\n" + actions_md.strip() + "\n")
    if sections.get("open_questions"):
        parts.append("## 미해결 질문\n" +
                     "\n".join(f"- {q}" for q in sections["open_questions"]) + "\n")
    if related_titles:
        parts.append("## 관련 노트\n" +
                     "\n".join(f"- [[{t}]]" for t in related_titles) + "\n")
    if transcript_md:
        parts.append("## 전사 (Transcript)\n" + transcript_md + "\n")

    content = "\n".join(parts)
    out_path = os.path.join(folder, "recording_note.md")
    try:
        open(out_path, "w", encoding="utf-8").write(content)
        return out_path
    except Exception:
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
