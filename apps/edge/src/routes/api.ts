import { Hono } from "hono";
import { z } from "zod";
import type { AppContext } from "../env";
import { now } from "../env";
import {
  createLoginToken,
  currentUser,
  endSession,
  isEmail,
  redeemLoginToken,
  safeReturnTo,
  startSession,
} from "../lib/auth";
import { openJson, sealJson, signRequest } from "../lib/crypto";
import { type Locale, scanReadyMessage, sendEmail, signInMessage } from "../lib/email";
import {
  createScan,
  dispatch,
  findActiveScan,
  findCachedScan,
  queuePosition,
  quotaState,
  type ScanRow,
  type Selection,
  sourceScan,
} from "../lib/scans";
import { verifyTurnstile } from "../lib/turnstile";

export const api = new Hono<AppContext>();

const locale = (value: unknown): Locale => (value === "en" ? "en" : "he");
const clientIp = (headers: Headers) => headers.get("cf-connecting-ip");

// ---------- auth ----------

api.post("/auth/request", async (c) => {
  const body = await c.req.json().catch(() => ({}));
  const { email, turnstileToken, returnTo } = body as Record<string, unknown>;
  const lang = locale(body.locale);
  if (!isEmail(email)) return c.json({ error: "invalid_email" }, 400);
  if (!(await verifyTurnstile(c.env, turnstileToken, "signin", clientIp(c.req.raw.headers)))) {
    return c.json({ error: "bot_check_failed" }, 403);
  }
  const token = await createLoginToken(c.env, email, lang, safeReturnTo(returnTo));
  const link = `${c.env.PUBLIC_URL}/auth/verify?token=${encodeURIComponent(token)}`;
  c.executionCtx.waitUntil(sendEmail(c.env, signInMessage(lang, email.trim(), link)));
  return c.json({ ok: true });
});

api.post("/auth/logout", async (c) => {
  await endSession(c);
  return c.json({ ok: true });
});

api.get("/me", async (c) => {
  const user = await currentUser(c);
  if (!user) return c.json({ user: null });
  return c.json({ user: { email: user.email, locale: user.locale }, quota: await quotaState(c.env, user.id) });
});

// ---------- lookup ----------

const lookupMatch = z.object({
  address: z.object({ label_he: z.string(), label_en: z.string() }).passthrough(),
  building: z.object({ building_id: z.number().int().positive() }).passthrough().nullable(),
});

api.get("/lookup", async (c) => {
  const q = (c.req.query("q") ?? "").trim().slice(0, 120);
  if (q.length < 3) return c.json({ matches: [] });

  const cache = caches.default;
  const cacheKey = new Request(`${c.env.PUBLIC_URL}/__lookup?q=${encodeURIComponent(q)}`);
  let data = (await (await cache.match(cacheKey))?.json()) as { matches: unknown[] } | undefined;
  if (!data) {
    const path = `/lookup?q=${encodeURIComponent(q)}`;
    const response = await fetch(c.env.LOOKUP_URL + path, {
      headers: { "X-Scanner-Signature": await signRequest(c.env.SCANNER_HMAC_KEY, "GET", path, "") },
    });
    if (!response.ok) return c.json({ error: "lookup_unavailable" }, 502);
    data = (await response.json()) as { matches: unknown[] };
    c.executionCtx.waitUntil(
      cache.put(cacheKey, new Response(JSON.stringify(data), { headers: { "Cache-Control": "max-age=86400" } })),
    );
  }

  const matches = [];
  for (const raw of data.matches ?? []) {
    const parsed = lookupMatch.safeParse(raw);
    if (!parsed.success || !parsed.data.building) continue;
    const selection = parsed.data as Selection;
    matches.push({
      label_he: selection.address.label_he,
      label_en: selection.address.label_en,
      building: selection.building,
      selection: await sealJson(c.env.SESSION_KEY, { ...selection, issued: now() }),
    });
  }
  return c.json({ matches });
});

// ---------- scans ----------

export const publicScan = async (c: { env: AppContext["Bindings"] }, row: ScanRow) => {
  const source = await sourceScan(c.env, row);
  const position = await queuePosition(c.env, source);
  return {
    id: row.id,
    label_he: row.label_he,
    label_en: row.label_en,
    building: JSON.parse(row.building_json),
    status: row.cached_from ? source.status : row.status,
    stages: JSON.parse(source.stages_json || "{}"),
    queue_position: position,
    summary: source.summary_json ? JSON.parse(source.summary_json) : null,
    error: source.error,
    reused: Boolean(row.cached_from),
    queued_at: row.queued_at,
    finished_at: source.finished_at,
    expires_at: source.expires_at,
    files_base: `/files/${source.id}/`,
  };
};

/** Shared by the website and the MCP server. */
export const startScan = async (
  env: AppContext["Bindings"],
  userId: string,
  selectionToken: string,
  origin: "web" | "mcp",
): Promise<{ ok: true; scanId: string; reused: boolean } | { ok: false; error: string; status: 400 | 429 | 503 }> => {
  const selection = await openJson<Selection & { issued: number }>(env.SESSION_KEY, selectionToken);
  if (!selection || now() - selection.issued > 3600) return { ok: false, error: "invalid_selection", status: 400 };

  const reusable =
    (await findCachedScan(env, selection.building.building_id)) ?? (await findActiveScan(env, selection.building.building_id));
  if (reusable) {
    return { ok: true, scanId: await createScan(env, userId, selection, origin, reusable), reused: true };
  }
  const quota = await quotaState(env, userId);
  if (quota.weeklyLeft <= 0) return { ok: false, error: "weekly_quota", status: 429 };
  if (quota.dailyCapReached) return { ok: false, error: "daily_cap", status: 503 };
  return { ok: true, scanId: await createScan(env, userId, selection, origin, null), reused: false };
};

api.post("/scans", async (c) => {
  const user = await currentUser(c);
  if (!user) return c.json({ error: "signin_required" }, 401);
  const body = (await c.req.json().catch(() => ({}))) as Record<string, unknown>;
  if (!(await verifyTurnstile(c.env, body.turnstileToken, "scan", clientIp(c.req.raw.headers)))) {
    return c.json({ error: "bot_check_failed" }, 403);
  }
  if (typeof body.selection !== "string") return c.json({ error: "invalid_selection" }, 400);
  const result = await startScan(c.env, user.id, body.selection, "web");
  if (!result.ok) return c.json({ error: result.error }, result.status);
  if (!result.reused) c.executionCtx.waitUntil(dispatch(c.env));
  return c.json({ scanId: result.scanId, reused: result.reused });
});

api.get("/scans", async (c) => {
  const user = await currentUser(c);
  if (!user) return c.json({ error: "signin_required" }, 401);
  const rows = await c.env.DB.prepare("SELECT * FROM scans WHERE user_id = ? ORDER BY queued_at DESC LIMIT 20")
    .bind(user.id)
    .all<ScanRow>();
  return c.json({ scans: await Promise.all(rows.results.map((row) => publicScan(c, row))) });
});

// Anyone with the (unguessable) scan link can see it: the contents are public municipal records.
api.get("/scans/:id", async (c) => {
  const row = await c.env.DB.prepare("SELECT * FROM scans WHERE id = ?").bind(c.req.param("id")).first<ScanRow>();
  if (!row) return c.json({ error: "not_found" }, 404);
  return c.json(await publicScan(c, row));
});

// ---------- email-link landing (not under /api; mounted separately) ----------

export const verifyHandler = async (c: import("hono").Context<AppContext>) => {
  const token = c.req.query("token") ?? "";
  const result = token ? await redeemLoginToken(c.env, token) : null;
  if (!result) return c.redirect("/?signin=expired", 302);
  await startSession(c, result.userId);
  const home = result.locale === "en" ? "/en/" : "/";
  return c.redirect(result.returnTo ?? home, 302);
};

export const notifyScanReady = async (env: AppContext["Bindings"], scanId: string) => {
  const rows = await env.DB.prepare(
    "SELECT s.id, s.label_he, s.label_en, u.email, u.locale FROM scans s JOIN users u ON u.id = s.user_id " +
      "WHERE s.id = ? OR s.cached_from = ?",
  )
    .bind(scanId, scanId)
    .all<{ id: string; label_he: string; label_en: string; email: string; locale: Locale }>();
  await Promise.all(
    rows.results.map((r) =>
      sendEmail(
        env,
        scanReadyMessage(
          r.locale,
          r.email,
          r.locale === "he" ? r.label_he : r.label_en,
          `${env.PUBLIC_URL}${r.locale === "en" ? "/en" : ""}/scan/${r.id}`,
        ),
      ),
    ),
  );
};
