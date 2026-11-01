import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { WebStandardStreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/webStandardStreamableHttp.js";
import { Buffer } from "node:buffer";
import { z } from "zod";
import type { Env } from "./env";
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
  file?: string | null;
  original_url: string | null;
  note: string | null;
}

export interface Manifest {
  scan_id: string;
  address: { label_he: string; gush?: number; helka?: number };
  building: { building_id: number; year_built?: number; floors?: number };
  archive_folder_no: string | null;
  plans: { plan_no: string; name: string | null; status: string | null; in_force_date: string | null }[];
  documents: ManifestDocument[];
  warnings: string[];
}

const MAX_PAGES_PER_CALL = 3;

const text = (value: unknown) => ({ content: [{ type: "text" as const, text: typeof value === "string" ? value : JSON.stringify(value, null, 1) }] });
const fail = (message: string) => ({ ...text(message), isError: true });

const loadScan = (env: Env, id: string) => env.DB.prepare("SELECT * FROM scans WHERE id = ?").bind(id).first<ScanRow>();

/** The manifest of a finished scan, following a free reuse to the scan that holds the files. */
// A short, predictable link per document (see /d/ in routes/summary.ts), so an AI can link what it cites.
export const docLink = (publicUrl: string, scanId: string, docId: string) => `${publicUrl}/d/${scanId}/${docId}`;

export const loadManifest = async (env: Env, scanId: string): Promise<{ manifest: Manifest; sourceId: string } | string> => {
  const row = await loadScan(env, scanId);
  if (!row) return "Unknown scan_id.";
  const scan = await publicScan({ env }, row);
  if (scan.status !== "done") return `Scan is ${scan.status}; documents are available once it is done.`;
  const sourceId = scan.files_base.split("/")[2];
  const object = await env.FILES.get(`scans/${sourceId}/manifest.json`);
  if (!object) return "The scan's files have expired.";
  return { manifest: (await object.json()) as Manifest, sourceId };
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
    { name: "tel-aviv-archive-scanner", version: "1.0.0" },
    {
      instructions:
        "Research Tel Aviv-Yafo buildings from public municipal records. Typical flow: find_building → start_scan " +
        "(uses one of the user's 3 weekly scans unless the building was scanned in the last 24h) → poll get_scan_status " +
        "every ~30s → list_documents → get_document_pages for the few documents that matter. Documents are scans " +
        "(mostly Hebrew, often handwritten); read the images yourself and cite doc_id and date, linking each doc_id to its link so the user can open it.",
    },
  );

  server.registerTool(
    "find_building",
    {
      title: "Find a building",
      description: "Look up a Tel Aviv-Yafo street address (Hebrew or English, e.g. 'לבונטין 15' or 'Levontin 15').",
      inputSchema: { address: z.string().min(3).max(120) },
    },
    async ({ address }) => {
      const result = await lookupMatches(env, ctx, address);
      if ("error" in result) return fail("Address lookup is unavailable right now; try again shortly.");
      if (!result.matches.length) return text("No matching address. Use a street name and house number.");
      return text(
        result.matches.map((m) => ({
          address_he: m.label_he,
          address_en: m.label_en,
          building_id: m.building.building_id,
          year_built: m.building.year_built ?? null,
          floors: m.building.floors ?? null,
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
        "from find_building. Takes ~3–10 minutes; poll get_scan_status.",
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
        expires_at: scan.expires_at ? new Date(scan.expires_at * 1000).toISOString() : null,
        error: scan.error,
        page: `${env.PUBLIC_URL}/en/scan/${scan.id}`,
      });
    },
  );

  server.registerTool(
    "list_documents",
    {
      title: "List documents",
      description:
        "The scan's index: one row per archive document, plan file or historic map, with type, date, permit/request " +
        "numbers and page count. Start here, then open only what you need.",
      inputSchema: {
        scan_id: z.string().regex(/^[0-9a-f]{32}$/),
        source: z.enum(["archive", "plan", "map"]).optional(),
        year_from: z.number().int().optional(),
        year_to: z.number().int().optional(),
        type_contains: z.string().max(60).optional(),
      },
    },
    async ({ scan_id, source, year_from, year_to, type_contains }) => {
      const loaded = await loadManifest(env, scan_id);
      if (typeof loaded === "string") return fail(loaded);
      const { manifest } = loaded;
      const rows = manifest.documents
        .filter((d) => !source || d.source === source)
        .filter((d) => year_from == null || (d.year ?? 0) >= year_from)
        .filter((d) => year_to == null || (d.year ?? 9999) <= year_to)
        .filter((d) => !type_contains || `${d.doc_type ?? ""} ${d.title}`.includes(type_contains))
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
        }));
      return text({
        address: manifest.address.label_he,
        building_id: manifest.building.building_id,
        year_built: manifest.building.year_built ?? null,
        building_file: manifest.archive_folder_no,
        gush_helka: `${manifest.address.gush ?? "?"}/${manifest.address.helka ?? "?"}`,
        plans: manifest.plans,
        documents: rows,
      });
    },
  );

  server.registerTool(
    "get_document_pages",
    {
      title: "Read document pages",
      description: `Page images of one document (max ${MAX_PAGES_PER_CALL} per call, 1-based). Read them visually.`,
      inputSchema: {
        scan_id: z.string().regex(/^[0-9a-f]{32}$/),
        doc_id: z.string().regex(/^[APM]-\d{3}$/),
        pages: z.array(z.number().int().min(1)).min(1).max(MAX_PAGES_PER_CALL).optional(),
      },
    },
    async ({ scan_id, doc_id, pages }) => {
      const loaded = await loadManifest(env, scan_id);
      if (typeof loaded === "string") return fail(loaded);
      const doc = loaded.manifest.documents.find((d) => d.doc_id === doc_id);
      if (!doc) return fail(`No document ${doc_id} in this scan.`);
      const wanted = (pages ?? [1]).filter((p) => p <= doc.page_images.length);
      if (!wanted.length) return fail(`${doc_id} has ${doc.page_images.length} page image(s).`);
      const images = await Promise.all(wanted.map((p) => imageFromR2(env, `scans/${loaded.sourceId}/${doc.page_images[p - 1]}`)));
      const header = `${doc_id} · ${doc.doc_type ?? doc.title} · ${doc.date ?? doc.year ?? "undated"} · pages ${wanted.join(", ")} of ${doc.pages}`;
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
              `Analyze Tel Aviv Archive Scanner scan ${scan_id}. Call list_documents first. Group documents by date and ` +
              "type; identify building permits (היתר בניה), permit requests, plans and correspondence. Open only the " +
              "documents you need with get_document_pages, earliest permits first. Write a chronological history of " +
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
