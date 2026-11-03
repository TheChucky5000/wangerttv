/* WangertTV – Fernsehen aus einem Guss: eigenes TV-Programm (EPG-Raster), Sender sortieren/ausblenden,
   intelligente Startseite ("Für dich") und die Einstellungen für Fernsehen, Personalisierung und Box.
   Baut auf app.js auf (S, Nav, act, t, api …). */
"use strict";

/* Ansichten mit eigener Tastensteuerung: Views[name] = { key(k, e) -> true wenn erledigt, back() } */
const Views = {};

function qualityBadge(q) {
  return q ? `<span class="q-badge${q === "UHD" ? " uhd" : ""}">${esc(q)}</span>` : "";
}
function chLogo(ch, cls = "") {
  const name = JSON.stringify(ch.name || "");
  return ch.logo ? `<img class="${cls}" src="${esc(ch.logo)}" alt="" onerror="this.replaceWith(Object.assign(document.createElement('b'),{textContent:${esc(name)}}))">`
    : `<b>${esc(ch.name || "")}</b>`;
}
function dayLabel(ts) {
  const d = new Date(ts * 1000), today = new Date(); today.setHours(0, 0, 0, 0);
  const diff = Math.round((new Date(d).setHours(0, 0, 0, 0) - today) / 86400000);
  if (diff === 0) return t("plan_today");
  if (diff === 1) return t("plan_tomorrow");
  return d.toLocaleDateString(locale(), { weekday: "long" });
}

/* ====================================================================== TV-Programm (EPG-Raster) */
const EPG_SPAN = 150 * 60;          // sichtbares Zeitfenster: 2,5 Stunden
const EPG_ROWS = 7;
const floor30 = (ts) => Math.floor(ts / 1800) * 1800;
const Epg = {
  chans: [], data: {}, loadedTo: 0, ws: 0, ci: 0, sel: null, anchor: 0, top: 0, digits: "", digitTimer: null, loading: false,

  async open({ at, channel } = {}) {
    Nav.popAll(); S.view = "epg";
    const now = Date.now() / 1000;
    this.ws = floor30(at || now);
    this.anchor = Math.max(at || now, now);
    this.data = {}; this.loadedTo = 0; this.chans = [];
    this.renderShell();
    await this.load(floor30(now), 12);
    if (S.view !== "epg") return;
    const want = channel || (S.settings.channel_prefs && (S.settings.channel_prefs.recent || [])[0]);
    const idx = this.chans.findIndex((c) => c.name === want);
    this.ci = idx >= 0 ? idx : 0;
    this.top = Math.max(0, this.ci - 2);
    this.pick(this.anchor);
    this.render();
  },
  async load(start, hours) {
    if (this.loading) return;
    this.loading = true;
    try {
      const r = await api(`/api/epg?start=${Math.floor(start)}&hours=${hours}`);
      if (!r || !r.channels) return;
      if (!this.chans.length) this.chans = r.channels.map(({ entries, ...c }) => c);
      for (const c of r.channels) {
        const have = this.data[c.name] || [];
        const seen = new Set(have.map((e) => e[0]));
        this.data[c.name] = [...have, ...(c.entries || []).filter((e) => !seen.has(e[0]))].sort((a, b) => a[0] - b[0]);
      }
      this.loadedTo = Math.max(this.loadedTo, start + hours * 3600);
    } finally { this.loading = false; }
  },
  entries(ci = this.ci) { const c = this.chans[ci]; return c ? this.data[c.name] || [] : []; },
  entry() { return this.sel === null ? null : this.entries().find((e) => e[0] === this.sel) || null; },
  /* Sendung auf dem aktuellen Sender wählen, die zum Zeitpunkt <at> läuft (oder als nächste im Fenster beginnt) */
  pick(at) {
    const list = this.entries(), we = this.ws + EPG_SPAN;
    let e = list.find((x) => x[0] <= at && at < x[1]) || list.find((x) => x[0] >= at && x[0] < we) || list.find((x) => x[1] > this.ws && x[0] < we);
    this.sel = e ? e[0] : null;
  },
  async ensureLoaded(until) {
    const limit = Date.now() / 1000 + 30 * 3600;
    if (until > this.loadedTo && this.loadedTo < limit) await this.load(this.loadedTo, 12);
  },
  key(k) {
    if (/^[0-9]$/.test(k)) { this.dial(k); return true; }
    if (k === "ArrowUp" || k === "ArrowDown" || k === "PageUp" || k === "PageDown") {
      const step = k === "ArrowUp" ? -1 : k === "ArrowDown" ? 1 : k === "PageUp" ? -EPG_ROWS : EPG_ROWS;
      const ci = clamp(this.ci + step, 0, this.chans.length - 1);
      if (ci === this.ci) return true;
      this.ci = ci; this.pick(this.anchor); this.render(); return true;
    }
    if (k === "ArrowRight" || k === "ArrowLeft") { this.horizontal(k === "ArrowRight" ? 1 : -1); return true; }
    if (k === "F1") { this.jump(Date.now() / 1000); return true; }
    if (k === "F2") { this.jump(slotTime("prime")); return true; }
    if (k === "F3") { this.jump(this.anchor + 86400); return true; }
    if (k === "F4") { ChEd.open("epg"); return true; }
    return false;
  },
  async horizontal(dir) {
    const now = Date.now() / 1000, list = this.entries();
    const cur = this.entry();
    let target;
    if (cur) {
      const i = list.indexOf(cur);
      target = list[i + dir];
    } else {
      target = dir > 0 ? list.find((e) => e[0] >= this.ws + EPG_SPAN / 2) : [...list].reverse().find((e) => e[1] <= this.ws + EPG_SPAN / 2);
    }
    if (!target && dir > 0) {
      await this.ensureLoaded(this.ws + EPG_SPAN + 3 * 3600);
      const fresh = this.entries();
      target = cur ? fresh[fresh.findIndex((e) => e[0] === cur[0]) + 1] : null;
      if (!target) { this.shift(dir); return; }
    }
    if (!target) { this.shift(dir); return; }
    if (target[1] <= now) return;                                     // schon vorbei
    this.sel = target[0];
    this.anchor = Math.max(target[0], now);
    if (target[0] >= this.ws + EPG_SPAN - 20 * 60) this.ws = floor30(target[0]) - 1800;
    else if (target[0] < this.ws && target[1] <= this.ws + 20 * 60) this.ws = Math.max(floor30(now), floor30(target[0]));
    this.ws = Math.max(this.ws, floor30(now));
    await this.ensureLoaded(this.ws + EPG_SPAN);
    this.render();
  },
  async shift(dir) {
    const now = Date.now() / 1000;
    const ws = Math.max(floor30(now), this.ws + dir * 3600);
    if (ws === this.ws) return;
    this.ws = ws; this.anchor = Math.max(now, ws);
    await this.ensureLoaded(ws + EPG_SPAN);
    this.pick(this.anchor); this.render();
  },
  async jump(at) {
    const now = Date.now() / 1000;
    this.ws = Math.max(floor30(now), floor30(at) - (at > now + 1800 ? 1800 : 0));
    this.anchor = Math.max(at, now);
    await this.ensureLoaded(this.ws + EPG_SPAN);
    this.pick(this.anchor); this.render();
  },
  dial(d) {
    this.digits = (this.digits + d).slice(-4);
    clearTimeout(this.digitTimer);
    const box = $(".epg-dial"); if (box) { box.textContent = this.digits; box.classList.add("on"); }
    this.digitTimer = setTimeout(() => {
      const n = Number(this.digits); this.digits = "";
      const b = $(".epg-dial"); if (b) b.classList.remove("on");
      const idx = this.chans.findIndex((c) => Number(c.number) === n);
      if (idx >= 0) { this.ci = idx; this.top = Math.max(0, idx - 2); this.pick(this.anchor); this.render(); } else toast(t("dial_none"), "tv");
    }, 1200);
  },
  back() { goHome(S.tab); },
  renderShell() {
    $("#app").innerHTML = `<header class="topbar"><div class="brand"><svg><use href="#logo-mark"/></svg><span>Wangert<b>TV</b></span></div>
        <div class="view-title">${icon("guide")}${esc(t("tv_guide"))}</div><div class="top-right"><div class="clock" id="clock">${clockText()}</div></div></header>
      <div class="epg-view view-enter"><div class="epg-info"></div><div class="epg-head"></div><div class="epg-body"><div class="epg-loading">${esc(t("loading"))}</div></div>
      <div class="epg-legend"><span><i class="k red"></i>${esc(t("epg_key_now"))}</span><span><i class="k green"></i>20:15</span>
        <span><i class="k yellow"></i>${esc(t("epg_key_day"))}</span><span><i class="k blue"></i>${esc(t("ched_title"))}</span>
        <span>0–9 ${esc(t("epg_key_number"))}</span><span>OK ${esc(t("epg_key_ok"))}</span></div><div class="epg-dial"></div></div>`;
  },
  render() {
    if (S.view !== "epg") return;
    const body = $(".epg-body"), head = $(".epg-head"); if (!body) return;
    if (!this.chans.length) {
      body.innerHTML = `<div class="empty-note">${icon("guide", "i")}<div class="grow"><h3>${esc(t("plan_no_epg"))}</h3>${esc(t("plan_no_epg_text"))}</div></div>`;
      return;
    }
    if (this.ci < this.top + 1) this.top = Math.max(0, this.ci - 1);
    if (this.ci > this.top + EPG_ROWS - 2) this.top = Math.min(Math.max(0, this.chans.length - EPG_ROWS), this.ci - EPG_ROWS + 2);
    const ws = this.ws, we = ws + EPG_SPAN, now = Date.now() / 1000;
    const W = body.clientWidth - 330, ppm = W / (EPG_SPAN / 60);
    const x = (ts) => ((ts - ws) / 60) * ppm;
    let marks = "";
    for (let m = ws; m < we; m += 1800) marks += `<span style="left:${x(m)}px">${hm(m)}</span>`;
    head.innerHTML = `<div class="epg-day">${esc(dayLabel(ws + 60))}</div><div class="epg-ruler">${marks}</div>`;
    const rems = new Set(((S.plan && S.plan.reminders) || []).map((r) => `${r.name}|${r.start}`));
    let rows = "";
    for (let i = this.top; i < Math.min(this.chans.length, this.top + EPG_ROWS); i++) {
      const c = this.chans[i];
      const list = (this.data[c.name] || []).filter((e) => e[1] > ws && e[0] < we);
      let cells = list.map((e) => {
        const l = Math.max(0, x(e[0])), r = Math.min(W, x(e[1])), w = Math.max(4, r - l - 4);
        const cls = [e[0] <= now && now < e[1] ? "live" : "", rems.has(`${c.name}|${e[0]}`) ? "rem" : "", i === this.ci && e[0] === this.sel ? "sel" : ""].filter(Boolean).join(" ");
        const sel = i === this.ci && e[0] === this.sel;
        return `<button class="epg-cell${sel ? " f" : ""} ${cls}" ${sel ? 'data-act="epg-item"' : ""} style="left:${l}px;width:${w}px">
          <b>${esc(e[2])}</b>${w > 120 ? `<small>${hm(e[0])}${rems.has(`${c.name}|${e[0]}`) ? " · 🔔" : ""}</small>` : ""}</button>`;
      }).join("");
      if (!list.length) {
        const sel = i === this.ci;
        cells = `<button class="epg-cell empty${sel ? " f sel" : ""}" ${sel ? 'data-act="epg-item"' : ""} style="left:0;width:${W - 4}px"><b>${esc(t("spot_no_epg"))}</b></button>`;
      }
      rows += `<div class="epg-row${i === this.ci ? " cur" : ""}"><div class="epg-ch"><span class="n">${esc(c.number || "")}</span>
        <span class="lg">${chLogo(c)}</span>${qualityBadge(c.quality)}${isFav(c.name) ? `<span class="fav">${icon("star")}</span>` : ""}</div>
        <div class="epg-line">${cells}</div></div>`;
    }
    const nowLine = now >= ws && now < we ? `<div class="epg-now" style="left:${330 + x(now)}px"></div>` : "";
    body.innerHTML = rows + nowLine;
    const cell = $(".epg-cell.f", body);
    if (cell) Nav.focus(cell, { scroll: false });
    this.info();
  },
  info() {
    const box = $(".epg-info"); if (!box) return;
    const c = this.chans[this.ci]; if (!c) { box.innerHTML = ""; return; }
    const e = this.entry(), now = Date.now() / 1000;
    let h = `<div class="ei-logo">${chLogo(c)}</div><div class="ei-body"><div class="hero-kicker">${esc(c.name)}${c.number ? ` · ${esc(t("channel_no", { n: c.number }))}` : ""}${c.quality ? ` · ${esc(c.quality)}` : ""}</div>`;
    if (!e) { box.innerHTML = `${h}<h1>${esc(t("spot_no_epg"))}</h1><p>${esc(t("epg_switch_hint"))}</p></div>`; return; }
    const live = e[0] <= now && now < e[1];
    const mins = Math.round((e[1] - e[0]) / 60);
    h += `<h1>${esc(e[2])}</h1><div class="ei-time">${esc(dayLabel(e[0]))}, ${hm(e[0])} – ${hm(e[1])} · ${mins} Min.`;
    if (live) h += ` · <span class="live-dot">LIVE</span> ${esc(t("epg_left", { n: Math.max(1, Math.round((e[1] - now) / 60)) }))}`;
    else h += ` · ${esc(t("plan_starts_in", { t: fmtDuration(Math.max(0, e[0] - now)) }))}`;
    h += "</div>";
    if (live) h += `<div class="ei-prog"><i style="width:${((now - e[0]) / (e[1] - e[0]) * 100).toFixed(1)}%"></i></div>`;
    if (e[3]) h += `<p>${esc(e[3])}</p>`;
    box.innerHTML = `${h}</div>`;
  },
};
Views.epg = { key: (k) => Epg.key(k), back: () => Epg.back() };
/* Jede Minute: Jetzt-Linie und laufende Sendungen nachführen */
setInterval(() => {
  if (S.view !== "epg" || Nav.layers.length || !Epg.chans.length) return;
  const now = Date.now() / 1000;
  if (Epg.ws + EPG_SPAN / 2 < now) { Epg.ws = floor30(now); if (Epg.anchor < now) { Epg.anchor = now; Epg.pick(now); } }
  Epg.render();
}, 60 * 1000);
function openEpg(opts) { return Epg.open(opts); }
/* Senderliste (Tab Live-TV) mit dem zuletzt gesehenen Sender im Fokus */
function focusLive() {
  goHome("live");
  const last = (chPrefs().recent || [])[0];
  const el = last && $(`.grid .channel[data-name="${CSS.escape(last)}"]`);
  if (el) Nav.focus(el); else Nav.first(".grid .channel");
}

act("epg-item", () => {
  const c = Epg.chans[Epg.ci]; if (!c) return;
  const e = Epg.entry(), now = Date.now() / 1000;
  if (!e) { playChannel(c.name); return; }
  const it = { name: c.name, number: c.number, logo: c.logo, title: e[2], start: e[0], end: e[1], desc: e[3] || "" };
  const live = e[0] <= now && now < e[1], set = hasReminder(it);
  const items = [];
  if (live) items.push({ label: t("watch_now"), icon: "play", value: "play" });
  else if (!set) items.push({ label: t("plan_remind"), sub: t("plan_remind_sub"), icon: "bell", value: "remind" },
    { label: t("plan_remind_auto"), sub: t("plan_remind_auto_sub"), icon: "tv", value: "auto" });
  if (set) items.push({ label: t("plan_remind_del"), icon: "trash", value: "del", danger: true });
  if (!live) items.push({ label: t("plan_channel_now"), icon: "play", value: "play" });
  items.push({ label: t(isFav(c.name) ? "fav_remove" : "fav_add"), icon: "star", value: "fav" });
  dialog({ title: e[2], text: esc(`${c.name} · ${dayLabel(e[0])}, ${hm(e[0])} – ${hm(e[1])}`), items, onPick: async (v) => {
    if (v === "play") { playChannel(c.name); return; }
    if (v === "fav") { await toggleFav(c.name); Epg.render(); return; }
    const r = await api("/api/reminders", { action: v === "del" ? "del" : "add", item: it, auto: v === "auto" });
    if (r && r.reminders) S.plan.reminders = r.reminders;
    toast(t(v === "del" ? "plan_reminder_deleted" : v === "auto" ? "plan_reminder_auto_ok" : "plan_reminder_ok", { t: hm(it.start) }), "bell");
    Epg.render();
  } });
});

/* ====================================================================== Sender sortieren & ausblenden */
const ChEd = {
  list: [], moving: -1, from: "settings", dirty: false,
  async open(from = "settings") {
    Nav.popAll(); S.view = "chedit"; this.from = from; this.moving = -1; this.dirty = false;
    $("#app").innerHTML = `<header class="topbar"><div class="brand"><svg><use href="#logo-mark"/></svg><span>Wangert<b>TV</b></span></div>
      <div class="view-title">${icon("sort")}${esc(t("ched_title"))}</div><div class="top-right"><div class="clock" id="clock">${clockText()}</div></div></header>
      <div class="ched view-enter"><p class="lead">${esc(t("loading"))}</p></div>`;
    const r = await api("/api/channels/all");
    this.list = ((r && r.channels) || []).map((c) => ({ name: c.name, logo: c.logo, quality: c.quality, hidden: !!c.hidden, group: c.group || "" }));
    this.render();
  },
  render(focusIdx) {
    if (S.view !== "chedit") return;
    const box = $(".ched"); if (!box) return;
    const renumber = (S.settings.tv || {}).renumber !== false;
    let n = 0;
    const rows = this.list.map((c, i) => {
      if (!c.hidden) n++;
      return `<div class="ched-row${c.hidden ? " off" : ""}${i === this.moving ? " moving" : ""}" data-row="ch-${i}">
        <button class="ched-main f" data-act="ched-pick" data-i="${i}"><span class="grip">${icon("sort")}</span>
          <span class="n">${c.hidden ? "–" : renumber ? n : ""}</span><span class="lg">${chLogo(c)}</span>
          <span class="nm">${esc(c.name)}</span>${qualityBadge(c.quality)}${isFav(c.name) ? `<span class="fav">${icon("star")}</span>` : ""}
          ${c.hidden ? `<span class="tag">${esc(t("ched_hidden"))}</span>` : ""}</button>
        <button class="ched-eye f" data-act="ched-eye" data-i="${i}" title="${esc(t(c.hidden ? "ched_show" : "ched_hide"))}">${icon(c.hidden ? "eyeoff" : "eye")}</button></div>`;
    }).join("");
    const visible = this.list.filter((c) => !c.hidden).length;
    box.innerHTML = `<div class="chips" data-row="ched-tools">
        <button class="btn small primary f" data-act="ched-done">${icon("check")}${esc(t("ched_done"))}</button>
        <button class="btn small f" data-act="ched-sort" data-how="favs">${icon("star")}${esc(t("ched_favs_first"))}</button>
        <button class="btn small f" data-act="ched-sort" data-how="hd">${icon("tv")}${esc(t("ched_hd_first"))}</button>
        <button class="btn small f" data-act="ched-sort" data-how="az">${icon("sort")}${esc(t("ched_az"))}</button>
        <button class="btn small f" data-act="ched-sort" data-how="reset">${icon("refresh")}${esc(t("ched_reset"))}</button>
        <span class="ched-count">${esc(t("ched_count", { n: visible, all: this.list.length }))}</span></div>
      <p class="ched-hint">${esc(t(this.moving >= 0 ? "ched_moving_hint" : "ched_hint"))}</p>
      <div class="view-scroll ched-list" data-row="ched-list">${rows || `<div class="empty-note">${esc(t("plan_no_epg_text"))}</div>`}</div>`;
    const sel = focusIdx !== undefined ? `.ched-main[data-i="${focusIdx}"]` : ".ched-main";
    Nav.first(sel);
  },
  move(dir) {
    const i = this.moving, j = i + dir;
    if (i < 0 || j < 0 || j >= this.list.length) return;
    [this.list[i], this.list[j]] = [this.list[j], this.list[i]];
    this.moving = j; this.dirty = true;
    this.render(j);
  },
  key(k) {
    if (this.moving < 0) return false;
    if (k === "ArrowUp") { this.move(-1); return true; }
    if (k === "ArrowDown") { this.move(1); return true; }
    if (k === "PageUp") { for (let x = 0; x < 8; x++) this.move(-1); return true; }
    if (k === "PageDown") { for (let x = 0; x < 8; x++) this.move(1); return true; }
    return k === "ArrowLeft" || k === "ArrowRight";
  },
  async save() {
    if (!this.dirty) return;
    this.dirty = false;
    await saveSettings({ tv: { order: this.list.map((c) => c.name), hidden: this.list.filter((c) => c.hidden).map((c) => c.name) } });
    await loadChannels();
    toast(t("ched_saved"), "check");
  },
  async back() {
    if (this.moving >= 0) { const i = this.moving; this.moving = -1; this.render(i); return; }
    await this.save();
    if (this.from === "epg") openEpg();
    else if (this.from === "live") goHome("live");
    else openSettings("television");
  },
};
Views.chedit = { key: (k) => ChEd.key(k), back: () => ChEd.back() };
act("ched-pick", (d) => {
  const i = +d.i;
  ChEd.moving = ChEd.moving === i ? -1 : i;
  ChEd.render(i);
});
act("ched-eye", (d) => {
  const c = ChEd.list[+d.i]; if (!c) return;
  if (!c.hidden && ChEd.list.filter((x) => !x.hidden).length <= 1) return;
  c.hidden = !c.hidden; ChEd.dirty = true;
  ChEd.render(+d.i);
  Nav.first(`.ched-eye[data-i="${d.i}"]`);
});
act("ched-done", () => ChEd.back());
act("ched-sort", (d) => {
  const L2 = ChEd.list;
  const pos = new Map(L2.map((c, i) => [c.name, i]));
  if (d.how === "reset") {
    confirmBox(t("ched_reset"), esc(t("ched_reset_q")), t("ched_reset"), async () => {
      await saveSettings({ tv: { order: [], hidden: [] } }); await loadChannels(); ChEd.dirty = false; ChEd.open(ChEd.from);
    });
    return;
  }
  if (d.how === "az") L2.sort((a, b) => a.name.localeCompare(b.name, locale(), { sensitivity: "base" }));
  if (d.how === "favs") L2.sort((a, b) => (isFav(b.name) - isFav(a.name)) || (pos.get(a.name) - pos.get(b.name)));
  if (d.how === "hd") { const r = { UHD: 0, HD: 1 }; L2.sort((a, b) => ((r[a.quality] ?? 2) - (r[b.quality] ?? 2)) || (pos.get(a.name) - pos.get(b.name))); }
  ChEd.dirty = true; ChEd.moving = -1;
  ChEd.render(); Nav.first(`[data-act="ched-sort"][data-how="${d.how}"]`);
});
function openChannelEditor(from) { return ChEd.open(from); }

/* ====================================================================== Intelligente Startseite ("Für dich") */
S.fy = null;
async function loadForYou() {
  const r = await api("/api/foryou");
  if (r && !r.error) S.fy = r;
}
setInterval(() => { if (!S.standby) loadForYou().then(() => { if (S.view === "home" && S.tab === "foryou" && !Nav.layers.length && !S.ambient && !Nav.current?.closest?.(".row-track")) refreshView(); }); }, 10 * 60 * 1000);

const WX = { 0: "wx_clear", 1: "wx_partly", 2: "wx_partly", 3: "wx_cloudy", 45: "wx_fog", 48: "wx_fog" };
function wxText(code) {
  if (WX[code]) return t(WX[code]);
  if (code >= 51 && code <= 57) return t("wx_drizzle");
  if ((code >= 61 && code <= 67) || (code >= 80 && code <= 82)) return t("wx_rain");
  if ((code >= 71 && code <= 77) || code === 85 || code === 86) return t("wx_snow");
  if (code >= 95) return t("wx_storm");
  return t("wx_cloudy");
}
function greetingTitle() {
  const fy = S.fy || {}, part = fy.daypart || "day";
  const name = (fy.name || "").trim();
  return name ? t(`greet_${part}_name`, { name }) : t(`greet_${part}`);
}
const NEWS_CHANNELS = /tagesschau|n-?tv|welt|phoenix|zdfinfo|euronews|bloomberg|cnn/i;
function newsChannels() { return S.channels.filter((c) => NEWS_CHANNELS.test(c.name)).slice(0, 4); }
function topPick(kind) { return ((S.fy && S.fy.picks) || []).find((p) => p.kind === kind); }

/* Erste Karte im Karussell: Begrüßung mit passendem Vorschlag */
function greetingSlide() {
  const fy = S.fy;
  if (!fy || !fy.enabled) return null;
  const part = fy.daypart || "day";
  const colors = { morning: ["#ff9f43", "#c2185b"], day: ["#1477ff", "#0b2f8a"], evening: ["#7b3cff", "#1d1460"], night: ["#1b2a55", "#05070f"] }[part];
  const slide = { kicker: new Date().toLocaleDateString(locale(), { weekday: "long", day: "numeric", month: "long" }), title: greetingTitle(),
    text: "", glyph: part === "night" ? "moon" : part === "morning" ? "sun" : "sparkle", c1: colors[0], c2: colors[1], actions: [] };
  const w = S.weather;
  const ch = topPick("channel") && S.channels.find((c) => c.name === topPick("channel").name);
  const st = topPick("radio");
  const newShow = (fy.shows || []).find((s) => s.new && s.start > Date.now() / 1000);
  const lines = [];
  if (fy.morning && w) lines.push(t("greet_weather", { temp: w.temp, wx: wxText(w.code), place: w.place || "" }));
  if (ch) { const cur = liveNow(ch); lines.push(cur ? t("greet_now_on", { title: cur.title, ch: ch.name }) : t("greet_channel", { ch: ch.name })); }
  if (newShow) lines.push(t("greet_new_episode", { title: newShow.title, t: `${dayLabel(newShow.start)} ${hm(newShow.start)}`, ch: newShow.name }));
  slide.text = lines.slice(0, 2).join(" ") || t("greet_default");
  if (fy.morning && st) {
    stationKey(st.station);
    slide.actions.push({ label: t("greet_play_radio", { name: st.station.name }), act: "radio-play", data: { id: st.station.id }, icon: "radio", primary: true });
  }
  if (ch) slide.actions.push({ label: t("greet_watch", { ch: ch.name }), act: "channel", data: { name: ch.name }, icon: "play", primary: !slide.actions.length });
  if (newShow && slide.actions.length < 3) slide.actions.push({ label: t("greet_remind"), act: "fy-remind", data: { key: `${newShow.name}|${newShow.start}` }, icon: "bell" });
  if (slide.actions.length < 3) slide.actions.push({ label: t("tv_guide"), act: "launch", data: { id: "tvguide" }, icon: "guide", primary: !slide.actions.length });
  return slide;
}
act("fy-remind", async (d) => {
  const it = ((S.fy && S.fy.shows) || []).find((s) => `${s.name}|${s.start}` === d.key); if (!it) return;
  const r = await api("/api/reminders", { action: "add", item: it, auto: false });
  if (r && r.reminders) S.plan.reminders = r.reminders;
  toast(t("plan_reminder_ok", { t: hm(it.start) }), "bell");
});

/* "Guten Morgen": Wetter, Nachrichten, Morgenradio */
function morningRow() {
  const fy = S.fy; if (!fy || !fy.morning) return "";
  const w = S.weather;
  let tiles = "";
  if (w) {
    const today = (w.daily || [])[0];
    tiles += `<div class="tile wx-card"><span class="wx-ic">${icon(weatherIcon(w.code, w.is_day))}</span><span class="wx-t">${w.temp}°</span>
      <span class="name">${esc(wxText(w.code))}</span><span class="sub">${esc(w.place || "")}${today ? ` · ${today.max}° / ${today.min}°` : ""}</span></div>`;
  }
  const st = topPick("radio");
  if (st) tiles += stationTile(st.station);
  else for (const f of (S.radio.favorites || []).slice(0, 1)) tiles += stationTile(f);
  tiles += newsChannels().map(channelTile).join("");
  if (!tiles) return "";
  return `<div class="row"><div class="row-title">${esc(t("morning_row"))}<small>${esc(t("morning_row_hint"))}</small></div><div class="row-track" data-row="morning">${tiles}</div></div>`;
}

/* "Jetzt für dich": was um diese Uhrzeit sonst läuft – Sender, Apps und Radio gemischt */
function picksRow() {
  const fy = S.fy; if (!fy || !fy.enabled) return "";
  const tiles = [];
  for (const p of fy.picks || []) {
    if (p.kind === "channel") { const ch = S.channels.find((c) => c.name === p.name); if (ch) tiles.push(channelTile(ch)); }
    else if (p.kind === "app") { const app = S.apps.all.find((a) => a.id === p.id); if (app) tiles.push(appTile(app)); }
    else if (p.kind === "radio" && p.station) tiles.push(stationTile(p.station));
    if (tiles.length >= 12) break;
  }
  if (tiles.length < 2) return "";
  return `<div class="row"><div class="row-title">${esc(t("fy_now_row"))}<small>${esc(t("fy_now_hint"))}</small></div><div class="row-track" data-row="fy-picks">${tiles.join("")}</div></div>`;
}

/* "Deine Sendungen": Lieblingssendungen (gelernt) mit "Neue Folge" */
function showsRow() {
  const shows = (S.fy && S.fy.shows) || []; if (!shows.length) return "";
  return `<div class="row"><div class="row-title">${esc(t("fy_shows_row"))}<small>${esc(t("fy_shows_hint"))}</small></div>
    <div class="row-track" data-row="fy-shows">${shows.slice(0, 14).map((it) => planTile(it).replace('<span class="name">', `${it.new ? `<span class="new-ep">${esc(t("new_episode"))}</span>` : ""}<span class="name">`)).join("")}</div></div>`;
}

/* "Heute für dich im TV" */
function todayRow() {
  const fy = S.fy; if (!fy || !fy.enabled) return "";
  const shows = new Set((fy.shows || []).map((s) => `${s.name}|${s.start}`));
  const items = (fy.today || []).filter((i) => !shows.has(`${i.name}|${i.start}`));
  if (items.length < 2) return "";
  return `<div class="row"><div class="row-title">${esc(t("fy_today_row"))}<small>${esc(t("plan_row_hint"))}</small></div>
    <div class="row-track" data-row="fy-today">${items.map(planTile).join("")}</div></div>`;
}

/* ====================================================================== Einstellungen: Fernsehen */
const SECS = [0, 3, 5, 6, 8, 12, 20];
Panels.television = {
  lead: () => esc(t("tvset_lead")),
  render() {
    const tv = S.settings.tv || {};
    let h = section(t("tvset_list"));
    h += item({ act: "ched-open", icon: "sort", title: t("ched_title"), sub: t("ched_sub"), val: (tv.order || []).length || (tv.hidden || []).length ? t("tvset_custom") : "" });
    h += item({ act: "toggle", icon: "tv", title: t("tvset_logos"), sub: t("tvset_logos_sub"), toggle: tv.logos !== "provider", data: { k: "tv.logos", on: "official", off: "provider" } });
    h += item({ act: "toggle", icon: "grid", title: t("tvset_renumber"), sub: t("tvset_renumber_sub"), toggle: tv.renumber !== false, data: { k: "tv.renumber" } });
    h += section(t("tvset_watching"));
    h += item({ act: "tv-infobar", icon: "info", title: t("tvset_infobar"), sub: t("tvset_infobar_sub"), val: tv.infobar_secs ? t("secs_n", { n: tv.infobar_secs }) : t("tvset_infobar_off") });
    h += item({ act: "tv-start", icon: "play", title: t("tvset_start"), val: esc(t(`tvset_start_${tv.start_with === "list" ? "list" : "last"}`)) });
    h += item({ act: "tv-back", icon: "back", title: t("tvset_back"), sub: t("tvset_back_sub"), val: esc(t(`tvset_back_${["epg", "home"].includes(tv.back_to) ? tv.back_to : "live"}`)) });
    h += item({ act: "toggle", icon: "kodi", title: t("tvset_kodi_menus"), sub: t("tvset_kodi_menus_sub"), toggle: !!tv.kodi_menus, data: { k: "tv.kodi_menus" } });
    h += section(t("plan_title"));
    h += item({ act: "toggle", icon: "bell", title: t("tvset_smart"), sub: t("tvset_smart_sub"), toggle: !!tv.smart_reminders, data: { k: "tv.smart_reminders" } });
    h += item({ act: "tv-reminders", icon: "clock", title: t("plan_reminders"), sub: t("tvset_reminders_sub", { n: ((S.plan && S.plan.reminders) || []).length }) });
    h += item({ act: "launch", icon: "guide", title: t("tv_guide"), data: { id: "tvguide" } });
    return h;
  },
};
act("ched-open", () => ChEd.open("settings"));
act("tv-infobar", () => choose({ title: t("tvset_infobar"), key: "tv.infobar_secs", current: (S.settings.tv || {}).infobar_secs ?? 6,
  options: SECS.map((n) => ({ label: n ? t("secs_n", { n }) : t("tvset_infobar_off"), sub: n ? "" : t("tvset_infobar_off_sub"), value: n })) }));
act("tv-start", () => choose({ title: t("tvset_start"), key: "tv.start_with", current: (S.settings.tv || {}).start_with || "last",
  options: ["last", "list"].map((v) => ({ label: t(`tvset_start_${v}`), sub: t(`tvset_start_${v}_sub`), value: v })) }));
act("tv-back", () => choose({ title: t("tvset_back"), key: "tv.back_to", current: (S.settings.tv || {}).back_to || "live",
  options: ["live", "epg", "home"].map((v) => ({ label: t(`tvset_back_${v}`), value: v })) }));
act("tv-reminders", () => openPlanner("rem"));

/* ====================================================================== Einstellungen: Für dich (Personalisierung) */
Panels.personal = {
  async load() { S.panelData.history = await api("/api/history"); },
  lead: () => esc(t("pers_lead")),
  render() {
    const p = S.settings.personal || {}, hs = S.panelData.history || {};
    const on = p.enabled !== false;
    let h = item({ act: "toggle", icon: "sparkle", title: t("pers_enabled"), sub: t("pers_enabled_sub"), toggle: on, data: { k: "personal.enabled" } });
    h += item({ act: "pers-name", icon: "device", title: t("pers_name"), sub: t("pers_name_sub"), val: esc(p.name || t("not_set")) });
    h += item({ act: "toggle", icon: "sun", title: t("pers_morning"), sub: t("pers_morning_sub"), toggle: p.morning !== false, data: { k: "personal.morning" } });
    h += section(t("pers_learned"));
    const it = hs.items || {};
    h += callout(esc(t("pers_learned_text", { ch: it.channel || 0, apps: it.app || 0, radio: it.radio || 0, shows: hs.favorites || 0 })));
    h += item({ act: "pers-clear", icon: "trash", title: t("pers_clear"), sub: t("pers_clear_sub"), cls: "danger" });
    h += callout(esc(t("pers_privacy")));
    return h;
  },
};
act("pers-name", () => editText({ title: t("pers_name"), key: "personal.name", placeholder: t("pers_name_ph"), after: async () => { await loadForYou(); } }));
act("pers-clear", () => confirmBox(t("pers_clear"), esc(t("pers_clear_q")), t("pers_clear"), async () => {
  await api("/api/history/clear", {}); await Panels.personal.load(); await loadForYou(); refreshPanel(); toast(t("pers_cleared"), "check");
}, true));

/* ====================================================================== Einstellungen: Box (Leistung, Einschalten) */
function boxItems() {
  const b = S.settings.box || {};
  let h = section(t("box_section"));
  h += item({ act: "box-power", icon: "sparkle", title: t("box_power"), sub: t("box_power_sub"), val: esc(t(`box_power_${["performance", "quiet"].includes(b.power_profile) ? b.power_profile : "balanced"}`)) });
  h += item({ act: "box-boot", icon: "power", title: t("box_boot"), sub: t("box_boot_sub"), val: esc(t(`box_boot_${b.wake_to === "tv" ? "tv" : "home"}`)) });
  return h;
}
act("box-power", () => choose({ title: t("box_power"), key: "box.power_profile", current: (S.settings.box || {}).power_profile || "balanced",
  options: ["balanced", "performance", "quiet"].map((v) => ({ label: t(`box_power_${v}`), sub: t(`box_power_${v}_sub`), value: v })) }));
act("box-boot", () => choose({ title: t("box_boot"), key: "box.wake_to", current: (S.settings.box || {}).wake_to || "home",
  options: ["home", "tv"].map((v) => ({ label: t(`box_boot_${v}`), sub: t(`box_boot_${v}_sub`), value: v })) }));

/* Programmtaste P+/P− auf der Oberfläche: ab dem zuletzt gesehenen Sender weiterschalten */
function nextChannel(step) {
  if (!S.channels.length) return null;
  const last = (chPrefs().recent || [])[0];
  const i = S.channels.findIndex((c) => c.name === last);
  return S.channels[i < 0 ? 0 : (i + step + S.channels.length) % S.channels.length].name;
}

/* "ZDF HD wird eingeschaltet …" – solange Kodi unsichtbar lädt; verschwindet, sobald das Bild läuft */
const TvLoading = {
  timer: null,
  show(name) {
    let el = $("#tv-loading");
    if (!el) { el = document.createElement("div"); el.id = "tv-loading"; el.className = "tv-loading"; document.body.appendChild(el); }
    const ch = S.channels.find((c) => c.name === name) || { name };
    el.innerHTML = `<span class="tl-logo">${name ? chLogo(ch) : icon("tv")}</span><span class="tl-text">${esc(name ? t("tv_switching", { name }) : t("tv_starting"))}</span><span class="tl-spin"></span>`;
    void el.offsetWidth; el.classList.add("show");
    clearTimeout(this.timer); this.timer = setTimeout(() => this.hide(), 35000);
  },
  hide() { clearTimeout(this.timer); const el = $("#tv-loading"); if (el) el.classList.remove("show"); },
};
