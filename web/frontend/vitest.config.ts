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
  },
});
