// Prüft die Oberfläche: Syntax, und ob alle verwendeten Texte in Deutsch und Englisch vorhanden sind.
// Aufruf: node tests/check_ui.js
"use strict";
const fs = require("fs");
const path = require("path");
const dir = path.join(__dirname, "..", "package/usr/lib/wangerttv/ui/assets");
const app = fs.readFileSync(path.join(dir, "app.js"), "utf8");
eval(fs.readFileSync(path.join(dir, "i18n.js"), "utf8").replace("const I18N", "global.I18N"));

const used = new Set([...app.matchAll(/\bt\("([a-z0-9_]+)"/g)].map((m) => m[1]));
const dynamic = {
  amb_: ["art", "picsum", "folder", "art_sub", "picsum_sub", "folder_sub"], job_: ["running", "done", "error"],
  power_key_: ["standby", "menu"], sleep_: ["standby", "poweroff"], inst_: ["partition", "format", "copy", "bootloader", "config", "done"],
  theme_: ["wangert", "oled", "aurora", "sunset", "neon", "graphite"].flatMap((x) => [x, `${x}_sub`]),
};
for (const [prefix, list] of Object.entries(dynamic)) list.forEach((s) => used.add(prefix + s));
const cats = app.slice(app.indexOf("const CATS = ["), app.indexOf("];", app.indexOf("const CATS = [")));
for (const m of cats.matchAll(/\["([a-z]+)", "[a-z]+"\]/g)) used.add(`cat_${m[1]}`);

let missing = 0;
for (const key of used) for (const lang of ["de", "en"]) if (!(key in I18N[lang])) { console.log(`fehlt (${lang}): ${key}`); missing++; }
const de = Object.keys(I18N.de), en = Object.keys(I18N.en);
de.filter((k) => !en.includes(k)).forEach((k) => { console.log(`nur de: ${k}`); missing++; });
console.log(`${used.size} Texte geprüft, ${missing} Probleme`);
process.exit(missing ? 1 : 0);
