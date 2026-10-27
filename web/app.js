const $ = (id) => document.getElementById(id);

const COPY = {
  queue: ["Queue", "Paste album URLs. Scan to preview pictures, or Grab to download everything."],
  preview: ["Preview", "Select pictures, then download selected. Scanning fills this grid as files are found."],
  history: ["History", "Finished, failed, and stopped albums."],
  sites: ["Sites", "Search gallery-dl sites. Click a card to paste an example URL."],
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
  layout: localStorage.getItem("stills-layout") || "thumbs",
  engine: {},
  kind: "all",
  scanId: "",
  selected: new Set(),
  lastIndex: null,
  health: null,
  disk: null,
  sites: [],
  logs: {},
};

function escapeHtml(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
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
function askConfirm({ kicker = "Please confirm", title = "Are you sure?", copy = "", ok = "OK" } = {}) {
  if (confirmBusy) return confirmBusy;
  confirmBusy = new Promise((resolve) => {
    const modal = $("confirm-modal");
    $("confirm-kicker").textContent = kicker;
    $("confirm-title").textContent = title;
    $("confirm-copy").textContent = copy;
    $("confirm-copy").hidden = !copy;
    $("confirm-ok").textContent = ok;
    modal.hidden = false;
    let done = false;
    const finish = (value) => {
      if (done) return;
      done = true;
      modal.hidden = true;
      confirmBusy = null;
      resolve(value);
    };
    $("confirm-ok").onclick = () => finish(true);
    $("confirm-cancel").onclick = () => finish(false);
    modal.onclick = (e) => {
      if (e.target === modal) finish(false);
    };
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
  }, 3200);
  const banner = $("banner");
  if (kind === "critical") {
    banner.hidden = false;
    banner.className = "banner critical";
    banner.textContent = msg;
  } else {
    banner.hidden = true;
  }
}

function activeJobs() {
  return state.jobs.filter(
    (j) =>
      ["queued", "starting", "scanning", "downloading", "stopping"].includes(j.status) ||
      (j.kind === "scan" && j.status === "error")
  );
}
function historyJobs() {
  return state.jobs.filter((j) => ["done", "error", "stopped", "partial"].includes(j.status) && j.kind !== "scan");
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
  $("jobs").hidden = view !== "queue" && view !== "history";
  $("preview-wrap").hidden = view !== "preview";
  $("sites-wrap").hidden = view !== "sites";
  $("settings-wrap").hidden = view !== "settings";
  $("layout-switch").hidden = view !== "queue" && view !== "history";
  $("stop-all").hidden = view !== "queue";
  $("retry-all").hidden = view !== "history";
  $("clear-history").hidden = view !== "history";
  if (view === "sites") loadSites();
  if (view === "preview") pullScanItems(true);
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
  $("disk-bar").style.width = `${Math.min(100, d.percent_used || 0)}%`;
  $("disk-free").textContent = d.free_gb != null ? `${d.free_gb} GB free` : "Checking disk…";
}

function renderCounts() {
  const q = activeJobs().length;
  const p = (currentScan() && (currentScan().item_count || (state.scanItems[currentScan().id] || []).length)) || 0;
  const h = historyJobs().length;
  const set = (id, n) => {
    const el = $(id);
    if (!el) return;
    el.hidden = !n;
    el.textContent = n || "";
  };
  set("nav-count-queue", q);
  set("nav-count-preview", p);
  set("nav-count-history", h);
  $("rec-pill").hidden = !q;
  $("rec-count").textContent = `${q} active`;
}

function emptyHtml(title, copy, icon = "inbox", action = "") {
  return `<div class="empty"><div class="empty-mark">${ICO[icon] || ICO.inbox}</div><p>${escapeHtml(title)}</p><p class="muted">${escapeHtml(copy)}</p>${action}</div>`;
}

function statusChip(job) {
  const live = ["queued", "starting", "scanning", "downloading", "stopping"].includes(job.status);
  const ok = job.status === "done";
  const bad = job.status === "error";
  const cls = live ? "live" : ok ? "ok" : bad ? "bad" : "";
  const label = job.kind === "scan" && job.status === "queued" ? "scan queued" : job.status;
  return `<span class="chip ${cls}">${escapeHtml(label)}</span>`;
}

function jobSig(job) {
  const p = job.progress || {};
  return [job.id, job.status, p.saved, p.current, p.percent, p.total, p.item_count, job.status_line, job.error, job.title, job.size, job.size_speed, p.speed, p.skipped, !!state.logs[job.id]].join("|");
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
  const phLabelJs = phLabel.replace(/'/g, "\\'");
  const thumb = job.thumbnail
    ? `<img class="thumb" src="${escapeHtml(thumbSrc(job.thumbnail))}" alt="" onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'thumb ph',title:'${escapeHtml(phLabel)}',textContent:'${phLabelJs}'}))" />`
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
  if (job.kind === "scan" && (job.status === "done" || job.item_count)) {
    actions.push(`<button class="icon-btn" data-act="preview" data-id="${job.id}">Preview</button>`);
  }
  actions.push(`<button class="icon-btn" data-act="log" data-id="${job.id}">Log</button>`);
  const log = state.logs[job.id];
  const title = job.title && job.title !== job.url ? job.title : hostOf(job.url);
  return `<article class="job" data-status="${escapeHtml(job.status)}" data-id="${job.id}" data-sig="${escapeHtml(jobSig(job))}">
    <div class="thumb-wrap">${thumb}</div>
    <div class="meta">
      <h3 title="${escapeHtml(job.title || job.url)}">${escapeHtml(title)}</h3>
      <div class="sub">${statusChip(job)}<span>${escapeHtml(job.extractor || hostOf(job.url))}</span><span>${escapeHtml(count)}</span><span>${fmtElapsed(p.elapsed)}</span></div>
      ${stats.length ? `<div class="stats">${escapeHtml(stats.join("  ·  "))}</div>` : ""}
      <div class="job-url">${escapeHtml(job.url)}</div>
      ${job.status_line ? `<div class="status-line">${escapeHtml(job.status_line)}</div>` : ""}
      ${negs && job.kind === "scan" ? `<div class="negs">${negs}</div>` : ""}
      <div class="bar${indeterminate ? " indeterminate" : ""}"><i style="width:${pct}%"></i></div>
      ${job.error ? `<div class="sub" style="color:#ffb3b9">${escapeHtml(job.error)}</div>` : ""}
    </div>
    <div class="actions">${actions.join("")}</div>
    ${log ? `<pre class="job-log">${escapeHtml(log.join("\n"))}</pre>` : ""}
  </article>`;
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
  const scans = scanJobs().filter((j) => (j.item_count || 0) > 0 || j.status === "scanning");
  const sel = $("preview-scan");
  const currentIds = scans.map((s) => s.id);
  if (sel.dataset.ids !== currentIds.join(",")) {
    sel.dataset.ids = currentIds.join(",");
    sel.innerHTML = scans.length
      ? scans.map((s) => `<option value="${s.id}">${escapeHtml(s.title || hostOf(s.url))} (${s.item_count || 0})</option>`).join("")
      : `<option value="">No scans yet</option>`;
    if (state.scanId && currentIds.includes(state.scanId)) sel.value = state.scanId;
    else if (scans[0]) {
      selectScan(scans[0].id);
      sel.value = scans[0].id;
    }
  }
  const scan = currentScan();
  const itemsAll = scanItemList(scan);
  $("preview-title").textContent = scan ? scan.title || hostOf(scan.url) : "No preview yet";
  const live = scan && scan.status === "scanning" ? " · scanning…" : "";
  $("preview-sub").textContent = scan
    ? `${scan.item_count || itemsAll.length} files${live} · ${scan.extractor || hostOf(scan.url)} · range ${scan.range || "all"}`
    : "Scan an album to list its pictures here.";
  const grid = $("shot-grid");
  if (!scan || !(scan.item_count || itemsAll.length)) {
    grid.innerHTML = emptyHtml("Nothing to preview", "Paste a URL on Queue, then click Scan. Pictures appear here as they are found.", "search");
    $("sel-count").textContent = "0 selected";
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
  $("sel-count").textContent = `${state.selected.size} selected · ${items.length} visible`;
  $("select-all").checked = items.length > 0 && visSel === items.length;
}

function renderSites() {
  const items = state.sites || [];
  if (!items.length) {
    $("sites-grid").innerHTML = emptyHtml("No sites loaded yet", "gallery-dl is still listing supported sites, or it is not installed.", "globe");
    return;
  }
  $("sites-grid").innerHTML = items
    .slice(0, 200)
    .map(
      (s) => `<button type="button" class="site-card" data-example="${escapeHtml(s.example || "")}" data-label="${escapeHtml(s.label)}">
        <strong>${escapeHtml(s.label)}</strong>
        <span>${escapeHtml(s.example || s.category)}</span>
      </button>`
    )
    .join("");
}

function renderJobs() {
  const list = state.view === "history" ? historyJobs() : state.view === "queue" ? activeJobs() : [];
  const el = $("jobs");
  el.classList.toggle("layout-thumbs", state.layout === "thumbs");
  el.classList.toggle("layout-list", state.layout === "list");
  if (state.view === "queue" && !list.length) {
    el.innerHTML = emptyHtml("Nothing in the queue", "Paste URLs above, then Scan or Grab.", "download");
    return;
  }
  if (state.view === "history" && !list.length) {
    el.innerHTML = emptyHtml("No history yet", "Finished albums will collect here.", "clock");
    return;
  }
  const stale = el.querySelector(".empty");
  if (stale) stale.remove();
  const keep = new Set(list.map((j) => j.id));
  for (const node of [...el.querySelectorAll(".job")]) {
    if (!keep.has(node.dataset.id)) node.remove();
  }
  for (const job of list) {
    const sig = jobSig(job);
    const prev = el.querySelector(`.job[data-id="${job.id}"]`);
    if (prev && prev.dataset.sig === sig) continue;
    const wrap = document.createElement("div");
    wrap.innerHTML = jobCard(job);
    const next = wrap.firstElementChild;
    if (prev) {
      reuseLoadedImages(prev, next);
      prev.replaceWith(next);
    } else {
      el.appendChild(next);
    }
  }
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
  if (state.view === "sites") renderSites();
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
  $("set-retries").value = cfg.retries || 4;
  $("set-ugoira").value = cfg.convert_ugoira || "mp4";
  $("set-warn").value = cfg.warn_free_gb || 0;
  $("set-min-free").value = cfg.min_free_gb || 0;
  $("set-proxy").value = cfg.proxy || "";
  $("terms-modal").hidden = !!cfg.accepted_terms;
}

async function loadSites(q = "") {
  try {
    const data = await api(`/api/sites?q=${encodeURIComponent(q)}`);
    state.sites = data.sites || [];
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

async function sendUrls(kind) {
  const url = $("url").value.trim();
  if (!url) {
    toast("Paste at least one URL.", "warn");
    $("url").focus();
    return;
  }
  const range = $("range").value.trim();
  const path = kind === "scan" ? "/api/scan" : "/api/grab";
  try {
    const created = await api(path, { method: "POST", body: JSON.stringify({ url, range }) });
    const already = !!created.already || (created.jobs || []).some((j) => j.already);
    if (already) {
      toast("Already downloading that album.", "warn");
      return;
    }
    $("url").value = "";
    if (kind === "scan") {
      const id = created.id || (created.jobs && created.jobs[0] && created.jobs[0].id);
      if (id) selectScan(id);
      toast("Scan started. Pictures will appear in Preview.");
      setView("preview");
    } else {
      const n = created.count || 1;
      toast(n > 1 ? `${n} downloads started.` : "Download started.");
      setView("queue");
    }
  } catch (err) {
    toast(err.message, "critical");
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
      localStorage.setItem("stills-layout", state.layout);
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
    toast("URL box cleared.");
  });
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
    await api("/api/jobs/stop-all", { method: "POST" });
    toast("Stopping all jobs.", "warn");
  });
  $("retry-all").addEventListener("click", async () => {
    let n = 0;
    for (const job of historyJobs().filter((j) => ["error", "stopped", "partial"].includes(j.status))) {
      try {
        await api(`/api/jobs/${job.id}/retry`, { method: "POST" });
        n += 1;
      } catch {}
    }
    toast(n ? `Retrying ${n} job${n === 1 ? "" : "s"}.` : "Nothing to retry.", n ? "ok" : "warn");
  });
  $("clear-history").addEventListener("click", async () => {
    if (!(await askConfirm({ title: "Clear history?", copy: "Finished jobs leave the list. Files on disk stay.", ok: "Clear history" }))) return;
    await api("/api/history/clear", { method: "POST" });
    toast("History cleared.");
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
    if (["queued", "starting", "scanning"].includes(scan.status)) {
      try {
        await api(`/api/jobs/${scan.id}/stop`, { method: "POST" });
      } catch {}
    }
    try {
      await api(`/api/jobs/${scan.id}`, { method: "DELETE" });
    } catch {}
    delete state.scanItems[scan.id];
    state.scanId = "";
    state.selected = new Set();
    state.lastIndex = null;
    $("shot-grid").dataset.key = "";
    toast("Preview cleared.");
    renderPreview();
  });
  $("download-selected").addEventListener("click", async () => {
    const scan = currentScan();
    if (!scan) return;
    const indices = [...state.selected];
    if (!indices.length) {
      toast("Select at least one picture.", "warn");
      return;
    }
    try {
      await api(`/api/scans/${scan.id}/download`, { method: "POST", body: JSON.stringify({ indices }) });
      toast(`Downloading ${indices.length} picture${indices.length === 1 ? "" : "s"}.`);
      setView("queue");
    } catch (err) {
      toast(err.message, "critical");
    }
  });
  $("shot-grid").addEventListener("click", (e) => {
    const shot = e.target.closest(".shot");
    if (!shot) return;
    const index = Number(shot.dataset.index);
    if (e.shiftKey && state.lastIndex != null) {
      const a = Math.min(state.lastIndex, index);
      const b = Math.max(state.lastIndex, index);
      const have = new Set(scanItemList(currentScan()).map((it) => it.index));
      for (let i = a; i <= b; i += 1) if (have.has(i)) state.selected.add(i);
    } else if (state.selected.has(index)) state.selected.delete(index);
    else state.selected.add(index);
    state.lastIndex = index;
    renderPreview();
  });
  $("shot-grid").addEventListener("dblclick", (e) => {
    const shot = e.target.closest(".shot");
    if (!shot) return;
    const item = scanItemList(currentScan()).find((it) => it.index === Number(shot.dataset.index));
    if (!item) return;
    // /api/thumb now returns small thumbnails; the lightbox wants the real picture.
    const full = ["image", "gif"].includes(item.kind) && item.url;
    openLightbox(full || item.thumb || item.url, `${item.filename}.${item.extension || ""}`, !!full);
  });
  $("lightbox-close").addEventListener("click", () => {
    $("lightbox").hidden = true;
  });
  $("lightbox").addEventListener("click", (e) => {
    if (e.target.id === "lightbox") $("lightbox").hidden = true;
  });
  let siteSearchTimer = 0;
  $("site-search").addEventListener("input", () => {
    clearTimeout(siteSearchTimer);
    siteSearchTimer = setTimeout(() => loadSites($("site-search").value), 250);
  });
  $("sites-grid").addEventListener("click", (e) => {
    const card = e.target.closest(".site-card");
    if (!card) return;
    if (card.dataset.example) $("url").value = card.dataset.example;
    toast("Example URL pasted. Edit it, then Scan or Grab.");
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
      retries: Number($("set-retries").value || 4),
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
  document.addEventListener("dragover", (e) => e.preventDefault());
  document.addEventListener("drop", (e) => {
    e.preventDefault();
    const text = e.dataTransfer.getData("text/uri-list") || e.dataTransfer.getData("text/plain");
    if (text) {
      $("url").value = [$("url").value, text].filter(Boolean).join("\n");
      toast("URLs added.");
      setView("queue");
    }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") $("lightbox").hidden = true;
  });
}

function openLightbox(src, cap, full = false) {
  $("lightbox-img").src = src.startsWith("/api/") ? src : thumbSrc(src) + (full ? "&full=1" : "");
  $("lightbox-cap").textContent = cap || "";
  $("lightbox").hidden = false;
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
      if (finishedDl) toast("Download finished.");
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
