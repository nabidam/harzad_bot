import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { viteSingleFile } from "vite-plugin-singlefile";

// HARZAD_WEBAPP_V2_SPEC §7: the build is one self-contained index.html
// (vite-plugin-singlefile). The VPS keeps serving exactly one file from
// web/static/ — no new routes in web/app.py, no node on the VPS, and the
// byte-equality test (server serves the repo file) survives the port.
export default defineConfig({
  plugins: [react(), viteSingleFile()],
  build: {
    outDir: fileURLToPath(new URL("../static", import.meta.url)),
    emptyOutDir: true,
  },
});
