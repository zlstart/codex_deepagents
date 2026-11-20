import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  timeout: 35000,
  workers: 1,
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:8176",
    viewport: { width: 1440, height: 900 },
    launchOptions: {
      executablePath: process.env.FAMSUN_E2E_CHROMIUM || undefined,
      args: [
        "--use-gl=angle",
        "--use-angle=swiftshader",
        "--enable-unsafe-swiftshader",
      ],
    },
  },
  webServer: {
    command: "cd .. && uv run --no-sync python -m web.tests.serve",
    url: "http://127.0.0.1:8176/api/health",
    reuseExistingServer: false,
    timeout: 50000,
  },
});
