/// <reference types="vitest" />
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// vite.config.ts 를 재사용하지 않고 별도 설정을 두는 이유 —
// 그쪽에는 `%MM_CSP%` 자리표시자가 없으면 **빌드를 실패시키는** CSP 플러그인이 있다
// (SEC-006 빌드 프로파일). 테스트는 index.html 을 변환하지 않으므로 그 플러그인이
// 필요 없고, 릴리즈 빌드 설정을 테스트 편의로 건드리지 않는 편이 안전하다.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    // 화면 테스트가 실제 타이머를 기다리며 느려지지 않게 기본 타임아웃을 낮게 둔다.
    testTimeout: 10000,
    restoreMocks: true,
    // 동시 실행 워커 수를 묶는다. 파일이 17개로 늘면서 jsdom 환경이 한꺼번에 뜨면
    // 워커가 "Worker exited unexpectedly" 로 죽고 **테스트 파일 2개가 조용히 안 돌았다**
    // (통과 수만 줄어 있어 눈치채기 어렵다). 이 리포는 테스트 수치를 정본으로 쓰므로
    // 러너가 실행마다 다른 답을 주면 안 된다 — 조금 느려도 결정적인 쪽을 고른다.
    poolOptions: { forks: { maxForks: 4, minForks: 1 } },
  },
});
