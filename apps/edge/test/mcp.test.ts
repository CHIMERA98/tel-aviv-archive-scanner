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
    "INSERT OR REPLACE INTO scans (id, user_id, building_key, building_id, address_json, building_json, label_he, label_en, status, " +
      "summary_json, queued_at, finished_at, expires_at) VALUES (?, 'u1', 'tel-aviv:2287', 2287, '{}', '{\"building_id\":2287}', 'הגר\"א 27', " +
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
      { doc_id: "A-001", source: "archive", title: "היתר-תכנית חתומה", doc_type: "היתר-תכנית חתומה", date: "1937-05-30", year: 1937, permit_no: "123", request_no: null, plan_no: null, plan_status: null, pages: 2, page_images: ["pages/A-001/A-001-001.jpg", "pages/A-001/A-001-002.jpg"], page_tiles: [[], ["pages/A-001/A-001-002-t01.jpg", "pages/A-001/A-001-002-t02.jpg"]], sheets: [{ image: "pages/A-001/A-001-sheet01.jpg", cells: ["p1", "p2.t1", "p2.t2"] }], original_url: null, note: null },
      { doc_id: "A-002", source: "archive", title: "מכתב מידע", doc_type: "מכתב מידע", date: "2006-06-18", year: 2006, permit_no: null, request_no: null, plan_no: null, plan_status: null, pages: 1, page_images: ["pages/A-002/A-002-001.jpg"], original_url: null, note: null },
    ],
    warnings: [],
  };
  await env.FILES.put(`scans/${SCAN}/manifest.json`, JSON.stringify(manifest));
  for (const name of ["A-001-001", "A-001-002", "A-001-002-t02", "A-001-sheet01"]) {
    await env.FILES.put(`scans/${SCAN}/pages/A-001/${name}.jpg`, new Uint8Array([0xff, 0xd8, 0xff, 0xd9]));
  }
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
      "browse_pages",
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

  it("returns the tiles of a long sheet and points to them", async () => {
    await seedDoneScan();
    const whole = await rpc("tools/call", { name: "get_document_pages", arguments: { scan_id: SCAN, doc_id: "A-001", pages: [2] } });
    expect(whole.result.content[0].text).toContain("page 2 is a long sheet in 2 tiles");
    const { result } = await rpc("tools/call", {
      name: "get_document_pages",
      arguments: { scan_id: SCAN, doc_id: "A-001", pages: [2], tiles: [2, 5] },
    });
    expect(result.content[0].text).toContain("tiles 2 of 2");
    expect(result.content).toHaveLength(2);
    const flat = await rpc("tools/call", { name: "get_document_pages", arguments: { scan_id: SCAN, doc_id: "A-001", tiles: [1] } });
    expect(flat.result.isError).toBe(true);
  });

  it("browses contact sheets with their cell labels", async () => {
    await seedDoneScan();
    const { result } = await rpc("tools/call", { name: "browse_pages", arguments: { scan_id: SCAN } });
    expect(result.content[0].text).toContain("A-001");
    expect(result.content[0].text).toContain("p1, p2.t1, p2.t2");
    expect(result.content[1]).toMatchObject({ type: "image", mimeType: "image/jpeg" });
    expect(result.content.at(-1).text).toContain("No thumbnails (open with get_document_pages): A-002");
    const none = await rpc("tools/call", { name: "browse_pages", arguments: { scan_id: SCAN, year_from: 2000 } });
    expect(none.result.content[0].text).toContain("A-002");
  });

  it("serves a plain page for AI apps that open links, no sign-in needed", async () => {
    await seedDoneScan();
    const ctx = createExecutionContext();
    const he = await worker.fetch(new Request(`https://scanner.test/scan/${SCAN}/ai`), env, ctx);
    const body = await he.text();
    expect(he.status).toBe(200);
    expect(he.headers.get("x-robots-tag")).toBe("noindex");
    expect(body).toContain('dir="rtl"');
    expect(body).toContain("A-001");
    expect(body).toContain(`https://scanner.test/files/${SCAN}/pages/A-001/A-001-001.jpg`);
    expect(body).toContain(`https://scanner.test/files/${SCAN}/pages/A-001/A-001-sheet01.jpg`);
    expect(body).toContain(`scan_id ${SCAN}`);
    expect(body).not.toContain("<script");
    // Every document has a short link, printed as text, and the AI is told to cite with it.
    expect(body).toContain(`[A-001](https://scanner.test/d/${SCAN}/A-001)`);
    expect(body).toContain(`>https://scanner.test/d/${SCAN}/A-002</a>`);

    const en = await worker.fetch(new Request(`https://scanner.test/en/scan/${SCAN}/ai`), env, ctx);
    expect(await en.text()).toContain("Instructions for the AI reading this page");
    const missing = await worker.fetch(new Request(`https://scanner.test/scan/${"c".repeat(32)}/ai`), env, ctx);
    expect(missing.status).toBe(404);
    await waitOnExecutionContext(ctx);
  });

  it("sends a document's short link to its file", async () => {
    await seedDoneScan();
    const ctx = createExecutionContext();
    const found = await worker.fetch(new Request(`https://scanner.test/d/${SCAN}/A-001`), env, ctx);
    expect(found.status).toBe(302);
    expect(found.headers.get("location")).toBe(`https://scanner.test/files/${SCAN}/pages/A-001/A-001-001.jpg`);
    const missing = await worker.fetch(new Request(`https://scanner.test/d/${SCAN}/A-999`), env, ctx);
    expect(missing.status).toBe(404);
    await waitOnExecutionContext(ctx);
    const { result } = await rpc("tools/call", { name: "list_documents", arguments: { scan_id: SCAN } });
    expect(JSON.parse(result.content[0].text).documents[0].link).toBe(`https://scanner.test/d/${SCAN}/A-001`);
  });

  it("explains when a scan isn't ready", async () => {
    const { result } = await rpc("tools/call", { name: "list_documents", arguments: { scan_id: "b".repeat(32) } });
    expect(result.isError).toBe(true);
  });
});
