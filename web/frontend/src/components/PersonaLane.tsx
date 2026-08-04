import React from "react";
import { Users } from "lucide-react";
import PersonaCard from "./PersonaCard";
import type { Facilitation, FacilitationStatus } from "../lib/types";

/**
 * 페르소나 레인 — 관련 노트 스트립 바로 아래 한 줄 (PRD §19.2).
 *
 * 관련 노트 바(emerald)와 **같은 시각 언어**를 쓰고 색만 다르게 한다(slate) —
 * 이미 작동하는 채널 위에 카드 한 장을 더 얹는 구조라 신규 UI 위험이 거의 없다.
 *
 * 규칙:
 *  - 개입이 0건이고 알릴 상태도 없으면 **렌더하지 않는다**(빈 바가 공간을 먹지 않게).
 *  - 소리·팝업·포커스 이동 없음. 참견도 4·5(알림음·음성)는 M3 이고 코드에 없다.
 *  - 끄는 버튼은 **항상 보인다**(§19.4 — 업계 교훈: 끌 수 없는 자동 개입은 반발을 산다).
 *  - 한도·예산으로 멈췄으면 그 사유를 같은 줄에 회색 칩으로 남긴다(§19.5).
 */
export default function PersonaLane({
  items, status, pending, muted, briefOn, briefBusy,
  onCheckNow, onBriefNow, onMute, onJump, onAck, onDismiss,
}: {
  items: Facilitation[];
  status?: FacilitationStatus | null;
  pending?: number;
  muted?: boolean;
  /** 중간 요약이 켜져 있는지(서버 실효값) — [지금 정리] 버튼의 표시 조건 */
  briefOn?: boolean;
  briefBusy?: boolean;
  onCheckNow?: () => void;
  onBriefNow?: () => void;
  onMute?: () => void;
  onJump?: (span: { t0: number; t1: number }) => void;
  onAck?: (id: string) => void;
  onDismiss?: (id: string) => void;
}) {
  const notice = status && status.kind !== "pending" ? status.message : "";
  // [지금 정리]가 가능하면 레인을 띄운다 — 버튼 자체가 내용이다. 그 외에는 낼 것이
  // 없으면 렌더하지 않는다(빈 바가 전사 영역을 잠식하지 않게).
  if (!items.length && !notice && !pending && !muted && !(briefOn && onBriefNow))
    return null;

  return (
    <div className="bg-slate-50 border-b border-slate-200 shrink-0">
      <div className="px-4 md:px-8 py-1.5 flex items-start gap-2 overflow-x-auto">
        <span className="flex items-center gap-1.5 text-[11px] font-black uppercase tracking-widest text-slate-500 shrink-0 mt-1">
          <Users className="w-3.5 h-3.5" /> 페르소나
        </span>

        {muted ? (
          /* 서버가 개입·중간 요약 생성을 멈춘 상태다(표시만 끄는 것이 아니다) —
             문구도 그렇게 적는다. 다르게 적으면 껐다고 믿은 뒤에도 비용이 남는 줄
             알거나, 반대로 판정 기록까지 멈춘 줄 안다. */
          <span
            className="shrink-0 mt-1 text-[11px] bg-white border border-zinc-200 text-zinc-500 px-2 py-0.5 rounded-full"
            title="개입 카드와 중간 요약 생성을 멈춰 추가 비용이 발생하지 않습니다. 판정 기록(오탐률 실측용)은 계속됩니다. 다음 녹음에서 다시 켜집니다."
          >
            이번 회의 끔 — 개입 생성을 멈췄습니다
          </span>
        ) : (
          <>
            {items.map((it) => (
              <PersonaCard
                key={it.id}
                item={it}
                onJump={onJump}
                onAck={onAck}
                onDismiss={onDismiss}
              />
            ))}

            {notice && (
              <span
                className="shrink-0 mt-1 text-[11px] bg-white border border-zinc-200 text-zinc-500 px-2 py-0.5 rounded-full whitespace-nowrap max-w-[60vw] truncate"
                title={notice}
              >
                {notice}
              </span>
            )}

            {!!pending && onCheckNow && (
              <button
                type="button"
                onClick={onCheckNow}
                className="shrink-0 mt-1 text-[11px] font-semibold bg-white border border-slate-300 text-slate-700 px-2 py-0.5 rounded-full hover:border-slate-500 transition-colors whitespace-nowrap"
                title="모아둔 점검 항목을 지금 보여줍니다(추가 비용 없음)"
              >
                지금 점검 {pending}
              </button>
            )}
          </>
        )}

        <div className="flex-1" />
        {/* [지금 정리] — 주기를 기다리지 않고 중간 요약 1회. [지금 점검]과 달리
            **새 비용이 발생**하므로 툴팁에 그 사실을 적는다. */}
        {briefOn && onBriefNow && !muted && (
          <button
            type="button"
            onClick={onBriefNow}
            disabled={briefBusy}
            className="shrink-0 mt-1 text-[11px] font-semibold bg-white border border-slate-300 text-slate-700 px-2 py-0.5 rounded-full hover:border-slate-500 transition-colors whitespace-nowrap disabled:opacity-50"
            title="지금까지의 논점·결정·액션을 한 번 정리합니다(요약 LLM 호출 1회 = 소액 비용 발생)"
          >
            {briefBusy ? "정리 중…" : "지금 정리"}
          </button>
        )}
        {onMute && !muted && (
          <button
            type="button"
            onClick={onMute}
            className="shrink-0 mt-1 text-[11px] text-zinc-500 hover:text-zinc-700 font-medium whitespace-nowrap"
            title="이번 회의의 개입 카드·중간 요약 생성을 멈춥니다 — 추가 비용이 발생하지 않습니다(설정은 그대로, 다음 녹음에서 다시 켜짐)"
          >
            이번 회의 끔
          </button>
        )}
      </div>
    </div>
  );
}
