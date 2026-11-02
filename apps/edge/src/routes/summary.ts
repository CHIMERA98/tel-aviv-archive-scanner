import type { Context } from "hono";
import type { AppContext } from "../env";
import { baseUrl } from "../lib/cities";
import { requestDocument } from "../lib/haifa";
import { dispatch } from "../lib/scans";
import { docLink, loadManifest, type ManifestDocument } from "../mcp";

// A plain HTML page per finished scan, written for AI apps that open links (no scripts, no sign-in).
// The "Analyze in Claude / ChatGPT" prompt points here, so analysis works without installing the connector.

type Lang = "he" | "en";

const esc = (value: unknown) =>
  String(value ?? "").replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[ch] ?? ch);

const COPY = {
  he: {
    title: (label: string) => `ארכיון הבניין: ${label}`,
    building: "מזהה מבנה",
    built: "שנת בנייה",
    gushHelka: "גוש/חלקה",
    folder: "תיק בניין",
    height: "גובה",
    conservation: "שימור",
    requests: (link: string) =>
      `בחיפה כל בקשה להיתר מסוכמת בקובץ requests.md (סוג, שימוש, מבקש ועורך, היתר, אירועים והחלטות ועדה). זו הסקירה הטובה ביותר לקרוא לפני המסמכים: ${link}`,
    pending: "עדיין לא הורד — הקישור פותח אותו באתר העירייה",
    partial: (ready: number, total: number) =>
      `הרשימה עדיין חלקית: עד עכשיו הגיעו ${ready} מתוך ${total} מסמכים, והשאר עדיין מורדים, ולכן ניתוח בשלב הזה מבוסס רק על חלק מהמסמכים וכדאי לחזור עליו כשהסריקה תסתיים.`,
    mapRights:
      "חלק מהמפות ההיסטוריות לקוחות מ-Palestine Open Maps. המקור המדויק מופיע בעמודת ההערה ומתחת לתמונה. זכויות הפרסום שלהן אינן ידועות, ולכן היסטוריה שמשתמשת במפה כזו מציינת את המקור, והמפה אינה מאושרת לפרסום מחדש.",
    forAi: "הנחיות לניתוח",
    instructions: (scanId: string, connect: string, example: string) => [
      "המטרה: היסטוריה כרונולוגית של הבניין מתוך המסמכים שבטבלה: הקמה, היתרים, תוספות, שינויי שימוש ותוכניות בניין עיר שחלות עליו.",
      `כל עובדה מלווה במספר המסמך ובתאריך, ומספר המסמך הוא קישור שהמשתמש יכול לפתוח: ${example}. הקישור לכל מסמך בנוי כך, עם מספר המסמך בסוף.`,
      `רוב המסמכים סרוקים ואין בהם טקסט. המחבר "Archive Scanner" קורא את הדפים עצמם: get_document_pages עם scan_id ${scanId}, ו-browse_pages לסריקת הרבה עמודים בבת אחת (למשל כדי למצוא תצלומים). קישורי "עמוד 1" (העמודים הבאים: ‎-002.jpg, ‎-003.jpg וכן הלאה) וקישורי "תמונות מוקטנות", שמראים את כל עמודי המסמך ברשת אחת, הם תמונות רגילות.`,
      "מסקנות שמבוססות על הרשימה בלבד נשענות על כותרות ותאריכים; היסטוריה שנכתבה רק מהרשימה מציינת זאת.",
      `העמוד הזה מספיק כדי להתחיל בלי להתקין דבר. המחבר, שקורא את כל הדפים הסרוקים, נמצא כאן: ${connect}`,
    ],
    summary: (c: Record<string, number>) => `${c.archive ?? 0} מסמכי ארכיון · ${c.plan ?? 0} קבצי תב"ע · ${c.map ?? 0} מפות היסטוריות`,
    plans: 'תוכניות בניין עיר (תב"ע)',
    documents: "מסמכים",
    columns: ["מזהה", "תאריך", "סוג", "כותרת", "היתר / תוכנית", "עמודים", "קישורים", "הערה"],
    file: "קובץ",
    page1: "עמוד 1",
    thumbs: "תמונות מוקטנות",
    original: "מקור",
    open: "פתיחה",
    downloads: "הורדות",
    index: "רשימת מסמכים (CSV)",
    zip: "כל הקבצים (ZIP)",
    expires: "הקישורים בעמוד זה פעילים 24 שעות מסיום הסריקה.",
    unavailable: "הסריקה לא נמצאה, עדיין לא הסתיימה, או שתוקפה פג.",
  },
  en: {
    title: (label: string) => `Building archive: ${label}`,
    building: "Building ID",
    built: "Built",
    gushHelka: "Block/parcel",
    folder: "Building file",
    height: "Height",
    conservation: "Conservation",
    requests: (link: string) =>
      `In Haifa every permit request is summarised in requests.md (type, use, applicant and architect, permit, events, committee decisions). It is the best overview to read before the documents: ${link}`,
    pending: "not downloaded yet — the link opens it on the municipal site",
    partial: (ready: number, total: number) =>
      `This list is still partial: ${ready} of ${total} documents have arrived so far and the rest are still downloading, so an analysis now rests on only some of the documents and is worth repeating once the scan is done.`,
    mapRights:
      "Some historic maps come from Palestine Open Maps. Their exact source is in the note column and under the image. Their publishing rights are unknown, so a history that uses such a map names that source, and the map is not cleared for republishing.",
    forAi: "Analysis guidelines",
    instructions: (scanId: string, connect: string, example: string) => [
      "The goal: a chronological history of the building from the documents below — construction, permits, additions, changes of use, and the zoning plans that apply to it.",
      `Each fact cites its document ID and date, with the ID as a link the user can open: ${example}. Every document's link follows this pattern, with its ID at the end.`,
      `Most documents are scans without text. The "Archive Scanner" connector reads the pages themselves: get_document_pages with scan_id ${scanId}, and browse_pages to skim many pages at once (for example to find photographs). The "page 1" links (later pages end in -002.jpg, -003.jpg and so on) and the "thumbnails" links, which show every page of a document in one grid, are plain images.`,
      "Conclusions drawn from this list alone rest on titles and dates; a history based only on the list says so.",
      `This page is enough to start without installing anything. The connector, which reads every scanned page, is at: ${connect}`,
    ],
    summary: (c: Record<string, number>) => `${c.archive ?? 0} archive documents · ${c.plan ?? 0} plan files · ${c.map ?? 0} historic maps`,
    plans: "Zoning plans",
    documents: "Documents",
    columns: ["ID", "Date", "Type", "Title", "Permit / plan", "Pages", "Links", "Note"],
    file: "file",
    page1: "page 1",
    thumbs: "thumbnails",
    original: "source",
    open: "open",
    downloads: "Downloads",
    index: "Document list (CSV)",
    zip: "All files (ZIP)",
    expires: "The links on this page work for 24 hours after the scan finished.",
    unavailable: "This scan wasn't found, isn't finished yet, or has expired.",
  },
} as const;

const page = (lang: Lang, title: string, body: string) =>
  `<!doctype html><html lang="${lang}" dir="${lang === "he" ? "rtl" : "ltr"}"><head><meta charset="utf-8">` +
  `<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex">` +
  `<title>${esc(title)}</title><style>body{font:15px/1.5 system-ui,sans-serif;margin:24px auto;max-width:1100px;padding:0 16px}` +
  `table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #ddd;padding:4px 6px;text-align:start;vertical-align:top}` +
  `</style></head><body>${body}</body></html>`;

export const aiPage = (lang: Lang) => async (c: Context<AppContext>) => {
  const t = COPY[lang];
  const id = c.req.param("id") ?? "";
  const loaded = /^[0-9a-f]{32}$/.test(id) ? await loadManifest(c.env, id) : "invalid";
  if (typeof loaded === "string") {
    return c.html(page(lang, t.unavailable, `<p>${esc(t.unavailable)}</p>`), 404, { "X-Robots-Tag": "noindex" });
  }
  const { manifest, sourceId, arriving } = loaded;
  const haifa = manifest.city === "haifa";
  const base = `${baseUrl(c.env, manifest.city ?? "tel-aviv")}/files/${sourceId}/`;
  const connect = `${c.env.PUBLIC_URL}${lang === "en" ? "/en" : ""}/connect`;
  const counts: Record<string, number> = {};
  for (const doc of manifest.documents) counts[doc.source] = (counts[doc.source] ?? 0) + 1;

  const b = manifest.building;
  const a = manifest.address;
  const conservation = b.conservation ? [b.conservation.status, b.conservation.grade, b.conservation.file_no].filter(Boolean) : [];
  const facts = [
    b.building_id ? `${t.building} ${b.building_id}` : null,
    b.year_built ? `${t.built} ${b.year_built}` : null,
    b.height_m ? `${t.height} ${Math.round(b.height_m)} m` : null,
    conservation.length ? `${t.conservation}: ${conservation.join(" ")}` : null,
    a.gush ? `${t.gushHelka} ${a.gush}/${a.helka ?? "?"}` : null,
    manifest.archive_folder_no ? `${t.folder} ${manifest.archive_folder_no}` : null,
  ].filter(Boolean);

  const link = (href: string, label: string) => `<a href="${esc(href)}">${esc(label)}</a>`;
  const sample = manifest.documents[0]?.doc_id ?? "A-001";
  const example = `[${sample}](${docLink(c.env.PUBLIC_URL, id, sample)})`;
  const row = (d: ManifestDocument) => {
    // The short link is printed as text too: some AI browsing tools see a page's text but not its hrefs.
    const short = docLink(c.env.PUBLIC_URL, id, d.doc_id);
    const links = [
      link(short, short),
      d.file ? link(base + d.file, t.file) : null,
      d.page_images[0] ? link(base + d.page_images[0], t.page1) : null,
      d.sheets?.[0] ? link(base + d.sheets[0].image, t.thumbs) : null,
      d.original_url ? link(d.original_url, t.original) : null,
    ].filter(Boolean);
    const ref = [d.permit_no, d.plan_no, d.plan_status].filter(Boolean).join(" · ");
    return (
      `<tr><td>${esc(d.doc_id)}</td><td>${esc(d.date ?? d.year ?? "")}</td><td>${esc(d.doc_type ?? d.source)}</td>` +
      `<td>${d.title !== d.doc_type ? esc(d.title) : ""}</td><td>${esc(ref)}</td><td>${esc(d.pages || "")}</td>` +
      `<td>${links.join(" · ")}</td><td>${esc(d.status === "pending" ? t.pending : (d.note ?? ""))}</td></tr>`
    );
  };

  const body =
    `<h1>${esc(t.title(a.label_he))}</h1><p>${esc(facts.join(" · "))}</p><p>${esc(t.summary(counts))}</p>` +
    `<h2>${esc(t.forAi)}</h2><ol>${[
      ...(arriving ? [t.partial(arriving.ready, arriving.total)] : []),
      ...(haifa && manifest.requests?.length ? [t.requests(base + "requests.md")] : []),
      ...t.instructions(sourceId, connect, example),
      ...(manifest.documents.some((d) => d.source === "map" && d.note) ? [t.mapRights] : []),
    ]
      .map((line) => `<li>${esc(line)}</li>`)
      .join("")}</ol>` +
    (manifest.plans.length
      ? `<h2>${esc(t.plans)}</h2><ul>${manifest.plans
          .map((p) => `<li>${esc([p.plan_no, p.name, p.status, p.in_force_date].filter(Boolean).join(" · "))}</li>`)
          .join("")}</ul>`
      : "") +
    `<h2>${esc(t.documents)}</h2><table><thead><tr>${t.columns.map((col) => `<th>${esc(col)}</th>`).join("")}</tr></thead>` +
    `<tbody>${manifest.documents.map(row).join("")}</tbody></table>` +
    `<h2>${esc(t.downloads)}</h2><p>${link(base + "index.csv", t.index)} · ${link(base + "bundle.zip", t.zip)}</p>` +
    (manifest.warnings.length ? `<ul>${manifest.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>` : "") +
    `<p>${esc(t.expires)}</p>`;

  return c.html(page(lang, t.title(a.label_he), body), 200, {
    "X-Robots-Tag": "noindex",
    // A filling scan's page changes as documents arrive.
    "Cache-Control": arriving ? "no-store" : "public, max-age=300",
  });
};

// /d/<scan>/<doc>: sends the reader to the document's file, its first page, or the original source.
export const docRedirect = async (c: Context<AppContext>) => {
  const id = c.req.param("id") ?? "";
  const loaded = /^[0-9a-f]{32}$/.test(id) ? await loadManifest(c.env, id) : "invalid";
  const doc = typeof loaded === "string" ? null : loaded.manifest.documents.find((d) => d.doc_id === c.req.param("doc"));
  if (typeof loaded === "string" || !doc) {
    return c.text("This document wasn't found, or the scan has expired (results are kept for 24 hours).", 404);
  }
  if (doc.status === "pending") {
    // Not here yet: queue it first and send the reader to the municipal copy meanwhile.
    if ((await requestDocument(c.env, loaded.sourceId, doc.doc_id)) === "queued") c.executionCtx.waitUntil(dispatch(c.env));
  }
  const path = doc.file ?? doc.page_images[0];
  const target = path ? `${c.env.PUBLIC_URL}/files/${loaded.sourceId}/${path}` : doc.original_url;
  if (!target) return c.text("This document has no file.", 404);
  return c.redirect(target, 302);
};
