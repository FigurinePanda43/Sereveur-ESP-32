const API = "/api/devices";
const REFRESH_INTERVAL = 30_000;
const UPDATE_CHECK_INTERVAL = 10 * 60_000;

let devices = [];
let deleteTarget = null;
let modeTarget = null;
let refreshTimer = null;
let updateCheckTimer = null;

// ── Helpers ──────────────────────────────────────────────────────────────────

function esc(str) {
  const d = document.createElement("div");
  d.textContent = str ?? "";
  return d.innerHTML;
}

function formatDate(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("fr-FR", {
    day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

function showToast(msg, type = "success") {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.className = `toast ${type}`;
  t.hidden = false;
  clearTimeout(t._timer);
  t._timer = setTimeout(() => { t.hidden = true; }, 3500);
}

// ── API calls ─────────────────────────────────────────────────────────────────

async function apiFetch(url, opts = {}) {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    ...opts,
  });
  if (res.status === 401) { window.location.href = "/auth/login"; return; }
  if (res.status === 204) return null;
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail ?? `Erreur ${res.status}`);
  return data;
}

// ── Render ────────────────────────────────────────────────────────────────────

function statusClass(status) {
  return ["online", "slow", "offline"].includes(status) ? status : "unknown";
}

function statusLabel(status) {
  return { online: "En ligne", slow: "Lent", offline: "Hors ligne", unknown: "Inconnu" }[status] ?? status;
}

function modeBadge(d) {
  const labels = { suspended: "⏸ Suspendu", protected: "🔒 Protégé", public_temporary: "🌐 Public temporaire", public: "🌐 Public permanent" };
  const classes = { suspended: "badge-suspended", protected: "badge-protected", public_temporary: "badge-public", public: "badge-public-perm" };
  return `<span class="mode-badge ${classes[d.access_mode] || ''}">${labels[d.access_mode] || esc(d.access_mode)}</span>`;
}

function publicWarning(d) {
  if (d.access_mode === "public") {
    return `<div class="public-warning public-warning--perm">⚠ Accessible sans authentification (permanent)</div>`;
  }
  if (d.access_mode !== "public_temporary" || !d.public_until) return "";
  const until = new Date(d.public_until).toLocaleString("fr-FR", {
    day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
  return `<div class="public-warning">⚠ Accessible sans authentification jusqu'au ${until}</div>`;
}

function cardClass(d) {
  if (d.access_mode === "suspended") return "device-card device-card--suspended";
  if (d.access_mode === "public_temporary") return "device-card device-card--public";
  if (d.access_mode === "public") return "device-card device-card--public-perm";
  return "device-card";
}

function renderCard(d) {
  const card = document.createElement("div");
  card.className = cardClass(d);
  card.dataset.id = d.id;

  const isSuspended = d.access_mode === "suspended";
  const sc = isSuspended ? "unknown" : statusClass(d.status);
  const statusText = isSuspended ? "Suspendu" : statusLabel(d.status);

  card.innerHTML = `
    <div class="card-header">
      <div style="flex:1;min-width:0;">
        <span class="card-name">${esc(d.project_name)}</span>
        ${modeBadge(d)}
      </div>
      <span class="status-dot ${sc}" title="${statusText}"></span>
    </div>
    ${d.description ? `<p class="card-desc">${esc(d.description)}</p>` : ""}
    ${publicWarning(d)}
    <div class="card-meta">
      <div class="card-meta-row">
        <span class="meta-label">Statut</span>
        <span class="meta-value">${statusText}</span>
      </div>
      ${!isSuspended && ["offline", "slow"].includes(d.status) && d.status_detail ? `
      <div class="card-meta-row">
        <span class="meta-label">Détail</span>
        <span class="meta-value status-detail">${esc(d.status_detail)}</span>
      </div>` : ""}
      <div class="card-meta-row">
        <span class="meta-label">IP locale</span>
        <span class="meta-value">${esc(d.local_ip)}:${esc(String(d.local_port))}</span>
      </div>
      <div class="card-meta-row">
        <span class="meta-label">Créé le</span>
        <span class="meta-value">${formatDate(d.created_at)}</span>
      </div>
      <div class="card-meta-row">
        <span class="meta-label">Vu le</span>
        <span class="meta-value">${formatDate(d.last_seen)}</span>
      </div>
    </div>
    ${d.public_url ? `
    <div class="card-url">
      <span class="meta-label">URL</span>
      <a href="${esc(d.public_url)}" target="_blank" rel="noopener">${esc(d.public_url)}</a>
    </div>` : ""}
    <div class="card-actions">
      <button class="btn btn-ghost btn-mode" data-id="${d.id}">🔧 Mode</button>
      <button class="btn btn-ghost btn-refresh" data-id="${d.id}" ${isSuspended ? "disabled" : ""}>↻ Tester</button>
      <button class="btn btn-secondary btn-edit" data-id="${d.id}">Modifier</button>
      <button class="btn btn-danger btn-delete" data-id="${d.id}">Supprimer</button>
    </div>
  `;
  return card;
}

function renderAll() {
  const grid = document.getElementById("device-grid");
  const empty = document.getElementById("empty-state");

  // Keep empty-state in DOM, remove cards only
  Array.from(grid.children).forEach(el => {
    if (!el.classList.contains("empty-state")) el.remove();
  });

  if (devices.length === 0) {
    empty.hidden = false;
    return;
  }
  empty.hidden = true;
  devices.forEach(d => grid.appendChild(renderCard(d)));
}

function updateStats() {
  const active = devices.filter(d => d.access_mode !== "suspended");
  document.getElementById("stat-total").textContent     = devices.length;
  document.getElementById("stat-online").textContent    = active.filter(d => d.status === "online").length;
  document.getElementById("stat-slow").textContent      = active.filter(d => d.status === "slow").length;
  document.getElementById("stat-offline").textContent   = active.filter(d => d.status === "offline").length;
  document.getElementById("stat-suspended").textContent = devices.filter(d => d.access_mode === "suspended").length;
}

// ── Load ──────────────────────────────────────────────────────────────────────

async function loadDevices() {
  try {
    devices = await apiFetch(API + "/");
    renderAll();
    updateStats();
  } catch (e) {
    showToast("Impossible de charger les équipements", "error");
  }
}

// ── Auto-refresh ──────────────────────────────────────────────────────────────

function startAutoRefresh() {
  clearInterval(refreshTimer);
  refreshTimer = setInterval(loadDevices, REFRESH_INTERVAL);
}

// ── Modal add/edit ─────────────────────────────────────────────────────────────

const modal         = document.getElementById("modal-backdrop");
const modalTitle    = document.getElementById("modal-title");
const form          = document.getElementById("device-form");
const fieldId       = document.getElementById("field-id");
const fieldName     = document.getElementById("field-name");
const fieldSlug     = document.getElementById("field-slug");
const fieldIp       = document.getElementById("field-ip");
const fieldPort     = document.getElementById("field-port");
const fieldDesc     = document.getElementById("field-desc");
const fieldHttps    = document.getElementById("field-https");
const formError     = document.getElementById("form-error");
const slugPreview   = document.getElementById("slug-preview");
const btnSubmit     = document.getElementById("btn-submit");

function openModal(device = null) {
  form.reset();
  formError.hidden = true;
  slugPreview.textContent = "";

  if (device) {
    modalTitle.textContent = "Modifier l'équipement";
    fieldId.value    = device.id;
    fieldName.value  = device.project_name;
    fieldSlug.value  = device.slug;
    fieldSlug.disabled = true;
    fieldIp.value    = device.local_ip;
    fieldPort.value  = device.local_port;
    fieldHttps.checked = device.local_protocol === "https";
    fieldDesc.value  = device.description;
    updateSlugPreview();
  } else {
    modalTitle.textContent = "Ajouter un équipement";
    fieldId.value = "";
    fieldSlug.disabled = false;
    fieldPort.value = "80";
    fieldHttps.checked = false;
  }

  modal.hidden = false;
  fieldName.focus();
}

function closeModal() {
  modal.hidden = true;
  fieldSlug.disabled = false;
}

// Ouvre le formulaire d'ajout pré-rempli depuis un résultat de scan réseau.
function openModalPrefill(svc) {
  openModal();
  fieldIp.value = svc.ip;
  fieldPort.value = svc.port;
  fieldHttps.checked = svc.scheme === "https";
  if (svc.title) {
    fieldName.value = svc.title;
    fieldSlug.value = svc.title
      .toLowerCase()
      .normalize("NFD").replace(/[\u0300-\u036f]/g, "")
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 63);
    updateSlugPreview();
  }
}

function updateSlugPreview() {
  const slug = fieldSlug.value.trim();
  slugPreview.textContent = slug ? `→ https://${slug}.iot.votre-domaine.com` : "";
}

function showFormError(msg) {
  formError.textContent = msg;
  formError.hidden = false;
}

// ── Form submit ───────────────────────────────────────────────────────────────

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  formError.hidden = true;
  btnSubmit.disabled = true;

  const id   = fieldId.value;
  const isEdit = Boolean(id);

  const payload = {
    project_name:   fieldName.value.trim(),
    local_ip:       fieldIp.value.trim(),
    local_port:     parseInt(fieldPort.value, 10),
    local_protocol: fieldHttps.checked ? "https" : "http",
    description:    fieldDesc.value.trim(),
  };

  if (!isEdit) {
    payload.slug = fieldSlug.value.trim();
  }

  try {
    if (isEdit) {
      await apiFetch(`${API}/${id}`, { method: "PUT", body: JSON.stringify(payload) });
      showToast("Équipement modifié");
    } else {
      await apiFetch(API + "/", { method: "POST", body: JSON.stringify(payload) });
      showToast("Équipement ajouté — DNS et proxy configurés");
    }
    closeModal();
    await loadDevices();
  } catch (err) {
    showFormError(err.message);
  } finally {
    btnSubmit.disabled = false;
  }
});

// ── Delete confirm ────────────────────────────────────────────────────────────

const confirmBackdrop = document.getElementById("confirm-backdrop");
const confirmText     = document.getElementById("confirm-text");

function openConfirm(device) {
  deleteTarget = device;
  confirmText.textContent = `Supprimer « ${device.project_name} » ? Cette action supprimera aussi l'enregistrement DNS Cloudflare.`;
  confirmBackdrop.hidden = false;
}

function closeConfirm() {
  confirmBackdrop.hidden = true;
  deleteTarget = null;
}

document.getElementById("confirm-cancel").addEventListener("click", closeConfirm);

document.getElementById("confirm-ok").addEventListener("click", async () => {
  if (!deleteTarget) return;
  const id = deleteTarget.id;
  const name = deleteTarget.project_name;
  closeConfirm();
  try {
    await apiFetch(`${API}/${id}`, { method: "DELETE" });
    showToast(`« ${name} » supprimé`);
    await loadDevices();
  } catch (err) {
    showToast(err.message, "error");
  }
});

// ── Mode modal ────────────────────────────────────────────────────────────────

function openModeModal(device) {
  modeTarget = device;
  document.getElementById("mode-device-name").textContent = device.project_name;
  document.getElementById("btn-close-public").hidden = !["public_temporary", "public"].includes(device.access_mode);
  document.getElementById("mode-backdrop").hidden = false;
}

function closeModeModal() {
  document.getElementById("mode-backdrop").hidden = true;
  modeTarget = null;
}

document.getElementById("mode-modal-close").addEventListener("click", closeModeModal);
document.getElementById("mode-cancel").addEventListener("click", closeModeModal);

document.querySelectorAll(".mode-btn").forEach(btn => {
  btn.addEventListener("click", async () => {
    if (!modeTarget) return;
    const mode = btn.dataset.mode;
    const duration = btn.dataset.duration || null;
    const payload = { access_mode: mode };
    if (duration) payload.duration = duration;

    btn.disabled = true;
    try {
      const updated = await apiFetch(`${API}/${modeTarget.id}/access-mode`, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      devices = devices.map(d => d.id === modeTarget.id ? updated : d);
      renderAll();
      updateStats();
      closeModeModal();
      const labels = { suspended: "suspendu", protected: "protégé", public_temporary: "public temporaire", public: "public permanent" };
      showToast(`${updated.project_name} → ${labels[mode] || mode}`);
    } catch (err) {
      showToast(err.message, "error");
    } finally {
      btn.disabled = false;
    }
  });
});

// ── Event delegation ──────────────────────────────────────────────────────────

document.getElementById("device-grid").addEventListener("click", async (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;

  const id = parseInt(btn.dataset.id, 10);
  const device = devices.find(d => d.id === id);
  if (!device) return;

  if (btn.classList.contains("btn-mode")) {
    openModeModal(device);
  } else if (btn.classList.contains("btn-edit")) {
    openModal(device);
  } else if (btn.classList.contains("btn-delete")) {
    openConfirm(device);
  } else if (btn.classList.contains("btn-refresh")) {
    btn.disabled = true;
    btn.textContent = "…";
    try {
      const updated = await apiFetch(`${API}/${id}/refresh`, { method: "POST" });
      devices = devices.map(d => d.id === id ? updated : d);
      renderAll();
      updateStats();
      showToast(`Statut : ${statusLabel(updated.status)}`);
    } catch (err) {
      showToast(err.message, "error");
    } finally {
      btn.disabled = false;
      btn.textContent = "↻ Tester";
    }
  }
});

// ── Buttons ───────────────────────────────────────────────────────────────────

document.getElementById("btn-add").addEventListener("click", () => openModal());
document.getElementById("btn-add-empty").addEventListener("click", () => openModal());
document.getElementById("modal-close").addEventListener("click", closeModal);
document.getElementById("btn-cancel").addEventListener("click", closeModal);

modal.addEventListener("click", (e) => { if (e.target === modal) closeModal(); });
confirmBackdrop.addEventListener("click", (e) => { if (e.target === confirmBackdrop) closeConfirm(); });
document.getElementById("mode-backdrop").addEventListener("click", (e) => {
  if (e.target === document.getElementById("mode-backdrop")) closeModeModal();
});

fieldSlug.addEventListener("input", updateSlugPreview);

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (!modal.hidden) closeModal();
    if (!confirmBackdrop.hidden) closeConfirm();
    if (!document.getElementById("mode-backdrop").hidden) closeModeModal();
  }
});

// ── Update modal ──────────────────────────────────────────────────────────────

const updateBackdrop = document.getElementById("update-backdrop");
const updateOutput   = document.getElementById("update-output");
const updateCloseBtn = document.getElementById("update-close-btn");
const updateBtn      = document.getElementById("btn-update");
const updateBadge    = document.getElementById("update-badge");

async function checkForUpdates() {
  try {
    const res = await fetch("/api/system/update-check", { credentials: "same-origin" });
    if (res.status === 401) return;
    const data = await res.json();
    const available = Boolean(data.update_available);
    updateBadge.hidden = !available;
    updateBtn.title = available
      ? `Mise à jour disponible (${data.commits_behind} commit${data.commits_behind > 1 ? "s" : ""})`
      : "Mettre à jour depuis GitHub";
  } catch (e) {
    // Vérification silencieuse — on ignore les échecs réseau ponctuels
  }
}

function startUpdateCheck() {
  clearInterval(updateCheckTimer);
  checkForUpdates();
  updateCheckTimer = setInterval(checkForUpdates, UPDATE_CHECK_INTERVAL);
}

updateBtn.addEventListener("click", async () => {
  updateOutput.textContent = "";
  updateCloseBtn.disabled = true;
  updateBackdrop.hidden = false;
  updateBadge.hidden = true;

  try {
    const res = await fetch("/api/system/update", {
      method: "POST",
      credentials: "same-origin",
    });

    const reader = res.body.getReader();
    const decoder = new TextDecoder();

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      updateOutput.textContent += decoder.decode(value, { stream: true });
      updateOutput.scrollTop = updateOutput.scrollHeight;
    }
  } catch (e) {
    updateOutput.textContent += `\n[Connexion interrompue — le serveur redémarre probablement]`;
  }

  updateCloseBtn.disabled = false;
});

document.getElementById("update-modal-close").addEventListener("click", () => {
  if (!updateCloseBtn.disabled) updateBackdrop.hidden = true;
});
updateCloseBtn.addEventListener("click", () => { updateBackdrop.hidden = true; });

// ── Terminal ──────────────────────────────────────────────────────────────────

const terminalBackdrop = document.getElementById("terminal-backdrop");
let term = null;
let termFit = null;
let termSocket = null;

function connectTerminalSocket() {
  if (termSocket && termSocket.readyState === WebSocket.OPEN) return;
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  termSocket = new WebSocket(`${proto}//${location.host}/api/system/terminal`);
  termSocket.binaryType = "arraybuffer";

  termSocket.addEventListener("open", () => sendResize());
  termSocket.addEventListener("message", (ev) => {
    const bytes = ev.data instanceof ArrayBuffer ? new Uint8Array(ev.data) : null;
    term.write(bytes ? new TextDecoder().decode(bytes) : ev.data);
  });
  termSocket.addEventListener("close", () => {
    term.write("\r\n\x1b[31m[Connexion terminale fermée]\x1b[0m\r\n");
  });
  termSocket.addEventListener("error", () => {
    term.write("\r\n\x1b[31m[Erreur de connexion terminale]\x1b[0m\r\n");
  });
}

function sendResize() {
  if (!termSocket || termSocket.readyState !== WebSocket.OPEN || !term) return;
  termSocket.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
}

function openTerminal() {
  terminalBackdrop.hidden = false;

  if (!term) {
    term = new Terminal({ cursorBlink: true, fontSize: 13, theme: { background: "#0a0c12" } });
    termFit = new FitAddon.FitAddon();
    term.loadAddon(termFit);
    term.open(document.getElementById("terminal-el"));
    term.onData((data) => {
      if (termSocket && termSocket.readyState === WebSocket.OPEN) {
        termSocket.send(new TextEncoder().encode(data));
      }
    });
  }

  connectTerminalSocket();
  setTimeout(() => { termFit.fit(); sendResize(); }, 50);
}

function closeTerminal() {
  terminalBackdrop.hidden = true;
  if (termSocket) { termSocket.close(); termSocket = null; }
}

document.getElementById("btn-terminal").addEventListener("click", openTerminal);
document.getElementById("terminal-modal-close").addEventListener("click", closeTerminal);
terminalBackdrop.addEventListener("click", (e) => { if (e.target === terminalBackdrop) closeTerminal(); });
window.addEventListener("resize", () => {
  if (!terminalBackdrop.hidden && termFit) { termFit.fit(); sendResize(); }
});

// ── Scan réseau ───────────────────────────────────────────────────────────────

const scanBackdrop     = document.getElementById("scan-backdrop");
const scanSubnet       = document.getElementById("scan-subnet");
const scanStartBtn     = document.getElementById("scan-start-btn");
const scanResults      = document.getElementById("scan-results");
const scanProgress     = document.getElementById("scan-progress");
const scanProgressFill = document.getElementById("scan-progress-fill");
const scanProgressText = document.getElementById("scan-progress-text");
let scanning = false;

// Devine la plage réseau à partir des équipements existants (sinon 192.168.1.0/24).
function guessSubnet() {
  const ipv4 = devices.map(d => d.local_ip).find(ip => /^\d+\.\d+\.\d+\.\d+$/.test(ip || ""));
  if (ipv4) {
    const parts = ipv4.split(".");
    return `${parts[0]}.${parts[1]}.${parts[2]}.0/24`;
  }
  return "192.168.1.0/24";
}

function openScan() {
  scanBackdrop.hidden = false;
  scanSubnet.value = guessSubnet();
}

function closeScan() {
  scanBackdrop.hidden = true;
}

function addScanItem(svc) {
  const item = document.createElement("div");
  item.className = "scan-item";
  const badgeClass = svc.is_web ? (svc.scheme === "https" ? "https" : "") : "raw";
  const badgeText = svc.is_web ? svc.scheme.toUpperCase() : "TCP";
  const label = svc.title || (svc.is_web ? "Interface web" : "Port ouvert (non-HTTP)");
  item.innerHTML = `
    <span class="scan-item-badge ${badgeClass}">${badgeText}</span>
    <div class="scan-item-main">
      <div class="scan-item-addr">${esc(svc.ip)}:${esc(String(svc.port))}</div>
      <div class="scan-item-title">${esc(label)}${svc.status ? ` · HTTP ${svc.status}` : ""}</div>
    </div>
    <button class="btn btn-secondary scan-add-btn">+ Ajouter</button>
  `;
  item.querySelector(".scan-add-btn").addEventListener("click", () => {
    closeScan();
    openModalPrefill(svc);
  });
  scanResults.appendChild(item);
}

async function runScan() {
  if (scanning) return;
  const subnet = scanSubnet.value.trim();
  if (!subnet) return;

  scanning = true;
  scanStartBtn.disabled = true;
  scanStartBtn.textContent = "Scan…";
  scanResults.innerHTML = "";
  scanProgress.hidden = false;
  scanProgressFill.style.width = "0%";
  scanProgressText.textContent = "Démarrage…";

  let foundCount = 0;

  try {
    const res = await fetch(`/api/system/scan-network?subnet=${encodeURIComponent(subnet)}`, {
      credentials: "same-origin",
    });
    if (res.status === 401) { window.location.href = "/auth/login"; return; }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop();
      for (const line of lines) {
        if (!line.trim()) continue;
        let evt;
        try { evt = JSON.parse(line); } catch { continue; }
        if (evt.type === "progress") {
          const pct = evt.total ? Math.round((evt.done / evt.total) * 100) : 0;
          scanProgressFill.style.width = `${pct}%`;
          scanProgressText.textContent = `${pct}% · ${foundCount} trouvé${foundCount > 1 ? "s" : ""}`;
        } else if (evt.type === "service") {
          foundCount++;
          addScanItem(evt);
          scanProgressText.textContent = `${foundCount} trouvé${foundCount > 1 ? "s" : ""}`;
        } else if (evt.type === "error") {
          showToast(evt.message, "error");
        } else if (evt.type === "complete") {
          scanProgressFill.style.width = "100%";
          scanProgressText.textContent = `Terminé · ${evt.found} trouvé${evt.found > 1 ? "s" : ""}`;
        }
      }
    }

    if (foundCount === 0) {
      scanResults.innerHTML = `<p class="scan-hint">Aucune interface web trouvée sur cette plage.</p>`;
    }
  } catch (e) {
    showToast("Scan interrompu", "error");
  } finally {
    scanning = false;
    scanStartBtn.disabled = false;
    scanStartBtn.textContent = "Scanner";
  }
}

document.getElementById("btn-scan").addEventListener("click", openScan);
document.getElementById("scan-modal-close").addEventListener("click", closeScan);
scanBackdrop.addEventListener("click", (e) => { if (e.target === scanBackdrop) closeScan(); });
scanStartBtn.addEventListener("click", runScan);
scanSubnet.addEventListener("keydown", (e) => { if (e.key === "Enter") runScan(); });

// ── Init ──────────────────────────────────────────────────────────────────────

loadDevices();
startAutoRefresh();
startUpdateCheck();
