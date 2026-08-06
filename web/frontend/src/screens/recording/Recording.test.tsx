import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

/**
 * 녹음 화면 스모크 + 표시 계약.
 *
 * Recorder 는 이 리포에서 가장 큰 컴포넌트인데 렌더 테스트가 없었다 — 오디오·WebSocket
 * 때문에 통째로 돌리기 어렵다는 이유였지만, 그 결과 **화면이 뜨는지조차** 자동으로
 * 확인되지 않았다. 여기서는 브라우저 API 를 최소한만 막고 유휴 화면을 렌더해,
 * 재구성 때 조용히 깨지는 것을 잡는다. 캡처·전사 자체는 여전히 실기 검증 영역이다.
 */

const api = vi.hoisted(() => ({
  createRealtimeWS: vi.fn(),
  backendAvailable: vi.fn(async () => false),
  createBackendRealtimeWS: vi.fn(),
  mirrorServerSession: vi.fn(),
  getCostRates: vi.fn(async () => null),
  getConfig: vi.fn(async () => ({ models: { stt: "gpt-4o-mini-transcribe" } })),
  getProfiles: vi.fn(async () => []),
  saveCompleteSession: vi.fn(),
  generateSummaryForSession: vi.fn(),
}));
vi.mock("../../lib/api", () => api);
vi.mock("@capacitor-community/keep-awake", () => ({ KeepAwake: { keepAwake: vi.fn(), allowSleep: vi.fn() } }));
vi.mock("@capacitor/haptics", () => ({
  Haptics: { impact: vi.fn(), notification: vi.fn() },
  ImpactStyle: { Heavy: "HEAVY" }, NotificationType: { Success: "SUCCESS" },
}));

import Recorder from "../../components/Recorder";

beforeEach(() => { vi.clearAllMocks(); });

describe("유휴 화면", () => {
  it("녹음 시작 버튼과 녹음 고지가 있다", async () => {
    render(<Recorder onComplete={vi.fn()} onExit={vi.fn()} />);
    expect(await screen.findByRole("button", { name: /녹음 시작/ })).toBeInTheDocument();
    // 이 도구는 몰래 녹음용이 아니다 — 진입점마다 최소 1회 보여야 한다.
    expect(screen.getByText(/참석자에게/)).toBeInTheDocument();
  });

  it("인-세션 설정 패널을 접었다 펼 수 있다(FR-REC-2)", async () => {
    const user = userEvent.setup();
    render(<Recorder onComplete={vi.fn()} onExit={vi.fn()} />);
    await screen.findByRole("button", { name: /녹음 시작/ });

    // 기본은 펼침 — 녹음 전에는 여기서 제목·참석자·모드를 정한다.
    expect(screen.getByLabelText("세션 제목")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "설정 숨기기" }));
    expect(screen.queryByLabelText("세션 제목")).toBeNull();
    await user.click(screen.getByRole("button", { name: "설정 보기" }));
    expect(screen.getByLabelText("세션 제목")).toBeInTheDocument();
  });

  it("음성 인식 선택지에 모델 ID 를 노출하지 않는다 (PRD §10 평문화)", async () => {
    render(<Recorder onComplete={vi.fn()} onExit={vi.fn()} />);
    const select = await screen.findByLabelText(/음성 인식/);
    const labels = Array.from(select.querySelectorAll("option")).map((o) => o.textContent || "");
    expect(labels.join(" ")).not.toMatch(/gpt-4o|whisper/i);
    expect(labels).toContain("고정확");
  });

  it("소리 잡는 법 3택이 라디오로 있다(체크박스 조합이 아니다)", async () => {
    render(<Recorder onComplete={vi.fn()} onExit={vi.fn()} />);
    await screen.findByRole("button", { name: /녹음 시작/ });
    // 에코 취소·자동 게인은 상황에 따라 정반대라 조합을 열면 모순 설정이 생긴다.
    expect(screen.getByRole("radio", { name: /내 마이크만/ })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /회의실 마이크/ })).toBeInTheDocument();
  });
});
