import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const api = process.env.FABRICA_API ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true,
    proxy: { "/api": api, "/health": api },
  },
});
