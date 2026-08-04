import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import PersonaLane from "./PersonaLane";
import type { Facilitation } from "../lib/types";

/**
 * 회의 진행 페르소나 레인·카드의 **표시 규칙**을 고정한다(PRD §19.1~§19.5).
 *
 * 이 기능의 위험은 정확도가 아니라 **방해**다. 회의 중 화면이 밀리거나, 끌 수 없거나,
 * 초안이 판정처럼 보이면 사용자는 기능을 끄고 다시 안 켠다(§18 업계 교훈). 그 규칙들은
 * 코드 리뷰로는 계속 새므로 테스트로 못박는다:
 *   1. 낼 게 없으면 **아무것도 렌더하지 않는다**(빈 바가 전사 영역을 잠식하지 않게).
 *   2. 카드는 **접힘이 기본** — 펼침은 사용자가 눌렀을 때만(자동 리플로우 0).
 *   3. '초안' 배지는 항상 붙는다 — 판정이 아니라 보조 제안이다.
 *   4. 끄는 버튼은 항상 보인다.
 *   5. 한도·예산으로 멈춘 사유는 조용히 사라지지 않는다(이 리포 반복 규칙).
 */

const item = (over: Partial<Facilitation> = {}): Facilitation => ({
  id: "fac_1",
  persona: "scribe",
  personaLabel: "📝 서기",
  level: 3,
  kind: "missing",
  risk: "low",
  text: "이 결정의 담당자와 기한이 비어 있습니다.",
  evidence: [],
  quote: "그럼 그렇게 하시죠",
  draft: true,
  searched: false,
  ...over,
});

describe("PersonaLane", () => {
  it("낼 것이 없으면 레인을 렌더하지 않는다", () => {
    const { container } = render(<PersonaLane items={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("개입이 오면 레인과 카드가 나타난다", () => {
    render(<PersonaLane items={[item()]} onMute={vi.fn()} />);
    expect(screen.getByText("페르소나")).toBeInTheDocument();
    expect(screen.getByText("📝 서기")).toBeInTheDocument();
    expect(screen.getByText(/담당자와 기한이 비어 있습니다/)).toBeInTheDocument();
  });

  it("끄는 버튼은 항상 보이고, 끄면 '생성을 멈췄다'로 바뀐다", async () => {
    const onMute = vi.fn();
    const { rerender } = render(<PersonaLane items={[item()]} onMute={onMute} />);
    await userEvent.click(screen.getByRole("button", { name: "이번 회의 끔" }));
    expect(onMute).toHaveBeenCalledOnce();

    rerender(<PersonaLane items={[]} muted onMute={onMute} />);
    // 문구가 실제 동작과 같아야 한다: 표시만 끄는 게 아니라 서버가 생성을 멈춘다.
    // "표시하지 않습니다"로만 적혀 있던 동안 서버는 계속 개입을 만들고 과금했다.
    expect(screen.getByText(/개입 생성을 멈췄습니다/)).toBeInTheDocument();
    // 끈 뒤에는 카드도, 다시 끄는 버튼도 없다
    expect(screen.queryByRole("button", { name: "이번 회의 끔" })).toBeNull();
  });

  it("한도·예산 사유는 회색 칩으로 남는다 — 조용히 꺼지지 않는다", () => {
    render(<PersonaLane items={[]} status={{
      kind: "budget", message: "이번 회의 개입 예산 12건을 모두 썼습니다",
    }} />);
    expect(screen.getByText(/개입 예산 12건을 모두 썼습니다/)).toBeInTheDocument();
  });

  it("대기(참견도 2)가 있으면 [지금 점검] 버튼과 건수가 뜬다", async () => {
    const onCheckNow = vi.fn();
    render(<PersonaLane items={[]} pending={2} onCheckNow={onCheckNow} />);
    await userEvent.click(screen.getByRole("button", { name: /지금 점검 2/ }));
    expect(onCheckNow).toHaveBeenCalledOnce();
  });

  it("pending 이 0이면 [지금 점검] 을 띄우지 않는다", () => {
    render(<PersonaLane items={[item()]} pending={0} onCheckNow={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /지금 점검/ })).toBeNull();
  });
});

describe("PersonaCard", () => {
  it("'초안' 배지는 항상 붙는다", () => {
    render(<PersonaLane items={[item()]} />);
    expect(screen.getByText("초안")).toBeInTheDocument();
  });

  it("접힘이 기본 — 근거·버튼은 펼친 뒤에만 보인다", async () => {
    const onJump = vi.fn();
    render(<PersonaLane items={[item({ span: { t0: 12.5, t1: 15.0 } })]}
                        onJump={onJump} onAck={vi.fn()} onDismiss={vi.fn()} />);
    expect(screen.queryByText(/발화 보기/)).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: /서기/ }));
    expect(screen.getByText(/그럼 그렇게 하시죠/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /발화 보기/ }));
    expect(onJump).toHaveBeenCalledWith({ t0: 12.5, t1: 15.0 });
  });

  it("확인·닫기는 각각 id 로 카드를 제거한다", async () => {
    const onAck = vi.fn();
    const onDismiss = vi.fn();
    render(<PersonaLane items={[item()]} onAck={onAck} onDismiss={onDismiss} />);
    await userEvent.click(screen.getByRole("button", { name: /서기/ }));
    await userEvent.click(screen.getByRole("button", { name: /확인/ }));
    expect(onAck).toHaveBeenCalledWith("fac_1");
    await userEvent.click(screen.getByRole("button", { name: /닫기/ }));
    expect(onDismiss).toHaveBeenCalledWith("fac_1");
  });

  it("위험도를 색만으로 전달하지 않는다 — 라벨을 병기한다(색약 대응)", () => {
    render(<PersonaLane items={[
      item({ id: "a", risk: "high", persona: "critic", personaLabel: "🧐 비판자",
             kind: "contrast" }),
    ]} />);
    expect(screen.getByText(/자료 대조 · 지적/)).toBeInTheDocument();
  });

  it("라이브 검증 없는 도메인·팩트체커 개입엔 '미검증' 배지를 붙인다", () => {
    render(<PersonaLane items={[
      item({ persona: "domain_expert", personaLabel: "🎓 도메인 전문가",
             kind: "question", searched: false }),
    ]} />);
    expect(screen.getByText(/미검증/)).toBeInTheDocument();
  });

  it("근거 없는 저위험 페르소나엔 '미검증' 배지를 붙이지 않는다", () => {
    render(<PersonaLane items={[item({ searched: false })]} />);
    expect(screen.queryByText(/미검증/)).toBeNull();
  });
});

/**
 * 중간 요약(🧾 summarizer) 카드와 [지금 정리] 버튼.
 *
 * 요약은 다른 카드와 성질이 다르다: 절이 4개라 한 줄 접힘으로는 쓸모가 없고, 버튼은
 * [지금 점검](과금 0)과 달리 **새 비용을 만든다**. 그 두 차이를 고정한다.
 */
const brief = (over: Partial<Facilitation> = {}): Facilitation => item({
  id: "brief_1",
  persona: "summarizer",
  personaLabel: "🧾 중간 요약",
  kind: "brief",
  text: "[결정] 9월 1일로 확정",
  brief: {
    points: ["출시 일정 논의"],
    decisions: ["9월 1일로 확정"],
    actions: [],
    open_questions: ["예산 승인?"],
  },
  onDemand: false,
  ...over,
});

describe("중간 요약 카드", () => {
  it("절 제목과 내용을 펼치지 않아도 보여준다", () => {
    render(<PersonaLane items={[brief()]} />);
    expect(screen.getByText("논점")).toBeInTheDocument();
    expect(screen.getByText(/출시 일정 논의/)).toBeInTheDocument();
    expect(screen.getByText("결정")).toBeInTheDocument();
    expect(screen.getByText("미결 질문")).toBeInTheDocument();
    expect(screen.getByText("중간 요약 · 정보")).toBeInTheDocument();
  });

  it("비어 있는 절은 렌더하지 않는다", () => {
    render(<PersonaLane items={[brief()]} />);
    expect(screen.queryByText("액션")).toBeNull();   // actions: []
  });

  it("brief 본문이 없으면 text 로 폴백한다(구버전 서버 호환)", () => {
    render(<PersonaLane items={[brief({ brief: undefined })]} />);
    expect(screen.getByText("[결정] 9월 1일로 확정")).toBeInTheDocument();
  });

  it("[지금 정리]로 만든 요약은 그 사실을 배지로 남긴다", () => {
    render(<PersonaLane items={[brief({ onDemand: true })]} />);
    expect(screen.getByText("지금 정리")).toBeInTheDocument();
  });
});

describe("[지금 정리] 버튼", () => {
  it("요약이 켜져 있으면 개입이 0건이어도 레인과 버튼이 보인다", async () => {
    const onBriefNow = vi.fn();
    render(<PersonaLane items={[]} briefOn onBriefNow={onBriefNow} />);
    const btn = screen.getByRole("button", { name: "지금 정리" });
    // 새 비용이 발생한다는 사실을 툴팁으로 알린다([지금 점검]과의 차이)
    expect(btn.getAttribute("title")).toMatch(/비용/);
    await userEvent.click(btn);
    expect(onBriefNow).toHaveBeenCalledOnce();
  });

  it("요약이 꺼져 있으면 버튼도 레인도 없다", () => {
    const { container } = render(
      <PersonaLane items={[]} briefOn={false} onBriefNow={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("생성 중에는 잠기고 '정리 중…'으로 바뀐다(연타로 과금되지 않게)", () => {
    render(<PersonaLane items={[]} briefOn briefBusy onBriefNow={vi.fn()} />);
    expect(screen.getByRole("button", { name: "정리 중…" })).toBeDisabled();
  });

  it("이번 회의 끔 상태에서는 버튼을 내린다", () => {
    render(<PersonaLane items={[]} briefOn muted onBriefNow={vi.fn()} onMute={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /지금 정리/ })).toBeNull();
  });
});
