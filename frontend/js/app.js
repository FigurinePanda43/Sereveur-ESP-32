const API = "/api/devices";
const REFRESH_INTERVAL = 30_000;

let devices = [];
let deleteTarget = null;
let refreshTimer = null;

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
  if (res.status === 401) { window.location.reload(); return; }
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

function renderCard(d) {
  const card = document.createElement("div");
  card.className = d.enabled ? "device-card" : "device-card device-card--disabled";
  card.dataset.id = d.id;

  const sc = d.enabled ? statusClass(d.status) : "unknown";
  const statusText = d.enabled ? statusLabel(d.status) : "Inactif";

  card.innerHTML = `
    <div class="card-header">
      <span class="card-name">${esc(d.project_name)}</span>
      <span class="status-dot ${sc}" title="${statusText}"></span>
    </div>
    ${d.description ? `<p class="card-desc">${esc(d.description)}</p>` : ""}
    <div class="card-meta">
      <div class="card-meta-row">
        <span class="meta-label">Statut</span>
        <span class="meta-value">${statusText}</span>
      </div>
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
      <button class="btn btn-ghost btn-toggle" data-id="${d.id}" title="${d.enabled ? "Désactiver la redirection" : "Activer la redirection"}">${d.enabled ? "⏸" : "▶"}</button>
      <button class="btn btn-ghost btn-refresh" data-id="${d.id}" ${d.enabled ? "" : "disabled"}>↻ Tester</button>
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
  const active = devices.filter(d => d.enabled);
  document.getElementById("stat-total").textContent    = devices.length;
  document.getElementById("stat-online").textContent   = active.filter(d => d.status === "online").length;
  document.getElementById("stat-slow").textContent     = active.filter(d => d.status === "slow").length;
  document.getElementById("stat-offline").textContent  = active.filter(d => d.status === "offline").length;
  document.getElementById("stat-disabled").textContent = devices.filter(d => !d.enabled).length;
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

// ── Modal ─────────────────────────────────────────────────────────────────────

const modal         = document.getElementById("modal-backdrop");
const modalTitle    = document.getElementById("modal-title");
const form          = document.getElementById("device-form");
const fieldId       = document.getElementById("field-id");
const fieldName     = document.getElementById("field-name");
const fieldSlug     = document.getElementById("field-slug");
const fieldIp       = document.getElementById("field-ip");
const fieldPort     = document.getElementById("field-port");
const fieldDesc     = document.getElementById("field-desc");
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
    fieldDesc.value  = device.description;
    updateSlugPreview();
  } else {
    modalTitle.textContent = "Ajouter un équipement";
    fieldId.value = "";
    fieldSlug.disabled = false;
    fieldPort.value = "80";
  }

  modal.hidden = false;
  fieldName.focus();
}

function closeModal() {
  modal.hidden = true;
  fieldSlug.disabled = false;
}

function updateSlugPreview() {
  const slug = fieldSlug.value.trim();
  slugPreview.textContent = slug ? `→ https://${slug}.votre-domaine.com` : "";
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
    project_name: fieldName.value.trim(),
    local_ip:     fieldIp.value.trim(),
    local_port:   parseInt(fieldPort.value, 10),
    description:  fieldDesc.value.trim(),
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

// ── Event delegation ──────────────────────────────────────────────────────────

document.getElementById("device-grid").addEventListener("click", async (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;

  const id = parseInt(btn.dataset.id, 10);
  const device = devices.find(d => d.id === id);
  if (!device) return;

  if (btn.classList.contains("btn-toggle")) {
    btn.disabled = true;
    try {
      const updated = await apiFetch(`${API}/${id}/toggle`, { method: "POST" });
      devices = devices.map(d => d.id === id ? updated : d);
      renderAll();
      updateStats();
      showToast(updated.enabled ? "Redirection activée" : "Redirection désactivée");
    } catch (err) {
      showToast(err.message, "error");
    } finally {
      btn.disabled = false;
    }
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

fieldSlug.addEventListener("input", updateSlugPreview);

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (!modal.hidden) closeModal();
    if (!confirmBackdrop.hidden) closeConfirm();
  }
});

// ── Init ──────────────────────────────────────────────────────────────────────

loadDevices();
startAutoRefresh();
