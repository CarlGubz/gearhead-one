import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    // The lazy-loaded workspace chunk (FEA + three.js) is large by nature; CFD and CAD load as their own chunks.
    chunkSizeWarningLimit: 1200,
  },
  server: {
    host: true,
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_BACKEND_URL || "http://localhost:8000",
        changeOrigin: true,
        ws: true, // CFD live run stream (WebSocket /api/cfd/runs/{id}/stream)
      },
    },
  },
  // unit tests of the CFD workspace (geometry rasterisation parity with the backend, colour maps, streamlines)
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
