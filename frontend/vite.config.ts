import path from "path"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

// `vite --mode v2` / `vite build --mode v2` builds the app against the v2 backend (see src/config.ts). The default mode is the legacy demo backend.
// Test configuration lives in vitest.config.ts so this file depends on nothing but Vite.
export default defineConfig(({ mode }) => ({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  define: mode === "v2"
    ? {
        "import.meta.env.VITE_BACKEND": JSON.stringify("v2"),
        "import.meta.env.VITE_V2_LOCAL_LOGIN": JSON.stringify(process.env.VITE_V2_LOCAL_LOGIN ?? "true"),
      }
    : {},
}))
