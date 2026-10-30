import { cloudflareTest, readD1Migrations } from "@cloudflare/vitest-plugin";
import { defineConfig } from "vitest/config";

export default defineConfig(async () => {
  const migrations = await readD1Migrations("./migrations");
  return {
    plugins: [
      cloudflareTest({
        wrangler: { configPath: "./wrangler.jsonc" },
        miniflare: {
          bindings: {
            TEST_MIGRATIONS: migrations,
            PUBLIC_URL: "https://scanner.test",
            LOOKUP_URL: "https://lookup.test",
            GCP_PROJECT: "test-project",
            TURNSTILE_HOSTNAMES: "scanner.test",
            TURNSTILE_SECRET: "turnstile-secret",
            SCANNER_HMAC_KEY: "hmac-key",
            SESSION_KEY: "session-key",
            GCP_SA_KEY: "{}",
          },
        },
      }),
    ],
    test: { setupFiles: ["./test/setup.ts"] },
  };
});
