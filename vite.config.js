import { defineConfig } from "vite";

export default defineConfig({
  clearScreen: false,
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    watch: { ignored: ["**/src-tauri/**", "**/.venv/**", "**/data/**", "**/tools/**", "**/models/**", "**/logs/**", "**/Temp/**"] },
  },
  build: { target: "chrome120", outDir: "dist", emptyOutDir: true, sourcemap: false },
});
