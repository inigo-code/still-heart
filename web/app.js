const $ = (id) => document.getElementById(id);

const COPY = {
  queue: ["Queue", "Paste album URLs. Scan to preview pictures, or Grab to download everything."],
  preview: ["Preview", "Click to select. Shift-click selects a range. Double-click opens a picture."],
  history: ["History", "Finished downloads and scans. Failed ones stay here so you can retry them."],
  library: ["Library", "Files already saved in the download folder. Newest first."],
  sites: ["Sites", "Search gallery-dl sites. Click a card to add an example URL."],
  settings: ["Settings", "Download folder, cookies, filters, and gallery-dl updates."],
};

const ICO = {
  inbox: `<svg viewBox="0 0 24 24"><path d="M4 13h4l2 3h4l2-3h4v6H4v-6Z"/><path d="M4 13 7 5h10l3 8"/></svg>`,
  grid: `<svg viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>`,
  globe: `<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a15 15 0 0 1 0 18M12 3a15 15 0 0 0 0 18"/></svg>`,
  clock: `<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/></svg>`,
  search: `<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.2-3.2"/></svg>`,
  download: `<svg viewBox="0 0 24 24"><path d="M12 3v12m0 0 4-4m-4 4-4-4M4 19h16"/></svg>`,
};

const state = {
  jobs: [],
  scanItems: {},
  settings: {},
  view: "queue",
  layout: localStorage.getItem("stillheart-layout") || "thumbs",
  engine: {},
  kind: "all",
  scanId: "",
  selected: new Set(),
  lastIndex: null,
  health: null,
  disk: null,
  sites: [],
  sitesTotal: 0,
  logs: {},
  jobQuery: "",
  historyStatus: "all",
  libraryKind: "all",
  libraryFiles: [],
  libraryTotal: 0,
  lightbox: [],
  lightboxAt: 0,
  sending: false,
};

function escapeHtml(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function fmtElapsed(seconds) {
  const s = Math.max(0, Math.floor(seconds || 0));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${String(r).padStart(2, "0")}`;
}

function fmtBytes(n) {
  n = Number(n) || 0;
  if (n < 1024) return `${Math.max(0, Math.round(n))} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let u = -1;
  do {
    n /= 1024;
    u += 1;
  } while (n >= 1024 && u < units.length - 1);
  return `${n >= 100 ? Math.round(n) : n.toFixed(1)} ${units[u]}`;
}

function hostOf(url) {
  try {
    return new URL(url).host.replace(/^www\./, "");
  } catch {
    return String(url || "").slice(0, 48);
  }
}

function thumbSrc(url) {
  if (!url) return "";
  return `/api/thumb?u=${encodeURIComponent(url)}`;
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.left = "-9999px";
    document.body.appendChild(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  let data = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  if (!res.ok) {
    const detail = data && (data.detail || data.message);
    throw new Error(typeof detail === "string" ? detail : `Request failed (${res.status})`);
  }
  return data;
}

let confirmBusy = null;
let confirmExtra = false;

async function reloadJobs() {
  const data = await api("/api/jobs");
  state.jobs = data.jobs || [];
  render();
}

function askConfirm({ kicker = "Please confirm", title = "Are you sure?", copy = "", ok = "OK", option = "" } = {}) {
  if (confirmBusy) return confirmBusy;
  confirmBusy = new Promise((resolve) => {
    const modal = $("confirm-modal");
    $("confirm-kicker").textContent = kicker;
    $("confirm-title").textContent = title;
    $("confirm-copy").textContent = copy;
    $("confirm-copy").hidden = !copy;
    $("confirm-ok").textContent = ok;
    confirmExtra = false;
    $("confirm-option").hidden = !option;
    $("confirm-option-box").checked = false;
    $("confirm-option-label").textContent = option;
    modal.hidden = false;
    let done = false;
    const finish = (value) => {
      if (done) return;
      done = true;
      confirmExtra = Boolean(value && option && $("confirm-option-box").checked);
      modal.hidden = true;
      confirmBusy = null;
      resolve(value);
    };
    $("confirm-ok").onclick = () => finish(true);
    $("confirm-cancel").onclick = () => finish(false);
    modal.onclick = (e) => {
      if (e.target === modal) finish(false);
    };
    $("confirm-ok").focus();
  });
  return confirmBusy;
}

function toast(msg, kind = "ok") {
  if (!msg) return;
  const host = $("toasts");
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = msg;
  host.appendChild(el);
  setTimeout(() => {
    el.classList.add("out");
    setTimeout(() => el.remove(), 220);
  }, 4200);
}

const LIVE = ["queued", "starting", "scanning", "downloading", "stopping"];
const TERMINAL = ["done", "error", "stopped", "partial"];

function activeJobs() {
  return state.jobs.filter((j) => LIVE.includes(j.status));
}
function historyJobs() {
  return state.jobs.filter((j) => TERMINAL.includes(j.status));
}
function issueJobs() {
  return state.jobs.filter((j) => ["error", "stopped", "partial"].includes(j.status));
}
function matchesQuery(job, q) {
  if (!q) return true;
  return `${job.title || ""} ${job.url || ""} ${job.extractor || ""} ${job.author || ""} ${job.error || ""}`
    .toLowerCase()
    .includes(q);
}
function listedJobs() {
  const q = state.jobQuery.trim().toLowerCase();
  let list = state.view === "history" ? historyJobs() : activeJobs();
  if (state.view === "history") {
    if (state.historyStatus === "saved") list = list.filter((j) => j.status === "done" && j.kind !== "scan");
    else if (state.historyStatus === "issues") list = list.filter((j) => ["error", "stopped", "partial"].includes(j.status));
    else if (state.historyStatus === "scans") list = list.filter((j) => j.kind === "scan");
  }
  return list.filter((j) => matchesQuery(j, q));
}
function scanJobs() {
  return state.jobs.filter((j) => j.kind === "scan");
}
function currentScan() {
  return scanJobs().find((j) => j.id === state.scanId) || scanJobs().find((j) => (j.item_count || 0) > 0) || scanJobs()[0] || null;
}

// Selections are preview indices of one scan, so they must never carry over to
// another scan — the same numbers would pick different files there.
function selectScan(id) {
  if (state.scanId === id) return;
  state.scanId = id || "";
  state.selected = new Set();
  state.lastIndex = null;
  $("shot-grid").dataset.key = "";
}

function setView(view) {
  state.view = view;
  document.querySelectorAll(".tab").forEach((btn) => {
    btn.classList.toggle("is-on", btn.dataset.view === view);
  });
  const [title, lede] = COPY[view] || COPY.queue;
  $("view-title").textContent = title;
  $("view-lede").textContent = lede;
  $("composer").hidden = view !== "queue";
  $("list-tools").hidden = view !== "queue" && view !== "history";
  $("history-pills").hidden = view !== "history";
  $("jobs").hidden = view !== "queue" && view !== "history";
  $("preview-wrap").hidden = view !== "preview";
  $("library-wrap").hidden = view !== "library";
  $("sites-wrap").hidden = view !== "sites";
  $("settings-wrap").hidden = view !== "settings";
  $("layout-switch").hidden = view !== "queue" && view !== "history";
  $("stop-all").hidden = view !== "queue";
  $("retry-all").hidden = view !== "history";
  $("clear-history").hidden = view !== "history";
  if (view === "sites") loadSites($("site-search").value || "");
  if (view === "preview") pullScanItems(true);
  if (view === "library") {
    if (!$("library-grid").children.length) {
      $("library-grid").innerHTML = emptyHtml("Loading library…", "Reading the download folder.", "grid");
    }
    loadLibrary();
  }
  render();
}

function applyEngine(info) {
  state.engine = info || state.engine || {};
  const cur = state.engine.current || ((state.health && state.health.runtime && state.health.runtime.gallery_dl_version) || "");
  const chip = $("engine-chip");
  const badge = $("nav-update");
  if (chip) {
    chip.textContent = !cur ? "Missing" : state.engine.available ? "Update available" : state.engine.checked ? "Up to date" : "Installed";
    chip.className = `chip ${!cur ? "bad" : state.engine.available ? "live" : "ok"}`;
  }
  if (badge) {
    badge.hidden = !state.engine.available;
    badge.textContent = "1";
  }
  if ($("engine-copy")) {
    if (!cur) $("engine-copy").textContent = "gallery-dl was not found.";
    else if (state.engine.available) $("engine-copy").textContent = `Installed ${state.engine.current}. Version ${state.engine.latest} is available.`;
    else if (state.engine.checked) $("engine-copy").textContent = `Installed ${cur}. This is the latest version.`;
    else $("engine-copy").textContent = `Installed ${cur}. Check for updates to see if a newer version exists.`;
  }
}

function renderHealth() {
  const rt = (state.health && state.health.runtime) || {};
  applyEngine((state.health && state.health.engine) || state.engine);
  if ($("health")) {
    $("health").textContent = `${rt.ok ? "gallery-dl is ready" : "gallery-dl is missing"}${rt.ffmpeg ? ". ffmpeg is ready." : ". ffmpeg was not found (optional)."}`;
  }
  if (state.health && state.health.version) $("app-version").textContent = `v${state.health.version}`;
  if (state.health && state.health.credit) $("credit").textContent = state.health.credit;
  if ($("about-runtime")) {
    $("about-runtime").textContent = `gallery-dl ${rt.gallery_dl_version || "not found"} · ffmpeg ${rt.ffmpeg_version || "optional"}`;
  }
}

function renderDisk() {
  const d = state.disk || {};
  const el = $("disk");
  el.classList.toggle("warn", d.level === "warn");
  el.classList.toggle("critical", d.level === "critical");
  el.title = d.total_gb ? `${d.free_gb} GB free of ${d.total_gb} GB` : "Free disk space";
  $("disk-bar").style.width = `${Math.min(100, d.percent_used || 0)}%`;
  $("disk-free").textContent = d.free_gb != null ? `${d.free_gb} GB free` : "Checking disk…";
  const banner = $("banner");
  const note = (d.alerts && d.alerts[0]) || "";
  if (note && (d.level === "warn" || d.level === "critical")) {
    banner.hidden = false;
    banner.className = d.level === "critical" ? "banner critical" : "banner";
    banner.textContent = note;
  } else {
    banner.hidden = true;
  }
}

function renderCounts() {
  const q = activeJobs().length;
  const scans = scanJobs().filter((j) => (j.item_count || 0) > 0 || LIVE.includes(j.status)).length;
  const issues = issueJobs().length;
  const set = (id, n) => {
    const el = $(id);
    if (!el) return;
    el.hidden = !n;
    el.textContent = n > 99 ? "99+" : String(n);
  };
  set("nav-count-queue", q);
  set("nav-count-preview", scans);
  set("nav-count-history", issues);
  $("rec-pill").hidden = !q;
  $("rec-count").textContent = q === 1 ? "1 active" : `${q} active`;
  if (state.view === "queue") $("stop-all").hidden = !q;
  if (state.view === "history") {
    $("retry-all").hidden = !issues;
    $("clear-history").hidden = !historyJobs().length;
  }
}

function emptyHtml(title, copy, icon = "inbox", action = "") {
  return `<div class="empty"><div class="empty-mark">${ICO[icon] || ICO.inbox}</div><p>${escapeHtml(title)}</p><p class="muted">${escapeHtml(copy)}</p>${action}</div>`;
}

function statusChip(job) {
  const live = LIVE.includes(job.status);
  const ok = job.status === "done";
  const bad = job.status === "error";
  const warn = job.status === "partial" || job.status === "stopped";
  const cls = live ? "live" : ok ? "ok" : bad ? "bad" : warn ? "warn" : "";
  let label = job.status;
  if (job.kind === "scan" && job.status === "queued") label = "scan queued";
  if (job.status === "partial") label = "partial";
  if (state.disk && state.disk.blocked && job.status === "queued") label = "paused";
  return `<span class="chip ${cls}">${escapeHtml(label)}</span>`;
}

function jobSig(job) {
  const p = job.progress || {};
  return [job.id, job.status, p.saved, p.current, p.percent, p.total, job.item_count, job.status_line, job.error, job.title, job.size, job.size_speed, p.speed, p.skipped, !!state.logs[job.id], state.disk && state.disk.blocked ? 1 : 0].join("|");
}

function jobCard(job) {
  const p = job.progress || {};
  const saved = p.saved || job.files_count || 0;
  const total = p.total;
  let pct = 0;
  let indeterminate = false;
  if (job.status === "done") pct = 100;
  else if (p.percent != null && Number(p.percent) > 0) pct = Math.max(0, Math.min(100, p.percent));
  else if (total) pct = Math.max(0, Math.min(100, (saved / total) * 100));
  else if (["downloading", "scanning", "starting"].includes(job.status)) {
    indeterminate = true;
    pct = 42;
  }
  const phLabel = (job.extractor || hostOf(job.url) || "stills").toLowerCase();
  const thumb = job.thumbnail
    ? `<img class="thumb" src="${escapeHtml(thumbSrc(job.thumbnail))}" alt="" data-ph="${escapeHtml(phLabel)}" />`
    : `<div class="thumb ph" title="${escapeHtml(job.extractor || hostOf(job.url))}">${escapeHtml(phLabel)}</div>`;
  const count =
    job.kind === "scan"
      ? `${job.item_count || p.current || 0} found`
      : total
        ? `${p.current || saved} / ${total}`
        : `${saved} saved`;
  const stats = [];
  const size = job.size || 0;
  const speed = Number(p.speed) || Number(job.size_speed) || 0;
  if (job.kind !== "scan") {
    const live = ["queued", "starting", "downloading"].includes(job.status);
    if (live) {
      if (size) stats.push(fmtBytes(size));
      if (speed) stats.push(`${fmtBytes(speed)}/s`);
      if (total && speed && size && saved > 0 && total > saved) {
        const eta = ((total - saved) * (size / saved)) / speed;
        if (eta > 0 && eta < 86400) stats.push(`ETA ${fmtElapsed(eta)}`);
      }
    } else if (job.status !== "error" || saved) {
      stats.push(`${saved || job.files_count || 0} file${(saved || job.files_count || 0) === 1 ? "" : "s"}`);
      if (size) stats.push(fmtBytes(size));
      if (p.skipped) stats.push(`${p.skipped} skipped`);
    }
  }
  const negs = (job.preview_thumbs || [])
    .slice(0, 6)
    .map((t) => `<img src="${escapeHtml(thumbSrc(t))}" alt="" />`)
    .join("");
  const actions = [];
  if (["queued", "starting", "scanning", "downloading"].includes(job.status)) {
    actions.push(`<button class="icon-btn stop" data-act="stop" data-id="${job.id}">Stop</button>`);
  }
  if (["error", "stopped", "partial"].includes(job.status)) {
    actions.push(`<button class="icon-btn" data-act="retry" data-id="${job.id}">Retry</button>`);
  }
  if (job.output_path || job.files_count) {
    actions.push(`<button class="icon-btn" data-act="open" data-id="${job.id}">Open</button>`);
  }
  if (job.kind === "scan" && ((job.item_count || 0) > 0 || job.status === "done" || job.status === "partial")) {
    actions.push(`<button class="icon-btn" data-act="preview" data-id="${job.id}">Preview</button>`);
  }
  actions.push(`<button class="icon-btn" data-act="copy" data-id="${job.id}">Copy link</button>`);
  actions.push(`<button class="icon-btn" data-act="log" data-id="${job.id}">Log</button>`);
  if (job.status === "queued" || TERMINAL.includes(job.status)) {
    actions.push(`<button class="icon-btn remove" data-act="remove" data-id="${job.id}">Remove</button>`);
  }
  const log = state.logs[job.id];
  const title = job.title && job.title !== job.url ? job.title : hostOf(job.url);
  return `<article class="job" data-status="${escapeHtml(job.status)}" data-id="${job.id}" data-sig="${escapeHtml(jobSig(job))}">
    <div class="thumb-wrap">${thumb}</div>
    <div class="meta">
      <h3 title="${escapeHtml(job.title || job.url)}">${escapeHtml(title)}</h3>
      <div class="sub">${statusChip(job)}<span>${escapeHtml(job.extractor || hostOf(job.url))}</span><span>${escapeHtml(count)}</span><span class="elapsed">${fmtElapsed(p.elapsed)}</span></div>
      ${stats.length ? `<div class="stats">${escapeHtml(stats.join("  ·  "))}</div>` : ""}
      <div class="job-url" data-act="copy" data-id="${job.id}" title="Copy link">${escapeHtml(job.url)}</div>
      ${pausedNote(job)}
      ${job.status_line ? `<div class="status-line">${escapeHtml(job.status_line)}</div>` : ""}
      ${negs && job.kind === "scan" ? `<div class="negs">${negs}</div>` : ""}
      <div class="bar${indeterminate ? " indeterminate" : ""}"><i style="width:${pct}%"></i></div>
      ${job.error ? `<div class="job-error">${escapeHtml(job.error)}</div>` : ""}
    </div>
    <div class="actions">${actions.join("")}</div>
    ${log ? `<pre class="job-log">${escapeHtml(log.join("\n"))}</pre>` : ""}
  </article>`;
}

function pausedNote(job) {
  if (!(state.disk && state.disk.blocked && job.status === "queued")) return "";
  return `<div class="status-line">Waiting for free disk space.</div>`;
}

function scanItemList(scan) {
  if (!scan) return [];
  return state.scanItems[scan.id] || [];
}

function visibleItems(scan) {
  const items = scanItemList(scan);
  const q = ($("preview-search").value || "").trim().toLowerCase();
  return items.filter((it) => {
    if (state.kind !== "all" && it.kind !== state.kind) return false;
    if (q && !`${it.filename} ${it.title} ${it.author}`.toLowerCase().includes(q)) return false;
    return true;
  });
}

function shotHtml(it) {
  const on = state.selected.has(it.index);
  const src = it.thumb ? thumbSrc(it.thumb) : "";
  const dim = it.child ? "album" : it.width && it.height ? `${it.width}×${it.height}` : it.extension || it.kind;
  return `<button type="button" class="shot ${on ? "is-on" : ""}" data-index="${it.index}" title="${escapeHtml(it.filename)}">
    ${src ? `<img src="${escapeHtml(src)}" alt="" loading="lazy" decoding="async" />` : `<div class="ph">${escapeHtml((it.extension || it.kind || "").toUpperCase())}</div>`}
    <span class="kind">${escapeHtml(dim)}</span>
    <span class="tick"></span>
    <span class="cap">${escapeHtml(it.filename)}${it.extension ? "." + escapeHtml(it.extension) : ""}</span>
  </button>`;
}

function renderPreview() {
  // Keep the scan the user just opened, even when it failed or found nothing.
  // Older empty scans stay in History so this menu does not grow forever.
  const scans = scanJobs().filter((j) => j.id === state.scanId || (j.item_count || 0) > 0 || LIVE.includes(j.status));
  const sel = $("preview-scan");
  const currentIds = scans.map((s) => s.id);
  const optionLabel = (s) => {
    const n = s.item_count || 0;
    const mark = s.status === "error" ? "failed" : s.status === "scanning" ? "scanning" : n ? String(n) : s.status;
    return `${s.title || hostOf(s.url)} (${mark})`;
  };
  if (sel.dataset.ids === currentIds.join(",")) {
    // Same scans: refresh labels in place so counts and titles stay current.
    scans.forEach((s, i) => {
      const opt = sel.options[i];
      if (opt && opt.textContent !== optionLabel(s)) opt.textContent = optionLabel(s);
    });
  } else {
    sel.dataset.ids = currentIds.join(",");
    sel.innerHTML = scans.length
      ? scans.map((s) => `<option value="${s.id}">${escapeHtml(optionLabel(s))}</option>`).join("")
      : `<option value="">No scans yet</option>`;
  }
  if (state.scanId && currentIds.includes(state.scanId)) sel.value = state.scanId;
  else if (scans[0]) {
    selectScan(scans[0].id);
    sel.value = scans[0].id;
  }
  const scan = currentScan();
  const itemsAll = scanItemList(scan);
  $("preview-title").textContent = scan ? scan.title || hostOf(scan.url) : "No preview yet";
  const live = scan && scan.status === "scanning" ? " · scanning…" : "";
  const sub = $("preview-sub");
  sub.classList.toggle("bad", !!(scan && scan.status === "error"));
  sub.textContent = !scan
    ? "Scan an album to list its pictures here."
    : scan.status === "error"
      ? scan.error || "This scan failed."
      : `${scan.item_count || itemsAll.length} files${live} · ${scan.extractor || hostOf(scan.url)} · range ${scan.range || "all"}`;
  const busy = scan && LIVE.includes(scan.status);
  $("load-more").disabled = !scan || busy;
  $("retry-scan").hidden = !(scan && ["error", "stopped", "partial"].includes(scan.status));
  $("download-all").disabled = !itemsAll.length;
  $("download-all").textContent = itemsAll.length ? `Download all (${itemsAll.length})` : "Download all";
  const grid = $("shot-grid");
  if (!scan || !(scan.item_count || itemsAll.length)) {
    grid.innerHTML = emptyHtml(
      scan && scan.status === "error" ? "Scan failed" : "Nothing to preview",
      scan && scan.status === "error" ? (scan.error || "Retry the scan, or check cookies and the URL.") : "Paste a URL on Queue, then click Scan. Pictures appear here as they are found.",
      "search"
    );
    $("sel-count").textContent = "0 selected";
    $("download-selected").textContent = "Download selected";
    $("download-selected").disabled = true;
    $("select-all").checked = false;
    grid.dataset.key = "";
    return;
  }
  const items = visibleItems(scan);
  const key = `${scan.id}:${state.kind}:${($("preview-search").value || "").trim()}`;
  if (grid.dataset.key !== key) {
    grid.dataset.key = key;
    grid.innerHTML = items.map(shotHtml).join("");
  } else {
    const have = new Set([...grid.querySelectorAll("[data-index]")].map((el) => el.dataset.index));
    const add = items.filter((it) => !have.has(String(it.index)));
    if (add.length) grid.insertAdjacentHTML("beforeend", add.map(shotHtml).join(""));
    grid.querySelectorAll(".shot").forEach((el) => {
      el.classList.toggle("is-on", state.selected.has(Number(el.dataset.index)));
    });
  }
  const visSel = items.filter((it) => state.selected.has(it.index)).length;
  const n = state.selected.size;
  $("sel-count").textContent = `${n} selected · ${items.length} shown`;
  $("select-all").checked = items.length > 0 && visSel === items.length;
  $("download-selected").textContent = n ? `Download ${n}` : "Download selected";
  $("download-selected").disabled = !n;
}

function renderSites() {
  const items = state.sites || [];
  const meta = $("sites-meta");
  if (meta) {
    meta.textContent = !state.sitesTotal
      ? ""
      : items.length < state.sitesTotal
        ? `Showing ${items.length} of ${state.sitesTotal} sites. Search to narrow the list.`
        : `${state.sitesTotal} sites`;
  }
  if (!items.length) {
    $("sites-grid").innerHTML = emptyHtml("No sites loaded yet", "gallery-dl is still listing supported sites, or it is not installed.", "globe");
    return;
  }
  $("sites-grid").innerHTML = items
    .map(
      (s) => `<button type="button" class="site-card" data-example="${escapeHtml(s.example || "")}" data-label="${escapeHtml(s.label)}">
        <strong>${escapeHtml(s.label)}</strong>
        <span>${escapeHtml(s.example || s.category)}</span>
      </button>`
    )
    .join("");
}

function renderLibrary() {
  const grid = $("library-grid");
  const files = state.libraryFiles || [];
  const total = state.libraryTotal || files.length;
  $("library-count").textContent = total ? `${files.length} of ${total}` : "";
  if (!files.length) {
    grid.innerHTML = emptyHtml(
      "No saved files yet",
      "Grab an album, or change the download folder in Settings. Pictures you save show up here.",
      "grid"
    );
    return;
  }
  grid.innerHTML = files
    .map((file, i) => {
      const src = file.kind === "video" ? "" : thumbSrc(file.path);
      const dim = file.kind === "video" ? "video" : fmtBytes(file.size);
      return `<button type="button" class="shot" data-lib="${i}" title="${escapeHtml(file.rel)}">
        ${src ? `<img src="${escapeHtml(src)}" alt="" loading="lazy" decoding="async" />` : `<div class="ph">${escapeHtml((file.ext || file.kind || "").toUpperCase())}</div>`}
        <span class="kind">${escapeHtml(dim)}</span>
        <span class="cap">${escapeHtml(file.name)}</span>
        <span class="lib-open" data-act="lib-open" data-path="${escapeHtml(file.path)}">Show</span>
      </button>`;
    })
    .join("");
}

function renderJobs() {
  const list = listedJobs();
  const el = $("jobs");
  const count = $("list-count");
  if (count) {
    const noun = state.view === "history" ? "finished" : "in the queue";
    count.textContent = list.length ? `${list.length} ${noun}` : "";
  }
  el.classList.toggle("layout-thumbs", state.layout === "thumbs");
  el.classList.toggle("layout-list", state.layout === "list");
  if (!list.length) {
    const filtered = state.jobQuery.trim() || (state.view === "history" && state.historyStatus !== "all");
    el.innerHTML = state.view === "history"
      ? emptyHtml(filtered ? "Nothing matches" : "No history yet", filtered ? "Try another filter." : "Finished albums will collect here.", "clock")
      : emptyHtml(filtered ? "Nothing matches" : "Nothing in the queue", filtered ? "Try another filter." : "Paste URLs above, then Scan or Grab.", "download");
    return;
  }
  const stale = el.querySelector(".empty");
  if (stale) stale.remove();
  const keep = new Set(list.map((j) => j.id));
  for (const node of [...el.querySelectorAll(".job")]) {
    if (!keep.has(node.dataset.id)) node.remove();
  }
  const ordered = [];
  for (const job of list) {
    const sig = jobSig(job);
    const prev = el.querySelector(`.job[data-id="${job.id}"]`);
    if (prev && prev.dataset.sig === sig) {
      // Elapsed is left out of the signature (rebuilding the card every tick
      // flickers), so tick the timer text in place.
      const timer = prev.querySelector(".elapsed");
      const text = fmtElapsed((job.progress || {}).elapsed);
      if (timer && timer.textContent !== text) timer.textContent = text;
      ordered.push(prev);
      continue;
    }
    const wrap = document.createElement("div");
    wrap.innerHTML = jobCard(job);
    const next = wrap.firstElementChild;
    if (prev) {
      reuseLoadedImages(prev, next);
      prev.replaceWith(next);
    } else {
      el.appendChild(next);
    }
    ordered.push(next);
  }
  // Newest job is first in the list. appendChild moves an existing node,
  // so a download that starts later does not stay stuck at the bottom.
  for (const node of ordered) el.appendChild(node);
}

// Carry already-decoded <img> elements from the old card into the rebuilt one
// (matched by src) so thumbnails don't blank and reload on every progress
// update — the main cause of the queue flicker during active jobs.
function reuseLoadedImages(prev, next) {
  const loaded = new Map();
  prev.querySelectorAll("img").forEach((im) => {
    if (im.complete && im.naturalWidth > 0) loaded.set(im.src, im);
  });
  if (!loaded.size) return;
  next.querySelectorAll("img").forEach((im) => {
    const hit = loaded.get(im.src);
    if (hit) {
      loaded.delete(im.src);
      im.replaceWith(hit);
    }
  });
}

let renderTimer = 0;
function render() {
  renderCounts();
  renderDisk();
  if (state.view === "queue" || state.view === "history") renderJobs();
  if (state.view === "preview") renderPreview();
  // Library and Sites paint themselves when their data changes. Rebuilding
  // them on every job tick reloaded every thumbnail and jumped the scroll.
}
function scheduleRender() {
  if (renderTimer) return;
  renderTimer = requestAnimationFrame(() => {
    renderTimer = 0;
    render();
  });
}

function fillSettings(cfg) {
  state.settings = cfg;
  $("set-output").value = cfg.output_dir || "";
  $("set-cookies-file").value = cfg.cookies_file || "";
  $("set-cookies-browser").value = cfg.cookies_browser || "";
  $("set-dir-preset").value = cfg.directory_preset || "site_author";
  $("set-name-preset").value = cfg.filename_preset || "original";
  $("set-dir-custom").value = cfg.directory_custom || "";
  $("set-name-custom").value = cfg.filename_custom || "";
  $("set-archive").checked = !!cfg.use_archive;
  $("set-skip").checked = !!cfg.skip_existing;
  $("set-meta").checked = !!cfg.write_metadata;
  $("set-tags").checked = !!cfg.write_tags;
  $("set-filter").value = cfg.filter_mode || "all";
  $("set-min-width").value = cfg.min_width || 0;
  $("set-active").value = cfg.max_active || 3;
  $("set-scan-limit").value = cfg.scan_limit || 240;
  $("set-sleep").value = cfg.sleep_request || 0;
  $("set-rate").value = cfg.rate || "";
  $("set-retries").value = cfg.retries ?? 4;
  $("set-ugoira").value = cfg.convert_ugoira || "mp4";
  $("set-warn").value = cfg.warn_free_gb || 0;
  $("set-min-free").value = cfg.min_free_gb || 0;
  $("set-proxy").value = cfg.proxy || "";
  $("terms-modal").hidden = !!cfg.accepted_terms;
  syncCustomFields();
}

function syncCustomFields() {
  const custom = $("set-dir-preset").value === "custom" || $("set-name-preset").value === "custom";
  $("custom-fields").hidden = !custom;
}

function countPasted(raw) {
  const parts = String(raw || "")
    .split(/\s+/)
    .flatMap((token) => token.split(/[,;]+(?=https?:\/\/)/i))
    .map((part) => part.trim().replace(/[,;]+$/, ""))
    .filter(Boolean);
  const urls = [];
  let invalid = 0;
  const seen = new Set();
  for (const part of parts) {
    if (!/^https?:\/\/\S+$/i.test(part)) {
      invalid += 1;
      continue;
    }
    if (seen.has(part)) continue;
    seen.add(part);
    urls.push(part);
  }
  return { urls, invalid };
}

function refreshUrlCount() {
  const el = $("url-count");
  const { urls, invalid } = countPasted($("url").value);
  el.classList.toggle("warn", urls.length > 80);
  if (!urls.length && !invalid) {
    el.textContent = "Paste one or more links. Scan lists pictures so you can choose. Grab downloads the whole album.";
    return;
  }
  const bits = [`${urls.length} URL${urls.length === 1 ? "" : "s"}`];
  if (invalid) bits.push(`${invalid} ignored`);
  if (urls.length > 80) bits.push(`${urls.length - 80} stay in the box after you send`);
  el.textContent = bits.join(" · ");
}

async function loadSites(q = "") {
  try {
    const data = await api(`/api/sites?q=${encodeURIComponent(q)}`);
    state.sites = data.sites || [];
    state.sitesTotal = data.count || state.sites.length;
    renderSites();
  } catch (err) {
    toast(err.message, "critical");
  }
}

const pulling = new Set();
async function pullScanItems(force = false) {
  const scan = currentScan();
  if (!scan) return;
  let existing = state.scanItems[scan.id] || [];
  if (existing.length > (scan.item_count || 0)) {
    // The scan was retried and is listing from scratch.
    existing = state.scanItems[scan.id] = [];
    if (state.scanId === scan.id) {
      state.selected = new Set();
      state.lastIndex = null;
    }
    $("shot-grid").dataset.key = "";
    force = true;
  }
  if (!force && existing.length >= (scan.item_count || 0)) return;
  if (pulling.has(scan.id)) return;
  pulling.add(scan.id);
  try {
    const after = force ? -1 : existing.length ? existing[existing.length - 1].index : -1;
    const data = await api(`/api/jobs/${scan.id}/items?after=${after}`);
    const fresh = data.items || [];
    state.scanItems[scan.id] = force ? fresh : existing.concat(fresh);
    if (state.view === "preview") renderPreview();
  } catch {
    /* ignore */
  } finally {
    pulling.delete(scan.id);
  }
}

let libraryTimer = 0;
async function loadLibrary() {
  const q = ($("library-search").value || "").trim();
  try {
    const data = await api(`/api/library?limit=240&kind=${encodeURIComponent(state.libraryKind)}&q=${encodeURIComponent(q)}`);
    state.libraryFiles = data.files || [];
    state.libraryTotal = data.total || 0;
    if (state.view === "library") renderLibrary();
    renderCounts();
  } catch (err) {
    toast(err.message, "critical");
  }
}

async function sendUrls(kind) {
  if (state.sending) return;
  const url = $("url").value.trim();
  if (!url) {
    toast("Paste at least one URL.", "warn");
    $("url").focus();
    return;
  }
  state.sending = true;
  $("grab-btn").disabled = true;
  $("scan-btn").disabled = true;
  const range = $("range").value.trim();
  const path = kind === "scan" ? "/api/scan" : "/api/grab";
  try {
    const created = await api(path, { method: "POST", body: JSON.stringify({ url, range }) });
    const list = created.jobs || [created];
    const fresh = list.filter((j) => !j.already);
    const rest = created.rest || [];
    const notes = [];
    if (list.length > fresh.length) notes.push(`${list.length - fresh.length} already in the queue`);
    if (created.invalid) notes.push(`${created.invalid} not a URL`);
    if (rest.length) notes.push(`${rest.length} over the batch limit left in the box — send again`);
    const extra = notes.length ? ` (${notes.join(", ")})` : "";
    // Leave anything that wasn't queued in the box so it isn't lost.
    $("url").value = rest.join("\n");
    refreshUrlCount();
    if (!fresh.length) {
      toast(`Nothing new queued${extra}.`, "warn");
      return;
    }
    const n = fresh.length;
    if (kind === "scan") {
      selectScan(fresh[0].id);
      toast((n > 1 ? `${n} scans started. Pictures will appear in Preview.` : "Scan started. Pictures will appear in Preview.") + extra, notes.length ? "warn" : "ok");
      setView("preview");
    } else {
      toast((n > 1 ? `${n} downloads started.` : "Download started.") + extra, notes.length ? "warn" : "ok");
      setView("queue");
    }
  } catch (err) {
    toast(err.message, "critical");
  } finally {
    state.sending = false;
    $("grab-btn").disabled = false;
    $("scan-btn").disabled = false;
  }
}

function wire() {
  document.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => setView(btn.dataset.view));
  });
  document.querySelectorAll(".layout-btn").forEach((btn) => {
    btn.classList.toggle("is-active", btn.dataset.layout === state.layout);
    btn.addEventListener("click", () => {
      state.layout = btn.dataset.layout;
      localStorage.setItem("stillheart-layout", state.layout);
      document.querySelectorAll(".layout-btn").forEach((b) => b.classList.toggle("is-active", b.dataset.layout === state.layout));
      render();
    });
  });
  $("grab-btn").addEventListener("click", () => sendUrls("download"));
  $("scan-btn").addEventListener("click", () => sendUrls("scan"));
  $("clear-urls").addEventListener("click", () => {
    $("url").value = "";
    $("range").value = "";
    $("url").focus();
    refreshUrlCount();
    toast("URL box cleared.");
  });
  $("url").addEventListener("input", refreshUrlCount);
  $("url").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && e.shiftKey) {
      e.preventDefault();
      sendUrls("scan");
    } else if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      sendUrls("download");
    }
  });
  $("import-urls").addEventListener("click", () => $("url-file").click());
  $("url-file").addEventListener("change", async (e) => {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    const text = await file.text();
    $("url").value = [$("url").value, text].filter(Boolean).join("\n");
    e.target.value = "";
    refreshUrlCount();
    toast("URL list imported.");
  });
  $("open-folder").addEventListener("click", async () => {
    try {
      await api("/api/open", { method: "POST", body: JSON.stringify({ path: "" }) });
      toast("Opened the download folder.");
    } catch (err) {
      toast(err.message, "critical");
    }
  });
  $("stop-all").addEventListener("click", async () => {
    const n = activeJobs().length;
    if (!n) return;
    if (!(await askConfirm({ title: `Stop ${n} active job${n === 1 ? "" : "s"}?`, copy: "Files already saved stay on disk.", ok: "Stop all" }))) return;
    await api("/api/jobs/stop-all", { method: "POST" });
    toast("Stopping all jobs.", "warn");
  });
  $("retry-all").addEventListener("click", async () => {
    let n = 0;
    for (const job of issueJobs()) {
      try {
        await api(`/api/jobs/${job.id}/retry`, { method: "POST" });
        n += 1;
      } catch {}
    }
    toast(n ? `Retrying ${n} job${n === 1 ? "" : "s"}.` : "Nothing to retry.", n ? "ok" : "warn");
  });
  $("clear-history").addEventListener("click", async () => {
    const ok = await askConfirm({
      title: "Clear history?",
      copy: "Your files stay on disk unless you tick the box.",
      ok: "Clear",
      option: "Also move the files to the Recycle Bin",
    });
    if (!ok) return;
    try {
      const result = await api(`/api/history/clear${confirmExtra ? "?files=true" : ""}`, { method: "POST" });
      await reloadJobs();
      toast(result.recycled ? `History cleared. ${result.recycled} moved to the Recycle Bin.` : "History cleared.");
    } catch (err) {
      toast(err.message, "critical");
    }
  });
  $("jobs").addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-act]");
    if (!btn) return;
    const id = btn.dataset.id;
    const act = btn.dataset.act;
    try {
      if (act === "stop") {
        await api(`/api/jobs/${id}/stop`, { method: "POST" });
        toast("Stopping job.", "warn");
      }
      if (act === "retry") {
        await api(`/api/jobs/${id}/retry`, { method: "POST" });
        toast("Retry started.");
      }
      if (act === "open") {
        const job = state.jobs.find((j) => j.id === id);
        await api("/api/open", { method: "POST", body: JSON.stringify({ path: (job && job.output_path) || "" }) });
      }
      if (act === "preview") {
        selectScan(id);
        setView("preview");
      }
      if (act === "log") {
        if (state.logs[id]) {
          delete state.logs[id];
        } else {
          const data = await api(`/api/jobs/${id}/log`);
          state.logs[id] = data.log || [];
        }
        render();
      }
      if (act === "copy") {
        const job = state.jobs.find((j) => j.id === id);
        if (job && job.url) {
          await copyText(job.url);
          toast("Link copied.");
        }
      }
      if (act === "remove") {
        const job = state.jobs.find((j) => j.id === id);
        const ok = await askConfirm({
          title: "Remove this from the list?",
          ok: "Remove",
          option: job && job.files_count ? "Also move its files to the Recycle Bin" : "",
        });
        if (!ok) return;
        const result = await api(`/api/jobs/${id}${confirmExtra ? "?files=true" : ""}`, { method: "DELETE" });
        delete state.logs[id];
        delete state.scanItems[id];
        if (state.scanId === id) {
          state.scanId = "";
          state.selected = new Set();
        }
        await reloadJobs();
        toast(result.recycled ? `${result.recycled} moved to the Recycle Bin.` : "Removed from the list. Files on disk stay.");
      }
    } catch (err) {
      toast(err.message, "critical");
    }
  });
  $("preview-scan").addEventListener("change", () => {
    selectScan($("preview-scan").value);
    pullScanItems(true);
    renderPreview();
  });
  document.querySelectorAll("#kind-pills .pill").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.kind = btn.dataset.kind;
      document.querySelectorAll("#kind-pills .pill").forEach((b) => b.classList.toggle("is-on", b === btn));
      $("shot-grid").dataset.key = "";
      renderPreview();
    });
  });
  $("preview-search").addEventListener("input", () => {
    $("shot-grid").dataset.key = "";
    renderPreview();
  });
  $("job-search").addEventListener("input", () => {
    state.jobQuery = $("job-search").value || "";
    renderJobs();
  });
  document.querySelectorAll("#history-pills .pill").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.historyStatus = btn.dataset.status;
      document.querySelectorAll("#history-pills .pill").forEach((b) => b.classList.toggle("is-on", b === btn));
      renderJobs();
    });
  });
  $("select-none").addEventListener("click", () => {
    state.selected = new Set();
    state.lastIndex = null;
    renderPreview();
  });
  $("select-all").addEventListener("change", () => {
    const items = visibleItems(currentScan());
    if ($("select-all").checked) items.forEach((it) => state.selected.add(it.index));
    else items.forEach((it) => state.selected.delete(it.index));
    renderPreview();
  });
  $("invert-sel").addEventListener("click", () => {
    visibleItems(currentScan()).forEach((it) => {
      if (state.selected.has(it.index)) state.selected.delete(it.index);
      else state.selected.add(it.index);
    });
    renderPreview();
  });
  $("load-more").addEventListener("click", async () => {
    const scan = currentScan();
    if (!scan) return;
    try {
      await api(`/api/scans/${scan.id}/more`, { method: "POST" });
      toast("Loading more pictures…");
    } catch (err) {
      toast(err.message, "critical");
    }
  });
  $("clear-preview").addEventListener("click", async () => {
    const scan = currentScan();
    if (!scan) return;
    if (["queued", "starting", "scanning", "stopping"].includes(scan.status)) {
      try {
        await api(`/api/jobs/${scan.id}/stop`, { method: "POST" });
        // The server won't delete a scan until gallery-dl has actually exited.
        for (let i = 0; i < 40; i += 1) {
          const job = await api(`/api/jobs/${scan.id}`);
          if (["done", "error", "stopped", "partial"].includes(job.status)) break;
          await new Promise((r) => setTimeout(r, 200));
        }
      } catch {}
    }
    try {
      await api(`/api/jobs/${scan.id}`, { method: "DELETE" });
    } catch (err) {
      toast(`Couldn't clear this scan: ${err.message}`, "critical");
      return;
    }
    delete state.scanItems[scan.id];
    state.scanId = "";
    state.selected = new Set();
    state.lastIndex = null;
    $("shot-grid").dataset.key = "";
    toast("Preview cleared.");
    renderPreview();
  });
  async function downloadIndices(indices) {
    const scan = currentScan();
    if (!scan) return;
    const known = new Set(scanItemList(scan).map((it) => it.index));
    indices = indices.filter((i) => known.has(i));
    if (!indices.length) {
      toast("Select at least one picture.", "warn");
      return;
    }
    $("download-selected").disabled = true;
    $("download-all").disabled = true;
    try {
      const data = await api(`/api/scans/${scan.id}/download`, { method: "POST", body: JSON.stringify({ indices }) });
      const n = data && data.jobs ? data.count : 1;
      toast(n > 1 ? `Queued ${n} downloads.` : `Downloading ${indices.length} file${indices.length === 1 ? "" : "s"}.`);
      setView("queue");
    } catch (err) {
      toast(err.message, "critical");
    } finally {
      $("download-selected").disabled = !state.selected.size;
      $("download-all").disabled = !scanItemList(currentScan()).length;
    }
  }
  $("download-selected").addEventListener("click", () => downloadIndices([...state.selected]));
  $("download-all").addEventListener("click", () => downloadIndices(scanItemList(currentScan()).map((it) => it.index)));
  $("retry-scan").addEventListener("click", async () => {
    const scan = currentScan();
    if (!scan) return;
    try {
      await api(`/api/jobs/${scan.id}/retry`, { method: "POST" });
      state.scanItems[scan.id] = [];
      state.selected = new Set();
      $("shot-grid").dataset.key = "";
      toast("Scan started again.");
    } catch (err) {
      toast(err.message, "critical");
    }
  });
  $("shot-grid").addEventListener("click", (e) => {
    const shot = e.target.closest(".shot");
    if (!shot) return;
    const index = Number(shot.dataset.index);
    if (e.shiftKey && state.lastIndex != null) {
      const items = visibleItems(currentScan());
      const ia = items.findIndex((it) => it.index === state.lastIndex);
      const ib = items.findIndex((it) => it.index === index);
      if (ia >= 0 && ib >= 0) {
        const lo = Math.min(ia, ib);
        const hi = Math.max(ia, ib);
        for (let i = lo; i <= hi; i += 1) state.selected.add(items[i].index);
      }
    } else if (state.selected.has(index)) state.selected.delete(index);
    else state.selected.add(index);
    state.lastIndex = index;
    renderPreview();
  });
  $("shot-grid").addEventListener("dblclick", (e) => {
    const shot = e.target.closest(".shot");
    if (!shot) return;
    const items = visibleItems(currentScan()).filter((it) => !it.child && (it.thumb || it.url));
    const index = items.findIndex((it) => it.index === Number(shot.dataset.index));
    const item = items[index];
    if (!item) {
      toast("Nested albums open after you download them.", "warn");
      return;
    }
    openLightbox(
      items.map((it) => {
        const full = ["image", "gif"].includes(it.kind) && it.url;
        const src = full || it.thumb || it.url;
        return {
          src: String(src).startsWith("/api/") ? src : thumbSrc(src) + (full ? "&full=1" : ""),
          cap: `${it.filename}${it.extension ? "." + it.extension : ""}`,
          video: false,
        };
      }),
      index
    );
  });
  $("lightbox-close").addEventListener("click", closeLightbox);
  $("lightbox-prev").addEventListener("click", () => showLightboxAt(state.lightboxAt - 1));
  $("lightbox-next").addEventListener("click", () => showLightboxAt(state.lightboxAt + 1));
  $("lightbox").addEventListener("click", (e) => {
    if (e.target.id === "lightbox") closeLightbox();
  });
  let siteSearchTimer = 0;
  $("site-search").addEventListener("input", () => {
    clearTimeout(siteSearchTimer);
    siteSearchTimer = setTimeout(() => loadSites($("site-search").value), 250);
  });
  $("sites-grid").addEventListener("click", (e) => {
    const card = e.target.closest(".site-card");
    if (!card) return;
    const example = card.dataset.example || "";
    if (example) {
      const cur = $("url").value.trim();
      if (!cur) $("url").value = example;
      else if (!cur.split(/\s+/).includes(example)) $("url").value = `${cur}\n${example}`;
      refreshUrlCount();
    }
    toast(example ? "Example URL added. Edit it, then Scan or Grab." : "This site has no example URL.", example ? "ok" : "warn");
    setView("queue");
    $("url").focus();
  });
  $("settings-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = {
      output_dir: $("set-output").value,
      cookies_file: $("set-cookies-file").value,
      cookies_browser: $("set-cookies-browser").value,
      directory_preset: $("set-dir-preset").value,
      filename_preset: $("set-name-preset").value,
      directory_custom: $("set-dir-custom").value,
      filename_custom: $("set-name-custom").value,
      use_archive: $("set-archive").checked,
      skip_existing: $("set-skip").checked,
      write_metadata: $("set-meta").checked,
      write_tags: $("set-tags").checked,
      filter_mode: $("set-filter").value,
      min_width: Number($("set-min-width").value || 0),
      max_active: Number($("set-active").value || 3),
      scan_limit: Number($("set-scan-limit").value || 240),
      sleep_request: Number($("set-sleep").value || 0),
      rate: $("set-rate").value,
      retries: $("set-retries").value === "" ? 4 : Number($("set-retries").value),
      convert_ugoira: $("set-ugoira").value,
      warn_free_gb: Number($("set-warn").value || 0),
      min_free_gb: Number($("set-min-free").value || 0),
      proxy: $("set-proxy").value,
    };
    try {
      fillSettings(await api("/api/settings", { method: "PUT", body: JSON.stringify(body) }));
      $("save-msg").hidden = false;
      toast("Settings saved.");
      setTimeout(() => {
        $("save-msg").hidden = true;
      }, 1800);
    } catch (err) {
      toast(err.message, "critical");
    }
  });
  async function refreshEngine() {
    try {
      const health = await api("/api/health");
      state.health = health;
      applyEngine(health.engine);
      renderHealth();
    } catch {}
  }
  async function checkEngine() {
    $("engine-msg").textContent = "Checking…";
    try {
      applyEngine(await api("/api/engine/check", { method: "POST" }));
      $("engine-msg").textContent = state.engine.available ? "Update available." : "Up to date.";
      toast(state.engine.available ? `Version ${state.engine.latest} is available.` : "gallery-dl is up to date.");
    } catch (err) {
      $("engine-msg").textContent = err.message;
      toast(err.message, "critical");
    }
  }
  async function updateEngine() {
    $("engine-msg").textContent = "Updating…";
    $("update-engine").disabled = true;
    try {
      const data = await api("/api/engine/update", { method: "POST" });
      applyEngine(data.engine || {});
      $("engine-msg").textContent = "Updated.";
      toast("gallery-dl updated.");
      await refreshEngine();
    } catch (err) {
      $("engine-msg").textContent = err.message;
      toast(err.message, "critical");
    } finally {
      $("update-engine").disabled = false;
    }
  }
  $("update-engine").addEventListener("click", updateEngine);
  $("check-engine").addEventListener("click", checkEngine);
  $("accept-terms").addEventListener("click", async () => {
    fillSettings(await api("/api/settings", { method: "PUT", body: JSON.stringify({ accepted_terms: true }) }));
  });
  $("set-dir-preset").addEventListener("change", syncCustomFields);
  $("set-name-preset").addEventListener("change", syncCustomFields);
  async function browse(kind, input) {
    input.disabled = true;
    try {
      const data = await api("/api/pick", { method: "POST", body: JSON.stringify({ kind, initial: input.value || "" }) });
      if (data.path) input.value = data.path;
    } catch (err) {
      toast(err.message, "critical");
    } finally {
      input.disabled = false;
    }
  }
  $("pick-output").addEventListener("click", () => browse("folder", $("set-output")));
  $("pick-cookies").addEventListener("click", () => browse("file", $("set-cookies-file")));
  $("library-search").addEventListener("input", () => {
    clearTimeout(libraryTimer);
    libraryTimer = setTimeout(loadLibrary, 200);
  });
  $("library-refresh").addEventListener("click", loadLibrary);
  document.querySelectorAll("#library-pills .pill").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.libraryKind = btn.dataset.kind;
      document.querySelectorAll("#library-pills .pill").forEach((b) => b.classList.toggle("is-on", b === btn));
      loadLibrary();
    });
  });
  $("library-grid").addEventListener("click", async (e) => {
    const opener = e.target.closest("[data-act='lib-open']");
    if (opener) {
      e.preventDefault();
      e.stopPropagation();
      try {
        await api("/api/open", { method: "POST", body: JSON.stringify({ path: opener.dataset.path || "" }) });
      } catch (err) {
        toast(err.message, "critical");
      }
      return;
    }
    const shot = e.target.closest(".shot");
    if (!shot || shot.dataset.lib == null) return;
    const files = state.libraryFiles || [];
    openLightbox(
      files.map((file) => ({
        src: `/api/library/file?p=${encodeURIComponent(file.path)}`,
        cap: `${file.rel} · ${fmtBytes(file.size)}`,
        video: file.kind === "video",
      })),
      Number(shot.dataset.lib)
    );
  });
  let dragDepth = 0;
  document.addEventListener("dragenter", (e) => {
    e.preventDefault();
    dragDepth += 1;
    $("drop").hidden = false;
  });
  document.addEventListener("dragover", (e) => e.preventDefault());
  document.addEventListener("dragleave", () => {
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) $("drop").hidden = true;
  });
  document.addEventListener("drop", async (e) => {
    e.preventDefault();
    dragDepth = 0;
    $("drop").hidden = true;
    const chunks = [e.dataTransfer.getData("text/uri-list"), e.dataTransfer.getData("text/plain")];
    for (const file of e.dataTransfer.files || []) {
      if (file.size > 2_000_000) continue;
      if (!/\.(txt|lst|url|csv)$/i.test(file.name) && !String(file.type || "").startsWith("text/")) continue;
      try {
        chunks.push(await file.text());
      } catch {}
    }
    const text = chunks.map((part) => String(part || "").trim()).filter(Boolean).join("\n");
    if (!text) {
      toast("Drop links, or a text file of URLs.", "warn");
      return;
    }
    $("url").value = [$("url").value, text].filter(Boolean).join("\n");
    refreshUrlCount();
    toast("Links added.");
    setView("queue");
  });
  document.addEventListener("error", (e) => {
    const img = e.target;
    if (!(img instanceof HTMLImageElement)) return;
    const ph = document.createElement("div");
    if (img.classList.contains("thumb")) {
      ph.className = "thumb ph";
      ph.textContent = img.dataset.ph || "";
      img.replaceWith(ph);
    } else if (img.closest(".shot")) {
      ph.className = "ph";
      ph.textContent = "—";
      img.replaceWith(ph);
    }
  }, true);
  document.addEventListener("keydown", (e) => {
    const confirmOpen = !$("confirm-modal").hidden;
    if (e.key === "Escape") {
      if (confirmOpen) {
        $("confirm-cancel").click();
        return;
      }
      if (!$("lightbox").hidden) closeLightbox();
      return;
    }
    if (e.key === "Enter" && confirmOpen) {
      e.preventDefault();
      $("confirm-ok").click();
      return;
    }
    const tag = ((e.target && e.target.tagName) || "").toLowerCase();
    const typing = tag === "input" || tag === "textarea" || tag === "select" || (e.target && e.target.isContentEditable);
    if (!$("lightbox").hidden && !typing) {
      if (e.key === "ArrowLeft") showLightboxAt(state.lightboxAt - 1);
      if (e.key === "ArrowRight") showLightboxAt(state.lightboxAt + 1);
      return;
    }
    if (!typing && state.view === "preview" && (e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a") {
      e.preventDefault();
      visibleItems(currentScan()).forEach((it) => state.selected.add(it.index));
      renderPreview();
    }
  });
}

function closeLightbox() {
  $("lightbox").hidden = true;
  const video = $("lightbox-video");
  video.pause();
  video.removeAttribute("src");
  video.load();
  $("lightbox-img").removeAttribute("src");
}

function showLightboxAt(index) {
  const items = state.lightbox || [];
  if (!items.length) return;
  state.lightboxAt = (index + items.length) % items.length;
  const item = items[state.lightboxAt];
  const img = $("lightbox-img");
  const video = $("lightbox-video");
  if (item.video) {
    img.hidden = true;
    video.hidden = false;
    if (video.src !== new URL(item.src, location.href).href) {
      video.src = item.src;
    }
    video.play().catch(() => {});
  } else {
    video.pause();
    video.hidden = true;
    video.removeAttribute("src");
    img.hidden = false;
    img.src = item.src;
    img.alt = item.cap || "";
  }
  $("lightbox-cap").textContent = item.cap || "";
  $("lightbox-prev").hidden = items.length < 2;
  $("lightbox-next").hidden = items.length < 2;
  $("lightbox").hidden = false;
}

function openLightbox(items, index) {
  state.lightbox = items || [];
  showLightboxAt(index || 0);
}

let eventSource = null;
function connectEvents() {
  if (eventSource) {
    try {
      eventSource.close();
    } catch {}
  }
  const es = new EventSource("/api/events");
  eventSource = es;
  es.addEventListener("disk", (ev) => {
    try {
      state.disk = JSON.parse(ev.data);
      renderDisk();
      if (state.view === "queue" || state.view === "history") scheduleRender();
    } catch {}
  });
  es.onmessage = (ev) => {
    try {
      const data = JSON.parse(ev.data);
      const prev = state.jobs;
      state.jobs = data.jobs || [];
      state.disk = data.disk || state.disk;
      const scanning = state.jobs.find((j) => j.kind === "scan" && (j.status === "scanning" || j.item_count));
      if (scanning && (state.view === "queue" || state.view === "preview")) {
        if (!state.scanId) selectScan(scanning.id);
        pullScanItems(false);
      }
      const finishedScan = state.jobs.find(
        (j) => j.kind === "scan" && j.status === "done" && j.item_count && !prev.find((p) => p.id === j.id && p.status === "done")
      );
      if (finishedScan) {
        // Don't yank the preview away from a selection the user is building.
        if (!state.selected.size) selectScan(finishedScan.id);
        toast(`Scan finished. ${finishedScan.item_count} picture${finishedScan.item_count === 1 ? "" : "s"} found.`);
        if (state.view === "preview") pullScanItems(true);
      }
      const finishedDl = state.jobs.find(
        (j) => j.kind !== "scan" && j.status === "done" && !prev.find((p) => p.id === j.id && p.status === "done")
      );
      if (finishedDl) {
        toast("Download finished.");
        if (state.view === "library") loadLibrary();
      }
      const failed = state.jobs.find(
        (j) => j.status === "error" && !prev.find((p) => p.id === j.id && p.status === "error")
      );
      if (failed) toast(failed.error || "A job failed.", "critical");
      scheduleRender();
    } catch {}
  };
  es.onerror = () => {
    setTimeout(() => {
      if (eventSource === es) connectEvents();
    }, 2500);
  };
}

async function boot() {
  wire();
  try {
    const health = await api("/api/health");
    state.health = health;
    state.disk = health.disk;
    fillSettings(health.settings || {});
    applyEngine(health.engine);
    renderHealth();
    const data = await api("/api/jobs");
    state.jobs = data.jobs || [];
  } catch (err) {
    toast(err.message, "critical");
  }
  setView("queue");
  connectEvents();
  setTimeout(async () => {
    try {
      const later = await api("/api/health");
      state.health = later;
      state.disk = later.disk || state.disk;
      applyEngine(later.engine);
      renderHealth();
      renderDisk();
      if (!later.engine || !later.engine.checked) {
        try {
          applyEngine(await api("/api/engine/check", { method: "POST" }));
        } catch {}
      }
    } catch {}
  }, 2500);
}

boot();
