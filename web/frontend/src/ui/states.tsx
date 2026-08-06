import React from "react";
import { Loader2, RefreshCw, type LucideIcon } from "lucide-react";
import { Button } from "./Button";

/**
 * 로딩·빈 상태·오류·진행바 (PRD §8 엣지케이스 표).
 *
 * 이 넷을 컴포넌트로 묶는 이유는 모양이 아니라 **구분을 강제하기 위해서**다. 종전 대시보드는
 * 조회 실패와 '회의 0건'을 같은 빈 화면으로 그렸다 — 백엔드가 죽은 것과 회의가 없는 것이
 * 사용자에게 똑같이 보였다. `ErrorState` 는 반드시 사유와 재시도를 받고, `EmptyState` 는
 * 재시도를 받지 않는다(그 구분이 타입으로 남는다).
 */

export function Spinner({ label = "불러오는 중", size = 20, className = "" }: {
  label?: string; size?: number; className?: string;
}) {
  return (
    <span role="status" className={`inline-flex items-center gap-2 text-ink-3 ${className}`}>
      <Loader2 size={size} className="animate-spin" aria-hidden="true" />
      <span className="sr-only">{label}</span>
    </span>
  );
}

/** 리스트·문서·그래프의 중앙 로딩. */
export function LoadingBlock({ label = "불러오는 중", className = "" }: {
  label?: string; className?: string;
}) {
  return (
    <div className={`flex items-center justify-center py-16 ${className}`}>
      <Spinner label={label} size={24} />
    </div>
  );
}

/** 아이콘 + 한 줄 설명 + 주 CTA. **재시도 버튼을 두지 않는다** — 그건 오류 상태의 몫이다. */
export function EmptyState({
  icon: Icon, title, description, action, className = "",
}: {
  icon: LucideIcon;
  title: string;
  description?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={`flex flex-col items-center justify-center gap-2 px-6 py-14 text-center ${className}`}>
      <Icon size={28} className="text-ink-3" aria-hidden="true" />
      <p className="text-md font-semibold text-ink">{title}</p>
      {description && <p className="max-w-sm text-sm text-ink-3">{description}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

/**
 * 조회·처리 실패. `role="alert"` 로 즉시 알리고 **사유를 그대로** 보여준다 —
 * 화면이 자기 문구를 지어내면 거짓이 될 수 있다(서버가 "폴더가 이미 없습니다"를 준 전례).
 */
export function ErrorState({
  title, detail, onRetry, retryLabel = "다시 시도", className = "",
}: {
  title: string;
  detail?: string;
  onRetry?: () => void;
  retryLabel?: string;
  className?: string;
}) {
  return (
    <div role="alert" className={`flex flex-col items-center gap-2 px-6 py-14 text-center ${className}`}>
      <span aria-hidden="true" className="text-lg font-bold text-rec">✕</span>
      <p className="text-md font-semibold text-ink">{title}</p>
      {detail && <p className="max-w-md break-words text-sm text-ink-2">{detail}</p>}
      {onRetry && (
        <Button variant="secondary" size="sm" icon={RefreshCw} onClick={onRetry} className="mt-2">
          {retryLabel}
        </Button>
      )}
    </div>
  );
}

/**
 * 진행바. `role="progressbar"` + `aria-valuenow` 로 진행률이 낭독된다.
 *
 * STT 단계는 내부 진행률이 없어 퍼센트가 한동안 멈춘 것처럼 보인다 — 그때 `indeterminate`
 * 를 켜면 '움직이고 있다'를 시각으로 알린다(reduced-motion 에서는 전역 규칙이 멈춘다).
 */
export function ProgressBar({
  percent, label, indeterminate, className = "",
}: {
  percent: number;
  label: string;
  indeterminate?: boolean;
  className?: string;
}) {
  const pct = Math.max(0, Math.min(100, Math.round(percent)));
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuenow={pct}
      aria-valuemin={0}
      aria-valuemax={100}
      className={`h-1.5 overflow-hidden rounded-full bg-surface-2 ${className}`}
    >
      <div
        className={`h-full rounded-full bg-accent transition-[width] duration-500 ${
          indeterminate && pct < 100 ? "animate-pulse" : ""
        }`}
        // 0% 여도 가느다란 막대를 남긴다 — 완전히 비면 '시작도 안 했다'로 읽힌다.
        style={{ width: `${Math.max(2, pct)}%` }}
      />
    </div>
  );
}
