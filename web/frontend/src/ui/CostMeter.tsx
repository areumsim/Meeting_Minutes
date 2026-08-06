import React, { useId, useState } from "react";
import { ChevronDown } from "lucide-react";
import { fmtUsd, type CostItem } from "../lib/costEstimate";
import { kindLabel } from "../lib/costKinds";

/**
 * 비용 표시 (PRD §9 · FR-REC-1 · FR-DET-2 · FR-LIB-5).
 *
 * 빌링 투명성이 이 앱의 규칙이라 금액은 **항상 노출 가능**해야 하고, 총액과 내역은
 * 반드시 같은 계산에서 나와야 한다(`lib/costEstimate.ts`). 여기서는 그걸 그리기만 한다 —
 * 이 파일은 단가를 알지 못한다.
 *
 * 내역을 툴팁(title)에만 두지 않는다: 터치(iOS)와 키보드에서는 볼 수 없다. 그래서
 * disclosure 버튼이다.
 */

export function CostMeter({
  total, items, label = "이번 세션", note, defaultOpen = false, compact,
}: {
  total: number;
  items: CostItem[];
  label?: string;
  /** 총액 옆 한 줄 보조(예: "대략치"). */
  note?: string;
  defaultOpen?: boolean;
  /** 헤더처럼 좁은 자리 — 라벨을 숨기고 금액만 낸다. */
  compact?: boolean;
}) {
  const id = useId();
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="min-w-0">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={id}
        className="inline-flex items-center gap-1 rounded-ctl px-1.5 py-0.5 text-sm text-ink-2 hover:bg-hover"
        title="누르면 항목별 내역이 열립니다"
      >
        {!compact && <span className="text-ink-3">{label}</span>}
        <b className="num text-ink">{fmtUsd(total)}</b>
        {note && <span className="text-xs text-ink-3">· {note}</span>}
        <ChevronDown size={13} aria-hidden="true"
          className={`transition-transform ${open ? "" : "-rotate-90"}`} />
      </button>
      {open && <div id={id} className="mt-1"><CostBreakdown items={items} total={total} /></div>}
    </div>
  );
}

/**
 * 항목별 내역. **화면이 항목 종류를 하드코딩하지 않는다** — 서버가 준 목록을 그대로 돈다
 * (PRD §9). 종전에 kind 를 손으로 적던 곳들은 새 과금 경로(web_research)가 생겼을 때
 * 조용히 빠뜨렸다.
 */
export function CostBreakdown({
  items, total, disclaimer = "대략치 — 실제 청구액보다 클 수 있습니다.",
}: {
  items: CostItem[];
  total: number;
  disclaimer?: string;
}) {
  return (
    <div className="rounded-ctl border border-line bg-surface-2 px-2.5 py-1.5 text-xs text-ink-2">
      <ul className="space-y-0.5">
        {items.map((it) => (
          <li key={it.key} className="flex items-baseline justify-between gap-4">
            <span>
              {it.label}
              {it.ratePerMin != null && (
                <span className="num ml-1 text-ink-3">{fmtUsd(it.ratePerMin, 4)}/분</span>
              )}
              {/* 추정과 실측을 구분해 적는다 — 개입 카드는 서버가 실제로 계산해 보낸 값이다. */}
              {it.actual && <span className="ml-1 text-ok">실측</span>}
            </span>
            <span className="num shrink-0">{fmtUsd(it.usd, 4)}</span>
          </li>
        ))}
      </ul>
      <div className="mt-1 flex items-baseline justify-between gap-4 border-t border-line pt-1 font-semibold text-ink">
        <span>합계</span>
        <span className="num">{fmtUsd(total)}</span>
      </div>
      <p className="mt-1 text-ink-3">{disclaimer}</p>
    </div>
  );
}

/**
 * 완료된 세션의 비용 내역 — 서버 `/api/sessions/{id}/cost` 응답을 그대로 그린다.
 *
 * `actual_kinds` 는 **서버 주도** 목록이다. 여기 이름을 적어 두면 새 과금이 생겼을 때
 * 화면이 조용히 빠뜨린다(전례 있음) → 목록을 돌고 라벨만 `lib/costKinds` 에서 찾는다.
 */
export function sessionCostItems(cost: Record<string, unknown>): CostItem[] {
  const num = (v: unknown) => (typeof v === "number" ? v : Number(v) || 0);
  const items: CostItem[] = [
    { key: "stt", label: `음성 인식${cost.stt_model ? ` — ${cost.stt_model}` : ""}`, usd: num(cost.stt) },
  ];
  if (num(cost.stt_revise) > 0) {
    items.push({ key: "stt_revise", label: "2단계 보정", usd: num(cost.stt_revise) });
  }
  if (num(cost.translate) > 0) items.push({ key: "translate", label: "번역", usd: num(cost.translate) });
  items.push({ key: "minutes", label: "회의록 생성", usd: num(cost.minutes) });
  for (const k of (cost.actual_kinds as string[] | undefined) ?? []) {
    items.push({ key: k, label: kindLabel(k), usd: num(cost[k]), actual: true });
  }
  return items;
}

export default CostMeter;
