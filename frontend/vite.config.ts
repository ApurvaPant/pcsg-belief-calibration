import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Frontend runs on :5173; backend on :8000 with CORS enabled.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
  },
});
