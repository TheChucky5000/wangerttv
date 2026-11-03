/* WangertTV – Fernbedienungs-Steuerung für Webseiten (Netflix, Spotify, Prime Video, Disney+, …)
 *
 * Wird von WangertTV in Web-Apps eingespielt:
 *   Pfeiltasten  springen zwischen Schaltflächen, Links und Eingabefeldern (sichtbarer Rahmen)
 *   OK           klickt bzw. öffnet bei Eingabefeldern die Bildschirmtastatur
 *   Zurück/Esc   schließt die Tastatur, sonst Seite zurück
 * Läuft ein Video im Vollbild, gehen alle Tasten unverändert an den Player (Pause, Spulen, …).
 * Echte Tastaturen funktionieren weiterhin normal.
 */
(() => {
  "use strict";
  if (window.top !== window || window.__wtvNav) return;
  window.__wtvNav = true;

  const LANG = String(window.__WTV_LANG || navigator.language || "de").slice(0, 2);
  const TXT = {
    de: { done: "Fertig", space: "Leerzeichen", hint: "Pfeile: wählen · OK: öffnen · Zurück lang drücken: Startseite",
          sym: "&123", abc: "ABC", empty: "(leer)" },
    en: { done: "Done", space: "Space", hint: "Arrows: select · OK: open · Hold Back: home screen",
          sym: "&123", abc: "ABC", empty: "(empty)" },
  }[LANG === "de" ? "de" : "en"];

  const SELECTOR = [
    "a[href]", "button", "input:not([type=hidden])", "select", "textarea", "summary", "iframe",
    "[tabindex]:not([tabindex='-1'])", "[role=button]", "[role=link]", "[role=tab]", "[role=menuitem]",
    "[role=option]", "[role=checkbox]", "[role=radio]", "[role=switch]", "[role=treeitem]",
    "[contenteditable='']", "[contenteditable='true']",
  ].join(",");
  const TEXT_TYPES = new Set(["", "text", "email", "password", "search", "tel", "url", "number"]);
  const NAV_KEYS = { ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left", ArrowRight: "right" };

  let cur = null;          // aktuell markiertes Element
  let osk = null;          // offene Bildschirmtastatur

  // ------------------------------------------------------------------ Darstellung
  const STYLE = `
    .wtv-focus{outline:4px solid #3ea6ff!important;outline-offset:3px!important;
      box-shadow:0 0 0 9px rgba(62,166,255,.35)!important;border-radius:6px;transition:outline-offset .12s}
    #wtv-osk{all:initial;position:fixed;left:0;right:0;bottom:0;z-index:2147483647;
      background:rgba(8,14,26,.97);border-top:2px solid #3ea6ff;padding:18px 3vw 22px;
      font:600 22px/1.2 Inter,Roboto,"Segoe UI",Arial,sans-serif;color:#fff;box-shadow:0 -10px 40px rgba(0,0,0,.6)}
    #wtv-osk *{box-sizing:border-box;font:inherit;color:inherit}
    #wtv-osk .wtv-prev{display:flex;gap:16px;align-items:baseline;margin:0 auto 14px;max-width:1300px}
    #wtv-osk .wtv-label{color:#8fb8e8;font-size:18px;white-space:nowrap}
    #wtv-osk .wtv-val{flex:1;background:#111c30;border:2px solid #2b4266;border-radius:10px;padding:10px 14px;
      min-height:48px;overflow:hidden;white-space:pre;text-overflow:ellipsis;font-weight:500}
    #wtv-osk .wtv-val.empty{color:#6b7d99}
    #wtv-osk .wtv-row{display:flex;gap:8px;justify-content:center;margin:8px auto 0;max-width:1300px}
    #wtv-osk .wtv-key{flex:1 1 0;min-width:0;height:58px;border-radius:10px;background:#1b2a44;border:2px solid transparent;
      display:flex;align-items:center;justify-content:center;cursor:pointer;user-select:none}
    #wtv-osk .wtv-key.w2{flex-grow:2}#wtv-osk .wtv-key.w5{flex-grow:5}
    #wtv-osk .wtv-key.act{background:#24406b}
    #wtv-osk .wtv-key.on{background:#3ea6ff;color:#04101f;border-color:#bfe2ff;transform:scale(1.06)}
    #wtv-hint{all:initial;position:fixed;left:50%;top:28px;transform:translateX(-50%);z-index:2147483647;
      background:rgba(8,14,26,.92);color:#fff;border:1px solid #3ea6ff;border-radius:999px;padding:12px 26px;
      font:500 19px/1.2 Inter,Roboto,Arial,sans-serif;transition:opacity .6s;pointer-events:none}`;

  function ensureStyle() {
    if (document.getElementById("wtv-style")) return;
    const st = document.createElement("style");
    st.id = "wtv-style";
    st.textContent = STYLE;
    (document.head || document.documentElement).appendChild(st);
  }

  // ------------------------------------------------------------------ Elemente finden
  function collect(root, out) {
    root.querySelectorAll(SELECTOR).forEach((el) => out.push(el));
    root.querySelectorAll("*").forEach((el) => { if (el.shadowRoot) collect(el.shadowRoot, out); });
    return out;
  }

  function deepActive() {
    let a = document.activeElement;
    while (a && a.shadowRoot && a.shadowRoot.activeElement) a = a.shadowRoot.activeElement;
    return a && a !== document.body && a !== document.documentElement ? a : null;
  }

  function usable(el) {
    if (!el || !el.isConnected || el.disabled) return false;
    if (osk && osk.root.contains(el)) return false;
    if (el.closest("[aria-hidden='true'],[inert]")) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) return false;
    const cs = getComputedStyle(el);
    if (cs.visibility === "hidden" || cs.display === "none" || Number(cs.opacity) === 0) return false;
    if (cs.pointerEvents === "none" && el.tagName !== "IFRAME") return false;
    return true;
  }

  function candidates() {
    return collect(document, []).filter(usable).filter((el) => {
      // Container-Elemente überspringen, wenn sie selbst bedienbare Elemente enthalten (z. B. Kachel mit Link)
      return !(el.matches("[tabindex]") && !el.matches("a,button,input,select,textarea,[role]") &&
               el.querySelector("a[href],button,[role=button],[role=link]"));
    });
  }

  function isText(el) {
    if (!el) return false;
    if (el.isContentEditable) return true;
    if (el.tagName === "TEXTAREA") return true;
    return el.tagName === "INPUT" && TEXT_TYPES.has((el.getAttribute("type") || "").toLowerCase());
  }

  // ------------------------------------------------------------------ Markierung
  function mark(el, scroll = true) {
    if (cur && cur !== el) cur.classList.remove("wtv-focus");
    cur = el;
    if (!el) return;
    ensureStyle();
    el.classList.add("wtv-focus");
    try { el.focus({ preventScroll: true }); } catch (e) { /* nicht fokussierbar */ }
    if (scroll) {
      const r = el.getBoundingClientRect();
      const vh = window.innerHeight, vw = window.innerWidth;
      if (r.top < 70 || r.bottom > vh - 40 || r.left < 0 || r.right > vw) {
        el.scrollIntoView({ block: "center", inline: "nearest", behavior: "smooth" });
      }
    }
  }

  function current() {
    const a = deepActive();
    if (a && a !== cur && usable(a) && a.matches(SELECTOR)) mark(a, false);
    if (cur && !usable(cur)) { cur.classList.remove("wtv-focus"); cur = null; }
    return cur;
  }

  function score(dir, a, b) {
    const ax = (a.left + a.right) / 2, ay = (a.top + a.bottom) / 2;
    const bx = (b.left + b.right) / 2, by = (b.top + b.bottom) / 2;
    let main, side, overlap;
    if (dir === "right" || dir === "left") {
      main = dir === "right" ? b.left - a.right : a.left - b.right;
      if ((dir === "right" ? bx <= ax + 1 : bx >= ax - 1)) return null;
      overlap = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
      side = Math.abs(by - ay);
    } else {
      main = dir === "down" ? b.top - a.bottom : a.top - b.bottom;
      if ((dir === "down" ? by <= ay + 1 : by >= ay - 1)) return null;
      overlap = Math.min(a.right, b.right) - Math.max(a.left, b.left);
      side = Math.abs(bx - ax);
    }
    main = Math.max(main, -Math.min(a.width, a.height) / 2);
    return main + (overlap > 0 ? side * 0.3 : side * 2.5 + 200);
  }

  function move(dir) {
    const from = current();
    const list = candidates();
    if (!from) {
      // erstes sichtbares Element oben links markieren
      const vis = list.filter((el) => { const r = el.getBoundingClientRect(); return r.bottom > 0 && r.top < innerHeight; });
      vis.sort((x, y) => { const a = x.getBoundingClientRect(), b = y.getBoundingClientRect();
        return (a.top - b.top) || (a.left - b.left); });
      if (vis.length) { mark(vis[0]); return true; }
      return scrollPage(dir);
    }
    const a = from.getBoundingClientRect();
    let best = null, bestScore = Infinity;
    for (const el of list) {
      if (el === from || el.contains(from)) continue;
      const s = score(dir, a, el.getBoundingClientRect());
      if (s !== null && s < bestScore) { best = el; bestScore = s; }
    }
    if (best) { mark(best); return true; }
    return scrollPage(dir);
  }

  function scrollPage(dir) {
    const dy = dir === "down" ? 0.4 : dir === "up" ? -0.4 : 0;
    const dx = dir === "right" ? 0.4 : dir === "left" ? -0.4 : 0;
    window.scrollBy({ top: dy * innerHeight, left: dx * innerWidth, behavior: "smooth" });
    return true;
  }

  function activate(el) {
    if (isText(el)) { openOsk(el); return; }
    if (el.tagName === "SELECT") {
      try { el.showPicker(); } catch (e) { el.focus(); }
      return;
    }
    if (el.tagName === "IFRAME") return;
    el.click();
  }

  // ------------------------------------------------------------------ Video-Modus
  function videoMode() {
    if (document.fullscreenElement) return true;
    const vids = document.querySelectorAll("video");
    const area = innerWidth * innerHeight;
    for (const v of vids) {
      const r = v.getBoundingClientRect();
      const visible = Math.max(0, Math.min(r.right, innerWidth) - Math.max(r.left, 0)) *
                      Math.max(0, Math.min(r.bottom, innerHeight) - Math.max(r.top, 0));
      if (visible >= area * 0.8 && (v.currentTime > 0 || !v.paused)) return true;
    }
    return false;
  }

  // ------------------------------------------------------------------ Bildschirmtastatur
  const LAYOUTS = {
    abc: [
      ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "ß"],
      ["q", "w", "e", "r", "t", "z", "u", "i", "o", "p", "ü"],
      ["a", "s", "d", "f", "g", "h", "j", "k", "l", "ö", "ä"],
      ["⇧", "y", "x", "c", "v", "b", "n", "m", "-", "_", "⌫"],
    ],
    sym: [
      ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "€"],
      ["!", "?", "#", "$", "%", "&", "*", "(", ")", "/", "\\"],
      ["+", "=", ":", ";", ",", "'", "\"", "~", "<", ">", "|"],
      ["[", "]", "{", "}", "^", "`", "§", "°", "²", "³", "⌫"],
    ],
  };

  function nativeSet(el, value) {
    const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
    setter.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
  }

  function caret(el) {
    try { if (typeof el.selectionStart === "number") return [el.selectionStart, el.selectionEnd]; } catch (e) { /* email/number */ }
    const n = (el.value || "").length;
    return [n, n];
  }

  function setCaret(el, pos) {
    try { el.setSelectionRange(pos, pos); } catch (e) { /* nicht unterstützt */ }
  }

  function typeText(el, text) {
    if (el.isContentEditable) { document.execCommand("insertText", false, text); return; }
    const v = el.value || "";
    const [s, e] = caret(el);
    if (el.maxLength > 0 && v.length - (e - s) + text.length > el.maxLength) return;
    nativeSet(el, v.slice(0, s) + text + v.slice(e));
    setCaret(el, s + text.length);
  }

  function backspace(el) {
    if (el.isContentEditable) { document.execCommand("delete"); return; }
    const v = el.value || "";
    let [s, e] = caret(el);
    if (s === e) { if (s === 0) return; s -= 1; }
    nativeSet(el, v.slice(0, s) + v.slice(e));
    setCaret(el, s);
  }

  function moveCaret(el, d) {
    const [s] = caret(el);
    setCaret(el, Math.max(0, Math.min((el.value || "").length, s + d)));
  }

  function labelFor(el) {
    const id = el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
    return (el.getAttribute("aria-label") || (id && id.textContent) || el.placeholder ||
            el.name || "").trim().slice(0, 40);
  }

  function openOsk(el) {
    closeOsk(false);
    ensureStyle();
    el.focus({ preventScroll: true });
    el.scrollIntoView({ block: "start", behavior: "smooth" });
    const root = document.createElement("div");
    root.id = "wtv-osk";
    osk = { root, el, layer: "abc", shift: false, row: 1, col: 0, keys: [] };
    render();
    document.documentElement.appendChild(root);
    root.addEventListener("mousedown", (e) => e.preventDefault());
    root.addEventListener("click", (e) => {
      const k = e.target.closest(".wtv-key");
      if (k) { osk.row = Number(k.dataset.r); osk.col = Number(k.dataset.c); press(); }
    });
  }

  function render() {
    const o = osk;
    const rows = LAYOUTS[o.layer].map((r) => r.map((k) => (o.shift && k.length === 1 ? k.toUpperCase() : k)));
    rows.push([o.layer === "abc" ? TXT.sym : TXT.abc, "@", ".", ".com", " ", "◀", "▶", TXT.done]);
    o.keys = rows;
    o.row = Math.min(o.row, rows.length - 1);
    o.col = Math.min(o.col, rows[o.row].length - 1);
    const label = labelFor(o.el);
    const pw = (o.el.getAttribute("type") || "").toLowerCase() === "password";
    const val = o.el.isContentEditable ? o.el.textContent : (o.el.value || "");
    const shown = pw ? "•".repeat(val.length) : val;
    let h = `<div class="wtv-prev">${label ? `<span class="wtv-label"></span>` : ""}` +
            `<div class="wtv-val${val ? "" : " empty"}"></div></div>`;
    rows.forEach((r, ri) => {
      h += '<div class="wtv-row">';
      r.forEach((k, ci) => {
        const cls = ["wtv-key"];
        if (k === " ") cls.push("w5");
        else if (k.length > 1 && k !== "⌫" && k !== "⇧") cls.push("w2");
        if ((k === "⇧" && o.shift)) cls.push("act");
        if (ri === o.row && ci === o.col) cls.push("on");
        h += `<div class="${cls.join(" ")}" data-r="${ri}" data-c="${ci}"></div>`;
      });
      h += "</div>";
    });
    o.root.innerHTML = h;
    if (label) o.root.querySelector(".wtv-label").textContent = label;
    o.root.querySelector(".wtv-val").textContent = shown || TXT.empty;
    o.root.querySelectorAll(".wtv-key").forEach((d) => {
      const k = rows[d.dataset.r][d.dataset.c];
      d.textContent = k === " " ? TXT.space : k;
    });
  }

  function oskMove(dir) {
    const o = osk;
    if (dir === "left") o.col = (o.col - 1 + o.keys[o.row].length) % o.keys[o.row].length;
    else if (dir === "right") o.col = (o.col + 1) % o.keys[o.row].length;
    else {
      const keysEls = o.root.querySelectorAll(".wtv-row")[o.row].children;
      const r = keysEls[o.col].getBoundingClientRect();
      const x = (r.left + r.right) / 2;
      o.row = Math.max(0, Math.min(o.keys.length - 1, o.row + (dir === "down" ? 1 : -1)));
      const target = o.root.querySelectorAll(".wtv-row")[o.row].children;
      let best = 0, dist = Infinity;
      Array.from(target).forEach((k, i) => {
        const kr = k.getBoundingClientRect();
        const d = Math.abs((kr.left + kr.right) / 2 - x);
        if (d < dist) { dist = d; best = i; }
      });
      o.col = best;
    }
    render();
  }

  function press() {
    const o = osk;
    const k = o.keys[o.row][o.col];
    const el = o.el;
    if (k === TXT.done) { closeOsk(true); return; }
    if (k === "⌫") backspace(el);
    else if (k === "⇧") o.shift = !o.shift;
    else if (k === TXT.sym || k === TXT.abc) { o.layer = o.layer === "abc" ? "sym" : "abc"; o.shift = false; }
    else if (k === "◀") moveCaret(el, -1);
    else if (k === "▶") moveCaret(el, 1);
    else {
      typeText(el, k);
      if (o.shift && k.length === 1) o.shift = false;
    }
    render();
  }

  function closeOsk(keepMark) {
    if (!osk) return;
    const el = osk.el;
    osk.root.remove();
    osk = null;
    el.dispatchEvent(new Event("change", { bubbles: true }));
    if (keepMark) mark(el, true);
  }

  // ------------------------------------------------------------------ Tasten
  function isBack(e) {
    return e.key === "Escape" || e.key === "BrowserBack" || e.key === "GoBack";
  }

  window.addEventListener("keydown", (e) => {
    if (e.ctrlKey || e.altKey || e.metaKey) return;
    if (osk) {
      if (!osk.el.isConnected) { closeOsk(false); return; }
      if (NAV_KEYS[e.key]) { e.preventDefault(); e.stopImmediatePropagation(); oskMove(NAV_KEYS[e.key]); return; }
      if (e.key === "Enter") { e.preventDefault(); e.stopImmediatePropagation(); press(); return; }
      if (isBack(e)) { e.preventDefault(); e.stopImmediatePropagation(); closeOsk(true); return; }
      // echte Tastatur: Zeichen gehen direkt ins Feld, Vorschau aktualisieren
      setTimeout(() => osk && render(), 0);
      return;
    }
    if (videoMode()) return;
    const dir = NAV_KEYS[e.key];
    const target = deepActive();
    if (dir) {
      // in Textfeldern mit echter Tastatur bleiben links/rechts für den Cursor
      if (isText(target) && (dir === "left" || dir === "right") && (target.value || "").length && e.isTrusted && !e.repeat && window.__wtvKbd) return;
      if (target && target.tagName === "SELECT" && (dir === "up" || dir === "down") && window.__wtvKbd) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      move(dir);
      return;
    }
    if (e.key === "Enter") {
      const el = current();
      if (!el) return;
      if (isText(el)) {
        // Enter in einem Feld mit Inhalt: normal absenden (z. B. Suche), leeres Feld: Tastatur öffnen
        if ((el.value || el.textContent || "").length && window.__wtvKbd) return;
        e.preventDefault();
        e.stopImmediatePropagation();
        openOsk(el);
        return;
      }
      e.preventDefault();
      e.stopImmediatePropagation();
      activate(el);
    }
  }, true);

  // Wurde eine echte Tastatur benutzt (Buchstaben getippt), verhalten sich Textfelder wie gewohnt
  window.addEventListener("keydown", (e) => {
    if (e.key && e.key.length === 1 && !e.ctrlKey && !e.altKey) window.__wtvKbd = true;
  }, true);

  // Maus/Touch: Markierung folgt dem Klick, Tastatur schließt bei Klick daneben
  document.addEventListener("mousedown", (e) => {
    if (osk && !osk.root.contains(e.target) && e.target !== osk.el) closeOsk(false);
  }, true);

  function hint() {
    try {
      if (sessionStorage.getItem("wtv-hint")) return;
      sessionStorage.setItem("wtv-hint", "1");
    } catch (e) { return; }
    ensureStyle();
    const d = document.createElement("div");
    d.id = "wtv-hint";
    d.textContent = TXT.hint;
    document.documentElement.appendChild(d);
    setTimeout(() => { d.style.opacity = "0"; }, 5500);
    setTimeout(() => d.remove(), 6500);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => { ensureStyle(); setTimeout(hint, 1500); });
  } else {
    ensureStyle();
    setTimeout(hint, 1500);
  }
})();
