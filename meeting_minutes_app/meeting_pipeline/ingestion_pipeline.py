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
        from meeting_minutes_app.meeting_pipeline.date_utils import parse_iso_date_from_text
        return parse_iso_date_from_text(audio_path, default_today=True)
    except Exception:
        return datetime.now().strftime("%Y-%m-%d")


def _detect_type_from_filename(audio_path: str) -> str:
    """파일명에서 문서 유형을 추론한다. 매치 없으면 빈 문자열(호출자가
    내용 기반 LLM 보완 또는 기본값으로 처리) — 과거엔 여기서 바로
    config.analysis.default_type으로 떨어져, 자동 녹음기 기본 파일명처럼
    키워드가 없는 경우 내용과 무관하게 항상 "meeting"으로 굳어지는 공백이 있었다."""
    stem = Path(audio_path).stem.lower()
    for doc_type, pat in _TYPE_PATTERNS.items():
        if pat.search(stem):
            return doc_type
    return ""


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

        # 문서 유형 감지 — 1차: 명시적 인자 또는 파일명 키워드.
        # 둘 다 없으면 STT 완료 후 전사 내용으로 LLM이 보완 판단(아래).
        doc_type_confirmed = bool(doc_type)
        if not doc_type:
            doc_type = _detect_type_from_filename(audio_path)
            doc_type_confirmed = bool(doc_type)

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
            expected_paths = _expected_recording_note_paths(title, topic, session_dt)
            self._ensure_obs()
            if self._obs and not force:
                for expected_rel_path in expected_paths:
                    if expected_rel_path and self._obs.exists(expected_rel_path):
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
            from meeting_minutes_app.meeting_pipeline import stt
            from meeting_minutes_app.meeting_pipeline import minutes_generation as mg
            work_dir = tempfile.mkdtemp(prefix="ingest_audio_")
            try:
                audio2 = stt.prepare_audio(audio_path, work_dir)
                segments = stt.run_stt(
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

            # 문서 유형 2차: 파일명으로 못 정했으면 전사 내용을 LLM에게 보여 보완
            # 판단(자동 녹음기 기본 파일명처럼 키워드가 없어도 세미나/강의를
            # 놓치지 않게 함). 그래도 못 정하면 최종적으로 config 기본값.
            if not doc_type_confirmed and _c("wiki_knowledge.doc_type_classify_llm", True):
                from meeting_minutes_app.meeting_pipeline import meeting_workflow as mw
                sample_text = mw.segments_to_search_text(segments)
                llm_doc_type = mw.classify_doc_type_llm(sample_text, self._llm)
                if llm_doc_type:
                    doc_type = llm_doc_type
                    print(f"[pipeline] 문서 유형 내용 기반 판별: {doc_type}")
            if not doc_type:
                doc_type = _c("analysis.default_type", "meeting")

            # 화자 라벨 → 실명 추론 (STT 특화 전처리, finalize 호출 전에 segments에 반영)
            try:
                if any(re.match(r'[Ss]peaker[\s_]?[A-Za-z0-9]', s.get("speaker", ""))
                       for s in segments):
                    inferred = mg.infer_speaker_names(segments, self._llm, known_names=None)
                    for seg in segments:
                        if seg.get("speaker") in (inferred or {}):
                            seg["speaker"] = inferred[seg["speaker"]]
            except Exception:
                print(f"[pipeline] ⚠ 화자 이름 추론 실패: {traceback.format_exc()}")

            speakers = sorted({s.get("speaker", "") for s in segments if s.get("speaker")})

            self._ensure_indexer()
            self._ensure_obs()

            # ── 참석자 폴백: vault 관련 노트에서 참석자 복원 (watcher 고유 기능) ──
            # STT 화자분리 미작동(청크 분할 등)으로 speakers=[] 일 때만, 제목/주제로
            # 직접 검색해 후보 노트를 찾는다(finalize의 컨텍스트 메모와 무관한 독립 검색).
            if not speakers and self._indexer and self._indexer.is_built:
                try:
                    import re as _re
                    attendees_from_vault: List[str] = []
                    candidate_titles = [
                        r.get("wikilink_title") or r.get("title", "")
                        for r in self._indexer.search(f"{title} {topic}", limit=8)
                    ]
                    for ref_title in candidate_titles:
                        if not ref_title:
                            continue
                        content = ""
                        hits = self._indexer.search(ref_title, limit=1)
                        if hits and hits[0].get("score", 0) > 0.05:
                            content = self._indexer.get_note_content(hits[0]["path"]) or ""
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
                    if attendees_from_vault:
                        speakers = attendees_from_vault[:10]
                        print(f"[pipeline] 참석자 vault에서 복원: {speakers}")
                except Exception:
                    pass

            base_memo = ""
            if meeting_scope != "unknown":
                scope_label = "외부 회의" if meeting_scope == "external" else "내부 회의"
                base_memo = f"[회의 구분]\n- {scope_label}로 분류해 기록하세요."

            transcript_md = _build_transcript_md_from_segments(segments)
            stt_meta = {
                "stt_segment_count": len(segments or []),
                "stt_raw_chars": sum(len(str(s.get("text", ""))) for s in segments or []),
                "stt_source": "new_stt",
            }

            # ── 공용 오케스트레이터로 통합 (refine→minutes→actions→claim_verify→
            # summary→script→publish[enrich_and_publish, 자동분류 라우팅 포함]→
            # wiki_context/proposal→registries→graph_sync). 배치(pipeline.py)/
            # 실시간(realtime_transcription.py)/웹(web/backend/api/realtime.py)과
            # 동일한 품질 보장 — 과거엔 이 전체를 손으로 복제해 enrichment가
            # 축소판(웹 리서치·신규 참조노트 생성 없음)이었고 재인덱싱/그래프
            # 동기화가 빠져 있었다.
            from meeting_minutes_app.meeting_pipeline import finalize as fz
            inputs = fz.SessionInputs(
                segments=segments, title=title, topic=topic, doc_type=doc_type,
                session_dt=session_dt, base_memo=base_memo, source="ingest",
                attendees=speakers,
            )
            options = fz.FinalizeOptions(
                llm=self._llm, indexer=self._indexer, obs=self._obs,
                do_graph_sync=True,   # watcher도 그래프 동기화 켬(기존엔 웹앱만)
                notify=(None if send_email is False
                        else (_c("notify.on_finish", "") or None)),
                artifacts_dir=Path(PROJECT_ROOT) / str(_c("output_dir", "output")) / "ingest",
                publish_extra={
                    "source_audio": audio_path,
                    "source_file_date": _extract_date_from_path(audio_path),
                    "stt_meta": stt_meta,
                    "transcript_md": transcript_md,
                },
            )
            res = fz.run_post_session(inputs, options)

            if not res.minutes:
                result["error"] = "회의록 생성 실패"
                return result

            note_path: Optional[str] = res.source_note or None
            if not note_path:
                # Obsidian 미연결/발행 실패 — STT/생성 결과 유실 방지용 로컬 폴백
                # (기존 동작 유지: watcher는 무인 처리라 결과를 조용히 버리면 안 됨).
                print("[pipeline] Obsidian 저장 실패 → output/ 폴더에 로컬 저장")
                note_path = _save_to_output(
                    title=title, doc_type=doc_type, minutes=res.minutes,
                    summary=res.summary, actions_md=res.actions_md,
                    related_titles=res.related_note_titles,
                    sections=_extract_sections(res.minutes),
                    transcript_md=transcript_md, audio_path=audio_path,
                    meeting_scope=meeting_scope,
                )
                if note_path:
                    print(f"[pipeline] 로컬 저장 완료: {note_path}")

            result["status"] = "done" if note_path else "failed"
            result["note_path"] = note_path or ""
            if not note_path:
                result["error"] = "노트 저장 실패"

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


def _expected_recording_note_paths(title: str, topic: str = "", session_dt: str = "") -> List[str]:
    """ObsidianClient가 실제로 쓸 상대경로 후보들을 STT 전에 미리 계산한다
    (이미 처리된 파일인지 중복 처리 방지 체크용).

    obsidian.auto_route_enabled=true면 실제 저장 폴더는 classify_meeting_route()가
    스크립트 내용까지 보고 동적으로 정하는데, 이 시점(STT 전)엔 전사가 아직 없다.
    그래서 정적 기본 경로 하나만이 아니라, 제목/주제만으로 키워드 매칭한(LLM 호출은
    생략 — llm=None) 라우팅 후보도 함께 반환한다. 완벽한 예측은 불가능하고 최악의
    경우 이미 처리된 파일이 한 번 더 처리될 뿐(중복 노트, 데이터 유실은 아님)."""
    try:
        from meeting_minutes_app.wiki_core.obsidian import _expand_path_template, safe_filename
        from meeting_minutes_app.meeting_pipeline.date_utils import iso_to_yymmdd
    except Exception:
        safe_filename = lambda s: re.sub(r'[\\/:*?"<>|]', "_", s).strip()[:80]  # noqa: E731
        _expand_path_template = lambda path, date_str="", project="": str(path or "").strip("/")  # noqa: E731
        iso_to_yymmdd = lambda s: re.sub(r"\D", "", s)[:6]  # noqa: E731

    date_str = (session_dt or datetime.now().strftime("%Y-%m-%d"))[:10]
    file_date = iso_to_yymmdd(date_str) or datetime.now().strftime("%y%m%d")
    base = safe_filename(f"{file_date} {title}" if title else f"{file_date} 녹음 기록")

    meetings_path = _c("obsidian.meetings_path", "")
    default_folder = meetings_path or _c("vault_watcher.output_folder", "Inbox/Processed Recordings")
    default_folder = _expand_path_template(default_folder, date_str)
    candidates = [f"{default_folder.strip('/')}/{base}.md"]

    if _c("obsidian.auto_route_enabled", False):
        try:
            from meeting_minutes_app.meeting_pipeline import meeting_workflow as mw
            route = mw.classify_meeting_route(title, topic, script_excerpt="", llm=None)
            if route.get("mode") == "domain":
                domains = _c("obsidian.project_domains", {}) or {}
                domain_folder = domains.get(route["project"], route["project"])
                route_folder = _expand_path_template(
                    meetings_path or default_folder, date_str, project=domain_folder)
            else:
                route_folder = route.get("output_folder", "")
            if route_folder:
                candidate = f"{route_folder.strip('/')}/{base}.md"
                if candidate not in candidates:
                    candidates.append(candidate)
        except Exception:
            pass

    return candidates


def _save_to_output(title: str, doc_type: str, minutes: str, summary: str,
                    actions_md: str, related_titles: List[str], sections: Dict,
                    transcript_md: str, audio_path: str,
                    meeting_scope: str = "unknown") -> Optional[str]:
    """Obsidian 없을 때 output/ 폴더에 마크다운 파일로 저장."""
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
