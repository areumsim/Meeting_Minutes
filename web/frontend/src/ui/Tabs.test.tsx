import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import Tabs, { TabPanel, SegmentedControl } from "./Tabs";

/**
 * 탭의 **키보드·시맨틱 계약**을 고정한다.
 *
 * 종전 화면들(상세 문서 탭·지식 세그먼트·인스펙터 탭)은 `<button>` 나열이라 스크린리더가
 * "탭 3개 중 2번째"를 읽지 못했고 방향키도 안 먹었다. 시각만 탭처럼 보이는 것과 탭인 것은
 * 다르다 — 그 차이가 여기서 무너지면 조용히 원래대로 돌아간다.
 */

const ITEMS = [
  { key: "a" as const, label: "회의록" },
  { key: "b" as const, label: "요약", count: 3 },
  { key: "c" as const, label: "그래프" },
];

function Harness({ initial = "a", items = ITEMS }: { initial?: "a" | "b" | "c"; items?: typeof ITEMS }) {
  const [v, setV] = useState<"a" | "b" | "c">(initial);
  return (
    <>
      <Tabs id="t" items={items} value={v} onChange={setV} label="문서 종류" />
      <TabPanel id="t" tabKey={v}>내용 {v}</TabPanel>
    </>
  );
}

describe("시맨틱", () => {
  it("tablist/tab/tabpanel 과 aria-selected 를 낸다", () => {
    render(<Harness />);
    expect(screen.getByRole("tablist", { name: "문서 종류" })).toBeInTheDocument();
    expect(screen.getAllByRole("tab")).toHaveLength(3);
    expect(screen.getByRole("tab", { name: /회의록/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: /그래프/ })).toHaveAttribute("aria-selected", "false");
  });

  it("탭과 패널이 aria 로 이어진다 — 패널만 읽어도 어느 탭인지 안다", () => {
    render(<Harness />);
    const tab = screen.getByRole("tab", { name: /회의록/ });
    const panel = screen.getByRole("tabpanel");
    expect(tab).toHaveAttribute("aria-controls", panel.id);
    expect(panel).toHaveAttribute("aria-labelledby", tab.id);
  });
});

describe("로빙 tabindex", () => {
  it("Tab 순서에는 선택된 탭 하나만 남는다(탭마다 멈추지 않는다)", () => {
    render(<Harness initial="b" />);
    const tabs = screen.getAllByRole("tab");
    expect(tabs.filter((t) => t.getAttribute("tabindex") === "0")).toHaveLength(1);
    expect(screen.getByRole("tab", { name: /요약/ })).toHaveAttribute("tabindex", "0");
  });
});

describe("방향키", () => {
  it("→ 로 다음 탭이 선택되고 포커스도 따라간다", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("tab", { name: /회의록/ }));
    await user.keyboard("{ArrowRight}");
    const next = screen.getByRole("tab", { name: /요약/ });
    expect(next).toHaveAttribute("aria-selected", "true");
    expect(next).toHaveFocus();
  });

  it("마지막에서 → 는 처음으로 감는다 — 아무 일도 안 일어나면 '지원 없음'으로 읽힌다", async () => {
    const user = userEvent.setup();
    render(<Harness initial="c" />);
    await user.click(screen.getByRole("tab", { name: /그래프/ }));
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: /회의록/ })).toHaveAttribute("aria-selected", "true");
  });

  it("Home·End 로 양끝으로 간다", async () => {
    const user = userEvent.setup();
    render(<Harness initial="b" />);
    await user.click(screen.getByRole("tab", { name: /요약/ }));
    await user.keyboard("{End}");
    expect(screen.getByRole("tab", { name: /그래프/ })).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{Home}");
    expect(screen.getByRole("tab", { name: /회의록/ })).toHaveAttribute("aria-selected", "true");
  });

  it("비활성 탭은 건너뛴다", async () => {
    const user = userEvent.setup();
    const items = [ITEMS[0], { ...ITEMS[1], disabled: true }, ITEMS[2]];
    render(<Harness items={items} />);
    await user.click(screen.getByRole("tab", { name: /회의록/ }));
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: /그래프/ })).toHaveAttribute("aria-selected", "true");
  });
});

describe("개수 배지", () => {
  it("0 은 그리지 않는다 — '0'이 붙으면 결과가 있는 것처럼 보인다", () => {
    const onChange = vi.fn();
    render(
      <Tabs id="x" label="인스펙터"
        items={[{ key: "n", label: "관련 노트", count: 0 }, { key: "p", label: "진행 도우미", count: 2 }]}
        value="n" onChange={onChange} />,
    );
    expect(screen.getByRole("tab", { name: "관련 노트" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /진행 도우미\s*2/ })).toBeInTheDocument();
  });
});

describe("SegmentedControl", () => {
  it("시각만 다른 탭이다 — 같은 시맨틱을 낸다", () => {
    render(
      <SegmentedControl id="s" label="입력 방식" value="rec"
        items={[{ key: "rec", label: "실시간 녹음" }, { key: "up", label: "파일 업로드" }]}
        onChange={vi.fn()} />,
    );
    expect(screen.getByRole("tablist", { name: "입력 방식" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "실시간 녹음" })).toHaveAttribute("aria-selected", "true");
  });
});
