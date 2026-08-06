import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import Modal from "./Modal";

/**
 * 공통 모달의 **접근성 계약**을 고정한다.
 *
 * 이전 커스텀 모달은 `fixed inset-0` div 뿐이었다 — role 없음, Escape 없음, 포커스가
 * 배경에 남음. 특히 예상 비용 확인 모달은 백드롭 클릭까지 의도적으로 막아서(오클릭
 * 방지) 키보드 사용자는 빠져나올 수단이 아예 없었다. 그 네 가지가 회귀 대상이다.
 */
describe("Modal 접근성 계약", () => {
  const body = (
    <>
      <h3 id="m-title">제목</h3>
      <button>첫 버튼</button>
      <button>둘째 버튼</button>
    </>
  );

  it("role=dialog + aria-modal + labelledby 로 대화상자임을 알린다", () => {
    render(<Modal labelledBy="m-title">{body}</Modal>);
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveAttribute("aria-labelledby", "m-title");
  });

  it("Escape 로 닫힌다 — 백드롭 클릭이 막힌 모달의 유일한 키보드 탈출구", async () => {
    const onClose = vi.fn();
    render(<Modal labelledBy="m-title" onClose={onClose}>{body}</Modal>);
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("onClose 가 없으면 Escape 로도 닫히지 않는다(강제 결정 화면)", async () => {
    render(<Modal labelledBy="m-title">{body}</Modal>);
    await userEvent.keyboard("{Escape}");   // 아무 일도 없어야 한다(크래시 없음)
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("열리면 패널 안 첫 요소로 포커스가 이동한다", () => {
    render(<Modal labelledBy="m-title">{body}</Modal>);
    expect(screen.getByRole("button", { name: "첫 버튼" })).toHaveFocus();
  });

  it("Tab 이 패널 안에서 순환한다 — 배경 페이지로 새지 않는다", async () => {
    render(
      <>
        <button>배경 버튼</button>
        <Modal labelledBy="m-title">{body}</Modal>
      </>,
    );
    const first = screen.getByRole("button", { name: "첫 버튼" });
    const second = screen.getByRole("button", { name: "둘째 버튼" });
    expect(first).toHaveFocus();
    await userEvent.tab();
    expect(second).toHaveFocus();
    await userEvent.tab();                    // 마지막에서 Tab → 처음으로 순환
    expect(first).toHaveFocus();
    await userEvent.tab({ shift: true });     // 처음에서 Shift+Tab → 마지막으로
    expect(second).toHaveFocus();
  });

  it("백드롭 클릭은 기본적으로 닫지 않는다(대용량 업로드 오클릭 사고 방지)", async () => {
    const onClose = vi.fn();
    const { container } = render(
      <Modal labelledBy="m-title" onClose={onClose}>{body}</Modal>,
    );
    await userEvent.click(container.firstElementChild as HTMLElement);
    expect(onClose).not.toHaveBeenCalled();
  });

  it("closeOnBackdrop 을 켠 경우에만 백드롭 클릭으로 닫힌다(더보기 시트)", async () => {
    const onClose = vi.fn();
    const { container } = render(
      <Modal labelledBy="m-title" onClose={onClose} closeOnBackdrop>{body}</Modal>,
    );
    await userEvent.click(container.firstElementChild as HTMLElement);
    expect(onClose).toHaveBeenCalledOnce();
  });
});
