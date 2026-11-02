import type { Env } from "../env";
import { now, num } from "../env";
import { runHaifaWorker } from "./gcp";

// The Haifa queue (docs/HAIFA_PLAN.md §3.2, §4). One Cloud Run worker does all Haifa Complot work, asking
// /internal/haifa/next for one unit at a time. Complot blocks an IP after too many calls, so after a 429 nothing goes
// to it for 45 minutes, and the pace stays 50% slower (up to 30 s per call) for the next 24 hours.

const BLOCK_S = 45 * 60;
const SLOW_S = 24 * 3600;
const MAX_INTERVAL_S = 30;
const WORKER_STALE_S = 3 * 60; // the worker sends a heartbeat every minute
const FILLING_MAX_S = 48 * 3600;
const MAX_FIRST_DELIVERY_TRIES = 5; // crashes, not blocks, use these up
export const MAX_OPEN_REQUESTS = 10; // documents a scan may have "opened, waiting" at once

interface SourceState {
  blocked_until: number | null;
  interval_s: number | null;
  slow_until: number | null;
  last_seen: number | null;
}

export type Work =
  | { kind: "scan"; scan_id: string; address: unknown; building: unknown; interval_s: number }
  | { kind: "document"; scan_id: string; doc_id: string; interval_s: number }
  | { kind: "finish"; scan_id: string; interval_s: number }
  | { kind: "idle" | "blocked"; interval_s: number; blocked_until?: number };

const state = (env: Env, source: string) =>
  env.DB.prepare("SELECT blocked_until, interval_s, slow_until, last_seen FROM source_state WHERE source = ?")
    .bind(source)
    .first<SourceState>();

const defaultInterval = (env: Env) => num(env.COMPLOT_INTERVAL_S, 12);

const interval = (env: Env, complot: SourceState | null) =>
  complot?.interval_s && (complot.slow_until ?? 0) > now() ? complot.interval_s : defaultInterval(env);

export const complotBlockedUntil = async (env: Env): Promise<number | null> => {
  const complot = await state(env, "complot");
  return complot?.blocked_until && complot.blocked_until > now() ? complot.blocked_until : null;
};

const touchWorker = (env: Env, at: number | null) =>
  env.DB.prepare(
    "INSERT INTO source_state (source, last_seen) VALUES ('haifa-worker', ?1) ON CONFLICT(source) DO UPDATE SET last_seen = ?1",
  )
    .bind(at)
    .run();

export const heartbeat = (env: Env) => touchWorker(env, now());

const haifaHasWork = async (env: Env): Promise<boolean> => {
  const row = await env.DB.prepare(
    "SELECT COUNT(*) AS n FROM scans WHERE city = 'haifa' AND cached_from IS NULL AND status IN ('queued','running','filling')",
  ).first<{ n: number }>();
  return (row?.n ?? 0) > 0;
};

/** Start the worker when Haifa has work, Complot isn't cooling down, and no worker has been heard from lately. */
export const startHaifaWorker = async (env: Env): Promise<boolean> => {
  if (!(await haifaHasWork(env)) || (await complotBlockedUntil(env))) return false;
  await env.DB.prepare("INSERT OR IGNORE INTO source_state (source) VALUES ('haifa-worker')").run();
  // Claiming the start (setting last_seen) and checking staleness in one statement keeps it to one execution.
  const claimed = await env.DB.prepare(
    "UPDATE source_state SET last_seen = ?1 WHERE source = 'haifa-worker' AND (last_seen IS NULL OR last_seen < ?2) " +
      "RETURNING source",
  )
    .bind(now(), now() - WORKER_STALE_S)
    .first();
  if (!claimed) return false;
  try {
    await runHaifaWorker(env);
  } catch (err) {
    await touchWorker(env, null);
    throw err;
  }
  return true;
};

/** The next unit of Haifa work, in priority order: opened documents, new scans, finishing, background documents. */
export const nextWork = async (env: Env): Promise<Work> => {
  await heartbeat(env);
  const complot = await state(env, "complot");
  const interval_s = interval(env, complot);
  if (complot?.blocked_until && complot.blocked_until > now()) {
    return { kind: "blocked", interval_s, blocked_until: complot.blocked_until };
  }

  const opened = await env.DB.prepare(
    "SELECT d.scan_id, d.doc_id FROM haifa_documents d JOIN scans s ON s.id = d.scan_id " +
      "WHERE d.status = 'pending' AND d.requested_at IS NOT NULL AND s.status = 'filling' ORDER BY d.requested_at LIMIT 1",
  ).first<{ scan_id: string; doc_id: string }>();
  if (opened) return { kind: "document", ...opened, interval_s };

  // A scan left "running" belongs to a worker that died (only one runs), so it resumes before new ones.
  const scan = await env.DB.prepare(
    `UPDATE scans SET status = 'running', dispatched_at = ?1, last_progress_at = ?1, attempts = attempts + 1
     WHERE id = (SELECT id FROM scans WHERE city = 'haifa' AND cached_from IS NULL AND status IN ('running','queued')
                 ORDER BY status = 'running' DESC, queued_at LIMIT 1)
     RETURNING id, address_json, building_json`,
  )
    .bind(now())
    .first<{ id: string; address_json: string; building_json: string }>();
  if (scan) {
    return {
      kind: "scan",
      scan_id: scan.id,
      address: JSON.parse(scan.address_json),
      building: JSON.parse(scan.building_json),
      interval_s,
    };
  }

  const complete = await env.DB.prepare(
    "SELECT id FROM scans s WHERE city = 'haifa' AND status = 'filling' AND NOT EXISTS " +
      "(SELECT 1 FROM haifa_documents d WHERE d.scan_id = s.id AND d.status = 'pending') ORDER BY last_progress_at LIMIT 1",
  ).first<{ id: string }>();
  if (complete) return { kind: "finish", scan_id: complete.id, interval_s };

  // Round-robin: the scan that waited longest gets the next document, so a large building can't hold up the rest.
  const background = await env.DB.prepare(
    "SELECT d.scan_id, d.doc_id FROM haifa_documents d JOIN scans s ON s.id = d.scan_id " +
      "WHERE d.status = 'pending' AND s.status = 'filling' ORDER BY s.last_progress_at, d.priority LIMIT 1",
  ).first<{ scan_id: string; doc_id: string }>();
  if (background) return { kind: "document", ...background, interval_s };

  await touchWorker(env, null); // the worker exits now; the next dispatch may start a new one at once
  return { kind: "idle", interval_s };
};

export interface DeliveredDocument {
  doc_id: string;
  guid?: string | null;
  priority: number;
}

/** First delivery uploaded: the scan opens ("filling") and its remaining documents join the queue. */
export const markDelivered = async (
  env: Env,
  scanId: string,
  summary: Record<string, unknown>,
  documents: DeliveredDocument[],
): Promise<boolean> => {
  const t = now();
  const updated = await env.DB.prepare(
    "UPDATE scans SET status = 'filling', summary_json = ?, last_progress_at = ? " +
      "WHERE id = ? AND city = 'haifa' AND status = 'running' RETURNING id",
  )
    .bind(JSON.stringify(summary), t, scanId)
    .first();
  if (!updated) return false;
  const statements = documents.map((d) =>
    env.DB.prepare(
      "INSERT OR IGNORE INTO haifa_documents (scan_id, doc_id, guid, priority, status) VALUES (?, ?, ?, ?, 'pending')",
    ).bind(scanId, String(d.doc_id).slice(0, 10), d.guid ? String(d.guid).slice(0, 40) : null, Number(d.priority) || 0),
  );
  for (let i = 0; i < statements.length; i += 50) await env.DB.batch(statements.slice(i, i + 50));
  return true;
};

export const markDocument = async (env: Env, scanId: string, docId: string, status: string): Promise<number> => {
  const final = ["ready", "failed"].includes(status) ? status : "failed";
  const t = now();
  await env.DB.batch([
    env.DB.prepare("UPDATE haifa_documents SET status = ?, done_at = ? WHERE scan_id = ? AND doc_id = ?").bind(
      final,
      t,
      scanId,
      docId,
    ),
    env.DB.prepare("UPDATE scans SET last_progress_at = ? WHERE id = ?").bind(t, scanId),
  ]);
  const row = await env.DB.prepare(
    "SELECT COUNT(*) AS n FROM haifa_documents WHERE scan_id = ? AND status = 'pending'",
  )
    .bind(scanId)
    .first<{ n: number }>();
  return row?.n ?? 0;
};

/** Complot said 429: pause everything for 45 minutes, slow down for a day, and put the interrupted scan back. */
export const recordBlock = async (env: Env, scanId: string | null, reason: string): Promise<number> => {
  const t = now();
  const complot = await state(env, "complot");
  const slower = Math.min(MAX_INTERVAL_S, interval(env, complot) * 1.5);
  await env.DB.prepare(
    "INSERT INTO source_state (source, blocked_until, interval_s, slow_until, reason) VALUES ('complot', ?1, ?2, ?3, ?4) " +
      "ON CONFLICT(source) DO UPDATE SET blocked_until = ?1, interval_s = ?2, slow_until = ?3, reason = ?4",
  )
    .bind(t + BLOCK_S, slower, t + SLOW_S, reason.slice(0, 200))
    .run();
  if (scanId) {
    // Not the scan's fault: give back the attempt.
    await env.DB.prepare(
      "UPDATE scans SET status = 'queued', stages_json = '{}', attempts = MAX(attempts - 1, 0) " +
        "WHERE id = ? AND city = 'haifa' AND status = 'running'",
    )
      .bind(scanId)
      .run();
  }
  await touchWorker(env, null);
  return t + BLOCK_S;
};

export type OpenResult = "queued" | "ready" | "failed" | "unknown" | "limit";

/** Someone opened a document that hasn't arrived: it goes to the front of the queue. */
export const requestDocument = async (env: Env, scanId: string, docId: string): Promise<OpenResult> => {
  const doc = await env.DB.prepare("SELECT status, requested_at FROM haifa_documents WHERE scan_id = ? AND doc_id = ?")
    .bind(scanId, docId)
    .first<{ status: string; requested_at: number | null }>();
  if (!doc) return "unknown";
  if (doc.status === "ready" || doc.status === "failed") return doc.status;
  if (doc.status !== "pending") return "failed";
  if (doc.requested_at) return "queued";
  const updated = await env.DB.prepare(
    "UPDATE haifa_documents SET requested_at = ?1 WHERE scan_id = ?2 AND doc_id = ?3 AND status = 'pending' " +
      "AND (SELECT COUNT(*) FROM haifa_documents WHERE scan_id = ?2 AND status = 'pending' AND requested_at IS NOT NULL) < ?4 " +
      "RETURNING doc_id",
  )
    .bind(now(), scanId, docId, MAX_OPEN_REQUESTS)
    .first();
  return updated ? "queued" : "limit";
};

/** How many of a Haifa scan's archive documents have arrived (first delivery + background). */
export const documentCounts = async (
  env: Env,
  scanId: string,
  summary: { documents_total?: number; documents_ready?: number } | null,
): Promise<{ total: number; ready: number; pending: number }> => {
  const row = await env.DB.prepare(
    "SELECT SUM(status = 'ready') AS ready, SUM(status = 'pending') AS pending FROM haifa_documents WHERE scan_id = ?",
  )
    .bind(scanId)
    .first<{ ready: number | null; pending: number | null }>();
  return {
    total: summary?.documents_total ?? 0,
    ready: (summary?.documents_ready ?? 0) + (row?.ready ?? 0),
    pending: row?.pending ?? 0,
  };
};

/** Every minute: resume scans of a dead worker, end scans that have been filling for too long. */
export const haifaSweep = async (env: Env): Promise<void> => {
  const t = now();
  const worker = await state(env, "haifa-worker");
  if (!worker?.last_seen || worker.last_seen < t - WORKER_STALE_S) {
    await env.DB.batch([
      env.DB.prepare(
        "UPDATE scans SET status = 'failed', error = 'The scanner stopped responding.', finished_at = ?1 " +
          "WHERE city = 'haifa' AND status = 'running' AND attempts >= ?2",
      ).bind(t, MAX_FIRST_DELIVERY_TRIES),
      env.DB.prepare(
        "DELETE FROM quota_events WHERE scan_id IN (SELECT id FROM scans WHERE city = 'haifa' AND status = 'failed' " +
          "AND finished_at = ?)",
      ).bind(t),
      env.DB.prepare("UPDATE scans SET status = 'queued', stages_json = '{}' WHERE city = 'haifa' AND status = 'running'"),
    ]);
  }
  await env.DB.prepare(
    "UPDATE haifa_documents SET status = 'skipped' WHERE status = 'pending' AND scan_id IN " +
      "(SELECT id FROM scans WHERE city = 'haifa' AND status = 'filling' AND dispatched_at < ?)",
  )
    .bind(t - FILLING_MAX_S)
    .run();
};
