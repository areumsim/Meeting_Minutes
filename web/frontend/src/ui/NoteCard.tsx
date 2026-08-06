import React from "react";
import { sourceStyle } from "../lib/entities";

/**
 * 관련 노트 1장 (PRD §5.4 · FR-REC-6 · FR-DET-6).
 *
 * 용어는 "관련 노트" 하나로 통일한다 — 상세 화면만 "참조된 관련 노트"를 쓴다(§10 C).
 * 종전에는 녹음 화면(칩 + 펼침 목록)과 상세 화면(카드 목록)이 같은 데이터를 서로 다른
 * 모양으로 그렸고, 점수 표기도 `score 0.81` / `관련도 0.81` 로 갈렸다.
 *
 * 규칙:
 *  - 출처(노트/논문/웹/지난 회의)는 아이콘 **+ 글자**로 낸다(PRD §5.3 이모지 금지).
 *  - 제목은 이동 가능할 때만 버튼이 된다. 녹음 중에는 이동하지 않는다(녹음 보호) —
 *    그때는 `onOpen` 을 주지 않으면 정적 텍스트가 된다.
 *  - 점수는 `num`(tabular-nums) 로 찍어 목록에서 자릿수가 흔들리지 않게 한다.
 */
export function NoteCard({
  title, sourceType, score, hits, foundBy, snippet, segmentText, notePath, onOpen, actions,
}: {
  title: string;
  sourceType?: string;
  score?: number;
  /** 이 회의에서 몇 번 참조됐는지(2회 이상일 때만 표시). */
  hits?: number;
  /** section | web | note — 무엇으로 찾았는지. */
  foundBy?: string;
  snippet?: string;
  /** 이 노트를 띄운 발화. */
  segmentText?: string;
  notePath?: string;
  onOpen?: () => void;
  actions?: React.ReactNode;
}) {
  const src = sourceStyle(sourceType);
  const Icon = src.icon;
  const foundLabel = foundBy === "section" ? "섹션 일치"
    : foundBy === "web" ? "웹"
    : foundBy === "note" ? "노트 일치" : "";

  const heading = (
    <span className="flex min-w-0 items-baseline gap-1.5">
      <Icon size={12} className="shrink-0 translate-y-0.5 text-ink-3" aria-hidden="true" />
      <span className="sr-only">{src.label}: </span>
      <span className="truncate font-semibold text-ink">{title}</span>
    </span>
  );

  return (
    <div className="rounded-ctl border border-line p-2">
      <div className="flex items-baseline gap-2">
        {onOpen ? (
          <button type="button" onClick={onOpen} title="지식 그래프에서 이 노트 보기"
            className="min-w-0 flex-1 text-left hover:text-accent">
            {heading}
          </button>
        ) : (
          <span className="min-w-0 flex-1">{heading}</span>
        )}
        <span className="num shrink-0 text-xs text-ink-3">
          {typeof score === "number" && score > 0 ? score.toFixed(2) : src.label}
        </span>
      </div>

      {(foundLabel || (hits ?? 1) > 1) && (
        <p className="mt-0.5 text-xs text-ink-3">
          {[(hits ?? 1) > 1 ? `${hits}회 참조` : "", foundLabel].filter(Boolean).join(" · ")}
        </p>
      )}
      {snippet && <p className="ko-text mt-1 text-sm text-ink-2">{snippet}</p>}
      {segmentText && (
        <p className="ko-text mt-1 truncate text-xs italic text-ink-3">발화: {segmentText}</p>
      )}
      {notePath && <p className="num mt-1 truncate text-xs text-ink-3">{notePath}</p>}
      {actions && <div className="mt-1.5 flex flex-wrap gap-1.5">{actions}</div>}
    </div>
  );
}

export default NoteCard;
