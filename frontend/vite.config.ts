/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The API is the FastAPI app on 127.0.0.1:8001 -- never localhost, which on
// Windows resolves ::1 first and finds nothing listening. `npm run dev` serves
// the pages here on 5173 and passes every data and action URL through.
// ADS_API=http://127.0.0.1:8101 npm run dev  points it at another port.
const API = process.env.ADS_API || "http://127.0.0.1:8001";

export default defineConfig(({ command }) => ({
  plugins: [react()],
  // Built into static/app/, which main.py already serves under /static. The
  // dev server stays at / so the routes (/, /brief, ...) match.
  base: command === "build" ? "/static/app/" : "/",
  build: { outDir: "../static/app", emptyOutDir: true },
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": API,
      "/ask.json": API,
      "/ask/prose.json": API,
      "/refresh": API,
      "/brief/summary.json": API,
      "^/ad/[^/]+/analyse\.json": API,
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
  },
}));
