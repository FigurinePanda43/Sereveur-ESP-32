// Gestion des utilisateurs secondaires (accès limité à certains services).
// S'appuie sur les helpers globaux définis dans app.js : apiFetch, esc,
// showToast, formatDate et le tableau global `devices`.

const USERS_API = "/api/users";

let users = [];
let userDeleteTarget = null;
let revealedUserIds = new Set();

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

function userStatusChip(u) {
  if (!u.enabled) return `<span class="chip chip-suspended">⏸ Désactivé</span>`;
  if (u.expired) return `<span class="chip chip-public-perm">⌛ Expiré</span>`;
  return `<span class="chip chip-active">✓ Actif</span>`;
}

function userServicesHtml(u) {
  if (!u.devices || u.devices.length === 0) {
    return `<span class="chip chip-neutral">Aucun service</span>`;
  }
  return u.devices
    .map((d) => `<span class="chip chip-protected">${esc(d.project_name)}</span>`)
    .join("");
}

function renderUserCard(u) {
  const card = document.createElement("article");
  const inactive = !u.enabled || u.expired;
  card.className = "card" + (inactive ? " card--muted" : "");
  card.dataset.id = u.id;

  const validity = u.valid_until
    ? `${u.expired ? "Expiré le" : "Jusqu'au"} ${formatDateUtc(u.valid_until)}`
    : "Sans limite de date";

  card.innerHTML = `
    <div class="card-top">
      <div class="card-heading">
        <h3 class="card-name">${esc(u.username)}</h3>
        ${u.description ? `<p class="card-desc">${esc(u.description)}</p>` : ""}
        ${userStatusChip(u)}
      </div>
    </div>
    <div>
      <p class="section-label">Services autorisés</p>
      <div class="chip-row">${userServicesHtml(u)}</div>
    </div>
    <div class="meta">
      <div class="meta-row">
        <span class="meta-key">Validité</span>
        <span class="meta-val${u.expired ? " meta-val--expired" : ""}">${esc(validity)}</span>
      </div>
      <div class="meta-row">
        <span class="meta-key">Créé le</span>
        <span class="meta-val">${formatDate(u.created_at)}</span>
      </div>
      <div class="meta-row">
        <span class="meta-key">Dernière connexion</span>
        <span class="meta-val">${u.last_login ? formatDateUtc(u.last_login) : "Jamais"}</span>
      </div>
    </div>
    <div class="card-actions">
      <button class="btn btn-secondary user-btn-edit" data-id="${u.id}" type="button">Modifier</button>
      <button class="btn btn-secondary btn-more user-btn-more" data-id="${u.id}" type="button"
              aria-label="Plus d'actions" aria-haspopup="menu" aria-expanded="false">•••</button>
    </div>
  `;
  return card;
}

function renderUsers() {
  const list = document.getElementById("user-list");
  const empty = document.getElementById("user-empty-state");

  Array.from(list.children).forEach((el) => {
    if (!el.classList.contains("empty")) el.remove();
  });

  if (users.length === 0) {
    empty.hidden = false;
    return;
  }
  empty.hidden = true;

  // Comme pour les équipements : seules les cartes nouvelles s'animent, sinon
  // chaque retour sur l'onglet rejouerait la cascade.
  const fresh = [];
  users.forEach((u) => {
    const card = renderUserCard(u);
    if (!revealedUserIds.has(u.id)) {
      card.classList.add("reveal");
      fresh.push(card);
    }
    list.appendChild(card);
  });
  revealedUserIds = new Set(users.map((u) => u.id));
  Motion.revealSequence(fresh);
}

async function loadUsers() {
  try {
    users = await apiFetch(USERS_API + "/");
    renderUsers();
  } catch (e) {
    showToast("Impossible de charger les utilisateurs", "error");
  }
}

// ── Feuille ajout/édition ────────────────────────────────────────────────────

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
    userDevicePicker.innerHTML = `<p class="hint">Aucun équipement disponible. Créez d'abord un équipement.</p>`;
    return;
  }
  userDevicePicker.innerHTML = devices
    .map((d) => `
      <label class="check-row">
        <input type="checkbox" value="${d.id}" ${selectedIds.includes(d.id) ? "checked" : ""}>
        <span class="picker-name">${esc(d.project_name)}</span>
        <span class="picker-slug">${esc(d.slug)}</span>
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

  Motion.presentSheet(userModal, { focus: user ? "#user-field-password" : "#user-field-name" });
}

function closeUserModal() {
  Motion.dismissSheet(userModal);
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
      userFormError.textContent = "Renseignez une date de validité ou activez « sans limite de date ».";
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
  userConfirmText.textContent =
    `« ${user.username} » ne pourra plus se connecter et son accès aux services sera immédiatement révoqué.`;
  Motion.presentSheet(userConfirmBackdrop, { focus: "#user-confirm-cancel" });
}

function closeUserConfirm() {
  Motion.dismissSheet(userConfirmBackdrop);
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
  } else if (btn.classList.contains("user-btn-more")) {
    Motion.showMenu(btn, [
      { label: "Modifier", glyph: "✎", action: () => openUserModal(user) },
      { separator: true },
      { label: "Supprimer", glyph: "🗑", danger: true, action: () => openUserConfirm(user) },
    ]);
  }
});

document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (Motion.isSheetOpen(userModal)) closeUserModal();
  if (Motion.isSheetOpen(userConfirmBackdrop)) closeUserConfirm();
});
