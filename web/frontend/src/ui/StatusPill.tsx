import React from "react";
import { AlertCircle, type LucideIcon } from "lucide-react";

/**
 * 상태 배지와 태그 (PRD §5.4·§5.5).
 *
 * 핵심 계약은 하나다 — **색만으로 상태를 전달하지 않는다.** 색 + 점/아이콘 + 글자를 함께
 * 낸다. 오류는 색이 아니라 ✕ 아이콘이 신호이고(적록색약), 녹음 중 점멸은
 * `prefers-reduced-motion` 에서 멈춘다(index.css 전역 규칙).
 *
 * 상태값은 서버 DB 의 것(processing/completed/error/pending)과 화면 전용(recording)을
 * 함께 받는다. 모르는 값은 중립(idle)으로 그리고 **원문을 그대로** 보여준다 — 서버가 새
 * 상태를 추가해도 화면이 비지 않게(lib/format.statusLabel 과 같은 방침).
 */

export type StatusTone = "rec" | "proc" | "ok" | "err" | "idle" | "warn" | "persona";

const TONE: Record<StatusTone, string> = {
  rec: "bg-rec-bg text-rec",
  proc: "bg-proc-bg text-proc",
  ok: "bg-ok-bg text-ok",
  err: "bg-rec-bg text-rec",
  idle: "bg-idle-bg text-idle",
  warn: "bg-warn-bg text-warn",
  persona: "bg-persona-bg text-persona",
};

const DOT: Record<StatusTone, string> = {
  rec: "bg-rec", proc: "bg-proc", ok: "bg-ok", err: "bg-rec",
  idle: "bg-idle", warn: "bg-warn", persona: "bg-persona",
};

export interface StatusPillProps {
  tone: StatusTone;
  children: React.ReactNode;
  /** 녹음 중처럼 '지금 일어나는 일'에만. reduced-motion 에서는 전역 규칙이 멈춘다. */
  pulse?: boolean;
  /** 점 대신 쓸 아이콘. 오류(err)는 지정하지 않아도 ✕ 계열 아이콘이 붙는다. */
  icon?: LucideIcon;
  title?: string;
  className?: string;
}

export function StatusPill({ tone, children, pulse, icon, title, className = "" }: StatusPillProps) {
  const Icon = icon ?? (tone === "err" ? AlertCircle : undefined);
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-semibold ${TONE[tone]} ${className}`}
    >
      {Icon
        ? <Icon size={11} className="shrink-0" aria-hidden="true" />
        : <span
            aria-hidden="true"
            className={`h-1.5 w-1.5 shrink-0 rounded-full ${DOT[tone]} ${pulse ? "animate-pulse" : ""}`}
          />}
      {children}
    </span>
  );
}

/** 세션 상태 → 배지 톤. 화면마다 다시 판정하지 않도록 여기 하나만 둔다. */
export function statusTone(status: string): StatusTone {
  switch (status) {
    case "recording": return "rec";
    case "processing": return "proc";
    case "completed": return "ok";
    case "error": return "err";
    default: return "idle";      // pending·planned·미지의 값
  }
}

/**
 * 중립 태그 — 유형·길이·모드처럼 **상태가 아닌** 부가 정보.
 * 상태 배지와 시각적으로 구분되어야 한다(그래서 채운 배경이 아니라 테두리다).
 */
export function Tag({
  children, className = "", title, tone,
}: {
  children: React.ReactNode;
  className?: string;
  title?: string;
  /** 유형별 색이 필요할 때만(lib/format.typeColor 가 주는 클래스). 기본은 중립. */
  tone?: string;
}) {
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs ${
        tone || "border-line-strong bg-surface text-ink-2"
      } ${className}`}
    >
      {children}
    </span>
  );
}

export default StatusPill;
