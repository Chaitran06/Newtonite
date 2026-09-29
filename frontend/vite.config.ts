import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// The dev server proxies /api/* to the FastAPI backend, so the browser sees one origin (no CORS).
export default defineConfig({
  plugins: [react()],
  test: { environment: "jsdom", globals: false },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_API_TARGET ?? "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
