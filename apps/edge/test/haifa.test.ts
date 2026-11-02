import { createExecutionContext, env, waitOnExecutionContext } from "cloudflare:test";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import worker from "../src/index";
import { sha256Hex, signRequest } from "../src/lib/crypto";
import type { Env } from "../src/env";

// The Haifa queue (docs/HAIFA_PLAN.md §3.2, §4): one worker, priorities, staged delivery, and the Complot cool-down.

let testEnv: Env;
const outbound: string[] = [];

beforeAll(async () => {
  const pair = (await crypto.subtle.generateKey(
    { name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" },
    true,
    ["sign", "verify"],
  )) as CryptoKeyPair;
  const der = (await crypto.subtle.exportKey("pkcs8", pair.privateKey)) as ArrayBuffer;
  const pem = `-----BEGIN PRIVATE KEY-----\n${btoa(String.fromCharCode(...new Uint8Array(der)))}\n-----END PRIVATE KEY-----`;
  testEnv = { ...env, GCP_SA_KEY: JSON.stringify({ client_email: "dispatch@test.iam", private_key: pem }) } as Env;
});

beforeEach(async () => {
  outbound.length = 0;
  // Each test starts with no worker and no cool-down.
  await testEnv.DB.prepare("DELETE FROM source_state").run();
  await testEnv.DB.prepare("UPDATE scans SET status = 'expired' WHERE city = 'haifa'").run();
});

const haifaBuilding = (n: number) => ({ key: `haifa:399:${n}`, city: "haifa", x: 1, y: 2, lat: 32.82, lon: 34.99, outline: [] });
const haifaAddress = (n: number) => ({ city: "haifa", label_he: `בן גוריון ${n}, חיפה`, label_en: `בן גוריון ${n}, Haifa`, street_code: 399, house_number: n });

const fakeFetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
  outbound.push(url);
  if (url.includes("challenges.cloudflare.com")) {
    const [ok, action] = (new URLSearchParams(String(init?.body)).get("response") ?? "").split(":");
    return Response.json({ success: ok === "pass", action, hostname: "scanner.test" });
  }
  if (url.startsWith("https://lookup.test/lookup")) {
    const params = new URL(url).searchParams;
    const n = Number(params.get("q")?.match(/\d+/)?.[0] ?? 50);
    return Response.json({ city: params.get("city"), matches: [{ address: haifaAddress(n), building: haifaBuilding(n) }] });
  }
  if (url === "https://oauth2.googleapis.com/token") return Response.json({ access_token: "gtoken", expires_in: 3600 });
  if (url.includes("run.googleapis.com")) return Response.json({ name: "operations/1" });
  return new Response("unexpected", { status: 599 });
});

const call = async (path: string, init: RequestInit = {}, host = "haifa.scanner.test") => {
  vi.stubGlobal("fetch", fakeFetch);
  const ctx = createExecutionContext();
  const response = await worker.fetch(new Request(`https://${host}${path}`, init), testEnv, ctx);
  await waitOnExecutionContext(ctx);
  return response;
};

const signed = async (method: string, path: string, body: string | Uint8Array = "") =>
  call(path, {
    method,
    body: method === "GET" ? undefined : body,
    headers: {
      "X-Scanner-Signature": await signRequest("hmac-key", method, path, body),
      "X-Content-SHA256": await sha256Hex(body),
    },
  });

const json = async <T>(response: Response) => (await response.json()) as T;
const next = async () => json<{ kind: string; scan_id?: string; doc_id?: string; interval_s: number }>(await signed("POST", "/internal/haifa/next", "{}"));

const signIn = async (email: string): Promise<string> => {
  const logs: string[] = [];
  const spy = vi.spyOn(console, "log").mockImplementation((...args) => void logs.push(args.join(" ")));
  await call("/api/auth/request", { method: "POST", body: JSON.stringify({ email, turnstileToken: "pass:signin", locale: "en" }) });
  spy.mockRestore();
  const token = logs.join("\n").match(/token=([\w-]+)/)?.[1];
  // The sign-in link points back at the Haifa host.
  expect(logs.join("\n")).toContain("https://haifa.tel-aviv-archive-scanner.work/auth/verify");
  const verify = await call(`/auth/verify?token=${token}`, { redirect: "manual" });
  return verify.headers.get("set-cookie")!.split(";")[0];
};

const startHaifaScan = async (cookie: string, n: number) => {
  const lookup = await json<{ city: string; matches: { selection: string; city: string }[] }>(
    await call(`/api/lookup?q=${encodeURIComponent(`בן גוריון ${n}`)}`),
  );
  expect(lookup.city).toBe("haifa");
  const response = await call("/api/scans", {
    method: "POST",
    headers: { cookie },
    body: JSON.stringify({ selection: lookup.matches[0].selection, turnstileToken: "pass:scan" }),
  });
  expect(response.status).toBe(200);
  return json<{ scanId: string; reused: boolean }>(response);
};

const deliver = (scanId: string, docs: string[]) =>
  signed(
    "POST",
    `/internal/haifa/scans/${scanId}/delivered`,
    JSON.stringify({
      summary: { counts: { archive: docs.length + 1, plan: 0, map: 0 }, documents_total: docs.length + 1, documents_ready: 1 },
      documents: docs.map((doc_id, i) => ({ doc_id, guid: `GUID-${i}`, priority: i })),
    }),
  );

describe("haifa", () => {
  it("looks up Haifa on the Haifa host and stores scans by building key", async () => {
    const cookie = await signIn("haifa@example.com");
    const { scanId } = await startHaifaScan(cookie, 50);
    const row = await testEnv.DB.prepare("SELECT city, building_key, building_id FROM scans WHERE id = ?").bind(scanId).first();
    expect(row).toEqual({ city: "haifa", building_key: "haifa:399:50", building_id: null });
    expect(outbound.some((u) => u.startsWith("https://lookup.test/lookup?") && u.includes("city=haifa"))).toBe(true);
    // The worker job starts, not the Tel Aviv job.
    expect(outbound.some((u) => u.includes("/jobs/haifa-worker:run"))).toBe(true);
    expect(outbound.some((u) => u.includes("/jobs/scanner:run"))).toBe(false);

    // A second user asking for the same building joins it for free.
    const other = await signIn("haifa2@example.com");
    expect((await startHaifaScan(other, 50)).reused).toBe(true);
  });

  it("runs a scan through first delivery, background documents and finishing", async () => {
    const cookie = await signIn("stages@example.com");
    const { scanId } = await startHaifaScan(cookie, 51);

    const first = await next();
    expect(first).toMatchObject({ kind: "scan", scan_id: scanId, interval_s: 12 });
    const manifest = new TextEncoder().encode(
      JSON.stringify({ city: "haifa", address: haifaAddress(51), building: {}, plans: [], warnings: [], documents: [] }),
    );
    expect((await signed("PUT", `/internal/scans/${scanId}/files/manifest.json`, manifest)).status).toBe(200);
    expect((await deliver(scanId, ["A-002", "A-003", "A-004"])).status).toBe(200);

    // Usable now: files are served and the page shows progress.
    expect((await call(`/files/${scanId}/manifest.json`)).status).toBe(200);
    let scan = await json<{ status: string; documents: { total: number; ready: number; pending: number } }>(await call(`/api/scans/${scanId}`));
    expect(scan.status).toBe("filling");
    expect(scan.documents).toEqual({ total: 4, ready: 1, pending: 3 });
    // The page for AIs says the list is partial, and isn't cached while it fills.
    let aiPage = await call(`/en/scan/${scanId}/ai`);
    expect(aiPage.headers.get("cache-control")).toBe("no-store");
    expect(await aiPage.text()).toContain("This list is still partial: 1 of 4 documents have arrived so far");

    // The worker reads the manifest back through the edge.
    expect((await signed("GET", `/internal/scans/${scanId}/files/manifest.json`)).status).toBe(200);

    // Background order follows priority, but a document someone opens jumps the queue.
    const opened = await json<{ status: string }>(await call(`/api/scans/${scanId}/documents/A-004/fetch`, { method: "POST" }));
    expect(opened.status).toBe("queued");
    expect(await next()).toMatchObject({ kind: "document", scan_id: scanId, doc_id: "A-004" });
    const report = (doc: string) => signed("POST", `/internal/haifa/scans/${scanId}/documents/${doc}`, JSON.stringify({ status: "ready", pages: 2 }));
    expect(await json<{ pending: number }>(await report("A-004"))).toMatchObject({ pending: 2 });
    expect(await next()).toMatchObject({ kind: "document", doc_id: "A-002" });
    await report("A-002");
    expect(await next()).toMatchObject({ kind: "document", doc_id: "A-003" });
    await report("A-003");

    // Nothing pending: finish, then done with a 24 h expiry.
    expect(await next()).toMatchObject({ kind: "finish", scan_id: scanId });
    const final = { counts: {}, documents_total: 4, documents_ready: 4 };
    expect((await signed("POST", `/internal/scans/${scanId}/done`, JSON.stringify(final))).status).toBe(200);
    scan = await json(await call(`/api/scans/${scanId}`));
    expect(scan.status).toBe("done");
    // The final summary already counts the queued documents; they are not added again.
    expect(scan.documents).toEqual({ total: 4, ready: 4, pending: 0 });
    aiPage = await call(`/en/scan/${scanId}/ai`);
    expect(await aiPage.text()).not.toContain("still partial");
    expect(await next()).toMatchObject({ kind: "idle" });
  });

  it("pauses Complot for 45 minutes after a block, slows down, and resumes the scan", async () => {
    const cookie = await signIn("blocked@example.com");
    const { scanId } = await startHaifaScan(cookie, 52);
    expect(await next()).toMatchObject({ kind: "scan", scan_id: scanId });

    const blocked = await json<{ blocked_until: number }>(
      await signed("POST", "/internal/haifa/blocked", JSON.stringify({ reason: "HTTP 429", scan_id: scanId })),
    );
    expect(blocked.blocked_until - Date.now() / 1000).toBeGreaterThan(44 * 60);
    const row = await testEnv.DB.prepare("SELECT status FROM scans WHERE id = ?").bind(scanId).first<{ status: string }>();
    expect(row?.status).toBe("queued");

    // No worker starts and no work is handed out during the cool-down.
    outbound.length = 0;
    await testEnv.DB.prepare("UPDATE source_state SET last_seen = NULL WHERE source = 'haifa-worker'").run();
    await call("/api/scans/none/documents/A-001/fetch", { method: "POST" });
    expect(await next()).toMatchObject({ kind: "blocked" });
    expect(outbound.some((u) => u.includes("haifa-worker:run"))).toBe(false);

    // After the cool-down: the same scan resumes, 50% slower.
    await testEnv.DB.prepare("UPDATE source_state SET blocked_until = ? WHERE source = 'complot'").bind(Math.floor(Date.now() / 1000) - 1).run();
    expect(await next()).toMatchObject({ kind: "scan", scan_id: scanId, interval_s: 18 });
  });

  it("puts a document that failed just before a silent block back in the queue", async () => {
    const cookie = await signIn("silent@example.com");
    const { scanId } = await startHaifaScan(cookie, 53);
    expect(await next()).toMatchObject({ kind: "scan", scan_id: scanId });
    await deliver(scanId, ["A-002", "A-003"]);
    const report = async (doc: string, status: string) =>
      json<{ pending: number }>(
        await signed("POST", `/internal/haifa/scans/${scanId}/documents/${doc}`, JSON.stringify({ status, pages: 0 })),
      );

    expect(await next()).toMatchObject({ kind: "document", doc_id: "A-002" });
    expect(await report("A-002", "failed")).toMatchObject({ pending: 1 });
    expect(await next()).toMatchObject({ kind: "document", doc_id: "A-003" });
    await report("A-003", "ready");
    // A-003's answer was the second suspect one: the worker puts A-002 back. A ready document stays ready.
    expect(await report("A-002", "pending")).toMatchObject({ pending: 1 });
    expect(await report("A-003", "pending")).toMatchObject({ pending: 1 });
    expect(await next()).toMatchObject({ kind: "document", doc_id: "A-002" });
  });

  it("keeps Complot results in the 30-day cache", async () => {
    const key = "docs/F04FD286-C3A8-4E75-9822-F69556D1F6C9.pdf";
    expect((await signed("GET", `/internal/haifa/cache/${key}`)).status).toBe(404);
    const pdf = new TextEncoder().encode("%PDF-1.7 test");
    expect((await signed("PUT", `/internal/haifa/cache/${key}`, pdf)).status).toBe(200);
    const hit = await signed("GET", `/internal/haifa/cache/${key}`);
    expect(await hit.text()).toBe("%PDF-1.7 test");
    expect((await signed("GET", "/internal/haifa/cache/scans/abc/manifest.json")).status).toBe(400);
    // The first suspect answer of a worker run is kept for diagnosis.
    const page = new TextEncoder().encode("<html>no link</html>");
    expect((await signed("PUT", "/internal/haifa/cache/debug/GetDocumentIFN-20261102T160623.html", page)).status).toBe(200);
  });

  it("serves the Tel Aviv city on the main host", async () => {
    const response = await call(`/api/lookup?q=${encodeURIComponent("לבונטין 15")}`, {}, "scanner.test");
    expect((await json<{ city: string }>(response)).city).toBe("tel-aviv");
    const haifa = await call(`/api/lookup?q=${encodeURIComponent("בן גוריון 50 חיפה")}`, {}, "scanner.test");
    expect((await json<{ city: string }>(haifa)).city).toBe("haifa");
  });
});
