import { Hono } from "hono";
import type { AppContext, Env } from "./env";
import { dispatch, sweep } from "./lib/scans";
import { api, verifyHandler } from "./routes/api";
import { files } from "./routes/files";
import { internal } from "./routes/internal";

const app = new Hono<AppContext>();

app.use("*", async (c, next) => {
  await next();
  c.header("X-Frame-Options", "DENY");
  c.header("Referrer-Policy", "strict-origin-when-cross-origin");
});

app.get("/api/health", (c) => c.json({ ok: true }));
app.route("/api", api);
app.route("/internal", internal);
app.route("/files", files);
app.get("/auth/verify", verifyHandler);

// Everything else is the static website.
app.all("*", (c) => c.env.ASSETS.fetch(c.req.raw));

export default {
  fetch: app.fetch,
  async scheduled(_controller: ScheduledController, env: Env, ctx: ExecutionContext) {
    ctx.waitUntil(sweep(env).then(() => dispatch(env)));
  },
} satisfies ExportedHandler<Env>;
