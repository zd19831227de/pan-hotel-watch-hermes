import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const apiProxyTarget = process.env.VITE_API_PROXY_TARGET ?? "http://127.0.0.1:8100";
const apiProxy = {
  "/api": {
    target: apiProxyTarget,
    changeOrigin: true,
  },
};

const tunnelAllowedHosts = [".ngrok-free.dev", ".ngrok-free.app", ".ngrok.app", ".ngrok.io"];

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: "0.0.0.0",
    allowedHosts: tunnelAllowedHosts,
    proxy: apiProxy,
  },
  preview: {
    port: 5173,
    host: "0.0.0.0",
    allowedHosts: tunnelAllowedHosts,
    proxy: apiProxy,
  },
});
