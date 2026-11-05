import type { Env } from "../env";
import { now, num } from "../env";
import type { City } from "./cities";
import { randomId } from "./crypto";
import { runScannerJob } from "./gcp";
import { haifaSweep, startHaifaWorker } from "./haifa";

// "linked": a free reuse of another scan that is still queued or running; it shows that scan's progress.
// "filling": a Haifa scan that can be opened while its remaining documents arrive (docs/HAIFA_PLAN.md §2).
export type ScanStatus = "queued" | "starting" | "running" | "filling" | "done" | "failed" | "expired" | "linked";

export interface ScanRow {
  id: string;
  user_id: string;
  city: City;
  building_key: string;
  building_id: number | null;
  address_json: string;
  building_json: string;
  label_he: string;
  label_en: string;
  origin: string;
  status: ScanStatus;
  stages_json: string;
  summary_json: string | null;
  error: string | null;
  attempts: number;
  cached_from: string | null;
  queued_at: number;
  dispatched_at: number | null;
  last_progress_at: number | null;
  finished_at: number | null;
  expires_at: number | null;
}

export interface Selection {
  address: Record<string, unknown> & { label_he: string; label_en: string; city?: City };
  building: Record<string, unknown> & { key: string; building_id?: number | null; city?: City };
}

const ACTIVE = "('starting','running')";
const WEEK_S = 7 * 86400;
const DAY_S = 86400;
const STALE_S = 10 * 60;
const MAX_ATTEMPTS = 2;

export const quotaState = async (env: Env, userId: string) => {
  const t = now();
  const [mine, all, user] = await env.DB.batch([
    env.DB.prepare("SELECT COUNT(*) AS n, MIN(created_at) AS first FROM quota_events WHERE user_id = ? AND created_at > ?").bind(userId, t - WEEK_S),
    // Unlimited accounts' scans don't use up the shared daily cap either.
    env.DB.prepare(
      "SELECT COUNT(*) AS n FROM quota_events q JOIN users u ON u.id = q.user_id WHERE q.created_at > ? AND u.unlimited = 0",
    ).bind(t - DAY_S),
    env.DB.prepare("SELECT unlimited FROM users WHERE id = ?").bind(userId),
  ]);
  const weekly = num(env.WEEKLY_QUOTA, 3);
  const used = (mine.results[0] as { n: number; first: number | null }) ?? { n: 0, first: null };
  const today = (all.results[0] as { n: number })?.n ?? 0;
  if ((user.results[0] as { unlimited: number } | undefined)?.unlimited) {
    return { unlimited: true, weeklyLimit: null, weeklyUsed: used.n, weeklyLeft: null, nextFreeAt: null, dailyCapReached: false };
  }
  return {
    unlimited: false,
    weeklyLimit: weekly,
    weeklyUsed: used.n,
    weeklyLeft: Math.max(0, weekly - used.n),
    nextFreeAt: used.n >= weekly && used.first ? used.first + WEEK_S : null,
    dailyCapReached: today >= num(env.DAILY_CAP, 30),
  };
};

/** A finished, unexpired scan of the same building that can be reused for free. */
export const findCachedScan = (env: Env, buildingKey: string) =>
  env.DB.prepare(
    "SELECT * FROM scans WHERE building_key = ? AND status = 'done' AND cached_from IS NULL AND expires_at > ? " +
      "ORDER BY finished_at DESC LIMIT 1",
  )
    .bind(buildingKey, now() + 3600) // at least an hour of life left
    .first<ScanRow>();

/** A scan of this building that is already queued, running or filling; joining it is free too. */
export const findActiveScan = (env: Env, buildingKey: string) =>
  env.DB.prepare(
    "SELECT * FROM scans WHERE building_key = ? AND status IN ('queued','starting','running','filling') " +
      "AND cached_from IS NULL ORDER BY queued_at LIMIT 1",
  )
    .bind(buildingKey)
    .first<ScanRow>();

export const createScan = async (
  env: Env,
  userId: string,
  selection: Selection,
  origin: "web" | "mcp",
  cachedFrom: ScanRow | null,
): Promise<string> => {
  const id = randomId();
  const t = now();
  const status: ScanStatus = cachedFrom ? (cachedFrom.status === "done" ? "done" : "linked") : "queued";
  const statements = [
    env.DB.prepare(
      "INSERT INTO scans (id, user_id, city, building_key, building_id, address_json, building_json, label_he, label_en, " +
        "origin, status, stages_json, summary_json, cached_from, queued_at, finished_at, expires_at) " +
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
    ).bind(
      id,
      userId,
      selection.building.city ?? "tel-aviv",
      selection.building.key,
      selection.building.building_id ?? null,
      JSON.stringify(selection.address),
      JSON.stringify(selection.building),
      selection.address.label_he,
      selection.address.label_en,
      origin,
      status,
      cachedFrom?.stages_json ?? "{}",
      cachedFrom?.summary_json ?? null,
      cachedFrom?.id ?? null,
      t,
      cachedFrom?.finished_at ?? null,
      cachedFrom?.expires_at ?? null,
    ),
  ];
  if (!cachedFrom) {
    statements.push(env.DB.prepare("INSERT INTO quota_events (scan_id, user_id, created_at) VALUES (?, ?, ?)").bind(id, userId, t));
  }
  await env.DB.batch(statements);
  return id;
};

/** The scan whose files and progress a row actually shows (itself, or the scan it reuses). */
export const sourceScan = async (env: Env, row: ScanRow): Promise<ScanRow> => {
  if (!row.cached_from) return row;
  return (await env.DB.prepare("SELECT * FROM scans WHERE id = ?").bind(row.cached_from).first<ScanRow>()) ?? row;
};

export const queuePosition = async (env: Env, row: ScanRow): Promise<number | null> => {
  if (row.status !== "queued") return null;
  const result = await env.DB.prepare("SELECT COUNT(*) AS n FROM scans WHERE status = 'queued' AND queued_at < ?")
    .bind(row.queued_at)
    .first<{ n: number }>();
  return (result?.n ?? 0) + 1;
};

/**
 * Start queued Tel Aviv scans while fewer than MAX_RUNNING are active, and wake the Haifa worker when Haifa has work.
 * Safe to call concurrently.
 */
export const dispatch = async (env: Env): Promise<string[]> => {
  const started: string[] = [];
  const limit = num(env.MAX_RUNNING, 2);
  for (let i = 0; i < limit; i++) {
    // One statement, so D1's single writer makes the "fewer than limit" check and the claim atomic.
    const claimed = await env.DB.prepare(
      `UPDATE scans SET status = 'starting', dispatched_at = ?1, last_progress_at = ?1, attempts = attempts + 1
       WHERE id = (SELECT id FROM scans WHERE status = 'queued' AND city = 'tel-aviv' ORDER BY queued_at LIMIT 1)
         AND (SELECT COUNT(*) FROM scans WHERE status IN ${ACTIVE} AND city = 'tel-aviv') < ?2
       RETURNING id`,
    )
      .bind(now(), limit)
      .first<{ id: string }>();
    if (!claimed) break;
    try {
      await runScannerJob(env, claimed.id);
      started.push(claimed.id);
    } catch (err) {
      console.error(`dispatch ${claimed.id}:`, err);
      await env.DB.prepare("UPDATE scans SET status = 'queued' WHERE id = ? AND status = 'starting'").bind(claimed.id).run();
      break;
    }
  }
  try {
    if (await startHaifaWorker(env)) started.push("haifa-worker");
  } catch (err) {
    console.error("haifa worker start:", err);
  }
  return started;
};

/** Housekeeping: retry or fail scans that stopped reporting, expire old results. */
export const sweep = async (env: Env): Promise<void> => {
  const t = now();
  const stale = await env.DB.prepare(
    `SELECT id, attempts FROM scans WHERE status IN ${ACTIVE} AND city = 'tel-aviv' AND last_progress_at < ?`,
  )
    .bind(t - STALE_S)
    .all<{ id: string; attempts: number }>();
  for (const scan of stale.results) {
    if (scan.attempts < MAX_ATTEMPTS) {
      await env.DB.prepare("UPDATE scans SET status = 'queued', stages_json = '{}' WHERE id = ?").bind(scan.id).run();
    } else {
      await failScan(env, scan.id, "The scanner stopped responding.", true);
    }
  }
  await haifaSweep(env);
  const expiring = await env.DB.prepare(
    "UPDATE scans SET status = 'expired' WHERE status = 'done' AND expires_at < ? RETURNING id, cached_from",
  )
    .bind(t)
    .all<{ id: string; cached_from: string | null }>();
  for (const scan of expiring.results) if (!scan.cached_from) await deleteScanFiles(env, scan.id);
  await env.DB.batch([
    env.DB.prepare(
      "DELETE FROM haifa_documents WHERE scan_id IN (SELECT id FROM scans WHERE status IN ('expired','failed'))",
    ),
    env.DB.prepare("DELETE FROM login_tokens WHERE expires_at < ?").bind(t - DAY_S),
    env.DB.prepare("DELETE FROM sessions WHERE expires_at < ?").bind(t),
  ]);
};

/**
 * An expired scan's files go at once. The bucket's lifecycle rule on scans/ is only a backstop: it counts from each
 * file's upload, and a Haifa scan uploads its first files hours before it is done and its 24 hours begin.
 */
export const deleteScanFiles = async (env: Env, scanId: string): Promise<void> => {
  let cursor: string | undefined;
  do {
    const page = await env.FILES.list({ prefix: `scans/${scanId}/`, cursor });
    if (page.objects.length) await env.FILES.delete(page.objects.map((o) => o.key));
    cursor = page.truncated ? page.cursor : undefined;
  } while (cursor);
};

/** Mark a scan (and every row reusing it) failed. Refunds quota when it wasn't the user's fault. */
export const failScan = async (env: Env, id: string, error: string, refund: boolean): Promise<void> => {
  const statements = [
    env.DB.prepare("UPDATE scans SET status = 'failed', error = ?, finished_at = ? WHERE id = ? OR cached_from = ?").bind(
      error,
      now(),
      id,
      id,
    ),
  ];
  if (refund) statements.push(env.DB.prepare("DELETE FROM quota_events WHERE scan_id = ?").bind(id));
  await env.DB.batch(statements);
};

export const retentionSeconds = (env: Env) => num(env.RETENTION_HOURS, 24) * 3600;
