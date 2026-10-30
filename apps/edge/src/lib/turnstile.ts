import type { Env } from "../env";

/** Server-side Turnstile check per Cloudflare's contract: success, expected action and an allowed hostname. */
export const verifyTurnstile = async (
  env: Env,
  token: unknown,
  action: string,
  remoteIp: string | null,
): Promise<boolean> => {
  if (env.DEV_MODE === "1") return true;
  const hostnames = new Set(
    (env.TURNSTILE_HOSTNAMES ?? "")
      .split(",")
      .map((h) => h.trim())
      .filter(Boolean),
  );
  if (typeof token !== "string" || token.length === 0 || token.length > 2048 || hostnames.size === 0) return false;
  try {
    const response = await fetch("https://challenges.cloudflare.com/turnstile/v0/siteverify", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      signal: AbortSignal.timeout(10_000),
      body: new URLSearchParams({ secret: env.TURNSTILE_SECRET, response: token, ...(remoteIp ? { remoteip: remoteIp } : {}) }),
    });
    if (!response.ok) return false;
    const result = (await response.json()) as { success?: boolean; action?: string; hostname?: string };
    return result.success === true && result.action === action && hostnames.has(result.hostname ?? "");
  } catch {
    return false;
  }
};
