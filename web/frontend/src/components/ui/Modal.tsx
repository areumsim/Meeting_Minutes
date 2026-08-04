import React, { useEffect, useRef } from "react";

/**
 * 접근 가능한 모달 — 이 앱의 커스텀 오버레이(비용 확인·온보딩·모바일 더보기)가
 * 전부 이 컴포넌트를 지난다.
 *
 * 지금까지 커스텀 모달은 `fixed inset-0` div 로만 그려져 있었다:
 *   - `role="dialog"`/`aria-modal` 없음 → 스크린리더가 대화상자임을 모른다
 *   - Escape 없음 → 키보드 사용자는 닫을 표준 수단이 없다. 비용 확인 모달은
 *     오클릭 방지를 위해 **백드롭 클릭까지 의도적으로 막았기 때문에** 키보드로는
 *     아예 빠져나올 수 없었다(Escape 는 명시적 행동이라 그 방지 목적과 충돌하지 않는다)
 *   - 포커스가 배경에 남음 → Tab 으로 모달 뒤 페이지를 계속 돌아다닐 수 있다
 *
 * 규칙:
 *   - Escape = `onClose`. 주지 않으면 Escape 로도 닫히지 않는다(정말 강제 결정이
 *     필요한 화면만 — 그 경우에도 닫기 버튼은 반드시 패널 안에 있어야 한다).
 *   - 열릴 때 `initialFocusRef`(없으면 첫 포커스 가능한 요소, 그것도 없으면 패널)로
 *     포커스를 옮기고, 닫힐 때 원래 요소로 되돌린다.
 *   - Tab/Shift+Tab 은 패널 안에서 순환한다(포커스 트랩).
 *   - 백드롭 클릭은 `closeOnBackdrop` 을 켠 경우에만 닫는다. 기본 꺼짐 —
 *     대용량 업로드를 오클릭 한 번으로 날리는 사고를 막았던 기존 판단을 유지한다.
 *
 * 애니메이션은 넣지 않는다(모션 감소 설정과의 상호작용·AnimatePresence exit 복잡도
 * 대비 이득이 없다). 필요하면 children 쪽에서 motion 요소를 쓴다.
 */

const FOCUSABLE =
  'a[href], button:not([disabled]), textarea:not([disabled]), ' +
  'input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

export default function Modal({
  labelledBy,
  onClose,
  closeOnBackdrop = false,
  panelClassName = "",
  overlayClassName = "fixed inset-0 z-[100] flex items-center justify-center bg-black/40 p-4",
  initialFocusRef,
  children,
}: {
  /** 패널 제목 요소의 id — 호출부가 제목에 같은 id 를 붙인다. */
  labelledBy: string;
  /** Escape·(옵션)백드롭 클릭 시 호출. 없으면 그 경로로는 닫히지 않는다. */
  onClose?: () => void;
  closeOnBackdrop?: boolean;
  panelClassName?: string;
  overlayClassName?: string;
  initialFocusRef?: React.RefObject<HTMLElement | null>;
  children: React.ReactNode;
}) {
  const panelRef = useRef<HTMLDivElement>(null);

  // 열릴 때 포커스 이동, 닫힐 때 원래 자리로 복귀.
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    const target =
      initialFocusRef?.current ??
      panelRef.current?.querySelector<HTMLElement>(FOCUSABLE) ??
      panelRef.current;
    target?.focus();
    return () => {
      try { prev?.focus(); } catch { /* 원래 요소가 사라졌으면 무시 */ }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      if (onClose) {
        e.stopPropagation();
        onClose();
      }
      return;
    }
    if (e.key !== "Tab") return;
    // 포커스 트랩 — 패널 안의 포커스 가능한 요소 사이에서만 순환한다.
    // `offsetParent` 로 보이는 요소만 거르면 안 된다: 오버레이가 position:fixed 라
    // 패널 안 **모든** 요소의 offsetParent 가 null 이고, 그 필터는 목록을 현재
    // 포커스 요소 하나로 줄여 Tab 을 통째로 죽인다(테스트가 잡아낸 실결함).
    const nodes = panelRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE);
    if (!nodes || nodes.length === 0) {
      e.preventDefault();
      return;
    }
    const list = Array.from(nodes);
    const first = list[0];
    const last = list[list.length - 1];
    const active = document.activeElement;
    if (e.shiftKey && (active === first || active === panelRef.current)) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && active === last) {
      e.preventDefault();
      first.focus();
    }
  };

  return (
    <div
      className={overlayClassName}
      onMouseDown={(e) => {
        if (closeOnBackdrop && onClose && e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        tabIndex={-1}
        onKeyDown={handleKeyDown}
        className={`outline-none ${panelClassName}`}
      >
        {children}
      </div>
    </div>
  );
}
