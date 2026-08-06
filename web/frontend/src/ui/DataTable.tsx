import React from "react";
import type { StatusTone } from "./StatusPill";

/**
 * 고밀도 데이터 표 (PRD §5.4 · FR-LIB-1).
 *
 * 두 가지를 한 컴포넌트가 함께 책임진다:
 *  1) 넓은 화면 = 표. 좁은 화면(<md) = **카드 리스트**(§4.3). 카드를 별도 렌더 함수로
 *     받지 않고 **같은 columns 정의에서 파생**한다 — 두 벌로 두면 한쪽에만 열이 추가되는
 *     갈라짐이 생긴다.
 *  2) 행 진입은 **키보드로 도달 가능한 버튼**이다. `<tr onClick>` 만 두면 마우스 전용
 *     기능이 된다 — 첫 열의 제목이 그 버튼이 되고, 행 클릭은 마우스 편의로만 남긴다.
 *
 * 행 동작(휴지통·재시도 등)은 호버로 드러나되 **포커스가 가면 항상 보인다**
 * (`group-focus-within`). 보이지 않는 채로 탭 포커스를 받는 버튼은 접근성 결함이다.
 */

export interface Column<T> {
  key: string;
  header: React.ReactNode;
  cell: (row: T) => React.ReactNode;
  align?: "left" | "right";
  /** `<th style={{width}}>` 로 그대로 들어간다(예: "32%"). */
  width?: string;
  /** 카드(모바일)에서는 감춘다 — 표에서만 의미 있는 보조 열. */
  cardHidden?: boolean;
}

export interface DataTableProps<T> {
  rows: T[];
  columns: Column<T>[];
  rowKey: (row: T) => string;
  /** 표가 무엇의 목록인지(`<caption>`, 시각적으로는 숨긴다). */
  caption: string;
  /** 행 진입. 주면 첫 열이 버튼이 된다. */
  onRowClick?: (row: T) => void;
  /** 행 진입 버튼의 접근 가능한 이름(제목만으로 부족할 때). */
  rowLabel?: (row: T) => string;
  /** 좌측 컬러바 + 옅은 배경으로 상태를 알린다(녹음중·처리중·오류). */
  rowTone?: (row: T) => StatusTone | undefined;
  /** 행 동작 버튼들. */
  actions?: (row: T) => React.ReactNode;
}

const TONE_BAR: Record<string, string> = {
  rec: "shadow-[inset_3px_0_0_var(--color-rec)] bg-rec-bg",
  proc: "shadow-[inset_3px_0_0_var(--color-proc)] bg-proc-bg",
  err: "shadow-[inset_3px_0_0_var(--color-rec)] bg-rec-bg",
  ok: "", idle: "", warn: "", persona: "",
};

const CARD_BORDER: Record<string, string> = {
  rec: "border-l-4 border-l-rec",
  proc: "border-l-4 border-l-proc",
  err: "border-l-4 border-l-rec",
  ok: "", idle: "", warn: "", persona: "",
};

export function DataTable<T>({
  rows, columns, rowKey, caption, onRowClick, rowLabel, rowTone, actions,
}: DataTableProps<T>) {
  const first = columns[0];
  const rest = columns.slice(1);

  const enter = (row: T) => (e: React.SyntheticEvent) => {
    e.stopPropagation();
    onRowClick?.(row);
  };

  return (
    <>
      {/* ── 표 (md 이상) ─────────────────────────────────────────── */}
      <table className="hidden w-full border-separate border-spacing-0 overflow-hidden
        rounded-card border border-line bg-surface shadow-card md:table">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key} scope="col" style={c.width ? { width: c.width } : undefined}
                className={`border-b border-line bg-surface-2 px-3 py-2 text-2xs font-semibold
                  text-ink-3 ${c.align === "right" ? "text-right" : "text-left"}`}>
                {c.header}
              </th>
            ))}
            {actions && (
              <th scope="col" className="w-24 border-b border-line bg-surface-2 px-3 py-2 text-right
                text-2xs font-semibold text-ink-3">동작</th>
            )}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const tone = rowTone?.(row);
            return (
              <tr
                key={rowKey(row)}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
                className={`group ${onRowClick ? "cursor-pointer" : ""} hover:bg-hover`}
              >
                {columns.map((c, i) => (
                  <td key={c.key}
                    className={`border-b border-line px-3 py-2.5 text-sm last:border-b-0
                      ${c.align === "right" ? "text-right" : ""}
                      ${i === 0 && tone ? TONE_BAR[tone] || "" : ""}`}>
                    {i === 0 && onRowClick ? (
                      <button type="button" onClick={enter(row)}
                        aria-label={rowLabel?.(row)}
                        className="w-full text-left">
                        {c.cell(row)}
                      </button>
                    ) : c.cell(row)}
                  </td>
                ))}
                {actions && (
                  <td className="border-b border-line px-3 py-2.5 text-right last:border-b-0">
                    {/* 호버로 드러나되 포커스가 오면 항상 보인다 — 안 보이는 채로 탭
                        포커스를 받는 버튼을 만들지 않는다. */}
                    <span className="inline-flex gap-1 opacity-40 transition-opacity
                      group-hover:opacity-100 group-focus-within:opacity-100">
                      {actions(row)}
                    </span>
                  </td>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>

      {/* ── 카드 (md 미만) — 같은 columns 에서 파생 ────────────────── */}
      <ul className="flex flex-col gap-2 md:hidden">
        {rows.map((row) => {
          const tone = rowTone?.(row);
          return (
            <li key={rowKey(row)}
              className={`rounded-card border border-line bg-surface p-3 shadow-card
                ${tone ? CARD_BORDER[tone] || "" : ""}`}>
              <div className="flex items-start gap-2">
                <div className="min-w-0 flex-1">
                  {onRowClick ? (
                    <button type="button" onClick={enter(row)} aria-label={rowLabel?.(row)}
                      className="w-full text-left">
                      {first?.cell(row)}
                    </button>
                  ) : first?.cell(row)}
                </div>
                {actions && <span className="flex shrink-0 gap-1">{actions(row)}</span>}
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-2">
                {rest.filter((c) => !c.cardHidden).map((c) => (
                  <span key={c.key} className="inline-flex items-center gap-1">{c.cell(row)}</span>
                ))}
              </div>
            </li>
          );
        })}
      </ul>
    </>
  );
}

export default DataTable;
