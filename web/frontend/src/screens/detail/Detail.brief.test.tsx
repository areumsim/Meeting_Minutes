import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

/**
 * 회의 상세의 '중간 정리' 탭.
 *
 * 회의 중 화면에 떴던 자동 요약은 녹음이 끝나면 사라졌다 — 관찰 로그의 span 은 500자
 * 컷이라 문서로 복원할 수 없었고, 조회 API 도 없었다. 이제 종료 시 `brief` 문서로
 * 저장되며 이 탭이 그것을 보여준다. 고정할 계약은 두 가지다: **문서가 있으면 보이고,
 * 없으면 탭 자체가 생기지 않는다**(페르소나를 끈 회의에 빈 탭이 뜨면 안 된다).
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
vi.mock("motion/react", () => ({
  motion: new Proxy({}, { get: () => (p: any) => <div {...p} /> }),
}));

import Detail from "./Detail";

const BRIEF_MD = "# 중간 정리 (회의 중 자동 요약)\n\n## 1. 자동 정리 · 10:00\n**결정**\n- 9월 1일 확정\n";

function mockSession(documents: any[]) {
  api.getSession.mockResolvedValue({
    session: { id: "s1", title: "주간 회의", type: "meeting", status: "completed",
               created_at: "2026-08-05T10:00:00", duration_sec: 1500 },
    segments: [],
    documents,
  });
  api.getSessionStatus.mockResolvedValue({ status: "completed" });
  api.getTargetEmail.mockResolvedValue("");
  api.getSessionGraph.mockResolvedValue(null);
  api.getSessionCost.mockResolvedValue(null);
  api.getSessionRelatedNotes.mockResolvedValue({ notes: [], cross: [] });
}

const doc = (type: string, content = "본문") =>
  ({ id: type, session_id: "s1", type, content, format: "markdown" });

describe("회의 상세 — 중간 정리 탭", () => {
  beforeEach(() => vi.clearAllMocks());

  it("brief 문서가 있으면 탭이 뜨고 내용을 보여준다", async () => {
    const user = userEvent.setup();
    mockSession([doc("minutes"), doc("brief", BRIEF_MD)]);
    render(<Detail id="s1" onBack={vi.fn()} />);

    const tab = await screen.findByRole("tab", { name: /중간 정리/ });
    await user.click(tab);
    await waitFor(() =>
      expect(screen.getByText(/9월 1일 확정/)).toBeInTheDocument());
  });

  it("brief 문서가 없으면 탭 자체가 생기지 않는다", async () => {
    mockSession([doc("minutes")]);
    render(<Detail id="s1" onBack={vi.fn()} />);
    await screen.findByRole("tab", { name: /회의록/ });
    expect(screen.queryByRole("tab", { name: /중간 정리/ })).toBeNull();
  });
});
