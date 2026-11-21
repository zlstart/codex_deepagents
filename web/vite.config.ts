import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { viteStaticCopy } from "vite-plugin-static-copy";
export default defineConfig({
  plugins: [
    react(),
    viteStaticCopy({
      targets: ["cmaps", "standard_fonts", "wasm", "iccs"].map((dir) => ({
        src: "node_modules/pdfjs-dist/" + dir + "/*",
        dest: "pdf-support/" + dir,
        rename: { stripBase: true },
      })),
    }),
  ],
  server: {
    proxy: { "/api": process.env.FAMSUN_API_ORIGIN || "http://127.0.0.1:8000" },
  },
});
