import React, { useId, useState } from "react";
import { PanelRightClose, PanelRightOpen, ChevronUp } from "lucide-react";
import Tabs, { type TabItem } from "./Tabs";
import { IconButton } from "./Button";
import BottomSheet from "./BottomSheet";

/**
 * 우측 인스펙터 (PRD §3-4·§5.4·FR-REC-6·FR-DET-6).
 *
 * 관련 노트·근거·페르소나·맥락은 **항상 여기**로 온다. 본문 위에 겹치는 오버레이를 쓰지
 * 않는다 — 회의 중에 전사를 가리는 팝업은 그 자체로 방해다.
 *
 * 좁은 화면에서는 같은 내용이 하단 트리거 바 + 바텀시트가 된다(§4.3). **호출부는 한 번만
 * 쓴다** — 데스크톱/모바일용 트리 두 벌을 각각 그리면 한쪽에만 새 탭이 추가되는 갈라짐이
 * 생긴다(이 리포가 반복해 겪은 형태).
 *
 * 접기(rail)는 **카운트를 남긴다.** 숫자 없이 접으면 "확인 필요 1건"이 조용히 사라진다.
 */

export interface InspectorProps<K extends string> {
  tabs: TabItem<K>[];
  value: K;
  onChange: (key: K) => void;
  /** 탭 묶음이 무엇을 고르는지(`aria-label`). */
  label: string;
  children: React.ReactNode;
  /** 탭 아래 고정 영역(예산 상태칩·지금 점검/정리·이번 회의 끔). */
  footer?: React.ReactNode;
  /** 모바일 트리거 바의 왼쪽에 붙는 강조(예: 확인 필요 1). 없으면 탭 라벨만 쓴다. */
  mobileAlert?: React.ReactNode;
  className?: string;
}

export function Inspector<K extends string>({
  tabs, value, onChange, label, children, footer, mobileAlert, className = "",
}: InspectorProps<K>) {
  const id = useId();
  const [collapsed, setCollapsed] = useState(false);
  const [sheetOpen, setSheetOpen] = useState(false);
  const total = tabs.reduce((n, t) => n + (t.count || 0), 0);

  return (
    <>
      {/* ── 데스크톱: 우측 고정 패널 ─────────────────────────────── */}
      <aside
        className={`hidden md:flex ${collapsed ? "w-11" : "w-inspector"} min-h-0 shrink-0 flex-col
          overflow-hidden rounded-card border border-line bg-surface shadow-card
          transition-[width] duration-150 ${className}`}
      >
        {collapsed ? (
          <div className="flex flex-col items-center gap-2 py-2">
            <IconButton icon={PanelRightOpen} label="인스펙터 펼치기"
              size="sm" onClick={() => setCollapsed(false)} />
            {/* 접어도 개수는 남긴다 — 이게 없으면 미확인 항목이 조용히 사라진다. */}
            {tabs.filter((t) => !!t.count).map((t) => (
              <span key={t.key} title={typeof t.label === "string" ? t.label : undefined}
                className="num rounded-full bg-accent-weak px-1.5 py-0.5 text-xs font-semibold text-accent">
                {t.count}
              </span>
            ))}
          </div>
        ) : (
          <>
            <div className="flex shrink-0 items-center gap-1 border-b border-line pr-1">
              <Tabs id={id} items={tabs} value={value} onChange={onChange}
                label={label} variant="underline" className="min-w-0 flex-1 border-b-0" />
              <IconButton icon={PanelRightClose} label="인스펙터 접기"
                size="sm" onClick={() => setCollapsed(true)} />
            </div>
            <div
              role="tabpanel"
              id={`${id}-panel-${value}`}
              aria-labelledby={`${id}-tab-${value}`}
              tabIndex={0}
              className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-2.5"
            >
              {children}
            </div>
            {footer && (
              <div className="shrink-0 border-t border-line bg-surface-2 px-2.5 py-2">{footer}</div>
            )}
          </>
        )}
      </aside>

      {/* ── 모바일: 하단 트리거 바 → 시트 ────────────────────────── */}
      <button
        type="button"
        onClick={() => setSheetOpen(true)}
        aria-haspopup="dialog"
        aria-expanded={sheetOpen}
        className="fixed inset-x-3 bottom-20 z-20 flex items-center gap-2 rounded-full border
          border-line bg-surface px-4 py-2.5 text-sm font-semibold text-ink shadow-pop md:hidden"
      >
        {mobileAlert}
        <span className={mobileAlert ? "text-ink-2" : ""}>
          {tabs.map((t) => `${typeof t.label === "string" ? t.label : ""}${t.count ? ` ${t.count}` : ""}`)
            .filter(Boolean).join(" · ")}
        </span>
        <ChevronUp size={14} className="ml-auto shrink-0" aria-hidden="true" />
      </button>

      {sheetOpen && (
        <BottomSheet labelledBy={`${id}-sheet`} title={label} onClose={() => setSheetOpen(false)}
          footer={footer}>
          <Tabs id={`${id}-m`} items={tabs} value={value} onChange={onChange}
            label={label} variant="underline" className="mb-2" />
          <div role="tabpanel" id={`${id}-m-panel-${value}`} aria-labelledby={`${id}-m-tab-${value}`}>
            {children}
          </div>
        </BottomSheet>
      )}
    </>
  );
}

export default Inspector;
