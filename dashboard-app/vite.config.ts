import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Builds into the Python package so hatchling can ship it in the wheel.
export default defineConfig({
  plugins: [react()],
  base: "/app/",
  build: {
    outDir: "../agenticledger/proxy/static",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/api": "http://localhost:8000",
      "/session": "http://localhost:8000",
      "/explain": "http://localhost:8000",
      "/ws": { target: "ws://localhost:8000", ws: true },
    },
  },
});
