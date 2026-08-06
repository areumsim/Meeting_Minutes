import React, { useId } from "react";

/**
 * 켜기/끄기 스위치 (PRD §5.4·§5.5).
 *
 * 계약:
 *  - `<button role="switch" aria-checked>` — div+onClick 이 아니다. 스크린리더가 "스위치,
 *    켜짐/꺼짐" 으로 읽고 Space·Enter 가 브라우저 기본으로 동작한다.
 *  - **꺼짐 상태의 트랙도 배경 대비 ≥3:1**(`--color-control-border`). 연회색 트랙은
 *    저시력 사용자에게 '없는 컨트롤'이 된다.
 *  - 라벨을 누르면 토글된다(히트 타깃 확대). 트랙만 32×20 이면 24×24 기준에 못 미친다 —
 *    그래서 버튼 자체에 세로 패딩을 줘 40px 높이를 만든다.
 *  - 설명(`description`)은 `aria-describedby` 로 연결한다. 옆에 그려만 두면 낭독되지 않는다.
 */
export function Toggle({
  checked, onChange, label, description, disabled, id,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: React.ReactNode;
  description?: React.ReactNode;
  disabled?: boolean;
  id?: string;
}) {
  const auto = useId();
  const labelId = `${id || auto}-label`;
  const descId = description ? `${id || auto}-desc` : undefined;

  return (
    <div className="flex items-start gap-3">
      <div className="min-w-0 flex-1">
        <label id={labelId} htmlFor={id || auto} className="block cursor-pointer text-base text-ink">
          {label}
        </label>
        {description && (
          <p id={descId} className="mt-0.5 text-xs text-ink-3">{description}</p>
        )}
      </div>
      <button
        id={id || auto}
        type="button"
        role="switch"
        aria-checked={checked}
        aria-labelledby={labelId}
        aria-describedby={descId}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className="group relative shrink-0 self-start py-2.5 disabled:cursor-not-allowed disabled:opacity-50"
      >
        <span
          aria-hidden="true"
          className={`block h-5 w-9 rounded-full transition-colors ${
            checked ? "bg-accent-solid" : "bg-control-border"
          }`}
        />
        <span
          aria-hidden="true"
          className={`absolute top-3 h-4 w-4 rounded-full bg-white shadow-flat transition-[left] ${
            checked ? "left-[1.125rem]" : "left-0.5"
          }`}
        />
      </button>
    </div>
  );
}

export default Toggle;
