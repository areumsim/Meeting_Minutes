import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

/**
 * 최상단 경고 배너 3종.
 *
 * 왜 화면 테스트가 필요한가 — 이 배너들은 **stderr 로는 사용자에게 도달하지 않는다**는
 * 사실 때문에 만든 것이다(포터블은 `pythonw.exe` 로 떠서 콘솔이 없다). 즉 이 배너가
 * 조용히 사라지면 그 결함이 원래 상태로 돌아간다 — 사용자는 이유 없이 저장이 안 되는
 * 앱을 보게 된다. 그래서 "뜬다/안 뜬다"가 회귀 대상이다.
 */

const api = vi.hoisted(() => ({
  getApiKey: vi.fn(() => "sk-test"),
  getConfig: vi.fn(async () => ({ api: { openai_api_key: "sk-test" } })),
  isPackagedMode: vi.fn(async () => true),
}));
vi.mock("./lib/api", () => api);
// 대시보드는 자체 조회가 있어 배너 검증과 무관하다.
vi.mock("./components/Dashboard", () => ({ default: () => <div>대시보드 자리</div> }));
vi.mock("./components/Onboarding", () => ({ default: () => null }));

import App from "./App";

function mockHealth(body: Record<string, unknown>) {
  vi.stubGlobal("fetch", vi.fn(async (input: any) => {
    if (String(input).includes("/api/health")) {
      return { ok: true, json: async () => ({ status: "ok", ffmpeg_available: true, ...body }) } as any;
    }
    return { ok: true, json: async () => ({}) } as any;
  }));
}

beforeEach(() => {
  vi.clearAllMocks();
  api.getApiKey.mockReturnValue("sk-test");
  api.getConfig.mockResolvedValue({ api: { openai_api_key: "sk-test" } });
  api.isPackagedMode.mockResolvedValue(true);
  localStorage.clear();
});

describe("정상 상태", () => {
  it("경고가 하나도 뜨지 않는다", async () => {
    mockHealth({});
    render(<App />);
    await screen.findByText("대시보드 자리");
    expect(screen.queryByText(/설정 파일\(config.json\)을 읽지 못했습니다/)).not.toBeInTheDocument();
    expect(screen.queryByText(/SSL 인증서 검증이 꺼져 있습니다/)).not.toBeInTheDocument();
    expect(screen.queryByText(/ffmpeg가 설치되어 있지 않습니다/)).not.toBeInTheDocument();
  });
});

describe("config 손상 배너", () => {
  it("사유와 함께 뜨고, **저장이 차단된 상태**임을 알린다", async () => {
    mockHealth({ config_error: "JSON 파싱 오류: line 3" });
    render(<App />);
    expect(await screen.findByText(/설정 파일\(config.json\)을 읽지 못했습니다/)).toBeInTheDocument();
    expect(screen.getByText(/JSON 파싱 오류: line 3/)).toBeInTheDocument();
    // 이 문구가 없으면 사용자는 "저장 버튼이 안 먹는다" 로만 경험한다.
    expect(screen.getByText(/설정 저장이 차단/)).toBeInTheDocument();
  });

  it("복구 버튼 2개를 준다", async () => {
    mockHealth({ config_error: "파일 읽기 실패" });
    render(<App />);
    expect(await screen.findByRole("button", { name: "마지막 정상 설정으로 되돌리기" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "보관하고 새로 시작" })).toBeInTheDocument();
  });

  it("복구는 확인을 받고 restore_backup 플래그를 서버에 넘긴다", async () => {
    mockHealth({ config_error: "파일 읽기 실패" });
    const confirmSpy = vi.fn(() => true);
    vi.stubGlobal("confirm", confirmSpy);
    vi.stubGlobal("alert", vi.fn());
    // 복구 성공 시 화면을 다시 읽는다 — jsdom 에서 실제 reload 는 되지 않으므로 목으로 막는다.
    const reload = vi.fn();
    Object.defineProperty(window, "location", { value: { ...window.location, reload }, writable: true });

    const fetchSpy = vi.fn(async (input: any, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/health")) {
        return { ok: true, json: async () => ({ status: "ok", ffmpeg_available: true, config_error: "파일 읽기 실패" }) } as any;
      }
      if (url.includes("/api/config/recover")) {
        return { ok: true, json: async () => ({ ok: true, message: "되돌렸습니다." }) } as any;
      }
      return { ok: true, json: async () => ({}) } as any;
    });
    vi.stubGlobal("fetch", fetchSpy);

    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "마지막 정상 설정으로 되돌리기" }));

    expect(confirmSpy).toHaveBeenCalled();
    const call = fetchSpy.mock.calls.find(c => String(c[0]).includes("/api/config/recover"));
    expect(call).toBeTruthy();
    expect(JSON.parse(String((call?.[1] as RequestInit)?.body))).toEqual({ restore_backup: true });
  });

  it("확인에서 취소하면 복구를 부르지 않는다", async () => {
    mockHealth({ config_error: "파일 읽기 실패" });
    vi.stubGlobal("confirm", vi.fn(() => false));
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "보관하고 새로 시작" }));
    const calls = (globalThis.fetch as any).mock.calls.map((c: any[]) => String(c[0]));
    expect(calls.some((u: string) => u.includes("/api/config/recover"))).toBe(false);
  });
});

describe("SSL 검증 꺼짐 배너", () => {
  it("꺼져 있으면 위험과 되돌리는 방법을 함께 알린다", async () => {
    mockHealth({ ssl_insecure: true });
    render(<App />);
    expect(await screen.findByText(/SSL 인증서 검증이 꺼져 있습니다/)).toBeInTheDocument();
    // 위험만 말하고 방법을 안 주면 사용자는 그대로 둔다.
    expect(screen.getByText(/Windows 인증서 저장소를 신뢰/)).toBeInTheDocument();
  });

  it("켜져 있으면 뜨지 않는다(기본값이라 상시 노출되면 의미를 잃는다)", async () => {
    mockHealth({ ssl_insecure: false });
    render(<App />);
    await screen.findByText("대시보드 자리");
    expect(screen.queryByText(/SSL 인증서 검증이 꺼져 있습니다/)).not.toBeInTheDocument();
  });
});

describe("모바일 [더보기] 시트 — 모든 화면에 도달할 수 있어야 한다", () => {
  /**
   * 예전 탭바(6개)에는 회의 준비·회의 비서·지식그래프·도움말이 없어서 모바일에서
   * 진입 경로가 0이었다 — 그 화면들을 여는 허브(도움말)조차 탭에 없었다.
   * PRD §17 확정 "v1 범위 = 10화면 전부"는 모바일도 포함한다.
   */
  // jsdom 은 미디어쿼리를 적용하지 않아 데스크톱 사이드바의 같은 라벨 버튼도 DOM 에
  // 함께 있다 — 시트(dialog) 안으로 범위를 좁혀서 조회한다.
  it("더보기를 누르면 탭에 없는 화면 6개가 전부 뜬다", async () => {
    mockHealth({});
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText("대시보드 자리");
    await user.click(screen.getByRole("button", { name: /더보기/ }));
    // 시트는 접근 가능한 대화상자다(Escape·포커스 트랩은 Modal 계약 테스트가 고정)
    const sheet = within(screen.getByRole("dialog"));
    for (const label of ["텍스트 분석", "회의 준비", "회의 비서", "지식그래프", "도움말", "설정"]) {
      expect(sheet.getByRole("button", { name: label })).toBeInTheDocument();
    }
  });

  it("항목을 고르면 시트가 닫히고 그 화면으로 이동한다", async () => {
    mockHealth({});
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText("대시보드 자리");
    await user.click(screen.getByRole("button", { name: /더보기/ }));
    const sheet = within(screen.getByRole("dialog"));
    await user.click(sheet.getByRole("button", { name: "도움말" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    // 도움말 화면이 실제로 로드된다(지연 로드 — 화면 고유 문구로 확인)
    expect(await screen.findByText(/사용법|자주 묻는/)).toBeInTheDocument();
  });
});

describe("백엔드가 없을 때(단독 모드)", () => {
  it("health 조회 실패가 화면을 깨뜨리지 않는다", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("no backend"); }));
    api.isPackagedMode.mockResolvedValue(false);
    render(<App />);
    // 배너 없이 정상 렌더돼야 한다 — 모바일 단독 모드가 이 경로다.
    expect(await screen.findByText("대시보드 자리")).toBeInTheDocument();
  });
});
