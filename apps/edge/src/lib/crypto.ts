const encoder = new TextEncoder();

export const toHex = (buf: ArrayBuffer): string =>
  [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");

export const base64url = (data: ArrayBuffer | Uint8Array | string): string => {
  const bytes = typeof data === "string" ? encoder.encode(data) : new Uint8Array(data);
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
};

export const fromBase64url = (text: string): string => {
  const padded = text.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((text.length + 3) % 4);
  const binary = atob(padded);
  return new TextDecoder().decode(Uint8Array.from(binary, (c) => c.charCodeAt(0)));
};

export const sha256Hex = async (data: string | ArrayBuffer | Uint8Array): Promise<string> =>
  toHex(await crypto.subtle.digest("SHA-256", typeof data === "string" ? encoder.encode(data) : data));

export const randomToken = (bytes = 32): string => base64url(crypto.getRandomValues(new Uint8Array(bytes)));

export const randomId = (bytes = 16): string => toHex(crypto.getRandomValues(new Uint8Array(bytes)).buffer);

const hmacKey = (secret: string) =>
  crypto.subtle.importKey("raw", encoder.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);

export const hmacHex = async (secret: string, message: string): Promise<string> =>
  toHex(await crypto.subtle.sign("HMAC", await hmacKey(secret), encoder.encode(message)));

/** Constant-time comparison of two hex strings. */
export const safeEqual = (a: string, b: string): boolean => {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
};

/**
 * Scanner <-> edge signature (mirrors scanner/edge_client.py):
 * `t=<unix>,v1=hex(HMAC-SHA256(key, "<t>.<METHOD>.<path+query>.<sha256(body)>"))`.
 */
export const signRequest = async (
  secret: string,
  method: string,
  pathWithQuery: string,
  body: ArrayBuffer | Uint8Array | string,
  t = Math.floor(Date.now() / 1000),
): Promise<string> => {
  const digest = await sha256Hex(body);
  return `t=${t},v1=${await hmacHex(secret, `${t}.${method.toUpperCase()}.${pathWithQuery}.${digest}`)}`;
};

export const verifyRequest = async (
  secret: string,
  header: string | null | undefined,
  method: string,
  pathWithQuery: string,
  body: ArrayBuffer,
  maxSkewS = 300,
): Promise<boolean> => {
  if (!secret || !header) return false;
  const parts = Object.fromEntries(header.split(",").map((p) => p.split("=", 2) as [string, string]));
  const t = Number(parts.t);
  if (!Number.isInteger(t) || !parts.v1 || Math.abs(Date.now() / 1000 - t) > maxSkewS) return false;
  const expected = (await signRequest(secret, method, pathWithQuery, body, t)).split("v1=")[1];
  return safeEqual(expected, parts.v1);
};

/** Compact signed token `base64url(json).hex(hmac)`, used to carry a lookup result to the scan form. */
export const sealJson = async (secret: string, value: unknown): Promise<string> => {
  const payload = base64url(JSON.stringify(value));
  return `${payload}.${await hmacHex(secret, `seal.${payload}`)}`;
};

export const openJson = async <T>(secret: string, token: string): Promise<T | null> => {
  const [payload, mac] = token.split(".");
  if (!payload || !mac || !safeEqual(await hmacHex(secret, `seal.${payload}`), mac)) return null;
  try {
    return JSON.parse(fromBase64url(payload)) as T;
  } catch {
    return null;
  }
};
