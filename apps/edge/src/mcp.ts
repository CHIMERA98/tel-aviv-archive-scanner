import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { WebStandardStreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/webStandardStreamableHttp.js";
import { Buffer } from "node:buffer";
import { z } from "zod";
import type { Env } from "./env";
import { baseUrl, type City, detectCity } from "./lib/cities";
import { requestDocument } from "./lib/haifa";
import { dispatch, quotaState, type ScanRow } from "./lib/scans";
import { lookupMatches, publicScan, startScan } from "./routes/api";

export interface McpProps {
  userId: string;
  email: string;
}

export interface ManifestDocument {
  doc_id: string;
  source: "archive" | "plan" | "map";
  title: string;
  doc_type: string | null;
  date: string | null;
  year: number | null;
  permit_no: string | null;
  request_no: string | null;
  plan_no: string | null;
  plan_status: string | null;
  pages: number;
  page_images: string[];
  page_tiles?: string[][]; // per page image: overlapping tiles of a long sheet, or []
  sheets?: { image: string; cells: string[] }[]; // thumbnail grids; cells read p3 or p3.t2 (tile 2 of page 3)
  file?: string | null;
  original_url: string | null;
  note: string | null;
  status?: "ready" | "pending" | "failed"; // Haifa documents arrive in the background
}

export interface Manifest {
  scan_id: string;
  city?: City;
  address: { label_he: string; gush?: number; helka?: number; neighborhood?: string | null };
  building: {
    key?: string;
    building_id?: number | null;
    year_built?: number;
    floors?: number;
    height_m?: number | null;
    conservation?: { status?: string | null; grade?: string | null; file_no?: string | null; survey?: string | null } | null;
  };
  archive_folder_no: string | null;
  building_files?: string[];
  requests?: unknown[];
  plans: { plan_no: string; name: string | null; status: string | null; in_force_date: string | null }[];
  documents: ManifestDocument[];
  warnings: string[];
}

const MAX_PAGES_PER_CALL = 3;
// browse_pages: stop adding contact sheets once a call holds this many images or thumbnails.
const MAX_SHEETS_PER_CALL = 6;
const MAX_CELLS_PER_CALL = 72;

const text = (value: unknown) => ({ content: [{ type: "text" as const, text: typeof value === "string" ? value : JSON.stringify(value, null, 1) }] });
const fail = (message: string) => ({ ...text(message), isError: true });

type Filters = { source?: ManifestDocument["source"]; year_from?: number; year_to?: number; type_contains?: string };

const matching = (documents: ManifestDocument[], f: Filters) =>
  documents
    .filter((d) => !f.source || d.source === f.source)
    .filter((d) => f.year_from == null || (d.year ?? 0) >= f.year_from)
    .filter((d) => f.year_to == null || (d.year ?? 9999) <= f.year_to)
    .filter((d) => !f.type_contains || `${d.doc_type ?? ""} ${d.title}`.includes(f.type_contains));

const loadScan = (env: Env, id: string) => env.DB.prepare("SELECT * FROM scans WHERE id = ?").bind(id).first<ScanRow>();

/** The manifest of a finished scan, following a free reuse to the scan that holds the files. */
// A short, predictable link per document (see /d/ in routes/summary.ts), so an AI can link what it cites.
export const docLink = (publicUrl: string, scanId: string, docId: string) => `${publicUrl}/d/${scanId}/${docId}`;

export const loadManifest = async (
  env: Env,
  scanId: string,
): Promise<{ manifest: Manifest; sourceId: string; arriving: { total: number; ready: number } | null } | string> => {
  const row = await loadScan(env, scanId);
  if (!row) return "Unknown scan_id.";
  const scan = await publicScan({ env }, row);
  if (scan.status !== "done" && scan.status !== "filling") {
    return `Scan is ${scan.status}; documents are available once it is done.`;
  }
  const sourceId = scan.files_base.split("/")[2];
  const object = await env.FILES.get(`scans/${sourceId}/manifest.json`);
  if (!object) return "The scan's files have expired.";
  // Haifa, still filling: how many documents have arrived so far.
  const arriving = scan.status === "filling" ? (scan.documents ?? { total: 0, ready: 0 }) : null;
  return { manifest: (await object.json()) as Manifest, sourceId, arriving };
};

const imageFromR2 = async (env: Env, key: string) => {
  const object = await env.FILES.get(key);
  if (!object) return null;
  return {
    type: "image" as const,
    data: Buffer.from(await object.arrayBuffer()).toString("base64"),
    mimeType: "image/jpeg",
  };
};

export const buildServer = (env: Env, ctx: ExecutionContext, props: McpProps) => {
  const server = new McpServer(
    { name: "tel-aviv-archive-scanner", version: "1.1.0" },
    {
      instructions:
        "Research buildings in Tel Aviv-Yafo and Haifa from public municipal records. Typical flow: find_building → " +
        "start_scan (uses one of the user's 3 weekly scans unless the building was scanned in the last 24h) → poll " +
        "get_scan_status every ~30s → list_documents → get_document_pages for the few documents that matter. " +
        "To find photographs, renderings or particular drawings, skim with browse_pages (thumbnail grids of every " +
        "page) instead of opening pages one by one. Long plan sheets are split into tiles; open them with " +
        "get_document_pages' `tiles`. " +
        "Haifa scans open after ~10–15 minutes (status \"filling\") with every document listed and the key ones " +
        "downloaded; the rest arrive in the background, and asking for a pending one moves it to the front. Haifa " +
        "scans also list every permit request with its events and committee decisions (requests.md). Documents are " +
        "scans (mostly Hebrew, often handwritten); read the images yourself and cite doc_id and date, linking each " +
        "doc_id to its link so the user can open it.",
    },
  );

  server.registerTool(
    "find_building",
    {
      title: "Find a building",
      description:
        "Look up a street address in Tel Aviv-Yafo or Haifa (Hebrew or English, e.g. 'לבונטין 15', 'Levontin 15', " +
        "'בן גוריון 50 חיפה'). Haifa street names are in Hebrew.",
      inputSchema: {
        address: z.string().min(3).max(120),
        city: z.enum(["tel-aviv", "haifa"]).optional().describe("Default: Haifa if the address says so, else Tel Aviv"),
      },
    },
    async ({ address, city }) => {
      const where = city ?? detectCity(address, "tel-aviv");
      const result = await lookupMatches(env, ctx, address, where);
      if ("error" in result) return fail("Address lookup is unavailable right now; try again shortly.");
      if (!result.matches.length) return text("No matching address. Use a street name and house number.");
      return text(
        result.matches.map((m) => ({
          city: m.city,
          address_he: m.label_he,
          address_en: m.label_en,
          building_key: m.building.key,
          year_built: m.building.year_built ?? null,
          floors: m.building.floors ?? null,
          height_m: m.building.height_m ?? null,
          selection: m.selection,
        })),
      );
    },
  );

  server.registerTool(
    "start_scan",
    {
      title: "Start a scan",
      description:
        "Collect historic maps, zoning plans and engineering-archive documents for a building. Pass the `selection` " +
        "from find_building. Tel Aviv: ~3–10 minutes. Haifa: opens after ~10–15 minutes, then keeps adding " +
        "documents. Poll get_scan_status.",
      inputSchema: { selection: z.string().min(10) },
    },
    async ({ selection }) => {
      const result = await startScan(env, props.userId, selection, "mcp");
      if (!result.ok) {
        const reason =
          result.error === "weekly_quota"
            ? "The user has used their 3 scans this week."
            : result.error === "daily_cap"
              ? "The service hit today's scan limit; try tomorrow."
              : "The selection is invalid or expired; call find_building again.";
        return fail(reason);
      }
      if (!result.reused) ctx.waitUntil(dispatch(env));
      const quota = await quotaState(env, props.userId);
      return text({ scan_id: result.scanId, reused_recent_scan: result.reused, scans_left_this_week: quota.weeklyLeft });
    },
  );

  server.registerTool(
    "get_scan_status",
    {
      title: "Scan status",
      description: "Progress of a scan: queue position, stages, and when files expire.",
      inputSchema: { scan_id: z.string().regex(/^[0-9a-f]{32}$/) },
    },
    async ({ scan_id }) => {
      const row = await loadScan(env, scan_id);
      if (!row) return fail("Unknown scan_id.");
      const scan = await publicScan({ env }, row);
      return text({
        status: scan.status,
        queue_position: scan.queue_position,
        stages: scan.stages,
        counts: scan.summary?.counts ?? null,
        documents: scan.documents, // Haifa: { total, ready, pending }
        expires_at: scan.expires_at ? new Date(scan.expires_at * 1000).toISOString() : null,
        error: scan.error,
        page: `${baseUrl(env, row.city)}/en/scan/${scan.id}`,
      });
    },
  );

  const filters = {
    source: z.enum(["archive", "plan", "map"]).optional(),
    year_from: z.number().int().optional(),
    year_to: z.number().int().optional(),
    type_contains: z.string().max(60).optional(),
  };

  server.registerTool(
    "list_documents",
    {
      title: "List documents",
      description:
        "The scan's index: one row per archive document, plan file or historic map, with type, date, permit/request " +
        "numbers and page count. Start here, then open only what you need.",
      inputSchema: { scan_id: z.string().regex(/^[0-9a-f]{32}$/), ...filters },
    },
    async ({ scan_id, source, year_from, year_to, type_contains }) => {
      const loaded = await loadManifest(env, scan_id);
      if (typeof loaded === "string") return fail(loaded);
      const { manifest } = loaded;
      const rows = matching(manifest.documents, { source, year_from, year_to, type_contains })
        .map((d) => ({
          doc_id: d.doc_id,
          source: d.source,
          type: d.doc_type ?? d.title,
          date: d.date ?? d.year,
          permit_no: d.permit_no ?? undefined,
          request_no: d.request_no ?? undefined,
          plan_no: d.plan_no ?? undefined,
          plan_status: d.plan_status ?? undefined,
          pages: d.pages,
          link: docLink(env.PUBLIC_URL, scan_id, d.doc_id),
          note: d.note ?? undefined,
          status: d.status && d.status !== "ready" ? d.status : undefined,
          title: d.title !== d.doc_type ? d.title : undefined,
        }));
      return text({
        city: manifest.city ?? "tel-aviv",
        address: manifest.address.label_he,
        building: manifest.building.key ?? manifest.building.building_id,
        year_built: manifest.building.year_built ?? null,
        height_m: manifest.building.height_m ?? undefined,
        conservation: manifest.building.conservation ?? undefined,
        building_file: manifest.archive_folder_no,
        gush_helka: `${manifest.address.gush ?? "?"}/${manifest.address.helka ?? "?"}`,
        permit_requests: manifest.requests?.length
          ? `${manifest.requests.length} — read requests.md: ${env.PUBLIC_URL}/files/${loaded.sourceId}/requests.md`
          : undefined,
        plans: manifest.plans,
        documents: rows,
      });
    },
  );

  server.registerTool(
    "browse_pages",
    {
      title: "Skim pages as thumbnails",
      description:
        "Contact sheets: grids of small labelled thumbnails of every page of the matching documents (long sheets " +
        "appear as their tiles), so you can spot photographs, renderings, maps or particular drawings without " +
        "opening pages one by one. Cells read p3 (page 3) or p3.t2 (tile 2 of page 3); open what you find with " +
        "get_document_pages. Pass next_cursor back to continue.",
      inputSchema: {
        scan_id: z.string().regex(/^[0-9a-f]{32}$/),
        doc_ids: z.array(z.string().regex(/^[APM]-\d{3}$/)).max(40).optional(),
        ...filters,
        cursor: z.number().int().min(0).optional(),
      },
    },
    async ({ scan_id, doc_ids, source, year_from, year_to, type_contains, cursor }) => {
      const loaded = await loadManifest(env, scan_id);
      if (typeof loaded === "string") return fail(loaded);
      const docs = matching(loaded.manifest.documents, { source, year_from, year_to, type_contains }).filter(
        (d) => !doc_ids || doc_ids.includes(d.doc_id),
      );
      const all = docs.flatMap((d) => (d.sheets ?? []).map((sheet, i, sheets) => ({ doc: d, sheet, i, of: sheets.length })));
      const start = cursor ?? 0;
      const picked: typeof all = [];
      let cells = 0;
      for (const item of all.slice(start)) {
        if (picked.length && (picked.length >= MAX_SHEETS_PER_CALL || cells + item.sheet.cells.length > MAX_CELLS_PER_CALL)) break;
        picked.push(item);
        cells += item.sheet.cells.length;
      }
      const notes: string[] = [];
      const pending = docs.filter((d) => d.status === "pending").map((d) => d.doc_id);
      if (pending.length) notes.push(`Still downloading (no thumbnails yet): ${pending.join(", ")}.`);
      const bare = docs
        .filter((d) => d.source !== "map" && d.status !== "pending" && d.page_images.length && !d.sheets?.length)
        .map((d) => d.doc_id);
      if (bare.length) notes.push(`No thumbnails (open with get_document_pages): ${bare.join(", ")}.`);
      if (!picked.length) return text(notes.join(" ") || "No thumbnails match.");
      const next = start + picked.length < all.length ? start + picked.length : null;
      const content: ({ type: "text"; text: string } | { type: "image"; data: string; mimeType: string })[] = [];
      for (const { doc, sheet, i, of } of picked) {
        const image = await imageFromR2(env, `scans/${loaded.sourceId}/${sheet.image}`);
        if (!image) continue;
        const label = `${doc.doc_id} · ${doc.doc_type ?? doc.title} · ${doc.date ?? doc.year ?? "undated"}`;
        content.push({ type: "text", text: `${label} — sheet ${i + 1}/${of}: ${sheet.cells.join(", ")}` }, image);
      }
      const footer = [`Showing ${picked.length} of ${all.length} sheets.`, ...notes];
      if (next != null) footer.push(`More: call again with cursor ${next}.`);
      content.push({ type: "text", text: footer.join(" ") });
      return { content };
    },
  );

  server.registerTool(
    "get_document_pages",
    {
      title: "Read document pages",
      description:
        `Page images of one document (max ${MAX_PAGES_PER_CALL} per call, 1-based). Read them visually. Long plan ` +
        "sheets are also split into tiles for detail: pass one page in `pages` and the tile numbers in `tiles`.",
      inputSchema: {
        scan_id: z.string().regex(/^[0-9a-f]{32}$/),
        doc_id: z.string().regex(/^[APM]-\d{3}$/),
        pages: z.array(z.number().int().min(1)).min(1).max(MAX_PAGES_PER_CALL).optional(),
        tiles: z.array(z.number().int().min(1)).min(1).max(MAX_PAGES_PER_CALL).optional(),
      },
    },
    async ({ scan_id, doc_id, pages, tiles }) => {
      const loaded = await loadManifest(env, scan_id);
      if (typeof loaded === "string") return fail(loaded);
      const doc = loaded.manifest.documents.find((d) => d.doc_id === doc_id);
      if (!doc) return fail(`No document ${doc_id} in this scan.`);
      if (doc.status === "pending") {
        const state = await requestDocument(env, loaded.sourceId, doc_id);
        if (state === "queued") ctx.waitUntil(dispatch(env));
        if (state === "queued" || state === "limit") {
          return text(
            `${doc_id} is still downloading from the municipal site; it has moved to the front of the queue. ` +
              "Try again in about a minute, and meanwhile read the documents that are ready.",
          );
        }
        if (state === "ready") return fail(`${doc_id} just arrived; call list_documents again, then retry.`);
      }
      const tilesOf = (page: number) => doc.page_tiles?.[page - 1] ?? [];
      const describe = `${doc_id} · ${doc.doc_type ?? doc.title} · ${doc.date ?? doc.year ?? "undated"}`;
      const read = (paths: string[]) => Promise.all(paths.map((p) => imageFromR2(env, `scans/${loaded.sourceId}/${p}`)));

      if (tiles) {
        if (pages && pages.length !== 1) return fail("With `tiles`, pass exactly one page in `pages`.");
        const page = pages?.[0] ?? 1;
        const available = tilesOf(page);
        if (!available.length) return fail(`Page ${page} of ${doc_id} has no tiles; read it whole.`);
        const wanted = tiles.filter((n) => n <= available.length);
        if (!wanted.length) return fail(`Page ${page} of ${doc_id} has ${available.length} tiles.`);
        const images = await read(wanted.map((n) => available[n - 1]));
        const header = `${describe} · page ${page}, tiles ${wanted.join(", ")} of ${available.length} (left to right or top to bottom, overlapping)`;
        return { content: [{ type: "text" as const, text: header }, ...images.filter((i) => i !== null)] };
      }

      const wanted = (pages ?? [1]).filter((p) => p <= doc.page_images.length);
      if (!wanted.length) return fail(`${doc_id} has ${doc.page_images.length} page image(s).`);
      const images = await read(wanted.map((p) => doc.page_images[p - 1]));
      const tiled = wanted
        .filter((p) => tilesOf(p).length)
        .map((p) => `page ${p} is a long sheet in ${tilesOf(p).length} tiles (pages: [${p}], tiles: [1, 2, 3] for detail)`);
      const header = [`${describe} · pages ${wanted.join(", ")} of ${doc.pages}`, ...tiled].join(" · ");
      return { content: [{ type: "text" as const, text: header }, ...images.filter((i) => i !== null)] };
    },
  );

  server.registerPrompt(
    "analyze_scan",
    {
      title: "Analyze a scan",
      description: "Instructions for writing a building history from a finished scan.",
      argsSchema: { scan_id: z.string(), language: z.enum(["he", "en"]).optional() },
    },
    ({ scan_id, language }) => ({
      messages: [
        {
          role: "user" as const,
          content: {
            type: "text" as const,
            text:
              `Analyze Archive Scanner scan ${scan_id}. Call list_documents first (for Haifa, also read requests.md). ` +
              "Group documents by date and " +
              "type; identify building permits (היתר בניה), permit requests, plans and correspondence. Open only the " +
              "documents you need with get_document_pages, earliest permits first; to find photographs of the " +
              "building, skim with browse_pages. Write a chronological history of " +
              "the building (construction, additions, changes of use, preservation), list key permits with numbers and " +
              "dates, explain the zoning plans that apply, and list open questions. Cite doc_id and date for every claim " +
              `and say when something is unreadable. Answer in ${language === "he" ? "Hebrew" : "English"}.`,
          },
        },
      ],
    }),
  );

  return server;
};

/** Stateless Streamable HTTP endpoint; the OAuth provider has already authenticated the caller. */
export const mcpHandler = {
  async fetch(request: Request, env: Env, ctx: ExecutionContext & { props?: McpProps }) {
    if (!ctx.props?.userId) return new Response("unauthorized", { status: 401 });
    const server = buildServer(env, ctx, ctx.props);
    const transport = new WebStandardStreamableHTTPServerTransport({ sessionIdGenerator: undefined, enableJsonResponse: true });
    await server.connect(transport);
    return transport.handleRequest(request);
  },
};
