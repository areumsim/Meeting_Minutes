import React, { useId } from "react";

/**
 * 폼 필드 (PRD §5.4).
 *
 * 라벨·설명·오류를 **id 로 연결**하는 일을 한 곳에서 한다 — 종전에는 화면마다 `<label>` 을
 * 손으로 붙였고, `htmlFor` 가 빠진 곳(Recorder 세션 설정 5개)에서는 라벨을 눌러도 포커스가
 * 가지 않고 스크린리더가 입력의 이름을 읽지 못했다.
 *
 * 고밀도(PRD §3-3)를 위해 라벨은 대문자 트래킹 없이 13px 한 줄로 둔다.
 */

export function Field({
  label, description, error, required, htmlFor, children, className = "", hint,
}: {
  label: React.ReactNode;
  description?: React.ReactNode;
  /** 값이 잘못됐을 때. `role="alert"` 로 즉시 알린다. */
  error?: string;
  required?: boolean;
  /** 아래 컨트롤의 id — `<Input id=…>` 를 쓸 때 같은 값을 넘긴다. */
  htmlFor?: string;
  /** 라벨 오른쪽 보조 정보(글자수 등). */
  hint?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={`space-y-1 ${className}`}>
      <div className="flex items-baseline gap-2">
        <label htmlFor={htmlFor} className="text-base font-medium text-ink">
          {label}
          {required && <span className="ml-0.5 text-rec" aria-hidden="true">*</span>}
          {required && <span className="sr-only"> (필수)</span>}
        </label>
        {hint && <span className="ml-auto text-xs text-ink-3">{hint}</span>}
      </div>
      {description && <p className="text-xs text-ink-3">{description}</p>}
      {children}
      {error && <p role="alert" className="text-xs font-medium text-rec">{error}</p>}
    </div>
  );
}

const CONTROL =
  "w-full rounded-ctl border border-control-border bg-surface px-2.5 py-1.5 text-base " +
  "text-ink placeholder:text-ink-3 transition-colors " +
  "hover:border-ink-3 disabled:cursor-not-allowed disabled:bg-surface-2 disabled:opacity-60";

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  function Input({ className = "", ...rest }, ref) {
    return <input ref={ref} className={`${CONTROL} ${className}`} {...rest} />;
  },
);

export const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(
  function Textarea({ className = "", ...rest }, ref) {
    return <textarea ref={ref} className={`${CONTROL} resize-y ${className}`} {...rest} />;
  },
);

export const Select = React.forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement>>(
  function Select({ className = "", children, ...rest }, ref) {
    // 네이티브 select 를 쓴다 — 커스텀 드롭다운은 키보드·IME·모바일 휠 피커를 전부 다시
    // 만들어야 하고, 이 앱의 선택지는 대부분 5개 미만이라 얻는 것이 없다.
    return (
      <select ref={ref} className={`${CONTROL} ${className}`} {...rest}>
        {children}
      </select>
    );
  },
);

/**
 * 라벨 있는 입력 한 줄 — 설정·폼에서 가장 흔한 조합의 지름길.
 * id 를 안 넘겨도 자동 생성해 라벨과 묶는다.
 */
export function TextField({
  label, description, error, required, hint, id, className, ...rest
}: {
  label: React.ReactNode;
  description?: React.ReactNode;
  error?: string;
  required?: boolean;
  hint?: React.ReactNode;
  className?: string;
} & React.InputHTMLAttributes<HTMLInputElement>) {
  const auto = useId();
  const fieldId = id || auto;
  return (
    <Field label={label} description={description} error={error} required={required}
           hint={hint} htmlFor={fieldId} className={className}>
      <Input id={fieldId} required={required} aria-invalid={error ? true : undefined} {...rest} />
    </Field>
  );
}

export default Field;
