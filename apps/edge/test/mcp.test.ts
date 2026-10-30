import { createExecutionContext, env, waitOnExecutionContext } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import worker from "../src/index";
import { mcpHandler } from "../src/mcp";

const SCAN = "a".repeat(32);

const rpc = async (method: string, params: Record<string, unknown> = {}) => {
  const ctx = Object.assign(createExecutionContext(), { props: { userId: "u1", email: "u1@example.com" } });
  const response = await mcpHandler.fetch(
    new Request("https://scanner.test/mcp", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json, text/event-stream" },
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }),
    }),
    env,
    ctx,
  );
  await waitOnExecutionContext(ctx);
  return (await response.json()) as { result?: any; error?: any };
};

const seedDoneScan = async () => {
  const t = Math.floor(Date.now() / 1000);
  await env.DB.prepare("INSERT OR IGNORE INTO users (id, email, created_at) VALUES ('u1', 'u1@example.com', ?)").bind(t).run();
  await env.DB.prepare(
    "INSERT OR REPLACE INTO scans (id, user_id, building_id, address_json, building_json, label_he, label_en, status, " +
      "summary_json, queued_at, finished_at, expires_at) VALUES (?, 'u1', 2287, '{}', '{\"building_id\":2287}', 'הגר\"א 27', " +
      "'Haghara 27', 'done', '{\"counts\":{\"archive\":2,\"plan\":0,\"map\":0}}', ?, ?, ?)",
  )
    .bind(SCAN, t, t, t + 86400)
    .run();
  const manifest = {
    scan_id: SCAN,
    address: { label_he: 'הגר"א 27', gush: 6977, helka: 22 },
    building: { building_id: 2287, year_built: 1946 },
    archive_folder_no: "03840270",
    plans: [],
    documents: [
      { doc_id: "A-001", source: "archive", title: "היתר-תכנית חתומה", doc_type: "היתר-תכנית חתומה", date: "1937-05-30", year: 1937, permit_no: "123", request_no: null, plan_no: null, plan_status: null, pages: 2, page_images: ["pages/A-001/A-001-001.jpg", "pages/A-001/A-001-002.jpg"], original_url: null, note: null },
      { doc_id: "A-002", source: "archive", title: "מכתב מידע", doc_type: "מכתב מידע", date: "2006-06-18", year: 2006, permit_no: null, request_no: null, plan_no: null, plan_status: null, pages: 1, page_images: ["pages/A-002/A-002-001.jpg"], original_url: null, note: null },
    ],
    warnings: [],
  };
  await env.FILES.put(`scans/${SCAN}/manifest.json`, JSON.stringify(manifest));
  await env.FILES.put(`scans/${SCAN}/pages/A-001/A-001-001.jpg`, new Uint8Array([0xff, 0xd8, 0xff, 0xd9]));
};

describe("MCP over OAuth", () => {
  it("rejects /mcp without a token and advertises OAuth metadata", async () => {
    const ctx = createExecutionContext();
    const denied = await worker.fetch(new Request("https://scanner.test/mcp", { method: "POST", body: "{}" }), env, ctx);
    expect(denied.status).toBe(401);
    expect(denied.headers.get("www-authenticate")).toMatch(/Bearer/);
    const meta = await worker.fetch(new Request("https://scanner.test/.well-known/oauth-authorization-server"), env, ctx);
    const body = (await meta.json()) as { registration_endpoint: string; authorization_endpoint: string };
    expect(body.registration_endpoint).toBe("https://scanner.test/register");
    expect(body.authorization_endpoint).toBe("https://scanner.test/authorize");
    await waitOnExecutionContext(ctx);
  });

  it("lists the tools", async () => {
    const { result } = await rpc("tools/list");
    expect(result.tools.map((t: { name: string }) => t.name).sort()).toEqual([
      "find_building",
      "get_document_pages",
      "get_scan_status",
      "list_documents",
      "start_scan",
    ]);
  });

  it("lists documents of a finished scan with filters", async () => {
    await seedDoneScan();
    const { result } = await rpc("tools/call", { name: "list_documents", arguments: { scan_id: SCAN, year_to: 1950 } });
    const data = JSON.parse(result.content[0].text);
    expect(data.building_file).toBe("03840270");
    expect(data.documents.map((d: { doc_id: string }) => d.doc_id)).toEqual(["A-001"]);
  });

  it("returns page images", async () => {
    await seedDoneScan();
    const { result } = await rpc("tools/call", {
      name: "get_document_pages",
      arguments: { scan_id: SCAN, doc_id: "A-001", pages: [1] },
    });
    expect(result.content[0].text).toContain("A-001");
    expect(result.content[1]).toMatchObject({ type: "image", mimeType: "image/jpeg", data: "/9j/2Q==" });
  });

  it("explains when a scan isn't ready", async () => {
    const { result } = await rpc("tools/call", { name: "list_documents", arguments: { scan_id: "b".repeat(32) } });
    expect(result.isError).toBe(true);
  });
});
