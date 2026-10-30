import type { Env } from "../env";
import { base64url } from "./crypto";

interface ServiceAccount {
  client_email: string;
  private_key: string;
}

let cached: { token: string; expiresAt: number } | null = null;

const pemToDer = (pem: string): ArrayBuffer => {
  const body = pem.replace(/-----[^-]+-----/g, "").replace(/\s+/g, "");
  return Uint8Array.from(atob(body), (c) => c.charCodeAt(0)).buffer;
};

/** OAuth access token for the scanner-dispatch service account (JWT bearer flow). */
export const accessToken = async (env: Env): Promise<string> => {
  const nowS = Math.floor(Date.now() / 1000);
  if (cached && cached.expiresAt - 60 > nowS) return cached.token;

  const account = JSON.parse(env.GCP_SA_KEY) as ServiceAccount;
  const header = base64url(JSON.stringify({ alg: "RS256", typ: "JWT" }));
  const claims = base64url(
    JSON.stringify({
      iss: account.client_email,
      scope: "https://www.googleapis.com/auth/cloud-platform",
      aud: "https://oauth2.googleapis.com/token",
      iat: nowS,
      exp: nowS + 3600,
    }),
  );
  const key = await crypto.subtle.importKey(
    "pkcs8",
    pemToDer(account.private_key),
    { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signature = base64url(
    await crypto.subtle.sign("RSASSA-PKCS1-v1_5", key, new TextEncoder().encode(`${header}.${claims}`)),
  );
  const response = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "urn:ietf:params:oauth:grant-type:jwt-bearer",
      assertion: `${header}.${claims}.${signature}`,
    }),
  });
  if (!response.ok) throw new Error(`Google token exchange failed: ${response.status} ${await response.text()}`);
  const data = (await response.json()) as { access_token: string; expires_in: number };
  cached = { token: data.access_token, expiresAt: nowS + data.expires_in };
  return data.access_token;
};

/** Start one execution of the Cloud Run scanner job for a scan. */
export const runScannerJob = async (env: Env, scanId: string): Promise<string> => {
  if (env.DEV_MODE === "1") {
    console.log(`[dev] start the scanner by hand: SCAN_ID=${scanId} python -m scanner.job`);
    return "dev";
  }
  const url =
    `https://run.googleapis.com/v2/projects/${env.GCP_PROJECT}/locations/${env.GCP_REGION}` +
    `/jobs/${env.GCP_JOB}:run`;
  const response = await fetch(url, {
    method: "POST",
    headers: { Authorization: `Bearer ${await accessToken(env)}`, "Content-Type": "application/json" },
    body: JSON.stringify({
      overrides: { taskCount: 1, containerOverrides: [{ env: [{ name: "SCAN_ID", value: scanId }] }] },
    }),
  });
  if (!response.ok) throw new Error(`Cloud Run job start failed: ${response.status} ${await response.text()}`);
  const operation = (await response.json()) as { name?: string };
  return operation.name ?? "";
};
