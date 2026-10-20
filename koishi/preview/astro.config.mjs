import { defineConfig } from "astro/config";

// Design preview only. The shipped Koishi entry and authenticated RPC stay separate.
export default defineConfig({
  output: "static",
  vite: { server: { strictPort: true } },
});
