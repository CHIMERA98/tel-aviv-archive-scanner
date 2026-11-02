import { type Context, Hono, type MiddlewareHandler } from "hono";
import type { AppContext } from "../env";
import { now } from "../env";
import { sha256Hex, verifyRequest } from "../lib/crypto";
import {
  type DeliveredDocument,
  heartbeat,
  markDelivered,
  markDocument,
  nextWork,
  recordBlock,
} from "../lib/haifa";
import { dispatch, failScan, retentionSeconds, type ScanRow } from "../lib/scans";
import { forgetActivity, notifyScanReady } from "./api";

/** Signed endpoints for the Cloud Run scanner job and the Haifa worker (see scanner/edge_client.py). */
export const internal = new Hono<AppContext>();

// "filling": a Haifa scan still receiving documents from the worker.
const ACTIVE = ["starting", "running", "filling"];
const FILE_PATH = /^[\w\u05D0-\u05EA\-./]{1,200}$/; // plan folders can carry Hebrew plan numbers
// Pages, documents, and suspect answers kept for diagnosis (complot.EXPECTED).
const CACHE_KEY =
  /^(?:pages\/\w{1,40}\/[0-9a-f]{16}\.html|docs\/[0-9A-F-]{36}\.\w{2,4}|debug\/\w{1,40}-\d{8}T\d{6}\.html)$/;
const streamed = (method: string, path: string) =>
  method === "PUT" && (path.includes("/files/") || path.startsWith("/internal/haifa/cache/"));

// The signature covers the declared body digest. Small JSON bodies are read once and checked against it here;
// file uploads (PUT) are streamed to R2 unread, because hashing tens of MB would blow the Worker's CPU budget.
// R2 verifies single uploads against the digest itself; multipart parts rely on TLS.
const signed: MiddlewareHandler<AppContext> = async (c, next) => {
  const url = new URL(c.req.url);
  const digest = c.req.header("x-content-sha256") ?? "";
  const ok = await verifyRequest(
    c.env.SCANNER_HMAC_KEY,
    c.req.header("x-scanner-signature"),
    c.req.method,
    url.pathname + url.search,
    digest,
  );
  if (!ok) return c.json({ error: "unauthorized" }, 401);
  if (!streamed(c.req.method, url.pathname)) {
    const body = await c.req.raw.arrayBuffer();
    if (body.byteLength > 1024 * 1024 || (await sha256Hex(body)) !== digest) return c.json({ error: "bad_body" }, 400);
    c.set("body", body);
  }
  await next();
};
internal.use("/scans/:id", signed);
internal.use("/scans/:id/*", signed);
internal.use("/haifa/*", signed);

const bodyOf = (c: Context<AppContext>) => c.get("body") ?? new ArrayBuffer(0);
const jsonOf = <T>(c: Context<AppContext>): T => JSON.parse(new TextDecoder().decode(bodyOf(c)) || "{}") as T;

const activeScan = (c: { env: AppContext["Bindings"] }, id: string) =>
  c.env.DB.prepare(`SELECT * FROM scans WHERE id = ? AND status IN ('starting','running','filling')`).bind(id).first<ScanRow>();

internal.get("/scans/:id", async (c) => {
  const scan = await activeScan(c, c.req.param("id"));
  if (!scan) return c.json({ error: "not_active" }, 409);
  await c.env.DB.prepare("UPDATE scans SET status = 'running', last_progress_at = ? WHERE id = ?").bind(now(), scan.id).run();
  return c.json({ scan_id: scan.id, address: JSON.parse(scan.address_json), building: JSON.parse(scan.building_json) });
});

internal.post("/scans/:id/progress", async (c) => {
  const { stage, status, detail } = jsonOf<{ stage: string; status: string; detail?: string | null }>(c);
  const scan = await activeScan(c, c.req.param("id"));
  if (!scan) return c.json({ error: "not_active" }, 409);
  const stages = JSON.parse(scan.stages_json || "{}") as Record<string, unknown>;
  stages[String(stage).slice(0, 20)] = { status: String(status).slice(0, 20), detail: detail ? String(detail).slice(0, 300) : null, at: now() };
  await c.env.DB.prepare("UPDATE scans SET stages_json = ?, last_progress_at = ? WHERE id = ?")
    .bind(JSON.stringify(stages), now(), scan.id)
    .run();
  return c.json({ ok: true });
});

// Uploads: PUT body (single), POST ?uploads (start multipart), PUT ?upload_id&part=N, POST ?upload_id&complete.
internal.on(["PUT", "POST"], "/scans/:id/files/*", async (c) => {
  const id = c.req.param("id");
  const rel = decodeURIComponent(new URL(c.req.url).pathname.split(`/scans/${id}/files/`)[1] ?? "");
  if (!FILE_PATH.test(rel) || rel.includes("..")) return c.json({ error: "bad_path" }, 400);
  if (!(await activeScan(c, id))) return c.json({ error: "not_active" }, 409);
  const key = `scans/${id}/${rel}`;
  const query = new URL(c.req.url).searchParams;
  const uploadId = query.get("upload_id");

  if (c.req.method === "PUT" && !uploadId) {
    const body = c.req.raw.body;
    if (!body) return c.json({ error: "empty" }, 400);
    await c.env.FILES.put(key, body, {
      sha256: c.req.header("x-content-sha256"),
      httpMetadata: { contentType: contentType(rel) },
    });
  } else if (c.req.method === "POST" && query.has("uploads")) {
    const upload = await c.env.FILES.createMultipartUpload(key, { httpMetadata: { contentType: contentType(rel) } });
    return c.json({ upload_id: upload.uploadId });
  } else if (c.req.method === "PUT" && uploadId) {
    const part = Number(query.get("part"));
    if (!Number.isInteger(part) || part < 1 || part > 10000) return c.json({ error: "bad_part" }, 400);
    const body = c.req.raw.body;
    if (!body) return c.json({ error: "empty" }, 400);
    const uploaded = await c.env.FILES.resumeMultipartUpload(key, uploadId).uploadPart(part, body);
    return c.json({ etag: uploaded.etag });
  } else if (c.req.method === "POST" && uploadId && query.has("complete")) {
    const { parts } = jsonOf<{ parts: { part: number; etag: string }[] }>(c);
    await c.env.FILES.resumeMultipartUpload(key, uploadId).complete(
      parts.map((p) => ({ partNumber: p.part, etag: p.etag })),
    );
  } else {
    return c.json({ error: "bad_request" }, 400);
  }
  await c.env.DB.prepare("UPDATE scans SET last_progress_at = ? WHERE id = ?").bind(now(), id).run();
  return c.json({ ok: true });
});

// The Haifa worker reads a scan's manifest and files back to add documents and rebuild the final outputs.
internal.get("/scans/:id/files/*", async (c) => {
  const id = c.req.param("id");
  const rel = decodeURIComponent(new URL(c.req.url).pathname.split(`/scans/${id}/files/`)[1] ?? "");
  if (!FILE_PATH.test(rel) || rel.includes("..")) return c.json({ error: "bad_path" }, 400);
  const object = await c.env.FILES.get(`scans/${id}/${rel}`);
  if (!object) return c.json({ error: "not_found" }, 404);
  return new Response(object.body, { headers: { "Content-Type": contentType(rel) } });
});

internal.post("/scans/:id/done", async (c) => {
  const id = c.req.param("id");
  const summary = jsonOf<Record<string, unknown>>(c);
  const t = now();
  const before = await activeScan(c, id);
  const updated = await c.env.DB.prepare(
    "UPDATE scans SET status = 'done', summary_json = ?, finished_at = ?, expires_at = ? " +
      "WHERE id = ? AND status IN ('starting','running','filling') RETURNING id",
  )
    .bind(JSON.stringify(summary), t, t + retentionSeconds(c.env), id)
    .first();
  if (!updated) return c.json({ error: "not_active" }, 409);
  // A Haifa scan that already opened ("filling") got its "ready" email then; this one says everything arrived.
  const kind = before?.status === "filling" ? "complete" : "ready";
  await c.env.DB.prepare(
    "UPDATE scans SET status = 'done', summary_json = ?, finished_at = ?, expires_at = ? WHERE cached_from = ?",
  )
    .bind(JSON.stringify(summary), t, t + retentionSeconds(c.env), id)
    .run();
  c.executionCtx.waitUntil(Promise.all([notifyScanReady(c.env, id, kind), dispatch(c.env), forgetActivity(c.env)]));
  return c.json({ ok: true });
});

internal.post("/scans/:id/failed", async (c) => {
  const id = c.req.param("id");
  const { error, blocked } = jsonOf<{ error: string; blocked?: boolean }>(c);
  const scan = await c.env.DB.prepare("SELECT status FROM scans WHERE id = ?").bind(id).first<{ status: string }>();
  if (!scan || !ACTIVE.includes(scan.status)) return c.json({ error: "not_active" }, 409);
  // Failures are ours (or the city's), never the user's: give the scan back.
  await failScan(c.env, id, String(error).slice(0, 500), true);
  if (blocked) console.error(`scan ${id}: municipal firewall blocked the scanner`);
  c.executionCtx.waitUntil(Promise.all([dispatch(c.env), forgetActivity(c.env)]));
  return c.json({ ok: true });
});

// ---------- Haifa worker (docs/HAIFA_PLAN.md §3.2) ----------

internal.post("/haifa/next", async (c) => c.json(await nextWork(c.env)));

internal.post("/haifa/heartbeat", async (c) => {
  await heartbeat(c.env);
  return c.json({ ok: true });
});

internal.post("/haifa/scans/:id/delivered", async (c) => {
  const id = c.req.param("id");
  const { summary, documents } = jsonOf<{ summary: Record<string, unknown>; documents: DeliveredDocument[] }>(c);
  if (!(await markDelivered(c.env, id, summary ?? {}, Array.isArray(documents) ? documents : []))) {
    return c.json({ error: "not_active" }, 409);
  }
  c.executionCtx.waitUntil(Promise.all([notifyScanReady(c.env, id, "ready"), forgetActivity(c.env)]));
  return c.json({ ok: true });
});

internal.post("/haifa/scans/:id/documents/:doc", async (c) => {
  const { status } = jsonOf<{ status: string; pages?: number }>(c);
  const pending = await markDocument(c.env, c.req.param("id"), c.req.param("doc"), String(status));
  return c.json({ ok: true, pending });
});

internal.post("/haifa/blocked", async (c) => {
  const { reason, scan_id } = jsonOf<{ reason?: string; scan_id?: string | null }>(c);
  const until = await recordBlock(c.env, scan_id ?? null, String(reason ?? "HTTP 429"));
  console.error(`Complot blocked the Haifa worker; paused until ${new Date(until * 1000).toISOString()}`);
  c.executionCtx.waitUntil(forgetActivity(c.env));
  return c.json({ ok: true, blocked_until: until });
});

// Complot pages and documents, kept privately for 30 days (R2 lifecycle on cache/haifa/) so nothing is fetched twice.
internal.on(["GET", "PUT"], "/haifa/cache/*", async (c) => {
  const key = decodeURIComponent(new URL(c.req.url).pathname.split("/haifa/cache/")[1] ?? "");
  if (!CACHE_KEY.test(key)) return c.json({ error: "bad_key" }, 400);
  if (c.req.method === "GET") {
    const object = await c.env.FILES.get(`cache/haifa/${key}`);
    return object ? new Response(object.body) : c.json({ error: "not_found" }, 404);
  }
  const body = c.req.raw.body;
  if (!body) return c.json({ error: "empty" }, 400);
  await c.env.FILES.put(`cache/haifa/${key}`, body, { sha256: c.req.header("x-content-sha256") });
  return c.json({ ok: true });
});

export const contentType = (path: string): string => {
  const ext = path.split(".").pop()?.toLowerCase() ?? "";
  return (
    {
      pdf: "application/pdf",
      jpg: "image/jpeg",
      jpeg: "image/jpeg",
      png: "image/png",
      webp: "image/webp",
      tif: "image/tiff",
      tiff: "image/tiff",
      csv: "text/csv; charset=utf-8",
      md: "text/markdown; charset=utf-8",
      json: "application/json",
      zip: "application/zip",
      pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    } as Record<string, string>
  )[ext] ?? "application/octet-stream";
};
