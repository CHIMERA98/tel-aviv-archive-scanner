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

const relative = new Intl.RelativeTimeFormat(lang === "he" ? "he" : "en", { numeric: "auto" });
const ago = (unix) => {
  const s = unix - Date.now() / 1000;
  const text =
    Math.abs(s) < 3600 ? relative.format(Math.round(s / 60), "minute")
    : Math.abs(s) < 86400 ? relative.format(Math.round(s / 3600), "hour")
    : relative.format(Math.round(s / 86400), "day");
  return text.replace(/\s*\(\d+\)$/, ""); // some browsers write Hebrew duals as "לפני שעתיים (2)"
};

const label = (item) => (lang === "he" ? item.label_he : item.label_en);

// Inline SVG (no inline script, so the CSP allows it). Points "back" in LTR; CSS flips it for RTL and forward.
const chevron = () => {
  const wrap = document.createElement("span");
  wrap.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 5l-7 7 7 7"/></svg>';
  return wrap.firstChild;
};

let config = null;
const getConfig = async () => (config ??= (await api("/config")).data);

let me = { user: null, quota: null };
const refreshMe = async () => (me = (await api("/me")).data);

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

// ---------- layout: one centered step at a time, arrows between visited steps ----------

// A step is { root, url?, enter?(), leave?() }. Its root node is built once, so going back keeps what was typed.
const trail = [];
let at = -1;

const stepView = h("div", { class: "step" });
const dots = h("span", { class: "dots", "aria-hidden": "true" });
const prevButton = h("button", { class: "arrow prev", type: "button", "aria-label": t.prev, onclick: () => move(-1) }, chevron());
const nextButton = h("button", { class: "arrow next", type: "button", "aria-label": t.next, onclick: () => move(1) }, chevron());
const stepsNav = h("nav", { class: "steps", "aria-label": t.stepsNav }, prevButton, nextButton);
const activityView = h("section", { class: "activity", "aria-label": t.activityTitle });

const show = () => {
  const step = trail[at];
  stepView.replaceChildren(step.root);
  if (step.url && location.pathname !== step.url) history.replaceState(null, "", step.url);
  prevButton.disabled = at <= 0;
  nextButton.disabled = at >= trail.length - 1;
  stepsNav.hidden = dots.hidden = trail.length <= 1;
  dots.replaceChildren(...trail.map((_, i) => h("span", { class: i === at ? "on" : null })));
  step.enter?.();
};

const go = (step) => {
  trail[at]?.leave?.();
  trail.splice(at + 1);
  trail.push(step);
  at = trail.length - 1;
  show();
};

const move = (delta) => {
  const target = at + delta;
  if (target < 0 || target >= trail.length) return;
  trail[at].leave?.();
  at = target;
  show();
};

const home = (event) => {
  event?.preventDefault();
  if (at === 0) return;
  trail[at].leave?.();
  at = 0;
  show();
};

const view = (...children) => h("div", { class: "view" }, ...children);

// The same page in the other language; read at click time because each step updates the URL.
const otherLanguage = () => (lang === "en" ? location.pathname.replace(/^\/en/, "") || "/" : `/en${location.pathname}`);

const layout = () => {
  const link = (href, text, step) =>
    h("a", { href, onclick: (event) => { event.preventDefault(); go(step()); } }, text);
  app.replaceChildren(
    h("header", { class: "topbar" },
      h("a", { class: "brand", href: `${base}/`, onclick: home }, t.brand),
      h("nav", {},
        link(`${base}/scans`, t.myScans, myScansStep),
        link(`${base}/connect`, t.connect, connectStep),
        h("a", { href: otherLanguage(), lang: lang === "en" ? "he" : "en", onclick: (e) => { e.currentTarget.href = otherLanguage(); } }, t.langSwitch))),
    h("div", { class: "center" }, stepView, dots),
    stepsNav,
    activityView,
    h("footer", {}, link(`${base}/privacy`, t.privacy, privacyStep)),
  );
};

// ---------- activity: what is scanning, waiting and already scanned ----------

let activityTimer;
const loadActivity = async () => {
  clearTimeout(activityTimer);
  if (!document.hidden) {
    const { ok, data } = await api("/activity");
    if (ok) renderActivity(data);
  }
  activityTimer = setTimeout(loadActivity, 10000);
};
document.addEventListener("visibilitychange", () => { if (!document.hidden) loadActivity(); });

const renderActivity = ({ total, items }) => {
  const row = (item) => {
    const meta =
      item.status === "running" ? (item.stage ? t.stages[item.stage] : t.state.running)
      : item.status === "queued" ? t.inLine(item.position)
      : item.status === "done" ? [item.counts ? t.docsCount(item.counts.archive) : null, ago(item.finished_at)].filter(Boolean).join(" · ")
      : `${t.filesDeleted} · ${ago(item.finished_at)}`;
    const content = [h("span", { class: `dot ${item.status}` }), h("span", { class: "address" }, label(item)), h("span", { class: "meta" }, meta)];
    return h("li", {}, item.id
      ? h("a", { href: `${base}/scan/${item.id}`, onclick: (event) => { event.preventDefault(); go(scanStep(item.id)); } }, content)
      : h("div", { class: "expired" }, content));
  };
  activityView.replaceChildren(
    h("h2", {}, t.activityTitle),
    total ? h("p", { class: "muted small" }, t.scannedTotal(total)) : null,
    items.length ? h("ul", {}, items.map(row)) : h("p", { class: "muted small" }, t.activityEmpty),
  );
};

// ---------- steps: search -> choose -> confirm -> sign in -> scan ----------

const quotaLine = (quota) =>
  quota ? h("p", { class: "muted small" }, quota.weeklyLeft > 0 ? t.quota(quota.weeklyLeft, quota.weeklyLimit) : t.quotaOut) : null;

const searchStep = () => {
  const input = h("input", { type: "text", name: "q", placeholder: t.placeholder, autocomplete: "street-address", "aria-label": t.placeholder });
  const button = h("button", { type: "submit" }, t.search);
  const note = h("div");
  const quota = h("div");

  const search = async (event) => {
    event.preventDefault();
    const q = input.value.trim();
    if (q.length < 3) return input.focus();
    button.disabled = true;
    button.textContent = t.searching;
    const { ok, data } = await api(`/lookup?q=${encodeURIComponent(q)}`);
    button.disabled = false;
    button.textContent = t.search;
    if (!ok) return note.replaceChildren(h("p", { class: "notice bad" }, t.lookupFailed));
    if (!data.matches?.length) return note.replaceChildren(h("p", { class: "notice" }, t.noMatches));
    note.replaceChildren();
    go(data.matches.length === 1 ? confirmStep(data.matches[0]) : chooseStep(data.matches));
  };

  return {
    url: `${base}/`,
    root: view(
      h("h1", {}, t.title),
      h("p", { class: "muted" }, t.intro),
      h("form", { class: "row", onsubmit: search }, input, button),
      quota,
      new URLSearchParams(location.search).get("signin") === "expired" ? h("p", { class: "notice" }, t.signinExpired) : null,
      note,
    ),
    enter: () => {
      quota.replaceChildren(me.user ? quotaLine(me.quota) : "");
      input.focus();
    },
  };
};

const chooseStep = (matches) => ({
  root: view(
    h("h2", {}, t.chooseTitle),
    h("ul", { class: "list" },
      matches.map((m) =>
        h("li", {},
          h("button", { class: "item", type: "button", onclick: () => go(confirmStep(m)) },
            h("span", {}, label(m)),
            h("span", { class: "muted small" }, describeBuilding(m.building)))))),
  ),
});

const describeBuilding = (b) =>
  [b.year_built ? `${t.built} ${b.year_built}` : null, b.floors ? `${b.floors} ${t.floors}` : null, `${t.buildingId} ${b.building_id}`]
    .filter(Boolean)
    .join(" · ");

const confirmStep = (match) => {
  const mapEl = h("div", { class: "map", role: "img", "aria-label": label(match) });
  const status = h("div");
  const slot = h("div", { class: "cf-turnstile-slot" });
  const start = h("button", { type: "button", onclick: () => begin(match, status, slot, start) }, t.scanThis);
  let map = null;
  return {
    root: view(
      h("h2", {}, t.confirmTitle),
      h("p", { class: "muted small" }, `${label(match)} · ${describeBuilding(match.building)}`),
      mapEl,
      h("p", { class: "muted small" }, t.confirmHint),
      start,
      slot,
      status,
    ),
    enter: async () => {
      if (map) return map.invalidateSize(); // the map was detached while another step was showing
      map = await drawBuilding(mapEl, match.building);
    },
  };
};

const drawBuilding = async (el, building) => {
  if (!window.L) {
    document.head.append(h("link", { rel: "stylesheet", href: "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" }));
    await new Promise((resolve) => document.head.append(h("script", { src: "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js", onload: resolve })));
  }
  const map = window.L.map(el, { scrollWheelZoom: false, attributionControl: false }).setView([building.lat, building.lon], 18);
  window.L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19 }).addTo(map);
  window.L.control.attribution({ prefix: false }).addAttribution("© OpenStreetMap").addTo(map);
  const rings = (building.outline || []).map((ring) => ring.map(([lon, lat]) => [lat, lon]));
  if (rings.length) window.L.polygon(rings, { color: "#d92d20", weight: 3, fillOpacity: 0.25 }).addTo(map);
  return map;
};

const begin = async (match, status, slot, button) => {
  await refreshMe();
  if (!me.user) return go(signinStep(match));
  button.disabled = true;
  status.replaceChildren(h("p", { class: "muted small" }, t.starting));
  const token = await turnstileToken(slot, "scan");
  if (!token) {
    button.disabled = false;
    return status.replaceChildren(h("p", { class: "notice bad" }, t.botFailed));
  }
  const { ok, data } = await api("/scans", { method: "POST", body: JSON.stringify({ selection: match.selection, turnstileToken: token }) });
  slot.replaceChildren();
  button.disabled = false;
  if (ok) {
    status.replaceChildren();
    loadActivity();
    return go(scanStep(data.scanId));
  }
  const message = data.error === "weekly_quota" ? t.quotaOut : data.error === "daily_cap" ? t.dailyCap : data.error === "bot_check_failed" ? t.botFailed : t.lookupFailed;
  status.replaceChildren(h("p", { class: "notice bad" }, message));
};

const signinStep = (match, returnTo = `${base}/`) => {
  const status = h("div");
  const slot = h("div", { class: "cf-turnstile-slot" });
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
    if (!ok) return status.replaceChildren(h("p", { class: "notice bad" }, data.error === "bot_check_failed" ? t.botFailed : t.email));
    if (match) store.set(PENDING_KEY, { match, at: Date.now() });
    status.replaceChildren(h("p", { class: "notice" }, t.linkSent));
  } }, email, send);
  return {
    root: view(h("h2", {}, t.signinTitle), h("p", { class: "muted small" }, t.signinHint), form, slot, status),
    enter: () => email.focus(),
  };
};

// ---------- scan progress and results ----------

const STAGE_ORDER = ["maps", "plans", "archive", "pages", "outputs", "upload"];

const scanStep = (id) => {
  const root = view(h("p", { class: "muted small" }, "…"));
  let timer;
  let active = false;
  const render = async () => {
    const { ok, data: scan } = await api(`/scans/${encodeURIComponent(id)}`);
    if (!active) return;
    if (!ok) return root.replaceChildren(h("p", { class: "notice" }, t.notFound));
    const name = label(scan);
    const parts = [h("h1", {}, name), h("p", { class: "muted small" }, describeBuilding(scan.building))];
    if (scan.reused) parts.push(h("p", { class: "notice" }, t.reused));
    if (scan.status === "failed") parts.push(h("p", { class: "notice bad" }, t.failed));
    else if (scan.status === "expired") parts.push(h("p", { class: "notice" }, t.expired));
    else if (scan.status === "done") parts.push(...results(scan, name));
    else {
      if (scan.queue_position) parts.push(h("p", { class: "notice" }, t.queued(scan.queue_position)));
      parts.push(stagesList(scan.stages), h("p", { class: "muted small" }, t.leaveHint));
    }
    root.replaceChildren(...parts);
    if (!["done", "failed", "expired"].includes(scan.status)) timer = setTimeout(render, 3000);
  };
  return {
    root,
    url: `${base}/scan/${id}`,
    enter: () => { active = true; render(); },
    leave: () => { active = false; clearTimeout(timer); },
  };
};

const stagesList = (stages = {}) =>
  h("div", { class: "stages" },
    STAGE_ORDER.map((key) => {
      const s = stages[key] || { status: "pending" };
      return h("div", { class: `stage ${s.status}` },
        h("span", {}, t.stages[key]),
        h("span", { class: "state" }, s.detail && s.status !== "running" ? s.detail : t.state[s.status] || s.status));
    }));

// Claude's documented install link: opens "Add custom connector" prefilled; the user confirms and signs in.
const claudeInstallUrl = () =>
  "https://claude.ai/customize/connectors?modal=add-custom-connector" +
  `&connectorName=${encodeURIComponent("Tel Aviv Archive Scanner")}&connectorUrl=${encodeURIComponent(`${location.origin}/mcp`)}`;

const results = (scan, name) => {
  const file = (path) => `${scan.files_base}${path}`;
  // The prompt points at the scan's plain AI page, so it works without installing anything.
  const prompt = encodeURIComponent(t.aiPrompt(`${location.origin}${base}/scan/${scan.id}/ai`, name));
  const counts = scan.summary?.counts;
  const items = [
    counts ? h("p", {}, t.counts(counts)) : h("p", {}, t.ready),
    h("div", { class: "actions" },
      h("a", { class: "button", href: `https://claude.ai/new?q=${prompt}`, target: "_blank", rel: "noopener" }, t.analyzeClaude),
      h("a", { class: "button", href: `https://chatgpt.com/?q=${prompt}`, target: "_blank", rel: "noopener" }, t.analyzeChatGPT)),
    h("p", { class: "muted small" }, t.analyzeHint),
    h("p", { class: "muted small" }, t.readPages, " ",
      h("a", { href: claudeInstallUrl(), target: "_blank", rel: "noopener" }, t.connectClaude), " · ",
      h("a", { href: `${base}/connect`, onclick: (e) => { e.preventDefault(); go(connectStep()); } }, t.otherApps)),
    h("div", { class: "actions quiet" },
      h("a", { href: file("bundle.zip") }, t.zip),
      h("a", { href: file("index.csv") }, t.index),
      h("a", { href: file(lang === "he" ? "PROMPT.he.md" : "PROMPT.en.md"), target: "_blank" }, t.prompt),
      h("a", { href: file("archive.pptx") }, t.archiveDeck),
      h("a", { href: file("maps_plans.pptx") }, t.mapsDeck)),
    scan.expires_at ? h("p", { class: "muted small" }, t.expires(formatDate(scan.expires_at))) : null,
  ];
  const warnings = scan.summary?.warnings || [];
  if (warnings.length) items.push(h("details", {}, h("summary", {}, `${t.warnings} (${warnings.length})`), h("ul", {}, warnings.map((w) => h("li", {}, w)))));
  return items;
};

// ---------- my scans and static pages ----------

const myScansStep = () => {
  const root = view(h("p", { class: "muted small" }, "…"));
  return {
    root,
    url: `${base}/scans`,
    enter: async () => {
      await refreshMe();
      if (!me.user) return root.replaceChildren(h("h1", {}, t.myScans), h("p", { class: "muted" }, t.signinHint));
      const { data } = await api("/scans");
      root.replaceChildren(
        h("h1", {}, t.myScans),
        h("p", { class: "muted small" }, t.signedInAs(me.user.email), " · ",
          h("a", { href: "#", onclick: async (e) => { e.preventDefault(); await api("/auth/logout", { method: "POST" }); await refreshMe(); home(); } }, t.signOut)),
        quotaLine(me.quota),
        h("ul", { class: "list" },
          (data.scans || []).map((s) =>
            h("li", {},
              h("button", { class: "item", type: "button", onclick: () => go(scanStep(s.id)) },
                h("span", {}, label(s)),
                h("span", { class: "muted small" }, t.state[s.status] || s.status))))),
      );
    },
  };
};

const copyButton = (text) => {
  const button = h("button", { class: "ghost small-button", type: "button", onclick: async () => {
    try {
      await navigator.clipboard.writeText(text);
      button.textContent = lang === "he" ? "הועתק" : "Copied";
    } catch {
      button.textContent = text; // clipboard blocked: show the text to copy by hand
    }
  } }, lang === "he" ? "העתקת הכתובת" : "Copy address");
  return button;
};

const connectStep = () => {
  const url = `${location.origin}/mcp`;
  const he = lang === "he";
  return {
    url: `${base}/connect`,
    root: view(
      h("h1", {}, t.connect),
      h("p", { class: "muted" }, he
        ? "אחרי חיבור אחד, ה-AI שלכם יוכל לקרוא את הדפים הסרוקים עצמם — לא רק את רשימת המסמכים."
        : "Connect once, and your AI can read the scanned pages themselves, not just the document list."),
      h("a", { class: "button", href: claudeInstallUrl(), target: "_blank", rel: "noopener" }, t.connectClaude),
      h("p", { class: "muted small" }, he
        ? "Claude ייפתח עם הכול ממולא. לוחצים Add ונכנסים עם האימייל. בתוכנית החינמית של Claude אפשר מחבר מותאם אחד."
        : "Claude opens with everything filled in. Click Add, then sign in with your email. Claude's free plan allows one custom connector."),
      h("details", {},
        h("summary", {}, "ChatGPT"),
        h("ol", {},
          h("li", {}, he ? "נדרשת תוכנית בתשלום עם Developer mode." : "Needs a paid plan with Developer mode."),
          h("li", {}, "Settings → Apps & Connectors → Advanced → Developer mode."),
          h("li", {}, he ? "Create ← הדביקו את הכתובת ← היכנסו עם האימייל." : "Create → paste the address → sign in with your email.")),
        h("p", {}, copyButton(url))),
      h("details", {},
        h("summary", {}, he ? "אפליקציות אחרות" : "Other apps"),
        h("p", {}, he ? "כל אפליקציה שתומכת במחברי MCP מרוחקים — הוסיפו את הכתובת:" : "Any app that supports remote MCP connectors — add this address:"),
        h("pre", {}, url),
        h("p", {}, copyButton(url))),
      h("p", { class: "muted small" }, he
        ? "אותן מגבלות כמו באתר: 3 סריקות בשבוע, ובניין שנסרק ב-24 השעות האחרונות — בחינם."
        : "Same limits as the website: 3 scans a week, and buildings scanned in the last 24 hours are free."),
    ),
  };
};

const privacyStep = () => {
  const he = lang === "he";
  return {
    url: `${base}/privacy`,
    root: view(
      h("h1", {}, t.privacy),
      h("p", {}, he
        ? "אנחנו שומרים רק את כתובת האימייל שלכם ואת רשימת הסריקות שביקשתם, כדי לשלוח קישורי כניסה, להודיע כשסריקה מוכנה ולאכוף את מגבלת הסריקות."
        : "We keep only your email address and the scans you requested, to send sign-in links, tell you when a scan is ready and enforce the scan limit."),
      h("p", {}, he
        ? "רשימת הפעילות באתר מציגה אילו כתובות נסרקות ונסרקו, בלי לציין מי ביקש אותן."
        : "The activity list on the site shows which addresses are being or were scanned, never who asked for them."),
      h("p", {}, he
        ? "קבצי הסריקה הם מסמכים ציבוריים של עיריית תל אביב-יפו ו-MapJunction. הם נמחקים אוטומטית 24 שעות אחרי שהסריקה מסתיימת."
        : "Scan files are public records from the Tel Aviv-Yafo municipality and MapJunction. They are deleted automatically 24 hours after a scan finishes."),
      h("p", {}, he ? "אנחנו לא משתמשים בעוגיות פרסום או מעקב. עוגייה אחת שומרת את הכניסה שלכם." : "No advertising or tracking cookies. One cookie keeps you signed in."),
    ),
  };
};

// ---------- start ----------

const boot = async () => {
  layout();
  loadActivity();
  await refreshMe();
  const path = location.pathname.replace(/^\/en(?=\/|$)/, "") || "/";
  const scanMatch = path.match(/^\/scan\/([0-9a-f]{32})\/?$/);
  const params = new URLSearchParams(location.search);

  // An AI app sent the user here to connect the scanner: sign-in is the only step.
  if (params.get("signin") === "1" && params.get("returnTo")?.startsWith("/authorize")) {
    return go(signinStep(null, params.get("returnTo")));
  }
  // Every other page sits one step after the search, so the back arrow always leads there.
  go(searchStep());
  if (scanMatch) go(scanStep(scanMatch[1]));
  else if (path === "/scans") go(myScansStep());
  else if (path === "/connect") go(connectStep());
  else if (path === "/privacy") go(privacyStep());
  else {
    // Back from the email link: continue with the building chosen before signing in.
    const pending = store.get(PENDING_KEY);
    if (me.user && pending && Date.now() - pending.at < 55 * 60 * 1000) {
      store.set(PENDING_KEY, null);
      go(confirmStep(pending.match));
    }
  }
};

boot();
