import type { Env } from "../env";

export type Locale = "he" | "en";

interface Message {
  to: string;
  subject: string;
  text: string;
  html: string;
}

const layout = (locale: Locale, body: string) =>
  `<!doctype html><html lang="${locale}" dir="${locale === "he" ? "rtl" : "ltr"}"><body style="margin:0;padding:32px 16px;` +
  `font-family:-apple-system,Segoe UI,Arial,sans-serif;color:#111;background:#fff">` +
  `<div style="max-width:480px;margin:0 auto;font-size:16px;line-height:1.6">${body}</div></body></html>`;

const button = (href: string, label: string) =>
  `<p style="margin:28px 0"><a href="${href}" style="display:inline-block;padding:12px 20px;background:#111;` +
  `color:#fff;text-decoration:none;border-radius:6px">${label}</a></p>`;

export const signInMessage = (locale: Locale, to: string, link: string): Message =>
  locale === "he"
    ? {
        to,
        subject: "קישור כניסה — סורק הארכיון",
        text: `לכניסה לסורק הארכיון של תל אביב:\n${link}\n\nהקישור תקף ל-15 דקות. אם לא ביקשת אותו, אפשר להתעלם.`,
        html: layout("he", `<p>לכניסה לסורק הארכיון של תל אביב:</p>${button(link, "כניסה")}<p style="color:#666;font-size:14px">הקישור תקף ל-15 דקות. אם לא ביקשת אותו, אפשר להתעלם.</p>`),
      }
    : {
        to,
        subject: "Your sign-in link — Archive Scanner",
        text: `Sign in to the Tel Aviv Archive Scanner:\n${link}\n\nThe link works for 15 minutes. If you didn't ask for it, ignore this email.`,
        html: layout("en", `<p>Sign in to the Tel Aviv Archive Scanner:</p>${button(link, "Sign in")}<p style="color:#666;font-size:14px">The link works for 15 minutes. If you didn't ask for it, ignore this email.</p>`),
      };

export const scanReadyMessage = (locale: Locale, to: string, label: string, link: string): Message =>
  locale === "he"
    ? {
        to,
        subject: `הסריקה מוכנה — ${label}`,
        text: `הסריקה של ${label} מוכנה:\n${link}\n\nהקבצים יימחקו בעוד 24 שעות.`,
        html: layout("he", `<p>הסריקה של <b>${label}</b> מוכנה.</p>${button(link, "לתוצאות")}<p style="color:#666;font-size:14px">הקבצים יימחקו בעוד 24 שעות.</p>`),
      }
    : {
        to,
        subject: `Your scan is ready — ${label}`,
        text: `The scan of ${label} is ready:\n${link}\n\nFiles are deleted after 24 hours.`,
        html: layout("en", `<p>The scan of <b>${label}</b> is ready.</p>${button(link, "View results")}<p style="color:#666;font-size:14px">Files are deleted after 24 hours.</p>`),
      };

/** Sends through Resend when its key is set, else Cloudflare Email Sending; with neither, logs (local development). */
export const sendEmail = async (env: Env, message: Message): Promise<void> => {
  if (env.RESEND_API_KEY) {
    const response = await fetch("https://api.resend.com/emails", {
      method: "POST",
      headers: { Authorization: `Bearer ${env.RESEND_API_KEY}`, "Content-Type": "application/json" },
      body: JSON.stringify({ from: env.EMAIL_FROM, ...message }),
    });
    if (!response.ok) throw new Error(`Resend failed: ${response.status} ${await response.text()}`);
    return;
  }
  if (env.EMAIL) {
    await env.EMAIL.send({ from: env.EMAIL_FROM, ...message });
    return;
  }
  console.log(`[email disabled] to=${message.to} subject=${message.subject}\n${message.text}`);
};
