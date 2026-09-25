import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// 构建产物直接进 FastAPI 的静态目录并入库：评委零构建，起服务即见完整 UI。
export default defineConfig({
  plugins: [vue()],
  base: "./",
  build: {
    outDir: "../starter/kbqa/static",
    emptyOutDir: true,
    chunkSizeWarningLimit: 1200,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
