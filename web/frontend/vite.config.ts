import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    rollupOptions: {
      output: {
        // vendor 분리 — 단일 대형 번들 대신 캐시 친화적으로 나눈다.
        // 경로 경계(/pkg/)로 매칭해 react-markdown 등이 vendor-react로 잘못 들어가
        // 순환 청크가 생기는 것을 방지한다(react/react-dom/scheduler만 리프로 격리).
        manualChunks(id) {
          if (!id.includes("node_modules")) return;
          if (id.includes("/react/") || id.includes("/react-dom/") || id.includes("/scheduler/"))
            return "vendor-react";
          if (id.includes("/motion/") || id.includes("/framer-motion/")) return "vendor-motion";
          if (id.includes("/lucide-react/")) return "vendor-icons";
          return "vendor";
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8501",
      "/ws": {
        target: "ws://localhost:8501",
        ws: true,
      },
    },
  },
});
