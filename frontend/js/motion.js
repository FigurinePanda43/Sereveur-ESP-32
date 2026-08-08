/* ─────────────────────────────────────────────────────────────────────────────
 * motion.js — moteur d'interaction fluide
 *
 * Trois briques, dans l'esprit « Designing Fluid Interfaces » :
 *
 *   1. Un solveur de ressort analytique paramétré en (amortissement, réponse)
 *      plutôt qu'en (masse, raideur, frottement). Toute animation part de la
 *      valeur affichée à l'instant T et absorbe la vélocité en cours : elle est
 *      donc interruptible et réversible sans saut visuel.
 *   2. La présentation des feuilles modales (sheets) : matérialisation,
 *      glisser-pour-fermer avec projection de l'élan, résistance élastique,
 *      piège de focus et verrou de défilement.
 *   3. Les retours immédiats : appui (pointerdown, pas click), contrôle
 *      segmenté à pouce glissant, menus ancrés sur leur déclencheur.
 *
 * Aucune dépendance. Expose `Motion` sur window.
 * ────────────────────────────────────────────────────────────────────────── */

(function () {
  "use strict";

  // ── Préférences système ────────────────────────────────────────────────────

  const reduceMotionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  const compactQuery = window.matchMedia("(max-width: 700px)");

  const prefersReducedMotion = () => reduceMotionQuery.matches;
  const isCompact = () => compactQuery.matches;

  // ── Solveur de ressort ─────────────────────────────────────────────────────

  // Résolution analytique de m·x'' + c·x' + k·x = 0 exprimée en (ζ, ω).
  // ω = 2π / réponse ; ζ = 1 → amorti critique (aucun dépassement).
  // Retourne une fonction t → [position relative à la cible, vélocité].
  function solver(zeta, omega, x0, v0) {
    if (zeta < 1) {
      const wd = omega * Math.sqrt(1 - zeta * zeta);
      const a = x0;
      const b = (v0 + zeta * omega * x0) / wd;
      return (t) => {
        const decay = Math.exp(-zeta * omega * t);
        const cos = Math.cos(wd * t);
        const sin = Math.sin(wd * t);
        return [
          decay * (a * cos + b * sin),
          decay * ((b * wd - zeta * omega * a) * cos - (a * wd + zeta * omega * b) * sin),
        ];
      };
    }
    // Amorti critique (ζ ≥ 1 est ramené à 1 : on ne veut jamais de sur-amortissement mou)
    const a = x0;
    const b = v0 + omega * x0;
    return (t) => {
      const decay = Math.exp(-omega * t);
      const pos = (a + b * t) * decay;
      return [pos, (b - omega * (a + b * t)) * decay];
    };
  }

  // Boucle unique synchronisée sur l'affichage : toutes les valeurs animées
  // avancent sur la même frame, ce qui garantit l'harmonie entre elles.
  const running = new Set();
  let frameHandle = null;

  function tick(now) {
    frameHandle = null;
    running.forEach((value) => value._step(now));
    if (running.size) frameHandle = requestAnimationFrame(tick);
  }

  function schedule() {
    if (frameHandle === null && running.size) frameHandle = requestAnimationFrame(tick);
  }

  /**
   * Valeur scalaire animée par ressort.
   *
   * `to()` recible sans jamais réinitialiser l'état : la position courante et
   * la vélocité courante deviennent les conditions initiales du nouveau
   * ressort. C'est ce qui évite le « mur de briques » quand un geste s'inverse.
   */
  class SpringValue {
    constructor(initial, options = {}) {
      this.value = initial;
      this.velocity = 0;
      this.target = initial;
      this.damping = options.damping ?? 1;
      this.response = options.response ?? 0.4;
      this.precision = options.precision ?? 0.001;
      this.onUpdate = options.onUpdate || null;
      this.onRest = options.onRest || null;
      this._solve = null;
      this._t0 = 0;
    }

    get isAnimating() {
      return running.has(this);
    }

    /** Fixe la valeur immédiatement et coupe toute animation en cours. */
    set(value, velocity = 0) {
      running.delete(this);
      this._solve = null;
      this.value = value;
      this.target = value;
      this.velocity = velocity;
      this._emit();
      return this;
    }

    /** Recible le ressort depuis la valeur *affichée* et la vélocité courante. */
    to(target, options = {}) {
      const damping = options.damping ?? this.damping;
      const response = options.response ?? this.response;
      this.damping = damping;
      this.response = response;
      this.target = target;
      if (options.velocity !== undefined) this.velocity = options.velocity;
      if (options.onRest !== undefined) this.onRest = options.onRest;

      if (prefersReducedMotion() && !options.force) {
        // Mouvement réduit : on saute à la valeur finale, les fondus CSS prennent le relais.
        this.set(target);
        if (this.onRest) this.onRest();
        return this;
      }

      const zeta = Math.min(damping, 1);
      const omega = (2 * Math.PI) / response;
      this._solve = solver(zeta, omega, this.value - target, this.velocity);
      this._t0 = performance.now();
      running.add(this);
      schedule();
      return this;
    }

    stop() {
      running.delete(this);
      this._solve = null;
      return this;
    }

    _step(now) {
      const t = (now - this._t0) / 1000;
      const [offset, velocity] = this._solve(t);
      const scale = Math.max(1, Math.abs(this.target));

      if (Math.abs(offset) < this.precision * scale && Math.abs(velocity) < this.precision * scale * 8) {
        this.value = this.target;
        this.velocity = 0;
        running.delete(this);
        this._emit();
        if (this.onRest) this.onRest();
        return;
      }

      this.value = this.target + offset;
      this.velocity = velocity;
      this._emit();
    }

    _emit() {
      if (this.onUpdate) this.onUpdate(this.value, this.velocity);
    }
  }

  // ── Physique de geste ──────────────────────────────────────────────────────

  /**
   * Point d'arrêt projeté d'un lancer, décroissance exponentielle (UIScrollView).
   * Ce n'est pas v²/2a : c'est la formule qu'utilise réellement iOS.
   */
  function project(velocity, decelerationRate = 0.998) {
    return ((velocity / 1000) * decelerationRate) / (1 - decelerationRate);
  }

  /** Résistance progressive au-delà d'une limite : rien ne s'arrête net. */
  function rubberband(overshoot, dimension, constant = 0.55) {
    if (!dimension) return overshoot;
    return (overshoot * dimension * constant) / (dimension + constant * Math.abs(overshoot));
  }

  /** Historique court de positions → vélocité au relâchement (px/s). */
  class VelocityTracker {
    constructor(window = 100) {
      this.window = window;
      this.samples = [];
    }
    reset() {
      this.samples = [];
    }
    add(position, time) {
      this.samples.push({ position, time });
      while (this.samples.length > 2 && time - this.samples[0].time > this.window) {
        this.samples.shift();
      }
    }
    velocity() {
      if (this.samples.length < 2) return 0;
      const first = this.samples[0];
      const last = this.samples[this.samples.length - 1];
      const dt = last.time - first.time;
      if (dt <= 0) return 0;
      return ((last.position - first.position) / dt) * 1000;
    }
  }

  // ── Verrou de défilement ───────────────────────────────────────────────────

  let scrollLocks = 0;
  let savedScrollY = 0;

  function lockScroll() {
    if (scrollLocks++ > 0) return;
    savedScrollY = window.scrollY;
    document.body.style.position = "fixed";
    document.body.style.top = `-${savedScrollY}px`;
    document.body.style.left = "0";
    document.body.style.right = "0";
    document.body.style.overflow = "hidden";
  }

  function unlockScroll() {
    if (--scrollLocks > 0) return;
    scrollLocks = 0;
    document.body.style.position = "";
    document.body.style.top = "";
    document.body.style.left = "";
    document.body.style.right = "";
    document.body.style.overflow = "";
    window.scrollTo(0, savedScrollY);
  }

  // ── Piège de focus ─────────────────────────────────────────────────────────

  const FOCUSABLE =
    'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

  function focusableIn(root) {
    return Array.from(root.querySelectorAll(FOCUSABLE)).filter(
      (el) => el.offsetParent !== null || el === document.activeElement
    );
  }

  function trapFocus(event, panel) {
    if (event.key !== "Tab") return;
    const items = focusableIn(panel);
    if (!items.length) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  // ── Feuilles modales ───────────────────────────────────────────────────────

  // Une feuille est décrite par une seule grandeur `t` ∈ [0, 1] :
  //   t = 0 → hors écran (en bas en compact, réduite et transparente sinon)
  //   t = 1 → présentée
  // Le geste écrit directement dans `t`, le ressort aussi. Les deux peuvent donc
  // se relayer à n'importe quel instant sans discontinuité.

  const sheets = new WeakMap();

  function sheetState(backdrop) {
    let state = sheets.get(backdrop);
    if (state) return state;

    const panel = backdrop.querySelector(".sheet");
    state = {
      backdrop,
      panel,
      phase: "dismissed", // dismissed | presenting | presented | dismissing
      returnFocus: null,
      dragging: false,
      spring: null,
      onDismissed: null,
    };

    state.spring = new SpringValue(0, {
      damping: 1,
      response: 0.4,
      precision: 0.0015,
      onUpdate: (t) => applySheetProgress(state, t),
    });

    sheets.set(backdrop, state);
    installSheetGesture(state);
    return state;
  }

  function applySheetProgress(state, t) {
    const { backdrop, panel } = state;
    const clamped = Math.min(t, 1);

    backdrop.style.setProperty("--scrim", clamped.toFixed(4));

    if (!panel) return;

    if (isCompact()) {
      const height = panel.offsetHeight || window.innerHeight;
      // t > 1 = tiré au-delà du repos : on remonte, mais avec résistance.
      const y = t <= 1 ? (1 - t) * height : -rubberband((t - 1) * height, height);
      panel.style.transform = `translate3d(0, ${y.toFixed(2)}px, 0)`;
      panel.style.opacity = "1";
    } else {
      // Sur grand écran la feuille se *matérialise* : elle grandit et se
      // déflloute en même temps qu'elle apparaît, au lieu d'un simple fondu.
      const scale = 0.94 + 0.06 * clamped;
      const y = (1 - clamped) * 12;
      panel.style.transform = `translate3d(0, ${y.toFixed(2)}px, 0) scale(${scale.toFixed(4)})`;
      panel.style.opacity = clamped.toFixed(3);
      panel.style.filter = clamped > 0.995 ? "" : `blur(${((1 - clamped) * 6).toFixed(2)}px)`;
    }
  }

  function presentSheet(backdrop, options = {}) {
    const state = sheetState(backdrop);
    if (state.phase === "presented" || state.phase === "presenting") return state;

    const wasDismissing = state.phase === "dismissing";
    state.phase = "presenting";
    state.onDismissed = null;

    if (!wasDismissing) {
      state.returnFocus = document.activeElement;
      backdrop.hidden = false;
      applySheetProgress(state, state.spring.value);
      lockScroll();
      // Une frame pour que le navigateur mesure la feuille avant de l'animer.
      void backdrop.offsetHeight;
    }

    backdrop.classList.add("is-open");

    state.spring.to(1, {
      damping: options.damping ?? 0.85,
      response: options.response ?? 0.38,
      onRest: () => {
        state.phase = "presented";
        if (state.panel) state.panel.style.filter = "";
      },
    });

    if (options.focus !== false) {
      const focusTarget =
        (options.focus && backdrop.querySelector(options.focus)) ||
        focusableIn(state.panel || backdrop).find((el) => !el.classList.contains("sheet-close")) ||
        state.panel;
      if (focusTarget) {
        try {
          focusTarget.focus({ preventScroll: true });
        } catch (e) {
          focusTarget.focus();
        }
      }
    }

    return state;
  }

  function dismissSheet(backdrop, options = {}) {
    const state = sheetState(backdrop);
    if (state.phase === "dismissed" || state.phase === "dismissing") return state;

    state.phase = "dismissing";
    backdrop.classList.remove("is-open");

    state.spring.to(0, {
      damping: 1,
      response: options.response ?? 0.32,
      velocity: options.velocity,
      onRest: () => {
        state.phase = "dismissed";
        backdrop.hidden = true;
        unlockScroll();
        if (state.returnFocus && document.body.contains(state.returnFocus)) {
          try {
            state.returnFocus.focus({ preventScroll: true });
          } catch (e) {
            state.returnFocus.focus();
          }
        }
        state.returnFocus = null;
        if (state.onDismissed) {
          const cb = state.onDismissed;
          state.onDismissed = null;
          cb();
        }
      },
    });

    return state;
  }

  function isSheetOpen(backdrop) {
    const state = sheets.get(backdrop);
    if (!state) return !backdrop.hidden;
    return state.phase === "presented" || state.phase === "presenting";
  }

  // Glisser-pour-fermer. Actif uniquement en présentation compacte, où la
  // feuille arrive du bas : elle doit donc repartir par le bas (§ symétrie).
  function installSheetGesture(state) {
    const { backdrop, panel } = state;
    if (!panel) return;

    const tracker = new VelocityTracker();
    let pointerId = null;
    let startY = 0;
    let startT = 1;
    let height = 0;
    let scroller = null;
    let committed = false;

    const grabbable = (target) =>
      target.closest(".sheet-handle, .sheet-header") &&
      !target.closest("button, a, input, select, textarea");

    panel.addEventListener(
      "pointerdown",
      (event) => {
        if (!isCompact() || pointerId !== null || event.button !== 0) return;
        if (!grabbable(event.target)) return;

        pointerId = event.pointerId;
        committed = false;
        startY = event.clientY;
        height = panel.offsetHeight || window.innerHeight;
        scroller = panel.querySelector(".sheet-body");

        // On saisit la feuille là où elle est *maintenant*, même en plein vol.
        state.spring.stop();
        startT = state.spring.value;
        state.dragging = true;
        tracker.reset();
        tracker.add(event.clientY, event.timeStamp);
      },
      { passive: true }
    );

    panel.addEventListener("pointermove", (event) => {
      if (event.pointerId !== pointerId) return;
      const dy = event.clientY - startY;
      tracker.add(event.clientY, event.timeStamp);

      // Hystérésis : on ne s'engage qu'après ~10px, et jamais si le contenu
      // interne peut encore défiler vers le haut.
      if (!committed) {
        if (Math.abs(dy) < 10) return;
        if (dy > 0 && scroller && scroller.scrollTop > 0) {
          pointerId = null;
          state.dragging = false;
          return;
        }
        committed = true;
        panel.setPointerCapture(event.pointerId);
        panel.classList.add("is-dragging");
      }

      event.preventDefault();
      state.spring.set(startT - dy / height);
      applySheetProgress(state, state.spring.value);
    });

    const release = (event) => {
      if (event.pointerId !== pointerId) return;
      pointerId = null;
      state.dragging = false;
      if (!committed) return;
      committed = false;
      panel.classList.remove("is-dragging");

      const velocityY = tracker.velocity(); // px/s, positif = vers le bas
      const current = state.spring.value;

      // On ne décide pas sur la position d'arrivée du doigt, mais sur celle que
      // l'élan *projette* : un petit geste rapide ferme, un grand geste lent non.
      const projectedY = (1 - current) * height + project(velocityY);
      const shouldDismiss = projectedY > height * 0.42;

      const springVelocity = -velocityY / height; // vélocité exprimée en unités de t

      if (shouldDismiss) {
        dismissSheet(backdrop, { velocity: springVelocity, response: 0.3 });
      } else {
        state.spring.to(1, { damping: 0.82, response: 0.35, velocity: springVelocity });
        state.phase = "presented";
      }
    };

    panel.addEventListener("pointerup", release);
    panel.addEventListener("pointercancel", release);
  }

  // Recalcule la géométrie quand on bascule compact ↔ large pendant l'affichage.
  const relayout = () => {
    document.querySelectorAll(".scrim").forEach((backdrop) => {
      const state = sheets.get(backdrop);
      if (!state || state.phase === "dismissed") return;
      if (state.panel) state.panel.style.filter = "";
      applySheetProgress(state, state.spring.value);
    });
  };
  window.addEventListener("resize", relayout);

  // ── Retour d'appui immédiat ────────────────────────────────────────────────

  // Le retour visuel se déclenche au *contact*, pas au relâchement, et se
  // rétracte si le doigt s'éloigne : on peut annuler un appui en glissant.
  function installPressFeedback() {
    const PRESSABLE = ".btn, .tool-row, .seg-item, .menu-item, .option-row, .scan-item-add, .icon-btn";
    const CANCEL_DISTANCE = 12;
    let pressed = null;
    let origin = null;

    const clear = () => {
      if (pressed) pressed.classList.remove("is-pressed");
      pressed = null;
      origin = null;
    };

    document.addEventListener(
      "pointerdown",
      (event) => {
        if (event.button !== 0) return;
        const target = event.target.closest(PRESSABLE);
        if (!target || target.disabled) return;
        pressed = target;
        origin = { x: event.clientX, y: event.clientY };
        target.classList.add("is-pressed");
      },
      { passive: true }
    );

    // S'éloigner rétracte le retour visuel ; revenir le rétablit. L'appui reste
    // annulable jusqu'au relâchement, dans les deux sens.
    document.addEventListener(
      "pointermove",
      (event) => {
        if (!pressed || !origin) return;
        const dx = event.clientX - origin.x;
        const dy = event.clientY - origin.y;
        pressed.classList.toggle("is-pressed", Math.hypot(dx, dy) <= CANCEL_DISTANCE);
      },
      { passive: true }
    );

    document.addEventListener("pointerup", clear, { passive: true });
    document.addEventListener("pointercancel", clear, { passive: true });
    window.addEventListener("blur", clear);
  }

  // ── Contrôle segmenté ──────────────────────────────────────────────────────

  // Le pouce ne « saute » pas d'un segment à l'autre : deux ressorts
  // indépendants (position, largeur) le portent, ce qui reste cohérent même
  // quand on change d'onglet avant la fin du mouvement précédent.
  function installSegmented(container, onSelect) {
    const thumb = container.querySelector(".seg-thumb");
    const items = () => Array.from(container.querySelectorAll(".seg-item"));

    const x = new SpringValue(0, { damping: 1, response: 0.36, precision: 0.4 });
    const width = new SpringValue(0, { damping: 1, response: 0.36, precision: 0.4 });

    const paint = () => {
      if (!thumb) return;
      thumb.style.transform = `translate3d(${x.value.toFixed(2)}px, 0, 0)`;
      thumb.style.width = `${Math.max(0, width.value).toFixed(2)}px`;
    };
    x.onUpdate = paint;
    width.onUpdate = paint;

    const moveTo = (item, animated = true) => {
      if (!thumb || !item) return;
      const target = item.offsetLeft;
      const targetWidth = item.offsetWidth;
      if (!animated || thumb.dataset.ready !== "1") {
        x.set(target);
        width.set(targetWidth);
        thumb.dataset.ready = "1";
        thumb.style.opacity = "1";
        paint();
        return;
      }
      x.to(target);
      width.to(targetWidth);
    };

    const select = (item, animated = true) => {
      items().forEach((el) => {
        const active = el === item;
        el.classList.toggle("is-active", active);
        el.setAttribute("aria-selected", active ? "true" : "false");
        el.tabIndex = active ? 0 : -1;
      });
      moveTo(item, animated);
    };

    container.addEventListener("click", (event) => {
      const item = event.target.closest(".seg-item");
      if (!item || !container.contains(item)) return;
      select(item);
      if (onSelect) onSelect(item);
    });

    // Navigation clavier : flèches, comme un vrai tablist.
    container.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
      const all = items();
      const index = all.findIndex((el) => el.classList.contains("is-active"));
      if (index < 0) return;
      const next = all[(index + (event.key === "ArrowRight" ? 1 : all.length - 1)) % all.length];
      event.preventDefault();
      next.focus();
      select(next);
      if (onSelect) onSelect(next);
    });

    window.addEventListener("resize", () => {
      const active = container.querySelector(".seg-item.is-active");
      if (active) moveTo(active, false);
    });

    return { select, refresh: () => moveTo(container.querySelector(".seg-item.is-active"), false) };
  }

  // ── Menu ancré ─────────────────────────────────────────────────────────────

  // Le menu naît du bouton qui l'ouvre (transform-origin sur le déclencheur) et
  // y retourne : la relation spatiale entre l'action et son origine reste lisible.
  let openMenu = null;

  function closeMenu(immediate = false) {
    if (!openMenu) return;
    const { element, trigger, spring } = openMenu;
    const ref = openMenu;
    openMenu = null;
    trigger.setAttribute("aria-expanded", "false");

    const finish = () => {
      element.remove();
      if (ref.onClose) ref.onClose();
    };

    if (immediate || prefersReducedMotion()) {
      finish();
      return;
    }
    spring.to(0, { damping: 1, response: 0.24, onRest: finish });
  }

  function showMenu(trigger, entries) {
    closeMenu(true);

    const element = document.createElement("div");
    element.className = "menu";
    element.setAttribute("role", "menu");

    entries.forEach((entry) => {
      if (entry.separator) {
        element.appendChild(Object.assign(document.createElement("div"), { className: "menu-sep" }));
        return;
      }
      const item = document.createElement("button");
      item.type = "button";
      item.className = "menu-item" + (entry.danger ? " menu-item--danger" : "");
      item.setAttribute("role", "menuitem");
      item.innerHTML = `<span class="menu-item-label">${entry.label}</span><span class="menu-item-glyph" aria-hidden="true">${entry.glyph || ""}</span>`;
      item.addEventListener("click", () => {
        closeMenu();
        entry.action();
      });
      element.appendChild(item);
    });

    document.body.appendChild(element);

    // Positionnement : sous le déclencheur, aligné à droite, replié si besoin.
    const rect = trigger.getBoundingClientRect();
    const menuRect = element.getBoundingClientRect();
    const margin = 8;
    let left = rect.right - menuRect.width;
    let top = rect.bottom + 6;
    let originY = "top";

    if (left < margin) left = margin;
    if (left + menuRect.width > window.innerWidth - margin) {
      left = window.innerWidth - margin - menuRect.width;
    }
    if (top + menuRect.height > window.innerHeight - margin) {
      top = rect.top - menuRect.height - 6;
      originY = "bottom";
    }

    element.style.left = `${Math.round(left)}px`;
    element.style.top = `${Math.round(top)}px`;
    element.style.transformOrigin = `${Math.round(rect.left + rect.width / 2 - left)}px ${originY}`;

    const spring = new SpringValue(0, {
      damping: 0.85,
      response: 0.3,
      precision: 0.002,
      onUpdate: (t) => {
        element.style.opacity = Math.min(1, t * 1.6).toFixed(3);
        element.style.transform = `scale(${(0.86 + 0.14 * t).toFixed(4)})`;
      },
    });

    openMenu = { element, trigger, spring, onClose: null };
    trigger.setAttribute("aria-expanded", "true");
    spring.to(1);

    const first = element.querySelector(".menu-item");
    if (first) first.focus({ preventScroll: true });

    return openMenu;
  }

  document.addEventListener("pointerdown", (event) => {
    if (!openMenu) return;
    if (event.target.closest(".menu") || event.target === openMenu.trigger) return;
    closeMenu();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && openMenu) {
      event.stopPropagation();
      const trigger = openMenu.trigger;
      closeMenu();
      trigger.focus();
    }
  });
  window.addEventListener("scroll", () => closeMenu(true), true);

  // ── Bord de défilement ─────────────────────────────────────────────────────

  // La barre translucide ne se sépare du contenu que lorsqu'elle le recouvre
  // vraiment : pas de filet 1px permanent.
  function installScrollEdge(header) {
    if (!header) return;
    const update = () => header.classList.toggle("is-scrolled", window.scrollY > 4);
    update();
    window.addEventListener("scroll", update, { passive: true });
  }

  // ── Apparence (clair / sombre / système) ───────────────────────────────────

  const THEME_KEY = "sereveur-theme";
  const THEMES = ["auto", "light", "dark"];

  function currentTheme() {
    const stored = localStorage.getItem(THEME_KEY);
    return THEMES.includes(stored) ? stored : "auto";
  }

  function applyTheme(theme) {
    const root = document.documentElement;
    if (theme === "auto") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
    localStorage.setItem(THEME_KEY, theme);
  }

  function cycleTheme() {
    const next = THEMES[(THEMES.indexOf(currentTheme()) + 1) % THEMES.length];
    applyTheme(next);
    return next;
  }

  // ── Révélation à l'entrée ──────────────────────────────────────────────────

  // Les cartes arrivent avec un décalage court et strictement croissant : le
  // regard suit l'ordre de lecture au lieu de recevoir tout d'un bloc.
  function revealSequence(elements, options = {}) {
    if (prefersReducedMotion()) {
      elements.forEach((el) => el.classList.add("is-revealed"));
      return;
    }
    const step = options.step ?? 26;
    const max = options.max ?? 8;
    elements.forEach((el, index) => {
      el.style.setProperty("--reveal-delay", `${Math.min(index, max) * step}ms`);
      requestAnimationFrame(() => el.classList.add("is-revealed"));
    });
  }

  window.Motion = {
    SpringValue,
    VelocityTracker,
    project,
    rubberband,
    presentSheet,
    dismissSheet,
    isSheetOpen,
    installPressFeedback,
    installSegmented,
    installScrollEdge,
    showMenu,
    closeMenu,
    revealSequence,
    theme: { current: currentTheme, apply: applyTheme, cycle: cycleTheme },
    prefersReducedMotion,
    isCompact,
  };
})();
