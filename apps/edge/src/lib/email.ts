import type { Env } from "../env";
import type { City } from "./cities";

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

const scannerName = (locale: Locale, city: City) =>
  locale === "he"
    ? city === "haifa"
      ? "סורק הארכיון של חיפה"
      : "סורק הארכיון של תל אביב"
    : city === "haifa"
      ? "the Haifa Archive Scanner"
      : "the Tel Aviv Archive Scanner";

export const signInMessage = (locale: Locale, to: string, link: string, city: City = "tel-aviv"): Message =>
  locale === "he"
    ? {
        to,
        subject: "קישור כניסה — סורק הארכיון",
        text: `לכניסה ל${scannerName("he", city)}:\n${link}\n\nהקישור תקף ל-15 דקות. אם לא ביקשת אותו, אפשר להתעלם.`,
        html: layout("he", `<p>לכניסה ל${scannerName("he", city)}:</p>${button(link, "כניסה")}<p style="color:#666;font-size:14px">הקישור תקף ל-15 דקות. אם לא ביקשת אותו, אפשר להתעלם.</p>`),
      }
    : {
        to,
        subject: "Your sign-in link — Archive Scanner",
        text: `Sign in to ${scannerName("en", city)}:\n${link}\n\nThe link works for 15 minutes. If you didn't ask for it, ignore this email.`,
        html: layout("en", `<p>Sign in to ${scannerName("en", city)}:</p>${button(link, "Sign in")}<p style="color:#666;font-size:14px">The link works for 15 minutes. If you didn't ask for it, ignore this email.</p>`),
      };

/** `filling`: a Haifa scan that opens now while the rest of its documents keep arriving. */
export const scanReadyMessage = (locale: Locale, to: string, label: string, link: string, filling = false): Message => {
  const he = filling
    ? "שאר המסמכים ממשיכים להגיע ברקע; נשלח מייל כשכולם יגיעו."
    : "הקבצים יימחקו בעוד 24 שעות.";
  const en = filling
    ? "The rest of the documents keep arriving in the background; we'll email you when they're all in."
    : "Files are deleted after 24 hours.";
  return locale === "he"
    ? {
        to,
        subject: `הסריקה מוכנה — ${label}`,
        text: `הסריקה של ${label} מוכנה:\n${link}\n\n${he}`,
        html: layout("he", `<p>הסריקה של <b>${label}</b> מוכנה.</p>${button(link, "לתוצאות")}<p style="color:#666;font-size:14px">${he}</p>`),
      }
    : {
        to,
        subject: `Your scan is ready — ${label}`,
        text: `The scan of ${label} is ready:\n${link}\n\n${en}`,
        html: layout("en", `<p>The scan of <b>${label}</b> is ready.</p>${button(link, "View results")}<p style="color:#666;font-size:14px">${en}</p>`),
      };
};

export const scanCompleteMessage = (locale: Locale, to: string, label: string, link: string): Message =>
  locale === "he"
    ? {
        to,
        subject: `כל המסמכים הגיעו — ${label}`,
        text: `כל המסמכים של ${label} הגיעו, והקבצים להורדה עודכנו:\n${link}\n\nהקבצים יימחקו בעוד 24 שעות.`,
        html: layout("he", `<p>כל המסמכים של <b>${label}</b> הגיעו, והקבצים להורדה עודכנו.</p>${button(link, "לתוצאות")}<p style="color:#666;font-size:14px">הקבצים יימחקו בעוד 24 שעות.</p>`),
      }
    : {
        to,
        subject: `All documents are in — ${label}`,
        text: `Every document for ${label} has arrived and the downloads are updated:\n${link}\n\nFiles are deleted after 24 hours.`,
        html: layout("en", `<p>Every document for <b>${label}</b> has arrived and the downloads are updated.</p>${button(link, "View results")}<p style="color:#666;font-size:14px">Files are deleted after 24 hours.</p>`),
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
