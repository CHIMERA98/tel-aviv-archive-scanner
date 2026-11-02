import { createExecutionContext, env, waitOnExecutionContext } from "cloudflare:test";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import worker from "../src/index";
import { sha256Hex, signRequest } from "../src/lib/crypto";
import type { Env } from "../src/env";

let testEnv: Env;
const outbound: { url: string; body: string }[] = [];

beforeAll(async () => {
  // A throwaway RSA key so the Google JWT flow can sign for real.
  const pair = (await crypto.subtle.generateKey(
    { name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" },
    true,
    ["sign", "verify"],
  )) as CryptoKeyPair;
  const der = (await crypto.subtle.exportKey("pkcs8", pair.privateKey)) as ArrayBuffer;
  const pem = `-----BEGIN PRIVATE KEY-----\n${btoa(String.fromCharCode(...new Uint8Array(der)))}\n-----END PRIVATE KEY-----`;
  testEnv = { ...env, GCP_SA_KEY: JSON.stringify({ client_email: "dispatch@test.iam", private_key: pem }) } as Env;
});

const building = (id: number) => ({
  key: `tel-aviv:${id}`,
  building_id: id,
  x: 1,
  y: 2,
  lat: 32.06,
  lon: 34.77,
  outline: [],
  year_built: 1946,
});
const address = (n: number) => ({ label_he: `הגר"א ${n}`, label_en: `Haghara ${n}`, street_code: 384, house_number: n });

const fakeFetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
  outbound.push({ url, body: typeof init?.body === "string" ? init.body : String(init?.body ?? "") });
  if (url.includes("challenges.cloudflare.com")) {
    const params = new URLSearchParams(String(init?.body));
    const token = params.get("response") ?? "";
    const [ok, action] = token.split(":");
    return Response.json({ success: ok === "pass", action, hostname: "scanner.test" });
  }
  if (url.startsWith("https://lookup.test/lookup")) {
    const q = new URL(url).searchParams.get("q") ?? "";
    const n = Number(q.match(/\d+/)?.[0] ?? 27);
    return Response.json({ matches: [{ address: address(n), building: building(1000 + n) }] });
  }
  if (url === "https://oauth2.googleapis.com/token") return Response.json({ access_token: "gtoken", expires_in: 3600 });
  if (url.includes("run.googleapis.com")) return Response.json({ name: "operations/123" });
  return new Response("unexpected", { status: 599 });
});

afterEach(() => {
  outbound.length = 0;
});

const call = async (path: string, init: RequestInit = {}) => {
  vi.stubGlobal("fetch", fakeFetch);
  const ctx = createExecutionContext();
  const response = await worker.fetch(new Request(`https://scanner.test${path}`, init), testEnv, ctx);
  await waitOnExecutionContext(ctx);
  return response;
};

const signedCall = async (method: string, path: string, body: string | Uint8Array = "") =>
  call(path, {
    method,
    body: method === "GET" ? undefined : body,
    headers: {
      "X-Scanner-Signature": await signRequest("hmac-key", method, path, body),
      "X-Content-SHA256": await sha256Hex(body),
    },
  });

const signIn = async (email: string): Promise<string> => {
  const logs: string[] = [];
  const spy = vi.spyOn(console, "log").mockImplementation((...args) => void logs.push(args.join(" ")));
  const response = await call("/api/auth/request", {
    method: "POST",
    body: JSON.stringify({ email, turnstileToken: "pass:signin", locale: "en" }),
  });
  spy.mockRestore();
  expect(response.status).toBe(200);
  const token = logs.join("\n").match(/token=([\w-]+)/)?.[1];
  expect(token).toBeTruthy();
  const verify = await call(`/auth/verify?token=${token}`, { redirect: "manual" });
  expect(verify.status).toBe(302);
  const cookie = verify.headers.get("set-cookie")?.split(";")[0];
  expect(cookie).toMatch(/^tas_session=/);
  // The link is single use.
  const again = await call(`/auth/verify?token=${token}`, { redirect: "manual" });
  expect(again.headers.get("location")).toContain("signin=expired");
  return cookie!;
};

const lookupSelection = async (n: number) => {
  const response = await call(`/api/lookup?q=${encodeURIComponent(`הגרא ${n}`)}`);
  const data = (await response.json()) as { matches: { selection: string }[] };
  return data.matches[0].selection;
};

const startScan = (cookie: string, selection: string, turnstile = "pass:scan") =>
  call("/api/scans", { method: "POST", headers: { cookie }, body: JSON.stringify({ selection, turnstileToken: turnstile }) });

describe("sign-in", () => {
  it("rejects a failed bot check", async () => {
    const response = await call("/api/auth/request", {
      method: "POST",
      body: JSON.stringify({ email: "a@example.com", turnstileToken: "fail:signin" }),
    });
    expect(response.status).toBe(403);
  });

  it("rejects a token issued for another action", async () => {
    const response = await call("/api/auth/request", {
      method: "POST",
      body: JSON.stringify({ email: "a@example.com", turnstileToken: "pass:scan" }),
    });
    expect(response.status).toBe(403);
  });

  it("signs in with an email link and reports the weekly quota", async () => {
    const cookie = await signIn("uri@example.com");
    const me = (await (await call("/api/me", { headers: { cookie } })).json()) as {
      user: { email: string };
      quota: { weeklyLeft: number };
    };
    expect(me.user.email).toBe("uri@example.com");
    expect(me.quota.weeklyLeft).toBe(3);
  });
});

describe("scans", () => {
  it("requires sign-in and a valid selection", async () => {
    expect((await startScan("tas_session=nope", "x")).status).toBe(401);
    const cookie = await signIn("sel@example.com");
    expect((await startScan(cookie, "forged.selection")).status).toBe(400);
  });

  it("queues, dispatches to Cloud Run, accepts scanner callbacks and serves files", async () => {
    const cookie = await signIn("flow@example.com");
    const response = await startScan(cookie, await lookupSelection(27));
    expect(response.status).toBe(200);
    const { scanId, reused } = (await response.json()) as { scanId: string; reused: boolean };
    expect(reused).toBe(false);

    const run = outbound.find((o) => o.url.includes("run.googleapis.com"));
    expect(run?.url).toContain("/projects/test-project/locations/me-west1/jobs/scanner:run");
    expect(JSON.parse(run!.body).overrides.containerOverrides[0].env[0]).toEqual({ name: "SCAN_ID", value: scanId });

    // Unsigned scanner calls are refused.
    expect((await call(`/internal/scans/${scanId}`)).status).toBe(401);

    const job = (await (await signedCall("GET", `/internal/scans/${scanId}`)).json()) as { building: { building_id: number } };
    expect(job.building.building_id).toBe(1027);

    const progress = JSON.stringify({ stage: "archive", status: "done", detail: "69 documents" });
    expect((await signedCall("POST", `/internal/scans/${scanId}/progress`, progress)).status).toBe(200);
    const csv = new TextEncoder().encode("doc_id,source\nA-001,archive\n");
    expect((await signedCall("PUT", `/internal/scans/${scanId}/files/index.csv`, csv)).status).toBe(200);

    // Files aren't served until the scan is done.
    expect((await call(`/files/${scanId}/index.csv`)).status).toBe(410);

    const summary = JSON.stringify({ counts: { archive: 1, plan: 0, map: 0 }, warnings: [], outputs: {} });
    expect((await signedCall("POST", `/internal/scans/${scanId}/done`, summary)).status).toBe(200);

    const status = (await (await call(`/api/scans/${scanId}`)).json()) as {
      status: string;
      stages: Record<string, { detail: string }>;
      expires_at: number;
    };
    expect(status.status).toBe("done");
    expect(status.stages.archive.detail).toBe("69 documents");
    expect(status.expires_at - Date.now() / 1000).toBeGreaterThan(23 * 3600);

    const file = await call(`/files/${scanId}/index.csv`);
    expect(file.status).toBe(200);
    expect(await file.text()).toContain("A-001");
    const partial = await call(`/files/${scanId}/index.csv`, { headers: { range: "bytes=0-5" } });
    expect(partial.status).toBe(206);
    expect(await partial.text()).toBe("doc_id");
  });

  it("enforces 3 scans a week, but reusing a recent scan is free", async () => {
    const cookie = await signIn("quota@example.com");
    for (const n of [101, 102, 103]) expect((await startScan(cookie, await lookupSelection(n))).status).toBe(200);
    expect((await startScan(cookie, await lookupSelection(104))).status).toBe(429);

    // Another user asking for a building that's already queued joins it for free.
    const other = await signIn("other@example.com");
    const joined = (await (await startScan(other, await lookupSelection(101))).json()) as { reused: boolean };
    expect(joined.reused).toBe(true);
    const me = (await (await call("/api/me", { headers: { cookie: other } })).json()) as { quota: { weeklyLeft: number } };
    expect(me.quota.weeklyLeft).toBe(3);
  });

  it("never runs more than two scans at once", async () => {
    const cookie = await signIn("concurrency@example.com");
    for (const n of [201, 202, 203]) await startScan(cookie, await lookupSelection(n));
    const active = await testEnv.DB.prepare("SELECT COUNT(*) AS n FROM scans WHERE status IN ('starting','running')").first<{ n: number }>();
    expect(active?.n).toBeLessThanOrEqual(2);
  });

  it("lists the queue and recent history publicly, without who asked", async () => {
    const cookie = await signIn("activity@example.com");
    await call("/api/activity"); // fills the 10-second cache; starting a scan must clear it
    for (const n of [401, 402, 403]) await startScan(cookie, await lookupSelection(n));
    const response = await call("/api/activity");
    const text = await response.text();
    expect(text).not.toContain("activity@example.com");
    expect(text).not.toContain("user_id");
    const { total, items } = JSON.parse(text) as {
      total: number;
      items: { label_en: string; status: string; position: number | null; id: string | null }[];
    };
    expect(typeof total).toBe("number");
    const mine = items.filter((i) => /Haghara 40[123]/.test(i.label_en));
    expect(mine).toHaveLength(3);
    expect(mine.every((i) => i.id && ["queued", "running"].includes(i.status))).toBe(true);
    const queued = items.filter((i) => i.status === "queued").map((i) => i.position);
    expect(queued).toEqual(queued.map((_, i) => i + 1));
  });

  it("refunds the quota when a scan fails", async () => {
    const cookie = await signIn("refund@example.com");
    const { scanId } = (await (await startScan(cookie, await lookupSelection(301))).json()) as { scanId: string };
    await testEnv.DB.prepare("UPDATE scans SET status = 'running' WHERE id = ?").bind(scanId).run();
    const body = JSON.stringify({ error: "blocked", blocked: true });
    expect((await signedCall("POST", `/internal/scans/${scanId}/failed`, body)).status).toBe(200);
    const me = (await (await call("/api/me", { headers: { cookie } })).json()) as { quota: { weeklyLeft: number } };
    expect(me.quota.weeklyLeft).toBe(3);
  });
});
