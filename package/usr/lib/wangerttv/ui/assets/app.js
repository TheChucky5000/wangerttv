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

function t(key, vars) {
  const lang = (S.settings && S.settings.lang) || "de";
  let s = (I18N[lang] && I18N[lang][key]) ?? I18N.de[key] ?? key;
  if (vars) for (const k in vars) s = s.split(`{${k}}`).join(vars[k]);
  return s;
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
    const scroller = el.closest(".home-scroll, .set-body, .wz-right, .list, .set-nav");
    if (scroller) {
      const block = el.closest(".row") || el;
      const sr = scroller.getBoundingClientRect(), br = block.getBoundingClientRect();
      const topPad = scroller.classList.contains("home-scroll") ? 150 : 90;
      if (br.top < sr.top + topPad || br.bottom > sr.bottom - 60) {
        const delta = br.top - sr.top - (scroller.classList.contains("home-scroll") ? 120 : sr.height / 3);
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
  if (S.view === "settings") { goHome(S.tab, "#btn-settings"); return; }
  if (S.view === "installer") { if (!S.installRunning) { S.installMode = false; S.settings.setup_done ? openSettings("system") : renderWizard(); } return; }
  if (S.view === "wizard") { if (S.wz > 0) { S.wz--; renderWizard(); } return; }
  if (S.view === "home") {
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
  phone(token, text) { if (this.open && token === this.token) { this.value = text; this.close(true); } },
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
};
function applyTheme() {
  const th = THEMES[S.settings.theme] ? S.settings.theme : "wangert";
  document.documentElement.dataset.theme = th;
}
function themeItems() {
  return Object.entries(THEMES).map(([id, [a, b, c]]) => `
    <button class="item f" data-act="theme" data-v="${id}">
      <span class="theme-swatch" style="background:linear-gradient(120deg, ${a} 0%, ${b} 55%, ${c} 100%)"></span>
      <span class="txt"><div class="t">${esc(t(`theme_${id}`))}</div><div class="s">${esc(t(`theme_${id}_sub`))}</div></span>
      ${S.settings.theme === id ? `<span class="chev">${icon("check")}</span>` : ""}</button>`).join("");
}
act("theme", async (d) => { await saveSettings({ theme: d.v }); refreshPanel(); });

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
  if (!res.ok) {
    if (res.message === "not_installed" && app && app.type === "flatpak") {
      confirmBox(t("install_app_q", { name: app.name }), t("install_app_text"), t("install"), async () => {
        await api("/api/apps/install", { id }); toast(t("installing", { name: app.name }), "download");
      });
    } else toast(t("launch_failed", { name: app ? app.name : id }), "alert");
  }
}
act("launch", (d) => launchApp(d.id, d.view ? { view: d.view } : {}));
act("channel", (d) => launchApp("livetv", { channel: d.name, force: true }));
act("android-app", (d) => api("/api/android/launch", { package: d.pkg, name: d.name }));
act("running-close", async (d) => { await api("/api/apps/close", { key: d.key }); await loadApps(); refreshView(); });

/* Kontextmenü (Menü-Taste oder OK lange drücken) */
function openContext() {
  const el = Nav.current; if (!el || Nav.layers.length) return;
  if (el.dataset.act === "launch" && el.closest("[data-row='apps']")) {
    const id = el.dataset.id;
    const app = S.apps.home.find((a) => a.id === id);
    const order = [...S.settings.apps]; const idx = order.indexOf(id);
    const items = [{ label: t("open"), icon: "play", value: "open" }];
    if (idx > 0) items.push({ label: t("move_left"), icon: "left", value: "left" });
    if (idx >= 0 && idx < order.length - 1) items.push({ label: t("move_right"), icon: "right", value: "right" });
    if (app && app.running) items.push({ label: t("close_app"), icon: "stop", value: "close" });
    items.push({ label: t("remove_from_home"), icon: "trash", value: "remove", danger: true });
    dialog({
      title: app ? app.name : id, items, onPick: async (v) => {
        if (v === "open") launchApp(id);
        else if (v === "close") { await api("/api/apps/close", { key: ["livetv", "tvguide", "kodi", "mediathek"].includes(id) ? "kodi" : (app.type === "android" ? "android" : id) }); await loadApps(); renderHome(); }
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
async function loadChannels() {
  const c = await api("/api/channels");
  if (c && c.channels) { S.channels = c.channels; S.channelsConfigured = c.configured; }
}
async function loadWeather() {
  if (!S.settings.city) { S.weather = null; return; }
  const w = await api("/api/weather"); S.weather = w && w.ok ? w : null;
}

/* ====================================================================== Kopfzeile */
function clockText() { return new Date().toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit" }); }
function topbar() {
  const tabs = [["foryou", t("tab_foryou")], ["apps", t("tab_apps")], ["live", t("tab_live")]];
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
act("tab", (d) => { if (S.tab !== d.tab) { S.tab = d.tab; renderHome(); Nav.first(`.tab[data-tab='${d.tab}']`); } });
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
function channelTile(ch) {
  const initials = esc(ch.name.replace(/\b(HD|SD|UHD|FHD)\b/g, "").trim());
  return `<button class="tile channel f" data-act="channel" data-name="${esc(ch.name)}">
    <span class="num">${ch.number || ""}</span>
    <span class="logo">${ch.logo ? `<img src="${esc(ch.logo)}" alt="" onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'fallback',textContent:${JSON.stringify(ch.name)}}))">` : `<span class="fallback">${initials}</span>`}</span>
    ${ch.logo ? `<span class="cname">${esc(ch.name)}</span>` : ""}
  </button>`;
}

function heroSlides() {
  const slides = [];
  const np = S.nowplaying;
  if (np && np.source === "spotify" && np.title) {
    slides.push({ kicker: t("hero_np_kicker"), title: np.title, text: np.artist || "", cover: np.cover, glyph: "music",
      actions: [{ label: t("open_spotify"), act: "launch", data: { id: "spotify" }, icon: "music", primary: true }] });
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
function heroHtml() {
  const slides = heroSlides();
  S.heroIndex = S.heroIndex % slides.length;
  const s = slides[S.heroIndex];
  const btns = s.actions.map((a) => `<button class="btn f${a.primary ? " primary" : ""}" data-act="${a.act}" ${Object.entries(a.data).map(([k, v]) => `data-${k}="${esc(v)}"`).join(" ")}>${icon(a.icon)}${esc(a.label)}</button>`).join("");
  const art = s.cover ? `<img class="hero-art cover" src="${esc(s.cover)}" alt="">` : `<svg class="hero-art" viewBox="0 0 24 24" style="stroke:rgba(255,255,255,.18);stroke-width:1;fill:none">${ICONS[s.glyph]}</svg>`;
  return `<section class="hero" id="hero" data-row="hero">${art}<div><div class="hero-kicker">${esc(s.kicker)}</div><h1>${esc(s.title)}</h1><p>${esc(s.text)}</p><div class="hero-actions">${btns}</div></div>
    <div class="hero-dots">${slides.map((_, i) => `<i class="${i === S.heroIndex ? "on" : ""}"></i>`).join("")}</div></section>`;
}
act("settings-cat", (d) => openSettings(d.cat));

function homeForYou() {
  const apps = S.apps.home;
  let html = heroHtml();
  html += `<div class="row"><div class="row-title">${esc(t("your_apps"))}</div><div class="row-track" data-row="apps">
    ${apps.map((a) => appTile(a)).join("")}
    <button class="tile add f" data-act="add-app">${icon("plus")}<span class="name">${esc(t("add_app"))}</span></button></div></div>`;
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
  const list = S.channelGroup ? S.channels.filter((c) => c.group === S.channelGroup) : S.channels;
  return `<div style="height:20px"></div>
    <div class="chips" data-row="live-actions">
      <button class="btn small primary f" data-act="launch" data-id="livetv">${icon("tv")}${esc(t("open_livetv"))}</button>
      <button class="btn small f" data-act="launch" data-id="tvguide">${icon("guide")}${esc(t("tv_guide"))}</button>
      <button class="btn small f" data-act="refresh-channels">${icon("refresh")}${esc(t("refresh_list"))}</button>
    </div>
    ${groups.length > 1 ? `<div class="chips" style="margin-top:22px" data-row="groups"><button class="pill f${!S.channelGroup ? " active" : ""}" data-act="group" data-g="">${esc(t("all"))}</button>${groups.slice(0, 14).map((g) => `<button class="pill f${S.channelGroup === g ? " active" : ""}" data-act="group" data-g="${esc(g)}">${esc(g)}</button>`).join("")}</div>` : ""}
    <div class="grid">${list.slice(0, 400).map(channelTile).join("")}</div>`;
}
act("group", (d) => { S.channelGroup = d.g; renderHome(); Nav.first(`.pill[data-g="${CSS.escape(d.g)}"]`); });
act("refresh-channels", async () => {
  toast(t("loading_channels"), "refresh");
  await api("/api/channels/refresh", {});
  setTimeout(async () => { await loadChannels(); refreshView(); }, 3000);
});

function renderHome(focusSel) {
  S.view = "home";
  const body = S.tab === "apps" ? homeApps() : S.tab === "live" ? homeLive() : homeForYou();
  $("#app").innerHTML = `${topbar()}<div class="home-scroll"><div class="home-content">${body}</div></div>`;
  if (focusSel) Nav.first(focusSel);
  else if (!Nav.layers.length) Nav.first(S.tab === "foryou" ? "[data-row='apps'] .tile" : ".tab.active");
}
function goHome(tab, focusSel) {
  if (tab) S.tab = tab;
  Nav.popAll();
  renderHome(focusSel);
}

/* Hero-Karussell weiterschalten, wenn die Startseite sichtbar und das Hero nicht fokussiert ist */
setInterval(() => {
  if (S.view !== "home" || S.tab !== "foryou" || Nav.layers.length || S.ambient) return;
  const hero = $("#hero"); if (!hero || hero.contains(Nav.current)) return;
  S.heroIndex++;
  const tmp = document.createElement("div"); tmp.innerHTML = heroHtml();
  hero.replaceWith(tmp.firstElementChild);
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
  ["network", "wifi"], ["remote", "remote"], ["display", "display"], ["apps", "apps"], ["livetv", "tv"],
  ["android", "android"], ["cast", "cast"], ["design", "sparkle"], ["ambient", "image"], ["updates", "update"], ["device", "device"], ["system", "info"],
];
function openSettings(cat) {
  Nav.popAll();
  S.view = "settings";
  if (cat) S.cat = cat;
  $("#app").innerHTML = `<div class="settings"><nav class="set-nav" data-row="setnav"><h1>${esc(t("settings"))}</h1>
    ${CATS.map(([id, ic]) => `<button class="set-cat f${S.cat === id ? " active" : ""}" data-act="cat" data-cat="${id}">${icon(ic)}<span>${esc(t(`cat_${id}`))}</span></button>`).join("")}
    </nav><section class="set-body"><div class="set-body-inner" id="panel"></div></section></div>`;
  renderPanel(true);
  Nav.first(cat ? "#panel .f" : `.set-cat[data-cat='${S.cat}']`);
  if (!Nav.current) Nav.first(`.set-cat[data-cat='${S.cat}']`);
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
function refreshView() { if (S.view === "home") { const sel = Nav.current && Nav.current.dataset.id ? `[data-id='${Nav.current.dataset.id}']` : null; renderHome(sel); } else refreshPanel(); }

/* Allgemeine Einstellungsaktionen */
act("toggle", async (d) => {
  const path = d.k.split(".");
  const patch = {}; let node = patch; let cur = S.settings;
  for (let i = 0; i < path.length - 1; i++) { node[path[i]] = {}; node = node[path[i]]; cur = cur[path[i]]; }
  node[path[path.length - 1]] = !cur[path[path.length - 1]];
  await saveSettings(patch); refreshPanel();
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
    return h;
  },
};
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
  ...S.apps.all.filter((a) => a.id !== "settings").map((a) => ({ label: t("action_open", { name: a.name }), value: `app:${a.id}` })),
  { label: t("act_home"), value: "home" }, { label: t("settings"), value: "settings" }, { label: t("standby"), value: "standby" },
  { label: t("power_title"), value: "power_menu" }, { label: t("act_volup"), value: "volume_up" }, { label: t("act_voldown"), value: "volume_down" },
  { label: t("act_mute"), value: "mute" },
];
function actionLabel(a) { const c = ACTION_CHOICES().find((x) => x.value === a); return c ? c.label : a; }
Panels.remote = {
  async load() { S.panelData.bt = (await api("/api/bt")).devices || []; S.panelData.remote = await api("/api/remote"); },
  lead: () => esc(t("remote_lead")),
  render() {
    const rem = S.panelData.remote || {}, cec = rem.cec || {};
    let h = callout(t("waipu_hint"));
    h += section(t("bt_devices"));
    for (const dev of S.panelData.bt || []) {
      h += item({ act: "bt-dev", icon: dev.icon.includes("audio") ? "sound" : dev.icon.includes("gaming") ? "remote" : dev.icon.includes("keyboard") || dev.icon.includes("input") ? "remote" : "bluetooth",
        title: esc(dev.name), sub: dev.connected ? t("connected") : dev.paired ? t("paired") : t("tap_to_pair"), data: { v: dev.mac, paired: dev.paired ? 1 : 0, connected: dev.connected ? 1 : 0, name: dev.name } });
    }
    h += item({ act: "bt-scan", icon: "refresh", title: t("bt_search"), sub: t("bt_search_sub") });
    h += section(t("keys"));
    h += item({ act: "power-key", icon: "power", title: t("power_key"), sub: t("power_key_sub"), val: esc(t(`power_key_${S.settings.power_key}`)) });
    for (const [i, m] of (S.settings.remote.mappings || []).entries()) {
      h += item({ act: "map-edit", icon: "key", title: esc(m.label || m.match), sub: esc(m.match), val: esc(actionLabel(m.action)), data: { i } });
    }
    h += item({ act: "map-learn", icon: "plus", title: t("learn_key"), sub: t("learn_key_sub") });
    h += section(t("cec"));
    h += cec.adapter ? item({ icon: "check", title: t("cec_found"), sub: cec.tv_vendor ? t("cec_tv", { v: esc(cec.tv_vendor) }) : t("cec_ready") })
      : callout(t("cec_missing"), true);
    h += item({ act: "toggle", icon: "power", title: t("cec_tv_on"), toggle: S.settings.cec.tv_on_with_box, data: { k: "cec.tv_on_with_box" } });
    h += item({ act: "toggle", icon: "moon", title: t("cec_standby_with_tv"), toggle: S.settings.cec.standby_with_tv, data: { k: "cec.standby_with_tv" } });
    h += item({ act: "toggle", icon: "tv", title: t("cec_tv_off"), toggle: S.settings.cec.tv_off_on_standby, data: { k: "cec.tv_off_on_standby" } });
    h += item({ act: "cec-port", icon: "display", title: t("cec_port"), sub: t("cec_port_sub"), val: S.settings.cec.hdmi_port ? `HDMI ${S.settings.cec.hdmi_port}` : t("auto") });
    return h;
  },
};
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
  options: [{ label: t("power_key_standby"), sub: t("power_key_standby_sub"), value: "standby" }, { label: t("power_key_menu"), sub: t("power_key_menu_sub"), value: "menu" }] }));
act("cec-port", () => choose({ title: t("cec_port"), key: "cec.hdmi_port", current: S.settings.cec.hdmi_port,
  options: [{ label: t("auto"), value: 0 }, ...[1, 2, 3, 4].map((n) => ({ label: `HDMI ${n}`, value: n }))] }));
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
  async load() { S.panelData.disp = await api("/api/display"); S.panelData.audio = await api("/api/audio"); },
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
    h += item({ act: "speakers", icon: "sound", title: t("speakers"), sub: t("speakers_sub"), val: esc(t(`sp_${(S.settings.audio.speakers || "2.0").replace(".", "")}`)) });
    h += item({ act: "toggle", icon: "sparkle", title: t("passthrough"), sub: t("passthrough_sub"), toggle: S.settings.kodi.passthrough, data: { k: "kodi.passthrough" } });
    h += callout(t("codecs_info"));
    return h;
  },
};
act("noop", () => {});
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
    setTimeout(async () => { const q = $("#android-qr"); if (q && !q.innerHTML) { const r = await api(`/api/qr?text=${encodeURIComponent(url)}`); if (r.svg) q.innerHTML = r.svg; } }, 0);
    return h;
  },
};
act("android-init", () => confirmBox(t("android_setup"), t("android_setup_confirm"), t("start"), async () => {
  await api("/api/android/init", {}); await saveSettings({ android: { wanted: true } }); pollAndroid();
}));
act("android-id", async () => { await api("/api/android/id", {}); toast(t("android_id_wait"), "android"); setTimeout(async () => { await Panels.android.load(); refreshPanel(); }, 8000); });
act("android-full", () => api("/api/android/launch", {}));
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
    h += callout(t("cast_hint", { name: n }));
    return h;
  },
};

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
    h += item({ act: "amb-src", icon: "image", title: t("ambient_source"), val: esc(t(`amb_${a.source}`)) });
    if (a.source === "folder") h += item({ icon: "usb", title: t("amb_folder"), sub: esc(a.folder) });
    h += item({ act: "toggle", icon: "cloudsun", title: t("ambient_weather"), toggle: a.show_weather, data: { k: "ambient.show_weather" } });
    h += item({ act: "amb-off", icon: "moon", title: t("ambient_off"), sub: t("ambient_off_sub"), val: a.display_off_hours ? t("hours", { n: a.display_off_hours }) : t("never") });
    h += item({ act: "amb-now", icon: "eye", title: t("ambient_preview") });
    return h;
  },
};
act("amb-min", () => choose({ title: t("ambient_after"), key: "ambient.minutes", current: S.settings.ambient.minutes, options: [1, 2, 5, 10, 15, 30].map((n) => ({ label: t("minutes", { n }), value: n })) }));
act("amb-src", () => choose({ title: t("ambient_source"), key: "ambient.source", current: S.settings.ambient.source,
  options: ["art", "picsum", "folder"].map((v) => ({ label: t(`amb_${v}`), sub: t(`amb_${v}_sub`), value: v })) }));
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
  async load() { if (!S.panelData.zones) S.panelData.zones = (await api("/api/timezones")).zones || []; },
  lead: () => esc(t("device_lead")),
  render() {
    const s = S.settings;
    let h = item({ act: "dev-name", icon: "device", title: t("device_name"), sub: t("device_name_sub"), val: esc(s.device_name) });
    h += item({ act: "lang", icon: "language", title: t("language"), val: s.lang === "en" ? "English" : "Deutsch" });
    h += item({ act: "tz", icon: "clock", title: t("timezone"), val: esc(s.timezone) });
    h += item({ act: "city", icon: "pin", title: t("weather_city"), sub: t("weather_city_sub"), val: esc(s.city || t("not_set")) });
    h += section(t("sleep_timer"));
    h += item({ act: "sleep-action", icon: "moon", title: t("sleep_action"), val: esc(t(`sleep_${s.sleep_action}`)) });
    h += item({ act: "power-menu", icon: "clock", title: t("sleep_timer"), sub: S.sleepEnds ? t("sleep_active", { t: fmtDuration(S.sleepEnds - Date.now() / 1000) }) : t("sleep_sub") });
    return h;
  },
};
act("dev-name", () => editText({ title: t("device_name"), key: "device_name", placeholder: "WangertTV Box" }));
act("lang", () => choose({ title: t("language"), key: "lang", current: S.settings.lang, options: [{ label: "Deutsch", value: "de" }, { label: "English", value: "en" }],
  after: async () => { await loadApps(); openSettings("device"); } }));
act("tz", () => choose({ title: t("timezone"), key: "timezone", current: S.settings.timezone, options: S.panelData.zones.map((z) => ({ label: z.replace(/_/g, " "), value: z })) }));
act("city", () => editText({ title: t("weather_city"), key: "city", placeholder: t("city_ph"), after: async () => { await loadWeather(); } }));
act("sleep-action", () => choose({ title: t("sleep_action"), key: "sleep_action", current: S.settings.sleep_action,
  options: ["standby", "poweroff"].map((v) => ({ label: t(`sleep_${v}`), value: v })) }));

/* ---- System */
Panels.system = {
  async load() { S.panelData.sys = await api("/api/sysinfo"); },
  lead: () => esc(t("system_lead")),
  render() {
    const i = S.panelData.sys || {};
    const cards = [
      ["WangertTV", i.version], ["Debian", i.debian], [t("processor"), i.cpu], [t("graphics"), i.gpu],
      [t("memory"), `${fmtBytes(i.mem_used)} / ${fmtBytes(i.mem_total)}`], [t("storage"), `${fmtBytes(i.disk_used)} / ${fmtBytes(i.disk_total)}`],
      [t("ip_address"), i.ip || "—"], [t("network_name"), i.hostname], ["Kodi", (i.kodi || "—").replace(/^\d+:/, "").split("+")[0]],
      ["Chrome", (i.chrome || "—").split("-")[0]], ["Kernel", i.kernel], [t("uptime"), fmtDuration(i.uptime || 0)],
    ];
    let h = `<div class="info-grid">${cards.map(([k, v]) => `<div class="info-card"><div class="k">${esc(k)}</div><div class="v">${esc(v || "—")}</div></div>`).join("")}</div>`;
    if (S.state && S.state.live) {
      h += section(t("installation"));
      h += callout(t("live_hint"));
      h += item({ act: "installer", icon: "hdd", title: t("install_system"), sub: t("install_system_sub") });
    }
    h += section(t("power_title"));
    h += item({ act: "power-menu", icon: "moon", title: t("standby"), sub: t("standby_sub") });
    h += item({ act: "sys-reboot", icon: "refresh", title: t("reboot") });
    h += item({ act: "sys-poweroff", icon: "power", title: t("poweroff") });
    h += section(t("reset"));
    h += item({ act: "factory-reset", icon: "trash", title: t("factory_reset"), sub: t("factory_reset_sub"), cls: "danger" });
    return h;
  },
};
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
    let h = callout(t("waipu_hint"));
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
  if (cfg.source === "folder") ambPhotos = (await api("/api/photos")).photos || [];
  const el = document.createElement("div");
  el.className = "ambient";
  el.innerHTML = `<div class="amb-slide"></div><div class="amb-slide"></div><div class="amb-shade"></div><div class="amb-np"></div>
    <div class="amb-info"><div class="amb-time">${clockText()}</div><div class="amb-date"></div></div><div class="amb-weather"></div>`;
  document.body.appendChild(el);
  const nextSlide = () => {
    const slides = $$(".amb-slide", el); const on = slides[ambIdx % 2]; const off = slides[(ambIdx + 1) % 2];
    let bg;
    if (cfg.source === "picsum") bg = `url("https://picsum.photos/seed/wtv${Date.now() % 100000}/1920/1080")`;
    else if (cfg.source === "folder" && ambPhotos.length) bg = `url("${ambPhotos[ambIdx % ambPhotos.length]}")`;
    else bg = artGradient(ambIdx);
    off.style.backgroundImage = bg; off.classList.add("on"); on.classList.remove("on");
    ambIdx++;
    $(".amb-date", el).textContent = new Date().toLocaleDateString(locale(), { weekday: "long", day: "numeric", month: "long" });
    const w = S.weather;
    $(".amb-weather", el).innerHTML = cfg.show_weather && w ? `${icon(weatherIcon(w.code, w.is_day))}<div>${w.temp}°<small>${esc(w.place)}</small></div>` : "";
    const np = S.nowplaying;
    $(".amb-np", el).innerHTML = np && np.title ? `${np.cover ? `<img src="${esc(np.cover)}" alt="">` : icon("music")}<div><b>${esc(np.title)}</b><br><span style="color:rgba(255,255,255,.7)">${esc(np.artist || "")}</span></div>` : "";
    // Bildschirm nach eingestellter Zeit ganz ausschalten (Standby)
    const offH = cfg.display_off_hours;
    if (offH && Date.now() - S.ambientSince > offH * 3600 * 1000 && !manual) api("/api/power", { action: "standby", source: "ambient" });
  };
  nextSlide();
  ambTimer = setInterval(nextSlide, 15000);
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
  es.onmessage = async (ev) => {
    let m; try { m = JSON.parse(ev.data); } catch { return; }
    switch (m.type) {
      case "running": case "apps":
        await loadApps(); if (S.view === "home" && !Nav.layers.length) refreshView(); else if (S.cat === "apps") refreshPanel();
        break;
      case "channels": await loadChannels(); if (S.view === "home" && !Nav.layers.length) refreshView(); break;
      case "home":
        if (S.ambient) stopAmbient();
        if (S.view === "home" || S.view === "settings") { OSK.close(false); goHome("foryou"); }
        break;
      case "open": if (m.target === "settings" && S.settings.setup_done) openSettings(); break;
      case "power_menu": if (S.settings.setup_done) { if (S.view !== "home") goHome(); openPowerMenu(); } break;
      case "standby": setStandby(m.on); break;
      case "focus": S.homeFocused = m.home; if (m.home) S.lastKey = Date.now(); break;
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
      case "sleeptimer": S.sleepEnds = m.ends || 0; break;
      case "phone_text": OSK.phone(m.token, m.text); break;
      case "toast": toast(t(m.key)); break;
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
  connectEvents();
  await sleep(Math.max(0, 1800 - (Date.now() - started)));   // Startanimation kurz zeigen
  if (S.settings.setup_done) renderHome(); else { S.wz = 0; renderWizard(); }
  $("#boot").classList.add("done");
  if (S.standby) setStandby(true);
  setInterval(loadWeather, 20 * 60 * 1000);
  setInterval(async () => { if (S.settings.setup_done && S.view === "home") { const a = await api("/api/android"); if (a && a.running) S.androidApps = (await api("/api/android/apps")).apps || []; } }, 60 * 1000);
}
init();
