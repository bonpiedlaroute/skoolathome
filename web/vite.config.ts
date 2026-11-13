import { svelte } from "@sveltejs/vite-plugin-svelte";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [svelte()],
  server: {
    // In development the API runs apart: uv run skoolathome api serve
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
  test: { include: ["src/**/*.test.ts"] },
});
