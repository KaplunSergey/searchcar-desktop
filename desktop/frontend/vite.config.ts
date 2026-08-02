import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";


const desktopRoot = fileURLToPath(new URL(".", import.meta.url));

export default defineConfig({
  root: desktopRoot,
  publicDir: fileURLToPath(new URL("../../public", import.meta.url)),
  plugins: [react()],
  define: {
    "process.env.NEXT_PUBLIC_API_URL": JSON.stringify("/api"),
    "process.env.NEXT_PUBLIC_CSRF_COOKIE_NAME": JSON.stringify("searchcar_csrf"),
  },
  server: {
    host: "127.0.0.1",
    port: 1420,
    strictPort: true,
  },
  build: {
    outDir: fileURLToPath(new URL("../dist", import.meta.url)),
    emptyOutDir: true,
    sourcemap: false,
  },
});
