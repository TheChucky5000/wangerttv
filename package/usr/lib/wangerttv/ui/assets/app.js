/* WangertTV – TV-Oberfläche: Startseite, Einrichtungsassistent, Einstellungen, Bildschirmschoner.
   Komplett mit Fernbedienung (Pfeiltasten, OK, Zurück, Menü) bedienbar. */
"use strict";

/* ====================================================================== Grundlagen */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

const S = {
  state: null, settings: {}, apps: { home: [], all: [], running: [] }, channels: [], channelsConfigured: false,
  weather: null, view: "boot", tab: "foryou", cat: "network", wz: 0, nowplaying: {}, updates: null,
  heroIndex: 0, channelGroup: "", panelData: {}, lastKey: Date.now(), ambient: false, standby: false,
  homeFocused: true, installMode: false,
};

/* Startbild ist gemalt -> die Box schaltet von der Bootanimation nahtlos auf die Oberfläche um */
requestAnimationFrame(() => requestAnimationFrame(() => {
  fetch("/api/ui/ready", { method: "POST", headers: { "Content-Type": "application/json", "X-WTV": "1" }, body: "{}" }).catch(() => {});
}));

function t(key, vars) {
  const lang = (S.settings && S.settings.lang) || "de";
  let s = (I18N[lang] && I18N[lang][key]) ?? I18N.de[key] ?? key;
  if (vars) for (const k in vars) s = s.split(`{${k}}`).join(vars[k]);
  return s;
}
function tOr(key, fallback, vars) {
  const lang = (S.settings && S.settings.lang) || "de";
  return ((I18N[lang] && I18N[lang][key]) ?? I18N.de[key]) !== undefined ? t(key, vars) : t(fallback, vars);
}
const L = (obj) => (obj && (obj[S.settings.lang] || obj.de)) || "";

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json", "X-WTV": "1" }, body: JSON.stringify(body),
  };
  try {
    const res = await fetch(path, opts);
    return await res.json();
  } catch (e) {
    return { error: String(e) };
  }
}

function fmtBytes(n) {
  if (!n) return "0 B";
  const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0;
  while (n >= 1000 && i < u.length - 1) { n /= 1000; i++; }
  return `${n.toFixed(n >= 100 || i === 0 ? 0 : 1)} ${u[i]}`;
}
function fmtTime(ts) {
  if (!ts) return t("never");
  return new Date(ts * 1000).toLocaleString(S.settings.lang === "en" ? "en-GB" : "de-DE", { dateStyle: "medium", timeStyle: "short" });
}
function fmtDuration(sec) {
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60);
  return h ? `${h} h ${m} min` : `${m} min`;
}
function locale() { return S.settings.lang === "en" ? "en-GB" : "de-DE"; }

/* ====================================================================== Fokus-Steuerung (Fernbedienung) */
const Nav = {
  layers: [],          // Overlay-Ebenen: {el, restore, onBack}
  current: null,
  memory: {},          // Reihe -> zuletzt fokussiertes Element (Index)

  root() { return this.layers.length ? this.layers[this.layers.length - 1].el : $("#app"); },
  items() {
    return $$(".f", this.root()).filter((el) => !el.disabled && el.offsetParent !== null && !el.closest(".hidden"));
  },
  focus(el, { scroll = true } = {}) {
    if (!el) return;
    if (this.current && this.current !== el) this.current.classList.remove("focused");
    this.current = el;
    el.classList.add("focused");
    const row = el.closest("[data-row]");
    if (row) this.memory[row.dataset.row] = $$(".f", row).indexOf(el);
    if (scroll) this.reveal(el);
    if (!this.layers.length) Spot.follow(el);
  },
  reveal(el) {
    if (el.closest(".topbar")) {
      const sc = $(".home-scroll"); if (sc) sc.scrollTo({ top: 0, behavior: "smooth" });
      return;
    }
    const track = el.closest(".row-track");
    if (track) {
      const target = el.offsetLeft - track.clientWidth / 2 + el.offsetWidth / 2;
      track.scrollTo({ left: Math.max(0, target), behavior: "smooth" });
    }
    const scroller = el.closest(".home-scroll, .set-body, .wz-right, .list, .set-nav, .view-scroll");
    if (scroller && scroller.classList.contains("home-scroll")) {
      // Startseite 2.0: die ausgewählte Reihe steht immer oben unter dem Spotlight (wie Google TV)
      const block = el.closest(".row") || el;
      const first = block === scroller.querySelector(".home-content > *");
      const pad = block === el ? 16 : 4;          // einzelne Kachel (Raster): Platz für die Vergrößerung lassen
      const target = first ? 0 : scroller.scrollTop + block.getBoundingClientRect().top - scroller.getBoundingClientRect().top - pad;
      if (Math.abs(target - scroller.scrollTop) > 2) scroller.scrollTo({ top: Math.max(0, target), behavior: "smooth" });
    } else if (scroller) {
      const block = el.closest(".row") || el;
      const sr = scroller.getBoundingClientRect(), br = block.getBoundingClientRect();
      const home = scroller.classList.contains("home-scroll");
      const topPad = home ? 4 : 90;
      if (br.top < sr.top + topPad || br.bottom > sr.bottom - (home ? 10 : 60)) {
        const delta = br.top - sr.top - (home ? 4 : sr.height / 3);
        scroller.scrollTo({ top: Math.max(0, scroller.scrollTop + delta), behavior: "smooth" });
      }
    }
  },
  first(selector) {
    const items = this.items();
    const el = (selector && $(selector, this.root())) || items.find((e) => e.dataset.autofocus) || items[0];
    if (el) this.focus(el);
  },
  valid() { return this.current && this.current.isConnected && this.root().contains(this.current) && this.current.offsetParent !== null; },
  move(dir) {
    if (!this.valid()) { this.first(); return; }
    const cur = this.current, cr = cur.getBoundingClientRect();
    const cx = cr.left + cr.width / 2, cy = cr.top + cr.height / 2;
    let best = null, bestScore = Infinity;
    for (const el of this.items()) {
      if (el === cur) continue;
      const r = el.getBoundingClientRect();
      const x = r.left + r.width / 2, y = r.top + r.height / 2;
      let primary, secondary;
      if (dir === "right") { if (r.left < cr.right - 6) continue; primary = x - cx; secondary = Math.abs(y - cy); }
      else if (dir === "left") { if (r.right > cr.left + 6) continue; primary = cx - x; secondary = Math.abs(y - cy); }
      else if (dir === "down") { if (r.top < cr.bottom - 6) continue; primary = y - cy; secondary = Math.abs(x - cx); }
      else { if (r.bottom > cr.top + 6) continue; primary = cy - y; secondary = Math.abs(x - cx); }
      if (primary <= 0) continue;
      const overlap = dir === "left" || dir === "right"
        ? Math.min(r.bottom, cr.bottom) - Math.max(r.top, cr.top)
        : Math.min(r.right, cr.right) - Math.max(r.left, cr.left);
      const score = primary + secondary * (overlap > 0 ? 0.6 : 2.5);
      if (score < bestScore) { bestScore = score; best = el; }
    }
    if (!best) return false;
    // Google-TV-Verhalten: beim Wechsel in eine andere Reihe die zuletzt gewählte Kachel wiederherstellen
    const row = best.closest("[data-row]");
    if ((dir === "up" || dir === "down") && row && row !== cur.closest("[data-row]")) {
      const idx = this.memory[row.dataset.row];
      const items = $$(".f", row);
      if (idx !== undefined && items[idx]) best = items[idx];
    }
    this.focus(best);
    return true;
  },
  push(el, { onBack } = {}) {
    this.layers.push({ el, restore: this.current, onBack });
    if (this.current) this.current.classList.remove("focused");
    this.current = null;
  },
  pop() {
    const layer = this.layers.pop();
    if (!layer) return;
    layer.el.remove();
    this.current = null;
    if (layer.restore && layer.restore.isConnected) this.focus(layer.restore, { scroll: false });
    else this.first();
  },
  popAll() { while (this.layers.length) this.pop(); },
};

/* ====================================================================== Aktionen */
const Actions = {};
function act(name, fn) { Actions[name] = fn; }

function activate(el) {
  if (!el) return;
  const name = el.dataset.act;
  if (name && Actions[name]) Actions[name](el.dataset, el);
}

/* ====================================================================== Tasten */
let enterTimer = null, enterLong = false;
const BACK_KEYS = new Set(["Escape", "Backspace", "BrowserBack", "GoBack"]);

document.addEventListener("keydown", (e) => {
  S.lastKey = Date.now();
  if (S.standby) { e.preventDefault(); api("/api/power", { action: "wake" }); return; }
  if (S.ambient) { e.preventDefault(); stopAmbient(); return; }
  if (OSK.open && OSK.key(e)) { e.preventDefault(); return; }
  const k = e.key;
  if (!Nav.layers.length && typeof Views !== "undefined" && Views[S.view] && !BACK_KEYS.has(k) && Views[S.view].key(k, e)) { e.preventDefault(); return; }
  if (k.startsWith("Arrow")) {
    if (OSK.open) OSK.typed = false;   // Navigation mit Fernbedienung: OK drückt wieder die markierte Taste
    e.preventDefault();
    const dir = k.slice(5).toLowerCase();
    const cur = Nav.current;
    if (cur && cur.dataset.lr && (dir === "left" || dir === "right") && Nav.valid()) {
      const fn = Actions[cur.dataset.lr]; if (fn) { fn(cur.dataset, cur, dir === "right" ? 1 : -1); return; }
    }
    Nav.move(dir);
    return;
  }
  if (k === "Enter" || k === "NumpadEnter") {
    e.preventDefault();
    if (e.repeat) {
      if (!enterLong && enterTimer === null) enterTimer = setTimeout(() => { enterLong = true; openContext(); }, 450);
      return;
    }
    enterLong = false;
    return;
  }
  if (/^[0-9]$/.test(k) && !Nav.layers.length && (S.view === "home") && Dial.key(k)) { e.preventDefault(); return; }
  if (BACK_KEYS.has(k) && Dial.digits) { e.preventDefault(); Dial.cancel(); return; }
  if (BACK_KEYS.has(k)) { e.preventDefault(); back(); return; }
  if (k === "ContextMenu" || k === "F10") { e.preventDefault(); openContext(); return; }
  if (k === "F1" || k === "F2" || k === "F3" || k === "F4") { e.preventDefault(); colorKey(k); }
});
document.addEventListener("keyup", (e) => {
  if (e.key !== "Enter" && e.key !== "NumpadEnter") return;
  clearTimeout(enterTimer); enterTimer = null;
  if (enterLong) { enterLong = false; return; }
  if (S.standby || S.ambient) return;
  if (!Nav.valid()) { Nav.first(); return; }
  if (OSK.open && OSK.typed) { OSK.close(true); return; }   // mit echter Tastatur getippt: Enter = Fertig
  activate(Nav.current);
});
document.addEventListener("mousemove", () => { S.lastKey = Date.now(); });
document.addEventListener("click", (e) => {
  const el = e.target.closest(".f"); if (!el) return;
  Nav.focus(el, { scroll: false }); activate(el);
});

function colorKey(k) {
  // Farbtasten: Rot = Live-TV, Grün = TV-Programm, Gelb = Apps, Blau = Einstellungen
  if (Nav.layers.length) return;
  if (k === "F1") launchApp("livetv");
  else if (k === "F2") launchApp("tvguide");
  else if (k === "F3") { goHome("apps"); }
  else openSettings();
}

function back() {
  if (OSK.open) { OSK.close(false); return; }
  if (Nav.layers.length) {
    const layer = Nav.layers[Nav.layers.length - 1];
    if (layer.onBack) layer.onBack(); else Nav.pop();
    return;
  }
  if (typeof Views !== "undefined" && Views[S.view]) { Views[S.view].back(); return; }
  if (S.view === "settings") { goHome(S.tab, "#btn-settings"); return; }
  if (S.view === "radio" || S.view === "share" || S.view === "planner") { goHome(S.tab); return; }
  if (S.view === "files") { filesBack(); return; }
  if (S.view === "installer") { if (!S.installRunning) { S.installMode = false; S.settings.setup_done ? openSettings("system") : renderWizard(); } return; }
  if (S.view === "wizard") { if (S.wz > 0) { S.wz--; renderWizard(); } return; }
  if (S.view === "home") {
    if (S.tab === "media" && S.media.dir) { mediaUp(); return; }
    if (S.tab !== "foryou") { S.tab = "foryou"; renderHome(); Nav.first(".tab.active"); return; }
    const sc = $(".home-scroll");
    if (sc && sc.scrollTop > 10) { Nav.first(".tab.active"); return; }
  }
}

/* ====================================================================== Dialoge, Menüs, Hinweise */
function overlay(html, cls = "") {
  const el = document.createElement("div");
  el.className = `overlay ${cls}`;
  el.innerHTML = html;
  document.body.appendChild(el);
  return el;
}

/** Auswahl-/Bestätigungsdialog. items: [{label, sub, value, icon, danger, selected}] */
function dialog({ title, text = "", items = [], buttons = [], onPick, onClose, wide = false }) {
  const list = items.length ? `<div class="list"><div class="list-inner">${items.map((it, i) => `
      <button class="item f${it.danger ? " danger" : ""}" data-act="dlg-pick" data-i="${i}" ${it.selected ? "data-autofocus=1" : ""}>
        ${it.icon ? `<span class="ic">${icon(it.icon)}</span>` : ""}
        <span class="txt"><div class="t">${esc(it.label)}</div>${it.sub ? `<div class="s">${esc(it.sub)}</div>` : ""}</span>
        ${it.selected ? `<span class="chev">${icon("check")}</span>` : ""}
      </button>`).join("")}</div></div>` : "";
  const btns = buttons.length ? `<div class="actions">${buttons.map((b, i) => `
      <button class="btn f${b.primary ? " primary" : ""}${b.danger ? " danger" : ""}" data-act="dlg-btn" data-i="${i}" ${b.focus ? "data-autofocus=1" : ""}>${esc(b.label)}</button>`).join("")}</div>` : "";
  const el = overlay(`<div class="dialog" style="${wide ? "width:1100px" : ""}"><h2>${esc(title)}</h2>${text ? `<p>${text}</p>` : ""}${list}${btns}</div>`);
  const close = () => { Nav.pop(); if (onClose) onClose(); };
  Nav.push(el, { onBack: close });
  el._items = items; el._buttons = buttons; el._onPick = onPick; el._close = close;
  Nav.first();
  return el;
}
act("dlg-pick", (d, btn) => {
  const el = btn.closest(".overlay"); const it = el._items[+d.i];
  Nav.pop(); if (el._onPick) el._onPick(it.value !== undefined ? it.value : it, it);
});
act("dlg-btn", (d, btn) => {
  const el = btn.closest(".overlay"); const b = el._buttons[+d.i];
  Nav.pop(); if (b.action) b.action();
});

function confirmBox(title, text, okLabel, onOk, danger = false) {
  dialog({ title, text, buttons: [{ label: t("cancel"), focus: true }, { label: okLabel, primary: !danger, danger, action: onOk }] });
}

function toast(msg, ic = "info") {
  let box = $(".toasts");
  if (!box) { box = document.createElement("div"); box.className = "toasts"; document.body.appendChild(box); }
  const el = document.createElement("div");
  el.className = "toast";
  el.innerHTML = `${icon(ic)}<span>${esc(msg)}</span>`;
  box.appendChild(el);
  setTimeout(() => el.remove(), 4500);
}

/* ====================================================================== Bildschirmtastatur (+ Handy-Eingabe) */
const OSK = {
  open: false, value: "", shift: false, mode: "abc", el: null, cb: null, secret: false, show: false, token: null,
  layouts: {
    abc_de: ["1234567890", "qwertzuiopü", "asdfghjklöä", "yxcvbnm-_.@"],
    abc_en: ["1234567890", "qwertyuiop", "asdfghjkl", "zxcvbnm-_.@"],
    sym: ["1234567890", "!?#$%&*+=/", ":;,'\"()[]{}", "<>|\\~^`€§°"],
  },
  async start({ title = "", value = "", secret = false, placeholder = "", onDone }) {
    this.open = true; this.typed = false; this.value = value || ""; this.cb = onDone; this.secret = secret; this.show = !secret;
    this.shift = false; this.mode = "abc"; this.placeholder = placeholder; this.title = title;
    this.el = overlay(`<div class="dialog osk"><h2>${esc(title)}</h2><div class="osk-field"></div><div class="osk-body"><div class="osk-keys"></div><div class="osk-phone"></div></div></div>`);
    Nav.push(this.el, { onBack: () => this.close(false) });
    this.render();
    const ph = await api("/api/phone/start", { label: title, secret });
    if (this.open && ph && ph.url) {
      this.token = ph.token;
      $(".osk-phone", this.el).innerHTML = ph.qr ? `<div class="qr">${ph.qr}</div>${esc(t("osk_phone"))}` :
        `${esc(t("osk_phone_url"))}<div class="mono" style="margin-top:8px;color:var(--text)">${esc(ph.url)}</div>`;
    }
  },
  rows() {
    const lang = S.settings.lang === "en" ? "en" : "de";
    const rows = this.mode === "sym" ? this.layouts.sym : this.layouts[`abc_${lang}`];
    return rows.map((r) => [...r].map((c) => (this.shift && this.mode !== "sym" ? c.toUpperCase() : c)));
  },
  render(keepFocus = true) {
    const focusedKey = keepFocus && Nav.current ? Nav.current.dataset.k : null;
    const shown = this.secret && !this.show ? "•".repeat(this.value.length) : this.value;
    $(".osk-field", this.el).innerHTML = `${icon(this.secret ? "lock" : "keyboard")}<span class="txt">${shown ? esc(shown) : `<span class="ph">${esc(this.placeholder)}</span>`}</span><span class="caret"></span>`;
    const keyRow = (keys) => `<div class="osk-row">${keys.map((k) => `<button class="key f" data-act="osk" data-k="${esc(k)}">${esc(k)}</button>`).join("")}</div>`;
    const extra = [
      `<button class="key wide f${this.shift ? " on" : ""}" data-act="osk" data-k="{shift}">⇧</button>`,
      `<button class="key wide f" data-act="osk" data-k="{mode}">${this.mode === "sym" ? "abc" : "#+="}</button>`,
      `<button class="key xwide f" data-act="osk" data-k=" ">␣</button>`,
      `<button class="key wide f" data-act="osk" data-k="{bs}">${icon("back")}</button>`,
      this.secret ? `<button class="key f" data-act="osk" data-k="{eye}">${icon("eye")}</button>` : "",
      `<button class="key wide act f" data-act="osk" data-k="{ok}">${esc(t("done"))}</button>`,
    ].join("");
    const helpers = keyRow(["https://", "http://", "www.", "/", ":", ".de", ".com"]);
    $(".osk-keys", this.el).innerHTML = this.rows().map(keyRow).join("") + helpers + `<div class="osk-row">${extra}</div>`;
    const target = focusedKey !== null && $$(".key", this.el).find((b) => b.dataset.k.toLowerCase() === String(focusedKey).toLowerCase());
    Nav.focus(target || $$(".key", this.el)[11] || $(".key", this.el), { scroll: false });
  },
  press(k) {
    if (k === "{shift}") { this.shift = !this.shift; this.render(); return; }
    if (k === "{mode}") { this.mode = this.mode === "sym" ? "abc" : "sym"; this.render(); return; }
    if (k === "{bs}") { this.value = this.value.slice(0, -1); this.render(); return; }
    if (k === "{eye}") { this.show = !this.show; this.render(); return; }
    if (k === "{ok}") { this.close(true); return; }
    this.value += k;
    if (this.shift && k.length === 1) this.shift = false;
    this.render();
  },
  key(e) {
    // Physische Tastatur: Zeichen direkt übernehmen
    if (e.key.length === 1 && !e.ctrlKey && !e.altKey && !e.metaKey) { this.value += e.key; this.typed = true; this.render(); return true; }
    if (e.key === "Backspace" && this.value) { this.value = this.value.slice(0, -1); this.render(); return true; }
    return false;
  },
  phone(token, text) { if (this.open && (token === this.token || token === "remote")) { this.value = text; this.close(true); } },
  close(ok) {
    if (!this.open) return;
    this.open = false;
    if (this.token) api("/api/phone/stop", { token: this.token });
    this.token = null;
    Nav.pop();
    if (ok && this.cb) this.cb(this.value);
  },
};
act("osk", (d) => OSK.press(d.k));

/* ====================================================================== Bausteine für Listen */
function item({ act: a, icon: ic, title, sub = "", val = "", toggle, chev = true, cls = "", data = {}, lr }) {
  const attrs = Object.entries(data).map(([k, v]) => `data-${k}="${esc(v)}"`).join(" ");
  const right = toggle !== undefined ? `<span class="toggle${toggle ? " on" : ""}"></span>`
    : `${val !== "" ? `<span class="val">${val}</span>` : ""}${chev && a ? `<span class="chev">${icon("chev")}</span>` : ""}`;
  return `<button class="item${a ? " f" : " static"} ${cls}" ${a ? `data-act="${a}"` : ""} ${lr ? `data-lr="${lr}"` : ""} ${attrs}>
    ${ic ? `<span class="ic">${icon(ic)}</span>` : ""}
    <span class="txt"><div class="t">${title}</div>${sub ? `<div class="s">${sub}</div>` : ""}</span>${right}</button>`;
}
const section = (title) => `<div class="set-section">${esc(title)}</div>`;
const callout = (html, warn = false) => `<div class="callout${warn ? " warn" : ""}">${icon(warn ? "alert" : "info")}<div>${html}</div></div>`;
function signalBars(sig) {
  const n = sig > 75 ? 4 : sig > 55 ? 3 : sig > 35 ? 2 : 1;
  return `<span class="signal">${[6, 11, 16, 22].map((h, i) => `<i class="${i < n ? "on" : ""}" style="height:${h}px"></i>`).join("")}</span>`;
}

/* ====================================================================== Einstellungen speichern */
async function saveSettings(patch) {
  const res = await api("/api/settings", patch);
  if (res && res.settings) { S.settings = res.settings; applyTheme(); }
  return res;
}

/* Designs */
const THEMES = {
  wangert: ["#22e1ff", "#1477ff", "#0b2f8a"], oled: ["#9cc3ff", "#11151d", "#000"], aurora: ["#3ef0b0", "#0fa37f", "#041a15"],
  sunset: ["#ffb04a", "#ff4f7b", "#210812"], neon: ["#d39bff", "#7b3cff", "#130833"], graphite: ["#a8c7fa", "#2a2f38", "#111214"],
  ocean: ["#4fd1ff", "#0077b6", "#011627"], forest: ["#9be15d", "#2e8b57", "#06140b"], rose: ["#ff9ac1", "#d6336c", "#1a0610"],
  midnight: ["#8fa8ff", "#3949ab", "#05061a"], ember: ["#ff7849", "#c0392b", "#140604"],
};
// Akzentfarben (Fokus, Fortschritt, Schaltflächen) – unabhängig vom Design wählbar
const ACCENTS = {
  cyan: ["#22e1ff", "#1477ff"], mint: ["#3ef0b0", "#0fa37f"], gold: ["#ffd24a", "#ff9800"], coral: ["#ff8a65", "#e64a19"],
  red: ["#ff5d6c", "#c2185b"], pink: ["#ff8ad8", "#b83dba"], violet: ["#c58bff", "#6a3cff"], blue: ["#8fb8ff", "#3d6fff"],
  lime: ["#c6f24e", "#5fa52b"], white: ["#ffffff", "#8da2bf"],
};
function applyTheme() {
  const th = THEMES[S.settings.theme] ? S.settings.theme : "wangert";
  const root = document.documentElement;
  root.dataset.theme = th;
  const acc = ACCENTS[S.settings.accent];
  for (const v of ["--cyan", "--blue", "--ring", "--accent"]) root.style.removeProperty(v);
  if (acc) {
    root.style.setProperty("--cyan", acc[0]); root.style.setProperty("--blue", acc[1]); root.style.setProperty("--accent", acc[0]);
    root.style.setProperty("--ring", `${acc[0]}59`);
  }
  document.body.classList.toggle("calm", S.settings.motion === "calm");
}
function themeItems() {
  let h = section(t("design_themes"));
  h += Object.entries(THEMES).map(([id, [a, b, c]]) => `
    <button class="item f" data-act="theme" data-v="${id}">
      <span class="theme-swatch" style="background:linear-gradient(120deg, ${a} 0%, ${b} 55%, ${c} 100%)"></span>
      <span class="txt"><div class="t">${esc(t(`theme_${id}`))}</div><div class="s">${esc(t(`theme_${id}_sub`))}</div></span>
      ${S.settings.theme === id ? `<span class="chev">${icon("check")}</span>` : ""}</button>`).join("");
  h += section(t("design_accent"));
  h += `<div class="accent-row" data-row="accents"><button class="accent f${!S.settings.accent ? " on" : ""}" data-act="accent" data-v="" title="${esc(t("accent_theme"))}">
      <span style="background:conic-gradient(#22e1ff,#ff4f7b,#3ef0b0,#ffd24a,#22e1ff)"></span><small>${esc(t("accent_theme"))}</small></button>
    ${Object.entries(ACCENTS).map(([id, [a, b]]) => `<button class="accent f${S.settings.accent === id ? " on" : ""}" data-act="accent" data-v="${id}">
      <span style="background:linear-gradient(135deg, ${a}, ${b})"></span><small>${esc(t(`accent_${id}`))}</small></button>`).join("")}</div>`;
  h += section(t("design_effects"));
  h += item({ act: "toggle", icon: "sparkle", title: t("motion_calm"), sub: t("motion_calm_sub"), toggle: S.settings.motion === "calm", data: { k: "motion", on: "calm", off: "full" } });
  return h;
}
act("theme", async (d) => { await saveSettings({ theme: d.v }); refreshPanel(); });
act("accent", async (d) => { await saveSettings({ accent: d.v }); applyTheme(); refreshPanel(); Nav.first(`.accent[data-v="${d.v}"]`); });

/* ====================================================================== Apps starten */
async function launchApp(id, extra = {}) {
  const app = S.apps.all.find((a) => a.id === id) || S.apps.home.find((a) => a.id === id);
  if ((id === "livetv" || id === "tvguide") && !S.channelsConfigured && !extra.force) {
    dialog({
      title: t("livetv_not_setup"), text: t("livetv_not_setup_text"),
      buttons: [{ label: t("later") }, { label: t("open_kodi_anyway"), action: () => launchApp(id, { force: true }) },
        { label: t("setup_now"), primary: true, focus: true, action: () => openSettings("livetv") }],
    });
    return;
  }
  if (app && app.installable && !app.available) {
    confirmBox(t("install_app_q", { name: app.name }), t("install_app_text"), t("install"), async () => {
      await api("/api/apps/install", { id }); toast(t("installing", { name: app.name }), "download");
    });
    return;
  }
  const res = await api("/api/apps/launch", { id, ...extra });
  if (res.internal === "settings") { openSettings(); return; }
  if (res.internal === "epg") { openEpg(); return; }
  if (res.internal === "live") { focusLive(); return; }
  if (res.internal === "radio") { openRadio(); return; }
  if (res.internal === "tvplaner") { openPlanner(); return; }
  if (res.internal === "files") { openFiles(); return; }
  if (res.internal === "share") { openShare(); return; }
  if (res.internal === "player") { S.media.dir = null; await loadMedia(); goHome("media"); Nav.first(".tab.active"); return; }
  if (!res.ok) {
    if (res.message === "not_installed" && app && app.type === "flatpak") {
      confirmBox(t("install_app_q", { name: app.name }), t("install_app_text"), t("install"), async () => {
        await api("/api/apps/install", { id }); toast(t("installing", { name: app.name }), "download");
      });
    } else toast(t("launch_failed", { name: app ? app.name : id }), "alert");
  }
}
act("launch", (d) => launchApp(d.id, d.view ? { view: d.view } : {}));
act("channel", (d) => playChannel(d.name));
act("android-app", (d) => api("/api/android/launch", { package: d.pkg, name: d.name }));
act("running-close", async (d) => { await api("/api/apps/close", { key: d.key }); await loadApps(); refreshView(); });

/* Kontextmenü (Menü-Taste oder OK lange drücken) */
function openContext() {
  const el = Nav.current; if (!el || Nav.layers.length) return;
  if (el.dataset.act === "radio-play") {
    const st = S.radio.map[el.dataset.id]; if (!st) return;
    const fav = S.radio.favorites.some((f) => f.id === st.id);
    dialog({ title: st.name, text: esc(st.tags || ""), items: [{ label: t("radio_play"), icon: "play", value: "play" },
      { label: t(fav ? "fav_remove" : "fav_add"), icon: "star", value: "fav" }],
    onPick: (v) => { if (v === "play") Actions["radio-play"]({ id: st.id }); else radioToggleFav(st.id); } });
    return;
  }
  if (el.dataset.act === "files-open" && typeof filesContext === "function") { filesContext(el); return; }
  if (el.dataset.act === "channel") {
    const name = el.dataset.name; const fav = isFav(name);
    const ch = S.channels.find((c) => c.name === name) || {};
    dialog({
      title: name, text: epgText(ch),
      items: [{ label: t("watch_now"), icon: "play", value: "play" },
        { label: t(fav ? "fav_remove" : "fav_add"), icon: "star", value: "fav" }],
      onPick: async (v) => {
        if (v === "play") playChannel(name);
        else { await toggleFav(name); refreshView(); const again = $(`[data-act="channel"][data-name="${CSS.escape(name)}"]`); if (again) Nav.focus(again); }
      },
    });
    return;
  }
  if (el.dataset.act === "launch" && el.closest("[data-row='apps']")) {
    const id = el.dataset.id;
    const app = S.apps.home.find((a) => a.id === id);
    const order = [...S.settings.apps]; const idx = order.indexOf(id);
    const items = [{ label: t("open"), icon: "play", value: "open" }];
    if (idx > 0) items.push({ label: t("move_left"), icon: "left", value: "left" });
    if (idx >= 0 && idx < order.length - 1) items.push({ label: t("move_right"), icon: "right", value: "right" });
    if (app && app.running) {
      items.push({ label: t("restart_app"), sub: t("restart_app_sub"), icon: "refresh", value: "restart" });
      items.push({ label: t("close_app"), icon: "stop", value: "close" });
    }
    items.push({ label: t("remove_from_home"), icon: "trash", value: "remove", danger: true });
    dialog({
      title: app ? app.name : id, items, onPick: async (v) => {
        if (v === "open") launchApp(id);
        else if (v === "close" || v === "restart") {
          await api("/api/apps/close", { key: ["kodi", "kodi_addon"].includes(app.type) ? "kodi" : (app.type === "android" ? "android" : id) });
          await loadApps(); renderHome();
          if (v === "restart") { await sleep(1500); launchApp(id); }
        }
        else if (v === "remove") { order.splice(idx, 1); await saveSettings({ apps: order }); await loadApps(); renderHome(); }
        else {
          const j = v === "left" ? idx - 1 : idx + 1;
          [order[idx], order[j]] = [order[j], order[idx]];
          await saveSettings({ apps: order }); await loadApps(); renderHome();
          const moved = $(`[data-row='apps'] [data-id='${id}']`); if (moved) Nav.focus(moved);
        }
      },
    });
  }
}

/* ====================================================================== Daten laden */
async function loadState() {
  const st = await api("/api/state");
  if (st && st.settings) { S.state = st; S.settings = st.settings; S.nowplaying = st.nowplaying || {}; S.standby = !!st.standby; S.sleepEnds = st.sleep_timer || 0; applyTheme(); }
}
async function loadApps() { const a = await api("/api/apps"); if (a && a.home) S.apps = a; }
async function loadUsb() { const u = await api("/api/usb"); S.usb = (u && u.drives) || []; }
act("usb-kodi", (d) => launchApp("kodi", { view: `path:${d.mount}` }));
act("usb-eject", async (d) => {
  const r = await api("/api/usb/eject", { path: d.path });
  toast(r && r.ok ? t("usb_ejected", { name: d.name }) : t("usb_busy"), r && r.ok ? "check" : "alert");
  await loadUsb(); S.heroIndex = 0; if (S.view === "home") refreshView();
});
async function loadChannels() {
  const c = await api("/api/channels");
  if (c && c.channels) { S.channels = c.channels; S.channelsConfigured = c.configured; S.channelsWaipu = !!c.waipu; }
}
async function loadWeather() {
  clearTimeout(loadWeather.retry);
  if (!S.settings.city) { S.weather = null; return; }
  const had = !!S.weather;
  const w = await api("/api/weather");
  if (w && w.ok) S.weather = w;
  else if (!w || w.message !== "city_not_found") {
    // Internet noch nicht da (z. B. direkt nach dem Einschalten): alle 30 s erneut versuchen, altes Wetter behalten
    loadWeather.tries = (loadWeather.tries || 0) + 1;
    if (loadWeather.tries < 60) loadWeather.retry = setTimeout(loadWeather, 30 * 1000);
    return;
  } else S.weather = null;
  if (w.ok && w.stale) { loadWeather.retry = setTimeout(loadWeather, 60 * 1000); }   // veraltet: bald frisch holen
  loadWeather.tries = 0;
  if (!had && S.view === "home" && !Nav.layers.length) refreshView();               // Wetter oben rechts einblenden
}

/* ====================================================================== Kopfzeile */
function clockText() { return new Date().toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit" }); }
function topbar() {
  const tabs = [["foryou", t("tab_foryou")], ["live", t("tab_live")], ["media", t("tab_media")], ["apps", t("tab_apps")]];
  const w = S.weather;
  const upd = S.updates && S.updates.total > 0;
  return `<header class="topbar">
    <div class="brand"><svg><use href="#logo-mark"/></svg><span>Wangert<b>TV</b></span></div>
    <nav class="tabs" data-row="tabs">${tabs.map(([id, label]) => `<button class="tab f${S.tab === id ? " active" : ""}" data-act="tab" data-tab="${id}">${esc(label)}</button>`).join("")}</nav>
    <div class="top-right">
      ${S.sleepEnds ? `<div class="chip">${icon("moon")}<span id="sleep-left"></span></div>` : ""}
      ${w ? `<div class="chip">${icon(weatherIcon(w.code, w.is_day))}<span>${w.temp}°</span></div>` : ""}
      <div class="clock" id="clock">${clockText()}</div>
      <button class="icon-btn f" id="btn-settings" data-act="settings" style="position:relative">${icon("settings")}${upd ? '<span class="badge"></span>' : ""}</button>
      <button class="icon-btn f" id="btn-power" data-act="power-menu">${icon("power")}</button>
    </div></header>`;
}
act("tab", async (d) => {
  if (S.tab === d.tab && !(d.tab === "media" && S.media.dir)) return;
  S.tab = d.tab;
  if (d.tab === "media") { S.media.dir = null; if (!S.media.loaded) await loadMedia(); else loadMedia().then(() => { if (S.tab === "media" && !S.media.dir && S.view === "home" && !Nav.layers.length) refreshView(); }); }
  renderHome(); Nav.first(`.tab[data-tab='${d.tab}']`);
});
act("settings", () => openSettings());
act("power-menu", () => openPowerMenu());

/* ====================================================================== Startseite */
function appTile(app, { small = false } = {}) {
  const sub = app.running ? t("running") : (app.installable && !app.available ? t("tap_to_install") : "");
  // Offizielles App-Symbol (von der App/dem Dienst bereitgestellt) – oder 16:9-Banner aus /etc/wangerttv/icons
  const art = app.icon ? (app.banner ? `<img class="app-icon" src="${esc(app.icon)}" alt="">` : `<img class="app-logo" src="${esc(app.icon)}" alt="">`)
    : `<span class="glyph">${icon(app.glyph)}</span>`;
  return `<button class="tile f${small ? " small" : ""}" data-act="launch" data-id="${esc(app.id)}" style="--c1:${esc(app.color)};--c2:${esc(app.color2)}">
    ${art}${app.running ? '<span class="run-dot"></span>' : ""}
    ${app.banner ? "" : `<span class="name">${esc(app.name)}</span>${sub ? `<span class="sub">${esc(sub)}</span>` : ""}`}
  </button>`;
}
/* Lieblingssender & zuletzt gesehen */
function chPrefs() { return S.settings.channel_prefs || { favorites: [], recent: [] }; }
function isFav(name) { return (chPrefs().favorites || []).includes(name); }
function favChannels() { const f = chPrefs().favorites || []; return f.map((n) => S.channels.find((c) => c.name === n)).filter(Boolean); }
function recentChannels() { const r = chPrefs().recent || []; return r.map((n) => S.channels.find((c) => c.name === n)).filter(Boolean); }
async function toggleFav(name) {
  const p = chPrefs(); const fav = [...(p.favorites || [])];
  const i = fav.indexOf(name);
  if (i >= 0) fav.splice(i, 1); else fav.push(name);
  await saveSettings({ channel_prefs: { favorites: fav, recent: p.recent || [] } });
  toast(t(i >= 0 ? "fav_removed" : "fav_added", { name }), "star");
}
async function rememberChannel(name) {
  const p = chPrefs(); const rec = [name, ...(p.recent || []).filter((n) => n !== name)].slice(0, 10);
  await saveSettings({ channel_prefs: { favorites: p.favorites || [], recent: rec } });
}
function playChannel(name) {
  rememberChannel(name);
  launchApp("livetv", { channel: name, force: true });
}

/* Direktwahl: Zifferntasten der Fernbedienung -> Sendernummer */
const Dial = {
  digits: "", timer: null, el: null,
  key(d) {
    if (!S.channels.length) return false;
    this.digits = (this.digits + d).slice(-4);
    if (!this.el) { this.el = document.createElement("div"); this.el.className = "dial"; document.body.appendChild(this.el); }
    const ch = this.find();
    this.el.innerHTML = `<b>${esc(this.digits)}</b><span>${esc(ch ? ch.name : t("dial_none"))}</span>`;
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.go(), 1600);
    return true;
  },
  find() { const n = Number(this.digits); return S.channels.find((c) => Number(c.number) === n); },
  go() {
    const ch = this.find();
    this.cancel();
    if (ch) playChannel(ch.name); else toast(t("dial_none"), "tv");
  },
  cancel() { clearTimeout(this.timer); this.timer = null; this.digits = ""; if (this.el) { this.el.remove(); this.el = null; } },
};

/* TV-Programm: laufende Sendung */
function hm(ts) { const d = new Date(ts * 1000); return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`; }
function liveNow(ch) {
  const now = Date.now() / 1000;
  if (ch.now && ch.now.start <= now && now < ch.now.end) return ch.now;
  if (ch.next && ch.next.start <= now && now < ch.next.end) return ch.next;   // Daten älter als die Sendung
  return null;
}
function epgHtml(ch) {
  const cur = liveNow(ch); if (!cur) return "";
  const pct = Math.max(0, Math.min(100, ((Date.now() / 1000 - cur.start) / (cur.end - cur.start)) * 100));
  return `<span class="now">${esc(cur.title)}</span><span class="bar"><i style="width:${pct.toFixed(1)}%"></i></span>`;
}
function epgText(ch) {
  const cur = liveNow(ch); const nxt = cur === ch.next ? null : ch.next;
  const lines = [];
  if (cur) lines.push(`<b>${esc(t("epg_now"))}</b> ${esc(cur.title)} <span class="dim">(${hm(cur.start)}–${hm(cur.end)}, ${esc(t("epg_left", { n: Math.max(1, Math.round((cur.end - Date.now() / 1000) / 60)) }))})</span>`);
  if (nxt) lines.push(`<b>${esc(t("epg_next"))}</b> ${esc(nxt.title)} <span class="dim">(${hm(nxt.start)})</span>`);
  return lines.join("<br>");
}

function channelTile(ch) {
  const initials = esc(ch.name.replace(/\b(HD|SD|UHD|FHD)\b/g, "").trim());
  const epg = epgHtml(ch);
  return `<button class="tile channel f${epg ? " epg" : ""}" data-act="channel" data-name="${esc(ch.name)}">
    <span class="num">${ch.number || ""}</span>${isFav(ch.name) ? `<span class="fav">${icon("star")}</span>` : ""}${ch.quality ? `<span class="q-badge${ch.quality === "UHD" ? " uhd" : ""}">${esc(ch.quality)}</span>` : ""}
    <span class="logo">${ch.logo ? `<img src="${esc(ch.logo)}" alt="" onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'fallback',textContent:${JSON.stringify(ch.name)}}))">` : `<span class="fallback">${initials}</span>`}</span>
    ${epg || (ch.logo ? `<span class="cname">${esc(ch.name)}</span>` : "")}
  </button>`;
}

function heroSlides() {
  const slides = [];
  const greet = typeof greetingSlide === "function" ? greetingSlide() : null;
  if (greet) slides.push(greet);
  const np = S.nowplaying;
  if (np && np.source === "spotify" && np.title) {
    slides.push({ kicker: t("hero_np_kicker"), title: np.title, text: np.artist || "", cover: np.cover, glyph: "music",
      actions: [{ label: t("open_spotify"), act: "launch", data: { id: np.via === "spotify_app" ? "spotify_desktop" : "spotify" }, icon: "music", primary: true }] });
  }
  const rs = S.radio.state;
  if (rs.playing && rs.station) {
    stationKey(rs.station);
    slides.push({ kicker: t("radio_playing"), title: rs.title || rs.station.name, text: rs.title ? rs.station.name : (rs.station.tags || ""),
      cover: rs.cover || rs.station.favicon || "", glyph: "radio", c1: "#7b3cff", c2: "#0f6157",
      actions: [{ label: t("radio_stop"), act: "radio-stop", data: {}, icon: "stop", primary: true },
        { label: t("radio"), act: "launch", data: { id: "radio" }, icon: "radio" }] });
  }
  for (const u of S.usb || []) {
    slides.push({ kicker: t("usb_kicker"), title: u.label, text: t("usb_text", { size: fmtBytes(u.size), free: u.free != null ? fmtBytes(u.free) : "—" }), glyph: "usb",
      actions: [{ label: t("usb_player"), act: "usb-player", data: { mount: u.mount }, icon: "play", primary: true },
        { label: t("usb_kodi"), act: "usb-kodi", data: { mount: u.mount }, icon: "kodi" },
        { label: t("usb_eject"), act: "usb-eject", data: { path: u.path, name: u.label }, icon: "usb" }] });
  }
  const last = recentChannels()[0];
  if (last) {
    const cur = liveNow(last);
    slides.push({ kicker: t("hero_resume_kicker"), title: last.name,
      text: cur ? t("hero_resume_now", { title: cur.title, n: Math.max(1, Math.round((cur.end - Date.now() / 1000) / 60)) }) : t("hero_resume_text"),
      glyph: "tv", cover: last.logo || "",
      actions: [{ label: t("watch_now"), act: "channel", data: { name: last.name }, icon: "play", primary: true },
        { label: t("all_channels"), act: "tab", data: { tab: "live" }, icon: "grid" }] });
  }
  if (S.channels.length) {
    slides.push({ kicker: t("hero_live_kicker"), title: t("hero_live_title"), text: t("hero_live_text", { n: S.channels.length }), glyph: "tv",
      actions: [{ label: t("watch_now"), act: "launch", data: { id: "livetv" }, icon: "tv", primary: true },
        { label: t("tv_guide"), act: "launch", data: { id: "tvguide" }, icon: "guide" }] });
  } else if (!S.channelsConfigured) {
    slides.push({ kicker: t("hero_setup_kicker"), title: t("hero_setup_title"), text: t("hero_setup_text"), glyph: "tv",
      actions: [{ label: t("setup_now"), act: "settings-cat", data: { cat: "livetv" }, icon: "settings", primary: true }] });
  }
  if (S.updates && S.updates.total > 0) {
    slides.push({ kicker: t("hero_update_kicker"), title: t("hero_update_title", { n: S.updates.total }), text: t("hero_update_text"), glyph: "update",
      actions: [{ label: t("update_now"), act: "settings-cat", data: { cat: "updates" }, icon: "update", primary: true }] });
  }
  slides.push({ kicker: t("hero_cast_kicker"), title: t("hero_cast_title"), text: t("hero_cast_text", { name: S.settings.device_name }), glyph: "cast",
    actions: [{ label: t("tab_apps"), act: "tab", data: { tab: "apps" }, icon: "apps", primary: true }] });
  return slides;
}
act("settings-cat", (d) => openSettings(d.cat));

function homeForYou() {
  const apps = S.apps.home;
  let html = morningRow() + `<div class="row"><div class="row-title">${esc(t("your_apps"))}</div><div class="row-track" data-row="apps">
    ${apps.map((a) => appTile(a)).join("")}
    <button class="tile add f" data-act="add-app">${icon("plus")}<span class="name">${esc(t("add_app"))}</span></button></div></div>`;
  if ((S.media.recent || []).length) {
    html += `<div class="row"><div class="row-title">${esc(t("media_continue"))}</div>
      <div class="row-track" data-row="continue">${S.media.recent.map((e) => mediaTile({ ...e, type: e.audio ? "audio" : "video" }, { act: "media-resume" })).join("")}</div></div>`;
  }
  html += picksRow() + showsRow() + todayRow();
  html += primeHomeRow();
  if (typeof radioHomeRow === "function") html += radioHomeRow();
  const favs = favChannels(), recent = recentChannels().filter((c) => !isFav(c.name)).slice(0, 8);
  if (favs.length) {
    html += `<div class="row"><div class="row-title">${esc(t("fav_channels"))}<small>${esc(t("fav_hint"))}</small></div>
      <div class="row-track" data-row="favs">${favs.map(channelTile).join("")}</div></div>`;
  }
  if (recent.length) {
    html += `<div class="row"><div class="row-title">${esc(t("recent_channels"))}</div>
      <div class="row-track" data-row="recent">${recent.map(channelTile).join("")}</div></div>`;
  }
  if (S.channels.length) {
    html += `<div class="row"><div class="row-title">${esc(t("live_channels"))}<small>${esc(t("n_channels", { n: S.channels.length }))}</small></div>
      <div class="row-track" data-row="channels">${S.channels.slice(0, 24).map(channelTile).join("")}
      <button class="tile channel add f" data-act="tab" data-tab="live">${icon("grid")}<span class="name">${esc(t("all_channels"))}</span></button></div></div>`;
  }
  if (S.apps.running.length) {
    html += `<div class="row"><div class="row-title">${esc(t("running_apps"))}<small>${esc(t("running_hint"))}</small></div>
      <div class="row-track" data-row="running">${S.apps.running.map((a) => `
        <button class="tile small f" data-act="running-menu" data-key="${esc(a.key)}" data-id="${esc(a.id)}" style="--c1:${esc(a.color || "#24354d")};--c2:${esc(a.color2 || "#0d1626")}">
          <span class="glyph">${icon(a.glyph || "apps")}</span><span class="run-dot"></span><span class="name">${esc(a.name)}</span></button>`).join("")}</div></div>`;
  }
  if (S.androidApps && S.androidApps.length) {
    html += `<div class="row"><div class="row-title">${esc(t("android_apps"))}</div><div class="row-track" data-row="android">
      ${S.androidApps.map((a) => `<button class="tile small f" data-act="android-app" data-pkg="${esc(a.package)}" data-name="${esc(a.name)}" style="--c1:#1f6f4a;--c2:#0b2b1d">${a.icon ? `<img class="app-logo" src="${esc(a.icon)}" alt="">` : `<span class="glyph">${icon("android")}</span>`}<span class="name">${esc(a.name)}</span></button>`).join("")}</div></div>`;
  }
  return html;
}
act("running-menu", (d) => {
  dialog({ title: t("running_app"), items: [{ label: t("open"), icon: "play", value: "open" }, { label: t("close_app"), icon: "stop", value: "close", danger: true }],
    onPick: async (v) => {
      if (v === "open") { if (d.key === "kodi") launchApp("kodi"); else if (d.key === "android") api("/api/android/launch", {}); else launchApp(d.id); }
      else { await api("/api/apps/close", { key: d.key }); await loadApps(); renderHome(); }
    } });
});
act("add-app", () => {
  const avail = S.apps.all.filter((a) => !S.settings.apps.includes(a.id));
  if (!avail.length) { toast(t("all_apps_added")); return; }
  dialog({ title: t("add_app"), items: avail.map((a) => ({ label: a.name, sub: L(a.desc), icon: a.glyph, value: a.id })), onPick: async (id) => {
    await saveSettings({ apps: [...S.settings.apps, id] });
    await loadApps(); renderHome();
    const app = S.apps.home.find((a) => a.id === id);
    if (app && app.installable) { api("/api/apps/install", { id }); toast(t("installing", { name: app.name }), "download"); }
  } });
});

function homeApps() {
  const cats = [["stream", t("cat_stream")], ["tv", t("cat_tv")], ["media", t("cat_media")], ["music", t("cat_music")], ["apps", t("cat_apps")], ["system", t("cat_system")]];
  let html = '<div style="height:20px"></div>';
  for (const [cat, label] of cats) {
    const list = S.apps.all.filter((a) => a.category === cat);
    if (!list.length) continue;
    html += `<div class="row"><div class="row-title">${esc(label)}</div><div class="row-track" data-row="cat-${cat}">${list.map((a) => appTile(a, { small: true })).join("")}</div></div>`;
  }
  return html;
}

function homeLive() {
  if (!S.channelsConfigured) {
    return `<div style="height:30px"></div><div class="empty-note">${icon("tv", "i")}<div class="grow"><h3>${esc(t("hero_setup_title"))}</h3>${esc(t("hero_setup_text"))}</div>
      <button class="btn primary f" data-act="settings-cat" data-cat="livetv">${icon("settings")}${esc(t("setup_now"))}</button></div>`;
  }
  const groups = [...new Set(S.channels.map((c) => c.group).filter(Boolean))];
  const list = S.channelGroup === "\u2605" ? favChannels() : S.channelGroup ? S.channels.filter((c) => c.group === S.channelGroup) : S.channels;
  return `<div style="height:20px"></div>
    <div class="chips" data-row="live-actions">
      <button class="btn small primary f" data-act="launch" data-id="livetv">${icon("tv")}${esc(t("open_livetv"))}</button>
      <button class="btn small f" data-act="launch" data-id="tvguide">${icon("guide")}${esc(t("tv_guide"))}</button>
      <button class="btn small f" data-act="ched-live">${icon("sort")}${esc(t("ched_title"))}</button>
      <button class="btn small f" data-act="refresh-channels">${icon("refresh")}${esc(t("refresh_list"))}</button>
    </div>
    ${!S.channels.length && S.channelsWaipu ? `<div style="height:22px"></div><div class="empty-note">${icon("tv", "i")}<div class="grow"><h3>${esc(t("waipu_channels_title"))}</h3>${esc(t("waipu_channels_hint"))}</div></div>` : ""}
    ${groups.length > 1 || favChannels().length ? `<div class="chips" style="margin-top:22px" data-row="groups"><button class="pill f${!S.channelGroup ? " active" : ""}" data-act="group" data-g="">${esc(t("all"))}</button>${favChannels().length ? `<button class="pill f${S.channelGroup === "\u2605" ? " active" : ""}" data-act="group" data-g="\u2605">\u2605 ${esc(t("fav_channels"))}</button>` : ""}${groups.length > 1 ? groups.slice(0, 14).map((g) => `<button class="pill f${S.channelGroup === g ? " active" : ""}" data-act="group" data-g="${esc(g)}">${esc(g)}</button>`).join("") : ""}</div>` : ""}
    <div class="grid">${list.slice(0, 400).map(channelTile).join("")}</div>`;
}
act("ched-live", () => openChannelEditor("live"));
act("group", (d) => { S.channelGroup = d.g; renderHome(); Nav.first(`.pill[data-g="${CSS.escape(d.g)}"]`); });
act("refresh-channels", async () => {
  toast(t("loading_channels"), "refresh");
  await api("/api/channels/refresh", {});
  setTimeout(async () => { await loadChannels(); refreshView(); }, 3000);
});

/* ====================================================================== Medien (WangertTV Player) */
S.media = { sources: [], recent: [], dir: null, root: null, loaded: false };
const MEDIA_ICON = { dir: "folder", bluray: "disc", dvd: "disc", video: "film", audio: "music", image: "image", playlist: "grid",
  usb: "usb", film: "film", music: "music", download: "download", home: "home" };
const MEDIA_COLORS = { video: ["#3b2a7a", "#140c33"], audio: ["#0f6157", "#052420"], image: ["#8a4515", "#2e1505"],
  dir: ["#1d3b63", "#0a1628"], bluray: ["#0b47a8", "#051a40"], dvd: ["#6a1f80", "#22082c"], playlist: ["#34506f", "#101b29"],
  usb: ["#0f6b8f", "#052433"], film: ["#3b2a7a", "#140c33"], music: ["#0f6157", "#052420"], download: ["#2f5a2a", "#0e1f0c"], home: ["#30445e", "#101a28"] };
async function loadMedia() {
  const r = await api("/api/player/sources");
  if (r && r.sources) { S.media.sources = r.sources; S.media.recent = r.recent || []; S.media.loaded = true; }
}
function mediaLeft(e) {
  const left = Math.max(0, (e.duration || 0) - (e.pos || 0));
  return t("media_left", { t: left >= 3600 ? `${Math.floor(left / 3600)}:${String(Math.floor((left % 3600) / 60)).padStart(2, "0")} h` : `${Math.max(1, Math.round(left / 60))} min` });
}
function mediaTile(it, { act: a = null } = {}) {
  const type = it.type || it.icon || "dir";
  const [c1, c2] = MEDIA_COLORS[type] || MEDIA_COLORS.dir;
  const action = a || (["dir"].includes(type) ? "media-open" : "media-play");
  let sub = "";
  if (type === "dir") sub = t("media_folder");
  else if (type === "bluray") sub = "Blu-ray";
  else if (type === "dvd") sub = "DVD";
  else if (it.progress >= 1) sub = t("media_seen");
  else if (it.progress > 0 && it.duration) sub = mediaLeft(it);
  const ext = !it.title && it.name && /\.[a-z0-9]{2,5}$/i.test(it.name) && type !== "dir" ? it.name.split(".").pop().toUpperCase() : "";
  if (!sub && it.size) sub = ext ? `${ext} · ${fmtBytes(it.size)}` : fmtBytes(it.size);
  const name = it.title || (ext ? it.name.slice(0, -(ext.length + 1)) : it.name) || t("media_home");
  const bar = it.progress > 0 && it.progress < 1 ? `<span class="mbar"><i style="width:${Math.round(it.progress * 100)}%"></i></span>` : "";
  return `<button class="tile small media f" data-act="${action}" data-path="${esc(it.path)}" data-type="${esc(type)}" data-progress="${it.progress || 0}" data-name="${esc(name)}" style="--c1:${c1};--c2:${c2}">
    <span class="glyph">${icon(MEDIA_ICON[type] || "film")}</span>${it.progress >= 1 ? `<span class="seen">${icon("check")}</span>` : ""}
    <span class="name">${esc(name)}</span>${sub ? `<span class="sub">${esc(sub)}</span>` : ""}${bar}</button>`;
}
function homeMedia() {
  const m = S.media;
  if (m.dir) return mediaBrowser();
  let html = '<div style="height:20px"></div>';
  if (m.recent.length) {
    html += `<div class="row"><div class="row-title">${esc(t("media_continue"))}<small>${esc(t("media_continue_hint"))}</small></div>
      <div class="row-track" data-row="media-recent">${m.recent.map((e) => mediaTile({ ...e, type: e.audio ? "audio" : "video" }, { act: "media-resume" })).join("")}</div></div>`;
  }
  const usb = m.sources.filter((s) => s.id === "usb"), folders = m.sources.filter((s) => s.id !== "usb");
  html += `<div class="row"><div class="row-title">${esc(t("media_sources"))}</div><div class="row-track" data-row="media-sources">
    ${[...usb, ...folders].map((s) => mediaTile({ ...s, type: s.icon, name: s.id === "home" ? t("media_home") : s.name }, { act: "media-open" })).join("")}</div></div>`;
  if (!usb.length) {
    html += `<div class="empty-note">${icon("usb", "i")}<div class="grow"><h3>${esc(t("media_no_usb"))}</h3>${esc(t("media_no_usb_text"))}</div></div>`;
  }
  html += `<div class="callout media-info">${icon("play")}<div>${esc(t("media_formats"))}</div></div>`;
  return html;
}
function mediaBrowser() {
  const d = S.media.dir;
  const items = d.items || [];
  const playable = items.some((i) => i.type === "video" || i.type === "audio" || i.type === "image");
  const head = `<div class="media-head" data-row="media-head">
    <button class="btn small f" data-act="media-up">${icon("back")}${esc(t("back"))}</button>
    <div class="media-path">${icon(d.disc ? "disc" : "folder")}<div><b>${esc(d.name)}</b><small>${esc(d.path)}</small></div></div>
    ${d.disc ? `<button class="btn small primary f" data-act="media-play" data-path="${esc(d.path)}" data-type="${d.disc}">${icon("play")}${esc(t(d.disc === "bluray" ? "media_play_bluray" : "media_play_dvd"))}</button>` : ""}
    ${playable && !d.disc ? `<button class="btn small f" data-act="media-play" data-path="${esc(d.path)}" data-type="dir">${icon("play")}${esc(t("media_play_all"))}</button>` : ""}
  </div>`;
  if (!items.length) {
    return `${head}<div class="empty-note">${icon("folder", "i")}<div class="grow"><h3>${esc(t("media_empty"))}</h3>${esc(t("media_empty_text"))}</div></div>`;
  }
  const shown = items.slice(0, 600);
  return `${head}<div class="grid media-grid" data-row="media-items">${shown.map((i) => mediaTile(i)).join("")}</div>
    ${items.length > shown.length ? `<div class="empty-note">${esc(t("media_more", { n: items.length - shown.length }))}</div>` : ""}`;
}
async function mediaOpen(path, { root = false, focus = null } = {}) {
  const r = await api(`/api/player/browse?path=${encodeURIComponent(path)}`);
  if (!r || !r.ok) { toast(t("media_unavailable"), "alert"); await loadMedia(); S.media.dir = null; renderHome(); return; }
  if (root) S.media.root = r.path;
  S.media.dir = r;
  renderHome(focus ? `.media[data-path="${CSS.escape(focus)}"]` : (r.items || []).length ? "[data-row='media-items'] .f" : "[data-row='media-head'] .f");
  const sc = $(".home-scroll"); if (sc && !focus) sc.scrollTop = 0;
}
async function mediaUp() {
  const d = S.media.dir; if (!d) return false;
  if (!d.parent || d.path === S.media.root) {
    S.media.dir = null; await loadMedia(); renderHome(`.media[data-path="${CSS.escape(S.media.root || "")}"]`); return true;
  }
  await mediaOpen(d.parent, { focus: d.path });
  return true;
}
async function mediaPlay(path, startOver = false) {
  const r = await api("/api/player/play", { path, start_over: startOver });
  if (!r || !r.ok) toast(t(r && r.message === "not_installed" ? "media_no_player" : "media_play_failed"), "alert");
}
act("media-open", (d) => mediaOpen(d.path, { root: !S.media.dir }));
act("media-up", () => mediaUp());
act("media-play", (d) => {
  const p = parseFloat(d.progress || "0");
  if (p > 0 && p < 1) {
    dialog({ title: d.name || "", items: [{ label: t("media_resume"), icon: "play", value: "resume" }, { label: t("media_from_start"), icon: "refresh", value: "start" }],
      onPick: (v) => mediaPlay(d.path, v === "start") });
  } else mediaPlay(d.path, p >= 1);
});
act("media-resume", (d) => {
  dialog({ title: d.name || "", items: [{ label: t("media_resume"), icon: "play", value: "resume" }, { label: t("media_from_start"), icon: "refresh", value: "start" },
    { label: t("media_forget"), icon: "trash", value: "forget", danger: true }],
  onPick: async (v) => {
    if (v === "forget") { await api("/api/player/forget", { path: d.path }); await loadMedia(); renderHome("[data-row='media-sources'] .f"); }
    else mediaPlay(d.path, v === "start");
  } });
});
act("usb-player", async (d) => { S.tab = "media"; S.media.dir = null; Nav.popAll(); await mediaOpen(d.mount, { root: true }); });

/* ====================================================================== Startseite 2.0: Spotlight & Hintergrund
 * Oben zeigt das "Spotlight" groß, was gerade ausgewählt ist (Sendung mit Fortschritt, App, Film, Radiosender …),
 * der Hintergrund färbt sich passend dazu ein. Auf "Für dich" zeigt es sonst die Empfehlungen (Hero). */
const Backdrop = {
  cur: 0, last: "",
  el() {
    let bd = $("#backdrop");
    if (!bd) { bd = document.createElement("div"); bd.id = "backdrop"; bd.innerHTML = '<i class="bd"></i><i class="bd"></i>'; document.body.prepend(bd); }
    return bd;
  },
  set({ c1 = "", c2 = "", img = "" } = {}) {
    const key = `${c1}|${c2}|${img}`; if (key === this.last) return; this.last = key;
    const layers = $$(".bd", this.el());
    this.cur = 1 - this.cur;
    const l = layers[this.cur];
    const grad = c1 ? `radial-gradient(1300px 900px at 78% 8%, ${c1}66 0%, transparent 62%), radial-gradient(1000px 700px at 0% 100%, ${c2 || c1}40 0%, transparent 65%)` : "none";
    l.style.backgroundImage = img ? `${grad}, url("${img.replace(/"/g, "%22")}")` : grad;
    l.classList.toggle("img", !!img);
    l.classList.add("on"); layers[1 - this.cur].classList.remove("on");
  },
  clear() { this.set({}); },
};

function rgbaHex(hex, fallback = "#1d3b63") { return /^#[0-9a-f]{6}$/i.test(hex || "") ? hex : fallback; }
function catName(cat) { return t(`cat_${cat}`) || ""; }
function spotFor(el) {
  if (!el) return null;
  const d = el.dataset;
  if (el.closest("#spot")) return "hero";
  if (el.classList.contains("tab")) return tabSpot(d.tab);
  if (d.act === "channel") {
    const ch = S.channels.find((c) => c.name === d.name); if (!ch) return null;
    const cur = liveNow(ch), nxt = cur === ch.next ? null : ch.next;
    const now = Date.now() / 1000;
    const info = { kicker: `${t("tab_live")}${ch.number ? ` · ${t("channel_no", { n: ch.number })}` : ""}${ch.group ? ` · ${ch.group}` : ""}`,
      title: cur ? cur.title : ch.name, sub: cur ? ch.name : "", c1: "#1477ff", c2: "#0b2f8a",
      art: ch.logo ? `<img class="spot-logo" src="${esc(ch.logo)}" alt="">` : "", glyph: "tv", hint: t("spot_hint_channel") };
    if (cur) {
      info.prog = { pct: Math.max(0, Math.min(100, (now - cur.start) / (cur.end - cur.start) * 100)), l: `${hm(cur.start)} – ${hm(cur.end)}`,
        r: t("epg_left", { n: Math.max(1, Math.round((cur.end - now) / 60)) }) };
      if (cur.desc) info.text = cur.desc;
    } else info.text = t("spot_no_epg");
    if (nxt) info.meta = [`${t("epg_next")} ${hm(nxt.start)}: ${nxt.title}`];
    if (isFav(ch.name)) (info.meta = info.meta || []).unshift(`★ ${t("fav_channels")}`);
    return info;
  }
  if (d.act === "launch" || d.act === "running-menu") {
    const app = S.apps.all.find((a) => a.id === d.id) || S.apps.home.find((a) => a.id === d.id); if (!app) return null;
    return { kicker: [catName(app.category), app.running ? t("running") : ""].filter(Boolean).join(" · "), title: app.name, text: L(app.desc),
      c1: rgbaHex(app.color), c2: rgbaHex(app.color2), art: app.icon ? `<img class="spot-appicon${app.banner ? " banner" : ""}" src="${esc(app.icon)}" alt="">` : "", glyph: app.glyph || "apps",
      hint: app.installable && !app.available ? t("tap_to_install") : t("spot_hint_app") };
  }
  if (d.act === "android-app") return { kicker: "Android", title: d.name, c1: "#1f6f4a", c2: "#0b2b1d", glyph: "android", hint: t("spot_hint_app") };
  if (el.classList.contains("media")) {
    const type = d.type || "video";
    const [c1, c2] = MEDIA_COLORS[type] || MEDIA_COLORS.dir;
    const p = parseFloat(d.progress || "0");
    const item = ((S.media.dir || {}).items || []).find((i) => i.path === d.path) || S.media.recent.find((i) => i.path === d.path) || {};
    const info = { kicker: ({ dir: t("media_folder"), bluray: "Blu-ray", dvd: "DVD", video: "Video", audio: t("cat_music"), image: t("image"), usb: "USB", playlist: t("playlist") })[type] || t("tab_media"),
      title: d.name, c1, c2, glyph: MEDIA_ICON[type] || "film", text: d.path, hint: d.act === "media-open" ? t("spot_hint_open") : t("spot_hint_play") };
    if (p > 0 && p < 1 && item.duration) info.prog = { pct: p * 100, l: fmtDuration(item.pos || 0), r: mediaLeft(item) };
    if (item.size) info.meta = [fmtBytes(item.size)];
    if (p >= 1) (info.meta = info.meta || []).unshift(`✓ ${t("media_seen")}`);
    return info;
  }
  if (d.act === "radio-play" && typeof radioSpot === "function") return radioSpot(d.id);
  if (d.act === "plan-item") return planSpot(d.key);
  const label = (el.querySelector(".name") || el).textContent.trim();
  return label ? { title: label, glyph: "sparkle" } : null;
}
function tabSpot(tab) {
  if (tab === "foryou") return "hero";
  if (tab === "live") return { kicker: t("tab_live"), title: S.channels.length ? t("hero_live_title") : t("hero_setup_title"),
    text: S.channels.length ? t("hero_live_text", { n: S.channels.length }) : t("hero_setup_text"), glyph: "tv", c1: "#1477ff", c2: "#0b2f8a" };
  if (tab === "media") return { kicker: t("tab_media"), title: t("spot_media_title"), text: t("spot_media_text"), glyph: "play", c1: "#3b2a7a", c2: "#0f6157" };
  if (tab === "apps") return { kicker: t("tab_apps"), title: t("spot_apps_title"), text: t("spot_apps_text", { n: S.apps.all.length }), glyph: "apps", c1: "#0f6b8f", c2: "#3b2a7a" };
  return null;
}

const Spot = {
  timer: null, shown: null,
  // wird bei jedem Fokuswechsel aufgerufen (Nav.focus)
  follow(el) {
    if (!["home", "radio", "planner"].includes(S.view) || !$("#spot")) return;
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.show(spotFor(el)), 70);
  },
  show(info, instant = false) {
    const spot = $("#spot"); if (!spot) return;
    if (!info && S.view === "radio") info = radioNowSpot();
    if (!info && S.view === "planner") info = planIntroSpot();
    if (info === "hero" || (!info && S.tab === "foryou")) { this.hero(instant); return; }
    if (!info) info = tabSpot(S.tab) || { title: "" };
    const key = JSON.stringify(info);
    spot.classList.remove("mode-hero"); spot.classList.add("mode-info");
    if (key === this.shown) return; this.shown = key;
    const box = $(".spot-info", spot);
    const prog = info.prog ? `<div class="spot-prog"><span>${esc(info.prog.l)}</span><b><i style="width:${info.prog.pct.toFixed(1)}%"></i></b><span>${esc(info.prog.r)}</span></div>` : "";
    const meta = (info.meta || []).filter(Boolean).map((m) => `<span class="pill-s">${esc(m)}</span>`).join("");
    box.innerHTML = `<div class="spot-art">${info.art || `<svg class="spot-glyph" viewBox="0 0 24 24">${ICONS[info.glyph] || ICONS.sparkle}</svg>`}</div>
      <div class="spot-body">${info.kicker ? `<div class="hero-kicker">${esc(info.kicker)}</div>` : ""}<h1>${esc(info.title)}</h1>
      ${info.sub ? `<div class="spot-sub">${esc(info.sub)}</div>` : ""}${prog}${meta ? `<div class="spot-meta">${meta}</div>` : ""}
      ${info.text ? `<p>${esc(info.text)}</p>` : ""}${info.hint ? `<div class="spot-hint">${icon("info")}${esc(info.hint)}</div>` : ""}</div>`;
    const img = box.querySelector(".spot-art img");
    if (img) img.onerror = () => { img.replaceWith(Object.assign(document.createElement("span"), { innerHTML: `<svg class="spot-glyph" viewBox="0 0 24 24">${ICONS[info.glyph] || ICONS.sparkle}</svg>` })); };
    if (!instant) { box.classList.remove("swap"); void box.offsetWidth; box.classList.add("swap"); }
    Backdrop.set({ c1: info.c1, c2: info.c2, img: info.bg || "" });
  },
  hero(instant = false) {
    const spot = $("#spot"); if (!spot) return;
    const box = $(".spot-hero", spot); if (!box) return;
    spot.classList.add("mode-hero"); spot.classList.remove("mode-info"); this.shown = null;
    const s = (S._slides || [])[S.heroIndex] || {};
    Backdrop.set({ c1: s.c1 || "#1477ff", c2: s.c2 || "#0b2f8a", img: s.cover || "" });
  },
};

function heroSpotHtml() {
  const slides = heroSlides(); S._slides = slides;
  S.heroIndex = S.heroIndex % slides.length;
  const s = slides[S.heroIndex];
  const btns = s.actions.map((a) => `<button class="btn f${a.primary ? " primary" : ""}" data-act="${a.act}" ${Object.entries(a.data).map(([k, v]) => `data-${k}="${esc(v)}"`).join(" ")}>${icon(a.icon)}${esc(a.label)}</button>`).join("");
  const art = s.cover ? `<img class="spot-cover" src="${esc(s.cover)}" alt="">` : `<svg class="spot-glyph" viewBox="0 0 24 24">${ICONS[s.glyph]}</svg>`;
  return `<div class="spot-hero" data-row="hero"><div class="spot-art">${art}</div><div class="spot-body"><div class="hero-kicker">${esc(s.kicker)}</div><h1>${esc(s.title)}</h1><p>${esc(s.text)}</p>
    <div class="hero-actions">${btns}</div></div>
    <div class="hero-dots">${slides.map((_, i) => `<i class="${i === S.heroIndex ? "on" : ""}"></i>`).join("")}</div></div>`;
}
function spotHtml() {
  return `<section id="spot" class="spot ${S.tab === "foryou" ? "mode-hero" : "mode-info"}">${S.tab === "foryou" ? heroSpotHtml() : ""}<div class="spot-info"></div></section>`;
}

/* Lautstärke-Anzeige (Box oder AV-Receiver per HDMI-CEC) */
function showVolume(m) {
  let el = $("#vol-osd");
  if (!el) { el = document.createElement("div"); el.id = "vol-osd"; el.className = "vol-osd"; document.body.appendChild(el); }
  const lvl = m.level == null ? null : Math.max(0, Math.min(100, +m.level));
  el.innerHTML = `${icon(m.mute ? "x" : "sound")}<div><small>${esc(m.source === "avr" ? t("vol_avr") : t("volume"))}</small>
    <b><i style="width:${m.mute ? 0 : (lvl ?? 0)}%"></i></b></div><span>${m.mute ? esc(t("muted")) : lvl == null ? "" : lvl}</span>`;
  el.classList.add("show");
  clearTimeout(showVolume.t); showVolume.t = setTimeout(() => el.classList.remove("show"), 1800);
}

/* Tab-Markierung gleitet zum aktiven Tab */
function placeTabInk(animate = true) {
  const nav = $(".tabs"); const act = nav && $(".tab.active", nav); if (!nav || !act) return;
  let ink = $(".tab-ink", nav);
  if (!ink) { ink = document.createElement("span"); ink.className = "tab-ink"; nav.prepend(ink); }
  const from = S._ink;
  const to = { l: act.offsetLeft, w: act.offsetWidth };
  if (animate && from) { ink.style.transition = "none"; ink.style.transform = `translateX(${from.l}px)`; ink.style.width = `${from.w}px`; void ink.offsetWidth; ink.style.transition = ""; }
  ink.style.transform = `translateX(${to.l}px)`; ink.style.width = `${to.w}px`;
  S._ink = to;
}

/* ====================================================================== Internetradio */
S.radio = { state: { playing: false }, favorites: [], recent: [], genres: [], list: [], cat: "top", q: "", map: {}, loading: false, error: "" };
function stationKey(st) { if (st && st.id) S.radio.map[st.id] = st; return st ? st.id : ""; }
function stationTile(st) {
  stationKey(st);
  const playing = S.radio.state.playing && S.radio.state.station && S.radio.state.station.id === st.id;
  const fav = S.radio.favorites.some((f) => f.id === st.id);
  const initials = esc((st.name || "?").replace(/[^A-Za-z0-9ÄÖÜäöü ]/g, "").split(" ").filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase());
  return `<button class="tile small station f${playing ? " playing" : ""}" data-act="radio-play" data-id="${esc(st.id)}">
    <span class="st-logo">${st.favicon ? `<img src="${esc(st.favicon)}" alt="" loading="lazy" onerror="this.replaceWith(Object.assign(document.createElement('b'),{textContent:'${initials}'}))">` : `<b>${initials}</b>`}</span>
    ${fav ? `<span class="fav">${icon("star")}</span>` : ""}${playing ? '<span class="eq"><i></i><i></i><i></i></span>' : ""}
    <span class="name">${esc(st.name)}</span></button>`;
}
function radioSpot(id) {
  const st = S.radio.map[id]; if (!st) return null;
  const rs = S.radio.state, playing = rs.playing && rs.station && rs.station.id === id;
  return { kicker: [t("radio"), st.country, st.codec && st.bitrate ? `${st.codec} ${st.bitrate} kbit/s` : st.codec].filter(Boolean).join(" · "),
    title: playing && rs.title ? rs.title : st.name, sub: playing && rs.title ? st.name : "", text: st.tags || "",
    meta: [playing ? `▶ ${t("radio_playing")}` : "", S.radio.favorites.some((f) => f.id === id) ? `★ ${t("radio_fav")}` : ""],
    art: playing && rs.cover ? `<img class="spot-cover" src="${esc(rs.cover)}" alt="">`
      : st.favicon ? `<img class="spot-logo station-logo" src="${esc(st.favicon)}" alt="">` : "", glyph: "radio", c1: "#7b3cff", c2: "#0f6157",
    bg: playing && rs.cover ? rs.cover : "",
    hint: t(playing ? "radio_hint_playing" : "radio_hint") };
}
function radioHomeRow() {
  const favs = S.radio.favorites || [];
  if (!favs.length) return "";
  return `<div class="row"><div class="row-title">${esc(t("radio_favs"))}</div><div class="row-track" data-row="radio-favs">${favs.map(stationTile).join("")}
    <button class="tile small add f" data-act="launch" data-id="radio">${icon("radio")}<span class="name">${esc(t("radio_all"))}</span></button></div></div>`;
}
async function loadRadio() {
  const r = await api("/api/radio");
  if (r && r.genres) Object.assign(S.radio, { state: { playing: r.playing, station: r.station, title: r.title, cover: r.cover }, favorites: r.favorites || [],
    recent: r.recent || [], genres: r.genres, states: r.states || [], db: r.db || {} });
}
async function radioLoadList(cat, q = "") {
  S.radio.cat = cat; S.radio.q = q; S.radio.error = "";
  if (cat === "fav") { S.radio.list = S.radio.favorites; renderRadio(); return; }
  if (cat === "recent") { S.radio.list = S.radio.recent; renderRadio(); return; }
  S.radio.loading = true; renderRadio();
  const kind = cat === "top" ? "top" : cat === "search" ? "search" : cat === "state" ? "state" : "genre";
  const r = await api(`/api/radio/list?kind=${kind}&q=${encodeURIComponent(cat === "search" || cat === "state" ? q : cat)}`);
  S.radio.loading = false;
  S.radio.list = (r && r.stations) || []; S.radio.error = r && r.ok === false ? r.message : "";
  if (S.view === "radio") renderRadio("[data-row='radio-list'] .f");
}
async function openRadio() {
  Nav.popAll(); S.view = "radio";
  await loadRadio();
  const start = S.radio.favorites.length ? "fav" : "top";
  renderRadio(); radioLoadList(start);
}
function renderRadio(focusSel) {
  S.view = "radio";
  const R = S.radio, rs = R.state;
  const cats = [["fav", `★ ${t("radio_fav")}`], ["recent", t("radio_recent")], ["top", t("radio_top")],
    ["state", R.cat === "state" ? `📍 ${R.q}` : `📍 ${t("radio_state")}`], ...R.genres.map((g) => [g, t(`radio_g_${g.replace(" ", "")}`)])];
  if (R.cat === "search") cats.push(["search", `🔍 ${R.q}`]);
  const ctl = `${rs.playing ? `<button class="btn small primary f" data-act="radio-stop">${icon("stop")}${esc(t("radio_stop"))}</button>` : ""}
    <button class="btn small f" data-act="radio-search">${icon("eye")}${esc(t("search"))}</button>
    ${cats.map(([id, label]) => `<button class="pill f${R.cat === id ? " active" : ""}" data-act="radio-cat" data-cat="${esc(id)}">${esc(label)}</button>`).join("")}`;
  let list;
  if (R.loading) list = `<div class="empty-note"><span class="spinner"></span><div class="grow">${esc(t("radio_loading"))}</div></div>`;
  else if (R.error) list = `<div class="empty-note">${icon("alert", "i")}<div class="grow"><h3>${esc(t("radio_offline"))}</h3>${esc(t("radio_offline_text"))}</div></div>`;
  else if (!R.list.length) list = `<div class="empty-note">${icon("radio", "i")}<div class="grow">${esc(t(R.cat === "fav" ? "radio_no_favs" : "radio_empty"))}</div></div>`;
  else list = `<div class="grid" data-row="radio-list">${R.list.map(stationTile).join("")}</div>`;
  const keep = Nav.current && Nav.current.dataset && Nav.current.isConnected ? Nav.current.dataset : null;
  $("#app").innerHTML = `<header class="topbar"><div class="brand"><svg><use href="#logo-mark"/></svg><span>Wangert<b>TV</b></span></div>
      <div class="view-title">${icon("radio")}${esc(t("radio"))}</div>
      <div class="top-right"><div class="clock" id="clock">${clockText()}</div></div></header>
    <section id="spot" class="spot mode-info"><div class="spot-info"></div></section>
    <div class="home-scroll"><div class="home-content"><div class="chips radio-ctl" data-row="radio-ctl">${ctl}</div>${list}</div></div>`;
  let sel = focusSel;
  if (!sel && keep) sel = keep.id ? `[data-act="radio-play"][data-id="${CSS.escape(keep.id)}"]` : keep.cat ? `[data-cat="${CSS.escape(keep.cat)}"]` : keep.act ? `[data-act="${keep.act}"]` : null;
  Nav.first(sel || (rs.playing ? "[data-act='radio-stop']" : "[data-row='radio-list'] .f") );
  if (!Nav.current || !$("#app").contains(Nav.current)) Nav.first();
  Spot.show(spotFor(Nav.current) || radioNowSpot(), true);
}
function radioNowSpot() {
  const rs = S.radio.state;
  if (rs.playing && rs.station) return radioSpot(stationKey(rs.station));
  const n = (S.radio.db || {}).count;
  return { kicker: t("radio"), title: t("radio_title"), text: t("radio_text"), glyph: "radio", c1: "#7b3cff", c2: "#0f6157",
    meta: n ? [t("radio_db", { n: n.toLocaleString(locale()) })] : [] };
}
act("radio-play", async (d) => {
  const st = S.radio.map[d.id]; if (!st) return;
  const rs = S.radio.state;
  if (rs.playing && rs.station && rs.station.id === d.id) { if (S.view !== "radio") openRadio(); return; }
  const r = await api("/api/radio/play", { station: st });
  if (!r || !r.ok) toast(t("radio_failed"), "alert");
});
act("radio-stop", async () => { await api("/api/radio/stop", {}); });
act("radio-cat", (d) => {
  if (d.cat !== "state") { radioLoadList(d.cat); return; }
  dialog({ title: t("radio_state"), items: (S.radio.states || []).map((x) => ({ label: x.name, sub: t("radio_n", { n: x.count }), value: x.name, selected: S.radio.cat === "state" && S.radio.q === x.name })),
    onPick: (v) => radioLoadList("state", v) });
});
act("radio-search", () => OSK.start({ title: t("radio_search"), value: S.radio.q || "", placeholder: t("radio_search_ph"),
  onDone: (v) => { if (v.trim().length >= 2) radioLoadList("search", v.trim()); } }));
async function radioToggleFav(id) {
  const st = S.radio.map[id]; if (!st) return;
  const on = !S.radio.favorites.some((f) => f.id === id);
  const r = await api("/api/radio/fav", { station: st, on });
  if (r && r.favorites) S.radio.favorites = r.favorites;
  toast(t(on ? "radio_fav_added" : "radio_fav_removed", { name: st.name }), "star");
  if (S.view === "radio") renderRadio(`[data-act="radio-play"][data-id="${CSS.escape(id)}"]`); else refreshView();
}

/* ====================================================================== Dateimanager */
S.files = { dir: null, roots: [], select: false, sel: new Set(), clip: null, job: null };
const FILE_ICON = { dir: "folder", video: "film", audio: "music", image: "image", playlist: "grid", file: "info", usb: "usb", folder: "folder", home: "home" };
function fmtDate(ts) { return new Date(ts * 1000).toLocaleDateString(locale(), { day: "2-digit", month: "2-digit", year: "numeric" }); }
async function openFiles(path) {
  Nav.popAll(); S.view = "files"; S.files.select = false; S.files.sel.clear();
  await filesLoad(path || null);
}
async function filesLoad(path, focusPath) {
  if (!path) {
    const r = await api("/api/files"); S.files.dir = null; S.files.roots = (r && r.roots) || [];
  } else {
    const r = await api(`/api/files?path=${encodeURIComponent(path)}`);
    if (!r || !r.ok) { toast(t("media_unavailable"), "alert"); return filesLoad(null); }
    S.files.dir = r;
  }
  renderFiles(focusPath ? `.frow[data-path="${CSS.escape(focusPath)}"]` : null);
}
function freeBar(f) {
  if (!f.total) return "";
  const used = 1 - f.free / f.total;
  return `<span class="fbar"><i style="width:${(used * 100).toFixed(0)}%"></i></span><span class="fmeta">${esc(t("files_free", { free: fmtBytes(f.free), total: fmtBytes(f.total) }))}</span>`;
}
function renderFiles(focusSel) {
  S.view = "files";
  const F = S.files, d = F.dir;
  let head, rows;
  if (!d) {
    head = `<div class="fpath">${icon("folder")}<b>${esc(t("files_title"))}</b></div>`;
    rows = F.roots.map((r) => `<button class="frow f" data-act="files-open" data-path="${esc(r.path)}" data-dir="1">
      <span class="fic">${icon(FILE_ICON[r.type] || "folder")}</span><span class="fname">${esc(r.name || t("media_home"))}</span>${freeBar(r)}</button>`).join("");
  } else {
    const n = F.sel.size;
    head = `<div class="fpath">${icon(d.parent ? "folder" : "usb")}<div><b>${esc(d.name)}</b><small>${esc(d.path)}</small></div>${d.total ? `<span class="fmeta">${esc(t("files_free", { free: fmtBytes(d.free), total: fmtBytes(d.total) }))}</span>` : ""}</div>
      <div class="chips fbar-actions" data-row="files-actions">
        <button class="btn small f" data-act="files-up">${icon("back")}${esc(t("back"))}</button>
        ${F.clip ? `<button class="btn small primary f" data-act="files-paste">${icon("download")}${esc(t("files_paste", { n: F.clip.paths.length }))}</button>
          <button class="btn small f" data-act="files-clip-clear">${icon("x")}${esc(t("cancel"))}</button>` : ""}
        <button class="btn small f${F.select ? " primary" : ""}" data-act="files-select">${icon("check")}${esc(F.select ? t("files_selected", { n }) : t("files_select"))}</button>
        ${F.select && n ? `<button class="btn small f" data-act="files-sel-menu">${icon("grid")}${esc(t("files_actions"))}</button>` : ""}
        ${d.writable ? `<button class="btn small f" data-act="files-mkdir">${icon("plus")}${esc(t("files_mkdir"))}</button>` : ""}
      </div>`;
    rows = d.items.length ? d.items.map((i) => `<button class="frow f${F.sel.has(i.path) ? " sel" : ""}" data-act="files-open" data-path="${esc(i.path)}" data-dir="${i.dir ? 1 : 0}" data-kind="${i.kind}" data-name="${esc(i.name)}">
        ${F.select ? `<span class="fchk">${F.sel.has(i.path) ? icon("check") : ""}</span>` : ""}
        <span class="fic k-${i.kind}">${icon(FILE_ICON[i.kind] || "info")}</span><span class="fname">${esc(i.name)}</span>
        <span class="fmeta">${i.dir ? esc(t("media_folder")) : esc(fmtBytes(i.size))}</span><span class="fmeta fdate">${esc(fmtDate(i.mtime))}</span></button>`).join("")
      : `<div class="empty-note">${icon("folder", "i")}<div class="grow">${esc(t("files_empty"))}</div></div>`;
    if (d.more) rows += `<div class="empty-note">${esc(t("media_more", { n: d.more }))}</div>`;
  }
  const job = F.job && F.job.state === "running" ? F.job : null;
  $("#app").innerHTML = `<header class="topbar"><div class="brand"><svg><use href="#logo-mark"/></svg><span>Wangert<b>TV</b></span></div>
      <div class="view-title">${icon("folder")}${esc(t("files_title"))}</div><div class="top-right"><div class="clock" id="clock">${clockText()}</div></div></header>
    <div class="files-view view-enter">${head}<div class="view-scroll flist" data-row="files-list">${rows}</div>
    <div class="fjob${job ? "" : " hidden"}" id="fjob">${job ? filesJobHtml(job) : ""}</div></div>`;
  Nav.first(focusSel || ".flist .f") ;
  if (!Nav.current || !$("#app").contains(Nav.current)) Nav.first();
}
function filesJobHtml(j) {
  const pct = j.total ? Math.min(100, j.done / j.total * 100) : 0;
  return `${icon(j.mode === "move" ? "right" : "download")}<div><small>${esc(t(j.mode === "move" ? "files_moving" : "files_copying"))} ${esc(j.current || "")}</small>
    <b><i style="width:${pct.toFixed(1)}%"></i></b></div><span>${pct.toFixed(0)} %</span>`;
}
function filesBack() {
  const F = S.files;
  if (F.select) { F.select = false; F.sel.clear(); renderFiles(); return; }
  if (F.dir && F.dir.parent) { filesLoad(F.dir.parent, F.dir.path); return; }
  if (F.dir) { const p = F.dir.path; filesLoad(null, p); return; }
  goHome(S.tab);
}
function filesItem(path) { return ((S.files.dir || {}).items || []).find((i) => i.path === path); }
function filesContext(el) {
  const d = el.dataset; if (!S.files.dir) return;
  const it = filesItem(d.path); if (!it) return;
  const items = [];
  if (it.dir) items.push({ label: t("open"), icon: "folder", value: "open" });
  if (["video", "audio", "image", "playlist"].includes(it.kind) || it.dir) items.push({ label: t(it.dir ? "files_play_folder" : "radio_play"), icon: "play", value: "play" });
  items.push({ label: t("files_copy"), icon: "download", value: "copy" }, { label: t("files_cut"), icon: "right", value: "move" },
    { label: t("files_rename"), icon: "key", value: "rename" }, { label: t("delete"), icon: "trash", value: "delete", danger: true });
  dialog({ title: it.name, text: esc(it.dir ? t("media_folder") : `${fmtBytes(it.size)} · ${fmtDate(it.mtime)}`), items, onPick: (v) => filesDo(v, [it]) });
}
function filesDo(v, its) {
  const paths = its.map((i) => i.path);
  if (v === "open") filesLoad(paths[0]);
  else if (v === "play") mediaPlay(paths[0]);
  else if (v === "copy" || v === "move") {
    S.files.clip = { mode: v, paths }; S.files.select = false; S.files.sel.clear();
    toast(t(v === "copy" ? "files_copied_clip" : "files_cut_clip", { n: paths.length }), "check"); renderFiles();
  } else if (v === "rename") {
    const it = its[0];
    OSK.start({ title: t("files_rename"), value: it.name, onDone: async (name) => {
      if (!name.trim() || name === it.name) return;
      const r = await api("/api/files/op", { op: "rename", path: it.path, name: name.trim() });
      if (!r.ok) toast(tOr(`files_err_${r.message}`, "action_failed"), "alert");
      await filesLoad(S.files.dir.path, r.path);
    } });
  } else if (v === "delete") {
    confirmBox(t("files_delete_q", { n: paths.length }), esc(its.length === 1 ? its[0].name : t("files_n_items", { n: its.length })), t("delete"), async () => {
      const r = await api("/api/files/op", { op: "delete", paths });
      toast(r && r.ok ? t("files_deleted", { n: r.count }) : t("action_failed"), r && r.ok ? "trash" : "alert");
      S.files.sel.clear(); await filesLoad(S.files.dir.path);
    }, true);
  }
}
act("files-open", (d) => {
  const F = S.files;
  if (F.select && F.dir) {
    F.sel.has(d.path) ? F.sel.delete(d.path) : F.sel.add(d.path);
    renderFiles(`.frow[data-path="${CSS.escape(d.path)}"]`); return;
  }
  if (d.dir === "1") { filesLoad(d.path); return; }
  if (["video", "audio", "image", "playlist"].includes(d.kind)) { mediaPlay(d.path); return; }
  filesContext(Nav.current);
});
act("files-up", () => filesBack());
act("files-select", () => { S.files.select = !S.files.select; S.files.sel.clear(); renderFiles("[data-act='files-select']"); });
act("files-sel-menu", () => {
  const its = [...S.files.sel].map(filesItem).filter(Boolean); if (!its.length) return;
  dialog({ title: t("files_selected", { n: its.length }), items: [{ label: t("files_copy"), icon: "download", value: "copy" },
    { label: t("files_cut"), icon: "right", value: "move" }, { label: t("delete"), icon: "trash", value: "delete", danger: true }],
  onPick: (v) => filesDo(v, its) });
});
act("files-clip-clear", () => { S.files.clip = null; renderFiles(); });
act("files-paste", async () => {
  const c = S.files.clip; if (!c || !S.files.dir) return;
  const r = await api("/api/files/transfer", { mode: c.mode, paths: c.paths, dest: S.files.dir.path });
  if (!r || !r.ok) { toast(tOr(`files_err_${(r || {}).message}`, "action_failed"), "alert"); return; }
  S.files.clip = null; S.files.job = { state: "running", mode: c.mode, done: 0, total: 0 }; renderFiles();
});
act("files-mkdir", () => OSK.start({ title: t("files_mkdir"), value: "", placeholder: t("files_mkdir_ph"), onDone: async (name) => {
  if (!name.trim()) return;
  const r = await api("/api/files/op", { op: "mkdir", path: S.files.dir.path, name: name.trim() });
  if (!r.ok) toast(tOr(`files_err_${r.message}`, "action_failed"), "alert");
  await filesLoad(S.files.dir.path, r.path);
} }));
function filesJobEvent(j) {
  S.files.job = j;
  const el = $("#fjob");
  if (j.state === "running") { if (el) { el.classList.remove("hidden"); el.innerHTML = filesJobHtml(j); } return; }
  if (el) el.classList.add("hidden");
  if (j.state === "done") toast(t("files_done"), "check");
  else if (j.state === "error") toast(tOr(`files_err_${j.message}`, "action_failed"), "alert");
  if (S.view === "files" && S.files.dir && !Nav.layers.length) filesLoad(S.files.dir.path);
}

/* ====================================================================== Vom Handy teilen */
S.share = null;
function handyThumb(e) {
  const rel = e.path.split("/Handy/")[1] || "";
  const glyph = `<span class="glyph">${icon(MEDIA_ICON[e.type] || "info")}</span>`;
  return e.type === "image" ? `<img src="/handy/${rel.split("/").map(encodeURIComponent).join("/")}" alt="" loading="lazy" onerror="this.outerHTML=${esc(JSON.stringify(glyph))}">` : glyph;
}
async function openShare() {
  Nav.popAll(); S.view = "share";
  S.share = await api("/api/share");
  renderShare();
}
function renderShare(focusSel) {
  S.view = "share";
  const sh = S.share || {};
  const recent = (sh.recent || []).filter((e) => ["image", "video", "audio"].includes(e.type));
  $("#app").innerHTML = `<header class="topbar"><div class="brand"><svg><use href="#logo-mark"/></svg><span>Wangert<b>TV</b></span></div>
      <div class="view-title">${icon("share")}${esc(t("share_title"))}</div><div class="top-right"><div class="clock" id="clock">${clockText()}</div></div></header>
    <div class="share-view view-enter">
      <div class="share-card"><div class="qr">${sh.qr || ""}</div>
        <div><h2>${esc(t("share_howto_title"))}</h2><ol><li>${esc(t("share_step1"))}</li><li>${esc(t("share_step2"))}</li><li>${esc(t("share_step3"))}</li></ol>
        <div class="share-url">${esc(sh.url || "")}</div>
        <div class="chips" data-row="share-actions" style="padding:0;margin-top:22px">
          <button class="btn small f" data-act="share-autoshow">${icon(sh.autoshow ? "check" : "x")}${esc(t(sh.autoshow ? "share_autoshow_on" : "share_autoshow_off"))}</button>
          <button class="btn small f" data-act="share-renew">${icon("refresh")}${esc(t("share_renew"))}</button>
          <button class="btn small f" data-act="share-folder">${icon("folder")}${esc(t("share_folder"))}</button></div></div></div>
      <div class="row-title" style="margin:34px 0 0">${esc(t("share_recent"))}</div>
      ${recent.length ? `<div class="row-track share-track" data-row="share-recent">${recent.map((e) => `<button class="tile small handy f" data-act="share-open" data-path="${esc(e.path)}" data-type="${e.type}">
          ${handyThumb(e)}<span class="name">${esc(fmtTime(e.mtime))}</span></button>`).join("")}</div>`
        : `<div class="empty-note" style="margin:16px 0">${icon("image", "i")}<div class="grow">${esc(t("share_none"))}</div></div>`}
    </div>`;
  Nav.first(focusSel || (recent.length ? "[data-row='share-recent'] .f" : "[data-act='share-autoshow']"));
}
act("share-open", (d) => api("/api/share/play", { path: d.path }));
act("share-autoshow", async () => { await saveSettings({ share: { autoshow: !(S.share || {}).autoshow } }); S.share = await api("/api/share"); renderShare("[data-act='share-autoshow']"); });
act("share-renew", () => confirmBox(t("share_renew"), esc(t("share_renew_text")), t("share_renew"), async () => {
  S.share = await api("/api/share/renew", {}); renderShare("[data-act='share-renew']");
}));
act("share-folder", () => openFiles((S.share && S.share.recent && S.share.recent[0] && S.share.recent[0].path.replace(/\/[^/]*$/, "")) || null));

/* ====================================================================== TV-Planer: "Heute um 20:15" & Erinnerungen */
S.plan = { slot: "prime", items: [], reminders: [], map: {}, prime: null, primeTs: 0 };
function slotTime(slot) {
  const d = new Date(), now = Date.now() / 1000;
  const at = (h, m, plusDay = 0) => { const x = new Date(d); x.setDate(x.getDate() + plusDay); x.setHours(h, m, 0, 0); return Math.floor(x / 1000); };
  if (slot === "now") return Math.floor(now);
  if (slot === "prime") { const tt = at(20, 15); return now > tt + 2 * 3600 ? at(20, 15, 1) : tt; }
  if (slot === "late") { const tt = at(22, 15); return now > tt + 3600 ? at(22, 15, 1) : tt; }
  if (slot === "tomorrow") return at(20, 15, 1);
  return Math.floor(now);
}
function slotLabel(slot) {
  const ts = slotTime(slot), tomorrow = new Date(ts * 1000).getDate() !== new Date().getDate();
  if (slot === "now") return t("plan_now");
  if (slot === "rem") return `🔔 ${t("plan_reminders")} (${S.plan.reminders.length})`;
  return `${tomorrow ? t("plan_tomorrow") : t("plan_today")} ${hm(ts)}`;
}
const planKey = (it) => `${it.name}|${it.start}`;
function hasReminder(it) { return S.plan.reminders.some((r) => r.name === it.name && r.start === it.start); }
function planTile(it) {
  const k = planKey(it); S.plan.map[k] = it;
  const ch = S.channels.find((c) => c.name === it.name) || {};
  const logo = it.logo || ch.logo;
  const now = Date.now() / 1000, live = it.start <= now && now < it.end;
  return `<button class="tile plan f" data-act="plan-item" data-key="${esc(k)}">
    <span class="plan-logo">${logo ? `<img src="${esc(logo)}" alt="" onerror="this.replaceWith(Object.assign(document.createElement('b'),{textContent:${esc(JSON.stringify(it.name))}}))">` : `<b>${esc(it.name)}</b>`}</span>
    ${hasReminder(it) ? `<span class="bell">${icon("clock")}</span>` : ""}${live ? `<span class="live-dot">LIVE</span>` : ""}
    <span class="name">${esc(it.title)}</span><span class="sub">${hm(it.start)} – ${hm(it.end)}${isFav(it.name) ? " · ★" : ""}</span></button>`;
}
function sortPlan(items) {
  return [...items].sort((a, b) => (isFav(b.name) - isFav(a.name)) || ((a.number || 999) - (b.number || 999)));
}
async function loadPrime(force = false) {
  if (!force && S.plan.prime && Date.now() - S.plan.primeTs < 10 * 60 * 1000) return;
  const r = await api(`/api/schedule?at=${slotTime("prime")}`);
  if (r && r.items) { S.plan.prime = r.items; S.plan.reminders = r.reminders || []; S.plan.primeTs = Date.now(); }
}
function primeHomeRow() {
  const items = sortPlan(S.plan.prime || []).slice(0, 16);
  if (!items.length) return "";
  return `<div class="row"><div class="row-title">${esc(t("plan_row", { t: slotLabel("prime") }))}<small>${esc(t("plan_row_hint"))}</small></div>
    <div class="row-track" data-row="prime">${items.map(planTile).join("")}
    <button class="tile plan add f" data-act="launch" data-id="tvplaner">${icon("guide")}<span class="name">${esc(t("plan_open"))}</span></button></div></div>`;
}
function planSpot(key) {
  const it = S.plan.map[key]; if (!it) return null;
  const ch = S.channels.find((c) => c.name === it.name) || {};
  const now = Date.now() / 1000, live = it.start <= now && now < it.end;
  const mins = Math.round((it.end - it.start) / 60);
  const info = { kicker: `${t("plan_title")} · ${it.name}${it.number ? ` · ${t("channel_no", { n: it.number })}` : ""}`, title: it.title,
    sub: `${new Date(it.start * 1000).toLocaleDateString(locale(), { weekday: "long" })}, ${hm(it.start)} – ${hm(it.end)} (${mins} Min.)`,
    art: (it.logo || ch.logo) ? `<img class="spot-logo" src="${esc(it.logo || ch.logo)}" alt="">` : "", glyph: "guide", c1: "#ff7849", c2: "#7b3cff",
    meta: [hasReminder(it) ? `🔔 ${t("plan_reminder_set")}` : "", isFav(it.name) ? `★ ${t("fav_channels")}` : ""],
    hint: t(live ? "plan_hint_live" : "plan_hint") };
  if (live) info.prog = { pct: (now - it.start) / (it.end - it.start) * 100, l: hm(it.start), r: t("epg_left", { n: Math.max(1, Math.round((it.end - now) / 60)) }) };
  else info.text = t("plan_starts_in", { t: fmtDuration(Math.max(0, it.start - now)) });
  return info;
}
async function openPlanner(slot) {
  Nav.popAll(); S.view = "planner";
  await planLoad(slot || (S.plan.slot === "rem" ? "prime" : S.plan.slot || "prime"));
}
async function planLoad(slot) {
  S.plan.slot = slot;
  if (slot !== "rem") {
    const r = await api(`/api/schedule?at=${slotTime(slot)}`);
    S.plan.items = sortPlan((r && r.items) || []); S.plan.reminders = (r && r.reminders) || [];
  } else {
    const r = await api(`/api/schedule?at=${slotTime("now")}`); S.plan.reminders = (r && r.reminders) || [];
    S.plan.items = S.plan.reminders;
  }
  renderPlanner();
}
function renderPlanner(focusSel) {
  S.view = "planner";
  const P = S.plan;
  const keep = Nav.current && Nav.current.isConnected ? Nav.current.dataset : null;
  const chips = ["now", "prime", "late", "tomorrow", "rem"].map((sl) => `<button class="pill f${P.slot === sl ? " active" : ""}" data-act="plan-slot" data-slot="${sl}">${esc(slotLabel(sl))}</button>`).join("");
  const list = P.items.length ? `<div class="grid" data-row="plan-list">${P.items.map(planTile).join("")}</div>`
    : `<div class="empty-note">${icon("guide", "i")}<div class="grow"><h3>${esc(t(P.slot === "rem" ? "plan_no_reminders" : "plan_no_epg"))}</h3>${esc(t(P.slot === "rem" ? "plan_no_reminders_text" : "plan_no_epg_text"))}</div></div>`;
  $("#app").innerHTML = `<header class="topbar"><div class="brand"><svg><use href="#logo-mark"/></svg><span>Wangert<b>TV</b></span></div>
      <div class="view-title">${icon("guide")}${esc(t("plan_title"))}</div><div class="top-right"><div class="clock" id="clock">${clockText()}</div></div></header>
    <section id="spot" class="spot mode-info"><div class="spot-info"></div></section>
    <div class="home-scroll"><div class="home-content"><div class="chips radio-ctl" data-row="plan-slots">${chips}</div>${list}</div></div>`;
  let sel = focusSel;
  if (!sel && keep && keep.key && P.items.some((i) => planKey(i) === keep.key)) sel = `[data-key="${CSS.escape(keep.key)}"]`;
  Nav.first(sel || "[data-row='plan-list'] .f");
  if (!Nav.current || !$("#app").contains(Nav.current)) Nav.first(".pill.active");
  Spot.show(spotFor(Nav.current) || planIntroSpot(), true);
}
function planIntroSpot() {
  return { kicker: t("plan_title"), title: t("plan_intro_title"), text: t("plan_intro_text"), glyph: "guide", c1: "#ff7849", c2: "#7b3cff" };
}
act("plan-slot", (d) => planLoad(d.slot));
act("plan-item", (d) => {
  const it = S.plan.map[d.key]; if (!it) return;
  const now = Date.now() / 1000, live = it.start <= now && now < it.end, set = hasReminder(it);
  const items = [];
  if (live) items.push({ label: t("watch_now"), icon: "play", value: "play" });
  if (!live && !set) items.push({ label: t("plan_remind"), sub: t("plan_remind_sub"), icon: "clock", value: "remind" },
    { label: t("plan_remind_auto"), sub: t("plan_remind_auto_sub"), icon: "tv", value: "auto" });
  if (set) items.push({ label: t("plan_remind_del"), icon: "trash", value: "del", danger: true });
  if (!live) items.push({ label: t("plan_channel_now"), icon: "play", value: "play" });
  dialog({ title: it.title, text: esc(`${it.name} · ${hm(it.start)} – ${hm(it.end)}`), items, onPick: async (v) => {
    if (v === "play") { playChannel(it.name); return; }
    const r = await api("/api/reminders", { action: v === "del" ? "del" : "add", item: it, auto: v === "auto" });
    if (r && r.reminders) S.plan.reminders = r.reminders;
    toast(t(v === "del" ? "plan_reminder_deleted" : v === "auto" ? "plan_reminder_auto_ok" : "plan_reminder_ok", { t: hm(it.start) }), "clock");
    if (S.view === "planner") { if (S.plan.slot === "rem") S.plan.items = S.plan.reminders; renderPlanner(`[data-key="${CSS.escape(d.key)}"]`); } else refreshView();
  } });
});
function showReminder(m) {
  const r = m.reminder || {};
  if (m.switched) { toast(t("plan_switched", { t: r.title, c: r.name }), "tv"); return; }
  if (S.view === "wizard" || S.view === "installer") return;
  if (S.ambient) stopAmbient();
  dialog({ title: t("plan_soon"), text: `<b style="font-size:34px">${esc(r.title)}</b><br>${esc(r.name)} · ${hm(r.start)}`,
    buttons: [{ label: t("later") }, { label: t("plan_watch"), primary: true, focus: true, action: () => playChannel(r.name) }] });
}

function renderHome(focusSel) {
  const fresh = S.view !== "home" || S._lastTab !== S.tab;
  S.view = "home"; S._lastTab = S.tab;
  const body = S.tab === "apps" ? homeApps() : S.tab === "live" ? homeLive() : S.tab === "media" ? homeMedia() : homeForYou();
  $("#app").innerHTML = `${topbar()}${spotHtml()}<div class="home-scroll"><div class="home-content${fresh ? " enter" : ""}">${body}</div></div>`;
  placeTabInk(fresh);
  Spot.shown = null;
  if (focusSel) Nav.first(focusSel);
  else if (!Nav.layers.length) Nav.first(S.tab === "foryou" ? "#spot .btn" : ".tab.active");
  if (!Nav.current || !Nav.current.isConnected || !$("#app").contains(Nav.current)) Spot.show(null, true);
  else Spot.show(spotFor(Nav.current), true);
}
function goHome(tab, focusSel) {
  if (tab) S.tab = tab;
  Nav.popAll();
  renderHome(focusSel);
}

/* Hero-Karussell weiterschalten, wenn die Startseite sichtbar und das Hero nicht fokussiert ist */
setInterval(() => {
  if (S.view !== "home" || S.tab !== "foryou" || Nav.layers.length || S.ambient) return;
  const spot = $("#spot"), hero = spot && $(".spot-hero", spot);
  if (!hero || hero.contains(Nav.current) || !spot.classList.contains("mode-hero")) return;
  S.heroIndex++;
  const tmp = document.createElement("div"); tmp.innerHTML = heroSpotHtml();
  const next = tmp.firstElementChild; next.classList.add("swap");
  hero.replaceWith(next);
  Spot.hero();
}, 9000);

/* Uhr */
setInterval(() => {
  const c = $("#clock"); if (c) c.textContent = clockText();
  const sl = $("#sleep-left");
  if (sl && S.sleepEnds) sl.textContent = fmtDuration(Math.max(0, S.sleepEnds - Date.now() / 1000));
  const at = $(".amb-time"); if (at) { at.textContent = clockText(); }
}, 1000);

/* ====================================================================== Energie-Menü */
function openPowerMenu() {
  const sleepOpts = [0, 15, 30, 45, 60, 90, 120];
  dialog({
    title: t("power_title"),
    items: [
      { label: t("standby"), sub: t("standby_sub"), icon: "moon", value: "standby", selected: true },
      { label: t("sleep_timer"), sub: S.sleepEnds ? t("sleep_active", { t: fmtDuration(S.sleepEnds - Date.now() / 1000) }) : t("sleep_sub"), icon: "clock", value: "sleep" },
      { label: t("reboot"), icon: "refresh", value: "reboot" },
      { label: t("poweroff"), icon: "power", value: "poweroff", danger: true },
    ],
    onPick: (v) => {
      if (v === "sleep") {
        dialog({ title: t("sleep_timer"), items: sleepOpts.map((m) => ({ label: m ? t("minutes", { n: m }) : t("off"), value: m })),
          onPick: async (m) => { const r = await api("/api/sleeptimer", { minutes: m }); S.sleepEnds = r.ends || 0; toast(m ? t("sleep_set", { n: m }) : t("sleep_off"), "moon"); refreshView(); } });
      } else if (v === "standby") api("/api/power", { action: "standby" });
      else confirmBox(t(v === "reboot" ? "reboot_q" : "poweroff_q"), "", t(v), () => api("/api/power", { action: v }), v === "poweroff");
    },
  });
}

/* ====================================================================== Einstellungen */
const CATS = [
  ["network", "wifi"], ["remote", "remote"], ["display", "display"], ["apps", "apps"], ["livetv", "guide"], ["television", "tv"],
  ["personal", "star"], ["android", "android"], ["cast", "cast"], ["design", "sparkle"], ["ambient", "image"], ["updates", "update"], ["device", "device"], ["system", "info"],
];
function openSettings(cat) {
  Nav.popAll();
  S.view = "settings";
  if (cat) S.cat = cat;
  $("#app").innerHTML = `<div class="settings"><nav class="set-nav" data-row="setnav"><h1>${esc(t("settings"))}</h1>
    ${CATS.map(([id, ic]) => `<button class="set-cat f${S.cat === id ? " active" : ""}" data-act="cat" data-cat="${id}">${icon(ic)}<span>${esc(t(`cat_${id}`))}</span></button>`).join("")}
    </nav><section class="set-body"><div class="set-body-inner" id="panel"></div></section></div>`;
  const catBtn = $(`.set-cat[data-cat='${S.cat}']`);
  Nav.first(`.set-cat[data-cat='${S.cat}']`);
  renderPanel(true).then(() => {
    // Direkt auf eine Seite geöffnet: nach dem Laden auf den ersten Eintrag springen (sofern nicht schon weiterbewegt)
    const first = $("#panel .f");
    if (cat && first && S.view === "settings" && S.cat === cat && (Nav.current === catBtn || !Nav.current || !Nav.current.isConnected)) Nav.focus(first);
  });
}
act("cat", (d) => {
  if (S.cat === d.cat) { const f = $("#panel .f"); if (f) Nav.focus(f); return; }
  S.cat = d.cat;
  $$(".set-cat").forEach((b) => b.classList.toggle("active", b.dataset.cat === d.cat));
  renderPanel(true);
  const f = $("#panel .f"); if (f) Nav.focus(f);
});

async function renderPanel(fetchData = false) {
  const cat = S.cat;
  const panel = $("#panel"); if (!panel) return;
  if (fetchData && Panels[cat].load) {
    if (!panel.innerHTML) panel.innerHTML = `<h2>${esc(t(`cat_${cat}`))}</h2><p class="lead">${esc(t("loading"))}</p>`;
    await Panels[cat].load();
    if (S.cat !== cat || S.view !== "settings") return;
  }
  const focusedAct = Nav.current && panel.contains(Nav.current) ? [Nav.current.dataset.act, Nav.current.dataset.k || Nav.current.dataset.v || ""] : null;
  panel.innerHTML = `<h2>${esc(t(`cat_${cat}`))}</h2>${Panels[cat].lead ? `<p class="lead">${Panels[cat].lead()}</p>` : ""}${Panels[cat].render()}`;
  if (focusedAct) {
    const again = $$(".f", panel).find((el) => el.dataset.act === focusedAct[0] && (el.dataset.k || el.dataset.v || "") === focusedAct[1]);
    Nav.focus(again || $(".f", panel) || $(`.set-cat[data-cat='${cat}']`), { scroll: !!again });
  } else if (Nav.current && !Nav.current.isConnected) {
    Nav.first(`.set-cat[data-cat='${cat}']`);
  }
}
function refreshPanel() { if (S.view === "wizard") wzRefresh(); else if (S.view === "settings") renderPanel(false); }
function refreshView() {
  if (S.view !== "home") { refreshPanel(); return; }
  const c = Nav.current && Nav.current.isConnected ? Nav.current : null;
  const sel = !c ? null : c.dataset.id ? `[data-id="${CSS.escape(c.dataset.id)}"]` : c.dataset.path ? `.media[data-path="${CSS.escape(c.dataset.path)}"]`
    : c.dataset.tab ? `.tab[data-tab="${c.dataset.tab}"]` : null;
  renderHome(sel);
}

/* Allgemeine Einstellungsaktionen */
act("toggle", async (d) => {
  const path = d.k.split(".");
  const patch = {}; let node = patch; let cur = S.settings;
  for (let i = 0; i < path.length - 1; i++) { node[path[i]] = {}; node = node[path[i]]; cur = cur[path[i]]; }
  const was = cur[path[path.length - 1]];
  node[path[path.length - 1]] = d.on !== undefined ? (was === d.on ? d.off : d.on) : !was;
  await saveSettings(patch); applyTheme(); refreshPanel();
});
function choose({ title, key, options, current, after }) {
  dialog({ title, items: options.map((o) => ({ label: o.label, sub: o.sub, value: o.value, selected: String(o.value) === String(current) })),
    onPick: async (v) => {
      const path = key.split("."); const patch = {}; let node = patch;
      for (let i = 0; i < path.length - 1; i++) { node[path[i]] = {}; node = node[path[i]]; }
      node[path[path.length - 1]] = v;
      await saveSettings(patch); if (after) await after(v); refreshPanel();
    } });
}
function getPath(key) { return key.split(".").reduce((o, k) => (o ? o[k] : undefined), S.settings); }
function editText({ title, key, secret = false, placeholder = "", after }) {
  OSK.start({ title, value: getPath(key) || "", secret, placeholder, onDone: async (v) => {
    const path = key.split("."); const patch = {}; let node = patch;
    for (let i = 0; i < path.length - 1; i++) { node[path[i]] = {}; node = node[path[i]]; }
    node[path[path.length - 1]] = v.trim();
    await saveSettings(patch); if (after) await after(v.trim()); refreshPanel();
  } });
}

const Panels = {};

/* ---- Netzwerk */
Panels.network = {
  async load() { S.panelData.net = await api("/api/network"); S.panelData.wifi = (await api("/api/wifi/scan")).networks || []; },
  lead: () => esc(t("net_lead")),
  render() {
    const n = S.panelData.net || {}, eth = n.ethernet, wifi = n.wifi;
    let h = section(t("net_status"));
    h += item({ icon: "ethernet", title: t("ethernet"), sub: eth ? (eth.connected ? t("connected_ip", { ip: eth.ip }) : t("not_connected")) : t("no_adapter") });
    if (wifi) {
      h += item({ act: "wifi-toggle", icon: "wifi", title: t("wifi"), sub: wifi.connected ? t("connected_to", { ssid: esc(wifi.connection), ip: wifi.ip }) : t("not_connected"), toggle: wifi.enabled !== false });
      if (wifi.enabled !== false) {
        h += section(t("networks"));
        for (const net of S.panelData.wifi || []) {
          h += item({ act: "wifi-net", icon: net.secure ? "lock" : "wifi", title: esc(net.ssid), sub: net.active ? t("connected") : (net.secure ? t("secured") : t("open_network")), val: signalBars(net.signal), data: { v: net.ssid, secure: net.secure ? 1 : 0, active: net.active ? 1 : 0 } });
        }
        h += item({ act: "wifi-rescan", icon: "refresh", title: t("rescan") });
      }
    }
    h += section(t("speed_title"));
    h += item({ act: "speedtest", icon: "download", title: t("speed_test"), sub: S.panelData.speed ? speedSummary(S.panelData.speed) : t("speed_test_sub") });
    return h;
  },
};
function speedSummary(r) {
  if (!r.ok) return t("speed_failed");
  return t("speed_result", { down: r.down ?? "—", up: r.up ?? "—", ping: r.ping ?? "—" }) + " · " + t(`speed_rating_${r.rating}`);
}
act("speedtest", async () => {
  const box = overlay(`<div class="dialog"><h2>${esc(t("speed_test"))}</h2><p>${esc(t("speed_running"))}</p><div class="spinner"></div></div>`, "busy");
  let r;
  try { r = await api("/api/speedtest", {}); } finally { box.remove(); }
  S.panelData.speed = r || { ok: false };
  refreshPanel();
  dialog({ title: t("speed_test"), text: r && r.ok ? `<b>${esc(speedSummary(r))}</b><br><br>${esc(t(`speed_explain_${r.rating}`))}` : esc(t("speed_failed")),
    buttons: [{ label: t("done"), primary: true, focus: true }] });
});
act("wifi-toggle", async () => { const on = !(S.panelData.net.wifi.enabled !== false); await api("/api/wifi/enable", { on }); await sleep(800); await Panels.network.load(); refreshPanel(); });
act("wifi-rescan", async () => { toast(t("scanning"), "wifi"); await Panels.network.load(); refreshPanel(); });
act("wifi-net", (d) => connectWifi(d.v, d.secure === "1", d.active === "1", () => { Panels.network.load().then(refreshPanel); }));
function connectWifi(ssid, secure, active, after) {
  if (active) {
    confirmBox(ssid, t("wifi_forget_q"), t("forget"), async () => { await api("/api/wifi/forget", { ssid }); after && after(); }, true);
    return;
  }
  const doConnect = async (password) => {
    toast(t("connecting_to", { ssid }), "wifi");
    const r = await api("/api/wifi/connect", { ssid, password });
    if (r.ok) toast(t("connected_ok", { ssid }), "check");
    else toast(r.message === "wrong_password" ? t("wrong_password") : t("connect_failed"), "alert");
    after && after(r.ok);
  };
  if (secure) OSK.start({ title: t("wifi_password_for", { ssid }), secret: true, placeholder: t("password"), onDone: doConnect });
  else doConnect("");
}

/* ---- Fernbedienung & Zubehör */
const ACTION_CHOICES = () => [
  { label: t("act_channel_up"), sub: t("act_channel_up_sub"), value: "channel_up" }, { label: t("act_channel_down"), sub: t("act_channel_down_sub"), value: "channel_down" },
  { label: t("act_channel_last"), value: "channel_last" }, { label: t("act_channel_list"), value: "channel_list" },
  { label: t("tv_guide"), value: "app:tvguide" }, { label: t("act_tv_info"), sub: t("act_tv_info_sub"), value: "tv_info" },
  { label: t("act_playpause"), value: "playpause" }, { label: t("act_stop"), sub: t("act_stop_sub"), value: "stop" },
  ...S.apps.all.filter((a) => !["settings", "tvguide"].includes(a.id)).map((a) => ({ label: t("action_open", { name: a.name }), value: `app:${a.id}` })),
  { label: t("act_home"), value: "home" }, { label: t("settings"), value: "settings" }, { label: t("standby"), value: "standby" },
  { label: t("power_title"), value: "power_menu" }, { label: t("act_volup"), value: "volume_up" }, { label: t("act_voldown"), value: "volume_down" },
  { label: t("act_mute"), value: "mute" },
];
function actionLabel(a) { const c = ACTION_CHOICES().find((x) => x.value === a); return c ? c.label : a; }
Panels.remote = {
  async load() {
    S.panelData.bt = (await api("/api/bt")).devices || []; S.panelData.remote = await api("/api/remote");
    S.panelData.ir = await api("/api/ir");
  },
  lead: () => esc(t("remote_lead")),
  render() {
    const rem = S.panelData.remote || {}, cec = rem.cec || {};
    let h = callout(t("remote_pair_hint"));
    h += section(t("bt_devices"));
    for (const dev of S.panelData.bt || []) {
      h += item({ act: "bt-dev", icon: dev.icon.includes("audio") ? "sound" : dev.icon.includes("gaming") ? "remote" : dev.icon.includes("keyboard") || dev.icon.includes("input") ? "remote" : "bluetooth",
        title: esc(dev.name), sub: dev.connected ? t("connected") : dev.paired ? t("paired") : t("tap_to_pair"), data: { v: dev.mac, paired: dev.paired ? 1 : 0, connected: dev.connected ? 1 : 0, name: dev.name } });
    }
    h += item({ act: "bt-scan", icon: "refresh", title: t("bt_search"), sub: t("bt_search_sub") });
    h += item({ act: "phone-remote", icon: "phone", title: t("phone_remote"), sub: t("phone_remote_sub") });
    h += section(t("keys"));
    h += item({ act: "power-key", icon: "power", title: t("power_key"), sub: t("power_key_sub"), val: esc(t(`power_key_${S.settings.power_key}`)) });
    for (const [i, m] of (S.settings.remote.mappings || []).entries()) {
      h += item({ act: "map-edit", icon: "key", title: esc(m.label || m.match), sub: esc(m.match), val: esc(actionLabel(m.action)), data: { i } });
    }
    h += item({ act: "map-learn", icon: "plus", title: t("learn_key"), sub: t("learn_key_sub") });
    h += section(t("ir_box"));
    const ir = S.panelData.ir || {};
    if (!ir.connected) {
      h += callout(t("ir_box_missing"));
    } else {
      h += item({ icon: "check", title: t("ir_box_ok"), sub: t("ir_box_fw", { v: esc(ir.fw || "?") }) + (ir.last ? ` · ${t("ir_last", { c: esc(ir.last) })}` : "") });
      for (const slot of ["pwr", "tvp", "avon", "avoff", "avp"]) {
        const code = (ir.slots || {})[slot] || "-";
        h += item({ act: "ir-slot", icon: slot === "pwr" ? "power" : slot === "tvp" ? "tv" : "sound", title: t(`ir_${slot}`),
          sub: code === "-" ? t(`ir_${slot}_sub`) : t("ir_learned", { c: esc(code) }), val: code === "-" ? t("not_set") : "", data: { slot } });
      }
    }
    h += section(t("cec"));
    const sc = S.settings.cec || {};
    if (cec.adapter) {
      const inf = cec.info || {};
      h += item({ icon: "check", title: inf.name || t("cec_found"),
        sub: [inf.firmware ? t("cec_fw", { v: esc(inf.firmware) }) : "", cec.port ? t("cec_addr", { a: esc(cec.port) }) : ""].filter(Boolean).join(" · ") || t("cec_ready"),
        val: cec.active ? t("cec_active") : "", chev: false });
      for (const d of cec.devices || []) {
        const kind = d.la === 0 ? "tv" : d.la === 5 ? "sound" : "device";
        const vol = d.la === 5 && cec.avr_volume != null ? ` · ${cec.avr_mute ? t("muted") : t("cec_vol", { n: cec.avr_volume })}` : "";
        h += item({ icon: kind, title: esc(d.name && d.name !== "TV" ? d.name : d.type), sub: esc([d.vendor, d.addr ? `HDMI ${d.addr}` : ""].filter(Boolean).join(" · ")) + vol,
          val: esc(t(`cec_pow_${(d.power || "").startsWith("on") ? "on" : (d.power || "").includes("standby") ? "standby" : "unknown"}`)), chev: false });
      }
      h += item({ act: "cec-active", icon: "tv", title: t("cec_select_input"), sub: t("cec_select_input_sub") });
      h += item({ act: "cec-scan", icon: "refresh", title: t("cec_scan"), sub: t("cec_scan_sub") });
    } else {
      h += callout(t("cec_missing"), true);
    }
    h += item({ act: "toggle", icon: "power", title: t("cec_tv_on"), toggle: sc.tv_on_with_box, data: { k: "cec.tv_on_with_box" } });
    h += item({ act: "toggle", icon: "moon", title: t("cec_standby_with_tv"), toggle: sc.standby_with_tv, data: { k: "cec.standby_with_tv" } });
    h += item({ act: "toggle", icon: "tv", title: t("cec_tv_off"), toggle: sc.tv_off_on_standby, data: { k: "cec.tv_off_on_standby" } });
    h += item({ act: "toggle", icon: "stop", title: t("cec_pause_switch"), sub: t("cec_pause_switch_sub"), toggle: sc.pause_on_switch !== false, data: { k: "cec.pause_on_switch" } });
    h += item({ act: "cec-volume", icon: "sound", title: t("cec_volume"), sub: t(`cec_volume_${sc.volume || "auto"}_sub`), val: esc(t(`cec_volume_${sc.volume || "auto"}`)) });
    h += item({ act: "cec-port", icon: "display", title: t("cec_port"), sub: t("cec_port_sub"),
      val: sc.hdmi_port ? `${t(sc.via === "avr" ? "cec_via_avr" : "cec_via_tv")} · HDMI ${sc.hdmi_port}` : t("auto") });
    return h;
  },
};
act("phone-remote", async () => {
  const r = await api("/api/phoneremote");
  if (!r.url) { toast(t("action_failed"), "alert"); return; }
  dialog({ title: t("phone_remote"), wide: true,
    text: `<span class="qr-box" style="display:flex;gap:36px;align-items:center"><span class="qr" style="flex:0 0 300px">${r.qr || ""}</span>
      <span style="display:block"><span style="display:block;font-size:22px;line-height:1.45">${esc(t("phone_remote_howto"))}</span>
      <span style="display:block;margin-top:14px;opacity:.7;font-size:19px;word-break:break-all">${esc(r.url)}</span></span></span>`,
    buttons: [{ label: t("done"), primary: true, focus: true },
      { label: t("phone_remote_renew"), action: async () => { await api("/api/phoneremote/renew", {}); toast(t("phone_remote_renewed")); } }] });
});
async function irLearn(slot) {
  toast(t("ir_press"), "remote");
  const r = await api("/api/ir", { action: "learn", slot });
  toast(r.ok ? t("ir_saved") : t(r.message === "timeout" ? "ir_timeout" : "action_failed"), r.ok ? "check" : "alert");
  await Panels.remote.load(); refreshPanel();
}
act("ir-slot", (d) => {
  const slot = d.slot; const learned = (((S.panelData.ir || {}).slots || {})[slot] || "-") !== "-";
  const items = [{ label: learned ? t("ir_relearn") : t("ir_learn"), sub: t(`ir_${slot}_sub`), icon: "plus", value: "learn" }];
  if (learned && slot !== "pwr") items.push({ label: t("ir_test"), icon: "play", value: "test" });
  if (learned) items.push({ label: t("ir_clear"), icon: "trash", value: "clear", danger: true });
  dialog({ title: t(`ir_${slot}`), items, onPick: async (v) => {
    if (v === "learn") irLearn(slot);
    else if (v === "test") { const r = await api("/api/ir", { action: "send", slot }); toast(r.ok ? t("ir_sent") : t("action_failed"), r.ok ? "check" : "alert"); }
    else if (v === "clear") { await api("/api/ir", { action: "clear", slot }); await Panels.remote.load(); refreshPanel(); }
  } });
});
act("bt-scan", async () => { await api("/api/bt/scan", {}); toast(t("bt_searching"), "bluetooth");
  for (let i = 0; i < 6; i++) { await sleep(3500); if (S.cat !== "remote" || S.view !== "settings") return; await Panels.remote.load(); refreshPanel(); } });
act("bt-dev", (d) => {
  if (d.paired !== "1") {
    toast(t("pairing", { name: d.name }), "bluetooth");
    api("/api/bt/pair", { mac: d.v }).then(async (r) => { toast(r.ok ? t("paired_ok", { name: d.name }) : t("pair_failed"), r.ok ? "check" : "alert"); await Panels.remote.load(); refreshPanel(); });
    return;
  }
  dialog({ title: d.name, items: [
    d.connected === "1" ? { label: t("disconnect"), icon: "x", value: "disconnect" } : { label: t("connect"), icon: "bluetooth", value: "connect" },
    { label: t("remove_device"), icon: "trash", value: "remove", danger: true }],
    onPick: async (v) => { await api("/api/bt/action", { action: v, mac: d.v }); await Panels.remote.load(); refreshPanel(); } });
});
act("power-key", () => choose({ title: t("power_key"), key: "power_key", current: S.settings.power_key,
  options: [{ label: t("power_key_standby"), sub: t("power_key_standby_sub"), value: "standby" },
    { label: t("power_key_suspend"), sub: t("power_key_suspend_sub"), value: "suspend" },
    { label: t("power_key_menu"), sub: t("power_key_menu_sub"), value: "menu" }] }));
act("cec-port", () => {
  const sc = S.settings.cec || {};
  const cur = sc.hdmi_port ? `${sc.via === "avr" ? "avr" : "tv"}:${sc.hdmi_port}` : "auto";
  const opts = [{ label: t("auto"), sub: t("cec_port_auto_sub"), value: "auto" }];
  for (const via of ["tv", "avr"]) for (const n of [1, 2, 3, 4, 5, 6]) opts.push({ label: `${t(via === "avr" ? "cec_via_avr" : "cec_via_tv")} · HDMI ${n}`, value: `${via}:${n}` });
  dialog({ title: t("cec_port"), items: opts.map((o) => ({ ...o, selected: o.value === cur })), onPick: async (v) => {
    const [via, n] = v === "auto" ? ["tv", 0] : v.split(":");
    await saveSettings({ cec: { via, hdmi_port: +n } }); refreshPanel();
  } });
});
act("cec-volume", () => choose({ title: t("cec_volume"), key: "cec.volume", current: (S.settings.cec || {}).volume || "auto",
  options: ["auto", "avr", "box"].map((v) => ({ label: t(`cec_volume_${v}`), sub: t(`cec_volume_${v}_sub`), value: v })) }));
act("cec-active", async () => { const r = await api("/api/remote/cec", { cmd: "active" }); toast(r && r.ok ? t("cec_input_selected") : t("action_failed"), r && r.ok ? "check" : "alert"); });
act("cec-scan", async () => {
  await api("/api/remote/cec", { cmd: "cec_scan" }); toast(t("cec_scanning"), "refresh");
  setTimeout(async () => { if (S.view === "settings" && S.cat === "remote") { await Panels.remote.load(); refreshPanel(); } }, 6000);
});
act("map-learn", async () => {
  const dlg = dialog({ title: t("learn_key"), text: t("learn_press"), buttons: [{ label: t("cancel") }] });
  const r = await api("/api/remote/learn", {});
  if (!dlg.isConnected) return;
  Nav.pop();
  if (!r.ok || !r.match) { toast(t("learn_none"), "alert"); return; }
  const label = r.key && r.key !== "KEY_UNKNOWN" ? r.key.replace("KEY_", "") : t("special_key");
  dialog({ title: t("learn_got", { key: label }), text: esc(r.device || ""), items: ACTION_CHOICES(), onPick: async (action) => {
    const maps = (S.settings.remote.mappings || []).filter((m) => m.match !== r.match);
    maps.push({ match: r.match, action, label });
    await saveSettings({ remote: { mappings: maps } }); refreshPanel(); toast(t("key_saved"), "check");
  } });
});
act("map-edit", (d) => {
  const maps = [...S.settings.remote.mappings]; const m = maps[+d.i];
  dialog({ title: m.label || m.match, items: [{ label: t("change_action"), icon: "key", value: "change" }, { label: t("delete"), icon: "trash", value: "delete", danger: true }],
    onPick: (v) => {
      if (v === "delete") { maps.splice(+d.i, 1); saveSettings({ remote: { mappings: maps } }).then(refreshPanel); return; }
      dialog({ title: t("change_action"), items: ACTION_CHOICES().map((c) => ({ ...c, selected: c.value === m.action })), onPick: async (action) => {
        maps[+d.i] = { ...m, action }; await saveSettings({ remote: { mappings: maps } }); refreshPanel(); } });
    } });
});

/* ---- Bild & Ton */
Panels.display = {
  async load() { S.panelData.disp = await api("/api/display"); S.panelData.audio = await api("/api/audio"); S.panelData.adrv = await api("/api/audiodriver"); },
  lead: () => esc(t("display_lead")),
  render() {
    const out = ((S.panelData.disp || {}).outputs || [])[0];
    const au = S.panelData.audio || { sinks: [] };
    const mode = S.settings.display.mode;
    const cur = out ? `${out.current.width}×${out.current.height} · ${out.current.refresh} Hz` : "—";
    let h = section(t("picture"));
    h += item({ act: "res", icon: "display", title: t("resolution"), sub: out ? esc(out.label || out.name) : t("no_display"), val: mode === "auto" ? `${t("auto")} (${cur})` : cur });
    h += item({ act: "scale", icon: "eye", title: t("ui_scale"), sub: t("ui_scale_sub"), val: S.settings.display.scale === "auto" ? t("auto") : `${S.settings.display.scale}×` });
    h += callout(t("display_4k_hint"));
    h += section(t("sound"));
    const sink = au.sinks.find((s) => s.name === au.default);
    h += item({ act: "sink", icon: "sound", title: t("audio_out"), val: esc(sink ? sink.label : "—") });
    h += item({ act: "noop", icon: "sound", title: t("volume"), sub: t("volume_sub"), val: `${au.volume}%`, lr: "vol", chev: false });
    h += item({ act: "audio-test", icon: "music", title: t("audio_test"), sub: t("audio_test_sub") });
    const rc = au.receiver;
    if (rc) {
      const fmts = [t("pcm_ch", { n: rc.pcm_channels }), ...rc.formats.map((f) => FORMAT_NAMES[f] || f)];
      h += item({ act: "noop", icon: "sound", title: t("receiver"), sub: esc(fmts.join(" · ")), val: esc(rc.name || "HDMI"), chev: false });
      if ((S.settings.audio.speakers || "2.0") !== rc.layout || !!S.settings.kodi.passthrough !== !!rc.passthrough) {
        h += item({ act: "receiver-apply", icon: "sparkle", title: t("receiver_apply"),
          sub: t(rc.passthrough ? "receiver_apply_sub" : "receiver_apply_sub_pcm", { layout: t(`sp_${rc.layout.replace(".", "")}`) }) });
      }
    } else {
      h += item({ act: "noop", icon: "sound", title: t("receiver"), sub: t("receiver_none"), chev: false });
    }
    h += item({ act: "speakers", icon: "sound", title: t("speakers"), sub: t("speakers_sub"), val: esc(t(`sp_${(S.settings.audio.speakers || "2.0").replace(".", "")}`)) });
    if ((S.settings.audio.speakers || "2.0") !== "2.0") {
      const pm = S.settings.audio.pcm_mode === "multi" ? "multi" : "auto";
      h += item({ act: "pcm-mode", icon: "sound", title: t("pcm_mode"), sub: t(`pcm_${pm}_sub`), val: esc(t(`pcm_${pm}`)) });
      if (pm === "multi") {
        h += item({ act: "toggle", icon: "sound", title: t("upmix"), sub: t("upmix_sub"), toggle: S.settings.audio.upmix !== false, data: { k: "audio.upmix" } });
      } else if (!S.settings.kodi.passthrough) {
        h += callout(t("pcm_auto_needs_pt"));
      }
    }
    h += item({ act: "toggle", icon: "sparkle", title: t("passthrough"), sub: t("passthrough_sub"), toggle: S.settings.kodi.passthrough, data: { k: "kodi.passthrough" } });
    if (S.settings.kodi.passthrough) {
      h += item({ act: "toggle", icon: "sparkle", title: t("hd_passthrough"), sub: t("hd_passthrough_sub"), toggle: !!S.settings.kodi.hd_passthrough, data: { k: "kodi.hd_passthrough" } });
    }
    const ad = S.panelData.adrv || {};
    if (ad.switchable) {
      h += item({ act: "audio-driver", icon: "sound", title: t("audio_driver"), sub: t(ad.current === "legacy" ? "audio_driver_legacy_sub" : "audio_driver_sof_sub"),
        val: esc(t(ad.current === "legacy" ? "audio_driver_legacy" : "audio_driver_sof")) });
    }
    h += callout(t("codecs_info"));
    return h;
  },
};
act("noop", () => {});
act("audio-driver", () => {
  const cur = (S.panelData.adrv || {}).current;
  dialog({
    title: t("audio_driver"), text: esc(t("audio_driver_text")),
    items: [{ label: t("audio_driver_legacy"), sub: t("audio_driver_legacy_sub"), value: "legacy", selected: cur === "legacy" },
      { label: t("audio_driver_sof"), sub: t("audio_driver_sof_sub"), value: "sof", selected: cur !== "legacy" }],
    onPick: (v) => {
      if ((v === "legacy") === (cur === "legacy")) return;
      confirmBox(t("audio_driver"), t("audio_driver_reboot"), t("reboot"), async () => {
        const r = await api("/api/audiodriver", { driver: v });
        toast(r && r.ok ? t("audio_driver_switching") : t("action_failed"), r && r.ok ? "refresh" : "alert");
      });
    },
  });
});
const FORMAT_NAMES = { ac3: "Dolby Digital", eac3: "Dolby Digital Plus", dts: "DTS", truehd: "Dolby TrueHD (Atmos)",
  dtshd: "DTS-HD MA (DTS:X)", aac: "AAC" };
act("receiver-apply", async () => {
  const rc = (S.panelData.audio || {}).receiver; if (!rc) return;
  await saveSettings({ audio: { speakers: rc.layout }, kodi: { passthrough: !!rc.passthrough } });
  toast(t("receiver_applied"));
  refreshPanel();
});
act("pcm-mode", () => choose({ title: t("pcm_mode"), key: "audio.pcm_mode", current: S.settings.audio.pcm_mode === "multi" ? "multi" : "auto",
  options: ["auto", "multi"].map((v) => ({ label: t(`pcm_${v}`), sub: t(`pcm_${v}_sub`), value: v })) }));
act("speakers", () => choose({ title: t("speakers"), key: "audio.speakers", current: S.settings.audio.speakers || "2.0",
  options: ["2.0", "5.1", "7.1"].map((v) => ({ label: t(`sp_${v.replace(".", "")}`), sub: t(`sp_${v.replace(".", "")}_sub`), value: v })) }));
act("res", () => {
  const out = ((S.panelData.disp || {}).outputs || [])[0]; if (!out) return;
  choose({ title: t("resolution"), key: "display.mode", current: S.settings.display.mode,
    options: [{ label: t("auto"), sub: t("auto_res_sub"), value: "auto" }, ...out.modes.map((m) => ({ label: `${m.width} × ${m.height}`, sub: `${m.refresh} Hz${m.width >= 3840 ? " · 4K UHD" : m.width >= 1920 ? " · Full HD" : ""}`, value: `${m.width}x${m.height}@${m.refresh}` }))],
    after: async () => { await sleep(1500); await Panels.display.load(); } });
});
act("scale", () => choose({ title: t("ui_scale"), key: "display.scale", current: S.settings.display.scale,
  options: [{ label: t("auto"), value: "auto" }, { label: "1×", value: 1 }, { label: "1,25×", value: 1.25 }, { label: "1,5×", value: 1.5 }, { label: "2×", value: 2 }] }));
act("sink", () => {
  const au = S.panelData.audio;
  dialog({ title: t("audio_out"), items: au.sinks.map((s) => ({ label: s.label, value: s.name, selected: s.name === au.default })),
    onPick: async (name) => { await saveSettings({ audio: { sink: name } }); await api("/api/audio", { sink: name }); await Panels.display.load(); refreshPanel(); } });
});
act("vol", async (d, el, dir) => {
  const au = S.panelData.audio; au.volume = clamp(au.volume + dir * 5, 0, 100);
  $(".val", el).textContent = `${au.volume}%`;
  api("/api/audio", { volume: au.volume });
});
act("audio-test", () => api("/api/audio/test", {}));

/* ---- Apps */
Panels.apps = {
  async load() { await loadApps(); S.panelData.jobs = (await api("/api/jobs")).jobs || []; },
  lead: () => esc(t("apps_lead")),
  render() {
    let h = section(t("home_row"));
    for (const [i, a] of S.apps.home.entries()) {
      h += item({ act: "home-app", icon: a.glyph, title: esc(a.name), sub: esc(L(a.desc)), val: `${i + 1}`, data: { id: a.id } });
    }
    h += item({ act: "add-app", icon: "plus", title: t("add_app") });
    h += section(t("web_apps"));
    h += item({ act: "toggle", icon: "remote", title: t("tvnav"), sub: t("tvnav_sub"), toggle: S.settings.tvnav !== false, data: { k: "tvnav" } });
    if (S.apps.running.length) {
      h += section(t("running_apps"));
      for (const a of S.apps.running) h += item({ act: "running-close", icon: "stop", title: esc(a.name), sub: t("tap_to_close"), data: { key: a.key } });
    }
    const jobs = S.panelData.jobs || [];
    if (jobs.length) {
      h += section(t("installs"));
      for (const j of jobs) h += item({ icon: j.state === "error" ? "alert" : j.state === "done" ? "check" : "download", title: esc(j.label), sub: esc(t(`job_${j.state}`) + (j.state === "error" ? ` – ${j.message}` : "")) });
    }
    return h;
  },
};
act("home-app", (d) => {
  const order = [...S.settings.apps]; const idx = order.indexOf(d.id);
  const items = [];
  if (idx > 0) items.push({ label: t("move_up"), icon: "left", value: -1 });
  if (idx < order.length - 1) items.push({ label: t("move_down"), icon: "right", value: 1 });
  items.push({ label: t("remove_from_home"), icon: "trash", value: 0, danger: true });
  dialog({ title: S.apps.home[idx].name, items, onPick: async (v) => {
    if (v === 0) order.splice(idx, 1); else { const j = idx + v; [order[idx], order[j]] = [order[j], order[idx]]; }
    await saveSettings({ apps: order }); await loadApps(); refreshPanel();
  } });
});

/* ---- Live-TV */
function waipuItems(wz) {
  const w = S.settings.waipu || {};
  let h = section(t("waipu_section")) + callout(t("waipu_hint"));
  h += item({ act: "waipu-provider", icon: "tv", title: t("waipu_provider"), val: esc(t(`waipu_prov_${w.provider || 0}`)) });
  h += item({ act: "waipu-user", icon: "device", title: t("waipu_user"), sub: w.username ? esc(w.username) : t("not_set") });
  h += item({ act: "waipu-pass", icon: "lock", title: t("password"), sub: w.password ? "••••••••" : t("not_set") });
  if (w.username && w.password) h += item({ icon: "check", title: t("waipu_ok"), sub: t("waipu_ok_sub") });
  return h;
}
act("waipu-provider", () => choose({ title: t("waipu_provider"), key: "waipu.provider", current: (S.settings.waipu || {}).provider || 0,
  options: [0, 1].map((v) => ({ label: t(`waipu_prov_${v}`), value: v })) }));
act("waipu-user", () => editText({ title: t("waipu_user"), key: "waipu.username", placeholder: "name@beispiel.de", after: loadChannels }));
act("waipu-pass", () => editText({ title: t("waipu_pass_title"), key: "waipu.password", secret: true, after: loadChannels }));

Panels.livetv = {
  lead: () => esc(t("livetv_lead")),
  render() {
    const iptv = S.settings.iptv; const res = S.panelData.iptvTest;
    let h = waipuItems(false) + section(t("iptv_section")) + callout(t("iptv_hint"));
    h += item({ act: "m3u", icon: "tv", title: t("m3u_url"), sub: iptv.m3u ? esc(iptv.m3u) : t("not_set"), val: "" });
    h += item({ act: "epg", icon: "guide", title: t("epg_url"), sub: iptv.epg ? esc(iptv.epg) : t("epg_optional"), val: "" });
    if (iptv.m3u) {
      h += item({ act: "iptv-test", icon: "check", title: t("iptv_test"), sub: res ? (res.ok ? t("iptv_ok", { n: res.count, s: esc(res.sample.join(", ")) }) : t("iptv_fail", { m: esc(res.message || "") })) : t("iptv_test_sub") });
      h += item({ act: "refresh-channels", icon: "refresh", title: t("refresh_list"), sub: t("channels_loaded", { n: S.channels.length }) });
      h += item({ act: "toggle", icon: "clock", title: t("timeshift"), sub: t("timeshift_sub"), toggle: !!iptv.timeshift, data: { k: "iptv.timeshift" } });
      h += item({ act: "iptv-refresh-h", icon: "update", title: t("refresh_interval"), val: t("hours", { n: iptv.refresh_hours }) });
    }
    h += section("Kodi");
    h += item({ act: "launch", icon: "tv", title: t("open_livetv"), data: { id: "livetv" } });
    h += item({ act: "launch", icon: "kodi", title: t("open_kodi"), data: { id: "kodi" } });
    return h;
  },
};
act("m3u", () => editText({ title: t("m3u_url"), key: "iptv.m3u", placeholder: "https://…/playlist.m3u", after: async (v) => {
  if (v) { S.panelData.iptvTest = await api("/api/iptv/test", { url: v }); setTimeout(async () => { await loadChannels(); refreshPanel(); }, 4000); }
} }));
act("epg", () => editText({ title: t("epg_url"), key: "iptv.epg", placeholder: "https://…/epg.xml.gz" }));
act("iptv-test", async () => { toast(t("testing"), "refresh"); S.panelData.iptvTest = await api("/api/iptv/test", { url: S.settings.iptv.m3u }); refreshPanel(); });
act("iptv-refresh-h", () => choose({ title: t("refresh_interval"), key: "iptv.refresh_hours", current: S.settings.iptv.refresh_hours,
  options: [6, 12, 24, 48].map((n) => ({ label: t("hours", { n }), value: n })) }));

/* ---- Android & Play Store */
Panels.android = {
  async load() { S.panelData.android = await api("/api/android"); },
  lead: () => esc(t("android_lead")),
  render() {
    const a = S.panelData.android || {};
    let h = "";
    if (!a.installed) return callout(t("android_missing"), true);
    if (!a.binder) h += callout(t("android_no_binder"), true);
    if (!a.initialized && a.state !== "running") {
      h += callout(t("android_info"));
      h += item({ act: "android-init", icon: "download", title: t("android_setup"), sub: t("android_setup_sub") });
      return h;
    }
    if (a.state === "running") {
      return h + `<div class="bigprogress"><div class="pct">${a.progress || 0}%</div><div class="meter"><i style="width:${a.progress || 0}%"></i></div><div class="msg">${esc(a.message || t("android_downloading"))}</div></div>`;
    }
    h += item({ icon: a.running ? "check" : "android", title: t("android_status"), sub: a.running ? t("android_running") : t("android_ready") });
    h += section(t("android_register"));
    const url = "https://www.google.com/android/uncertified";
    h += `<div class="qr-box"><div class="qr" id="android-qr"></div><div><div class="t" style="font-size:24px;font-weight:600">${esc(t("android_register_title"))}</div>
      <p style="color:var(--muted);margin:8px 0 14px">${t("android_register_text")}</p>
      <div>${esc(t("android_id"))}: <b class="mono">${a.android_id ? esc(a.android_id) : "—"}</b></div></div></div>`;
    h += item({ act: "android-id", icon: "refresh", title: t("android_get_id"), sub: t("android_get_id_sub") });
    h += section(t("cat_apps"));
    h += item({ act: "launch", icon: "store", title: t("open_playstore"), data: { id: "playstore" } });
    h += item({ act: "android-full", icon: "android", title: t("open_android"), sub: t("open_android_sub") });
    h += callout(t("android_mouse_hint"));
    h += item({ act: "android-restart", icon: "refresh", title: t("android_restart"), sub: t("android_restart_sub") });
    setTimeout(async () => { const q = $("#android-qr"); if (q && !q.innerHTML) { const r = await api(`/api/qr?text=${encodeURIComponent(url)}`); if (r.svg) q.innerHTML = r.svg; } }, 0);
    return h;
  },
};
act("android-init", () => confirmBox(t("android_setup"), t("android_setup_confirm"), t("start"), async () => {
  await api("/api/android/init", {}); await saveSettings({ android: { wanted: true } }); pollAndroid();
}));
act("android-id", async () => { await api("/api/android/id", {}); toast(t("android_id_wait"), "android"); setTimeout(async () => { await Panels.android.load(); refreshPanel(); }, 8000); });
act("android-full", () => api("/api/android/launch", {}));
act("android-restart", async () => { await api("/api/android/restart", {}); toast(t("android_restarting"), "android");
  setTimeout(async () => { if (S.cat === "android") { await Panels.android.load(); refreshPanel(); } }, 20000); });
async function pollAndroid() {
  for (let i = 0; i < 600; i++) {
    await sleep(2000);
    if (S.cat !== "android" || S.view !== "settings") return;
    await Panels.android.load(); refreshPanel();
    if (S.panelData.android.state !== "running") return;
  }
}

/* ---- Streaming-Empfang */
Panels.cast = {
  lead: () => esc(t("cast_lead")),
  render() {
    const n = esc(S.settings.device_name);
    let h = item({ act: "toggle", icon: "cast", title: "AirPlay", sub: t("airplay_sub", { name: n }), toggle: S.settings.airplay, data: { k: "airplay" } });
    h += item({ act: "toggle", icon: "music", title: "Spotify Connect", sub: t("spotify_sub", { name: n }), toggle: S.settings.spotify_connect, data: { k: "spotify_connect" } });
    if (S.settings.spotify_connect) {
      const mode = S.settings.spotify_mode === "app" ? "app" : "librespot";
      h += item({ act: "spotify-mode", icon: "music", title: t("spotify_mode"), sub: t(`spotify_mode_${mode}_sub`), val: esc(t(`spotify_mode_${mode}`)) });
      if (mode === "app") {
        h += item({ act: "launch", icon: "key", title: t("spotify_login"), sub: t("spotify_login_sub"), data: { id: "spotify_desktop" } });
      }
    }
    h += callout(t("cast_hint", { name: n }));
    return h;
  },
};

act("spotify-mode", () => choose({ title: t("spotify_mode"), key: "spotify_mode", current: S.settings.spotify_mode === "app" ? "app" : "librespot",
  options: ["librespot", "app"].map((v) => ({ label: t(`spotify_mode_${v}`), sub: t(`spotify_mode_${v}_sub`), value: v })) }));

/* ---- Design */
Panels.design = {
  lead: () => esc(t("design_lead")),
  render: () => themeItems(),
};

/* ---- Bildschirmschoner */
Panels.ambient = {
  lead: () => esc(t("ambient_lead")),
  render() {
    const a = S.settings.ambient;
    let h = item({ act: "toggle", icon: "image", title: t("ambient_on"), toggle: a.enabled, data: { k: "ambient.enabled" } });
    h += item({ act: "amb-min", icon: "clock", title: t("ambient_after"), val: t("minutes", { n: a.minutes }) });
    h += item({ act: "amb-style", icon: "sparkle", title: t("ambient_style"), sub: t(`amb_style_${a.style || "classic"}_sub`), val: esc(t(`amb_style_${a.style || "classic"}`)) });
    h += item({ act: "amb-src", icon: "image", title: t("ambient_source"), val: esc(t(`amb_${a.source}`)) });
    h += item({ act: "amb-int", icon: "clock", title: t("ambient_interval"), val: t("seconds", { n: a.interval || 15 }) });
    if (a.source === "folder") h += item({ icon: "usb", title: t("amb_folder"), sub: esc(a.folder) });
    h += item({ act: "toggle", icon: "cloudsun", title: t("ambient_weather"), toggle: a.show_weather, data: { k: "ambient.show_weather" } });
    h += item({ act: "amb-off", icon: "moon", title: t("ambient_off"), sub: t("ambient_off_sub"), val: a.display_off_hours ? t("hours", { n: a.display_off_hours }) : t("never") });
    h += item({ act: "amb-now", icon: "eye", title: t("ambient_preview") });
    return h;
  },
};
act("amb-min", () => choose({ title: t("ambient_after"), key: "ambient.minutes", current: S.settings.ambient.minutes, options: [1, 2, 5, 10, 15, 30].map((n) => ({ label: t("minutes", { n }), value: n })) }));
act("amb-style", () => choose({ title: t("ambient_style"), key: "ambient.style", current: S.settings.ambient.style || "classic",
  options: ["classic", "center", "info", "night"].map((v) => ({ label: t(`amb_style_${v}`), sub: t(`amb_style_${v}_sub`), value: v })) }));
act("amb-int", () => choose({ title: t("ambient_interval"), key: "ambient.interval", current: S.settings.ambient.interval || 15,
  options: [8, 15, 30, 60, 120].map((n) => ({ label: t("seconds", { n }), value: n })) }));
act("amb-src", () => choose({ title: t("ambient_source"), key: "ambient.source", current: S.settings.ambient.source,
  options: ["art", "picsum", "folder", "handy"].map((v) => ({ label: t(`amb_${v}`), sub: t(`amb_${v}_sub`), value: v })) }));
act("amb-off", () => choose({ title: t("ambient_off"), key: "ambient.display_off_hours", current: S.settings.ambient.display_off_hours,
  options: [0, 1, 2, 3, 4, 6].map((n) => ({ label: n ? t("hours", { n }) : t("never"), value: n })) }));
act("amb-now", () => startAmbient(true));

/* ---- Updates */
Panels.updates = {
  async load() { S.updates = await api("/api/updates"); },
  lead: () => esc(t("updates_lead")),
  render() {
    const u = S.updates || {};
    const busy = u.state === "running" || u.state === "checking";
    let h = "";
    if (busy) {
      h += `<div class="bigprogress"><div class="pct">${u.state === "checking" ? esc(t("checking")) : `${u.progress}%`}</div><div class="meter"><i style="width:${u.progress}%"></i></div><div class="msg">${esc(u.message)}</div></div>`;
    } else if (u.state === "error") {
      h += callout(t("update_error", { m: esc(u.message) }), true);
    }
    if (u.reboot_required && !busy) h += callout(t("reboot_required"), true) + item({ act: "do-reboot", icon: "refresh", title: t("reboot_now") });
    h += item({ icon: u.total ? "download" : "check", title: u.total ? t("updates_available", { n: u.total }) : t("up_to_date"), sub: t("last_check", { t: fmtTime(u.last_check) }) });
    if (u.total && u.packages && u.packages.length) {
      const av = u.available || {};
      h += `<div class="info-grid">${[["apt", t("upd_system")], ["flatpak", t("upd_apps")], ["android", "Android"], ["wangerttv", "WangertTV"]].filter(([k]) => av[k]).map(([k, l]) => `<div class="info-card"><div class="k">${esc(l)}</div><div class="v">${av[k]}</div></div>`).join("")}</div>`;
      h += `<p class="lead" style="font-size:19px">${esc(u.packages.slice(0, 12).join(", "))}${u.packages.length > 12 ? " …" : ""}</p>`;
    }
    h += item({ act: "upd-run", icon: "download", title: t("update_all"), sub: t("update_all_sub"), cls: busy ? "disabled" : "" });
    h += item({ act: "upd-check", icon: "refresh", title: t("check_updates") });
    h += item({ act: "toggle", icon: "clock", title: t("autoupdate"), sub: t("autoupdate_sub"), toggle: S.settings.autoupdate.enabled, data: { k: "autoupdate.enabled" } });
    const repo = S.settings.update_repo || u.repo || "";
    h += item({ act: "upd-repo", icon: "server", title: t("upd_repo"), sub: repo ? t("upd_repo_set", { r: esc(repo) }) : t("upd_repo_sub"), val: repo ? "" : t("not_set") });
    if (u.last_update) h += item({ icon: "check", title: t("last_update"), sub: fmtTime(u.last_update) });
    return h;
  },
};
act("upd-repo", () => editText({ title: t("upd_repo"), key: "update_repo", placeholder: "benutzer/wangerttv",
  after: async (v) => { if (v) { await api("/api/updates/check", {}); toast(t("checking"), "refresh"); } } }));
act("upd-check", async () => { await api("/api/updates/check", {}); toast(t("checking"), "refresh"); });
act("upd-run", async () => { if (S.updates && (S.updates.state === "running" || S.updates.state === "checking")) return; await api("/api/updates/run", {}); toast(t("update_started"), "download"); });
act("do-reboot", () => confirmBox(t("reboot_q"), "", t("reboot"), () => api("/api/power", { action: "reboot" })));

/* ---- Gerät */
Panels.device = {
  async load() {
    if (!S.panelData.zones) S.panelData.zones = (await api("/api/timezones")).zones || [];
    S.panelData.ssh = await api("/api/ssh");
  },
  lead: () => esc(t("device_lead")),
  render() {
    const s = S.settings;
    let h = item({ act: "dev-name", icon: "device", title: t("device_name"), sub: t("device_name_sub"), val: esc(s.device_name) });
    h += item({ act: "lang", icon: "language", title: t("language"), val: s.lang === "en" ? "English" : "Deutsch" });
    h += item({ act: "tz", icon: "clock", title: t("timezone"), val: esc(s.timezone) });
    h += item({ act: "city", icon: "pin", title: t("weather_city"), sub: t("weather_city_sub"), val: esc(s.city || t("not_set")) });
    h += section(t("web_admin"));
    const wa = s.web_admin || {};
    h += item({ act: "web-admin", icon: "globe", title: t("web_admin"), sub: t("web_admin_sub"), val: wa.enabled === false ? t("off") : "" });
    const ssh = S.panelData.ssh || {};
    h += item({ act: "ssh", icon: "server", title: t("ssh"), sub: t("ssh_sub"), val: ssh.enabled ? t("on") : t("off") });
    h += section(t("sleep_timer"));
    h += item({ act: "sleep-action", icon: "moon", title: t("sleep_action"), val: esc(t(`sleep_${s.sleep_action}`)) });
    h += item({ act: "auto-standby", icon: "clock", title: t("auto_standby"), sub: t("auto_standby_sub"),
      val: s.auto_standby_hours ? t("hours_n", { n: s.auto_standby_hours }) : t("off") });
    h += item({ act: "power-menu", icon: "clock", title: t("sleep_timer"), sub: S.sleepEnds ? t("sleep_active", { t: fmtDuration(S.sleepEnds - Date.now() / 1000) }) : t("sleep_sub") });
    h += boxItems();
    return h;
  },
};
act("web-admin", async () => {
  const r = await api("/api/webadmin");
  if (!r.url) { toast(t("action_failed"), "alert"); return; }
  const on = r.enabled !== false;
  dialog({ title: t("web_admin"), wide: true,
    text: `<span class="qr-box" style="display:flex;gap:36px;align-items:center"><span class="qr" style="flex:0 0 260px">${r.qr || ""}</span>
      <span style="display:block"><span style="display:block;font-size:21px;line-height:1.45">${esc(t("web_admin_howto"))}</span>
      <span style="display:block;margin-top:16px;font-size:26px;font-weight:600">${esc(r.url)}</span>
      <span style="display:block;margin-top:10px;font-size:21px">${esc(t("web_admin_pin"))}: <b style="font-size:34px;letter-spacing:6px">${esc(r.pin)}</b></span>
      ${on ? "" : `<span style="display:block;margin-top:10px;color:var(--warn,#ffb84d)">${esc(t("web_admin_disabled"))}</span>`}</span></span>`,
    buttons: [{ label: t("done"), primary: true, focus: true },
      { label: t("web_admin_newpin"), action: async () => { await api("/api/webadmin/renew", {}); toast(t("web_admin_pin_renewed")); } },
      { label: on ? t("web_admin_off") : t("web_admin_on"), action: async () => { await saveSettings({ web_admin: { enabled: !on } }); refreshPanel(); } }] });
});
function sshDialog(r) {
  const on = !!r.enabled;
  const body = on
    ? `<span style="display:block;font-size:21px;line-height:1.5">${esc(t("ssh_howto"))}</span>
       <span style="display:block;margin-top:16px;font-size:30px;font-weight:600" class="mono">${esc(r.command)}</span>
       <span style="display:block;margin-top:12px;font-size:21px">${esc(t("ssh_password"))}: <b class="mono" style="font-size:34px;letter-spacing:2px">${esc(r.password)}</b></span>
       <span style="display:block;margin-top:14px;font-size:18px;opacity:.75">${esc(t("ssh_note"))}</span>`
    : `<span style="display:block;font-size:21px;line-height:1.5">${esc(t("ssh_off_text"))}</span>`;
  dialog({ title: t("ssh"), wide: true, text: body,
    buttons: on
      ? [{ label: t("done"), primary: true, focus: true },
         { label: t("ssh_renew"), action: async () => { const x = await api("/api/ssh", { action: "renew" }); sshDialog(x); } },
         { label: t("web_admin_off"), action: async () => { await api("/api/ssh", { action: "off" }); toast(t("ssh_disabled")); await Panels.device.load(); refreshPanel(); } }]
      : [{ label: t("web_admin_on"), primary: true, focus: true, action: async () => {
          toast(t("ssh_enabling")); const x = await api("/api/ssh", { action: "on" });
          await Panels.device.load(); refreshPanel(); if (x.enabled) sshDialog(x); else toast(t("action_failed"), "alert"); } },
         { label: t("cancel") }] });
}
act("ssh", async () => sshDialog(await api("/api/ssh")));
act("dev-name", () => editText({ title: t("device_name"), key: "device_name", placeholder: "WangertTV Box" }));
act("lang", () => choose({ title: t("language"), key: "lang", current: S.settings.lang, options: [{ label: "Deutsch", value: "de" }, { label: "English", value: "en" }],
  after: async () => { await loadApps(); openSettings("device"); } }));
act("tz", () => choose({ title: t("timezone"), key: "timezone", current: S.settings.timezone, options: S.panelData.zones.map((z) => ({ label: z.replace(/_/g, " "), value: z })) }));
act("city", () => editText({ title: t("weather_city"), key: "city", placeholder: t("city_ph"), after: async () => { await loadWeather(); } }));
act("auto-standby", () => choose({ title: t("auto_standby"), key: "auto_standby_hours", current: S.settings.auto_standby_hours || 0,
  options: [0, 2, 3, 4, 6, 8].map((v) => ({ label: v ? t("hours_n", { n: v }) : t("off"), value: v })) }));
act("sleep-action", () => choose({ title: t("sleep_action"), key: "sleep_action", current: S.settings.sleep_action,
  options: ["standby", "poweroff"].map((v) => ({ label: t(`sleep_${v}`), value: v })) }));

/* ---- System */
Panels.system = {
  async load() {
    const [sys, health] = await Promise.all([api("/api/sysinfo"), api("/api/health")]);
    S.panelData.sys = sys; S.panelData.health = health || {};
  },
  lead: () => esc(t("system_lead")),
  render() {
    const i = S.panelData.sys || {};
    const cards = [
      ["WangertTV", i.version], ["Debian", i.debian], [t("processor"), i.cpu], [t("graphics"), i.gpu],
      [t("memory"), `${fmtBytes(i.mem_used)} / ${fmtBytes(i.mem_total)}`], [t("storage"), `${fmtBytes(i.disk_used)} / ${fmtBytes(i.disk_total)}`],
      [t("ip_address"), i.ip || "—"], [t("network_name"), i.hostname], ["Kodi", (i.kodi || "—").replace(/^\d+:/, "").split("+")[0]],
      ["Chrome", (i.chrome || "—").split("-")[0]], ["Kernel", i.kernel], [t("uptime"), fmtDuration(i.uptime || 0)],
    ];
    const hl = S.panelData.health || {};
    if (hl.temp != null) cards.push([t("cpu_temp"), `${hl.temp} °C`]);
    if (hl.load != null) cards.push([t("cpu_load"), `${hl.load} %`]);
    if (hl.swap_total) cards.push([hl.zram ? t("zram_swap") : t("swap"), `${fmtBytes(hl.swap_used)} / ${fmtBytes(hl.swap_total)}`]);
    let h = `<div class="info-grid">${cards.map(([k, v]) => `<div class="info-card"><div class="k">${esc(k)}</div><div class="v">${esc(v || "—")}</div></div>`).join("")}</div>`;
    if (S.state && S.state.live) {
      h += section(t("installation"));
      h += callout(t("live_hint"));
      h += item({ act: "installer", icon: "hdd", title: t("install_system"), sub: t("install_system_sub") });
    }
    h += section(t("maintenance"));
    h += item({ act: "cleanup", icon: "trash", title: t("cleanup"), sub: t("cleanup_sub") });
    h += item({ act: "toggle", icon: "clock", title: t("maintenance_reboot"), sub: t("maintenance_reboot_sub"), toggle: !!S.settings.maintenance_reboot, data: { k: "maintenance_reboot" } });
    h += section(t("power_title"));
    h += item({ act: "power-menu", icon: "moon", title: t("standby"), sub: t("standby_sub") });
    h += item({ act: "sys-reboot", icon: "refresh", title: t("reboot") });
    h += item({ act: "sys-poweroff", icon: "power", title: t("poweroff") });
    h += section(t("reset"));
    h += item({ act: "factory-reset", icon: "trash", title: t("factory_reset"), sub: t("factory_reset_sub"), cls: "danger" });
    return h;
  },
};
act("cleanup", async () => {
  const p = await api("/api/cleanup");
  if (!p || !p.bytes) { toast(t("cleanup_nothing"), "check"); return; }
  confirmBox(t("cleanup"), t("cleanup_q", { size: fmtBytes(p.bytes) }), t("cleanup_go"), async () => {
    const r = await api("/api/cleanup", {});
    toast(t("cleanup_done", { size: fmtBytes((r && r.freed) || 0) }), "check");
    await Panels.system.load(); refreshPanel();
  });
});
act("sys-reboot", () => confirmBox(t("reboot_q"), "", t("reboot"), () => api("/api/power", { action: "reboot" })));
act("sys-poweroff", () => confirmBox(t("poweroff_q"), "", t("poweroff"), () => api("/api/power", { action: "poweroff" }), true));
act("factory-reset", () => confirmBox(t("factory_reset"), t("factory_reset_q"), t("continue"), () =>
  confirmBox(t("factory_reset_sure"), t("factory_reset_q2"), t("factory_reset"), () => api("/api/reset", {}), true), true));
act("installer", () => openInstaller());

/* ====================================================================== Installation auf die SSD */
function openInstaller() {
  S.view = "installer"; S.installStep = "disk"; S.installRunning = false;
  renderInstaller();
}
async function renderInstaller() {
  Nav.popAll();
  let left, right;
  if (S.installStep === "disk") {
    const disks = (await api("/api/disks")).disks || [];
    left = { ic: "hdd", title: t("inst_title"), text: t("inst_text") };
    right = callout(t("inst_warning"), true) + (disks.length ? disks.map((d) => item({ act: "inst-disk", icon: d.tran === "usb" ? "usb" : "hdd",
      title: esc(d.model || d.name), sub: `${esc(d.path)} · ${fmtBytes(d.size)}${d.tran ? ` · ${d.tran.toUpperCase()}` : ""}${d.too_small ? ` · ${t("too_small")}` : ""}`, data: { v: d.path, small: d.too_small ? 1 : 0, name: d.model || d.name } })).join("")
      : callout(t("inst_no_disk"), true)) + `<div class="wz-footer"><button class="btn f" data-act="inst-cancel">${icon("back")}${esc(t("back"))}</button></div>`;
  } else if (S.installStep === "progress") {
    const st = await api("/api/install");
    left = { ic: "download", title: t("inst_running"), text: t("inst_running_text") };
    right = `<div class="bigprogress"><div class="pct">${st.progress || 0}%</div><div class="meter"><i style="width:${st.progress || 0}%"></i></div><div class="msg">${esc(t(`inst_${st.message}`) !== `inst_${st.message}` ? t(`inst_${st.message}`) : st.message || "")}</div></div><button class="btn f hidden" data-act="noop"></button>`;
    if (st.state === "done") { S.installStep = "done"; S.installRunning = false; return renderInstaller(); }
    if (st.state === "error") { S.installStep = "error"; S.installError = st.message; S.installRunning = false; return renderInstaller(); }
    setTimeout(() => { if (S.view === "installer" && S.installStep === "progress") renderInstaller(); }, 1500);
  } else if (S.installStep === "done") {
    left = { ic: "check", title: t("inst_done"), text: t("inst_done_text") };
    right = callout(t("inst_remove_usb")) + `<div class="wz-footer"><button class="btn primary f" data-act="inst-reboot" data-autofocus="1">${icon("refresh")}${esc(t("reboot_now"))}</button></div>`;
  } else {
    left = { ic: "alert", title: t("inst_failed"), text: t("inst_failed_text") };
    right = callout(esc(S.installError || ""), true) + `<div class="wz-footer"><button class="btn f" data-act="inst-cancel">${icon("back")}${esc(t("back"))}</button></div>`;
  }
  $("#app").innerHTML = `<div class="wizard"><div class="wz-left"><div class="wz-icon">${icon(left.ic)}</div><h1>${esc(left.title)}</h1><p>${esc(left.text)}</p></div>
    <div class="wz-right"><div class="wz-right-inner">${right}</div></div></div>`;
  if (S.installStep !== "progress" || !Nav.valid()) Nav.first();
}
act("inst-disk", (d) => {
  if (d.small === "1") { toast(t("too_small"), "alert"); return; }
  confirmBox(t("inst_confirm_title"), t("inst_confirm", { disk: esc(d.name), path: esc(d.v) }), t("inst_erase"), () =>
    confirmBox(t("inst_confirm2_title"), t("inst_confirm2"), t("install"), async () => {
      const r = await api("/api/install", { disk: d.v });
      if (!r.ok) { toast(t("inst_failed"), "alert"); return; }
      S.installStep = "progress"; S.installRunning = true; renderInstaller();
    }, true), true);
});
act("inst-cancel", () => { S.installMode = false; if (S.settings.setup_done) openSettings("system"); else { S.wz = 0; renderWizard(); } });
act("inst-reboot", () => api("/api/power", { action: "reboot" }));

/* ====================================================================== Einrichtungsassistent */
const WZ = {};
function wizardSteps() {
  const steps = ["welcome"];
  if (S.state && S.state.live) steps.push("mode");
  steps.push("network", "remote", "region", "display", "apps", "livetv", "android", "cast", "done");
  return steps;
}
async function renderWizard() {
  S.view = "wizard";
  Nav.popAll();
  const steps = wizardSteps();
  S.wz = clamp(S.wz, 0, steps.length - 1);
  const id = steps[S.wz];
  const step = WZ[id];
  if (step.load) await step.load();
  if (S.view !== "wizard" || wizardSteps()[S.wz] !== id) return;
  const left = step.left();
  $("#app").innerHTML = `<div class="wizard">
    <div class="wz-left">
      <div class="wz-steps">${steps.map((_, i) => `<i class="${i < S.wz ? "done" : i === S.wz ? "cur" : ""}"></i>`).join("")}</div>
      <div class="wz-icon${left.logo ? " logo" : ""}">${left.logo ? '<svg><use href="#logo-mark"/></svg>' : icon(left.ic)}</div>
      <h1>${left.title}</h1><p>${left.text}</p>
      ${S.wz > 0 ? `<div class="hint">${icon("back")}${esc(t("wz_back_hint"))}</div>` : ""}
    </div>
    <div class="wz-right" id="wz-right"><div class="wz-right-inner">${step.right()}</div></div></div>`;
  Nav.first();
}
function wzRefresh() {
  const r = $("#wz-right .wz-right-inner"); if (!r) return;
  const id = wizardSteps()[S.wz];
  const keep = Nav.current ? [Nav.current.dataset.act, Nav.current.dataset.v || ""] : null;
  r.innerHTML = WZ[id].right();
  const again = keep && $$(".f", r).find((e) => e.dataset.act === keep[0] && (e.dataset.v || "") === keep[1]);
  Nav.focus(again || $(".f", r) || null);
}
function wzNext() { S.wz++; renderWizard(); }
const nextBtn = (label, extra = "") => `<div class="wz-footer"><button class="btn primary f" data-act="wz-next" ${extra}>${esc(label || t("next"))}${icon("right")}</button></div>`;
act("wz-next", () => wzNext());
act("wz-skip", () => wzNext());

WZ.welcome = {
  left: () => ({ logo: true, title: t("wz_welcome_title"), text: esc(t("wz_welcome_text")) }),
  right: () => section(t("language")) +
    item({ act: "wz-lang", icon: "language", title: "Deutsch", sub: "Deutschland, Österreich, Schweiz", val: S.settings.lang === "de" ? icon("check") : "", chev: false, data: { v: "de" } }) +
    item({ act: "wz-lang", icon: "language", title: "English", sub: "United Kingdom, United States", val: S.settings.lang === "en" ? icon("check") : "", chev: false, data: { v: "en" } }) +
    nextBtn(t("wz_start")),
};
act("wz-lang", async (d) => { await saveSettings({ lang: d.v }); renderWizard(); });

WZ.mode = {
  left: () => ({ ic: "hdd", title: t("wz_mode_title"), text: esc(t("wz_mode_text")) }),
  right: () => item({ act: "wz-install", icon: "hdd", title: t("install_system"), sub: t("wz_install_sub") }) +
    item({ act: "wz-next", icon: "play", title: t("wz_try"), sub: t("wz_try_sub") }) + callout(t("live_hint")),
};
act("wz-install", () => openInstaller());

WZ.network = {
  async load() { S.panelData.net = await api("/api/network"); if (!S.panelData.wifi) S.panelData.wifi = (await api("/api/wifi/scan")).networks || []; },
  left: () => ({ ic: "wifi", title: t("wz_net_title"), text: esc(t("wz_net_text")) }),
  right() {
    const n = S.panelData.net || {};
    let h = "";
    if (n.ethernet && n.ethernet.connected) h += item({ icon: "ethernet", title: t("ethernet"), sub: t("connected_ip", { ip: n.ethernet.ip }), val: icon("check") });
    if (n.wifi) {
      for (const net of (S.panelData.wifi || []).slice(0, 8)) {
        h += item({ act: "wz-wifi", icon: net.secure ? "lock" : "wifi", title: esc(net.ssid), sub: net.active ? t("connected") : "", val: signalBars(net.signal), data: { v: net.ssid, secure: net.secure ? 1 : 0, active: net.active ? 1 : 0 } });
      }
      h += item({ act: "wz-wifi-rescan", icon: "refresh", title: t("rescan") });
    }
    h += n.online ? nextBtn() : `<div class="wz-footer"><button class="btn f" data-act="wz-skip">${esc(t("skip"))}</button></div>`;
    return h;
  },
};
act("wz-wifi", (d) => d.active === "1" ? null : connectWifi(d.v, d.secure === "1", false, async () => { S.panelData.wifi = null; await WZ.network.load(); wzRefresh(); }));
act("wz-wifi-rescan", async () => { S.panelData.wifi = null; await WZ.network.load(); wzRefresh(); });

WZ.remote = {
  async load() { S.panelData.bt = (await api("/api/bt")).devices || []; S.panelData.remote = await api("/api/remote"); },
  left: () => ({ ic: "remote", title: t("wz_remote_title"), text: esc(t("wz_remote_text")) }),
  right() {
    let h = callout(t("remote_pair_hint"));
    for (const dev of S.panelData.bt || []) {
      h += item({ act: "bt-dev-wz", icon: "bluetooth", title: esc(dev.name), sub: dev.connected ? t("connected") : dev.paired ? t("paired") : t("tap_to_pair"), data: { v: dev.mac, paired: dev.paired ? 1 : 0, name: dev.name } });
    }
    h += item({ act: "bt-scan-wz", icon: "refresh", title: t("bt_search"), sub: t("bt_search_sub") });
    const cec = (S.panelData.remote || {}).cec || {};
    h += cec.adapter ? item({ icon: "check", title: t("cec_found"), sub: t("cec_ready") }) : callout(t("cec_missing"), true);
    return h + nextBtn();
  },
};
act("bt-scan-wz", async () => { await api("/api/bt/scan", {}); toast(t("bt_searching"), "bluetooth");
  for (let i = 0; i < 6; i++) { await sleep(3500); if (S.view !== "wizard") return; await WZ.remote.load(); wzRefresh(); } });
act("bt-dev-wz", (d) => {
  if (d.paired === "1") return;
  toast(t("pairing", { name: d.name }), "bluetooth");
  api("/api/bt/pair", { mac: d.v }).then(async (r) => { toast(r.ok ? t("paired_ok", { name: d.name }) : t("pair_failed"), r.ok ? "check" : "alert"); await WZ.remote.load(); wzRefresh(); });
});

WZ.region = {
  async load() { if (!S.panelData.zones) S.panelData.zones = (await api("/api/timezones")).zones || []; },
  left: () => ({ ic: "device", title: t("wz_region_title"), text: esc(t("wz_region_text")) }),
  right: () => item({ act: "dev-name-wz", icon: "device", title: t("device_name"), sub: t("device_name_sub"), val: esc(S.settings.device_name) }) +
    item({ act: "tz-wz", icon: "clock", title: t("timezone"), val: esc(S.settings.timezone) }) +
    item({ act: "city-wz", icon: "pin", title: t("weather_city"), sub: t("weather_city_sub"), val: esc(S.settings.city || t("not_set")) }) + nextBtn(),
};
act("dev-name-wz", () => OSK.start({ title: t("device_name"), value: S.settings.device_name, onDone: async (v) => { if (v.trim()) await saveSettings({ device_name: v.trim() }); wzRefresh(); } }));
act("tz-wz", () => dialog({ title: t("timezone"), items: S.panelData.zones.map((z) => ({ label: z.replace(/_/g, " "), value: z, selected: z === S.settings.timezone })),
  onPick: async (z) => { await saveSettings({ timezone: z }); wzRefresh(); } }));
act("city-wz", () => OSK.start({ title: t("weather_city"), value: S.settings.city, placeholder: t("city_ph"), onDone: async (v) => { await saveSettings({ city: v.trim() }); loadWeather(); wzRefresh(); } }));

WZ.display = {
  async load() { await Panels.display.load(); },
  left: () => ({ ic: "display", title: t("wz_display_title"), text: esc(t("wz_display_text")) }),
  right() {
    const out = ((S.panelData.disp || {}).outputs || [])[0];
    const au = S.panelData.audio || { sinks: [] };
    const sink = au.sinks.find((s) => s.name === au.default);
    return item({ act: "res", icon: "display", title: t("resolution"), val: out ? `${out.current.width}×${out.current.height} · ${out.current.refresh} Hz` : "—" }) +
      item({ act: "sink", icon: "sound", title: t("audio_out"), val: esc(sink ? sink.label : "—") }) +
      item({ act: "audio-test", icon: "music", title: t("audio_test"), sub: t("audio_test_sub") }) +
      item({ act: "speakers", icon: "sound", title: t("speakers"), sub: t("speakers_sub"), val: esc(t(`sp_${(S.settings.audio.speakers || "2.0").replace(".", "")}`)) }) +
      item({ act: "toggle", icon: "sparkle", title: t("passthrough"), sub: t("passthrough_sub"), toggle: S.settings.kodi.passthrough, data: { k: "kodi.passthrough" } }) + nextBtn();
  },
};

WZ.apps = {
  async load() { await loadApps(); },
  left: () => ({ ic: "apps", title: t("wz_apps_title"), text: esc(t("wz_apps_text")) }),
  right() {
    const sel = new Set(S.settings.apps);
    return S.apps.all.filter((a) => a.id !== "settings").map((a) => item({ act: "wz-app", icon: a.glyph, title: esc(a.name), sub: esc(L(a.desc)), toggle: sel.has(a.id), data: { v: a.id } })).join("") + nextBtn();
  },
};
act("wz-app", async (d) => {
  const apps = [...S.settings.apps]; const i = apps.indexOf(d.v);
  if (i >= 0) apps.splice(i, 1); else apps.push(d.v);
  await saveSettings({ apps }); await loadApps();
  if (S.view === "wizard") wzRefresh(); else refreshPanel();
});

WZ.livetv = {
  left: () => ({ ic: "tv", title: t("wz_live_title"), text: esc(t("wz_live_text")) }),
  right() {
    const iptv = S.settings.iptv; const res = S.panelData.iptvTest;
    const w = S.settings.waipu || {};
    let h = waipuItems(true) + section(t("iptv_section")) + callout(t("iptv_hint"));
    h += item({ act: "m3u-wz", icon: "tv", title: t("m3u_url"), sub: iptv.m3u ? esc(iptv.m3u) : t("not_set") });
    h += item({ act: "epg-wz", icon: "guide", title: t("epg_url"), sub: iptv.epg ? esc(iptv.epg) : t("epg_optional") });
    if (res) h += res.ok ? item({ icon: "check", title: t("iptv_found", { n: res.count }), sub: esc(res.sample.join(", ")) }) : callout(t("iptv_fail", { m: esc(res.message || "") }), true);
    return h + (iptv.m3u || (w.username && w.password) ? nextBtn() : `<div class="wz-footer"><button class="btn f" data-act="wz-skip">${esc(t("skip_later"))}</button></div>`);
  },
};
act("m3u-wz", () => OSK.start({ title: t("m3u_url"), value: S.settings.iptv.m3u, placeholder: "https://…/playlist.m3u", onDone: async (v) => {
  await saveSettings({ iptv: { m3u: v.trim() } });
  S.panelData.iptvTest = v.trim() ? await api("/api/iptv/test", { url: v.trim() }) : null; wzRefresh();
} }));
act("epg-wz", () => OSK.start({ title: t("epg_url"), value: S.settings.iptv.epg, placeholder: "https://…/epg.xml.gz", onDone: async (v) => { await saveSettings({ iptv: { epg: v.trim() } }); wzRefresh(); } }));

WZ.android = {
  async load() { S.panelData.android = await api("/api/android"); },
  left: () => ({ ic: "store", title: t("wz_android_title"), text: esc(t("wz_android_text")) }),
  right() {
    const a = S.panelData.android || {};
    if (!a.installed) return callout(t("android_missing"), true) + nextBtn();
    const wanted = S.settings.android.wanted;
    return callout(t("android_info")) +
      item({ act: "wz-android", icon: "download", title: t("wz_android_yes"), sub: t("wz_android_yes_sub"), toggle: wanted }) + nextBtn();
  },
};
act("wz-android", async () => { await saveSettings({ android: { wanted: !S.settings.android.wanted } }); wzRefresh(); });

WZ.cast = {
  left: () => ({ ic: "cast", title: t("wz_cast_title"), text: esc(t("wz_cast_text")) }),
  right: () => Panels.cast.render() + section(t("cat_design")) + themeItems() + section(t("cat_ambient")) +
    item({ act: "toggle", icon: "image", title: t("ambient_on"), sub: t("ambient_lead"), toggle: S.settings.ambient.enabled, data: { k: "ambient.enabled" } }) +
    item({ act: "toggle", icon: "download", title: t("autoupdate"), sub: t("autoupdate_sub"), toggle: S.settings.autoupdate.enabled, data: { k: "autoupdate.enabled" } }) + nextBtn(),
};

WZ.done = {
  left: () => ({ ic: "check", title: t("wz_done_title"), text: esc(t("wz_done_text", { name: S.settings.device_name })) }),
  right() {
    const s = S.settings;
    const rows = [
      ["device", t("device_name"), s.device_name], ["tv", t("cat_livetv"), s.iptv.m3u ? t("configured") : t("not_set")],
      ["apps", t("home_row"), t("n_apps", { n: s.apps.length })], ["store", "Play Store", s.android.wanted ? t("wz_android_bg") : t("later")],
      ["cast", "AirPlay / Spotify Connect", s.airplay || s.spotify_connect ? t("on") : t("off")],
    ];
    return rows.map(([ic, k, v]) => item({ icon: ic, title: esc(k), sub: esc(v) })).join("") +
      `<div class="wz-footer"><button class="btn primary f" data-act="wz-finish" data-autofocus="1">${esc(t("wz_go"))}${icon("right")}</button></div>`;
  },
};
act("wz-finish", async () => {
  await api("/api/setup/finish", {});
  await loadState(); await loadApps(); await loadChannels(); await loadWeather();
  S.tab = "foryou"; renderHome();
  toast(t("welcome_toast"), "sparkle");
});

/* ====================================================================== Bildschirmschoner (Ambient) */
let ambTimer = null, ambIdx = 0, ambPhotos = [];
function artGradient(i) {
  const hues = [[205, 250], [190, 280], [170, 220], [230, 320], [20, 200], [260, 200], [150, 240]];
  const [a, b] = hues[i % hues.length];
  const x = 20 + ((i * 37) % 60), y = 20 + ((i * 53) % 60);
  return `radial-gradient(1200px 900px at ${x}% ${y}%, hsl(${a} 85% 45% / .9), transparent 60%), radial-gradient(1000px 800px at ${100 - x}% ${100 - y}%, hsl(${b} 80% 35% / .85), transparent 65%), linear-gradient(135deg, hsl(${a} 60% 8%), hsl(${b} 60% 4%))`;
}
async function startAmbient(manual = false) {
  if (S.ambient || S.view === "wizard" || S.view === "installer") return;
  const cfg = S.settings.ambient;
  S.ambient = true; S.ambientSince = Date.now(); ambIdx = 0;
  if (cfg.source === "folder" || cfg.source === "handy") ambPhotos = (await api("/api/photos")).photos || [];
  const el = document.createElement("div");
  const style = ["classic", "center", "info", "night"].includes(cfg.style) ? cfg.style : "classic";
  el.className = `ambient ambs-${style}`;
  el.innerHTML = `<div class="amb-slide"></div><div class="amb-slide"></div><div class="amb-shade"></div><div class="amb-np"></div>
    <div class="amb-info"><div class="amb-time">${clockText()}</div><div class="amb-date"></div><div class="amb-days"></div></div><div class="amb-weather"></div>`;
  document.body.appendChild(el);
  const nextSlide = () => {
    const slides = $$(".amb-slide", el); const on = slides[ambIdx % 2]; const off = slides[(ambIdx + 1) % 2];
    let bg;
    if (style === "night") bg = "none";
    else if (cfg.source === "picsum") bg = `url("https://picsum.photos/seed/wtv${Date.now() % 100000}/1920/1080")`;
    else if ((cfg.source === "folder" || cfg.source === "handy") && ambPhotos.length) bg = `url("${ambPhotos[ambIdx % ambPhotos.length]}")`;
    else bg = artGradient(ambIdx);
    off.style.backgroundImage = bg; off.classList.add("on"); on.classList.remove("on");
    ambIdx++;
    $(".amb-date", el).textContent = new Date().toLocaleDateString(locale(), { weekday: "long", day: "numeric", month: "long" });
    const w = S.weather;
    $(".amb-weather", el).innerHTML = cfg.show_weather && w ? `${icon(weatherIcon(w.code, w.is_day))}<div>${w.temp}°<small>${esc(w.place)}</small></div>` : "";
    const np = S.nowplaying;
    $(".amb-np", el).innerHTML = np && np.title ? `${np.cover ? `<img src="${esc(np.cover)}" alt="">` : icon(np.source === "radio" ? "radio" : "music")}<div><small>${esc(t("radio_playing"))}</small><b>${esc(np.title)}</b><span>${esc(np.artist || "")}</span></div>` : "";
    // Vorhersage (Stil "Info") und Einbrennschutz: Uhr wandert langsam
    $(".amb-days", el).innerHTML = style === "info" && w && w.daily ? w.daily.slice(0, 3).map((dd) => `<span>${icon(weatherIcon(dd.code, 1))}<b>${esc(new Date(dd.date).toLocaleDateString(locale(), { weekday: "short" }))}</b>${dd.max}° / ${dd.min}°</span>`).join("") : "";
    const info = $(".amb-info", el);
    info.style.transform = style === "night" || style === "center"
      ? `translate(${((ambIdx * 137) % 240) - 120}px, ${((ambIdx * 89) % 160) - 80}px)` : `translate(${(ambIdx * 23) % 40}px, ${-((ambIdx * 17) % 30)}px)`;
    // Bildschirm nach eingestellter Zeit ganz ausschalten (Standby)
    const offH = cfg.display_off_hours;
    if (offH && Date.now() - S.ambientSince > offH * 3600 * 1000 && !manual) api("/api/power", { action: "standby", source: "ambient" });
  };
  nextSlide();
  ambTimer = setInterval(nextSlide, Math.max(8, +cfg.interval || 15) * 1000);
}
function stopAmbient() {
  S.ambient = false; clearInterval(ambTimer);
  const el = $(".ambient"); if (el) { el.style.transition = "opacity .4s"; el.style.opacity = "0"; setTimeout(() => el.remove(), 400); }
  S.lastKey = Date.now();
}
setInterval(() => {
  const cfg = S.settings.ambient;
  if (!cfg || !cfg.enabled || S.ambient || S.standby || !S.homeFocused || OSK.open) return;
  if (S.view !== "home" && S.view !== "settings") return;
  if (Date.now() - S.lastKey > cfg.minutes * 60 * 1000) startAmbient();
}, 5000);

/* ====================================================================== Ereignisse vom Hintergrunddienst */
function connectEvents() {
  const es = new EventSource("/api/events");
  // Nach einem Update läuft der Hintergrunddienst in neuer Version – dann die Oberfläche neu laden
  es.onopen = async () => {
    try {
      const st = await api("/api/state");
      if (S.bootVersion && st.version && st.version !== S.bootVersion) { location.reload(); return; }
      S.bootVersion = S.bootVersion || st.version;
    } catch { /* Dienst startet noch */ }
  };
  es.onmessage = async (ev) => {
    let m; try { m = JSON.parse(ev.data); } catch { return; }
    switch (m.type) {
      case "running": case "apps":
        await loadApps();
        if (S.tab === "media" && !S.media.dir) await loadMedia();   // "Weiterschauen" nach dem Player aktualisieren
        if (S.view === "home" && !Nav.layers.length) refreshView(); else if (S.cat === "apps") refreshPanel();
        break;
      case "channels": await loadChannels(); if (S.view === "home" && !Nav.layers.length) refreshView(); break;
      case "usb":
        S.usb = m.drives || [];
        for (const name of m.added || []) toast(t("usb_added", { name }), "usb");
        if (S.tab === "media") {
          await loadMedia();
          // Stick abgezogen, während man darin stöbert: zurück zur Übersicht
          if (S.media.dir && !S.media.dir.path.startsWith("/home") && !S.usb.some((u) => S.media.dir.path.startsWith(u.mount))) S.media.dir = null;
        }
        if (S.view === "home" && !Nav.layers.length) { if ((m.added || []).length) S.heroIndex = 0; refreshView(); }
        break;
      case "home":
        if (S.ambient) stopAmbient();
        if (S.view === "home" || S.view === "settings") { OSK.close(false); goHome("foryou"); }
        break;
      case "open":
        if (!S.settings.setup_done) break;
        if (S.ambient) stopAmbient();
        if (m.target === "settings") openSettings();
        else if (m.target === "epg") openEpg();
        else if (m.target === "live") focusLive();
        else if (m.target === "radio") openRadio();
        else if (m.target === "tvplaner") openPlanner();
        else if (m.target === "files") openFiles();
        else if (m.target === "share") openShare();
        else if (m.target === "player") { S.media.dir = null; await loadMedia(); goHome("media"); }
        break;
      case "power_menu": if (S.settings.setup_done) { if (S.view !== "home") goHome(); openPowerMenu(); } break;
      case "standby": setStandby(m.on); break;
      case "focus": S.homeFocused = m.home; if (m.home) S.lastKey = Date.now(); else TvLoading.hide(); break;
      case "tv_loading": if (m.on) TvLoading.show(m.name); else TvLoading.hide(); break;
      case "update":
        S.updates = m.status;
        if (S.view === "settings" && S.cat === "updates" && !Nav.layers.length) refreshPanel();
        if (m.status.state === "done") toast(t("update_done"), "check");
        break;
      case "job":
        if (m.job.state === "done") toast(t("installed", { name: m.job.label }), "check");
        else if (m.job.state === "error") toast(t("install_failed", { name: m.job.label }), "alert");
        if (m.job.state !== "running") { await loadApps(); if (S.view === "home" && !Nav.layers.length) refreshView(); }
        break;
      case "nowplaying": S.nowplaying = m.nowplaying || {}; break;
      case "volume": if (!S.standby) showVolume(m); break;
      case "files_job": filesJobEvent(m.job || {}); break;
      case "reminder": showReminder(m); break;
      case "reminders": S.plan.reminders = m.reminders || []; break;
      case "share": toast(t("share_received", { name: m.name }), "share"); if (S.view === "share" && !Nav.layers.length) { S.share = await api("/api/share"); renderShare(); } break;
      case "radio":
        S.radio.state = { playing: m.playing, station: m.station, title: m.title, cover: m.cover };
        if (m.playing) S.heroIndex = 0;
        if (Nav.layers.length) break;
        if (S.view === "radio") renderRadio(); else if (S.view === "home") refreshView();
        break;
      case "sleeptimer": S.sleepEnds = m.ends || 0; break;
      case "phone_text": OSK.phone(m.token, m.text); break;
      case "toast": toast(t(m.key), m.icon || "info"); break;
      case "channel_key":
        if (S.view === "epg" && !Nav.layers.length) Epg.key(m.step > 0 ? "ArrowDown" : "ArrowUp");
        else { const name = nextChannel(m.step); if (name) playChannel(name); }
        break;
      case "watching": {
        // beim Umschalten in Kodi: "Zuletzt gesehen" hier mitführen (sonst überschreibt ein alter Stand die Liste)
        const p = chPrefs();
        S.settings.channel_prefs = { favorites: p.favorites || [], recent: [m.name, ...(p.recent || []).filter((n) => n !== m.name)].slice(0, 10) };
        clearTimeout(S._fyTimer); S._fyTimer = setTimeout(loadForYou, 8000); break;
      }
      default: break;
    }
  };
  es.onerror = () => { /* EventSource verbindet sich selbst neu */ };
}
function setStandby(on) {
  S.standby = !!on;
  const ex = $(".standby");
  if (on && !ex) { if (S.ambient) stopAmbient(); const el = document.createElement("div"); el.className = "standby"; document.body.appendChild(el); }
  if (!on && ex) { ex.remove(); S.lastKey = Date.now(); }
}

/* ====================================================================== Start */
/* Ende der Startanimation: das Logo fliegt an seinen Platz in der Kopfzeile */
function bootFinish() {
  const boot = $("#boot"), mark = $(".boot-mark", boot), target = $(".topbar .brand svg");
  if (mark && target) {
    const a = mark.getBoundingClientRect(), b = target.getBoundingClientRect();
    boot.style.setProperty("--fly-x", `${b.left + b.width / 2 - (a.left + a.width / 2)}px`);
    boot.style.setProperty("--fly-y", `${b.top + b.height / 2 - (a.top + a.height / 2)}px`);
    boot.style.setProperty("--fly-s", (b.width / a.width).toFixed(3));
  }
  boot.classList.add("done");
  setTimeout(() => boot.remove(), 1400);
}
async function init() {
  const started = Date.now();
  await loadState();
  for (let i = 0; !S.state && i < 30; i++) { await sleep(500); await loadState(); }
  document.documentElement.lang = S.settings.lang || "de";
  await Promise.all([loadApps(), loadChannels(), loadWeather(), (async () => { S.updates = await api("/api/updates"); })()]);
  if (S.settings.setup_done) {
    const a = await api("/api/android");
    if (a && a.initialized) S.androidApps = (await api("/api/android/apps")).apps || [];
  }
  await Promise.all([loadUsb(), loadRadio(), loadMedia(), loadPrime(), loadForYou()]);
  connectEvents();
  await sleep(Math.max(0, 1800 - (Date.now() - started)));   // Startanimation kurz zeigen
  if (S.settings.setup_done) renderHome(); else { S.wz = 0; renderWizard(); }
  bootFinish();
  if (S.standby) setStandby(true);
  setInterval(loadWeather, 20 * 60 * 1000);
  // TV-Programm: Fortschrittsbalken und "Jetzt läuft" jede Minute aktualisieren
  setInterval(async () => {
    if (S.view !== "home" || Nav.layers.length || S.standby || !S.channels.some((c) => c.now)) return;
    await Promise.all([loadChannels(), loadPrime()]); refreshView();
  }, 60 * 1000);
  setInterval(async () => { if (S.settings.setup_done && S.view === "home") { const a = await api("/api/android"); if (a && a.running) S.androidApps = (await api("/api/android/apps")).apps || []; } }, 60 * 1000);
}
init();
