// Gestion des utilisateurs secondaires (accès limité à certains services).
// S'appuie sur les helpers globaux définis dans app.js : apiFetch, esc,
// showToast, formatDate et le tableau global `devices`.

const USERS_API = "/api/users";

let users = [];
let userDeleteTarget = null;

// ── Helpers dates ───────────────────────────────────────────────────────────

// Les dates du backend sont en UTC naïf ; on ajoute "Z" pour un affichage local correct.
function formatDateUtc(iso) {
  if (!iso) return "—";
  const s = /[Z+]/.test(iso) ? iso : iso + "Z";
  return new Date(s).toLocaleString("fr-FR", {
    day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

// UTC naïf → valeur pour <input type="datetime-local"> (heure locale).
function utcToLocalInput(iso) {
  const s = /[Z+]/.test(iso) ? iso : iso + "Z";
  const d = new Date(s);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// ── Rendu ────────────────────────────────────────────────────────────────────

function userStatusBadge(u) {
  if (!u.enabled) return `<span class="mode-badge badge-suspended">Désactivé</span>`;
  if (u.expired) return `<span class="mode-badge badge-public-perm">Expiré</span>`;
  return `<span class="mode-badge badge-online">Actif</span>`;
}

function userServicesHtml(u) {
  if (!u.devices || u.devices.length === 0) {
    return `<span class="user-chip user-chip--empty">Aucun service</span>`;
  }
  return u.devices
    .map((d) => `<span class="user-chip">${esc(d.project_name)}</span>`)
    .join("");
}

function renderUserCard(u) {
  const card = document.createElement("div");
  card.className = "user-card" + (u.enabled && !u.expired ? "" : " user-card--inactive");
  card.dataset.id = u.id;

  const validity = u.valid_until
    ? `<span class="${u.expired ? "user-expired" : ""}">Jusqu'au ${formatDateUtc(u.valid_until)}</span>`
    : "Sans limite de date";

  card.innerHTML = `
    <div class="card-header">
      <div style="flex:1;min-width:0;">
        <span class="card-name">${esc(u.username)}</span>
        ${userStatusBadge(u)}
      </div>
    </div>
    ${u.description ? `<p class="card-desc">${esc(u.description)}</p>` : ""}
    <div class="user-services">${userServicesHtml(u)}</div>
    <div class="card-meta">
      <div class="card-meta-row">
        <span class="meta-label">Validité</span>
        <span class="meta-value">${validity}</span>
      </div>
      <div class="card-meta-row">
        <span class="meta-label">Créé le</span>
        <span class="meta-value">${formatDate(u.created_at)}</span>
      </div>
      <div class="card-meta-row">
        <span class="meta-label">Connexion</span>
        <span class="meta-value">${u.last_login ? formatDateUtc(u.last_login) : "Jamais"}</span>
      </div>
    </div>
    <div class="card-actions">
      <button class="btn btn-secondary user-btn-edit" data-id="${u.id}">Modifier</button>
      <button class="btn btn-danger user-btn-delete" data-id="${u.id}">Supprimer</button>
    </div>
  `;
  return card;
}

function renderUsers() {
  const list = document.getElementById("user-list");
  const empty = document.getElementById("user-empty-state");

  Array.from(list.children).forEach((el) => {
    if (!el.classList.contains("empty-state")) el.remove();
  });

  if (users.length === 0) {
    empty.hidden = false;
    return;
  }
  empty.hidden = true;
  users.forEach((u) => list.appendChild(renderUserCard(u)));
}

async function loadUsers() {
  try {
    users = await apiFetch(USERS_API + "/");
    renderUsers();
  } catch (e) {
    showToast("Impossible de charger les utilisateurs", "error");
  }
}

// ── Modal ajout/édition ──────────────────────────────────────────────────────

const userModal        = document.getElementById("user-modal-backdrop");
const userForm         = document.getElementById("user-form");
const userFieldId      = document.getElementById("user-field-id");
const userFieldName    = document.getElementById("user-field-name");
const userFieldPw      = document.getElementById("user-field-password");
const userDevicePicker = document.getElementById("user-device-picker");
const userNoExpiry     = document.getElementById("user-no-expiry");
const userExpiryGroup  = document.getElementById("user-expiry-group");
const userFieldExpiry  = document.getElementById("user-field-expiry");
const userEnabledGroup = document.getElementById("user-enabled-group");
const userFieldEnabled = document.getElementById("user-field-enabled");
const userFormError    = document.getElementById("user-form-error");
const userBtnSubmit    = document.getElementById("user-btn-submit");

function renderDevicePicker(selectedIds = []) {
  if (!devices || devices.length === 0) {
    userDevicePicker.innerHTML = `<p class="field-hint">Aucun équipement disponible. Créez d'abord un équipement.</p>`;
    return;
  }
  userDevicePicker.innerHTML = devices
    .map((d) => `
      <label class="checkbox-label device-picker-item">
        <input type="checkbox" value="${d.id}" ${selectedIds.includes(d.id) ? "checked" : ""}>
        ${esc(d.project_name)} <span class="device-picker-slug">${esc(d.slug)}</span>
      </label>`)
    .join("");
}

function openUserModal(user = null) {
  userForm.reset();
  userFormError.hidden = true;

  if (user) {
    document.getElementById("user-modal-title").textContent = "Modifier l'utilisateur";
    userFieldId.value = user.id;
    userFieldName.value = user.username;
    userFieldName.disabled = true;
    document.getElementById("user-password-required").hidden = true;
    document.getElementById("user-password-hint").textContent = "Laisser vide pour conserver le mot de passe actuel.";
    renderDevicePicker((user.devices || []).map((d) => d.id));
    if (user.valid_until) {
      userNoExpiry.checked = false;
      userExpiryGroup.hidden = false;
      userFieldExpiry.value = utcToLocalInput(user.valid_until);
    } else {
      userNoExpiry.checked = true;
      userExpiryGroup.hidden = true;
    }
    userEnabledGroup.hidden = false;
    userFieldEnabled.checked = user.enabled;
  } else {
    document.getElementById("user-modal-title").textContent = "Ajouter un utilisateur";
    userFieldId.value = "";
    userFieldName.disabled = false;
    document.getElementById("user-password-required").hidden = false;
    document.getElementById("user-password-hint").textContent = "";
    renderDevicePicker([]);
    userNoExpiry.checked = true;
    userExpiryGroup.hidden = true;
    userEnabledGroup.hidden = true;
    userFieldEnabled.checked = true;
  }

  userModal.hidden = false;
  userFieldName.focus();
}

function closeUserModal() {
  userModal.hidden = true;
  userFieldName.disabled = false;
}

function selectedDeviceIds() {
  return Array.from(userDevicePicker.querySelectorAll("input[type=checkbox]:checked"))
    .map((cb) => parseInt(cb.value, 10));
}

userNoExpiry.addEventListener("change", () => {
  userExpiryGroup.hidden = userNoExpiry.checked;
});

userForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  userFormError.hidden = true;

  const id = userFieldId.value;
  const isEdit = Boolean(id);

  let validUntil = null;
  if (!userNoExpiry.checked) {
    if (!userFieldExpiry.value) {
      userFormError.textContent = "Renseignez une date de validité ou cochez « sans limite de date ».";
      userFormError.hidden = false;
      return;
    }
    validUntil = new Date(userFieldExpiry.value).toISOString();
  }

  const payload = {
    device_ids: selectedDeviceIds(),
    valid_until: validUntil,
    description: "",
  };

  const pw = userFieldPw.value.trim();

  try {
    userBtnSubmit.disabled = true;
    if (isEdit) {
      if (pw) payload.password = pw;
      payload.enabled = userFieldEnabled.checked;
      await apiFetch(`${USERS_API}/${id}`, { method: "PUT", body: JSON.stringify(payload) });
      showToast("Utilisateur modifié");
    } else {
      payload.username = userFieldName.value.trim();
      payload.password = pw;
      await apiFetch(USERS_API + "/", { method: "POST", body: JSON.stringify(payload) });
      showToast("Utilisateur créé");
    }
    closeUserModal();
    await loadUsers();
  } catch (err) {
    userFormError.textContent = err.message;
    userFormError.hidden = false;
  } finally {
    userBtnSubmit.disabled = false;
  }
});

// ── Suppression ──────────────────────────────────────────────────────────────

const userConfirmBackdrop = document.getElementById("user-confirm-backdrop");
const userConfirmText     = document.getElementById("user-confirm-text");

function openUserConfirm(user) {
  userDeleteTarget = user;
  userConfirmText.textContent = `Supprimer l'utilisateur « ${user.username} » ? Son accès aux services sera immédiatement révoqué.`;
  userConfirmBackdrop.hidden = false;
}

function closeUserConfirm() {
  userConfirmBackdrop.hidden = true;
  userDeleteTarget = null;
}

document.getElementById("user-confirm-cancel").addEventListener("click", closeUserConfirm);
document.getElementById("user-confirm-ok").addEventListener("click", async () => {
  if (!userDeleteTarget) return;
  const { id, username } = userDeleteTarget;
  closeUserConfirm();
  try {
    await apiFetch(`${USERS_API}/${id}`, { method: "DELETE" });
    showToast(`« ${username} » supprimé`);
    await loadUsers();
  } catch (err) {
    showToast(err.message, "error");
  }
});

// ── Événements ───────────────────────────────────────────────────────────────

document.getElementById("btn-add-user").addEventListener("click", () => openUserModal());
document.getElementById("btn-add-user-empty").addEventListener("click", () => openUserModal());
document.getElementById("user-modal-close").addEventListener("click", closeUserModal);
document.getElementById("user-btn-cancel").addEventListener("click", closeUserModal);

userModal.addEventListener("click", (e) => { if (e.target === userModal) closeUserModal(); });
userConfirmBackdrop.addEventListener("click", (e) => { if (e.target === userConfirmBackdrop) closeUserConfirm(); });

document.getElementById("user-list").addEventListener("click", (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;
  const id = parseInt(btn.dataset.id, 10);
  const user = users.find((u) => u.id === id);
  if (!user) return;

  if (btn.classList.contains("user-btn-edit")) {
    openUserModal(user);
  } else if (btn.classList.contains("user-btn-delete")) {
    openUserConfirm(user);
  }
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (!userModal.hidden) closeUserModal();
    if (!userConfirmBackdrop.hidden) closeUserConfirm();
  }
});
