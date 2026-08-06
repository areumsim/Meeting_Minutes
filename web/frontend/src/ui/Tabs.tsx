import React, { useId, useRef } from "react";

/**
 * 탭 · 세그먼트 컨트롤 (PRD §5.4·§5.5).
 *
 * 둘은 시각만 다르고 **의미는 같다**(한 번에 하나를 고르는 뷰 전환) — 그래서 한 구현에
 * `variant` 만 둔다. 종전에는 상세 문서 탭·지식 세그먼트·인스펙터 탭이 각각 `<button>`
 * 나열이라 스크린리더가 "탭 3개 중 2번째"를 읽지 못했고 방향키도 안 먹었다.
 *
 * 계약:
 *  - `role="tablist"` + `role="tab"` + `aria-selected` + `aria-controls`,
 *    본문은 `role="tabpanel"` + `aria-labelledby`.
 *  - **로빙 tabindex** — Tab 키 한 번이면 탭 묶음을 지나간다(탭마다 멈추지 않는다).
 *    묶음 안에서는 ←/→(세로면 ↑/↓)·Home·End 로 옮긴다.
 *  - 선택은 포커스를 따라간다(automatic activation). 이 앱의 패널은 전부 가볍고,
 *    수동 활성화는 Enter 를 한 번 더 요구해 오히려 도달성을 떨어뜨린다.
 *  - 히트 타깃 ≥24px(sm 28px / md 32px).
 */

export interface TabItem<K extends string = string> {
  key: K;
  label: React.ReactNode;
  /** 옆에 붙는 개수(관련 노트 3 · 진행 도우미 2). 0 이면 표시하지 않는다. */
  count?: number;
  disabled?: boolean;
  title?: string;
}

type Variant = "underline" | "pill" | "segment";

const LIST: Record<Variant, string> = {
  underline: "flex items-stretch border-b border-line overflow-x-auto",
  pill: "flex items-center gap-1 overflow-x-auto",
  segment: "inline-flex items-center gap-0.5 rounded-ctl bg-surface-2 p-0.5",
};

const TAB: Record<Variant, (on: boolean) => string> = {
  underline: (on) =>
    "shrink-0 border-b-2 px-3 py-2 text-sm font-semibold transition-colors " +
    (on ? "border-accent text-accent" : "border-transparent text-ink-3 hover:text-ink"),
  pill: (on) =>
    "shrink-0 rounded-ctl px-3 py-1.5 text-sm font-medium transition-colors " +
    (on ? "bg-accent-weak font-semibold text-accent" : "text-ink-3 hover:bg-hover hover:text-ink"),
  segment: (on) =>
    "shrink-0 rounded-[4px] px-3.5 py-1.5 text-sm font-semibold transition-colors " +
    (on ? "bg-surface text-accent shadow-flat" : "text-ink-2 hover:text-ink"),
};

export function Tabs<K extends string>({
  items, value, onChange, label, variant = "underline", id, className = "",
}: {
  items: TabItem<K>[];
  value: K;
  onChange: (key: K) => void;
  /** 이 탭 묶음이 무엇을 고르는지(`aria-label`). "문서 종류" 처럼 적는다. */
  label: string;
  variant?: Variant;
  id?: string;
  className?: string;
}) {
  const auto = useId();
  const base = id || auto;
  const listRef = useRef<HTMLDivElement>(null);

  const move = (delta: number, e: React.KeyboardEvent) => {
    const enabled = items.filter((t) => !t.disabled);
    if (enabled.length === 0) return;
    e.preventDefault();
    const cur = enabled.findIndex((t) => t.key === value);
    // 끝에서 반대편으로 감는다 — 마지막 탭에서 → 를 눌렀을 때 아무 일도 안 일어나면
    // 사용자는 키보드 지원이 없다고 판단한다.
    const next = enabled[(cur + delta + enabled.length) % enabled.length];
    onChange(next.key);
    listRef.current
      ?.querySelector<HTMLButtonElement>(`[data-tab-key="${CSS.escape(next.key)}"]`)
      ?.focus();
  };

  const jump = (to: "first" | "last", e: React.KeyboardEvent) => {
    const enabled = items.filter((t) => !t.disabled);
    if (!enabled.length) return;
    e.preventDefault();
    const target = to === "first" ? enabled[0] : enabled[enabled.length - 1];
    onChange(target.key);
    listRef.current
      ?.querySelector<HTMLButtonElement>(`[data-tab-key="${CSS.escape(target.key)}"]`)
      ?.focus();
  };

  return (
    <div
      ref={listRef}
      role="tablist"
      aria-label={label}
      className={`${LIST[variant]} ${className}`}
      onKeyDown={(e) => {
        if (e.key === "ArrowRight" || e.key === "ArrowDown") move(1, e);
        else if (e.key === "ArrowLeft" || e.key === "ArrowUp") move(-1, e);
        else if (e.key === "Home") jump("first", e);
        else if (e.key === "End") jump("last", e);
      }}
    >
      {items.map((t) => {
        const on = t.key === value;
        return (
          <button
            key={t.key}
            type="button"
            role="tab"
            id={`${base}-tab-${t.key}`}
            data-tab-key={t.key}
            aria-selected={on}
            aria-controls={`${base}-panel-${t.key}`}
            // 로빙 tabindex — 선택된 탭 하나만 Tab 순서에 남는다.
            tabIndex={on ? 0 : -1}
            disabled={t.disabled}
            title={t.title}
            onClick={() => onChange(t.key)}
            className={`${TAB[variant](on)} disabled:cursor-not-allowed disabled:opacity-40`}
          >
            {t.label}
            {!!t.count && (
              <span className={`ml-1.5 num text-xs ${on ? "" : "text-ink-3"}`}>{t.count}</span>
            )}
          </button>
        );
      })}
    </div>
  );
}

/** 탭 본문 — `Tabs` 와 **같은 `id`** 를 넘겨야 `aria-controls` 가 이어진다. */
export function TabPanel({
  id, tabKey, children, className = "",
}: {
  id: string;
  tabKey: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      role="tabpanel"
      id={`${id}-panel-${tabKey}`}
      aria-labelledby={`${id}-tab-${tabKey}`}
      tabIndex={0}
      className={className}
    >
      {children}
    </div>
  );
}

/** 세그먼트 컨트롤 — 시각만 다른 탭이다(새로 만들기 3분할, 지식 2분할). */
export function SegmentedControl<K extends string>(
  props: Omit<React.ComponentProps<typeof Tabs<K>>, "variant">,
) {
  return <Tabs {...props} variant="segment" />;
}

export default Tabs;
