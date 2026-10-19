import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the backend runs on :8000. Proxying /api avoids CORS entirely;
// set VITE_API_BASE_URL at build time to point the UI at a different backend.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": "http://localhost:8000" },
  },
});
