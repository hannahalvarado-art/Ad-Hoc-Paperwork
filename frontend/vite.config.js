import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// /api is proxied to the FastAPI dev server so the frontend can use
// same-origin relative URLs in both development and production.
export default defineConfig({
  plugins: [react()],
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
