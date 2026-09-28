import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Dev-only proxy to the API; nginx.conf (Task 2) does the equivalent prefix rewrite in
// production, so the browser only ever talks to one origin (D22, Spec 05 §2).
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/setupTests.ts"],
  },
});
