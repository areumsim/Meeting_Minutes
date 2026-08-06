import React from "react";
import NoteCard from "../../ui/NoteCard";
import { formatTime } from "../../lib/format";
import type { RelatedNoteRow, RelatedNoteCross } from "../../lib/api";

/**
 * 상세 화면 인스펙터 — "참조된 관련 노트" (PRD FR-DET-6).
 *
 * 회의 중 실시간 검색이 찾은 내부 자료를 근거와 함께 다시 열람한다. 제목을 누르면 지식
 * 그래프로 이동한다(녹음 중에는 이동하지 않지만 여기서는 가도 된다 — 진행 중인 회의가 없다).
 *
 * "최근 자주 참조"는 **교차 회의 집계**다: 이 노트가 최근 회의들에서 몇 번 나왔는지.
 * 한 회의만 보면 안 보이는 것을 알려 주는 유일한 자리라 접기 뒤에 숨기지 않는다.
 */
export default function RelatedNotesPanel({
  notes, cross, onOpenNote,
}: {
  notes: RelatedNoteRow[];
  cross: RelatedNoteCross[];
  onOpenNote?: (title: string) => void;
}) {
  if (notes.length === 0 && cross.length === 0) {
    return (
      <p className="text-sm text-ink-3">
        이 회의에서 자동으로 찾은 내부 자료가 없습니다. 노트 폴더를 연결하고 실시간 노트
        검색을 켜면 회의 중 발화와 관련된 노트가 여기 쌓입니다.
      </p>
    );
  }

  return (
    <div className="space-y-2">
      {notes.length > 0 && (
        <>
          <p className="text-xs text-ink-3">
            회의 중 발화와 관련해 자동으로 찾은 내부 자료입니다(원본 노트는 수정되지 않습니다).
          </p>
          {notes.map((n) => (
            <NoteCard
              key={n.note_path || n.title}
              title={n.section_path || n.title}
              sourceType={n.source_type}
              score={n.score}
              hits={n.hits}
              foundBy={n.found_by}
              snippet={n.snippet}
              segmentText={n.segment_text
                ? `${n.elapsed_sec ? `(${formatTime(n.elapsed_sec)}) ` : ""}${n.segment_text}`
                : undefined}
              notePath={n.note_path}
              onOpen={onOpenNote ? () => onOpenNote(n.title) : undefined}
            />
          ))}
        </>
      )}

      {cross.length > 0 && (
        <div className="border-t border-line pt-2">
          <h4 className="mb-1.5 text-xs font-semibold text-ink-3">최근 회의에서 자주 참조된 노트</h4>
          <ul className="flex flex-wrap gap-1.5">
            {cross.map((c) => (
              <li key={c.note_path || c.title}>
                <button
                  type="button"
                  onClick={() => onOpenNote?.(c.title)}
                  title={`${c.note_path}${c.last_date ? ` · 최근 ${c.last_date.slice(0, 10)}` : ""}`}
                  className="rounded-full border border-line-strong bg-surface px-2 py-0.5 text-xs
                    text-ink-2 hover:bg-hover"
                >
                  {c.title} <span className="num text-ink-3">· 회의 {c.session_count}건</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
