/* iPodFS - Oberflaeche. Vanilla JS, kein Build-Schritt. */
"use strict";

const TOKEN = window.IPODFS_TOKEN;
const $ = (sel, ctx = document) => ctx.querySelector(sel);
const $$ = (sel, ctx = document) => [...ctx.querySelectorAll(sel)];

const state = {
  device: { connected: false },
  library: {},
  options: {},
  layouts: {},
  issueLabels: {},
  knownPlayers: {},
  apps: [],
  fs: { root: "media", path: "/", selected: new Set() },
  jobTimer: null,
  jobDone: null,
  openAlbum: null,
};

/* ───────────────────────────────────────────────────────── Helfer ── */
async function api(path, options = {}) {
  const opts = { headers: { "X-IPodFS-Token": TOKEN }, ...options };
  if (opts.json !== undefined) {
    opts.method = opts.method || "POST";
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(opts.json);
    delete opts.json;
  }
  const response = await fetch(path, opts);
  let data = {};
  try { data = await response.json(); } catch { /* z. B. leere Antwort */ }
  if (!response.ok) {
    const err = new Error(data.error || `HTTP ${response.status}`);
    err.hint = data.hint || "";
    err.kind = data.kind || "";
    throw err;
  }
  return data;
}

function bytes(value) {
  const n = Number(value) || 0;
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let size = n / 1024, i = 0;
  while (size >= 1024 && i < units.length - 1) { size /= 1024; i++; }
  return `${size < 10 ? size.toFixed(1) : Math.round(size)} ${units[i]}`;
}

function when(epoch) {
  if (!epoch) return "—";
  const d = new Date(epoch * 1000);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" });
}

function duration(seconds) {
  const total = Math.round(Number(seconds) || 0);
  const h = Math.floor(total / 3600), m = Math.floor((total % 3600) / 60);
  return h ? `${h} h ${m} min` : `${m} min`;
}

function esc(text) {
  return String(text ?? "").replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function toast(title, detail = "", kind = "") {
  const node = document.createElement("div");
  node.className = `toast ${kind}`;
  node.innerHTML = `<b>${esc(title)}</b>${detail ? `<span>${esc(detail)}</span>` : ""}`;
  $("#toasts").appendChild(node);
  setTimeout(() => { node.style.opacity = "0"; node.style.transition = "opacity .3s"; }, 5200);
  setTimeout(() => node.remove(), 5600);
}

function oops(error) {
  toast(error.message || "Fehler", error.hint || "", "bad");
  console.warn(error);
}

/* ───────────────────────────────────────────────────────── Tabs ── */
$$("nav button").forEach(button => button.onclick = () => {
  $$("nav button").forEach(b => b.classList.toggle("active", b === button));
  $$("main section").forEach(s => s.hidden = s.id !== `tab-${button.dataset.tab}`);
  if (button.dataset.tab === "diag") runDoctor();
  if (button.dataset.tab === "music") loadBackups();
});

/* ───────────────────────────────────────────────────────── Jobs ── */
function watchJob(jobId, onDone) {
  state.jobDone = onDone;
  clearInterval(state.jobTimer);
  state.jobTimer = setInterval(() => pollJob(jobId), 500);
  pollJob(jobId);
}

async function pollJob(jobId) {
  let job;
  try { job = await api(`/api/jobs/${jobId}`); }
  catch { clearInterval(state.jobTimer); $("#jobbar").hidden = true; return; }

  $("#jobbar").hidden = false;
  $("#job-title").textContent = job.title;
  $("#job-msg").textContent = job.message || "";
  $("#job-pct").textContent = job.total ? `${job.percent}%` : "";
  $("#job-bar").style.width = `${job.total ? job.percent : 0}%`;
  $("#job-cancel").dataset.job = jobId;

  if (job.running) return;

  clearInterval(state.jobTimer);
  setTimeout(() => { $("#jobbar").hidden = true; }, 900);

  if (job.status === "error") toast("Fehlgeschlagen", job.error + (job.hint ? ` — ${job.hint}` : ""), "bad");
  else if (job.status === "cancelled") toast("Abgebrochen", "", "");

  const callback = state.jobDone;
  state.jobDone = null;
  if (callback) callback(job);
}

$("#job-cancel").onclick = async (event) => {
  const jobId = event.target.dataset.job;
  if (jobId) await api(`/api/jobs/${jobId}/cancel`, { method: "POST" }).catch(() => {});
};

/* ───────────────────────────────────────────────────── Geraet ── */
async function refreshState() {
  try {
    const data = await api("/api/state");
    state.device = data.device || { connected: false };
    state.library = data.library || {};
    state.options = data.options || {};
    state.layouts = data.layouts || {};
    state.issueLabels = data.issue_labels || {};
    state.knownPlayers = data.known_players || {};
    renderDeviceChip();
    renderLibraryStats();
    applyOptionInputs();
    fillLayouts();
  } catch (error) { oops(error); }
}

function renderDeviceChip() {
  const chip = $("#device-chip");
  const device = state.device;
  chip.classList.toggle("on", !!device.connected);
  if (device.connected) {
    $("#dev-name").textContent = device.name || "iPod";
    const parts = [device.model, device.ios ? `iOS ${device.ios}` : ""].filter(Boolean);
    if (device.total_bytes) parts.push(`${bytes(device.free_bytes)} frei`);
    if (device.jailbroken) parts.push("afc2");
    $("#dev-sub").textContent = parts.join(" · ");
  } else {
    $("#dev-name").textContent = "Nicht verbunden";
    $("#dev-sub").textContent = "klicken zum Verbinden";
  }
  fillRootSelects();
}

$("#device-chip").onclick = () => { $("#dlg-device").showModal(); listDevices(); };
$("#dev-close").onclick = () => $("#dlg-device").close();
$("#dev-refresh").onclick = () => listDevices();
$("#dev-disconnect").onclick = async () => {
  await api("/api/device/disconnect", { method: "POST" }).catch(() => {});
  state.apps = [];
  await refreshState();
  $("#dlg-device").close();
  renderFiles([]);
};

async function listDevices() {
  const box = $("#dev-list");
  box.innerHTML = '<div class="hint">Suche …</div>';
  try {
    const { devices } = await api("/api/device/list");
    if (!devices.length) {
      box.innerHTML = `<div class="note warn"><h3>Kein Gerät gefunden</h3>
        <p>iPod per USB anstecken und entsperren. Viele „Ladekabel“ haben keine
        Datenleitung — im Zweifel ein anderes Kabel probieren.</p></div>`;
      return;
    }
    box.innerHTML = devices.map(device => `
      <div class="row" style="padding:9px 0;border-bottom:1px solid var(--line-soft)">
        <div><b>${esc(device.udid.slice(0, 18))}…</b>
          <div class="hint">${esc(device.connection)}</div></div>
        <div class="spacer"></div>
        <button class="btn primary small" data-udid="${esc(device.udid)}">Verbinden</button>
      </div>`).join("");
    $$("#dev-list button[data-udid]").forEach(button => button.onclick = async () => {
      button.disabled = true;
      button.textContent = "Verbinde …";
      try {
        await api("/api/device/connect", { json: { udid: button.dataset.udid } });
        await refreshState();
        await loadApps();
        $("#dlg-device").close();
        toast("Verbunden", state.device.name || "", "good");
        openPath(state.fs.root, "/");
      } catch (error) {
        oops(error);
        button.disabled = false;
        button.textContent = "Verbinden";
      }
    });
  } catch (error) {
    box.innerHTML = `<div class="note warn"><h3>${esc(error.message)}</h3>
      <p>${esc(error.hint || "")}</p></div>`;
  }
}

async function loadApps() {
  try {
    const { apps } = await api("/api/device/apps");
    state.apps = apps || [];
  } catch { state.apps = []; }
  fillRootSelects();
}

function rootOptions() {
  const options = [["media", "Medien-Partition  (/var/mobile/Media)"]];
  if (state.device.jailbroken) options.push(["root", "Wurzel-Dateisystem  (Jailbreak, afc2)"]);
  state.apps.filter(app => app.file_sharing).forEach(app =>
    options.push([`app:${app.bundle_id}`,
      `App: ${app.name}${app.known_player ? "  ♪ Player" : ""}`]));
  return options;
}

function fillRootSelects() {
  const options = rootOptions();
  ["#fs-root", "#sync-root"].forEach(selector => {
    const select = $(selector);
    const previous = select.value;
    select.innerHTML = options.map(([value, label]) =>
      `<option value="${esc(value)}">${esc(label)}</option>`).join("");
    if (options.some(o => o[0] === previous)) select.value = previous;
  });
}

function fillLayouts() {
  const select = $("#sync-layout");
  if (select.options.length) return;
  select.innerHTML = Object.entries(state.layouts)
    .map(([value, label]) => `<option value="${esc(value)}">${esc(label)}</option>`).join("");
}

/* ─────────────────────────────────────────────── Dateibrowser ── */
$("#fs-root").onchange = () => openPath($("#fs-root").value, "/");
$("#fs-reload").onclick = () => openPath(state.fs.root, state.fs.path);
$("#fs-up").onclick = () => {
  const parts = state.fs.path.split("/").filter(Boolean);
  parts.pop();
  openPath(state.fs.root, "/" + parts.join("/"));
};

async function openPath(root, path) {
  state.fs.root = root;
  state.fs.path = path || "/";
  state.fs.selected.clear();
  updateDeleteButton();
  if (!state.device.connected) {
    $("#fs-empty").textContent = "Noch nicht verbunden.";
    $("#fs-empty").hidden = false;
    $("#fs-rows").innerHTML = "";
    renderCrumbs();
    return;
  }
  $("#fs-empty").textContent = "Lade …";
  $("#fs-empty").hidden = false;
  try {
    const data = await api(`/api/fs/list?root=${encodeURIComponent(root)}&path=${encodeURIComponent(state.fs.path)}`);
    renderFiles(data.entries);
  } catch (error) {
    oops(error);
    $("#fs-rows").innerHTML = "";
    $("#fs-empty").textContent = error.message;
    $("#fs-empty").hidden = false;
  }
  renderCrumbs();
}

function renderCrumbs() {
  const parts = state.fs.path.split("/").filter(Boolean);
  const label = $("#fs-root").selectedOptions[0]?.textContent || "Gerät";
  let html = `<button data-p="/">${esc(label.split("  ")[0])}</button>`;
  let acc = "";
  parts.forEach(part => {
    acc += "/" + part;
    html += `<i>›</i><button data-p="${esc(acc)}">${esc(part)}</button>`;
  });
  $("#fs-crumbs").innerHTML = html;
  $$("#fs-crumbs button").forEach(button =>
    button.onclick = () => openPath(state.fs.root, button.dataset.p));
}

function renderFiles(entries) {
  const body = $("#fs-rows");
  $("#fs-empty").hidden = entries.length > 0;
  if (!entries.length) $("#fs-empty").textContent = "Dieser Ordner ist leer.";
  body.innerHTML = entries.map(entry => `
    <tr data-path="${esc(entry.path)}" data-dir="${entry.is_dir ? 1 : 0}">
      <td><input type="checkbox"></td>
      <td><div class="nm"><span class="ico">${entry.is_dir ? "📁" : entry.is_link ? "🔗" : "📄"}</span>
        <span>${esc(entry.name)}</span></div></td>
      <td class="num">${entry.is_dir ? "" : bytes(entry.size)}</td>
      <td class="num">${when(entry.mtime)}</td>
      <td class="act">${entry.is_dir ? "" :
        `<button class="btn small ghost" data-dl="${esc(entry.path)}">Laden</button>`}</td>
    </tr>`).join("");

  $$("#fs-rows .nm").forEach(cell => cell.onclick = () => {
    const row = cell.closest("tr");
    if (row.dataset.dir === "1") openPath(state.fs.root, row.dataset.path);
  });
  $$("#fs-rows input[type=checkbox]").forEach(box => box.onchange = () => {
    const row = box.closest("tr");
    row.classList.toggle("sel", box.checked);
    box.checked ? state.fs.selected.add(row.dataset.path) : state.fs.selected.delete(row.dataset.path);
    updateDeleteButton();
  });
  $$("#fs-rows button[data-dl]").forEach(button => button.onclick = () => {
    const url = `/api/fs/download?root=${encodeURIComponent(state.fs.root)}`
      + `&path=${encodeURIComponent(button.dataset.dl)}&token=${encodeURIComponent(TOKEN)}`;
    window.location.href = url;
  });
}

function updateDeleteButton() {
  $("#fs-del").disabled = state.fs.selected.size === 0;
}

$("#fs-all").onchange = (event) => {
  $$("#fs-rows input[type=checkbox]").forEach(box => {
    box.checked = event.target.checked;
    box.dispatchEvent(new Event("change"));
  });
};

$("#fs-del").onclick = async () => {
  const paths = [...state.fs.selected];
  if (!confirm(`${paths.length} Objekt(e) endgültig vom iPod löschen?`)) return;
  try {
    const result = await api("/api/fs/delete", { json: { root: state.fs.root, paths } });
    if (result.errors?.length) toast("Teilweise fehlgeschlagen", result.errors.join("\n"), "bad");
    else toast("Gelöscht", `${result.deleted} Objekt(e)`, "good");
    openPath(state.fs.root, state.fs.path);
  } catch (error) { oops(error); }
};

$("#fs-mkdir").onclick = async () => {
  const name = prompt("Name des neuen Ordners:");
  if (!name) return;
  const path = (state.fs.path === "/" ? "" : state.fs.path) + "/" + name;
  try {
    await api("/api/fs/mkdir", { json: { root: state.fs.root, path } });
    openPath(state.fs.root, state.fs.path);
  } catch (error) { oops(error); }
};

$("#fs-pick").onclick = () => $("#fs-files").click();
$("#fs-files").onchange = (event) => {
  const files = [...event.target.files].map(file => ({ file, rel: file.name }));
  event.target.value = "";
  uploadFiles(files);
};

/* Drag & Drop - inklusive ganzer Ordner. */
const dropBox = $("#fs-box");
let dragDepth = 0;
["dragenter", "dragover"].forEach(type => dropBox.addEventListener(type, event => {
  event.preventDefault();
  if (type === "dragenter") dragDepth++;
  dropBox.classList.add("drag");
}));
["dragleave", "drop"].forEach(type => dropBox.addEventListener(type, event => {
  event.preventDefault();
  if (type === "dragleave") dragDepth = Math.max(0, dragDepth - 1);
  else dragDepth = 0;
  if (!dragDepth) dropBox.classList.remove("drag");
}));
dropBox.addEventListener("drop", async (event) => {
  if (!state.device.connected) return toast("Nicht verbunden", "Erst den iPod verbinden.", "bad");
  const files = await collectDropped(event.dataTransfer);
  if (files.length) uploadFiles(files);
});

async function collectDropped(transfer) {
  const roots = [...(transfer.items || [])]
    .map(item => item.webkitGetAsEntry && item.webkitGetAsEntry())
    .filter(Boolean);
  if (!roots.length) return [...transfer.files].map(file => ({ file, rel: file.name }));

  const out = [];
  const walk = async (entry, prefix) => {
    if (entry.isFile) {
      const file = await new Promise((res, rej) => entry.file(res, rej));
      out.push({ file, rel: prefix + entry.name });
    } else if (entry.isDirectory) {
      const reader = entry.createReader();
      let batch;
      do {
        batch = await new Promise((res, rej) => reader.readEntries(res, rej));
        for (const child of batch) await walk(child, `${prefix}${entry.name}/`);
      } while (batch.length);
    }
  };
  for (const entry of roots) await walk(entry, "");
  return out;
}

async function uploadFiles(files) {
  if (!files.length) return;
  const form = new FormData();
  form.append("root", state.fs.root);
  form.append("path", state.fs.path);
  files.forEach(({ file, rel }) => { form.append("files", file); form.append("relative", rel); });
  try {
    const { job } = await api("/api/fs/upload", { method: "POST", body: form });
    watchJob(job, (finished) => {
      const result = finished.result || {};
      if (result.errors?.length) toast("Mit Fehlern beendet", result.errors.slice(0, 3).join("\n"), "bad");
      else if (finished.status === "done") toast("Hochgeladen", `${result.uploaded} Datei(en)`, "good");
      openPath(state.fs.root, state.fs.path);
      refreshState();
    });
  } catch (error) { oops(error); }
}

/* ──────────────────────────────────────────────────── Musik ── */
$("#lib-browse").onclick = async () => {
  try {
    const { path, error } = await api("/api/library/pick", { method: "POST" });
    if (path) $("#lib-path").value = path;
    else if (error) toast("Dialog nicht verfügbar", "Pfad bitte von Hand eintippen.", "bad");
  } catch (error) { oops(error); }
};

$("#lib-scan").onclick = async () => {
  const path = $("#lib-path").value.trim();
  if (!path) return toast("Kein Ordner", "Bitte einen Musikordner angeben.", "bad");
  try {
    const { job } = await api("/api/library/scan", { json: { roots: [path] } });
    watchJob(job, async (finished) => {
      if (finished.status === "done") {
        await refreshState();
        await loadAlbums();
        const stats = finished.result || {};
        toast("Eingelesen", `${stats.tracks} Songs in ${stats.albums} Alben`, "good");
      }
    });
  } catch (error) { oops(error); }
};

function renderLibraryStats() {
  const stats = state.library || {};
  const has = (stats.tracks || 0) > 0;
  $("#lib-stats-card").hidden = !has;
  $("#lib-albums-card").hidden = !has;
  $("#lib-roots").textContent = (stats.roots || []).join("  ·  ");

  const badge = $("#badge-issues");
  badge.hidden = !stats.albums_with_issues;
  badge.textContent = stats.albums_with_issues || 0;
  if (!has) return;

  $("#lib-stats").innerHTML = `
    <div class="stat"><b>${stats.tracks}</b><span>Songs</span></div>
    <div class="stat"><b>${stats.albums}</b><span>Alben</span></div>
    <div class="stat ${stats.duplicate_tiles ? "bad" : "good"}"><b>${stats.tiles_before}</b>
      <span>Cover-Kacheln jetzt</span></div>
    <div class="stat good"><b>${stats.tiles_after}</b><span>nach der Reparatur</span></div>
    <div class="stat ${stats.duplicate_tiles ? "bad" : "good"}"><b>${stats.duplicate_tiles}</b>
      <span>überflüssige Dubletten</span></div>
    <div class="stat"><b>${bytes(stats.total_bytes)}</b><span>${duration(stats.total_duration)}</span></div>`;
  $("#lib-fixall").disabled = !stats.albums_with_issues;
  $("#lib-fixall").textContent = stats.albums_with_issues
    ? `${stats.albums_with_issues} Album(en) reparieren` : "Nichts zu tun";
}

$("#lib-opts-toggle").onclick = () => { $("#lib-opts").hidden = !$("#lib-opts").hidden; };

function applyOptionInputs() {
  $$("#lib-opts input[data-opt]").forEach(input => {
    input.checked = !!state.options[input.dataset.opt];
    input.onchange = async () => {
      const payload = {};
      $$("#lib-opts input[data-opt]").forEach(i => payload[i.dataset.opt] = i.checked);
      try {
        const data = await api("/api/library/options", { json: payload });
        state.options = data.options;
        state.library = data.stats;
        renderLibraryStats();
        loadAlbums();
      } catch (error) { oops(error); }
    };
  });
}

let searchTimer;
$("#lib-search").oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(loadAlbums, 250); };
$("#lib-only-issues").onchange = loadAlbums;

async function loadAlbums() {
  if (!(state.library.tracks > 0)) return;
  const params = new URLSearchParams({
    issues: $("#lib-only-issues").checked ? "1" : "0",
    q: $("#lib-search").value.trim(),
  });
  try {
    const data = await api(`/api/library/albums?${params}`);
    renderAlbums(data.albums);
    $("#lib-count").textContent = `— ${data.albums.length} von ${data.total}`;
  } catch (error) { oops(error); }
}

function renderAlbums(albums) {
  const box = $("#lib-albums");
  if (!albums.length) {
    box.innerHTML = `<div class="empty">Keine Alben in dieser Ansicht.
      ${$("#lib-only-issues").checked ? "Alles sauber! 🎉" : ""}</div>`;
    return;
  }
  box.innerHTML = albums.map(album => {
    const saved = album.tiles_before - album.tiles_after;
    const tags = album.issues.map(issue =>
      `<span class="pill ${issue === "artwork_mismatch" ? "warn" : "bad"}">${esc(state.issueLabels[issue] || issue)}</span>`).join("");
    return `
      <div class="album ${album.needs_fix ? "issue" : "clean"}" data-key="${esc(album.key)}">
        <div class="cover ph" data-cover="${esc(album.key)}">♪</div>
        <div class="meta">
          <b>${esc(album.album)}</b>
          <div class="sub">${esc(album.album_artist)} · ${album.track_count} Songs${album.is_compilation ? " · Sampler" : ""}</div>
          <div class="tags">${tags}</div>
        </div>
        <div class="right">
          ${saved > 0 ? `<div class="tiles"><span class="from">${album.tiles_before}</span>
            <span class="arrow">→</span><span class="to">${album.tiles_after}</span> Kacheln</div>` : ""}
          ${album.needs_fix ? `<button class="btn small primary" data-fix="${esc(album.key)}">Reparieren</button>`
            : `<span class="pill good">in Ordnung</span>`}
        </div>
      </div>
      <div class="tracklist" data-detail="${esc(album.key)}" hidden></div>`;
  }).join("");

  $$("#lib-albums .album").forEach(card => card.onclick = (event) => {
    if (event.target.closest("button")) return;
    toggleAlbum(card.dataset.key);
  });
  $$("#lib-albums button[data-fix]").forEach(button =>
    button.onclick = () => fixAlbums([button.dataset.fix]));
  loadCovers(albums);
}

async function loadCovers(albums) {
  for (const album of albums.slice(0, 60)) {
    try {
      const detail = await api(`/api/library/album/${encodeURIComponent(album.key)}`);
      const withArt = (detail.tracks || []).find(track => track.art_hash);
      if (!withArt) continue;
      const holder = $(`[data-cover="${CSS.escape(album.key)}"]`);
      if (!holder) continue;
      const img = document.createElement("img");
      img.className = "cover";
      img.loading = "lazy";
      img.src = `/api/library/art?path=${encodeURIComponent(withArt.path)}&token=${encodeURIComponent(TOKEN)}`;
      img.onerror = () => img.remove();
      holder.replaceWith(img);
    } catch { /* Cover sind Beiwerk */ }
  }
}

async function toggleAlbum(key) {
  const panel = $(`[data-detail="${CSS.escape(key)}"]`);
  if (!panel) return;
  if (!panel.hidden) { panel.hidden = true; return; }
  panel.hidden = false;
  panel.innerHTML = '<div class="hint" style="padding:10px">Lade …</div>';
  try {
    const album = await api(`/api/library/album/${encodeURIComponent(key)}`);
    const changes = Object.fromEntries((album.changes || []).map(c => [c.path, c.fields]));
    panel.innerHTML = `<table>${album.tracks.map(track => {
      const fields = changes[track.path] || {};
      const diff = Object.entries(fields).map(([name, value]) =>
        `<div class="chg">${esc(label(name))}: <del>${esc(String(value.old ?? "") || "leer")}</del>
         → <ins>${esc(String(value.new ?? "") || "leer")}</ins></div>`).join("");
      return `<tr>
        <td class="n">${track.track ?? ""}</td>
        <td><div>${esc(track.title)}</div>
          <div class="hint">${esc(track.artist || "—")}</div>${diff}</td>
        <td class="num">${esc(fmtLen(track.duration))}</td>
        <td class="num"><button class="btn small ghost" data-edit="${esc(track.path)}">Bearbeiten</button></td>
      </tr>`;
    }).join("")}</table>`;
    $$("[data-edit]", panel).forEach(button =>
      button.onclick = () => editTrack(album.tracks.find(t => t.path === button.dataset.edit)));
  } catch (error) { oops(error); }
}

function fmtLen(seconds) {
  const total = Math.round(Number(seconds) || 0);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

function label(field) {
  return {
    title: "Titel", artist: "Interpret", albumartist: "Album-Interpret", album: "Album",
    genre: "Genre", year: "Jahr", track: "Track", track_total: "Tracks gesamt",
    disc: "CD", disc_total: "CDs gesamt", compilation: "Sampler-Flag",
    sort_artist: "Sortierung Interpret", sort_albumartist: "Sortierung Album-Interpret",
    sort_album: "Sortierung Album",
  }[field] || field;
}

async function fixAlbums(keys) {
  try {
    const { job } = await api("/api/library/fix", { json: keys ? { keys } : {} });
    watchJob(job, async (finished) => {
      if (finished.status !== "done") return;
      const result = finished.result || {};
      state.library = result.stats || state.library;
      renderLibraryStats();
      await loadAlbums();
      loadBackups();
      if (result.errors?.length) toast("Teilweise fehlgeschlagen", result.errors.slice(0, 3).join("\n"), "bad");
      else toast("Repariert", `${result.files_changed} Dateien, ${result.fields_changed} Felder`, "good");
    });
  } catch (error) { oops(error); }
}

$("#lib-fixall").onclick = () => {
  if (!confirm("Alle erkannten Probleme in den Tags beheben?\n\nDie alten Werte werden gesichert und lassen sich zurückholen.")) return;
  fixAlbums(null);
};

async function loadBackups() {
  try {
    const { backups } = await api("/api/library/backups");
    $("#lib-backups-card").hidden = !backups.length;
    $("#lib-backups").innerHTML = backups.map(backup => `
      <div class="row" style="padding:8px 0;border-bottom:1px solid var(--line-soft)">
        <div><b>${when(backup.created)}</b>
          <div class="hint">${backup.files} Datei(en)</div></div>
        <div class="spacer"></div>
        <button class="btn small" data-undo="${esc(backup.id)}">Zurücknehmen</button>
      </div>`).join("");
    $$("#lib-backups button[data-undo]").forEach(button => button.onclick = async () => {
      if (!confirm("Diesen Reparaturlauf komplett zurücknehmen?")) return;
      const { job } = await api("/api/library/undo", { json: { id: button.dataset.undo } });
      watchJob(job, async () => {
        await refreshState();
        await loadAlbums();
        loadBackups();
        toast("Zurückgenommen", "", "good");
      });
    });
  } catch { /* Backups sind optional */ }
}

/* Einzelnen Song bearbeiten */
let editing = null;
function editTrack(track) {
  if (!track) return;
  editing = track;
  $("#t-title").value = track.title || "";
  $("#t-artist").value = track.artist || "";
  $("#t-albumartist").value = track.albumartist || "";
  $("#t-album").value = track.album || "";
  $("#t-track").value = track.track ?? "";
  $("#t-track-total").value = track.track_total ?? "";
  $("#t-disc").value = track.disc ?? "";
  $("#t-disc-total").value = track.disc_total ?? "";
  $("#t-genre").value = track.genre || "";
  $("#t-year").value = track.year || "";
  $("#dlg-track").showModal();
}
$("#t-cancel").onclick = () => $("#dlg-track").close();
$("#t-save").onclick = async () => {
  if (!editing) return;
  const fields = {
    title: $("#t-title").value, artist: $("#t-artist").value,
    albumartist: $("#t-albumartist").value, album: $("#t-album").value,
    genre: $("#t-genre").value, year: $("#t-year").value,
    track: $("#t-track").value, track_total: $("#t-track-total").value,
    disc: $("#t-disc").value, disc_total: $("#t-disc-total").value,
  };
  try {
    const data = await api("/api/library/track", { json: { path: editing.path, fields } });
    state.library = data.stats;
    $("#dlg-track").close();
    renderLibraryStats();
    await loadAlbums();
    toast("Gespeichert", "", "good");
  } catch (error) { oops(error); }
};

/* ───────────────────────────────────────────────────── Sync ── */
function syncTarget() {
  return {
    root: $("#sync-root").value || "media",
    base: $("#sync-base").value.trim() || "/iPodFS",
    layout: $("#sync-layout").value || "albumartist_album",
  };
}

$("#sync-preview").onclick = async () => {
  if (!state.device.connected) return toast("Nicht verbunden", "Erst den iPod verbinden.", "bad");
  if (!(state.library.tracks > 0)) return toast("Keine Sammlung", "Erst einen Musikordner einlesen.", "bad");
  try {
    const { job } = await api("/api/sync/plan", {
      json: { target: syncTarget(), delete_orphans: $("#sync-delete").checked },
    });
    watchJob(job, (finished) => {
      if (finished.status === "done") renderSyncPlan(finished.result);
    });
  } catch (error) { oops(error); }
};

function renderSyncPlan(plan) {
  $("#sync-result-card").hidden = false;
  $("#sync-run").disabled = plan.upload_count + plan.delete_count === 0;
  const tight = !plan.fits;
  $("#sync-stats").innerHTML = `
    <div class="stat accent"><b>${plan.upload_count}</b><span>zu übertragen</span></div>
    <div class="stat"><b>${plan.skip_count}</b><span>schon aktuell</span></div>
    <div class="stat ${plan.delete_count ? "bad" : ""}"><b>${plan.delete_count}</b><span>zu löschen</span></div>
    <div class="stat ${tight ? "bad" : ""}"><b>${bytes(plan.upload_bytes)}</b>
      <span>von ${bytes(plan.free_bytes)} frei</span></div>`;

  const warnings = [...(plan.warnings || [])];
  if (tight) warnings.unshift("Der freie Speicher auf dem iPod reicht dafür nicht aus.");
  $("#sync-warn").innerHTML = warnings.length
    ? `<div class="note warn"><ul>${warnings.map(w => `<li>${esc(w)}</li>`).join("")}</ul></div>` : "";

  $("#sync-rows").innerHTML = (plan.actions || []).map(action => `
    <tr><td><span class="pill ${action.kind === "delete" ? "bad" : action.kind === "replace" ? "warn" : "info"}">
      ${action.kind === "delete" ? "löschen" : action.kind === "replace" ? "ersetzen" : "neu"}</span></td>
      <td class="mono">${esc(action.dst)}</td>
      <td class="num">${bytes(action.size)}</td></tr>`).join("")
    + (plan.truncated ? `<tr><td></td><td class="hint">… weitere nicht aufgelistet</td><td></td></tr>` : "");
}

$("#sync-run").onclick = async () => {
  if (!confirm("Jetzt auf den iPod übertragen?")) return;
  try {
    const { job } = await api("/api/sync/run", {
      json: { target: syncTarget(), delete_orphans: $("#sync-delete").checked },
    });
    watchJob(job, (finished) => {
      if (finished.status !== "done") return;
      const result = finished.result || {};
      if (result.errors?.length) toast("Mit Fehlern beendet", result.errors.slice(0, 3).join("\n"), "bad");
      else toast("Übertragen", `${result.uploaded} Datei(en), ${bytes(result.bytes)}`, "good");
      $("#sync-run").disabled = true;
      refreshState();
    });
  } catch (error) { oops(error); }
};

/* App per Kabel installieren */
$("#ipa-pick").onclick = () => {
  if (!state.device.connected) return toast("Nicht verbunden", "Erst den iPod verbinden.", "bad");
  $("#ipa-file").click();
};
$("#ipa-file").onchange = async (event) => {
  const file = event.target.files[0];
  event.target.value = "";
  if (!file) return;
  $("#ipa-name").textContent = file.name;
  const form = new FormData();
  form.append("ipa", file);
  try {
    const { job } = await api("/api/device/install", { method: "POST", body: form });
    watchJob(job, async (finished) => {
      if (finished.status === "done") {
        toast("Installiert", file.name, "good");
        await loadApps();
      }
    });
  } catch (error) { oops(error); }
};

/* ─────────────────────────────────────────────────── Diagnose ── */
async function runDoctor() {
  const list = $("#diag-list");
  list.innerHTML = '<div class="hint">Prüfe …</div>';
  try {
    const report = await api("/api/doctor");
    $("#diag-platform").textContent = report.platform;
    list.innerHTML = report.checks.map(check => `
      <div class="diag-row ${check.status}">
        <div class="mark">${check.status === "ok" ? "✓" : check.status === "warn" ? "!" : "✕"}</div>
        <div class="body"><b>${esc(check.name)}</b>
          <div class="d">${esc(check.detail)}</div>
          ${check.fix && check.status !== "ok" ? `<div class="f">${esc(check.fix)}</div>` : ""}</div>
      </div>`).join("");
  } catch (error) {
    list.innerHTML = `<div class="note warn"><h3>${esc(error.message)}</h3></div>`;
  }
}
$("#diag-run").onclick = runDoctor;

/* ───────────────────────────────────────────────────── Start ── */
(async function start() {
  await refreshState();
  if (state.device.connected) { await loadApps(); openPath("media", "/"); }
  else {
    // Automatisch verbinden, wenn genau ein Geraet am Kabel haengt.
    try {
      const { devices } = await api("/api/device/list");
      if (devices.length === 1) {
        await api("/api/device/connect", { json: { udid: devices[0].udid } });
        await refreshState();
        await loadApps();
        openPath("media", "/");
        toast("Verbunden", state.device.name || "", "good");
      } else { openPath("media", "/"); }
    } catch { openPath("media", "/"); }
  }
  loadBackups();
})();
