import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

// jsdom 에는 없는데 앱이 쓰는 것들. 없으면 컴포넌트가 렌더 중 죽어 "테스트가 못 도는"
// 상태가 되고, 그러면 회귀 그물이 아니라 방해물이 된다.
if (!window.matchMedia) {
  window.matchMedia = ((q: string) => ({
    matches: false, media: q, onchange: null,
    addListener: vi.fn(), removeListener: vi.fn(),
    addEventListener: vi.fn(), removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })) as any;
}

// motion/react 의 진입/퇴장 애니메이션이 jsdom 에서 요소를 남기거나 늦게 지우는 것을 막는다.
if (!window.requestAnimationFrame) {
  window.requestAnimationFrame = ((cb: FrameRequestCallback) =>
    setTimeout(() => cb(performance.now()), 0) as unknown as number) as any;
  window.cancelAnimationFrame = ((id: number) => clearTimeout(id)) as any;
}

// `speechSynthesis` 는 아직 쓰지 않지만(PRD 검토 단계), 없을 때 접근하면 던지는 코드가
// 들어오면 여기서 막힌다 — 필요해지면 이 자리에 목을 둔다.

afterEach(() => {
  cleanup();
  localStorage.clear();
});
