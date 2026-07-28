"""
vault_audio.py — 옵시디언 자체 녹음(노트에 임베드된 오디오) 처리
====================================================================
Obsidian 오디오 레코더는 녹음을 볼트에 파일(.webm 등)로 저장하고 노트에
`![[recording.webm]]` 로 임베드한다. 이 임베드 링크가 "이 녹음이 어느 노트(=어느 회의)
것인지"를 명시하므로, 시간/제목 매칭 없이 **그 노트에 바로 회의록을 병합**할 수 있다.

흐름: 볼트의 오디오 파일 탐색 → 그 파일을 임베드한 노트 찾기 → STT·회의록 생성
      → 해당 노트에 '## 회의 기록' 병합 + frontmatter audio_processed 표시(재처리 방지).

통합 런처로 호출:
    python run_meeting.py vault-audio --vault "D:\\Claude\\QC"
"""

from __future__ import annotations

import os
import re
import glob
import tempfile
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict

from meeting_minutes_app.wiki_core.obsidian import parse_frontmatter, build_frontmatter, _as_str_list

AUDIO_EXTS = (".webm", ".m4a", ".mp3", ".wav", ".ogg", ".mp4", ".mpga", ".flac")


# ── 탐색 ──────────────────────────────────────────────────────
def find_audio_files(vault_root: str) -> List[str]:
    out = []
    for p in glob.glob(os.path.join(vault_root, "**", "*"), recursive=True):
        if os.path.isfile(p) and os.path.splitext(p)[1].lower() in AUDIO_EXTS:
            out.append(p)
    return out


def find_embedding_note(vault_root: str, audio_name: str,
                        notes_subdir: str = "00_Meetings") -> Optional[str]:
    """audio_name(파일명)을 임베드/링크한 노트(.md) 경로를 찾는다.
    회의 폴더(notes_subdir)를 우선 검색하고, 없으면 볼트 전체."""
    base = os.path.basename(audio_name)
    stem = os.path.splitext(base)[0]
    roots = [os.path.join(vault_root, notes_subdir), vault_root]
    seen = set()
    for root in roots:
        if not os.path.isdir(root):
            continue
        for p in glob.glob(os.path.join(root, "**", "*.md"), recursive=True):
            if p in seen or os.path.basename(p).startswith("_"):
                continue
            seen.add(p)
            try:
                txt = open(p, encoding="utf-8").read()
            except Exception:
                continue
            # ![[recording.webm]] / [[recording]] / 본문에 파일명 등장
            if base in txt or f"[[{stem}" in txt:
                return p
    return None


def _already_processed(meta: Dict, audio_name: str) -> bool:
    return os.path.basename(audio_name) in _as_str_list(meta.get("audio_processed"))


# ── 병합(파일시스템) ─────────────────────────────────────────
def merge_into_note_file(note_path: str, *, minutes: str, summary: str = "",
                         actions_md: str = "", attendees: Optional[List[str]] = None,
                         audio_name: str = "", doc_type: str = "meeting") -> bool:
    """노트 파일에 '## 회의 기록'을 추가 병합(원문 보존). 성공 시 True.
    계획(planned) 노트면 status→done, 참석자 합집합, audio_processed 표시."""
    try:
        content = open(note_path, encoding="utf-8").read()
    except Exception as e:
        print(f"    ⚠ 노트 읽기 실패 ({type(e).__name__}: {e}): {note_path}")
        return False
    meta, body = parse_frontmatter(content)

    merged_att: List[str] = []
    for x in _as_str_list(meta.get("attendees")) + list(attendees or []):
        if x and x not in merged_att:
            merged_att.append(x)
    proc = _as_str_list(meta.get("audio_processed"))
    if audio_name and os.path.basename(audio_name) not in proc:
        proc.append(os.path.basename(audio_name))

    now_iso = datetime.now().isoformat(timespec="seconds")
    meta = dict(meta)
    if str(meta.get("status", "")).strip().lower() == "planned":
        meta["status"] = "done"
    meta["attendees"] = merged_att
    meta["recorded"] = now_iso
    if proc:
        meta["audio_processed"] = proc
    if not meta.get("type"):
        meta["type"] = doc_type

    parts = [build_frontmatter(meta), ""]
    if body.lstrip().startswith("# "):
        parts.append(body.rstrip() + "\n")
    else:
        title = meta.get("title", "") or os.path.basename(note_path)[:-3]
        parts.append(f"# {title}\n")
        if body.strip():
            parts.append(body.rstrip() + "\n")
    src = f" · 🎙 {os.path.basename(audio_name)}" if audio_name else ""
    parts.append("\n---\n")
    parts.append(f"## 회의 기록 ({datetime.now().strftime('%Y-%m-%d %H:%M')}{src})\n")
    if summary.strip():
        parts.append("### 한눈에 보는 요약\n"); parts.append(summary.strip() + "\n")
    parts.append(minutes.strip() + "\n")
    if actions_md.strip():
        parts.append("### 액션 아이템\n"); parts.append(actions_md.strip() + "\n")
    try:
        open(note_path, "w", encoding="utf-8").write("\n".join(parts))
        return True
    except Exception as e:
        print(f"    ⚠ 노트 쓰기 실패 ({type(e).__name__}: {e}): {note_path}")
        return False


# ── STT + 회의록 생성 (공용 finalize 파이프라인 재사용) ────────
def transcribe_and_minutes(audio_path: str, doc_type: str = "meeting",
                           topic: str = "", session_dt: str = "",
                           title: str = "",
                           known_names: Optional[List[str]] = None,
                           llm=None,
                           memo: str = ""):
    """오디오 → (FinalizeResult, speakers). 회의록 생성은 batch/web/실시간과 동일한
    공용 파이프라인(meeting_pipeline.finalize.run_post_session)을 재사용한다.

    과거엔 이 함수가 STT→회의록→요약→액션만 손으로 호출해서, 계획 사전자료/Wiki
    컨텍스트 주입/사실검증/Wiki 업데이트 제안이 이 경로(vault-audio)에서만 빠져
    있었다 — finalize.py 도입 취지("과거엔 이 흐름이 4곳에 복사돼 있었다")와
    같은 문제가 이 5번째 경로에도 있었던 것.

    Obsidian 노트 병합은 이 함수가 하지 않는다 — 오디오가 노트에 임베드된
    링크로 이미 노트가 100% 확정돼 있어(다른 경로처럼 제목/날짜 fuzzy 매칭 +
    사용자 확인이 필요 없음) merge_into_note_file()이 직접 처리한다. 그래서
    FinalizeOptions(do_publish=False, plan_match=None)로 finalize의 계획-매칭/
    발행 스테이지는 건너뛴다. do_registries도 꺼두고, 병합 성공 후 실제 노트
    경로(source_note)로 process_vault()가 직접 갱신한다.
    """
    from meeting_minutes_app.meeting_pipeline import meeting_minutes as mm
    from meeting_minutes_app.meeting_pipeline import stt
    from meeting_minutes_app.meeting_pipeline import minutes_generation as mg
    from meeting_minutes_app.meeting_pipeline import finalize as fz
    if llm is None:
        llm = mm.LLMClient(preferred=mm._c("models.llm", "gpt") or "gpt")
    work = tempfile.mkdtemp(prefix="vault_audio_")
    try:
        audio2 = stt.prepare_audio(audio_path, work)
        segments = stt.run_stt(audio2, model=mm.DEFAULT_STT_MODEL,
                              language=mm._c("realtime.language", None),
                              speaker_names=known_names, work_dir=work)
        if not segments:
            return None
        # 화자 추론(참석자 힌트)
        try:
            if any(re.match(r'[Ss]peaker[\s_]?[A-Za-z0-9]', s.get("speaker", ""))
                   for s in segments):
                inferred = mg.infer_speaker_names(segments, llm, known_names=known_names)
                for seg in segments:
                    if seg.get("speaker") in (inferred or {}):
                        seg["speaker"] = inferred[seg["speaker"]]
        except Exception:
            pass
        speakers = sorted({s.get("speaker", "") for s in segments if s.get("speaker")})

        # wiki_context.json/wiki_proposal 저장 위치 — batch(pipeline.py)와 동일한
        # 저장소 규칙(repo-root output 폴더 하위, 세션별 서브폴더).
        artifacts_dir = None
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_title = "".join(c for c in (title or "vault_audio")
                                 if c.isalnum() or c in " _-").strip()[:50]
            repo_root = Path(__file__).resolve().parent.parent.parent
            out_root = repo_root / str(mm._c("output_dir", "output") or "output")
            artifacts_dir = out_root / f"vault_audio_{ts}_{safe_title}"
            artifacts_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print(f"    ⚠ wiki_context/proposal 저장 폴더 생성 실패 (무시): {e}")
            artifacts_dir = None

        res = fz.run_post_session(
            fz.SessionInputs(
                segments=segments, title=title, topic=topic, doc_type=doc_type,
                session_dt=session_dt, base_memo=memo or None, source="vault_audio",
                attendees=known_names or [],
                language=str(mm._c("realtime.language", "") or ""),
            ),
            fz.FinalizeOptions(
                llm=llm,
                plan_match=None,
                do_publish=False,
                do_registries=False,
                do_proposal=True,
                do_graph_sync=False,
                artifacts_dir=artifacts_dir,
                proposal_dir=artifacts_dir,
            ),
        )
        return res, speakers
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ── 이메일 발송(요약본) ──────────────────────────────────────
def _send_email_summary(title: str, summary: str, actions_md: str = "",
                        notify: str = "email",
                        attachment_paths: Optional[List[str]] = None,
                        minutes_md: str = "") -> bool:
    """요약본을 메일로 발송. config.json email 섹션(sender/password/recipient,
    선택적으로 smtp_host/smtp_port) 사용. Gmail·Naver·Outlook(office365) 자동 인식.
    minutes_md: 회의록 본문 (일시·참석자·안건 추출용).
    attachment_paths: 추가 첨부 파일 경로 목록 (예: Obsidian 노트 .md)."""
    try:
        from meeting_minutes_app.meeting_pipeline import meeting_minutes as mm
        from meeting_minutes_app.common.notifier import Notifier
    except ImportError:
        return False
    sender = mm._c("email.sender", "")
    pw = mm._c("email.password", "")
    if not (sender and pw):
        print("    📧 메일 설정 없음(config.email) → 메일 건너뜀")
        return False
    recipients = [r.strip() for r in mm._c("email.recipient", "").split(",") if r.strip()]
    n = Notifier()
    n.add_email(sender=sender, password=pw, recipients=recipients,
                smtp_host=mm._c("email.smtp_host", ""),
                smtp_port=int(mm._c("email.smtp_port", 0) or 0))
    if not n.has_channels:
        return False

    # 회의 기본 정보(일시·참석자·안건)를 minutes_md 에서 추출해 body 맨 앞에 추가
    header_lines: list = []
    if minutes_md:
        import re as _re
        in_agenda = False
        for ln in minutes_md.split("\n"):
            # 일시·참석자 라인
            if _re.match(r"^-\s+\*\*(일시|참석자)\*\*", ln):
                header_lines.append(ln.strip())
                in_agenda = False
            # 안건 라인 (헤더)
            elif _re.match(r"^-\s+\*\*안건\*\*", ln):
                header_lines.append(ln.strip())
                in_agenda = True
            # 안건 항목 (들여쓰기 번호 목록)
            elif in_agenda and _re.match(r"^\s+\d+\.", ln):
                header_lines.append(ln.strip())
            # 빈 줄 또는 다른 섹션에서 안건 종료
            elif in_agenda and (ln.startswith("#") or (ln.strip() and not ln.startswith(" "))):
                break

    body_parts = []
    if header_lines:
        body_parts.append("## 회의 정보\n" + "\n".join(header_lines))
    body_parts.append(summary.strip())
    if actions_md.strip():
        body_parts.append("## 액션 아이템\n" + actions_md.strip())
    body = "\n\n".join(p for p in body_parts if p)

    tf = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
    tf.write(body)
    tf.close()
    try:
        res = n.send(title=title, summary_path=tf.name,
                     files=list(attachment_paths or []))
        ok = any(r.get("success") for r in res)
        print("    📧 메일 발송 " + ("완료" if ok else "실패: "
              + "; ".join(r.get("error", "") for r in res if not r.get("success"))))
        return ok
    finally:
        os.unlink(tf.name)


# ── 오케스트레이션 ───────────────────────────────────────────
def process_vault(vault_root: str, notes_subdir: str = "00_Meetings",
                  only_audio: str = "", dry_run: bool = False,
                  notify: str = "") -> int:
    from meeting_minutes_app.meeting_pipeline import publish
    audios = [only_audio] if only_audio else find_audio_files(vault_root)
    done = 0
    for ap in audios:
        # 파일 하나 처리 중 오류(STT/LLM 호출 실패, 손상된 오디오 등)가 나도 나머지
        # 파일 처리는 계속돼야 한다 — audio_watcher.py/_handle_file()과 같은 격리
        # 패턴. 과거엔 여기서 예외가 나면 process_vault() 전체가 죽어 그 이후 볼트의
        # 모든 노트 처리가 중단됐다.
        try:
            note = find_embedding_note(vault_root, ap, notes_subdir)
            if not note:
                continue
            meta, _ = parse_frontmatter(open(note, encoding="utf-8").read())
            if _already_processed(meta, ap):
                continue
            rel = os.path.relpath(note, vault_root).replace("\\", "/")
            print(f"  🎙 {os.path.basename(ap)}  →  노트 '{meta.get('title') or rel}'")
            if dry_run:
                done += 1
                continue
            known = publish._clean_attendee_names(_as_str_list(meta.get("attendees")))
            sdt = f"{meta.get('date','')} {meta.get('time','')}".strip()
            note_title = meta.get("title") or os.path.basename(note)[:-3]
            result = transcribe_and_minutes(
                ap, doc_type=(meta.get("type") or "meeting"),
                topic=meta.get("topic", "") or note_title,
                session_dt=sdt, title=note_title, known_names=known or None,
            )
            if not result:
                print("    STT 실패 → 건너뜀")
                continue
            res, speakers = result
            if not res.minutes:
                print("    회의록 생성 실패 → 건너뜀")
                continue
            if merge_into_note_file(note, minutes=res.minutes, summary=res.summary,
                                    actions_md=res.actions_md, attendees=speakers,
                                    audio_name=ap, doc_type=(meta.get("type") or "meeting")):
                print(f"    ✅ 병합 완료 → {rel}")
                # 액션/결정 레지스트리 갱신 — finalize에서는 병합 전이라 실제 노트
                # 경로를 몰라 do_registries=False로 꺼뒀으므로, 병합 성공 후 실제
                # 노트 경로(rel)로 여기서 직접 갱신한다.
                try:
                    from meeting_minutes_app.wiki_core.wiki_knowledge import (
                        update_action_registry_from_actions,
                        update_decision_registry_from_minutes,
                        extract_decisions_from_minutes,
                    )
                    if res.actions_json:
                        update_action_registry_from_actions(
                            res.actions_json, source_meeting=note_title, source_note=rel)
                    decisions = extract_decisions_from_minutes(res.minutes)
                    if decisions:
                        update_decision_registry_from_minutes(
                            decisions, source_meeting=note_title, source_note=rel)
                except Exception as e:
                    print(f"    ⚠ registry 갱신 실패 (무시): {e}")
                if notify:
                    _send_email_summary(
                        title=note_title,
                        summary=res.summary, actions_md=res.actions_md, notify=notify,
                        minutes_md=res.minutes,
                        attachment_paths=[note] if os.path.isfile(note) else None)
                done += 1
            else:
                print("    병합 실패")
        except Exception as e:
            print(f"  ⚠ {os.path.basename(ap)} 처리 중 오류 ({type(e).__name__}: {e}) "
                  "→ 건너뛰고 다음 파일 계속")
            continue
    return done


def main():
    import argparse
    ap = argparse.ArgumentParser(description="옵시디언 임베드 오디오 처리")
    ap.add_argument("--vault", required=True)
    ap.add_argument("--notes-subdir", default="00_Meetings")
    ap.add_argument("--audio", default="", help="특정 오디오 파일만")
    ap.add_argument("--dry-run", action="store_true", help="대상만 표시")
    ap.add_argument("--notify", default="", help="처리 후 이메일 발송: email")
    a = ap.parse_args()
    n = process_vault(a.vault, a.notes_subdir, only_audio=a.audio,
                      dry_run=a.dry_run, notify=a.notify)
    print(f"\n[vault-audio] 처리 {n}건")


if __name__ == "__main__":
    main()
