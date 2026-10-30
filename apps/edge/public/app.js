import { STRINGS } from "./i18n.js";

const lang = document.documentElement.lang === "en" ? "en" : "he";
const t = STRINGS[lang];
const base = lang === "en" ? "/en" : "";
const app = document.getElementById("app");
const PENDING_KEY = "tas.pending-selection";

// ---------- helpers ----------

const h = (tag, attrs = {}, ...children) => {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const child of children.flat()) if (child != null && child !== false) el.append(child);
  return el;
};

const api = async (path, init = {}) => {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init.headers || {}) },
    credentials: "same-origin",
  });
  const data = await response.json().catch(() => ({}));
  return { ok: response.ok, status: response.status, data };
};

const store = {
  get(key) {
    try { return JSON.parse(localStorage.getItem(key) || "null"); } catch { return null; }
  },
  set(key, value) {
    try { value == null ? localStorage.removeItem(key) : localStorage.setItem(key, JSON.stringify(value)); } catch {}
  },
};

const formatDate = (unix) =>
  new Date(unix * 1000).toLocaleString(lang === "he" ? "he-IL" : "en-GB", { dateStyle: "medium", timeStyle: "short" });

let config = null;
const getConfig = async () => (config ??= (await api("/config")).data);

// Turnstile: one widget per action, rendered on demand, reset after each use.
let turnstileReady = null;
const loadTurnstile = () =>
  (turnstileReady ??= new Promise((resolve) => {
    window.onTurnstileLoad = resolve;
    document.head.append(
      h("script", { src: "https://challenges.cloudflare.com/turnstile/v0/api.js?onload=onTurnstileLoad&render=explicit", async: true }),
    );
  }));

const turnstileToken = async (slot, action) => {
  const { turnstileSiteKey, devMode } = await getConfig();
  if (devMode) return "dev"; // local development: the server skips the check too
  await loadTurnstile();
  return new Promise((resolve) => {
    slot.replaceChildren();
    window.turnstile.render(slot, {
      sitekey: turnstileSiteKey,
      action,
      language: lang,
      appearance: "interaction-only",
      callback: resolve,
      "error-callback": () => resolve(null),
    });
  });
};

const shell = (...content) => {
  const other = lang === "en" ? location.pathname.replace(/^\/en/, "") || "/" : `/en${location.pathname}`;
  app.replaceChildren(
    h("header", {},
      h("a", { class: "brand", href: `${base}/` }, t.brand),
      h("nav", {},
        h("a", { href: `${base}/scans` }, t.myScans),
        h("a", { href: `${base}/connect` }, t.connect),
        h("a", { href: other, lang: lang === "en" ? "he" : "en" }, t.langSwitch))),
    ...content.filter((item) => item != null && item !== false),
    h("footer", {}, h("a", { href: `${base}/privacy` }, t.privacy), h("span", {}, "Tel Aviv Archive Scanner")),
  );
};

// ---------- home: search -> confirm -> sign in -> start ----------

const quotaLine = (quota) =>
  quota ? h("p", { class: "muted small" }, quota.weeklyLeft > 0 ? t.quota(quota.weeklyLeft, quota.weeklyLimit) : t.quotaOut) : null;

const home = async () => {
  const results = h("div");
  const input = h("input", { type: "text", name: "q", placeholder: t.placeholder, autocomplete: "street-address", "aria-label": t.placeholder });
  const button = h("button", { type: "submit" }, t.search);
  const me = (await api("/me")).data;

  const search = async (event) => {
    event?.preventDefault();
    const q = input.value.trim();
    if (q.length < 3) return;
    button.disabled = true;
    button.textContent = t.searching;
    const { ok, data } = await api(`/lookup?q=${encodeURIComponent(q)}`);
    button.disabled = false;
    button.textContent = t.search;
    if (!ok) return results.replaceChildren(h("p", { class: "notice bad" }, t.lookupFailed));
    if (!data.matches?.length) return results.replaceChildren(h("p", { class: "notice" }, t.noMatches));
    results.replaceChildren(
      h("ul", { class: "list" },
        data.matches.map((m) =>
          h("li", {},
            h("button", { class: "item", type: "button", onclick: () => confirm(m) },
              h("span", {}, lang === "he" ? m.label_he : m.label_en),
              h("span", { class: "muted small" }, describeBuilding(m.building)))))),
    );
  };

  const confirm = (match) => {
    const mapEl = h("div", { id: "map", role: "img", "aria-label": lang === "he" ? match.label_he : match.label_en });
    const status = h("div");
    const slot = h("div", { class: "cf-turnstile-slot" });
    const start = h("button", { type: "button", onclick: () => begin(match, status, slot, start) }, t.scanThis);
    results.replaceChildren(
      h("h2", {}, t.confirmTitle),
      h("p", { class: "muted small" }, `${lang === "he" ? match.label_he : match.label_en} · ${describeBuilding(match.building)}`),
      mapEl,
      h("p", { class: "muted small" }, t.confirmHint),
      h("div", { class: "actions" }, start, h("button", { class: "ghost", type: "button", onclick: () => search() }, t.back)),
      slot,
      status,
    );
    drawBuilding(mapEl, match.building);
  };

  shell(
    h("h1", {}, t.title),
    h("p", { class: "muted" }, t.intro),
    h("form", { class: "row", onsubmit: search }, input, button),
    me.user ? quotaLine(me.quota) : null,
    new URLSearchParams(location.search).get("signin") === "expired" ? h("p", { class: "notice" }, t.signinExpired) : null,
    results,
  );

  // Back from the email link: continue with the building chosen before signing in.
  const pending = store.get(PENDING_KEY);
  if (me.user && pending && Date.now() - pending.at < 55 * 60 * 1000) {
    store.set(PENDING_KEY, null);
    confirm(pending.match);
  }
  input.focus();
};

const describeBuilding = (b) =>
  [b.year_built ? `${t.built} ${b.year_built}` : null, b.floors ? `${b.floors} ${t.floors}` : null, `${t.buildingId} ${b.building_id}`]
    .filter(Boolean)
    .join(" · ");

const drawBuilding = async (el, building) => {
  if (!window.L) {
    document.head.append(h("link", { rel: "stylesheet", href: "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" }));
    await new Promise((resolve) => document.head.append(h("script", { src: "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js", onload: resolve })));
  }
  const map = window.L.map(el, { scrollWheelZoom: false }).setView([building.lat, building.lon], 18);
  window.L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap" }).addTo(map);
  const rings = (building.outline || []).map((ring) => ring.map(([lon, lat]) => [lat, lon]));
  if (rings.length) window.L.polygon(rings, { color: "#d92d20", weight: 3, fillOpacity: 0.25 }).addTo(map);
};

const begin = async (match, status, slot, button) => {
  const me = (await api("/me")).data;
  if (!me.user) return signIn(match, status, slot);
  button.disabled = true;
  status.replaceChildren(h("p", { class: "muted small" }, t.starting));
  const token = await turnstileToken(slot, "scan");
  if (!token) {
    button.disabled = false;
    return status.replaceChildren(h("p", { class: "notice bad" }, t.botFailed));
  }
  const { ok, data } = await api("/scans", { method: "POST", body: JSON.stringify({ selection: match.selection, turnstileToken: token }) });
  slot.replaceChildren();
  if (ok) return void (location.href = `${base}/scan/${data.scanId}`);
  button.disabled = false;
  const message = data.error === "weekly_quota" ? t.quotaOut : data.error === "daily_cap" ? t.dailyCap : data.error === "bot_check_failed" ? t.botFailed : t.lookupFailed;
  status.replaceChildren(h("p", { class: "notice bad" }, message));
};

const signIn = (match, status, slot, returnTo = `${base}/`) => {
  const email = h("input", { type: "email", name: "email", placeholder: t.email, autocomplete: "email", required: true, "aria-label": t.email });
  const send = h("button", { type: "submit" }, t.sendLink);
  const form = h("form", { class: "row", onsubmit: async (event) => {
    event.preventDefault();
    send.disabled = true;
    const token = await turnstileToken(slot, "signin");
    const { ok, data } = await api("/auth/request", {
      method: "POST",
      body: JSON.stringify({ email: email.value, turnstileToken: token, locale: lang, returnTo }),
    });
    slot.replaceChildren();
    send.disabled = false;
    if (!ok) return status.append(h("p", { class: "notice bad" }, data.error === "bot_check_failed" ? t.botFailed : t.email));
    if (match) store.set(PENDING_KEY, { match, at: Date.now() });
    status.replaceChildren(h("p", { class: "notice" }, t.linkSent));
  } }, email, send);
  status.replaceChildren(h("h2", {}, t.signinTitle), h("p", { class: "muted small" }, t.signinHint), form);
  email.focus();
};

// Sign-in only (an AI app sent the user here to connect the scanner).
const signInPage = (returnTo) => {
  const status = h("div");
  const slot = h("div", { class: "cf-turnstile-slot" });
  shell(h("h1", {}, t.connect), slot, status);
  signIn(null, status, slot, returnTo);
};

// ---------- scan page ----------

const STAGE_ORDER = ["maps", "plans", "archive", "pages", "outputs", "upload"];

const scanPage = async (id) => {
  let timer;
  const render = async () => {
    const { ok, data: scan } = await api(`/scans/${encodeURIComponent(id)}`);
    if (!ok) return shell(h("p", { class: "notice" }, t.notFound));
    const label = lang === "he" ? scan.label_he : scan.label_en;
    const parts = [h("h1", {}, label), h("p", { class: "muted small" }, describeBuilding(scan.building))];
    if (scan.reused) parts.push(h("p", { class: "notice" }, t.reused));

    if (scan.status === "failed") parts.push(h("p", { class: "notice bad" }, t.failed));
    else if (scan.status === "expired") parts.push(h("p", { class: "notice" }, t.expired));
    else if (scan.status === "done") parts.push(...results(scan, label));
    else {
      if (scan.queue_position) parts.push(h("p", { class: "notice" }, t.queued(scan.queue_position)));
      parts.push(stagesList(scan.stages), h("p", { class: "muted small" }, t.leaveHint));
    }
    shell(...parts);
    clearTimeout(timer);
    if (!["done", "failed", "expired"].includes(scan.status)) timer = setTimeout(render, 3000);
  };
  await render();
};

const stagesList = (stages = {}) =>
  h("div", { class: "stages" },
    STAGE_ORDER.map((key) => {
      const s = stages[key] || { status: "pending" };
      return h("div", { class: `stage ${s.status}` },
        h("span", {}, t.stages[key]),
        h("span", { class: "state" }, s.detail && s.status !== "running" ? s.detail : t.state[s.status] || s.status));
    }));

const results = (scan, label) => {
  const file = (name) => `${scan.files_base}${name}`;
  const prompt = encodeURIComponent(t.aiPrompt(scan.id, label));
  const counts = scan.summary?.counts;
  const items = [
    h("h2", {}, t.ready),
    counts ? h("p", { class: "muted small" }, t.counts(counts)) : null,
    h("h2", {}, t.analyzeTitle),
    h("p", { class: "muted small" }, t.analyzeHint),
    h("div", { class: "actions" },
      h("a", { class: "button", href: `https://claude.ai/new?q=${prompt}`, target: "_blank", rel: "noopener" }, t.analyzeClaude),
      h("a", { class: "button", href: `https://chatgpt.com/?q=${prompt}`, target: "_blank", rel: "noopener" }, t.analyzeChatGPT)),
    h("p", { class: "small" }, h("a", { href: `${base}/connect` }, t.connectLink)),
    h("h2", {}, t.downloadsTitle),
    h("div", { class: "actions" },
      h("a", { class: "button ghost", href: file("bundle.zip") }, t.zip),
      h("a", { class: "button ghost", href: file("index.csv") }, t.index),
      h("a", { class: "button ghost", href: file(lang === "he" ? "PROMPT.he.md" : "PROMPT.en.md"), target: "_blank" }, t.prompt),
      h("a", { class: "button ghost", href: file("archive.pptx") }, t.archiveDeck),
      h("a", { class: "button ghost", href: file("maps_plans.pptx") }, t.mapsDeck)),
    scan.expires_at ? h("p", { class: "muted small" }, t.expires(formatDate(scan.expires_at))) : null,
  ];
  const warnings = scan.summary?.warnings || [];
  if (warnings.length) items.push(h("details", {}, h("summary", {}, `${t.warnings} (${warnings.length})`), h("ul", {}, warnings.map((w) => h("li", {}, w)))));
  return items;
};

// ---------- my scans ----------

const myScans = async () => {
  const { ok, data } = await api("/scans");
  if (!ok) return home();
  const me = (await api("/me")).data;
  shell(
    h("h1", {}, t.myScans),
    me.user ? h("p", { class: "muted small" }, t.signedInAs(me.user.email), " · ",
      h("a", { href: "#", onclick: async (e) => { e.preventDefault(); await api("/auth/logout", { method: "POST" }); location.href = `${base}/`; } }, t.signOut)) : null,
    quotaLine(me.quota),
    h("ul", { class: "list" },
      data.scans.map((s) =>
        h("li", {}, h("a", { class: "item", href: `${base}/scan/${s.id}`, style: "display:flex;justify-content:space-between;padding:14px 4px;text-decoration:none" },
          h("span", {}, lang === "he" ? s.label_he : s.label_en),
          h("span", { class: "muted small" }, t.state[s.status] || s.status))))),
  );
};

// ---------- static pages ----------

const connectPage = () => {
  const url = `${location.origin}/mcp`;
  const he = lang === "he";
  shell(
    h("h1", {}, t.connect),
    h("p", { class: "muted" }, he
      ? "חברו את סורק הארכיון ל-AI שלכם פעם אחת. אחר כך ה-AI יוכל להתחיל סריקות, לקרוא את רשימת המסמכים ולפתוח עמודים סרוקים בעצמו."
      : "Connect the scanner to your AI once. After that it can start scans, read the document list and open scanned pages by itself."),
    h("h2", {}, he ? "כתובת המחבר" : "Connector URL"),
    h("pre", {}, url),
    h("h2", {}, "Claude"),
    h("p", {}, he
      ? "Settings → Connectors → Add custom connector → הדביקו את הכתובת ← Add. בפעם הראשונה תתבקשו להיכנס עם האימייל."
      : "Settings → Connectors → Add custom connector → paste the URL → Add. The first time, you'll sign in with your email."),
    h("h2", {}, "ChatGPT"),
    h("p", {}, he
      ? "Settings → Apps & Connectors → Advanced → Developer mode → Create → הדביקו את הכתובת. הזמינות תלויה בתוכנית שלכם."
      : "Settings → Apps & Connectors → Advanced → Developer mode → Create → paste the URL. Availability depends on your plan."),
    h("p", { class: "muted small" }, he
      ? "המחבר משתמש באותן מגבלות כמו האתר: 3 סריקות בשבוע, וסריקות של בניין מ-24 השעות האחרונות בחינם."
      : "The connector has the same limits as the website: 3 scans a week, and buildings scanned in the last 24 hours are free."),
  );
};

const privacyPage = () => {
  const he = lang === "he";
  shell(
    h("h1", {}, t.privacy),
    h("p", {}, he
      ? "אנחנו שומרים רק את כתובת האימייל שלכם ואת רשימת הסריקות שביקשתם, כדי לשלוח קישורי כניסה, להודיע כשסריקה מוכנה ולאכוף את מגבלת הסריקות."
      : "We keep only your email address and the scans you requested, to send sign-in links, tell you when a scan is ready and enforce the scan limit."),
    h("p", {}, he
      ? "קבצי הסריקה הם מסמכים ציבוריים של עיריית תל אביב-יפו ו-MapJunction. הם נמחקים אוטומטית 24 שעות אחרי שהסריקה מסתיימת."
      : "Scan files are public records from the Tel Aviv-Yafo municipality and MapJunction. They are deleted automatically 24 hours after a scan finishes."),
    h("p", {}, he ? "אנחנו לא משתמשים בעוגיות פרסום או מעקב. עוגייה אחת שומרת את הכניסה שלכם." : "No advertising or tracking cookies. One cookie keeps you signed in."),
  );
};

// ---------- router ----------

const path = location.pathname.replace(/^\/en(?=\/|$)/, "") || "/";
const scanMatch = path.match(/^\/scan\/([0-9a-f]{32})\/?$/);
const params = new URLSearchParams(location.search);
if (scanMatch) scanPage(scanMatch[1]);
else if (params.get("signin") === "1" && params.get("returnTo")?.startsWith("/authorize")) signInPage(params.get("returnTo"));
else if (path === "/scans") myScans();
else if (path === "/connect") connectPage();
else if (path === "/privacy") privacyPage();
else home();
