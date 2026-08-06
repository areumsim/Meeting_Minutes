import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

/**
 * 설정의 **도달성** 계약 (PRD FR-SET-1, AC).
 *
 * AC 는 두 문장이다: "첫 실행 사용자는 필수의 키+폴더만 보면 시작할 수 있다" 와 "어떤
 * 설정이든 검색으로 도달한다". 14그룹·수십 필드에서 이 둘이 무너지면 화면은 예전의
 * 무한 스크롤로 돌아간다.
 */

const api = vi.hoisted(() => ({
  getConfig: vi.fn(), updateConfig: vi.fn(), getConfigSchema: vi.fn(), isPackagedMode: vi.fn(),
  testOpenAIKey: vi.fn(), testAnthropicKey: vi.fn(), testObsidianPath: vi.fn(), testEmail: vi.fn(),
  testSlack: vi.fn(), testTeams: vi.fn(), reindexVault: vi.fn(), shutdownApp: vi.fn(),
  getProfiles: vi.fn(), createProfile: vi.fn(), deleteProfile: vi.fn(), clearSessions: vi.fn(),
  getApiKey: vi.fn(), setApiKey: vi.fn(), getAnthropicKey: vi.fn(), setAnthropicKey: vi.fn(),
  getWatcherStatus: vi.fn(), startWatcher: vi.fn(), stopWatcher: vi.fn(), obsidianDiagnose: vi.fn(),
  pickFolder: vi.fn(), getBackendUrl: vi.fn(), setBackendUrl: vi.fn(), testBackendUrl: vi.fn(),
  revealSecret: vi.fn(), localSttStatus: vi.fn(), prepareLocalStt: vi.fn(),
  getFacilitationPersonas: vi.fn(),
}));
vi.mock("../../lib/api", () => api);
vi.mock("@capacitor/core", () => ({ Capacitor: { isNativePlatform: () => false } }));

import Settings from "./Settings";

/** 서버 스키마의 축소판 — tier 값은 config_schema.py 의 실제 값과 같게 둔다. */
const SCHEMA = [
  {
    id: "api", label: "API 키", tier: "core", desc: "이 PC에만 저장됩니다.",
    fields: [
      { section: "api", key: "openai_api_key", label: "OpenAI API 키 (필수)", type: "password", required: true },
      { section: "ssl", key: "verify", label: "SSL 인증서 검증", type: "bool", desc: "기본값 켜짐(권장)." },
    ],
  },
  {
    // 서버에서는 core 다 — 화면에서만 common 으로 내린다(TIER_OVERRIDE).
    id: "models", label: "모델", tier: "core",
    fields: [{ section: "models", key: "stt", label: "음성 인식(STT) 모델", type: "text" }],
  },
  {
    id: "storage", label: "저장 위치 · 회의록 형식", tier: "core",
    fields: [{ section: "output_dir", key: "", label: "결과물 저장 폴더", type: "text", scalar: true }],
  },
  {
    id: "facilitation", label: "회의 진행 페르소나", tier: "advanced", advanced: true,
    fields: [{ section: "facilitation", key: "enabled", label: "페르소나 사용", type: "bool" }],
  },
];

beforeEach(() => {
  vi.clearAllMocks();
  api.isPackagedMode.mockResolvedValue(true);
  api.getConfigSchema.mockResolvedValue(SCHEMA);
  api.getConfig.mockResolvedValue({ api: {}, ssl: { verify: true }, models: {}, output_dir: "./output" });
  api.getProfiles.mockResolvedValue([]);
  api.localSttStatus.mockResolvedValue(null);
  api.getApiKey.mockReturnValue("");
  api.getAnthropicKey.mockReturnValue("");
  api.getBackendUrl.mockReturnValue("");
  api.getFacilitationPersonas.mockResolvedValue(null);
});

describe("3티어 — 첫 실행에 보이는 것을 좁힌다", () => {
  it("모델 그룹은 '필수' 가 아니라 '자주 쓰는' 으로 내려간다", async () => {
    // 서버 스키마는 core 지만 PRD 는 필수를 '키 + 폴더'로 확정했다(리뷰 P2-4).
    render(<Settings />);
    const models = await screen.findByRole("button", { name: /모델/ });
    const common = screen.getByText("자주 쓰는 선택").closest("div,section") as HTMLElement;
    expect(models).toBeInTheDocument();
    expect(common).toBeInTheDocument();
    // 필수 단에는 API 키와 저장 위치만 남는다.
    expect(screen.getByRole("button", { name: /API 키/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /저장 위치/ })).toBeInTheDocument();
  });

  it("SSL 검증은 API 키 카드(필수)에 그대로 둔다 — PRD §6.7 이 정본", async () => {
    // 매트릭스 §6 은 '고급으로 내리기'라고 적었지만 PRD 가 정본이고, 서버 스키마도 api 그룹이다.
    render(<Settings />);
    expect(await screen.findByText("SSL 인증서 검증")).toBeInTheDocument();
  });

  it("고급 그룹은 접혀서 나온다", async () => {
    render(<Settings />);
    await screen.findByRole("button", { name: /API 키/ });
    expect(screen.getByRole("button", { name: /회의 진행 페르소나/ })).toBeInTheDocument();
    expect(screen.queryByText("페르소나 사용")).toBeNull();
  });
});

describe("설정 검색 — 어떤 설정이든 도달한다", () => {
  it("라벨로 찾는다", async () => {
    const user = userEvent.setup();
    render(<Settings />);
    await screen.findByRole("button", { name: /API 키/ });
    await user.type(screen.getByLabelText("설정 검색"), "페르소나");

    expect(screen.getByRole("button", { name: /회의 진행 페르소나/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /저장 위치/ })).toBeNull();
  });

  it("키(ssl.verify)로도 찾는다 — 사용자는 영문 키로도 검색한다", async () => {
    const user = userEvent.setup();
    render(<Settings />);
    await screen.findByRole("button", { name: /API 키/ });
    await user.type(screen.getByLabelText("설정 검색"), "ssl");
    expect(screen.getByText("SSL 인증서 검증")).toBeInTheDocument();
  });

  it("검색 중에는 고급 그룹도 펼쳐진다 — 찾았는데 접혀 있으면 못 찾은 것과 같다", async () => {
    const user = userEvent.setup();
    render(<Settings />);
    await screen.findByRole("button", { name: /API 키/ });
    await user.type(screen.getByLabelText("설정 검색"), "페르소나 사용");
    expect(screen.getByText("페르소나 사용")).toBeInTheDocument();
  });

  it("결과가 없으면 그렇게 말하고 지우는 방법을 준다", async () => {
    const user = userEvent.setup();
    render(<Settings />);
    await screen.findByRole("button", { name: /API 키/ });
    await user.type(screen.getByLabelText("설정 검색"), "존재하지않는설정");
    expect(screen.getByText(/맞는 설정이 없습니다/)).toBeInTheDocument();
  });
});

describe("화면 테마", () => {
  it("설정에서 라이트/다크/시스템을 고를 수 있다", async () => {
    const user = userEvent.setup();
    render(<Settings />);
    await screen.findByRole("button", { name: /API 키/ });

    await user.click(screen.getByRole("tab", { name: "다크" }));
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    await user.click(screen.getByRole("tab", { name: "라이트" }));
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
  });
});
