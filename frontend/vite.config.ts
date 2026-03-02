import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  base: "/_ui/",
  build: {
    outDir: path.resolve(__dirname, "../src/agent_interception/ui/static"),
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/_interceptor": {
        target: "http://localhost:8080",
        changeOrigin: true,
      },
      "/api": {
        target: "http://localhost:8080",
        changeOrigin: true,
      },
    },
  },
});
