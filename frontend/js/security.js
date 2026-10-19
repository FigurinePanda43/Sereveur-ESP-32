// Onglet Sécurité : IPBan (bannissements, listes d'accès, filtrage par pays,
// règles anti-force brute, compte-rendu et journal).
// S'appuie sur les helpers globaux d'app.js (apiFetch, esc, showToast) et de
// users.js (formatDateUtc).

const SEC_API = "/api/security";
const SEC_REFRESH_INTERVAL = 30_000;

let secSettings = null;
let secClient = null;
let secCounts = { allow: 0, deny: 0, active_bans: 0 };
let secPeriod = "24h";
let secJournalKind = "all";
let secJournalBefore = null;
let secRefreshTimer = null;

// ── Pays ─────────────────────────────────────────────────────────────────────

// ISO 3166-1 alpha-2, plus XK (Kosovo, utilisé par Cloudflare), T1 (Tor) et XX (inconnu).
const SEC_COUNTRY_CODES = (
  "AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW " +
  "BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI " +
  "FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN " +
  "IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME " +
  "MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF " +
  "PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV " +
  "SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS XK " +
  "YE YT ZA ZM ZW T1 XX"
).split(" ");

const secRegionNames = (() => {
  try { return new Intl.DisplayNames(["fr"], { type: "region" }); } catch { return null; }
})();

function countryName(cc) {
  if (!cc) return "Pays inconnu";
  if (cc === "XX") return "Inconnu";
  if (cc === "T1") return "Réseau Tor";
  if (cc === "XK") return "Kosovo";
  try { return (secRegionNames && secRegionNames.of(cc)) || cc; } catch { return cc; }
}

function countryFlag(cc) {
  if (!cc || cc === "XX") return "🏳";
  if (cc === "T1") return "🧅";
  if (!/^[A-Z]{2}$/.test(cc)) return "🏳";
  return String.fromCodePoint(...[...cc].map((c) => 0x1f1e6 + c.charCodeAt(0) - 65));
}

const SEC_COUNTRIES = SEC_COUNTRY_CODES
  .map((code) => ({ code, name: countryName(code) }))
  .sort((a, b) => a.name.localeCompare(b.name, "fr"));

// ── Formatage ────────────────────────────────────────────────────────────────

const fmtNumber = (n) => Number(n || 0).toLocaleString("fr-FR");

// esc() ne neutralise pas les guillemets : indispensable dans un attribut, où
// une IP non analysable (en-tête forgé) pourrait sinon en sortir.
const escAttr = (v) => esc(v).replace(/"/g, "&quot;").replace(/'/g, "&#39;");

function fmtMinutes(m) {
  m = Math.round(m);
  if (m < 60) return `${m} min`;
  if (m < 1440) {
    const h = Math.floor(m / 60), r = m % 60;
    return r ? `${h} h ${String(r).padStart(2, "0")}` : `${h} h`;
  }
  const d = m / 1440;
  return Number.isInteger(d) ? `${d} j` : `${(Math.round(d * 10) / 10).toLocaleString("fr-FR")} j`;
}

function utcDate(iso) {
  return new Date(/[Z+]/.test(iso) ? iso : iso + "Z");
}

function fmtRelative(iso) {
  if (!iso) return "—";
  const diff = (Date.now() - utcDate(iso).getTime()) / 1000;
  if (diff < 60) return "à l'instant";
  if (diff < 3600) return `il y a ${Math.floor(diff / 60)} min`;
  if (diff < 86400) return `il y a ${Math.floor(diff / 3600)} h`;
  return formatDateUtc(iso);
}

const SEC_MODE_LABELS = { standard: "Standard", whitelist: "Liste blanche uniquement" };

function countrySummary(s) {
  if (s.country_mode === "off" || !s.countries.length) return "aucun filtrage par pays";
  const names = s.countries.slice(0, 3).map(countryName).join(", ");
  const more = s.countries.length > 3 ? ` +${s.countries.length - 3}` : "";
  return s.country_mode === "allow" ? `seulement ${names}${more}` : `${names}${more} bloqué${s.countries.length > 1 ? "s" : ""}`;
}

function rulesSummary(s) {
  if (!s.bruteforce_enabled) return "Bannissement automatique désactivé";
  const r = s.rules[0];
  const first = r ? `${r.max_failures} échecs en ${fmtMinutes(r.window_minutes)} → ${fmtMinutes(r.ban_minutes)}` : "";
  const extra = s.rules.length > 1 ? ` · ${s.rules.length - 1} autre${s.rules.length > 2 ? "s" : ""} palier${s.rules.length > 2 ? "s" : ""}` : "";
  const esc8 = s.escalation_factor > 1 ? ` · récidive ×${String(s.escalation_factor).replace(".", ",")}` : "";
  return first + extra + esc8;
}

// ── Réglages & état ──────────────────────────────────────────────────────────

const secEnabled = document.getElementById("sec-enabled");

function renderSecStatus() {
  const s = secSettings;
  if (!s) return;
  secEnabled.checked = s.enabled;
  document.getElementById("sec-forced-off").hidden = !s.forced_off;

  const summary = document.getElementById("sec-status-summary");
  if (!s.enabled) {
    summary.textContent = "Désactivée : aucune adresse n'est bloquée, les tentatives restent journalisées.";
  } else {
    const scope = s.apply_to_services ? "portail et équipements" : "portail uniquement";
    summary.textContent = `${SEC_MODE_LABELS[s.mode]} · ${countrySummary(s)} · ${scope}`;
  }
  document.getElementById("sec-status").classList.toggle("card--warn", !s.enabled || s.forced_off);

  const c = secClient;
  const chip = c.allowed
    ? `<span class="chip chip-active">✓ ${esc(c.code === "allowed" ? "Autorisée" : c.label)}</span>`
    : `<span class="chip chip-public-perm">⛔ ${esc(c.label)}</span>`;
  const where = c.country ? ` · ${countryFlag(c.country)} ${esc(countryName(c.country))}` : "";
  document.getElementById("sec-client").innerHTML =
    `<span class="t-mono">${esc(c.ip)}</span>${where} ${chip}`;

  document.getElementById("sec-mode-desc").textContent =
    `${SEC_MODE_LABELS[s.mode]} · ${s.apply_to_services ? "portail et équipements" : "portail uniquement"}` +
    (s.exempt_private ? " · réseau local exempté" : "");
  const cd = countrySummary(s);
  document.getElementById("sec-country-desc").textContent = cd.charAt(0).toUpperCase() + cd.slice(1);
  document.getElementById("sec-rules-desc").textContent = rulesSummary(s);
  document.getElementById("sec-allow-count").textContent = secCounts.allow;
  document.getElementById("sec-deny-count").textContent = secCounts.deny;
}

async function loadSecSettings() {
  const data = await apiFetch(`${SEC_API}/settings`);
  if (!data) return;
  secSettings = data.settings;
  secClient = data.client;
  secCounts = data.counts;
  renderSecStatus();
}

async function saveSecSettings(patch) {
  const data = await apiFetch(`${SEC_API}/settings`, { method: "PUT", body: JSON.stringify(patch) });
  secSettings = data.settings;
  secClient = data.client;
  secCounts = data.counts;
  renderSecStatus();
  return data;
}

secEnabled.addEventListener("change", async () => {
  const wanted = secEnabled.checked;
  try {
    await saveSecSettings({ enabled: wanted });
    showToast(wanted ? "Protection IPBan activée" : "Protection IPBan désactivée");
    loadSecJournal(true);
  } catch (err) {
    secEnabled.checked = !wanted;
    showToast(err.message, "error");
  }
});

// ── Compte-rendu ─────────────────────────────────────────────────────────────

const secChart = document.getElementById("sec-chart");
const secChartAxis = document.getElementById("sec-chart-axis");
const secChartTip = document.getElementById("sec-chart-tip");

function bucketLabel(start, period, long = false) {
  const d = utcDate(start);
  if (period === "24h") {
    const h = d.getHours();
    return long ? `${h} h – ${(h + 1) % 24} h` : `${h} h`;
  }
  return d.toLocaleDateString("fr-FR", long ? { weekday: "long", day: "numeric", month: "long" } : { day: "numeric", month: "short" });
}

function renderChart(timeline, period) {
  const max = Math.max(1, ...timeline.map((b) => b.blocked + b.failed));
  const total = timeline.reduce((n, b) => n + b.blocked + b.failed, 0);
  // Graduation lisible : un repère par 6 h, par jour (7 j) ou tous les 5 jours.
  const every = period === "24h" ? 6 : period === "7d" ? 1 : 5;

  secChart.innerHTML = `
    <span class="viz-max" aria-hidden="true">${fmtNumber(max)}</span>
    ${total === 0 ? `<span class="viz-empty">Aucune attaque sur la période.</span>` : ""}
    <div class="viz-cols">
      ${timeline.map((b, i) => {
        const hb = (b.blocked / max) * 100;
        const hf = (b.failed / max) * 100;
        const label = `${bucketLabel(b.start, period, true)} : ${b.blocked} requête(s) bloquée(s), ${b.failed} échec(s)` +
          (b.bans ? `, ${b.bans} bannissement(s)` : "");
        return `
        <button class="viz-col" type="button" data-i="${i}" aria-label="${escAttr(label)}">
          <span class="viz-stack">
            ${b.failed ? `<span class="viz-seg viz-seg--2" style="height:${hf.toFixed(2)}%"></span>` : ""}
            ${b.blocked ? `<span class="viz-seg viz-seg--1" style="height:${hb.toFixed(2)}%"></span>` : ""}
          </span>
        </button>`;
      }).join("")}
    </div>`;

  secChartAxis.innerHTML = timeline
    .map((b, i) => `<span>${(timeline.length - 1 - i) % every === 0 ? esc(bucketLabel(b.start, period)) : ""}</span>`)
    .join("");

  document.getElementById("sec-chart-table").innerHTML = timeline
    .map((b) => `<tr><th scope="row">${esc(bucketLabel(b.start, period, true))}</th><td>${fmtNumber(b.blocked)}</td><td>${fmtNumber(b.failed)}</td><td>${fmtNumber(b.bans)}</td></tr>`)
    .join("");

  secChart.querySelectorAll(".viz-col").forEach((col) => {
    const show = () => {
      const b = timeline[Number(col.dataset.i)];
      secChartTip.innerHTML = `
        <strong>${esc(bucketLabel(b.start, period, true))}</strong>
        <span class="viz-tip-row"><span class="viz-swatch viz-swatch--1"></span>Requêtes bloquées<b>${fmtNumber(b.blocked)}</b></span>
        <span class="viz-tip-row"><span class="viz-swatch viz-swatch--2"></span>Échecs de connexion<b>${fmtNumber(b.failed)}</b></span>
        ${b.bans ? `<span class="viz-tip-row"><span class="viz-swatch"></span>Bannissements<b>${fmtNumber(b.bans)}</b></span>` : ""}`;
      secChartTip.hidden = false;
      const host = secChart.parentElement.getBoundingClientRect();
      const r = col.getBoundingClientRect();
      const tipW = secChartTip.offsetWidth;
      // À côté de la colonne survolée (à droite, ou à gauche près du bord),
      // en haut du tracé : la barre elle-même reste visible.
      const center = r.left - host.left + r.width / 2;
      let left = center + r.width / 2 + 8;
      if (left + tipW > host.width - 8) left = center - r.width / 2 - tipW - 8;
      secChartTip.style.left = `${Math.max(8, left)}px`;
      secChartTip.style.top = `${secChart.offsetTop}px`;
      col.classList.add("is-hover");
    };
    const hide = () => { secChartTip.hidden = true; col.classList.remove("is-hover"); };
    col.addEventListener("pointerenter", show);
    col.addEventListener("pointerleave", hide);
    col.addEventListener("focus", show);
    col.addEventListener("blur", hide);
  });
}

function rankList(el, rows, nameFn, emptyText) {
  if (!rows.length) {
    el.innerHTML = `<li class="rank-empty">${esc(emptyText)}</li>`;
    return;
  }
  const max = Math.max(...rows.map((r) => r.value));
  el.innerHTML = rows.map((r) => `
    <li class="rank-row">
      ${nameFn(r)}
      <span class="rank-val">${fmtNumber(r.value)}</span>
      <span class="rank-bar" style="--w:${((r.value / max) * 100).toFixed(1)}%" aria-hidden="true"></span>
    </li>`).join("");
}

let secTopIps = [];

function renderReport(rep) {
  const t = rep.totals;
  document.getElementById("sec-stat-bans").textContent = fmtNumber(t.active_bans);
  document.getElementById("sec-stat-failed").textContent = fmtNumber(t.failed_logins);
  document.getElementById("sec-stat-blocked").textContent = fmtNumber(t.blocked_requests);
  document.getElementById("sec-stat-attackers").textContent = fmtNumber(t.unique_attackers);

  renderChart(rep.timeline, rep.period);

  secTopIps = rep.top_ips;
  rankList(
    document.getElementById("sec-top-ips"),
    rep.top_ips.map((r) => ({ ...r, value: r.failed + r.blocked })),
    (r) => `<button class="rank-name rank-name--action" type="button" data-ip="${escAttr(r.ip)}" aria-haspopup="menu"
                title="${escAttr(`${r.failed} échec(s), ${r.blocked} requête(s) bloquée(s)`)}">
              <span aria-hidden="true">${countryFlag(r.country)}</span><span class="t-mono">${esc(r.ip)}</span>
              ${r.banned ? `<span class="chip chip-public-perm">bannie</span>` : ""}
            </button>`,
    "Aucune adresse hostile.",
  );
  rankList(
    document.getElementById("sec-top-countries"),
    rep.top_countries.map((r) => ({ ...r, value: r.count })),
    (r) => `<span class="rank-name"><span aria-hidden="true">${countryFlag(r.country)}</span>${esc(countryName(r.country))}</span>`,
    "Aucune donnée.",
  );
  rankList(
    document.getElementById("sec-top-users"),
    rep.top_usernames.map((r) => ({ ...r, value: r.count })),
    (r) => `<span class="rank-name t-mono">${esc(r.username)}</span>`,
    "Aucun échec de connexion.",
  );
  rankList(
    document.getElementById("sec-top-reasons"),
    rep.block_reasons.map((r) => ({ ...r, value: r.count })),
    (r) => `<span class="rank-name">${esc(r.label)}</span>`,
    "Aucune requête bloquée.",
  );
}

async function loadSecReport() {
  const rep = await apiFetch(`${SEC_API}/report?period=${secPeriod}`);
  if (rep) renderReport(rep);
}

const secPeriodSeg = Motion.installSegmented(document.getElementById("sec-period"), (item) => {
  secPeriod = item.dataset.period;
  loadSecReport().catch((err) => showToast(err.message, "error"));
});

document.getElementById("sec-top-ips").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-ip]");
  if (!btn) return;
  const ip = btn.dataset.ip;
  const row = secTopIps.find((r) => r.ip === ip);
  const entries = [];
  if (row && row.banned) {
    entries.push({ label: "Débloquer", glyph: "↺", action: () => secUnban(ip) });
  } else {
    entries.push({ label: "Bannir…", glyph: "⛔", action: () => openSecBan(ip) });
  }
  entries.push({ label: "Ajouter à la liste noire", glyph: "⊘", action: () => secAddToList("deny", ip) });
  entries.push({ label: "Ajouter à la liste blanche", glyph: "✓", action: () => secAddToList("allow", ip) });
  entries.push({ separator: true });
  entries.push({ label: "Tester cette adresse", glyph: "?", action: () => openSecTest(ip, row && row.country) });
  Motion.showMenu(btn, entries);
});

// ── IP bannies ───────────────────────────────────────────────────────────────

const secBansHistory = document.getElementById("sec-bans-history");

function banChip(b) {
  if (!b.active) return `<span class="chip chip-neutral">Expiré</span>`;
  if (b.permanent) return `<span class="chip chip-public-perm">Définitif</span>`;
  return `<span class="chip chip-public">Jusqu'au ${esc(formatDateUtc(b.blocked_until))}</span>`;
}

function renderBans(bans) {
  const el = document.getElementById("sec-bans");
  if (!bans.length) {
    el.innerHTML = `<li class="row-empty">${secBansHistory.checked ? "Aucun bannissement enregistré." : "Aucune IP bannie en ce moment."}</li>`;
    return;
  }
  el.innerHTML = bans.map((b) => `
    <li class="row row--wrap${b.active ? "" : " row--muted"}">
      <span class="row-glyph" aria-hidden="true">${countryFlag(b.country)}</span>
      <span class="row-main">
        <span class="row-title t-mono">${esc(b.ip)}</span>
        <span class="row-sub">${esc(b.reason || "—")} · ${b.source === "manual" ? "manuel" : "automatique"}${b.ban_count > 1 ? ` · ${b.ban_count}ᵉ bannissement` : ""} · ${esc(fmtRelative(b.since))}</span>
      </span>
      <span class="row-side">${banChip(b)}</span>
      ${b.active ? `<button class="btn btn-secondary btn-sm" type="button" data-unban="${escAttr(b.ip)}">Débloquer</button>` : ""}
    </li>`).join("");
}

async function loadSecBans() {
  const bans = await apiFetch(`${SEC_API}/bans?include_expired=${secBansHistory.checked}`);
  if (bans) renderBans(bans);
}

async function secUnban(ip) {
  try {
    await apiFetch(`${SEC_API}/bans/${encodeURIComponent(ip)}`, { method: "DELETE" });
    showToast(`${ip} débloquée`);
    await Promise.all([loadSecBans(), loadSecReport(), loadSecSettings(), loadSecJournal(true)]);
  } catch (err) {
    showToast(err.message, "error");
  }
}

secBansHistory.addEventListener("change", () => loadSecBans().catch((err) => showToast(err.message, "error")));

document.getElementById("sec-bans").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-unban]");
  if (btn) secUnban(btn.dataset.unban);
});

// ── Listes d'accès ───────────────────────────────────────────────────────────

function renderList(el, entries, emptyText) {
  if (!entries.length) {
    el.innerHTML = `<li class="row-empty">${esc(emptyText)}</li>`;
    return;
  }
  el.innerHTML = entries.map((r) => `
    <li class="row">
      <span class="row-main">
        <span class="row-title t-mono">${esc(r.value)}</span>
        <span class="row-sub">${r.note ? `${esc(r.note)} · ` : ""}ajoutée ${esc(fmtRelative(r.created_at))}</span>
      </span>
      <button class="icon-btn icon-btn--sm" type="button" data-rule="${r.id}" aria-label="Retirer ${escAttr(r.value)}" title="Retirer">✕</button>
    </li>`).join("");
}

async function loadSecLists() {
  const lists = await apiFetch(`${SEC_API}/lists`);
  if (!lists) return;
  renderList(document.getElementById("sec-allow"), lists.allow, "Aucune adresse autorisée.");
  renderList(document.getElementById("sec-deny"), lists.deny, "Aucune adresse bloquée.");
}

async function secAddToList(listType, value, note = "") {
  try {
    await apiFetch(`${SEC_API}/lists`, {
      method: "POST",
      body: JSON.stringify({ list_type: listType, value, note }),
    });
    showToast(`${value} ajoutée à la ${listType === "allow" ? "liste blanche" : "liste noire"}`);
    await Promise.all([loadSecLists(), loadSecSettings(), loadSecJournal(true)]);
    return true;
  } catch (err) {
    showToast(err.message, "error");
    return false;
  }
}

document.querySelectorAll("#view-security .inline-add").forEach((formEl) => {
  formEl.addEventListener("submit", async (e) => {
    e.preventDefault();
    const value = formEl.elements.value.value.trim();
    if (!value) { formEl.elements.value.focus(); return; }
    const btn = formEl.querySelector("button[type=submit]");
    btn.disabled = true;
    if (await secAddToList(formEl.dataset.list, value, formEl.elements.note.value.trim())) formEl.reset();
    btn.disabled = false;
  });
});

["sec-allow", "sec-deny"].forEach((id) => {
  document.getElementById(id).addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-rule]");
    if (!btn) return;
    btn.disabled = true;
    try {
      await apiFetch(`${SEC_API}/lists/${btn.dataset.rule}`, { method: "DELETE" });
      showToast("Entrée retirée");
      await Promise.all([loadSecLists(), loadSecSettings(), loadSecJournal(true)]);
    } catch (err) {
      btn.disabled = false;
      showToast(err.message, "error");
    }
  });
});

// ── Journal ──────────────────────────────────────────────────────────────────

const SEC_EVENT_STYLE = {
  blocked:     { glyph: "⊘", tone: "red",    label: "Requête bloquée" },
  ban:         { glyph: "⛔", tone: "red",    label: "Bannissement" },
  manual_ban:  { glyph: "⛔", tone: "red",    label: "Bannissement manuel" },
  unban:       { glyph: "↺", tone: "blue",   label: "Déblocage" },
  auth_failed: { glyph: "⚠", tone: "orange", label: "Échec de connexion" },
  settings:    { glyph: "⚙", tone: "gray",   label: "Réglages" },
  list:        { glyph: "☰", tone: "gray",   label: "Listes d'accès" },
};

function journalRow(ev) {
  const st = SEC_EVENT_STYLE[ev.kind] || { glyph: "•", tone: "gray", label: ev.kind };
  const who = ev.ip
    ? `<span class="t-mono">${esc(ev.ip)}</span>${ev.country ? ` ${countryFlag(ev.country)}` : ""}`
    : "";
  const where = ev.host ? ` · ${esc(ev.host)}${ev.path ? esc(ev.path.length > 40 ? ev.path.slice(0, 40) + "…" : ev.path) : ""}` : "";
  const count = ev.count > 1 ? `<span class="chip chip-neutral">×${fmtNumber(ev.count)}</span>` : "";
  const when = ev.kind === "blocked" && ev.last_seen && ev.count > 1
    ? `${formatDateUtc(ev.at)} → ${fmtRelative(ev.last_seen)}`
    : formatDateUtc(ev.at);
  return `
    <li class="row">
      <span class="option-glyph option-glyph--${st.tone}" aria-hidden="true">${st.glyph}</span>
      <span class="row-main">
        <span class="row-title">${esc(ev.message || st.label)}</span>
        <span class="row-sub">${who}${where}${who || where ? " · " : ""}${esc(when)}</span>
      </span>
      ${count}
    </li>`;
}

async function loadSecJournal(reset = false) {
  if (reset) secJournalBefore = null;
  const params = new URLSearchParams({ kind: secJournalKind, limit: "20" });
  if (secJournalBefore) params.set("before", secJournalBefore);
  const data = await apiFetch(`${SEC_API}/events?${params}`);
  if (!data) return;
  const el = document.getElementById("sec-journal");
  const html = data.items.map(journalRow).join("");
  if (reset) {
    el.innerHTML = html || `<li class="row-empty">Aucun événement.</li>`;
  } else {
    el.insertAdjacentHTML("beforeend", html);
  }
  secJournalBefore = data.next_before;
  document.getElementById("sec-journal-more").hidden = !data.next_before;
}

const secJournalSeg = Motion.installSegmented(document.getElementById("sec-journal-filter"), (item) => {
  secJournalKind = item.dataset.kind;
  loadSecJournal(true).catch((err) => showToast(err.message, "error"));
});

document.getElementById("sec-journal-more").addEventListener("click", () =>
  loadSecJournal(false).catch((err) => showToast(err.message, "error")));

// ── Feuilles : ouverture / fermeture communes ────────────────────────────────

const secSheets = ["sec-ban-backdrop", "sec-mode-backdrop", "sec-country-backdrop", "sec-rules-backdrop", "sec-test-backdrop"]
  .map((id) => document.getElementById(id));

secSheets.forEach((backdrop) => {
  backdrop.addEventListener("click", (e) => {
    if (e.target === backdrop || e.target.closest("[data-close]")) Motion.dismissSheet(backdrop);
  });
});

document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  secSheets.forEach((b) => { if (Motion.isSheetOpen(b)) Motion.dismissSheet(b); });
});

function sheetError(id, msg) {
  const el = document.getElementById(id);
  el.textContent = msg || "";
  el.hidden = !msg;
}

// ── Feuille : bannir ─────────────────────────────────────────────────────────

const secBanBackdrop = document.getElementById("sec-ban-backdrop");
const secBanDurations = document.getElementById("sec-ban-durations");

function openSecBan(ip = "") {
  document.getElementById("sec-ban-form").reset();
  document.getElementById("sec-ban-ip").value = ip;
  sheetError("sec-ban-error");
  secBanDurations.querySelectorAll(".btn").forEach((b) => b.classList.toggle("is-selected", b.dataset.minutes === "1440"));
  Motion.presentSheet(secBanBackdrop, { focus: ip ? "#sec-ban-reason" : "#sec-ban-ip" });
}

secBanDurations.addEventListener("click", (e) => {
  const btn = e.target.closest(".btn");
  if (!btn) return;
  secBanDurations.querySelectorAll(".btn").forEach((b) => {
    b.classList.toggle("is-selected", b === btn);
    b.setAttribute("aria-pressed", b === btn ? "true" : "false");
  });
});

document.getElementById("sec-ban-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  sheetError("sec-ban-error");
  const ip = document.getElementById("sec-ban-ip").value.trim();
  if (!ip) { sheetError("sec-ban-error", "Renseignez une adresse IP."); return; }
  const selected = secBanDurations.querySelector(".is-selected");
  const minutes = selected && selected.dataset.minutes ? parseInt(selected.dataset.minutes, 10) : null;
  const submit = document.getElementById("sec-ban-submit");
  submit.disabled = true;
  try {
    await apiFetch(`${SEC_API}/bans`, {
      method: "POST",
      body: JSON.stringify({ ip, duration_minutes: minutes, reason: document.getElementById("sec-ban-reason").value.trim() }),
    });
    Motion.dismissSheet(secBanBackdrop);
    showToast(`${ip} bannie${minutes ? ` pour ${fmtMinutes(minutes)}` : " définitivement"}`);
    await Promise.all([loadSecBans(), loadSecReport(), loadSecSettings(), loadSecJournal(true)]);
  } catch (err) {
    sheetError("sec-ban-error", err.message);
  } finally {
    submit.disabled = false;
  }
});

document.getElementById("sec-btn-ban").addEventListener("click", () => openSecBan());

// ── Feuille : mode de fonctionnement ─────────────────────────────────────────

const secModeBackdrop = document.getElementById("sec-mode-backdrop");
let secModeDraft = "standard";

function paintModeOptions() {
  secModeBackdrop.querySelectorAll(".sec-mode-opt").forEach((row) => {
    const on = row.dataset.mode === secModeDraft;
    row.classList.toggle("is-current", on);
    row.setAttribute("aria-checked", on ? "true" : "false");
  });
}

document.getElementById("sec-open-mode").addEventListener("click", () => {
  if (!secSettings) return;
  secModeDraft = secSettings.mode;
  document.getElementById("sec-apply-services").checked = secSettings.apply_to_services;
  document.getElementById("sec-exempt-private").checked = secSettings.exempt_private;
  sheetError("sec-mode-error");
  paintModeOptions();
  Motion.presentSheet(secModeBackdrop, { focus: false });
});

secModeBackdrop.querySelectorAll(".sec-mode-opt").forEach((row) => {
  row.addEventListener("click", () => { secModeDraft = row.dataset.mode; paintModeOptions(); });
});

document.getElementById("sec-mode-save").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  btn.disabled = true;
  sheetError("sec-mode-error");
  try {
    await saveSecSettings({
      mode: secModeDraft,
      apply_to_services: document.getElementById("sec-apply-services").checked,
      exempt_private: document.getElementById("sec-exempt-private").checked,
    });
    Motion.dismissSheet(secModeBackdrop);
    showToast("Mode de fonctionnement enregistré");
    loadSecJournal(true);
  } catch (err) {
    sheetError("sec-mode-error", err.message);
  } finally {
    btn.disabled = false;
  }
});

// ── Feuille : filtrage par pays ──────────────────────────────────────────────

const secCountryBackdrop = document.getElementById("sec-country-backdrop");
const secCountryList = document.getElementById("sec-country-list");
const secCountrySearch = document.getElementById("sec-country-search");
let secCountryMode = "off";
let secCountrySelected = new Set();

function paintCountrySheet() {
  secCountryBackdrop.querySelectorAll(".sec-country-opt").forEach((row) => {
    const on = row.dataset.mode === secCountryMode;
    row.classList.toggle("is-current", on);
    row.setAttribute("aria-checked", on ? "true" : "false");
  });
  document.getElementById("sec-country-picker-wrap").hidden = secCountryMode === "off";

  const chips = [...secCountrySelected].sort((a, b) => countryName(a).localeCompare(countryName(b), "fr"));
  document.getElementById("sec-country-chips").innerHTML = chips.length
    ? chips.map((cc) => `
        <button class="chip chip-protected chip-removable" type="button" data-cc="${escAttr(cc)}" aria-label="Retirer ${escAttr(countryName(cc))}">
          ${countryFlag(cc)} ${esc(countryName(cc))} <span aria-hidden="true">✕</span>
        </button>`).join("")
    : `<span class="hint">Aucun pays sélectionné.</span>`;
}

function renderCountryList() {
  const q = secCountrySearch.value.trim().toLowerCase()
    .normalize("NFD").replace(/[̀-ͯ]/g, "");
  const rows = SEC_COUNTRIES.filter(({ code, name }) => {
    if (!q) return true;
    const n = name.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
    return code.toLowerCase() === q || n.includes(q);
  });
  secCountryList.innerHTML = rows.length
    ? rows.map(({ code, name }) => `
      <label class="check-row">
        <input type="checkbox" value="${code}" ${secCountrySelected.has(code) ? "checked" : ""}>
        <span aria-hidden="true">${countryFlag(code)}</span>
        <span class="picker-name">${esc(name)}</span>
        <span class="picker-slug">${code}</span>
      </label>`).join("")
    : `<p class="hint">Aucun pays ne correspond.</p>`;
}

document.getElementById("sec-open-country").addEventListener("click", () => {
  if (!secSettings) return;
  secCountryMode = secSettings.country_mode;
  secCountrySelected = new Set(secSettings.countries);
  secCountrySearch.value = "";
  sheetError("sec-country-error");
  paintCountrySheet();
  renderCountryList();
  Motion.presentSheet(secCountryBackdrop, { focus: false });
});

secCountryBackdrop.querySelectorAll(".sec-country-opt").forEach((row) => {
  row.addEventListener("click", () => { secCountryMode = row.dataset.mode; paintCountrySheet(); });
});

secCountrySearch.addEventListener("input", renderCountryList);

secCountryList.addEventListener("change", (e) => {
  const cb = e.target;
  if (cb.type !== "checkbox") return;
  if (cb.checked) secCountrySelected.add(cb.value); else secCountrySelected.delete(cb.value);
  paintCountrySheet();
});

document.getElementById("sec-country-chips").addEventListener("click", (e) => {
  const chip = e.target.closest("[data-cc]");
  if (!chip) return;
  secCountrySelected.delete(chip.dataset.cc);
  paintCountrySheet();
  renderCountryList();
});

document.getElementById("sec-country-save").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  sheetError("sec-country-error");
  if (secCountryMode !== "off" && secCountrySelected.size === 0) {
    sheetError("sec-country-error", "Sélectionnez au moins un pays.");
    return;
  }
  btn.disabled = true;
  try {
    await saveSecSettings({ country_mode: secCountryMode, countries: [...secCountrySelected] });
    Motion.dismissSheet(secCountryBackdrop);
    showToast("Filtrage par pays enregistré");
    loadSecJournal(true);
  } catch (err) {
    sheetError("sec-country-error", err.message);
  } finally {
    btn.disabled = false;
  }
});

// ── Feuille : règles de blocage ──────────────────────────────────────────────

const secRulesBackdrop = document.getElementById("sec-rules-backdrop");
const secRuleList = document.getElementById("sec-rule-list");

function ruleRow(r = { max_failures: 10, window_minutes: 15, ban_minutes: 15 }) {
  const row = document.createElement("div");
  row.className = "rule-row";
  row.innerHTML = `
    <input type="number" class="rule-in" data-k="max_failures" min="1" max="1000" value="${r.max_failures}" aria-label="Nombre d'échecs">
    <span>échecs en</span>
    <input type="number" class="rule-in" data-k="window_minutes" min="1" max="10080" value="${r.window_minutes}" aria-label="Fenêtre en minutes">
    <span>min → bannie</span>
    <input type="number" class="rule-in" data-k="ban_minutes" min="1" max="525600" value="${r.ban_minutes}" aria-label="Durée du bannissement en minutes">
    <span>min</span>
    <button class="icon-btn icon-btn--sm" type="button" data-remove aria-label="Supprimer ce palier" title="Supprimer">✕</button>`;
  return row;
}

document.getElementById("sec-open-rules").addEventListener("click", () => {
  if (!secSettings) return;
  const s = secSettings;
  document.getElementById("sec-bf-enabled").checked = s.bruteforce_enabled;
  secRuleList.innerHTML = "";
  s.rules.forEach((r) => secRuleList.appendChild(ruleRow(r)));
  document.getElementById("sec-escalation").value = s.escalation_factor;
  document.getElementById("sec-max-ban").value = Math.max(1, Math.round(s.max_ban_minutes / 60));
  document.getElementById("sec-permanent-after").value = s.permanent_after;
  document.getElementById("sec-retention").value = s.retention_days;
  sheetError("sec-rules-error");
  Motion.presentSheet(secRulesBackdrop, { focus: false });
});

document.getElementById("sec-rule-add").addEventListener("click", () => {
  if (secRuleList.children.length >= 10) { sheetError("sec-rules-error", "Dix paliers au maximum."); return; }
  const row = ruleRow();
  secRuleList.appendChild(row);
  row.querySelector("input").focus();
});

secRuleList.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-remove]");
  if (btn) btn.closest(".rule-row").remove();
});

function intField(id, label, min, max) {
  const v = Number(document.getElementById(id).value);
  if (!Number.isFinite(v) || v < min || v > max) throw new Error(`${label} : valeur entre ${min} et ${max} attendue.`);
  return v;
}

document.getElementById("sec-rules-save").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  sheetError("sec-rules-error");
  let patch;
  try {
    const rules = [...secRuleList.querySelectorAll(".rule-row")].map((row) => {
      const r = {};
      row.querySelectorAll(".rule-in").forEach((input) => {
        const v = parseInt(input.value, 10);
        if (!Number.isFinite(v) || v < 1 || v > Number(input.max)) throw new Error("Chaque palier attend des nombres entiers positifs.");
        r[input.dataset.k] = v;
      });
      return r;
    });
    const enabled = document.getElementById("sec-bf-enabled").checked;
    if (enabled && rules.length === 0) throw new Error("Ajoutez au moins un palier, ou désactivez le bannissement automatique.");
    patch = {
      bruteforce_enabled: enabled,
      escalation_factor: intField("sec-escalation", "Multiplicateur", 1, 10),
      max_ban_minutes: Math.round(intField("sec-max-ban", "Durée maximale", 1, 8760) * 60),
      permanent_after: Math.round(intField("sec-permanent-after", "Bannissement définitif", 0, 100)),
      retention_days: Math.round(intField("sec-retention", "Conservation", 1, 365)),
    };
    if (rules.length) patch.rules = rules;
  } catch (err) {
    sheetError("sec-rules-error", err.message);
    return;
  }
  btn.disabled = true;
  try {
    await saveSecSettings(patch);
    Motion.dismissSheet(secRulesBackdrop);
    showToast("Règles de blocage enregistrées");
    loadSecJournal(true);
  } catch (err) {
    sheetError("sec-rules-error", err.message);
  } finally {
    btn.disabled = false;
  }
});

// ── Feuille : tester une IP ──────────────────────────────────────────────────

const secTestBackdrop = document.getElementById("sec-test-backdrop");
const secTestResult = document.getElementById("sec-test-result");

function openSecTest(ip = "", country = "") {
  document.getElementById("sec-test-form").reset();
  document.getElementById("sec-test-ip").value = ip;
  document.getElementById("sec-test-country").value = country && country !== "XX" ? country : "";
  secTestResult.hidden = true;
  Motion.presentSheet(secTestBackdrop, { focus: "#sec-test-ip" });
}

document.getElementById("sec-open-test").addEventListener("click", () => openSecTest());

document.getElementById("sec-test-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const ip = document.getElementById("sec-test-ip").value.trim();
  const country = document.getElementById("sec-test-country").value.trim().toUpperCase();
  if (!ip) return;
  try {
    const r = await apiFetch(`${SEC_API}/test`, {
      method: "POST",
      body: JSON.stringify({
        ip,
        country: country || null,
        scope: document.getElementById("sec-test-service").checked ? "service" : "portal",
      }),
    });
    let detail = r.detail || "";
    if (r.code.startsWith("country") && detail) detail = `${countryFlag(detail)} ${countryName(detail)}`;
    if (r.until) detail += `${detail ? " · " : ""}jusqu'au ${formatDateUtc(r.until)}`;
    secTestResult.className = `test-result ${r.allowed ? "test-result--ok" : "test-result--ko"}`;
    secTestResult.innerHTML = `
      <span class="test-result-glyph" aria-hidden="true">${r.allowed ? "✓" : "⛔"}</span>
      <span>
        <strong>${r.allowed ? "Autorisée" : "Bloquée"}</strong> — ${esc(r.label)}${detail ? ` (${esc(detail)})` : ""}
        ${r.effective ? "" : `<span class="hint">IPBan est actuellement inactif : ce résultat s'appliquera une fois réactivé.</span>`}
      </span>`;
    secTestResult.hidden = false;
  } catch (err) {
    secTestResult.className = "test-result test-result--ko";
    secTestResult.textContent = err.message;
    secTestResult.hidden = false;
  }
});

// ── Chargement ───────────────────────────────────────────────────────────────

async function loadSecurity() {
  // La vue vient d'être affichée : les contrôles segmentés peuvent enfin se mesurer.
  requestAnimationFrame(() => { secPeriodSeg.refresh(); secJournalSeg.refresh(); });
  try {
    await Promise.all([loadSecSettings(), loadSecReport(), loadSecBans(), loadSecLists(), loadSecJournal(true)]);
  } catch (err) {
    showToast("Impossible de charger la sécurité", "error");
  }
  clearInterval(secRefreshTimer);
  secRefreshTimer = setInterval(() => {
    const visible = !document.getElementById("view-security").hidden;
    const busy = secSheets.some((b) => Motion.isSheetOpen(b));
    if (!visible) { clearInterval(secRefreshTimer); return; }
    if (!busy) Promise.all([loadSecReport(), loadSecBans()]).catch(() => {});
  }, SEC_REFRESH_INTERVAL);
}
