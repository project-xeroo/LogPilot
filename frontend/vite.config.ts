/// <reference types="vitest" />
import react from "@vitejs/plugin-react";
import path from "node:path";
import { defineConfig, loadEnv } from "vite";

// Dev server proxies the API and WebSocket to the gateway so the browser only ever talks to one origin.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const target = env.VITE_API_PROXY || "http://localhost:8000";
  return {
    plugins: [react()],
    resolve: { alias: { "@": path.resolve(__dirname, "src") } },
    server: {
      port: Number(env.VITE_PORT) || 5173,
      strictPort: false,
      proxy: {
        "/api": { target, changeOrigin: true },
        "/ws": { target, ws: true, changeOrigin: true },
      },
    },
    build: { sourcemap: false, chunkSizeWarningLimit: 900 },
    test: { environment: "jsdom", globals: true, setupFiles: ["./tests/setup.ts"], css: false },
  };
});
