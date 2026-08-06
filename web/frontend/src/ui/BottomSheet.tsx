import React from "react";
import Modal from "./Modal";

/**
 * 하단 시트 (모바일, PRD §4.3).
 *
 * 데스크톱의 우측 인스펙터가 좁은 화면에서 되는 모습이다 — 본문(전사·회의록)을 가리지 않고
 * 아래에서 올라온다. 모달 계약(Escape·포커스 트랩·role=dialog)은 `Modal` 을 그대로 쓴다:
 * 시트라고 해서 접근성 규칙이 달라지지 않는다.
 *
 * 백드롭 클릭으로 닫는다 — 시트는 대개 '보기'용이라 잘못 닫혀도 잃는 것이 없다(대용량
 * 업로드를 날리는 비용 확인 모달과 다르다). 그립을 눌러도 닫힌다.
 */
export function BottomSheet({
  labelledBy, title, onClose, children, footer, heightClass = "h-[68dvh]",
}: {
  labelledBy: string;
  /** 시각 제목. 시트에는 항상 제목이 있어야 무엇이 올라왔는지 알 수 있다. */
  title: React.ReactNode;
  onClose: () => void;
  children: React.ReactNode;
  footer?: React.ReactNode;
  heightClass?: string;
}) {
  return (
    <Modal
      labelledBy={labelledBy}
      onClose={onClose}
      closeOnBackdrop
      overlayClassName="fixed inset-0 z-100 flex items-end justify-center bg-black/40"
      panelClassName={`flex w-full flex-col rounded-t-2xl border-t border-line bg-surface ${heightClass} pb-[env(safe-area-inset-bottom,0px)]`}
    >
      <button
        type="button"
        onClick={onClose}
        aria-label="시트 닫기"
        className="mx-auto mt-2 mb-1 h-1 w-10 shrink-0 rounded-full bg-line-strong"
      />
      <h2 id={labelledBy} className="shrink-0 px-4 pb-2 text-md font-semibold text-ink">
        {title}
      </h2>
      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 pb-3">
        {children}
      </div>
      {footer && <div className="shrink-0 border-t border-line bg-surface-2 px-4 py-2">{footer}</div>}
    </Modal>
  );
}

export default BottomSheet;
