import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// En desarrollo, /api y /health se redirigen al backend FastAPI.
const api = process.env.FABRICA_API ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true,
    proxy: { "/api": api, "/health": api },
  },
});
