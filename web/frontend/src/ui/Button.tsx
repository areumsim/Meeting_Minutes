import React from "react";
import { Loader2, type LucideIcon } from "lucide-react";

/**
 * 캐노니컬 버튼 (PRD §5.4).
 *
 * 화면 코드가 버튼 클래스를 직접 쓰지 않게 하는 것이 목적이다 — 종전에는 Assistant·
 * Settings·FileUpload 가 각자 지역 `Btn` 을 갖고 있어서 색·크기·hover 가 조금씩 달랐다.
 *
 * 지키는 계약:
 *  - **hover 상태가 반드시 있다**(primary·danger 포함). 눌리는 것인지 아닌지 마우스로 알 수 있어야 한다.
 *  - secondary 테두리는 배경 대비 ≥3:1(`--color-control-border`) — 테두리가 유일한 경계다.
 *  - 최소 히트 타깃 24×24(sm=28px, md=32px, icon 도 정사각으로 같은 높이).
 *  - 아이콘만 있는 버튼은 `label` 을 **반드시** 받는다(접근 가능한 이름). 안 주면 타입 에러.
 *  - `busy` 는 스피너로 바꾸되 **버튼 폭을 유지**한다 — 연타 방지로 disabled 되면서 폭이
 *    줄면 옆 버튼이 커서 밑으로 들어와 오클릭이 난다.
 */

type Variant = "primary" | "secondary" | "danger" | "ghost";
type Size = "md" | "sm";

const VARIANT: Record<Variant, string> = {
  primary:
    "bg-accent-solid text-on-accent border border-accent-solid " +
    "hover:bg-accent-solid-hover hover:border-accent-solid-hover shadow-flat",
  secondary:
    "bg-surface text-ink border border-control-border hover:bg-hover shadow-flat",
  danger:
    "bg-rec text-white border border-rec hover:brightness-90 shadow-flat",
  ghost:
    "bg-transparent text-ink-2 border border-transparent hover:bg-hover hover:text-ink",
};

const SIZE: Record<Size, string> = {
  md: "h-8 px-3.5 text-sm",
  sm: "h-7 px-2.5 text-xs",
};

const ICON_SIZE: Record<Size, string> = {
  md: "h-8 w-8",
  sm: "h-7 w-7",
};

const BASE =
  "inline-flex items-center justify-center gap-1.5 rounded-ctl font-semibold " +
  "whitespace-nowrap transition-colors select-none " +
  "disabled:opacity-50 disabled:cursor-not-allowed disabled:pointer-events-none";

export interface ButtonProps extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "type"> {
  variant?: Variant;
  size?: Size;
  /** 진행 중 — 스피너로 바꾸고 비활성화한다(폭은 유지). */
  busy?: boolean;
  icon?: LucideIcon;
  /** 아이콘을 글자 뒤에 둘 때(예: 펼침 ▾). */
  iconAfter?: LucideIcon;
  type?: "button" | "submit";
}

export function Button({
  variant = "secondary", size = "md", busy, icon: Icon, iconAfter: IconAfter,
  className = "", children, disabled, type = "button", ...rest
}: ButtonProps) {
  const px = size === "sm" ? 13 : 14;
  return (
    <button
      type={type}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      className={`${BASE} ${SIZE[size]} ${VARIANT[variant]} ${className}`}
      {...rest}
    >
      {busy
        ? <Loader2 size={px} className="animate-spin shrink-0" aria-hidden="true" />
        : Icon && <Icon size={px} className="shrink-0" aria-hidden="true" />}
      {children}
      {IconAfter && !busy && <IconAfter size={px} className="shrink-0" aria-hidden="true" />}
    </button>
  );
}

export interface IconButtonProps extends Omit<ButtonProps, "children" | "icon" | "iconAfter"> {
  icon: LucideIcon;
  /** 접근 가능한 이름 — 아이콘만 있는 버튼에는 필수다(스크린리더에 "버튼"만 읽히지 않게). */
  label: string;
}

export function IconButton({
  icon: Icon, label, variant = "ghost", size = "md", busy,
  className = "", disabled, type = "button", ...rest
}: IconButtonProps) {
  return (
    <button
      type={type}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      aria-label={label}
      title={label}
      className={`${BASE} ${ICON_SIZE[size]} ${VARIANT[variant]} ${className}`}
      {...rest}
    >
      {busy
        ? <Loader2 size={15} className="animate-spin" aria-hidden="true" />
        : <Icon size={15} aria-hidden="true" />}
    </button>
  );
}

export default Button;
