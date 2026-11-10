import preact from "@preact/preset-vite";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

// 원본: app/web (index.html, src/, public/) → 결과물: app/web-dist (FastAPI가 WEB_DIR로 서빙)
// 개발: `npm run dev`(5173)에서 화면을 열고 API는 FastAPI(8000)로 넘긴다.
const API = ["/me", "/auth", "/lms", "/sync", "/courses", "/todos", "/week", "/assignments", "/saved-videos", "/downloads",
  "/transcripts", "/access", "/admin", "/playback", "/notifications", "/health"];

export default defineConfig({
  root: "app/web",
  plugins: [preact(), tailwindcss()],
  build: {
    outDir: "../web-dist",
    emptyOutDir: true,
    target: ["chrome110", "edge110", "firefox115", "safari16"],
    assetsInlineLimit: 0,  // CSP와 캐시를 단순하게: 이미지·글꼴은 data: 대신 파일로 둔다
  },
  worker: { format: "es" },
  server: {
    proxy: Object.fromEntries(API.map((path) => [path, "http://127.0.0.1:8000"])),
  },
});
