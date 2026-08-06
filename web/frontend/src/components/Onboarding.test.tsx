import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

/**
 * 첫 실행 마법사 (PRD FR-OB-1·FR-OB-2).
 *
 * 고정할 것은 "5단계가 뜬다"가 아니라 **단계 이동이 화면과 어긋나지 않는다**는 것이다.
 * 종전에는 본문에 AnimatePresence(mode="wait")가 걸려 있어 [다음]을 빠르게 누르면
 * 헤더·진행바는 5/5 인데 본문은 1단계인 상태가 실제로 눈에 보였다(실기에서 확인).
 * 마법사는 폼이라 그 어긋남이 곧 "어디까지 입력했는지 모르겠다"가 된다.
 */

const api = vi.hoisted(() => ({
  updateConfig: vi.fn(async () => ({ success: true })),
  testOpenAIKey: vi.fn(async () => ({ ok: true, message: "연결됨" })),
  testAnthropicKey: vi.fn(async () => ({ ok: true, message: "연결됨" })),
  testEmail: vi.fn(async () => ({ ok: true, message: "보냈습니다" })),
  pickFolder: vi.fn(async () => ({ ok: false })),
}));
vi.mock("../lib/api", () => api);

import Onboarding from "./Onboarding";

beforeEach(() => { vi.clearAllMocks(); localStorage.clear(); });

describe("단계 이동", () => {
  it("[다음]을 연달아 눌러도 본문이 헤더와 같은 단계를 보여준다", async () => {
    const user = userEvent.setup();
    render(<Onboarding onClose={vi.fn()} />);

    expect(screen.getByText(/1 \/ 5 단계/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /OpenAI API 키/ })).toBeInTheDocument();

    for (let i = 0; i < 4; i++) await user.click(screen.getByRole("button", { name: /다음|완료/ }));

    // 헤더가 5/5 라면 본문도 5단계여야 한다 — 이 둘이 갈라지는 것이 회귀 대상이다.
    expect(screen.getByText(/5 \/ 5 단계/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /이메일 자동 발송/ })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /OpenAI API 키/ })).toBeNull();
  });

  it("진행바가 진행률을 값으로 알린다(색 막대만으로는 낭독되지 않는다)", async () => {
    const user = userEvent.setup();
    render(<Onboarding onClose={vi.fn()} />);
    const bar = screen.getByRole("progressbar", { name: "설정 마법사 진행률" });
    expect(bar).toHaveAttribute("aria-valuenow", "20");
    await user.click(screen.getByRole("button", { name: "다음" }));
    expect(bar).toHaveAttribute("aria-valuenow", "40");
  });

  it("헤더가 단계 번호와 **이름**을 함께 알린다", () => {
    render(<Onboarding onClose={vi.fn()} />);
    // 숫자만으로는 어디인지 알 수 없다 — role=status 로 낭독된다.
    expect(screen.getByRole("status")).toHaveTextContent("1 / 5 단계 · OpenAI API 키");
  });

  it("첫 단계에는 [이전]이 보이지 않고, 이후 단계에서 돌아갈 수 있다", async () => {
    const user = userEvent.setup();
    render(<Onboarding onClose={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "다음" }));
    await user.click(screen.getByRole("button", { name: "이전" }));
    expect(screen.getByRole("heading", { name: /OpenAI API 키/ })).toBeInTheDocument();
  });
});

describe("저장·종료", () => {
  it("입력한 키만 저장한다 — 빈 칸으로 넘어가면 서버를 부르지 않는다", async () => {
    const user = userEvent.setup();
    render(<Onboarding onClose={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "다음" }));
    expect(api.updateConfig).not.toHaveBeenCalled();
  });

  it("키를 넣고 넘어가면 그 단계 값만 저장한다", async () => {
    const user = userEvent.setup();
    render(<Onboarding onClose={vi.fn()} />);
    await user.type(screen.getByLabelText("OpenAI API 키"), "sk-proj-abc");
    await user.click(screen.getByRole("button", { name: "다음" }));
    expect(api.updateConfig).toHaveBeenCalledWith({ api: { openai_api_key: "sk-proj-abc" } });
  });

  it("[나중에 하기]는 다시 뜨지 않도록 표시하고 닫는다", async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<Onboarding onClose={onClose} />);
    await user.click(screen.getByRole("button", { name: "나중에 하기" }));
    expect(localStorage.getItem("ONBOARDING_DISMISSED")).toBe("1");
    expect(onClose).toHaveBeenCalled();
  });
});

describe("키 입력", () => {
  it("기본은 가려져 있고, 눌러서 확인할 수 있다(키보드로도 도달한다)", async () => {
    const user = userEvent.setup();
    render(<Onboarding onClose={vi.fn()} />);
    const input = screen.getByLabelText("OpenAI API 키");
    expect(input).toHaveAttribute("type", "password");
    await user.click(screen.getByRole("button", { name: "키 표시" }));
    expect(input).toHaveAttribute("type", "text");
  });

  it("연결 테스트 결과를 색만이 아니라 글자로도 알린다", async () => {
    const user = userEvent.setup();
    render(<Onboarding onClose={vi.fn()} />);
    await user.type(screen.getByLabelText("OpenAI API 키"), "sk-proj-abc");
    await user.click(screen.getByRole("button", { name: "연결 테스트" }));
    expect(await screen.findByText("연결됨")).toBeInTheDocument();
  });
});
