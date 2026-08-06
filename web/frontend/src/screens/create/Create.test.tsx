import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

/**
 * 새로 만들기 — **돈이 나가는 진입점**의 계약을 고정한다.
 *
 * 업로드와 텍스트는 서버 계약이 다르다: 업로드만 `confirm_required` 로 예상 금액을 주고,
 * 텍스트(`/api/process-text`)는 금액도 한도 검사도 없다. 화면이 그 차이를 정직하게
 * 드러내는지 — 없는 금액을 지어내지 않고, 그래도 사전 동의는 받는지 — 를 본다.
 */

const api = vi.hoisted(() => ({
  uploadFile: vi.fn(),
  confirmUpload: vi.fn(),
  cancelPendingUpload: vi.fn(),
  processTextInput: vi.fn(),
  getCostSummary: vi.fn(),
  getProfiles: vi.fn(),
}));
vi.mock("../../lib/api", () => api);

import UploadForm from "./UploadForm";
import TextForm from "./TextForm";

beforeEach(() => {
  vi.clearAllMocks();
  api.getProfiles.mockResolvedValue([]);
  api.getCostSummary.mockResolvedValue({
    ok: true, monthToDateUsd: 0.4, monthlyCapUsd: 5, perFileCapUsd: 0,
    months: [], byType: [], top: [], otherUsd: 0, otherByKind: {},
  });
  api.processTextInput.mockResolvedValue({ sessionId: "t1", status: "processing" });
  api.uploadFile.mockResolvedValue({
    pendingId: "p1", estimateUsd: 0.32, durationSec: 2520,
    monthToDateUsd: 0.4, monthlyCapUsd: 5,
  });
  api.confirmUpload.mockResolvedValue({ sessionId: "u1", status: "processing" });
});

describe("파일 업로드 — 서버가 준 금액으로 확인받는다", () => {
  const pick = async (user: ReturnType<typeof userEvent.setup>) => {
    const file = new File(["x"], "회의.m4a", { type: "audio/m4a" });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(input, file);
  };

  it("바로 처리하지 않고 예상 비용을 먼저 보여준다", async () => {
    const user = userEvent.setup();
    render(<UploadForm onComplete={vi.fn()} />);
    await pick(user);
    await user.click(screen.getByRole("button", { name: /분석 & 회의록 생성/ }));

    expect(api.uploadFile).toHaveBeenCalled();
    // 서버가 계산한 금액과 길이를 그대로 보여준다(화면이 다시 계산하지 않는다)
    expect(await screen.findByText("$0.32")).toBeInTheDocument();
    expect(screen.getByText("42분")).toBeInTheDocument();
    // 아직 처리는 시작되지 않았다
    expect(api.confirmUpload).not.toHaveBeenCalled();
  });

  it("[계속 처리]를 눌러야 실제 처리가 시작된다", async () => {
    const onComplete = vi.fn();
    const user = userEvent.setup();
    render(<UploadForm onComplete={onComplete} />);
    await pick(user);
    await user.click(screen.getByRole("button", { name: /분석 & 회의록 생성/ }));
    await user.click(await screen.findByRole("button", { name: "계속 처리" }));

    expect(api.confirmUpload).toHaveBeenCalledWith("p1");
    expect(onComplete).toHaveBeenCalledWith("u1");
  });

  it("취소하면 서버에 올라간 임시 파일을 정리한다", async () => {
    const user = userEvent.setup();
    render(<UploadForm onComplete={vi.fn()} />);
    await pick(user);
    await user.click(screen.getByRole("button", { name: /분석 & 회의록 생성/ }));
    await user.click(await screen.findByRole("button", { name: "취소" }));

    expect(api.cancelPendingUpload).toHaveBeenCalledWith("p1");
    expect(api.confirmUpload).not.toHaveBeenCalled();
  });

  it("파일이 없으면 시작 버튼이 잠겨 있다", () => {
    render(<UploadForm onComplete={vi.fn()} />);
    expect(screen.getByRole("button", { name: /분석 & 회의록 생성/ })).toBeDisabled();
  });
});

describe("텍스트 분석 — 금액이 없어도 사전 동의는 받는다", () => {
  it("서버가 금액을 안 주므로 지어내지 않고, 대신 규모와 월 지출을 보여준다", async () => {
    const user = userEvent.setup();
    render(<TextForm onComplete={vi.fn()} />);
    await user.type(screen.getByLabelText("본문"), "회의 메모입니다");
    await user.click(screen.getByRole("button", { name: /분석 & 문서 생성/ }));

    expect(await screen.findByText("미리 계산되지 않음")).toBeInTheDocument();
    expect(screen.getByText("본문 길이")).toBeInTheDocument();
    expect(screen.getByText("$0.40 / $5.00")).toBeInTheDocument();
    // 확인 전에는 호출하지 않는다
    expect(api.processTextInput).not.toHaveBeenCalled();
  });

  it("[분석 시작] 을 눌러야 서버를 부른다", async () => {
    const onComplete = vi.fn();
    const user = userEvent.setup();
    render(<TextForm onComplete={onComplete} />);
    await user.type(screen.getByLabelText("본문"), "회의 메모입니다");
    await user.click(screen.getByRole("button", { name: /분석 & 문서 생성/ }));
    await user.click(await screen.findByRole("button", { name: "분석 시작" }));

    expect(api.processTextInput).toHaveBeenCalled();
    expect(onComplete).toHaveBeenCalledWith("t1");
  });

  it("이 경로에는 번역이 없으므로 모드 요약에 번역을 적지 않는다", () => {
    // /api/process-text 는 language·translate 를 받지 않는다 — 적으면 거짓말이 된다.
    render(<TextForm onComplete={vi.fn()} />);
    // 모드 요약 칩만 본다 — 드롭다운 선택지 이름에는 "번역"이 들어갈 수 있다(모드 2번).
    expect(screen.queryByText(/^번역 /)).toBeNull();
    expect(screen.getByText(/^유형 /)).toBeInTheDocument();
  });

  it("[붙여넣기]는 본문 칸에 넣는다 — 화면의 다른 textarea 를 건드리지 않는다", async () => {
    // 종전 코드는 document.querySelector("textarea") 로 **문서 첫 textarea** 를 잡았다.
    // 이 화면에는 주제·본문 둘이 있어 붙여넣기가 엉뚱한 칸으로 갔다.
    const user = userEvent.setup();
    // navigator.clipboard 는 getter 전용이라 대입이 아니라 정의로 바꾼다.
    Object.defineProperty(navigator, "clipboard", {
      value: { readText: vi.fn(async () => "붙여넣은 내용") }, configurable: true,
    });
    render(<TextForm onComplete={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "붙여넣기" }));
    expect(await screen.findByDisplayValue("붙여넣은 내용")).toBe(screen.getByLabelText("본문"));
    expect(screen.getByLabelText(/주제/)).toHaveValue("");
  });
});
