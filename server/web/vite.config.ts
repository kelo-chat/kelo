import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: `npm run dev` proxies /api to the Python server. Prod: the server serves dist/ itself.
export default defineConfig({
  plugins: [react()],
  server: { host: true, proxy: { "/api": "http://127.0.0.1:8080" } },
});
