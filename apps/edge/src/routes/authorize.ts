import { Hono } from "hono";
import type { AppContext } from "../env";
import { currentUser } from "../lib/auth";

/** OAuth consent for AI clients (Claude, ChatGPT…) connecting to /mcp. Sign-in reuses the email link. */
export const authorize = new Hono<AppContext>();

const esc = (value: string) =>
  value.replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]!);

const page = (lang: "he" | "en", body: string) =>
  `<!doctype html><html lang="${lang}" dir="${lang === "he" ? "rtl" : "ltr"}"><head><meta charset="utf-8">` +
  `<meta name="viewport" content="width=device-width, initial-scale=1"><title>${lang === "he" ? "חיבור AI" : "Connect AI"}</title>` +
  `<link rel="stylesheet" href="/styles.css"></head><body><main>${body}</main></body></html>`;

const withHeaders = (response: Response, headers: Headers) => {
  for (const [name, value] of headers) response.headers.append(name, value);
  return response;
};

authorize.get("/", async (c) => {
  let request;
  try {
    request = await c.env.OAUTH_PROVIDER.parseAuthRequest(c.req.raw);
  } catch {
    return c.html(page("en", "<h1>Invalid request</h1><p class='muted'>The app that sent you here made a malformed request.</p>"), 400);
  }
  const user = await currentUser(c);
  if (!user) {
    const here = new URL(c.req.url);
    return c.redirect(`/en/?signin=1&returnTo=${encodeURIComponent(here.pathname + here.search)}`, 302);
  }
  const he = user.locale === "he";
  const about = await c.env.OAUTH_PROVIDER.describeConsent(request);
  const consent = await c.env.OAUTH_PROVIDER.beginConsent(request);
  const client = esc(about.clientName);
  const target = esc(new URL(about.redirectUri).hostname);
  const html = page(
    he ? "he" : "en",
    he
      ? `<h1>לחבר את ${client}?</h1><p class="muted">${client} יוכל לחפש בניינים, להתחיל סריקות בשמך (${esc(user.email)}) ולקרוא את המסמכים שנסרקו. התשובות יישלחו אל ${target}.</p>`
      : `<h1>Connect ${client}?</h1><p class="muted">${client} will be able to look up buildings, start scans as you (${esc(user.email)}) and read the scanned documents. Responses go to ${target}.</p>`,
  ).replace(
    "</main>",
    `<form method="post" action="/authorize" class="actions"><input type="hidden" name="handle" value="${esc(consent.handle)}">` +
      `<button name="decision" value="approve">${he ? "אישור" : "Allow"}</button>` +
      `<button name="decision" value="deny" class="ghost">${he ? "ביטול" : "Cancel"}</button></form></main>`,
  );
  return withHeaders(c.html(html), consent.headers);
});

authorize.post("/", async (c) => {
  const user = await currentUser(c);
  if (!user) return c.text("Sign in first", 401);
  const form = await c.req.formData();
  const handle = String(form.get("handle") ?? "");
  try {
    if (form.get("decision") !== "approve") {
      const denied = await c.env.OAUTH_PROVIDER.denyConsent(c.req.raw, handle);
      return withHeaders(new Response(null, { status: 302 }), denied.headers);
    }
    const approved = await c.env.OAUTH_PROVIDER.approveConsent(c.req.raw, handle, { scope: ["scan"] });
    const { redirectTo } = await c.env.OAUTH_PROVIDER.completeAuthorization({
      request: approved.request,
      userId: user.id,
      metadata: { email: user.email },
      scope: ["scan"],
      props: { userId: user.id, email: user.email },
    });
    return withHeaders(new Response(null, { status: 302, headers: { Location: redirectTo } }), approved.headers);
  } catch {
    return c.html(page("en", "<h1>This request expired</h1><p class='muted'>Start the connection again from your AI app.</p>"), 400);
  }
});
