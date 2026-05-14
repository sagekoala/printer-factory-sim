import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// We proxy the three FastAPI apps through the Vite dev server so the browser
// never has to deal with CORS (the apps don't enable CORSMiddleware).
//
// Browser hits  /p/api/stock   ->  http://localhost:8001/api/stock
// Browser hits  /m/api/stock   ->  http://localhost:8002/api/stock
// Browser hits  /r/api/stock   ->  http://localhost:8003/api/stock
const proxyMap = {
  "/p": "http://localhost:8001",
  "/m": "http://localhost:8002",
  "/r": "http://localhost:8003",
};

const proxy = Object.fromEntries(
  Object.entries(proxyMap).map(([prefix, target]) => [
    prefix,
    {
      target,
      changeOrigin: true,
      rewrite: (path) => path.replace(new RegExp(`^${prefix}`), ""),
    },
  ])
);

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy,
  },
});
