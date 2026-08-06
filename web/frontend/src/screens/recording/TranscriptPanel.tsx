import React, { memo } from "react";
import { ChevronDown, Mic, MicOff, Monitor, Building2, Pause } from "lucide-react";
import { SegmentedControl } from "../../ui/Tabs";
import { Tag } from "../../ui/StatusPill";
import type { RealtimeSegment } from "../../lib/types";

/**
 * 실시간 전사 패널 (PRD FR-REC-4).
 *
 * 성능: 한 줄이 `React.memo` 컴포넌트다. 델타가 올 때마다 배열이 새로 만들어지므로 250줄
 * 전체가 재조정되던 것을, 실제로 바뀐 줄만 다시 그리게 한다(PRD §11 이 허용한 표현 계층
 * 최적화). 진입 효과는 CSS 로 — motion 요소 250개는 그 자체가 비용이다.
 *
 * 접근성: `role="log" aria-live="polite"` — 전사는 흘러가는 기록이라 assertive 로 읽으면
 * 스크린리더 사용자가 아무것도 못 한다.
 *
 * 표시 토글(원문+번역/원문/번역)은 신규다(FR-REC-4). 번역 회의에서 2열은 정보가 많지만
 * 좁은 화면·집중해서 읽을 때는 한쪽만 보고 싶다 — 서버 동작과 무관한 표시 상태다.
 */

export type ViewMode = "both" | "source" | "translated";

export interface TranscriptPanelProps {
  segments: RealtimeSegment[];
  /** 화면에 그리지 않고 앞에서 잘라낸 줄 수. 0 이면 표시하지 않는다. */
  hiddenCount: number;
  totalCount: number;
  translate: boolean;
  viewMode: ViewMode;
  onViewMode: (m: ViewMode) => void;
  /** 발화 점프로 강조된 줄의 start(초). */
  flashStart: number | null;
  hasSuspectMark: boolean;
  status: string;
  isPaused: boolean;
  soundDetected: boolean;
  systemAudioOn: boolean;
  roomMic: boolean;
  followLatest: boolean;
  unseenCount: number;
  onJumpLatest: () => void;
  onScroll: () => void;
  panelRef: React.RefObject<HTMLDivElement | null>;
}

export default function TranscriptPanel(p: TranscriptPanelProps) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* 상태 줄 — 전사를 보는 바로 그 자리에서 소리가 들어가는지 알 수 있어야 한다. */}
      <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
        <span className="num text-xs text-ink-3">
          {p.totalCount > 0 && `전사 ${p.totalCount}줄`}
          {p.hiddenCount > 0 && ` (앞 ${p.hiddenCount}줄은 종료 후 전사 문서에서)`}
        </span>
        <div className="flex-1" />
        {p.translate && (
          <SegmentedControl id="transcript-view" label="전사 표시" value={p.viewMode}
            onChange={p.onViewMode}
            items={[
              { key: "both" as const, label: "원문+번역" },
              { key: "source" as const, label: "원문" },
              { key: "translated" as const, label: "번역" },
            ]} />
        )}
        {p.status === "recording" && (
          <span className="flex items-center gap-1.5">
            {p.isPaused ? (
              <Tag><Pause size={11} aria-hidden="true" /> 일시정지</Tag>
            ) : p.soundDetected ? (
              <span className="inline-flex items-center gap-1 rounded-full bg-ok-bg px-2 py-0.5
                text-xs font-semibold text-ok">
                <Mic size={11} aria-hidden="true" /> 소리 감지 중
              </span>
            ) : (
              <Tag><MicOff size={11} aria-hidden="true" /> 무음</Tag>
            )}
            {/* 고른 값이 아니라 **성사된 상태**를 표시한다. */}
            {p.systemAudioOn && (
              <Tag title="이 PC에서 나는 소리(온라인 회의 상대방 목소리)가 함께 녹음되고 있습니다.">
                <Monitor size={11} aria-hidden="true" /> PC 소리 포함
              </Tag>
            )}
            {p.roomMic && (
              <Tag title="에코 취소를 끄고 마이크 감도를 올린 상태입니다(멀리서 나는 소리용).">
                <Building2 size={11} aria-hidden="true" /> 회의실 마이크
              </Tag>
            )}
          </span>
        )}
      </div>

      <div
        ref={p.panelRef}
        onScroll={p.onScroll}
        role="log"
        aria-live="polite"
        aria-label="실시간 전사"
        className="relative min-h-0 flex-1 overflow-y-auto overscroll-contain rounded-card
          border border-line bg-surface p-3 shadow-card"
      >
        {p.segments.length === 0 ? (
          <p className="py-10 text-center text-sm text-ink-3">
            {p.status === "generating" ? "회의 문서를 만드는 중…"
              : p.isPaused ? "일시정지됨"
              : p.soundDetected ? "소리 감지 중 — 전사를 기다리는 중…"
              : "오디오를 듣는 중… (아직 소리가 감지되지 않았어요)"}
          </p>
        ) : (
          <div className="flex flex-col">
            {p.segments.map((seg) => (
              <Line key={seg.id ?? `${seg.start.toFixed(2)}-${seg.text.slice(0, 16)}`}
                seg={seg} translate={p.translate} viewMode={p.viewMode}
                flash={p.flashStart !== null && seg.start === p.flashStart} />
            ))}
          </div>
        )}

        {p.hasSuspectMark && (
          <p className="mt-1.5 border-t border-line pt-1.5 text-xs text-ink-3">
            <b>[불명]</b> 표시는 음성인식이 잘못 만들어낸 구간입니다(주로 무음·잡음 구간).
            회의록에는 반영되지 않습니다.
          </p>
        )}

        {/* 위를 읽는 동안 자동 스크롤이 멈추므로 돌아갈 방법을 항상 준다 */}
        {!p.followLatest && p.segments.length > 0 && (
          <button type="button" onClick={p.onJumpLatest}
            className="sticky bottom-0 mx-auto flex items-center gap-1 rounded-full bg-accent-solid
              px-3 py-1.5 text-xs font-semibold text-on-accent shadow-pop">
            <ChevronDown size={12} aria-hidden="true" />
            최신 전사로{p.unseenCount > 0 ? ` (새 ${p.unseenCount}줄)` : ""}
          </button>
        )}
      </div>
    </div>
  );
}

/**
 * 전사 한 줄. `memo` 로 감싼 이유는 위 주석 참조 — props 가 같으면 다시 그리지 않는다.
 * `provisional`(빠른 패스 임시 조각)은 흐리게, 보정되면 선명해진다.
 */
const Line = memo(function Line({
  seg, translate, viewMode, flash,
}: {
  seg: RealtimeSegment; translate: boolean; viewMode: ViewMode; flash: boolean;
}) {
  const streaming = seg.start === -1;
  const showSource = viewMode !== "translated";
  const showTranslated = translate && viewMode !== "source";
  const twoCol = showSource && showTranslated;

  return (
    <div
      data-seg-start={seg.start}
      className={`border-b border-surface-2 py-1.5 last:border-b-0 ${
        flash ? "-mx-1 rounded-ctl px-1 ring-2 ring-accent" : ""}`}
    >
      {seg.speaker && (
        <span className="block text-xs font-semibold text-accent">{seg.speaker}</span>
      )}
      <div className={twoCol ? "grid gap-0.5 md:grid-cols-2 md:gap-3" : ""}>
        {showSource && (
          <p className={`ko-text min-w-0 text-base leading-snug ${
            streaming ? "italic text-ink-3" : seg.provisional ? "text-ink-3" : "text-ink"}`}>
            {seg.text}{streaming && " …"}
          </p>
        )}
        {showTranslated && (
          <p className={`ko-text min-w-0 text-base leading-snug ${
            twoCol ? "md:border-l-2 md:border-line md:pl-3" : ""} ${
            seg.translatedText ? "text-ink" : "italic text-ink-3"}`}>
            {seg.translatedText || "번역 중…"}
          </p>
        )}
      </div>
    </div>
  );
});
