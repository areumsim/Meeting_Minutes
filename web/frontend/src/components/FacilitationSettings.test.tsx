import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

/**
 * 참견도 매트릭스 — 고위험 페르소나를 화면에 여는 선택.
 *
 * 코드 상한(hard_cap)을 2→3 으로 푼 것은 소유자 결정이지, "기본으로 켠다"가 아니다.
 * 이 테스트가 고정하는 것은 그 경계다: **고를 수는 있고, 저절로 올라가지는 않으며,
 * 고르면 무엇을 고른 것인지 화면이 말한다.** 경고 문구가 사라지면 사용자는 오탐률
 * 실측 전이라는 사실을 모른 채 회의 중에 잘못된 대조를 보게 된다.
 */

const api = vi.hoisted(() => ({
  getFacilitationPersonas: vi.fn(),
  updateConfig: vi.fn(),
}));

vi.mock("../lib/api", () => api);

import FacilitationSettings from "./FacilitationSettings";

const persona = (over: Partial<any> = {}) => ({
  key: "scribe", label: "📝 서기", role: "결정·액션을 놓치지 않는다",
  risk: "low", hardCap: null, configuredLevel: 1, level: 1, ...over,
});

const payload = (over: Partial<any> = {}) => ({
  enabled: true, maxLevel: 3, displayLevel: 3,
  personas: [
    persona(),
    persona({ key: "fact_checker", label: "🔍 팩트체커", risk: "high", hardCap: 3 }),
    persona({ key: "devils_advocate", label: "😈 악마의 변호인", risk: "medium" }),
  ],
  ...over,
});

const WARN = /오탐률 실측 전입니다/;

/** 특정 페르소나 줄의 참견도 버튼 — 같은 라벨이 줄마다 반복되므로 줄로 좁힌다. */
function levelButton(label: string, lvl: number) {
  const row = screen.getByText(label).closest("div.flex.flex-col")!;
  return within(row as HTMLElement).getByRole("button", { name: new RegExp(`^${lvl} `) });
}

describe("FacilitationSettings — 고위험 개방", () => {
  beforeEach(() => {
    api.getFacilitationPersonas.mockResolvedValue(payload());
    api.updateConfig.mockResolvedValue({ ok: true });
  });

  it("고위험도 3(옆 카드)을 고를 수 있다 — 잠겨 있지 않다", async () => {
    render(<FacilitationSettings />);
    await screen.findByText("🔍 팩트체커");
    expect(levelButton("🔍 팩트체커", 3)).not.toBeDisabled();
  });

  it("4·5(알림음·음성)는 여전히 잠겨 있다 — 미구현이다", async () => {
    api.getFacilitationPersonas.mockResolvedValue(payload({ maxLevel: 5 }));
    render(<FacilitationSettings />);
    await screen.findByText("🔍 팩트체커");
    expect(levelButton("🔍 팩트체커", 4)).toBeDisabled();
    expect(levelButton("🔍 팩트체커", 5)).toBeDisabled();
  });

  it("기본(관찰)에서는 경고가 없고, 3을 고르면 그 줄에 뜬다", async () => {
    const user = userEvent.setup();
    render(<FacilitationSettings />);
    await screen.findByText("🔍 팩트체커");
    expect(screen.queryByText(WARN)).toBeNull();

    await user.click(levelButton("🔍 팩트체커", 3));
    expect(await screen.findByText(WARN)).toBeInTheDocument();
  });

  it("저위험을 3으로 올려도 경고는 뜨지 않는다 — 경고가 배경음이 되면 안 된다", async () => {
    const user = userEvent.setup();
    render(<FacilitationSettings />);
    await screen.findByText("📝 서기");
    await user.click(levelButton("📝 서기", 3));
    expect(screen.queryByText(WARN)).toBeNull();
  });

  it("'적극' 프리셋도 고위험은 올리지 않는다 — 개별 선택으로만 열린다", async () => {
    const user = userEvent.setup();
    render(<FacilitationSettings />);
    await screen.findByText("🔍 팩트체커");
    await user.click(screen.getByRole("button", { name: "적극" }));
    expect(screen.queryByText(WARN)).toBeNull();

    await user.click(screen.getByRole("button", { name: /참견도 저장/ }));
    await waitFor(() => expect(api.updateConfig).toHaveBeenCalled());
    const sent = api.updateConfig.mock.calls[0][0].facilitation;
    expect(sent["personas.fact_checker.level"]).toBe(1);
    expect(sent["personas.scribe.level"]).toBe(3);
  });
});
