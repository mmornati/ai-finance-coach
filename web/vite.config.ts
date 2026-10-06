/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath } from "node:url";

// Production build goes into the Python package: `coach ui` serves it as static files.
const outDir = fileURLToPath(new URL("../src/coach/api/static", import.meta.url));

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  build: { outDir, emptyOutDir: true, sourcemap: false, chunkSizeWarningLimit: 600 },
  server: {
    port: 5173,
    strictPort: true,
    // dev mode: `uv run coach ui --dev --no-browser` serves the API on 8765; the page itself comes from Vite
    proxy: { "/api": { target: "http://127.0.0.1:8765", changeOrigin: false } },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
