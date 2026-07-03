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
from typing import Optional, List, Dict, Tuple

from obsidian import parse_frontmatter, build_frontmatter, _as_str_list

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
    except Exception:
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
    except Exception:
        return False


# ── STT + 회의록 생성 ────────────────────────────────────────
def transcribe_and_minutes(audio_path: str, doc_type: str = "meeting",
                           topic: str = "", session_dt: str = "",
                           title: str = "",
                           known_names: Optional[List[str]] = None,
                           llm=None,
                           return_segments: bool = False,
                           memo: str = ""):
    """오디오 → (minutes, summary, actions_md, speakers). meeting_minutes 재사용.
    memo: generate_minutes에 전달할 추가 컨텍스트 (web_research 결과 등).
    """
    import meeting_minutes as mm
    if llm is None:
        llm = mm.LLMClient(preferred=mm._c("models.llm", "gpt") or "gpt")
    work = tempfile.mkdtemp(prefix="vault_audio_")
    try:
        audio2 = mm.prepare_audio(audio_path, work)
        segments = mm.run_stt(audio2, model=mm.DEFAULT_STT_MODEL,
                              language=mm._c("realtime.language", None),
                              speaker_names=known_names, work_dir=work)
        if not segments:
            return "", "", "", []
        # 화자 추론(참석자 힌트)
        try:
            if any(re.match(r'[Ss]peaker[\s_]?[A-Za-z0-9]', s.get("speaker", ""))
                   for s in segments):
                inferred = mm.infer_speaker_names(segments, llm, known_names=known_names)
                for seg in segments:
                    if seg.get("speaker") in (inferred or {}):
                        seg["speaker"] = inferred[seg["speaker"]]
        except Exception:
            pass
        minutes = mm.generate_minutes(segments, llm, doc_type, memo or None, None,
                                      topic=topic, session_dt=session_dt, title=title)
        summary = mm.generate_summary(minutes, llm, doc_type, topic=topic,
                                      session_dt=session_dt)
        actions_md = ""
        try:
            aj = mm.extract_action_items(minutes, llm, doc_type)
            if aj:
                actions_md = mm.format_actions_md(aj)
        except Exception:
            pass
        speakers = sorted({s.get("speaker", "") for s in segments if s.get("speaker")})
        if return_segments:
            return minutes, summary, actions_md, speakers, segments
        return minutes, summary, actions_md, speakers
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
        import meeting_minutes as mm
        from notifier import Notifier
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
    import meeting_minutes as mm
    audios = [only_audio] if only_audio else find_audio_files(vault_root)
    done = 0
    for ap in audios:
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
        known = mm._clean_attendee_names(_as_str_list(meta.get("attendees")))
        sdt = f"{meta.get('date','')} {meta.get('time','')}".strip()
        minutes, summary, actions_md, speakers = transcribe_and_minutes(
            ap, doc_type=(meta.get("type") or "meeting"),
            topic=meta.get("topic", "") or meta.get("title", ""),
            session_dt=sdt, known_names=known or None,
        )
        if not minutes:
            print("    STT/회의록 생성 실패 → 건너뜀")
            continue
        if merge_into_note_file(note, minutes=minutes, summary=summary,
                                actions_md=actions_md, attendees=speakers,
                                audio_name=ap, doc_type=(meta.get("type") or "meeting")):
            print(f"    ✅ 병합 완료 → {rel}")
            if notify:
                _send_email_summary(
                    title=(meta.get("title") or os.path.basename(note)[:-3]),
                    summary=summary, actions_md=actions_md, notify=notify,
                    attachment_paths=[note] if os.path.isfile(note) else None)
            done += 1
        else:
            print("    병합 실패")
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
