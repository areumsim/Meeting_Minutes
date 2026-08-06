import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CostConfirmModal } from "./CostConfirmModal";
import { CostBreakdown, sessionCostItems } from "./CostMeter";

/**
 * 과금 전 확인의 두 계약을 고정한다.
 *
 *  1. 한도를 넘으면 시작 버튼이 **잠긴다**. 서버가 최종 판정이지만, 눌러서 400 을 받는
 *     경험은 "왜 안 되는지 모르겠다"로 남는다.
 *  2. 서버가 금액을 주지 않는 경로(텍스트·볼트 오디오·계획 자동화)에서 **프런트가 금액을
 *     지어내지 않는다**. 단가 표를 두 번째로 만들면 서버 단가가 바뀔 때 화면만 옛 숫자를
 *     말한다 — 이 리포가 반복해서 없애 온 형태다.
 */

const base = { what: "이 파일을 처리하면 비용이 듭니다.", onCancel: vi.fn(), onConfirm: vi.fn() };

describe("한도 판정", () => {
  it("예상 + 이번 달 지출이 한도를 넘으면 [계속 처리]가 잠기고 사유를 알린다", () => {
    render(<CostConfirmModal {...base} estimateUsd={3} monthToDateUsd={4} monthlyCapUsd={5} />);
    expect(screen.getByRole("button", { name: "계속 처리" })).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent(/한도를 넘습니다/);
  });

  it("한도 안이면 잠기지 않는다", () => {
    render(<CostConfirmModal {...base} estimateUsd={0.3} monthToDateUsd={0.4} monthlyCapUsd={5} />);
    expect(screen.getByRole("button", { name: "계속 처리" })).toBeEnabled();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("한도가 0(무제한)이면 판정하지 않는다", () => {
    render(<CostConfirmModal {...base} estimateUsd={99} monthToDateUsd={99} monthlyCapUsd={0} />);
    expect(screen.getByRole("button", { name: "계속 처리" })).toBeEnabled();
  });

  it("금액을 모르는 경로에서도 이미 한도를 넘겼으면 막는다", () => {
    render(<CostConfirmModal {...base} monthToDateUsd={5} monthlyCapUsd={5} />);
    expect(screen.getByRole("button", { name: "계속 처리" })).toBeDisabled();
  });
});

describe("금액 미제공 변형", () => {
  it("서버가 금액을 안 주면 '$0' 이 아니라 '미리 계산되지 않음' 으로 적는다", () => {
    render(<CostConfirmModal {...base} monthToDateUsd={0.4} monthlyCapUsd={5} />);
    expect(screen.getByText("미리 계산되지 않음")).toBeInTheDocument();
    // 빈칸·0 을 만들지 않는다 — 사용자는 그것을 '무료'로 읽는다.
    expect(screen.queryByText("$0.00")).toBeNull();
  });

  it("금액 대신 규모(대상 건수)를 보여줄 수 있다", () => {
    render(<CostConfirmModal {...base} targets={[{ label: "처리 대상", value: "4건(미처리 2)" }]} />);
    expect(screen.getByText("처리 대상")).toBeInTheDocument();
    expect(screen.getByText("4건(미처리 2)")).toBeInTheDocument();
  });

  it("서버가 준 금액은 그대로 보여준다", () => {
    render(<CostConfirmModal {...base} estimateUsd={0.32} durationSec={2520} />);
    expect(screen.getByText("$0.32")).toBeInTheDocument();
    expect(screen.getByText("42분")).toBeInTheDocument();
  });
});

describe("취소 경로", () => {
  it("Escape 로 닫힌다 — 백드롭 클릭이 막힌 모달의 유일한 키보드 탈출구", async () => {
    const onCancel = vi.fn();
    const user = userEvent.setup();
    render(<CostConfirmModal {...base} onCancel={onCancel} />);
    await user.keyboard("{Escape}");
    expect(onCancel).toHaveBeenCalled();
  });
});

describe("CostBreakdown — 항목은 서버 주도", () => {
  it("모르는 kind 가 와도 라벨 폴백으로 그린다(화면이 종류를 하드코딩하지 않는다)", () => {
    const items = sessionCostItems({
      stt: 0.12, stt_model: "gpt-4o-transcribe", translate: 0.04, minutes: 0.13,
      actual_kinds: ["facilitation", "some_new_kind_2027"],
      facilitation: 0.02, some_new_kind_2027: 0.01,
    });
    render(<CostBreakdown items={items} total={0.32} />);
    // 알려진 kind 는 한국어 라벨로
    expect(screen.getByText(/회의 진행 페르소나/)).toBeInTheDocument();
    // 모르는 kind 도 빠지지 않고 원문 키로 남는다 — 조용히 사라지면 과금이 감춰진다
    expect(screen.getByText("some_new_kind_2027")).toBeInTheDocument();
    expect(screen.getByText("$0.0100")).toBeInTheDocument();
  });

  it("2단계 보정이 0이면 그 줄을 만들지 않는다(없는 단계를 암시하지 않게)", () => {
    const items = sessionCostItems({ stt: 0.1, stt_revise: 0, translate: 0, minutes: 0.1 });
    expect(items.find((i) => i.key === "stt_revise")).toBeUndefined();
  });

  it("실측 항목은 그렇다고 표시한다", () => {
    const items = sessionCostItems({ stt: 0.1, minutes: 0.1, actual_kinds: ["facilitation"], facilitation: 0.02 });
    render(<CostBreakdown items={items} total={0.22} />);
    expect(screen.getByText("실측")).toBeInTheDocument();
  });
});
