import { Hono } from "hono";
import type { AppContext } from "../env";
import { now } from "../env";
import type { ScanRow } from "../lib/scans";
import { contentType } from "./internal";

/** Streams a finished scan's files from R2. Links work until the scan expires. */
export const files = new Hono<AppContext>();

const DOWNLOAD_NAMES: Record<string, string> = {
  "bundle.zip": "scan.zip",
  "archive.pptx": "archive.pptx",
  "maps_plans.pptx": "maps-and-plans.pptx",
  "index.csv": "index.csv",
};

const parseRange = (header: string | undefined): R2Range | undefined => {
  const match = /^bytes=(\d*)-(\d*)$/.exec(header ?? "");
  if (!match || (!match[1] && !match[2])) return undefined;
  if (!match[1]) return { suffix: Number(match[2]) };
  const offset = Number(match[1]);
  return match[2] ? { offset, length: Number(match[2]) - offset + 1 } : { offset };
};

files.get("/:id/*", async (c) => {
  const id = c.req.param("id");
  const rel = decodeURIComponent(new URL(c.req.url).pathname.split(`/files/${id}/`)[1] ?? "");
  if (!rel || rel.includes("..")) return c.notFound();
  const scan = await c.env.DB.prepare("SELECT status, expires_at, label_en FROM scans WHERE id = ? AND cached_from IS NULL")
    .bind(id)
    .first<Pick<ScanRow, "status" | "expires_at" | "label_en">>();
  if (!scan || scan.status !== "done" || (scan.expires_at ?? 0) < now()) {
    return c.json({ error: "expired_or_missing" }, 410);
  }

  const range = parseRange(c.req.header("range"));
  const object = await c.env.FILES.get(`scans/${id}/${rel}`, range ? { range } : {});
  if (!object) return c.notFound();

  const headers = new Headers();
  object.writeHttpMetadata(headers);
  headers.set("Content-Type", headers.get("Content-Type") ?? contentType(rel));
  headers.set("ETag", object.httpEtag);
  headers.set("Accept-Ranges", "bytes");
  headers.set("Cache-Control", "private, max-age=3600");
  headers.set("X-Content-Type-Options", "nosniff");
  const name = DOWNLOAD_NAMES[rel];
  if (name) {
    const label = scan.label_en.replace(/[^\w\- ]+/g, "").trim().replace(/\s+/g, "-") || "scan";
    headers.set("Content-Disposition", `attachment; filename="${label}-${name}"`);
  }
  if (range && object.range && "offset" in object.range) {
    const offset = object.range.offset ?? 0;
    const length = object.range.length ?? object.size - offset;
    headers.set("Content-Range", `bytes ${offset}-${offset + length - 1}/${object.size}`);
    return new Response(object.body, { status: 206, headers });
  }
  return new Response(object.body, { headers });
});
