import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

/**
 * 회의 상세의 **본문 우선** 규칙과 상태별 출구를 고정한다(PRD §6.4).
 *
 * AC 는 "본문이 화면 세로의 대부분을 차지한다"이다. 종전에는 회의록 아래에 재생성 폼이
 * (제목 + 설명 + 큰 textarea) 늘 펼쳐져 있어 본문을 밀어냈다. 재생성은 없애는 게 아니라
 * 접는 것이고, 접힌 상태가 기본이라는 것이 여기서 무너지면 조용히 원래대로 돌아간다.
 */

const api = vi.hoisted(() => ({
  getSession: vi.fn(),
  getSessionStatus: vi.fn(),
  generateSummaryForSession: vi.fn(),
  getTargetEmail: vi.fn(),
  getSessionGraph: vi.fn(),
  getNodeNeighbors: vi.fn(),
  getUploadProgress: vi.fn(),
  getSessionCost: vi.fn(),
  cancelUpload: vi.fn(),
  mirrorServerSession: vi.fn(),
  retrySession: vi.fn(),
  getSessionRelatedNotes: vi.fn(),
}));
vi.mock("../../lib/api", () => api);
vi.mock("@capacitor/share", () => ({ Share: { share: vi.fn() } }));
vi.mock("../../ui/GraphView", () => ({ default: () => null, GraphNodeList: () => null }));

import Detail from "./Detail";

const doc = (type: string, content = "본문") =>
  ({ id: type, session_id: "s1", type, content, format: "markdown" });

function mockSession(over: Record<string, unknown> = {}, documents = [doc("minutes")]) {
  api.getSession.mockResolvedValue({
    session: {
      id: "s1", title: "주간 회의", type: "meeting", status: "completed",
      created_at: "2026-08-05T10:00:00", duration_sec: 1500, translate: 0, ...over,
    },
    segments: [], documents,
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  mockSession();
  api.getSessionStatus.mockResolvedValue({ status: "completed" });
  api.getTargetEmail.mockReturnValue("");
  api.getSessionGraph.mockResolvedValue(null);
  api.getSessionCost.mockResolvedValue(null);
  api.getSessionRelatedNotes.mockResolvedValue({ notes: [], cross: [] });
  api.retrySession.mockResolvedValue({ sessionId: "s1", status: "processing", reusedStt: true });
  api.cancelUpload.mockResolvedValue({ ok: true });
});

describe("재생성 — 회의록·요약 탭에서만, 접힘이 기본", () => {
  it("기본 상태에서 재생성 입력 줄이 본문을 밀지 않는다", async () => {
    render(<Detail id="s1" onBack={vi.fn()} />);
    expect(await screen.findByRole("button", { name: /노트 반영해 재생성/ })).toBeInTheDocument();
    expect(screen.queryByLabelText("재생성 지시")).toBeNull();
  });

  it("눌러야 얇은 입력 줄이 열린다", async () => {
    const user = userEvent.setup();
    render(<Detail id="s1" onBack={vi.fn()} />);
    await user.click(await screen.findByRole("button", { name: /노트 반영해 재생성/ }));
    expect(screen.getByLabelText("재생성 지시")).toBeInTheDocument();
    // 지시가 비면 실행할 수 없다 — 빈 재생성은 돈만 쓰고 같은 문서를 만든다.
    expect(screen.getByRole("button", { name: "재생성" })).toBeDisabled();
  });

  it("스크립트 탭에는 재생성을 두지 않는다(원본과 같은 범위)", async () => {
    const user = userEvent.setup();
    mockSession({}, [doc("minutes"), doc("script")]);
    render(<Detail id="s1" onBack={vi.fn()} />);
    await user.click(await screen.findByRole("tab", { name: /스크립트/ }));
    expect(screen.queryByRole("button", { name: /노트 반영해 재생성/ })).toBeNull();
  });
});

describe("상태별 출구", () => {
  it("오류면 사유와 [다시 시도]를 주고, 재과금 여부를 함께 알린다", async () => {
    const user = userEvent.setup();
    mockSession({ status: "error", error_detail: "STT 호출 실패(429)" });
    render(<Detail id="s1" onBack={vi.fn()} />);

    expect(await screen.findByText("STT 호출 실패(429)")).toBeInTheDocument();
    expect(screen.getByText(/비용을 다시 청구하지 않습니다/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "다시 시도" }));
    expect(api.retrySession).toHaveBeenCalledWith("s1");
  });

  it("처리 중이면 진행바와 [처리 취소]가 있다", async () => {
    api.getUploadProgress.mockResolvedValue({ found: true, percent: 40, stage: "STT", elapsed: 30 });
    mockSession({ status: "processing" });
    render(<Detail id="s1" onBack={vi.fn()} />);
    expect(await screen.findByRole("button", { name: "처리 취소" })).toBeInTheDocument();
  });

  it("세션을 못 찾으면 막다른 화면 대신 다시 불러오기를 준다", async () => {
    api.getSession.mockRejectedValue(new Error("not found"));
    api.mirrorServerSession.mockResolvedValue(false);
    render(<Detail id="s1" onBack={vi.fn()} />);
    expect(await screen.findByText("회의를 찾을 수 없습니다")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "다시 불러오기" })).toBeInTheDocument();
  });
});

describe("벤더 전환 고지", () => {
  it("대체 처리된 회의는 어디로 음성이 갔는지 밝힌다", async () => {
    const user = userEvent.setup();
    mockSession({ stt_fallback_used: 1, stt_provider: "groq" });
    render(<Detail id="s1" onBack={vi.fn()} />);

    // 상시 배너가 아니라 칩이다 — 누르면 내용이 나온다.
    await user.click(await screen.findByRole("button", { name: /대체 처리/ }));
    expect(screen.getByText(/groq/)).toBeInTheDocument();
    expect(screen.getByText(/회의록 출처에도 같은 내용이 기록됩니다/)).toBeInTheDocument();
  });

  it("폴백이 없으면 칩도 없다", async () => {
    render(<Detail id="s1" onBack={vi.fn()} />);
    await screen.findByRole("tab", { name: /회의록/ });
    expect(screen.queryByRole("button", { name: /대체 처리/ })).toBeNull();
  });
});

describe("비용 내역", () => {
  it("완료 세션은 서버가 준 항목을 그대로 펼친다(kind 를 화면이 정하지 않는다)", async () => {
    const user = userEvent.setup();
    api.getSessionCost.mockResolvedValue({
      ok: true, total: 0.31, stt: 0.12, stt_model: "gpt-4o-transcribe", translate: 0.04,
      minutes: 0.13, actual_kinds: ["facilitation"], facilitation: 0.02,
    });
    render(<Detail id="s1" onBack={vi.fn()} />);
    await user.click(await screen.findByRole("button", { name: /\$0\.310/ }));
    expect(screen.getByText(/회의 진행 페르소나/)).toBeInTheDocument();
    expect(screen.getByText("실측")).toBeInTheDocument();
  });
});
