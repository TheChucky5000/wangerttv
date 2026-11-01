# WangertTV

Eigenes TV-Box-Betriebssystem für Mini-PCs – Startbildschirm im Stil von Google TV, komplett mit der
Fernbedienung bedienbar. Gebaut auf **Debian 13 „trixie“** mit eigener Oberfläche, optimiert für den
**Geekom Mini IT11** (Intel Core 11. Gen, Iris Xe, Wi-Fi 6, Bluetooth 5.2), läuft aber auf praktisch jedem
x86-64-Mini-PC mit Intel- oder AMD-Grafik.

| | |
|---|---|
| **Name im Netzwerk** | „WangertTV Box“ (AirPlay, Spotify Connect, Bluetooth, Kodi, HDMI-CEC, Android) |
| **Start** | Bootanimation → direkt die TV-Oberfläche, **kein Anmeldebildschirm** |
| **Erster Start** | Einrichtungsassistent wie bei Android TV |
| **Live-TV** | Kodi 21 mit IPTV Simple Client (M3U + EPG), öffnet sich über „Live-TV“ |
| **Vorinstalliert** | YouTube, Netflix, Spotify, Live-TV, Mediathek, Play Store, Browser |

---

## Was die Box kann (Geekom Mini IT11)

**Bild**
- Ausgabe bis **4K UHD mit 60 Hz** über HDMI 2.0 (Oberfläche wird automatisch passend skaliert)
- Hardware-Dekodierung (Intel Iris Xe, VA-API): **H.264, H.265/HEVC 8/10-bit, VP9 8/10-bit, AV1 8/10-bit, MPEG-2, VC-1** – 4K-Filme ruckelfrei
- Alles andere (z. B. MPEG-4/DivX/XviD, ältere Formate) dekodiert ffmpeg in Software
- Kodi passt die Bildwiederholrate automatisch an den Film an (24p/50 Hz/60 Hz)
- HDR10/HLG und Dolby Vision (Profil 7/8): werden abgespielt, aber auf SDR umgerechnet – echtes HDR-Durchreichen ist unter
  Wayland noch nicht möglich (siehe „Ideen für später“)

**Ton**
- Dekodierung: **AAC, MP3, FLAC, ALAC, Opus, Vorbis, PCM, Dolby Digital (AC3), Dolby Digital Plus (E-AC3), Dolby TrueHD, DTS, DTS-HD MA**
- **Dolby Atmos und DTS:X**: werden in Kodi **bitgenau per HDMI an einen Atmos-/DTS:X-fähigen AV-Receiver oder eine Soundbar
  durchgereicht** (Einstellungen → Bild & Ton → „Raumklang an AV-Receiver durchreichen“). Das läuft über PipeWire
  (IEC958-Passthrough am HDMI-Ausgang), andere Apps bleiben dabei hörbar.
- Ohne Receiver: Atmos/DTS:X werden als normales 5.1/7.1 wiedergegeben (die Höhenkanäle kann nur ein Receiver/eine Soundbar berechnen)
- **Lautsprecher**: Stereo, 5.1 oder 7.1 einstellbar (schaltet den HDMI-Ausgang und Kodi passend um)
- Streaming im Browser (Netflix, Prime, Disney+): dort liefern die Anbieter unter Linux nur Stereo – Atmos gibt es da nicht

**Installierte Codecs & Treiber**: ffmpeg mit `libavcodec-extra`, GStreamer (base/good/bad/ugly/libav, VA-API),
Intel `intel-media-va-driver-non-free` + `va-driver-all` (Intel/AMD), oneVPL/Quick Sync, Intel-Grafik- und Ton-Firmware,
dav1d (AV1), libbluray, libdvdnav, Google Chrome mit Widevine (DRM für Netflix & Co.)

**Streaming**
- **YouTube** in der TV-Oberfläche (bis 4K), **Netflix**, **Prime Video**, **Disney+**, **Joyn**, **Twitch** über Google Chrome
  – Hinweis: Netflix & Co. erlauben unter Linux im Browser **höchstens 720p** (Kopierschutz Widevine L3). Das ist eine Vorgabe der Anbieter.
- **Spotify** (App) + **Spotify Connect** (Box als Lautsprecher in der Spotify-App, Premium nötig)
- **AirPlay**: iPhone/iPad/Mac-Bildschirm samt Ton auf den Fernseher spiegeln
- **Mediatheken**: ARD, ZDF, arte, 3sat … über MediathekView in Kodi; **Plex** und **Jellyfin** als App installierbar
- **Android-Apps aus dem Google Play Store** (Waydroid). Einmalige Registrierung als „nicht zertifiziertes Gerät“ nötig.
  Apps mit strengem Kopierschutz (z. B. Netflix-App) laufen dort meist nicht – dafür gibt es die Browser-Versionen.

**Bedienung**
- **Bluetooth-Fernbedienungen** wie die **waipu.tv-Fernbedienung** (Kopplung: *Zurück + Home* halten, bis sie rot blinkt)
- **Sondertasten anlernen**: Netflix-, YouTube-, TV-, Programm-Taste … frei belegen
- **HDMI-CEC** (mit Pulse-Eight USB-CEC-Adapter): normale TV-Fernbedienung steuert **alle** Apps, Fernseher geht mit an/aus
- **Aus-Taste = Standby**: Wiedergabe stoppt, Bild + Fernseher aus; erneut drücken → alles wieder an
- **Texteingabe per Handy**: QR-Code scannen und lange Texte (WLAN-Passwort, IPTV-Adresse) am Handy tippen
- Farbtasten: Rot = Live-TV, Grün = TV-Programm, Gelb = Apps, Blau = Einstellungen
- Menü-Taste oder OK lange drücken: App verschieben, entfernen, beenden

**Komfort**
- **Updater für alles** mit einem Klick: System, Kodi, Chrome, Spotify/Flatpak-Apps, Android und WangertTV selbst – optional automatisch nachts
- **6 Designs**: WangertTV (Cyan & Tiefblau), OLED-Schwarz, Polarlicht, Sonnenuntergang, Neon, Graphit
- **Bildschirmschoner** mit Uhr, Wetter und Bildern (eingebaute Kunst, Fotos aus dem Internet oder eigene Fotos)
- Wetter auf der Startseite (Open-Meteo, ohne Konto), Sleep-Timer, Deutsch/Englisch, Werksreset
- Offizielle App-Symbole: werden automatisch von den Apps/Diensten selbst übernommen

---

## 1. ISO bauen

Du brauchst **Docker** (unter Windows: Docker Desktop mit WSL2) oder ein **Debian 13**.

```bash
git clone <dein-repo> wangerttv && cd wangerttv
./build.sh            # baut out/wangerttv-1.0.0-amd64.iso (ca. 20–40 Minuten)
./build.sh deb        # nur das WangertTV-Paket
```

Alternativ ohne eigenen Rechner: Projekt auf GitHub hochladen → **Actions → „WangertTV bauen“ → Run workflow**.
Die fertige ISO liegt danach unter den Artefakten des Laufs.

## 2. Auf USB-Stick schreiben und installieren

1. ISO mit **Rufus** (Windows, Modus „DD-Image“) oder **balenaEtcher** auf einen USB-Stick (≥ 8 GB) schreiben.
2. Geekom einschalten, sofort mehrmals **F7** (Bootmenü) drücken und den USB-Stick (UEFI) wählen.
   Falls er nicht startet: im BIOS (**Entf** direkt nach dem Einschalten) **Secure Boot** deaktivieren.
3. WangertTV startet mit Bootanimation → Assistent → **„Auf internen Speicher installieren“** → SSD wählen.
   ⚠️ Die SSD wird dabei komplett gelöscht (auch Windows).
4. Nach etwa 5–10 Minuten: USB-Stick abziehen, neu starten → der **Einrichtungsassistent** erscheint auf dem Fernseher.

Für die Installation reicht die Fernbedienung oder eine USB-Tastatur. Wartungs-Passwort des Benutzers `tv`: **wangerttv**
(bitte nach der Einrichtung mit `passwd` ändern – Terminal: **Strg + Alt + T**).

## 3. Einrichtungsassistent

Sprache → Netzwerk (LAN/WLAN) → Fernbedienung koppeln → Gerätename, Zeitzone, Wetter-Ort → Bild & Ton →
Apps für die Startseite → Live-TV (M3U/EPG) → Google Play Store → AirPlay/Spotify Connect, Design, Bildschirmschoner, Updates → Fertig.

## 4. Wichtige Hinweise

**waipu.tv-Fernbedienung:** *Zurück* und *Home* gleichzeitig halten, bis sie rot blinkt → Einstellungen → Fernbedienung → „Nach Geräten suchen“.
Die Sprachsuche (Mikrofon) funktioniert nicht – das ist ein Google-eigenes Verfahren. Die Infrarot-Funktionen für den Fernseher (Ein/Aus, Lautstärke) laufen weiter.

**HDMI-CEC:** Der Geekom hat kein eingebautes CEC. Mit einem **Pulse-Eight USB-CEC-Adapter** (zwischen PC und TV) wird alles automatisch erkannt.

**Live-TV:** Du brauchst eine M3U-Senderliste (IPTV-Anbieter, FRITZ!Box Cable, eigener TVHeadend …). waipu.tv selbst liefert keine M3U-Liste.

**Play Store:** Einstellungen → Android & Play Store → einrichten (ca. 1 GB). Danach Geräte-ID abrufen und unter
google.com/android/uncertified eintragen (QR-Code wird angezeigt).

**Eigene App-Symbole / Apps:** Bilder nach `/etc/wangerttv/icons/<app-id>.png` (quadratisch) oder `<app-id>-banner.png` (16:9).
Eigene Apps als JSON nach `/etc/wangerttv/apps.d/` (Beispiel liegt dort).

**WangertTV selbst aktualisieren:** In `/etc/wangerttv/update.conf` dein GitHub-Repo eintragen (`WTV_REPO=benutzer/wangerttv`).
Bei jedem Versions-Tag (`git tag v1.0.1 && git push --tags`) baut GitHub ein Release – die Boxen holen es sich über „Alles aktualisieren“.

---

## Technik

```
greetd (Autologin) ─► sway (Vollbild, je App eine Arbeitsfläche)
                       ├─ wtvd  (Python, 127.0.0.1:8765) ── API + Oberfläche
                       │    └─ Startbildschirm: Google Chrome im Kiosk-Modus
                       ├─ Apps: Chrome-Web-Apps, Kodi, Flatpak, Waydroid
                       └─ UxPlay (AirPlay), librespot (Spotify Connect)
wtv-remote (root) ─ HDMI-CEC (cec-client) + Bluetooth-Tasten → virtuelle Tastatur / Aktionen
wtv-update (root) ─ apt + Flatpak + Waydroid + WangertTV-Releases
wtv-install (root) ─ Installation Live-Stick → SSD (GPT, UEFI, GRUB mit shim)
Plymouth ─ Bootanimation „wangerttv“
```

| Pfad | Inhalt |
|---|---|
| `package/` | Inhalt des Pakets `wangerttv_<version>_all.deb` |
| `package/usr/lib/wangerttv/wtv/` | Hintergrunddienst (Python, nur Standardbibliothek) |
| `package/usr/lib/wangerttv/ui/` | Oberfläche (HTML/CSS/JS, ohne Frameworks) |
| `package/usr/lib/wangerttv/bin/` | Systemprogramme (Remote, Updater, Installer, Android …) |
| `package/usr/lib/wangerttv/apps.json` | App-Katalog |
| `live/` | live-build-Konfiguration der ISO |
| `tests/` | `python3 -m unittest discover tests` und `node tests/check_ui.js` |

**Oberfläche am PC testen** (ohne TV-Box, mit Testdaten):
```bash
cd package/usr/lib/wangerttv
WTV_DEV=1 WTV_LIB=$PWD WTV_CONF=/tmp/wtv/conf WTV_CACHE=/tmp/wtv/cache WTV_STATE=/tmp/wtv/state python3 -m wtv
# dann im Browser http://127.0.0.1:8765 öffnen (1920×1080) und mit Pfeiltasten/Enter/Esc bedienen
```

**Logs auf der Box:** `~/.cache/wangerttv/logs/` (Oberfläche, Kodi, Apps), `/var/log/wangerttv-update.log`,
`/var/log/wangerttv-install.log`, `journalctl -u wtv-remote`.

## Ideen für später
- Handy als vollwertige Fernbedienung (Webseite mit Steuerkreuz)
- Kindersicherung mit PIN
- Gaming: RetroArch, Moonlight (PC-Spiele streamen)
- Kodi-HDR-Modus (Kodi exklusiv im GBM-Modus für echtes HDR10)
- Kodi 22 „Piers“, sobald er stabil in Debian ankommt (kommt dann über den Updater)
