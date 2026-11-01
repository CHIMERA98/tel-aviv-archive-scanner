import OAuthProvider from "@cloudflare/workers-oauth-provider";
import { Hono } from "hono";
import type { AppContext, Env } from "./env";
import { dispatch, sweep } from "./lib/scans";
import { mcpHandler } from "./mcp";
import { api, verifyHandler } from "./routes/api";
import { authorize } from "./routes/authorize";
import { files } from "./routes/files";
import { internal } from "./routes/internal";

const app = new Hono<AppContext>();

const CSP = [
  "default-src 'self'",
  "script-src 'self' https://challenges.cloudflare.com https://unpkg.com",
  "style-src 'self' https://unpkg.com 'unsafe-inline'",
  "img-src 'self' data: https://*.tile.openstreetmap.org https://tile.openstreetmap.org https://unpkg.com",
  "frame-src https://challenges.cloudflare.com",
  "connect-src 'self'",
  "base-uri 'none'",
  "form-action 'self'",
].join("; ");

app.use("*", async (c, next) => {
  await next();
  // Responses from ASSETS/R2 can have immutable headers, so copy before adding ours.
  const res = new Response(c.res.body, c.res);
  res.headers.set("X-Frame-Options", "DENY");
  res.headers.set("Referrer-Policy", "strict-origin-when-cross-origin");
  res.headers.set("X-Content-Type-Options", "nosniff");
  if ((res.headers.get("Content-Type") ?? "").includes("text/html")) res.headers.set("Content-Security-Policy", CSP);
  c.res = res;
});

// www.<domain> is attached to this Worker only to redirect to the canonical address.
app.use("*", async (c, next) => {
  const url = new URL(c.req.url);
  if (!url.hostname.startsWith("www.")) return next();
  return c.redirect(`${c.env.PUBLIC_URL}${url.pathname}${url.search}`, 301);
});

app.get("/api/health", (c) => c.json({ ok: true }));
app.route("/api", api);
app.route("/internal", internal);
app.route("/files", files);
app.get("/auth/verify", verifyHandler);
app.route("/authorize", authorize);

// Website routes (/, /scan/<id>, /scans, /connect, /privacy, and the same under /en/) share one page shell.
const PAGE = /^(\/en)?\/(?:scan\/[0-9a-f]{32}|scans|connect|privacy)?\/?$/;
app.get("*", (c) => {
  const url = new URL(c.req.url);
  const page = PAGE.exec(url.pathname);
  if (!page) return c.env.ASSETS.fetch(c.req.raw);
  url.pathname = page[1] ? "/en/" : "/"; // the assets layer maps these to index.html without redirecting
  return c.env.ASSETS.fetch(new Request(url, c.req.raw));
});
app.all("*", (c) => c.env.ASSETS.fetch(c.req.raw));

// /mcp is guarded by OAuth (dynamic client registration + PKCE); everything else goes to the app.
// Built lazily because the canonical resource URL comes from the environment.
let provider: OAuthProvider<Env> | null = null;
const providerFor = (env: Env) =>
  (provider ??= new OAuthProvider<Env>({
    apiRoute: "/mcp",
    apiHandler: mcpHandler as unknown as ExportedHandler<Env> & Pick<Required<ExportedHandler<Env>>, "fetch">,
    defaultHandler: { fetch: app.fetch as unknown as ExportedHandlerFetchHandler<Env> },
    authorizeEndpoint: "/authorize",
    tokenEndpoint: "/token",
    clientRegistrationEndpoint: "/register",
    scopesSupported: ["scan"],
    resourceMetadata: { resource: `${env.PUBLIC_URL}/mcp`, resource_name: "Tel Aviv Archive Scanner" },
  }));

export default {
  fetch: (request: Request, env: Env, ctx: ExecutionContext) => providerFor(env).fetch(request, env, ctx),
  async scheduled(_controller: ScheduledController, env: Env, ctx: ExecutionContext) {
    ctx.waitUntil(sweep(env).then(() => dispatch(env)));
  },
} satisfies ExportedHandler<Env>;
