import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Toggle from "./Toggle";

/**
 * 토글의 **접근성 계약**. 설정 화면 전체가 이 컴포넌트를 지나므로 여기가 무너지면
 * SSL 검증·자동 실행 일시정지 같은 스위치를 키보드·스크린리더로 조작할 수 없게 된다.
 */

describe("Toggle", () => {
  it("스위치로 읽힌다 — div+onClick 이 아니다", () => {
    render(<Toggle checked={false} onChange={vi.fn()} label="SSL 인증서 검증" />);
    const sw = screen.getByRole("switch", { name: "SSL 인증서 검증" });
    expect(sw).toHaveAttribute("aria-checked", "false");
  });

  it("켜짐 상태를 aria-checked 로 알린다(색만으로 전달하지 않는다)", () => {
    render(<Toggle checked onChange={vi.fn()} label="자동 실행" />);
    expect(screen.getByRole("switch", { name: "자동 실행" })).toHaveAttribute("aria-checked", "true");
  });

  it("Space 로 토글된다 — 네이티브 button 이라 기본 동작이 산다", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<Toggle checked={false} onChange={onChange} label="자동 실행" />);
    screen.getByRole("switch").focus();
    await user.keyboard(" ");
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it("라벨을 눌러도 토글된다(히트 타깃 확대)", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<Toggle checked onChange={onChange} label="자동 실행" />);
    await user.click(screen.getByText("자동 실행"));
    expect(onChange).toHaveBeenCalledWith(false);
  });

  it("설명은 aria-describedby 로 연결한다 — 옆에 그려만 두면 낭독되지 않는다", () => {
    render(
      <Toggle checked={false} onChange={vi.fn()} label="Groq 대체 전사"
        description="켜면 회의 음성이 다른 회사로 전송됩니다." />,
    );
    expect(screen.getByRole("switch")).toHaveAccessibleDescription(
      "켜면 회의 음성이 다른 회사로 전송됩니다.",
    );
  });

  it("비활성이면 눌러도 바뀌지 않는다", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<Toggle checked={false} onChange={onChange} label="자동 실행" disabled />);
    await user.click(screen.getByRole("switch"));
    expect(onChange).not.toHaveBeenCalled();
  });
});
