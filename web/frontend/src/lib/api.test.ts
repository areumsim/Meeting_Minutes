import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * `lib/api.ts` 의 **모드 게이팅**과 세션 삭제 흐름.
 *
 * 왜 이것이 1순위인가 — 배포본 CSP 를 `connect-src 'self'` 로 좁혔다(SEC-006).
 * 그 결정이 안전한 근거는 "패키지 모드에서는 외부 호스트를 직접 부르지 않는다"이고,
 * 그 보장은 전부 이 파일의 `isPackagedMode()` 분기에 걸려 있다. 분기가 한 번 잘못되면
 * 배포본에서 조용히 차단돼 기능이 죽는다(브라우저 콘솔을 보는 사용자는 없다).
 *
 * localforage(IndexedDB)는 실물 대신 **인메모리 목**으로 대체한다 — IndexedDB 폴리필을
 * 들이면 설치가 커지고 테스트가 플래키해진다. 여기서 검증하려는 것은 저장소 구현이
 * 아니라 "무엇을 어디에 요청하는가"다.
 */

// ── localforage 목 (hoisted — vi.mock 이 import 보다 먼저 평가된다) ──────────
const stores = vi.hoisted(() => {
  const make = () => {
    const m = new Map<string, any>();
    return {
      _m: m,
      getItem: vi.fn(async (k: string) => (m.has(k) ? m.get(k) : null)),
      setItem: vi.fn(async (k: string, v: any) => { m.set(k, v); return v; }),
      removeItem: vi.fn(async (k: string) => { m.delete(k); }),
      clear: vi.fn(async () => { m.clear(); }),
      iterate: vi.fn(async (cb: (v: any, k: string, i: number) => void) => {
        let i = 1;
        for (const [k, v] of m) cb(v, k, i++);
      }),
    };
  };
  return { sessionsStore: make(), segmentsStore: make(), documentsStore: make() };
});

vi.mock("./db", () => stores);

import {
  deleteSession, getTrash, restoreSession, purgeSession, clearSessions,
  getSessions, resetPackagedMode, getBackendUrl, setBackendUrl, isPackagedMode,
} from "./api";

// ── fetch 목 헬퍼 ───────────────────────────────────────────────────────────
type Route = (url: string, init?: RequestInit) => any;

function mockFetch(routes: Record<string, Route>) {
  const calls: { url: string; method: string }[] = [];
  const fn = vi.fn(async (input: any, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, method: (init?.method || "GET").toUpperCase() });
    for (const [pattern, handler] of Object.entries(routes)) {
      if (url.includes(pattern)) {
        const r = handler(url, init);
        return {
          ok: r.ok !== false,
          status: r.status ?? (r.ok === false ? 500 : 200),
          json: async () => r.body ?? null,
          text: async () => JSON.stringify(r.body ?? null),
          clone() { return this; },
        } as any;
      }
    }
    throw new Error(`목에 없는 요청: ${url}`);
  });
  vi.stubGlobal("fetch", fn);
  return { fn, calls };
}

const healthOk: Route = () => ({ ok: true, body: { status: "ok" } });

beforeEach(() => {
  resetPackagedMode();
  for (const s of Object.values(stores)) { s._m.clear(); vi.clearAllMocks(); }
  localStorage.clear();
});

describe("모드 판정 (CSP 안전성의 근거)", () => {
  it("백엔드가 응답하면 패키지 모드", async () => {
    mockFetch({ "/api/health": healthOk });
    expect(await isPackagedMode()).toBe(true);
  });

  it("백엔드가 없으면 단독 모드", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("connection refused"); }));
    expect(await isPackagedMode()).toBe(false);
  });

  it("판정을 캐시한다 — 요청마다 health 를 부르지 않는다", async () => {
    const { fn } = mockFetch({ "/api/health": healthOk });
    await isPackagedMode();
    await isPackagedMode();
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("resetPackagedMode 로 캐시가 무효화된다(모바일에서 PC 주소 변경)", async () => {
    const { fn } = mockFetch({ "/api/health": healthOk });
    await isPackagedMode();
    resetPackagedMode();
    await isPackagedMode();
    expect(fn).toHaveBeenCalledTimes(2);
  });

  it("기본 base 는 빈 문자열 — 같은 오리진 상대 경로", () => {
    expect(getBackendUrl()).toBe("");
  });

  it("PC 연결 모드에서는 그 주소가 base 가 된다", () => {
    setBackendUrl("http://192.168.0.10:8501");
    expect(getBackendUrl()).toBe("http://192.168.0.10:8501");
  });
});

describe("세션 삭제 — 휴지통(soft delete)", () => {
  it("서버에 DELETE 를 보내고 되돌릴 수 있다고 알린다", async () => {
    stores.sessionsStore._m.set("s1", { id: "s1", title: "회의" });
    const { calls } = mockFetch({
      "/api/health": healthOk,
      "/api/sessions/s1": () => ({ ok: true, body: { success: true, restorable: true } }),
    });
    const r = await deleteSession("s1");
    expect(r).toEqual({ success: true, restorable: true });
    expect(calls.some(c => c.method === "DELETE" && c.url.endsWith("/api/sessions/s1"))).toBe(true);
  });

  it("[회귀] 서버 삭제가 실패하면 **로컬 사본을 남긴다** — 사라졌다 되살아나면 안 된다", async () => {
    // 예전에는 로컬을 먼저 지우고 서버를 불렀다. 서버가 실패해도 목록에서는 사라지고,
    // 다음 조회가 `!local` 이라 서버에서 다시 미러링해 와서 "지웠는데 되살아났다"가 됐다.
    // 목록에 그대로 보이는 것이 정직하다.
    stores.sessionsStore._m.set("s1", { id: "s1" });
    stores.segmentsStore._m.set("s1", [{ text: "a" }]);
    mockFetch({
      "/api/health": healthOk,
      "/api/sessions/s1": () => ({ ok: false, status: 404 }),
    });
    const r = await deleteSession("s1");
    expect(r).toEqual({ success: false, restorable: false });
    expect(stores.sessionsStore._m.has("s1")).toBe(true);
    expect(stores.segmentsStore._m.has("s1")).toBe(true);
  });

  it("[회귀] 서버 연결이 끊기면(예외) 로컬을 지우지 않고 실패를 알린다", async () => {
    stores.sessionsStore._m.set("s1", { id: "s1" });
    let health = true;
    vi.stubGlobal("fetch", vi.fn(async (input: any) => {
      if (String(input).includes("/api/health") && health) { health = false; return { ok: true, json: async () => ({}) } as any; }
      throw new Error("network down");
    }));
    const r = await deleteSession("s1");
    expect(r.success).toBe(false);
    expect(stores.sessionsStore._m.has("s1")).toBe(true);
  });

  it("단독 모드에서는 서버를 부르지 않고 로컬만 지운다(휴지통 없음)", async () => {
    stores.sessionsStore._m.set("s1", { id: "s1" });
    const { calls } = mockFetch({ "/api/health": () => { throw new Error("no backend"); } });
    const r = await deleteSession("s1");
    expect(r.restorable).toBe(false);
    expect(calls.filter(c => c.url.includes("/api/sessions")).length).toBe(0);
    expect(stores.sessionsStore._m.has("s1")).toBe(false);
  });

  it("전사·문서까지 로컬에서 함께 지운다(고아 데이터 방지)", async () => {
    stores.segmentsStore._m.set("s1", [{ text: "a" }]);
    stores.documentsStore._m.set("s1", [{ type: "minutes" }]);
    mockFetch({
      "/api/health": healthOk,
      "/api/sessions/s1": () => ({ ok: true, body: { success: true, restorable: true } }),
    });
    await deleteSession("s1");
    expect(stores.segmentsStore._m.has("s1")).toBe(false);
    expect(stores.documentsStore._m.has("s1")).toBe(false);
  });
});

describe("휴지통 조회·복원·완전삭제", () => {
  it("휴지통은 **서버가 진실** — 로컬 미러를 읽지 않는다", async () => {
    stores.sessionsStore._m.set("local-only", { id: "local-only", title: "로컬에만" });
    mockFetch({
      "/api/health": healthOk,
      "/api/sessions/trash": () => ({ ok: true, body: [{ id: "s9", title: "지운 회의" }] }),
    });
    const rows = await getTrash();
    expect(rows.map(r => r.id)).toEqual(["s9"]);
  });

  it("단독 모드에서는 휴지통이 빈 목록(서버가 없으므로)", async () => {
    mockFetch({ "/api/health": () => { throw new Error("no backend"); } });
    expect(await getTrash()).toEqual([]);
  });

  it("휴지통 조회 실패를 '비어 있음' 으로 위장하지 않는다", async () => {
    mockFetch({
      "/api/health": healthOk,
      "/api/sessions/trash": () => ({ ok: false, status: 500 }),
    });
    await expect(getTrash()).rejects.toThrow();
  });

  it("복원은 POST /restore", async () => {
    const { calls } = mockFetch({
      "/api/sessions/s1/restore": () => ({ ok: true, body: { success: true } }),
    });
    await restoreSession("s1");
    expect(calls.some(c => c.method === "POST" && c.url.includes("/restore"))).toBe(true);
  });

  it("완전삭제는 DELETE /purge 이고 서버 메시지를 그대로 전달한다", async () => {
    const { calls } = mockFetch({
      "/api/sessions/s1/purge": () => ({
        ok: true, body: { success: true, folder_removed: true, message: "결과 폴더를 휴지통으로 보냈습니다." },
      }),
    });
    const r = await purgeSession("s1");
    // 폴더 정리 결과는 서버만 안다 — 화면이 자기 문구를 만들면 거짓이 될 수 있다.
    expect(r.message).toContain("휴지통으로");
    expect(calls.some(c => c.method === "DELETE" && c.url.includes("/purge"))).toBe(true);
  });

  it("복원·완전삭제 실패는 예외로 올린다(화면이 성공처럼 보이면 안 된다)", async () => {
    mockFetch({
      "/api/sessions/s1/restore": () => ({ ok: false, status: 404 }),
      "/api/sessions/s1/purge": () => ({ ok: false, status: 500 }),
    });
    await expect(restoreSession("s1")).rejects.toThrow();
    await expect(purgeSession("s1")).rejects.toThrow();
  });
});

describe("전체 삭제", () => {
  it("서버 clear 가 성공하면 로컬도 비운다", async () => {
    stores.sessionsStore._m.set("s1", { id: "s1" });
    const { calls } = mockFetch({
      "/api/health": healthOk,
      "/api/sessions/clear": () => ({ ok: true, body: { success: true, moved: 1 } }),
    });
    expect(await clearSessions()).toEqual({ success: true });
    expect(stores.sessionsStore._m.size).toBe(0);
    expect(calls.some(c => c.method === "POST" && c.url.includes("/clear"))).toBe(true);
  });

  it("[회귀] 서버 clear 가 실패하면 로컬을 비우지 않는다", async () => {
    // 비워 버리면 다음 조회에서 전량이 다시 미러링돼 "전체 삭제가 안 먹는다"로 보인다.
    stores.sessionsStore._m.set("s1", { id: "s1" });
    mockFetch({
      "/api/health": healthOk,
      "/api/sessions/clear": () => ({ ok: false, status: 500 }),
    });
    expect(await clearSessions()).toEqual({ success: false });
    expect(stores.sessionsStore._m.size).toBe(1);
  });
});

describe("목록 미러링", () => {
  it("서버에 있고 로컬에 없는 세션을 가져온다", async () => {
    mockFetch({
      "/api/health": healthOk,
      "/api/sessions/s1": () => ({
        ok: true, body: { session: { id: "s1", title: "서버 회의", created_at: "2026-08-03T10:00:00" } },
      }),
      "/api/sessions": () => ({ ok: true, body: [{ id: "s1", status: "completed" }] }),
    });
    const list = await getSessions();
    expect(list.map((s: any) => s.id)).toEqual(["s1"]);
  });

  it("[회귀] 서버가 더 이상 주지 않는 세션은 목록에서 사라진다", async () => {
    // 예전에는 로컬 미러를 지우는 코드가 `deleteSession` 에만 있어, **다른 기기에서
    // 삭제한 회의가 이 기기에는 영구히 남았다**. soft delete 가 이 상태를 더 자주 만든다
    // (서버는 목록에서 빼지만 행은 남긴다).
    stores.sessionsStore._m.set("gone", { id: "gone", title: "다른 기기에서 삭제됨", created_at: "2026-08-01T10:00:00", _mirrored: true });
    stores.sessionsStore._m.set("alive", { id: "alive", title: "살아 있음", created_at: "2026-08-02T10:00:00", _mirrored: true });
    mockFetch({
      "/api/health": healthOk,
      "/api/sessions/alive": () => ({
        ok: true, body: { session: { id: "alive", title: "살아 있음", created_at: "2026-08-02T10:00:00" } },
      }),
      "/api/sessions": () => ({ ok: true, body: [{ id: "alive", status: "completed" }] }),
    });
    const list = await getSessions();
    expect(list.map((s: any) => s.id)).toEqual(["alive"]);
  });

  it("[데이터 손실 방어] 이 기기에서 만든 녹음(단독 모드)은 정리하지 않는다", async () => {
    // 정리 조건을 "서버 목록에 없음" 하나로 두면, 단독 모드에서 녹음한 회의는 서버에
    // 존재하지 않으므로 **PC 에 연결한 순간 전부 삭제**된다. 그래서 미러 사본만
    // (`_mirrored`) 정리한다. 이 테스트가 그 조건을 지킨다.
    stores.sessionsStore._m.set("local", {
      id: "local", title: "폰에서 녹음", source: "mobile", created_at: "2026-08-01T10:00:00",
    });
    mockFetch({
      "/api/health": healthOk,
      "/api/sessions": () => ({ ok: true, body: [] }),   // 서버에는 아무것도 없다
    });
    const list = await getSessions();
    expect(list.map((s: any) => s.id)).toEqual(["local"]);
  });

  it("서버가 죽었을 때는 로컬을 지우지 않는다 — 오프라인이 데이터 삭제가 되면 안 된다", async () => {
    stores.sessionsStore._m.set("s1", { id: "s1", title: "로컬", created_at: "2026-08-01T10:00:00" });
    mockFetch({ "/api/health": healthOk, "/api/sessions": () => ({ ok: false, status: 503 }) });
    const list = await getSessions();
    expect(list.map((s: any) => s.id)).toEqual(["s1"]);
  });

  it("단독 모드에서는 로컬만 나열한다", async () => {
    stores.sessionsStore._m.set("s1", { id: "s1", title: "로컬", created_at: "2026-08-01T10:00:00" });
    mockFetch({ "/api/health": () => { throw new Error("no backend"); } });
    const list = await getSessions();
    expect(list.map((s: any) => s.id)).toEqual(["s1"]);
  });

  it("최신순으로 정렬하고 검색·유형으로 걸러낸다", async () => {
    stores.sessionsStore._m.set("a", { id: "a", title: "주간보고", type: "meeting", created_at: "2026-08-01T10:00:00" });
    stores.sessionsStore._m.set("b", { id: "b", title: "세미나 발표", type: "seminar", created_at: "2026-08-03T10:00:00" });
    mockFetch({ "/api/health": () => { throw new Error("no backend"); } });
    expect((await getSessions()).map((s: any) => s.id)).toEqual(["b", "a"]);
    expect((await getSessions("주간")).map((s: any) => s.id)).toEqual(["a"]);
    expect((await getSessions("", "seminar")).map((s: any) => s.id)).toEqual(["b"]);
  });
});
