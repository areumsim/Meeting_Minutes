import React, { useId, useRef, useState } from "react";
import { AlertTriangle, ShieldAlert, X, type LucideIcon } from "lucide-react";

/**
 * 전역 고지 (PRD §8 · §10).
 *
 * 두 종류를 **의도적으로 구분**한다:
 *
 *  - `Banner` — 지금 조치가 필요한 것. config 손상(저장이 막혀 있다)·ffmpeg 없음·백엔드
 *    오프라인. 화면 폭을 차지하고 `role="alert"` 로 즉시 알린다.
 *  - `QuietBadge` — 사실이지만 상시 경고할 일은 아닌 것. SSL 검증을 **사용자가 껐을 때**가
 *    이것이다. 종전에는 이것도 배너였는데, 기본값이 안전(ON)해진 뒤로는 상시 배너가
 *    "늘 뭔가 잘못된 앱"이라는 인상만 남기고 아무도 읽지 않았다(PRD §1.2·§10).
 *    배지는 topbar 에 조용히 있다가 누르면 위험과 **되돌리는 방법**을 함께 보여준다 —
 *    위험만 말하고 방법을 안 주면 사용자는 그대로 둔다.
 */

export function Banner({
  tone = "warn", icon: Icon, title, children, actions, onDismiss,
}: {
  tone?: "warn" | "err";
  icon?: LucideIcon;
  title: React.ReactNode;
  children?: React.ReactNode;
  actions?: React.ReactNode;
  onDismiss?: () => void;
}) {
  const I = Icon ?? (tone === "err" ? ShieldAlert : AlertTriangle);
  const cls = tone === "err"
    ? "border-rec bg-rec-bg text-rec"
    : "border-warn-line bg-warn-bg text-warn";
  return (
    <div role="alert" className={`flex items-start gap-2 rounded-card border px-3 py-2 text-sm ${cls}`}>
      <I size={15} className="mt-0.5 shrink-0" aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <p className="font-semibold">{title}</p>
        {children && <div className="mt-0.5 text-ink-2">{children}</div>}
        {actions && <div className="mt-2 flex flex-wrap gap-2">{actions}</div>}
      </div>
      {onDismiss && (
        <button type="button" onClick={onDismiss} aria-label="알림 닫기"
          className="shrink-0 rounded p-0.5 hover:bg-hover">
          <X size={14} aria-hidden="true" />
        </button>
      )}
    </div>
  );
}

/**
 * 조용한 상태 배지 + 팝오버.
 *
 * 색만으로 알리지 않는다 — 아이콘 + 짧은 글자를 함께 낸다. `role="status"` 라 배지가
 * 나타난 사실이 낭독되고, 내용은 눌러서 펼친다(터치·키보드 모두 가능).
 */
export function QuietBadge({
  label, icon: Icon = ShieldAlert, tone = "warn", title, children,
}: {
  label: string;
  icon?: LucideIcon;
  tone?: "warn" | "err";
  /** 팝오버 제목. */
  title: React.ReactNode;
  children: React.ReactNode;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const cls = tone === "err" ? "text-rec bg-rec-bg" : "text-warn bg-warn-bg";

  return (
    <div
      ref={wrapRef}
      className="relative"
      // 팝오버 밖으로 포커스가 나가면 닫는다. 바깥 클릭 리스너를 document 에 붙이지 않는
      // 이유는 그쪽이 모달·시트와 순서 다툼을 만들기 때문이다.
      onBlur={(e) => {
        if (!wrapRef.current?.contains(e.relatedTarget as Node)) setOpen(false);
      }}
    >
      <button
        type="button"
        role="status"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => setOpen((v) => !v)}
        onKeyDown={(e) => { if (e.key === "Escape") setOpen(false); }}
        className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-semibold ${cls}`}
      >
        <Icon size={12} aria-hidden="true" />
        {label}
      </button>
      {open && (
        <div id={id}
          className="absolute right-0 top-full z-50 mt-1.5 w-80 rounded-card border border-line
            bg-surface p-3 text-sm shadow-pop">
          <p className="mb-1 font-semibold text-ink">{title}</p>
          <div className="space-y-2 text-sm text-ink-2">{children}</div>
        </div>
      )}
    </div>
  );
}

export default Banner;
