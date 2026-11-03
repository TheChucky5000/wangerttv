// Prüft die Oberfläche: Syntax, und ob alle verwendeten Texte in Deutsch und Englisch vorhanden sind.
// Aufruf: node tests/check_ui.js
"use strict";
const fs = require("fs");
const path = require("path");
const dir = path.join(__dirname, "..", "package/usr/lib/wangerttv/ui/assets");
const app = ["app.js", "tv.js"].map((f) => fs.readFileSync(path.join(dir, f), "utf8")).join("\n");
eval(fs.readFileSync(path.join(dir, "i18n.js"), "utf8").replace("const I18N", "global.I18N"));

const used = new Set([...app.matchAll(/\bt\("([a-z0-9_]+)"/g)].map((m) => m[1]));
const dynamic = {
  amb_: ["art", "picsum", "folder", "art_sub", "picsum_sub", "folder_sub"], job_: ["running", "done", "error"],
  power_key_: ["standby", "menu"], pcm_: ["auto", "multi", "auto_sub", "multi_sub"],
  cec_volume_: ["auto", "avr", "box", "auto_sub", "avr_sub", "box_sub"], cec_pow_: ["on", "standby", "unknown"],
  spotify_mode_: ["librespot", "app", "librespot_sub", "app_sub"], amb_style_: ["classic", "center", "info", "night"].flatMap((x) => [x, `${x}_sub`]),
  radio_g_: ["pop", "rock", "news", "schlager", "80s", "90s", "oldies", "dance", "electronic", "hiphop", "jazz", "classical", "chillout", "metal", "country", "kids", "sport", "christmas"],
  greet_: ["morning", "day", "evening", "night"].flatMap((x) => [x, `${x}_name`]), tvset_start_: ["last", "list", "last_sub", "list_sub"],
  tvset_back_: ["live", "epg", "home"], box_power_: ["balanced", "performance", "quiet"].flatMap((x) => [x, `${x}_sub`]),
  box_boot_: ["home", "tv", "home_sub", "tv_sub"],
  accent_: ["cyan", "mint", "gold", "coral", "red", "pink", "violet", "blue", "lime", "white"], sleep_: ["standby", "poweroff"], inst_: ["partition", "format", "copy", "bootloader", "config", "done"],
  theme_: ["wangert", "oled", "aurora", "sunset", "neon", "graphite", "ocean", "forest", "rose", "midnight", "ember"].flatMap((x) => [x, `${x}_sub`]),
};
for (const [prefix, list] of Object.entries(dynamic)) list.forEach((s) => used.add(prefix + s));
const cats = app.slice(app.indexOf("const CATS = ["), app.indexOf("];", app.indexOf("const CATS = [")));
for (const m of cats.matchAll(/\["([a-z]+)", "[a-z]+"\]/g)) used.add(`cat_${m[1]}`);

// doppelte Schlüssel je Sprache (der spätere würde den früheren still überschreiben)
const src = fs.readFileSync(path.join(dir, "i18n.js"), "utf8");
for (const lang of ["de", "en"]) {
  const start = src.indexOf(`\n  ${lang}: {`), end = src.indexOf("\n  },", start);
  const seen = new Set();
  for (const m of src.slice(start, end).matchAll(/(?:^|[\s,{])([a-z0-9_]+): "/g)) {
    if (seen.has(m[1])) { console.log(`Doppelter Text-Schlüssel (${lang}): ${m[1]}`); process.exitCode = 1; }
    seen.add(m[1]);
  }
}
let missing = 0;
for (const key of used) for (const lang of ["de", "en"]) if (!(key in I18N[lang])) { console.log(`fehlt (${lang}): ${key}`); missing++; }
const de = Object.keys(I18N.de), en = Object.keys(I18N.en);
de.filter((k) => !en.includes(k)).forEach((k) => { console.log(`nur de: ${k}`); missing++; });
console.log(`${used.size} Texte geprüft, ${missing} Probleme`);
process.exit(missing ? 1 : 0);
