import React from "react";
import NoteCard from "../../ui/NoteCard";
import { Button } from "../../ui/Button";

export interface LiveNote {
  filename: string;
  title: string;
  score: number;
  snippet?: string;
  heading?: string;
  sectionPath?: string;
  sourceType?: string;
  foundBy?: string;
  segmentText?: string;
  rankScore?: number;
}

/**
 * 녹음 중 인스펙터 — 관련 노트 탭 (PRD FR-REC-6).
 *
 * 녹음 중에는 **노트로 이동하지 않는다**(녹음 보호). 그래서 NoteCard 에 `onOpen` 을 주지
 * 않는다 — 링크처럼 보이는데 안 눌리는 것보다 처음부터 텍스트인 편이 낫다. 종료 후 상세의
 * "참조된 관련 노트"에서 열 수 있다는 사실을 아래에 적는다.
 *
 * [이번 회의 끔]은 표시만 끄는 것이 아니라 **서버 검색과 웹 보완 과금까지 멈춘다** —
 * 문구가 그 사실을 말해야 한다. 목록만 숨기면 아무도 안 보는 결과에 계속 돈을 쓴다.
 */
export default function RelatedNotesTab({
  notes, muted, searchOff, searchOffReason, canMute, onMute, showEvidence, onToggleEvidence,
}: {
  notes: LiveNote[];
  muted: boolean;
  /** 설정·게이트로 검색 자체가 꺼진 상태(사용자가 끈 것과 구분한다). */
  searchOff: boolean;
  searchOffReason?: string;
  canMute: boolean;
  onMute: () => void;
  showEvidence: boolean;
  onToggleEvidence: () => void;
}) {
  if (muted) {
    return (
      <p className="rounded-ctl border border-line bg-surface-2 px-2 py-1.5 text-xs text-ink-3"
        title="서버의 볼트 검색과 웹 보완을 멈춰 추가 비용이 발생하지 않습니다. 이미 찾은 노트는 회의록에 남습니다.">
        이번 회의 끔 — 검색·웹 보완을 멈췄습니다(새 녹음에서 다시 켜집니다)
      </p>
    );
  }

  return (
    <div className="space-y-2">
      {searchOff && (
        <p className="rounded-ctl border border-line bg-surface-2 px-2 py-1.5 text-xs text-ink-3">
          검색 꺼짐{searchOffReason ? ` — ${searchOffReason}` : ""}
        </p>
      )}

      {notes.length === 0 ? (
        <p className="text-sm text-ink-3">
          {searchOff ? "노트 검색이 꺼져 있습니다." : "발화와 관련된 내부 노트를 찾는 중…"}
        </p>
      ) : (
        <>
          {notes.map((n) => (
            <NoteCard
              key={n.filename || n.title}
              title={n.sectionPath || (n.heading ? `${n.title} › ${n.heading}` : n.title)}
              sourceType={n.sourceType}
              score={showEvidence ? n.score : undefined}
              foundBy={showEvidence ? n.foundBy : undefined}
              snippet={showEvidence ? n.snippet : undefined}
              segmentText={showEvidence ? n.segmentText : undefined}
              notePath={showEvidence ? n.filename : undefined}
            />
          ))}
          <div className="flex flex-wrap items-center gap-1.5">
            <Button size="sm" variant="ghost" onClick={onToggleEvidence}>
              {showEvidence ? "근거 접기" : "근거 보기"}
            </Button>
            {canMute && (
              <Button size="sm" variant="ghost" className="ml-auto" onClick={onMute}
                title="이번 회의에서만 끕니다 — 서버 검색과 웹 보완도 함께 멈춥니다">
                이번 회의 끔
              </Button>
            )}
          </div>
          <p className="text-xs text-ink-3">
            녹음 중에는 노트로 이동하지 않습니다(녹음 보호). 종료 후 회의 상세의
            <b> 참조된 관련 노트</b>에서 열 수 있습니다.
          </p>
        </>
      )}

      {/* 아직 결과가 없어도 끌 수 있어야 한다 — 검색은 이미 돌고 있고 그게 과금이다. */}
      {notes.length === 0 && canMute && (
        <Button size="sm" variant="ghost" onClick={onMute}
          title="이번 회의에서만 끕니다 — 서버 검색과 웹 보완도 함께 멈춥니다">
          이번 회의 끔
        </Button>
      )}
    </div>
  );
}
