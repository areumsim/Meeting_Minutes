import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

/**
 * 라이브러리의 삭제·휴지통 흐름.
 *
 * 왜 이 테스트가 필요한가 — 실기 검증 중 "휴지통이 0개로 나온다"를 **버그로 오해**했다.
 * 실제로는 조회가 끝나기 전에 화면을 읽은 것이었고, 판별에 여러 번의 재현이 필요했다.
 * 이런 화면-상태 동기화는 컴포넌트 테스트가 몇 초에 답을 준다.
 *
 * `lib/api` 를 통째로 목으로 대체한다 — 여기서 검증하려는 것은 네트워크가 아니라
 * "무엇을 눌렀을 때 어떤 API 를 부르고 화면이 어떻게 바뀌는가"다.
 */

const api = vi.hoisted(() => ({
  getSessions: vi.fn(),
  getTrash: vi.fn(),
  deleteSession: vi.fn(),
  restoreSession: vi.fn(),
  purgeSession: vi.fn(),
  clearSessions: vi.fn(),
  retrySession: vi.fn(),
  cancelUpload: vi.fn(),
}));

vi.mock("../../lib/api", () => api);
// 비용 요약은 자체적으로 조회하므로 화면 밖 관심사다.
vi.mock("./CostSummaryCard", () => ({ default: () => null }));

import Library from "./Library";

const session = (over: Partial<any> = {}) => ({
  id: "s1", title: "주간 회의", type: "meeting", status: "completed",
  created_at: "2026-08-03T10:00:00", date: "2026-08-03T10:00:00",
  duration_sec: 1800, translate: 0, ...over,
});

function renderLibrary() {
  return render(<Library onSelectSession={vi.fn()} onNewUpload={vi.fn()} onNewRecord={vi.fn()} />);
}

beforeEach(() => {
  vi.clearAllMocks();
  api.getSessions.mockResolvedValue([session()]);
  api.getTrash.mockResolvedValue([]);
  api.deleteSession.mockResolvedValue({ success: true, restorable: true });
  api.restoreSession.mockResolvedValue({ success: true });
  api.purgeSession.mockResolvedValue({ message: "결과 폴더를 휴지통으로 보냈습니다." });
  api.clearSessions.mockResolvedValue({ success: true });
  api.retrySession.mockResolvedValue({ sessionId: "s1", status: "processing", reusedStt: true });
  api.cancelUpload.mockResolvedValue({ ok: true, message: "취소를 요청했습니다." });
  vi.stubGlobal("confirm", vi.fn(() => true));
});

describe("목록 표시", () => {
  it("세션을 보여준다", async () => {
    renderLibrary();
    expect(await screen.findByText("주간 회의")).toBeInTheDocument();
    expect(screen.getByText("회의 1건")).toBeInTheDocument();
  });

  it("조회 실패를 '세션 없음' 과 구분한다", async () => {
    // 과거엔 console.error 만 하고 빈 상태를 그려서, 백엔드가 죽은 것과 회의가 하나도
    // 없는 것이 화면상 똑같았다.
    api.getSessions.mockRejectedValue(new Error("boom"));
    renderLibrary();
    expect(await screen.findByText(/불러올 수 없습니다/)).toBeInTheDocument();
  });
});

describe("삭제 → 휴지통 → 되돌리기", () => {
  it("삭제하면 되돌릴 수 있다고 알리고 [되돌리기] 를 준다", async () => {
    const user = userEvent.setup();
    renderLibrary();
    await user.click(await screen.findByRole("button", { name: /휴지통으로 보내기/ }));

    expect(api.deleteSession).toHaveBeenCalledWith("s1");
    expect(await screen.findByText(/휴지통으로 보냈습니다/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "되돌리기" })).toBeInTheDocument();
  });

  it("확인 대화상자에서 취소하면 삭제하지 않는다", async () => {
    vi.stubGlobal("confirm", vi.fn(() => false));
    const user = userEvent.setup();
    renderLibrary();
    await user.click(await screen.findByRole("button", { name: /휴지통으로 보내기/ }));
    expect(api.deleteSession).not.toHaveBeenCalled();
  });

  it("서버가 되돌릴 수 없다고 하면 [되돌리기] 를 띄우지 않는다", async () => {
    // 단독 모드처럼 휴지통이 없는 경로에서 되돌리기 버튼을 보여주면 눌러도 실패만 본다.
    api.deleteSession.mockResolvedValue({ success: true, restorable: false });
    const user = userEvent.setup();
    renderLibrary();
    await user.click(await screen.findByRole("button", { name: /휴지통으로 보내기/ }));
    expect(await screen.findByText("삭제했습니다.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "되돌리기" })).not.toBeInTheDocument();
  });

  it("[되돌리기] 를 누르면 복원 API 를 부르고 목록을 다시 읽는다", async () => {
    const user = userEvent.setup();
    renderLibrary();
    await user.click(await screen.findByRole("button", { name: /휴지통으로 보내기/ }));
    await user.click(await screen.findByRole("button", { name: "되돌리기" }));

    expect(api.restoreSession).toHaveBeenCalledWith("s1");
    expect(await screen.findByText("되돌렸습니다.")).toBeInTheDocument();
  });

  it("복원 실패 사유를 화면에 보여준다", async () => {
    api.restoreSession.mockRejectedValue(new Error("휴지통에 그 세션이 없습니다."));
    const user = userEvent.setup();
    renderLibrary();
    await user.click(await screen.findByRole("button", { name: /휴지통으로 보내기/ }));
    await user.click(await screen.findByRole("button", { name: "되돌리기" }));
    expect(await screen.findByText("휴지통에 그 세션이 없습니다.")).toBeInTheDocument();
  });
});

describe("휴지통 화면", () => {
  it("[휴지통] 토글이 휴지통 목록을 읽어 온다", async () => {
    api.getTrash.mockResolvedValue([session({ id: "d1", title: "지운 회의" })]);
    const user = userEvent.setup();
    renderLibrary();
    await screen.findByText("주간 회의");

    await user.click(screen.getByRole("button", { name: "휴지통" }));

    // 실기에서 이 지점을 "0개 버그" 로 오해했다 — 조회 완료를 기다려야 한다.
    expect(await screen.findByText("지운 회의")).toBeInTheDocument();
    expect(screen.getByText("휴지통 1건")).toBeInTheDocument();
    expect(api.getTrash).toHaveBeenCalled();
  });

  it("휴지통에서는 [전체 삭제] 를 숨긴다(오조작 방지)", async () => {
    const user = userEvent.setup();
    renderLibrary();
    await screen.findByText("주간 회의");
    expect(screen.getByRole("button", { name: "전체 삭제" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "휴지통" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "전체 삭제" })).not.toBeInTheDocument());
  });

  it("휴지통 항목은 되돌리기·완전삭제만 준다", async () => {
    api.getTrash.mockResolvedValue([session({ id: "d1", title: "지운 회의" })]);
    const user = userEvent.setup();
    renderLibrary();
    await screen.findByText("주간 회의");
    await user.click(screen.getByRole("button", { name: "휴지통" }));
    await screen.findByText("지운 회의");

    // 항목 단위로 정확히 지정한다 — AnimatePresence 의 퇴장 애니메이션 때문에 전환
    // 직후에는 이전 행이 잠시 함께 남고, 그 행도 새 상태(휴지통)의 버튼을 그린다.
    expect(screen.getByRole("button", { name: "지운 회의 되돌리기" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "지운 회의 완전 삭제" })).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /휴지통으로 보내기/ })).not.toBeInTheDocument());
  });

  it("완전 삭제는 **서버 메시지를 그대로** 보여준다", async () => {
    // 폴더가 실제로 어떻게 됐는지는 서버만 안다. 화면이 자기 문구를 만들면 거짓이 될 수 있다
    // (실기에서 서버가 "폴더가 이미 없습니다" 를 준 적이 있고 그것이 결함의 단서였다).
    api.getTrash.mockResolvedValue([session({ id: "d1", title: "지운 회의" })]);
    api.purgeSession.mockResolvedValue({ message: "결과 폴더를 휴지통으로 보냈습니다." });
    const user = userEvent.setup();
    renderLibrary();
    await screen.findByText("주간 회의");
    await user.click(screen.getByRole("button", { name: "휴지통" }));
    await user.click(await screen.findByRole("button", { name: "지운 회의 완전 삭제" }));

    expect(api.purgeSession).toHaveBeenCalledWith("d1");
    expect(await screen.findByText("결과 폴더를 휴지통으로 보냈습니다.")).toBeInTheDocument();
  });

  it("휴지통이 비면 그렇게 말한다(목록 비어 있음과 다른 문구)", async () => {
    const user = userEvent.setup();
    renderLibrary();
    await screen.findByText("주간 회의");
    await user.click(screen.getByRole("button", { name: "휴지통" }));
    expect(await screen.findByText("휴지통이 비어 있습니다")).toBeInTheDocument();
  });
});

describe("전체 삭제", () => {
  it("되돌릴 수 있다고 안내한다", async () => {
    const user = userEvent.setup();
    renderLibrary();
    await screen.findByText("주간 회의");
    await user.click(screen.getByRole("button", { name: "전체 삭제" }));

    expect(api.clearSessions).toHaveBeenCalled();
    expect(await screen.findByText(/\[휴지통\]에서 되돌릴 수 있습니다/)).toBeInTheDocument();
  });

  it("확인 문구가 '되돌릴 수 없다' 고 말하지 않는다", async () => {
    const confirmSpy = vi.fn((_msg?: string) => true);
    vi.stubGlobal("confirm", confirmSpy);
    const user = userEvent.setup();
    renderLibrary();
    await screen.findByText("주간 회의");
    await user.click(screen.getByRole("button", { name: "전체 삭제" }));

    const msg = String(confirmSpy.mock.calls[0]?.[0] ?? "");
    expect(msg).toContain("휴지통");
    expect(msg).not.toContain("되돌릴 수 없");
  });
});

describe("목록 점진 렌더링 — 수백 건 누적에도 첫 화면이 가볍다", () => {
  it("50건까지만 그리고, [더 보기]가 남은 개수를 알린다(조용한 절단 금지)", async () => {
    api.getSessions.mockResolvedValue(
      Array.from({ length: 120 }, (_, i) => session({ id: `s${i}`, title: `회의 ${i}` })),
    );
    renderLibrary();
    await screen.findByText("회의 0");
    // 행 수는 '열기' 진입 버튼으로 센다 — 표에서는 제목 칸이 그 버튼이다.
    const rowCount = () => screen.getAllByRole("button", { name: /회의 \d+ 열기/ }).length;
    expect(rowCount()).toBe(50);
    await userEvent.click(screen.getByRole("button", { name: /더 보기 \(70개 남음\)/ }));
    expect(rowCount()).toBe(100);
    await userEvent.click(screen.getByRole("button", { name: /더 보기 \(20개 남음\)/ }));
    expect(rowCount()).toBe(120);
    expect(screen.queryByRole("button", { name: /더 보기/ })).toBeNull();
  });

  it("50건 이하면 [더 보기]가 아예 없다", async () => {
    api.getSessions.mockResolvedValue([session()]);
    renderLibrary();
    await screen.findByText("주간 회의");
    expect(screen.queryByRole("button", { name: /더 보기/ })).toBeNull();
  });
});

/**
 * 행 동작은 상태마다 다르다(FR-LIB-2). 잘못 붙으면 오류 세션에서 재시도를 못 하거나,
 * 완료된 회의에 [처리 취소]가 떠서 눌러도 아무 일이 없는 화면이 된다.
 */
describe("상태별 행 동작", () => {
  it("오류 행에는 [재시도]가 붙고, 재과금 여부를 알린다", async () => {
    api.getSessions.mockResolvedValue([session({ status: "error", error_detail: "STT 실패" })]);
    const user = userEvent.setup();
    renderLibrary();
    await user.click(await screen.findByRole("button", { name: "재시도" }));

    expect(api.retrySession).toHaveBeenCalledWith("s1");
    // STT 를 재사용하는지 여부는 서버만 안다 — 그 답을 그대로 사용자에게 전한다.
    expect(await screen.findByText(/음성 인식 비용은 다시 청구되지 않습니다/)).toBeInTheDocument();
  });

  it("STT 를 재사용하지 못하면 재과금 가능성을 분명히 말한다", async () => {
    api.getSessions.mockResolvedValue([session({ status: "error" })]);
    api.retrySession.mockResolvedValue({ sessionId: "s1", status: "processing", reusedStt: false });
    const user = userEvent.setup();
    renderLibrary();
    await user.click(await screen.findByRole("button", { name: "재시도" }));
    expect(await screen.findByText(/비용이 다시 발생할 수 있습니다/)).toBeInTheDocument();
  });

  it("처리 중 행에서만 [처리 취소]를 준다", async () => {
    api.getSessions.mockResolvedValue([
      session({ id: "p1", title: "처리중 회의", status: "processing" }),
      session({ id: "c1", title: "완료 회의", status: "completed" }),
    ]);
    renderLibrary();
    await screen.findByText("처리중 회의");
    expect(screen.getByRole("button", { name: "처리중 회의 처리 취소" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "완료 회의 처리 취소" })).toBeNull();
  });

  it("완료된 회의에는 재시도·취소가 없다(누를 수 없는 버튼을 만들지 않는다)", async () => {
    renderLibrary();
    await screen.findByText("주간 회의");
    expect(screen.queryByRole("button", { name: "재시도" })).toBeNull();
    expect(screen.queryByRole("button", { name: /처리 취소/ })).toBeNull();
  });
});

describe("데이터에 없는 것을 그리지 않는다", () => {
  it("기계 생성 세션명은 '제목 없음' 으로 바꿔 보여준다(PRD §10)", async () => {
    api.getSessions.mockResolvedValue([session({ title: "web_realtime_65032efb-1a2b" })]);
    renderLibrary();
    expect(await screen.findByText("제목 없음")).toBeInTheDocument();
    expect(screen.queryByText(/web_realtime_/)).toBeNull();
  });

  it("참석자 명단이 없으면 화자 수를 '—' 로 둔다(0 이라고 단정하지 않는다)", async () => {
    api.getSessions.mockResolvedValue([session({ speakers: "" })]);
    renderLibrary();
    await screen.findByText("주간 회의");
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  it("참석자 명단이 있으면 사람 수로 센다", async () => {
    api.getSessions.mockResolvedValue([session({ speakers: "심아름, 김영희, 이철수" })]);
    renderLibrary();
    await screen.findByText("주간 회의");
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("회의 준비 세션은 '계획' 으로 적고 브리핑 열기를 준다", async () => {
    // DB 에 'planned' 상태가 없으므로 계획은 prep 세션으로만 존재한다 — 없는 상태를
    // 만들어 내지 않고 있는 것으로 표현한다.
    api.getSessions.mockResolvedValue([session({ type: "prep", title: "Q3 로드맵 준비" })]);
    renderLibrary();
    expect(await screen.findByText("계획")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Q3 로드맵 준비 브리핑 열기" })).toBeInTheDocument();
  });
});

describe("상태 필터 — 서버에 없어 클라이언트에서 거른다", () => {
  it("고른 상태만 남기고, 전체 건수를 함께 알린다", async () => {
    api.getSessions.mockResolvedValue([
      session({ id: "a", title: "완료 회의", status: "completed" }),
      session({ id: "b", title: "실패 회의", status: "error" }),
    ]);
    const user = userEvent.setup();
    renderLibrary();
    await screen.findByText("완료 회의");

    await user.selectOptions(screen.getByRole("combobox", { name: "상태 필터" }), "error");
    expect(screen.getByText("실패 회의")).toBeInTheDocument();
    expect(screen.queryByText("완료 회의")).toBeNull();
    // 거른 결과만 보여주고 끝내면 "회의가 사라졌다"로 읽힌다 — 전체 건수를 함께 남긴다.
    expect(screen.getByText(/회의 1건 \(전체 2건\)/)).toBeInTheDocument();
  });
});
