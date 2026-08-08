const API = "/api/devices";
const REFRESH_INTERVAL = 30_000;
const UPDATE_CHECK_INTERVAL = 10 * 60_000;

let devices = [];
let deleteTarget = null;
let modeTarget = null;
let refreshTimer = null;
let updateCheckTimer = null;
let revealedDeviceIds = new Set();

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

// ── Notification éphémère ─────────────────────────────────────────────────────

// Le bandeau monte sur un ressort et redescend sur le même chemin : ce qui
// arrive par le bas repart par le bas.
const toastEl = document.getElementById("toast");
const toastSpring = new Motion.SpringValue(0, {
  damping: 0.85,
  response: 0.4,
  precision: 0.002,
  onUpdate: (t) => {
    toastEl.style.opacity = Math.min(1, t * 1.4).toFixed(3);
    toastEl.style.transform = `translate3d(-50%, ${((1 - t) * 24).toFixed(2)}px, 0) scale(${(0.96 + 0.04 * t).toFixed(4)})`;
  },
});

function showToast(msg, type = "success") {
  const glyph = type === "error" ? "⚠" : "✓";
  toastEl.className = `toast ${type}`;
  toastEl.innerHTML = `<span class="toast-glyph" aria-hidden="true">${glyph}</span><span class="toast-text">${esc(msg)}</span>`;
  toastEl.setAttribute("role", type === "error" ? "alert" : "status");

  if (toastEl.hidden) {
    toastEl.hidden = false;
    toastSpring.set(0);
  }
  toastSpring.to(1);

  clearTimeout(toastEl._timer);
  toastEl._timer = setTimeout(() => {
    toastSpring.to(0, {
      damping: 1,
      response: 0.3,
      onRest: () => { toastEl.hidden = true; },
    });
  }, 3500);
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

const MODE_LABELS = {
  suspended: "Suspendu",
  protected: "Protégé",
  public_temporary: "Public temporaire",
  public: "Public permanent",
};

const MODE_CHIPS = {
  suspended: "chip-suspended",
  protected: "chip-protected",
  public_temporary: "chip-public",
  public: "chip-public-perm",
};

const MODE_GLYPHS = {
  suspended: "⏸",
  protected: "🔒",
  public_temporary: "🌐",
  public: "🌐",
};

function modeChip(d) {
  // Un service suspendu affiche déjà « Suspendu » comme statut : répéter
  // l'information dans une étiquette n'ajoute rien.
  if (d.access_mode === "suspended") return "";
  const label = MODE_LABELS[d.access_mode] || d.access_mode;
  const glyph = MODE_GLYPHS[d.access_mode] || "";
  return `<span class="chip ${MODE_CHIPS[d.access_mode] || "chip-neutral"}">${glyph} ${esc(label)}</span>`;
}

function exposureNotice(d) {
  if (d.access_mode === "public") {
    return `<div class="notice notice--alert"><span aria-hidden="true">⚠</span><span>Accessible sans authentification, sans expiration.</span></div>`;
  }
  if (d.access_mode !== "public_temporary" || !d.public_until) return "";
  const until = new Date(d.public_until).toLocaleString("fr-FR", {
    day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
  return `<div class="notice"><span aria-hidden="true">⚠</span><span>Accessible sans authentification jusqu'au ${esc(until)}.</span></div>`;
}

function cardClass(d) {
  if (d.access_mode === "suspended") return "card card--muted";
  if (d.access_mode === "public_temporary") return "card card--warn";
  if (d.access_mode === "public") return "card card--alert";
  return "card";
}

function renderCard(d) {
  const card = document.createElement("article");
  card.className = cardClass(d);
  card.dataset.id = d.id;

  const isSuspended = d.access_mode === "suspended";
  const sc = isSuspended ? "unknown" : statusClass(d.status);
  const statusText = isSuspended ? "Suspendu" : statusLabel(d.status);
  const hasLink = Boolean(d.public_url) && !isSuspended;

  card.innerHTML = `
    <div class="card-top">
      <div class="card-heading">
        <h3 class="card-name">${esc(d.project_name)}</h3>
        ${d.description ? `<p class="card-desc">${esc(d.description)}</p>` : ""}
        ${modeChip(d)}
      </div>
      <span class="status status-${sc}">
        <span class="status-dot" aria-hidden="true"></span>${esc(statusText)}
      </span>
    </div>
    ${exposureNotice(d)}
    <div class="meta">
      ${!isSuspended && ["offline", "slow"].includes(d.status) && d.status_detail ? `
      <div class="meta-row">
        <span class="meta-key">Détail</span>
        <span class="meta-val meta-val--detail">${esc(d.status_detail)}</span>
      </div>` : ""}
      <div class="meta-row">
        <span class="meta-key">Adresse locale</span>
        <span class="meta-val t-mono">${esc(d.local_protocol || "http")}://${esc(d.local_ip)}:${esc(String(d.local_port))}</span>
      </div>
      <div class="meta-row">
        <span class="meta-key">Créé le</span>
        <span class="meta-val">${formatDate(d.created_at)}</span>
      </div>
      <div class="meta-row">
        <span class="meta-key">Vu le</span>
        <span class="meta-val">${formatDate(d.last_seen)}</span>
      </div>
    </div>
    ${d.public_url ? `
    <div class="card-link">
      <span aria-hidden="true">🔗</span>
      <a href="${esc(d.public_url)}" target="_blank" rel="noopener">${esc(d.public_url)}</a>
    </div>` : ""}
    <div class="card-actions">
      ${hasLink
        ? `<a class="btn btn-tinted" href="${esc(d.public_url)}" target="_blank" rel="noopener">Ouvrir ↗</a>`
        : `<button class="btn btn-secondary btn-refresh" data-id="${d.id}" type="button" ${isSuspended ? "disabled" : ""}>Tester</button>`}
      <button class="btn btn-secondary btn-mode" data-id="${d.id}" type="button">Mode</button>
      <button class="btn btn-secondary btn-more" data-id="${d.id}" type="button"
              aria-label="Plus d'actions" aria-haspopup="menu" aria-expanded="false">•••</button>
    </div>
  `;
  return card;
}

function renderAll() {
  const grid = document.getElementById("device-grid");
  const empty = document.getElementById("empty-state");

  // On conserve l'état vide dans le DOM, on ne retire que les cartes.
  Array.from(grid.children).forEach(el => {
    if (!el.classList.contains("empty")) el.remove();
  });

  if (devices.length === 0) {
    empty.hidden = false;
    return;
  }
  empty.hidden = true;

  // Le rafraîchissement automatique reconstruit la grille toutes les 30 s.
  // Seules les cartes réellement nouvelles s'animent : rejouer la cascade à
  // chaque sondage transformerait le tableau de bord en clignotant.
  const fresh = [];
  devices.forEach(d => {
    const card = renderCard(d);
    if (!revealedDeviceIds.has(d.id)) {
      card.classList.add("reveal");
      fresh.push(card);
    }
    grid.appendChild(card);
  });
  revealedDeviceIds = new Set(devices.map(d => d.id));
  Motion.revealSequence(fresh);
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

// ── Feuille ajout/modification ────────────────────────────────────────────────

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

  Motion.presentSheet(modal, { focus: "#field-name" });
}

function closeModal() {
  Motion.dismissSheet(modal);
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
  slugPreview.textContent = slug ? `https://${slug}.iot.votre-domaine.com` : "";
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

// ── Confirmation de suppression ───────────────────────────────────────────────

const confirmBackdrop = document.getElementById("confirm-backdrop");
const confirmText     = document.getElementById("confirm-text");

function openConfirm(device) {
  deleteTarget = device;
  confirmText.textContent =
    `« ${device.project_name} » sera retiré du proxy et son enregistrement DNS Cloudflare supprimé. L'équipement lui-même n'est pas modifié.`;
  Motion.presentSheet(confirmBackdrop, { focus: "#confirm-cancel" });
}

function closeConfirm() {
  Motion.dismissSheet(confirmBackdrop);
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

// ── Feuille mode d'accès ──────────────────────────────────────────────────────

const modeBackdrop = document.getElementById("mode-backdrop");

function openModeModal(device) {
  modeTarget = device;
  document.getElementById("mode-device-name").textContent = device.project_name;
  document.getElementById("btn-close-public").hidden =
    !["public_temporary", "public"].includes(device.access_mode);

  // Le mode courant porte une coche : on voit où l'on est avant de choisir.
  modeBackdrop.querySelectorAll(".option-row.mode-btn").forEach(row => {
    row.classList.toggle("is-current", row.dataset.mode === device.access_mode && !row.dataset.duration);
  });

  Motion.presentSheet(modeBackdrop, { focus: "#mode-cancel" });
}

function closeModeModal() {
  Motion.dismissSheet(modeBackdrop);
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

    const target = modeTarget;
    btn.disabled = true;
    try {
      const updated = await apiFetch(`${API}/${target.id}/access-mode`, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      devices = devices.map(d => d.id === target.id ? updated : d);
      renderAll();
      updateStats();
      closeModeModal();
      showToast(`${updated.project_name} → ${(MODE_LABELS[mode] || mode).toLowerCase()}`);
    } catch (err) {
      showToast(err.message, "error");
    } finally {
      btn.disabled = false;
    }
  });
});

// ── Test de connexion ─────────────────────────────────────────────────────────

async function testDevice(id) {
  const card = document.querySelector(`.card[data-id="${id}"]`);
  // Le retour commence à l'instant du clic, pas à l'arrivée de la réponse.
  if (card) card.classList.add("is-testing");
  try {
    const updated = await apiFetch(`${API}/${id}/refresh`, { method: "POST" });
    devices = devices.map(d => d.id === id ? updated : d);
    renderAll();
    updateStats();
    showToast(`${updated.project_name} — ${statusLabel(updated.status).toLowerCase()}`);
  } catch (err) {
    showToast(err.message, "error");
    if (card) card.classList.remove("is-testing");
  }
}

// ── Délégation d'événements sur les cartes ────────────────────────────────────

document.getElementById("device-grid").addEventListener("click", (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;

  const id = parseInt(btn.dataset.id, 10);
  const device = devices.find(d => d.id === id);
  if (!device) return;

  if (btn.classList.contains("btn-mode")) {
    openModeModal(device);
  } else if (btn.classList.contains("btn-refresh")) {
    testDevice(id);
  } else if (btn.classList.contains("btn-more")) {
    const isSuspended = device.access_mode === "suspended";
    const entries = [];
    // « Tester » n'est proposé ici que s'il n'est pas déjà sur la carte.
    if (device.public_url && !isSuspended) {
      entries.push({ label: "Tester la connexion", glyph: "↻", action: () => testDevice(id) });
    }
    entries.push({ label: "Modifier", glyph: "✎", action: () => openModal(device) });
    entries.push({ separator: true });
    entries.push({ label: "Supprimer", glyph: "🗑", danger: true, action: () => openConfirm(device) });
    Motion.showMenu(btn, entries);
  }
});

// ── Boutons ───────────────────────────────────────────────────────────────────

document.getElementById("btn-add").addEventListener("click", () => openModal());
document.getElementById("btn-add-empty").addEventListener("click", () => openModal());
document.getElementById("modal-close").addEventListener("click", closeModal);
document.getElementById("btn-cancel").addEventListener("click", closeModal);

// Clic sur le voile = fermeture. Le geste part d'où il tombe.
modal.addEventListener("click", (e) => { if (e.target === modal) closeModal(); });
confirmBackdrop.addEventListener("click", (e) => { if (e.target === confirmBackdrop) closeConfirm(); });
modeBackdrop.addEventListener("click", (e) => { if (e.target === modeBackdrop) closeModeModal(); });

fieldSlug.addEventListener("input", updateSlugPreview);

document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (Motion.isSheetOpen(modal)) closeModal();
  if (Motion.isSheetOpen(confirmBackdrop)) closeConfirm();
  if (Motion.isSheetOpen(modeBackdrop)) closeModeModal();
});

// ── Mise à jour ───────────────────────────────────────────────────────────────

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
    const toolBadge = document.getElementById("tool-update-badge");
    if (toolBadge) toolBadge.hidden = !available;
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
  updateBadge.hidden = true;
  Motion.presentSheet(updateBackdrop, { focus: false });

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

function closeUpdate() {
  if (updateCloseBtn.disabled) return;
  Motion.dismissSheet(updateBackdrop);
}

document.getElementById("update-modal-close").addEventListener("click", closeUpdate);
updateCloseBtn.addEventListener("click", () => Motion.dismissSheet(updateBackdrop));
updateBackdrop.addEventListener("click", (e) => { if (e.target === updateBackdrop) closeUpdate(); });

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
  Motion.presentSheet(terminalBackdrop, { focus: false });

  if (!term) {
    term = new Terminal({ cursorBlink: true, fontSize: 13, theme: { background: "#0b0b0d" } });
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
  // On attend la fin de la mise en place de la feuille pour mesurer.
  setTimeout(() => { termFit.fit(); sendResize(); term.focus(); }, 220);
}

function closeTerminal() {
  Motion.dismissSheet(terminalBackdrop);
  if (termSocket) { termSocket.close(); termSocket = null; }
}

document.getElementById("btn-terminal").addEventListener("click", openTerminal);
document.getElementById("terminal-modal-close").addEventListener("click", closeTerminal);
terminalBackdrop.addEventListener("click", (e) => { if (e.target === terminalBackdrop) closeTerminal(); });
window.addEventListener("resize", () => {
  if (Motion.isSheetOpen(terminalBackdrop) && termFit) { termFit.fit(); sendResize(); }
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
  scanSubnet.value = guessSubnet();
  Motion.presentSheet(scanBackdrop, { focus: "#scan-subnet" });
}

function closeScan() {
  Motion.dismissSheet(scanBackdrop);
}

function addScanItem(svc) {
  const item = document.createElement("div");
  item.className = "scan-item reveal";
  const badgeClass = svc.is_web ? (svc.scheme === "https" ? "https" : "") : "raw";
  const badgeText = svc.is_web ? svc.scheme.toUpperCase() : "TCP";
  const label = svc.title || (svc.is_web ? "Interface web" : "Port ouvert (non-HTTP)");
  item.innerHTML = `
    <span class="scan-badge ${badgeClass}">${esc(badgeText)}</span>
    <div class="scan-item-main">
      <div class="scan-item-addr">${esc(svc.ip)}:${esc(String(svc.port))}</div>
      <div class="scan-item-title">${esc(label)}${svc.status ? ` · HTTP ${esc(String(svc.status))}` : ""}</div>
    </div>
    <button class="btn btn-tinted scan-item-add" type="button">Ajouter</button>
  `;
  item.querySelector(".scan-item-add").addEventListener("click", () => {
    closeScan();
    openModalPrefill(svc);
  });
  scanResults.appendChild(item);
  Motion.revealSequence([item]);
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
      scanResults.innerHTML = `<p class="scan-empty">Aucune interface web trouvée sur cette plage.</p>`;
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

document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (Motion.isSheetOpen(scanBackdrop)) closeScan();
  if (Motion.isSheetOpen(terminalBackdrop)) closeTerminal();
  if (Motion.isSheetOpen(updateBackdrop)) closeUpdate();
});

// ── Navigation ────────────────────────────────────────────────────────────────

function showView(name) {
  document.querySelectorAll(".view").forEach(v => { v.hidden = v.id !== `view-${name}`; });
  if (name === "users" && typeof loadUsers === "function") loadUsers();
}

const segmented = Motion.installSegmented(
  document.getElementById("main-nav"),
  (item) => showView(item.dataset.view)
);

// ── Apparence ─────────────────────────────────────────────────────────────────

const themeBtn = document.getElementById("btn-theme");
const THEME_GLYPHS = { auto: "◐", light: "☀", dark: "☾" };
const THEME_LABELS = { auto: "système", light: "clair", dark: "sombre" };

function paintThemeButton(theme) {
  themeBtn.textContent = THEME_GLYPHS[theme];
  themeBtn.title = `Apparence : ${THEME_LABELS[theme]}`;
  themeBtn.setAttribute("aria-label", `Apparence : ${THEME_LABELS[theme]}. Changer.`);
}

themeBtn.addEventListener("click", () => {
  const next = Motion.theme.cycle();
  paintThemeButton(next);
  segmented.refresh();
});

// ── Init ──────────────────────────────────────────────────────────────────────

paintThemeButton(Motion.theme.current());
Motion.installPressFeedback();
Motion.installScrollEdge(document.getElementById("chrome"));
requestAnimationFrame(() => segmented.refresh());

loadDevices();
startAutoRefresh();
startUpdateCheck();
