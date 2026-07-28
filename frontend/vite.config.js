import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// /api is proxied to the FastAPI dev server so the frontend can use
// same-origin relative URLs in both development and production.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // shadcn/ui generates imports like "@/lib/utils"; mirrored in jsconfig.json
  // so the editor resolves them too.
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    // Vite rejects requests whose Host header it doesn't recognise, which
    // blocks tunnelled dev URLs. Listed here so `code` port forwarding and
    // cloudflared/ngrok links reach the dev server; localhost is always allowed.
    allowedHosts: [".devtunnels.ms", ".trycloudflare.com", ".ngrok-free.app"],
    proxy: {
      "/api": {
        target: process.env.API_URL || "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
