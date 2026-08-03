import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// ── CSP 빌드 프로파일 2종 (SEC-006) ───────────────────────────────
// 한 개의 CSP 로 두 배포 형태를 덮으려니 **임의 호스트 허용**이 됐다
// (`connect-src ... http: https: ws: wss:`). 두 형태가 정말로 다른 것을 요구한다:
//
//   packaged   PC 배포본. 프런트를 FastAPI 백엔드가 같은 오리진에서 서빙하고,
//              모든 외부 호출은 백엔드를 지난다 → `'self'` 만 있으면 된다
//              ('self' 는 같은 오리진의 ws/wss 도 포함한다).
//   standalone 아이폰 앱 번들. 서버 없이 OpenAI 를 직접 부르고(api.ts), 사용자가
//              입력한 LAN 주소의 PC 백엔드에 붙는다 → 그 주소를 빌드 시점에 알 수 없다.
//
// 기본값은 **packaged**(좁은 쪽)다. 프로파일 지정을 잊었을 때 넓게 열리는 것보다
// 안전한 쪽으로 실패해야 한다. iOS 번들은 `npm run build:standalone` 을 쓴다.
const CSP_COMMON = [
  "default-src 'self' data: blob:",
  "style-src 'self' 'unsafe-inline'",
  "font-src 'self'",
  "img-src 'self' data: blob:",
  "media-src 'self' blob: mediastream:",
  // 'unsafe-eval' 은 넣지 않는다 — 의존성 조사에서 요구하는 코드가 0건이었다(G-04).
  "script-src 'self' 'unsafe-inline'",
];
const CSP_CONNECT = {
  packaged: "connect-src 'self'",
  standalone:
    "connect-src 'self' https://api.openai.com wss://api.openai.com http: https: ws: wss:",
};

function cspPlugin(profile: "packaged" | "standalone") {
  const csp = [...CSP_COMMON, CSP_CONNECT[profile]].join("; ") + ";";
  return {
    name: "mm-csp-profile",
    transformIndexHtml(html: string) {
      if (!html.includes("%MM_CSP%")) {
        throw new Error("index.html 에 %MM_CSP% 자리표시자가 없습니다 — CSP 가 빠진 채 빌드됩니다.");
      }
      return html.replace("%MM_CSP%", csp);
    },
  };
}

// 프로파일은 vite 의 `--mode` 로 고른다(`vite build --mode standalone`). 환경변수를
// 쓰면 셸마다 인용 규칙이 달라 깨진다(실제로 PowerShell 에서 깨졌다).
export default defineConfig(({ mode }) => ({
  plugins: [
    react(),
    tailwindcss(),
    cspPlugin(mode === "standalone" ? "standalone" : "packaged"),
  ],
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
}));
