import { deleteCookie, getCookie, setCookie } from "hono/cookie";
import type { Context } from "hono";
import type { AppContext, Env } from "../env";
import { now } from "../env";
import { randomId, randomToken, sha256Hex } from "./crypto";
import type { Locale } from "./email";

export const SESSION_COOKIE = "tas_session";
const SESSION_DAYS = 30;
export const LOGIN_TOKEN_MINUTES = 15;

const normalizeEmail = (email: string) => email.trim().toLowerCase();
export const isEmail = (email: unknown): email is string =>
  typeof email === "string" && email.length <= 254 && /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(email.trim());

/** Only same-site relative paths are allowed as post-login destinations. */
// OAuth /authorize URLs carry long encoded parameters, hence the generous length.
export const safeReturnTo = (value: unknown): string | null =>
  typeof value === "string" && /^\/(?![\/\\])[\w\-./?=&%#~:+]*$/.test(value) && value.length <= 2000 ? value : null;

export const createLoginToken = async (
  env: Env,
  email: string,
  locale: Locale,
  returnTo: string | null,
): Promise<string> => {
  const token = randomToken();
  await env.DB.prepare(
    "INSERT INTO login_tokens (token_hash, email, locale, return_to, expires_at) VALUES (?, ?, ?, ?, ?)",
  )
    .bind(await sha256Hex(token), normalizeEmail(email), locale, returnTo, now() + LOGIN_TOKEN_MINUTES * 60)
    .run();
  return token;
};

/** Redeems a login token (single use). Returns the user and where to send them next. */
export const redeemLoginToken = async (
  env: Env,
  token: string,
): Promise<{ userId: string; returnTo: string | null; locale: Locale } | null> => {
  const row = await env.DB.prepare(
    "UPDATE login_tokens SET used_at = ? WHERE token_hash = ? AND used_at IS NULL AND expires_at > ? " +
      "RETURNING email, locale, return_to",
  )
    .bind(now(), await sha256Hex(token), now())
    .first<{ email: string; locale: Locale; return_to: string | null }>();
  if (!row) return null;
  const user = await env.DB.prepare(
    "INSERT INTO users (id, email, locale, created_at) VALUES (?, ?, ?, ?) " +
      "ON CONFLICT(email) DO UPDATE SET locale = excluded.locale RETURNING id",
  )
    .bind(randomId(), row.email, row.locale, now())
    .first<{ id: string }>();
  return user ? { userId: user.id, returnTo: row.return_to, locale: row.locale } : null;
};

export const startSession = async (c: Context<AppContext>, userId: string): Promise<void> => {
  const id = randomToken();
  const expires = now() + SESSION_DAYS * 86400;
  await c.env.DB.prepare("INSERT INTO sessions (id_hash, user_id, expires_at) VALUES (?, ?, ?)")
    .bind(await sha256Hex(id), userId, expires)
    .run();
  setCookie(c, SESSION_COOKIE, id, {
    httpOnly: true,
    secure: true,
    sameSite: "Lax",
    path: "/",
    maxAge: SESSION_DAYS * 86400,
    ...cookieDomain(c),
  });
};

export const currentUser = async (c: Context<AppContext>): Promise<{ id: string; email: string; locale: Locale } | null> => {
  const id = getCookie(c, SESSION_COOKIE);
  if (!id) return null;
  return c.env.DB.prepare(
    "SELECT u.id, u.email, u.locale FROM sessions s JOIN users u ON u.id = s.user_id " +
      "WHERE s.id_hash = ? AND s.expires_at > ?",
  )
    .bind(await sha256Hex(id), now())
    .first<{ id: string; email: string; locale: Locale }>();
};

export const endSession = async (c: Context<AppContext>): Promise<void> => {
  const id = getCookie(c, SESSION_COOKIE);
  if (id) await c.env.DB.prepare("DELETE FROM sessions WHERE id_hash = ?").bind(await sha256Hex(id)).run();
  deleteCookie(c, SESSION_COOKIE, { path: "/" });
  if (cookieDomain(c).domain) deleteCookie(c, SESSION_COOKIE, { path: "/", ...cookieDomain(c) });
};

/**
 * One sign-in for the Tel Aviv and Haifa hosts: the cookie belongs to the parent domain when the site runs under it.
 * Checked against PUBLIC_URL, not the request: `wrangler dev` reports the production host for localhost requests.
 */
const cookieDomain = (c: Context<AppContext>): { domain?: string } => {
  const domain = c.env.COOKIE_DOMAIN;
  const under = (host: string) => !!domain && (host === domain.replace(/^\./, "") || host.endsWith(domain));
  return under(new URL(c.env.PUBLIC_URL).hostname) && under(new URL(c.req.url).hostname) ? { domain } : {};
};
