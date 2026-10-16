import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// In development the SPA talks to the API through Vite's proxy, so the browser sees a single
// origin exactly like in production (where Traefik routes /api and /ws to the backend).
const apiTarget = process.env.VITE_API_PROXY ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: apiTarget, changeOrigin: false },
      "/ws": { target: apiTarget, ws: true },
    },
  },
  build: {
    sourcemap: false,
    chunkSizeWarningLimit: 600,
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
