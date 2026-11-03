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

**Fernsehen aus einem Guss (ab 1.0.38)**
- Eigenes **TV-Programm** (Raster mit Jetzt-Linie, Farbtasten: Rot = jetzt, Grün = 20:15, Gelb = +1 Tag, Blau = Sender sortieren,
  Zifferntasten = Sendernummer) – Kodi dient nur noch als unsichtbare Wiedergabe
- **Info-Leiste beim Umschalten** im WangertTV-Stil: Sender, Sendung, Fortschritt, Danach – dazu **Bildformat
  (SD/HD/Full HD/UHD, HDR10/HDR10+/Dolby Vision/HLG, Codec)** und **Tonformat (z. B. Dolby Digital Plus 5.1, „Bitstream“
  = unverändert an den Receiver)**; schnelles Durchschalten mit Vorschau, Zifferneingabe mit den eigenen Sendernummern
- **Sender sortieren & ausblenden** (am Fernseher und in der Web-Oberfläche), **offizielle Senderlogos**, HD/UHD-Abzeichen
- **Intelligente Startseite**: Begrüßung nach Tageszeit, „Jetzt für dich“, „Deine Sendungen“ mit „Neue Folge“,
  „Heute für dich im TV“, Morgenmodus (Wetter, Nachrichten, Morgenradio), automatische Erinnerungen – alles lernt nur lokal

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
- **Spotify** (Web-Player, mit Fernbedienung bedienbar) + **Spotify Connect** (Box als Lautsprecher in der Spotify-App, Premium nötig).
  Die Spotify-Desktop-App gibt es zusätzlich unter „Alle Apps“ (nur mit Maus & Tastatur sinnvoll).
- **AirPlay**: iPhone/iPad/Mac-Bildschirm samt Ton auf den Fernseher spiegeln
- **Mediatheken**: ARD, ZDF, arte, 3sat … über MediathekView in Kodi; **Plex** und **Jellyfin** als App installierbar
- **Android-Apps aus dem Google Play Store** (Waydroid). Einmalige Registrierung als „nicht zertifiziertes Gerät“ nötig.
  Apps mit strengem Kopierschutz (z. B. Netflix-App) laufen dort meist nicht – dafür gibt es die Browser-Versionen.

**Bedienung**
- **Bluetooth-Fernbedienungen** wie die **waipu.tv-Fernbedienung** (Kopplung: *Zurück + Home* halten, bis sie rot blinkt)
- **Sondertasten anlernen**: Netflix-, YouTube-, TV-, Programm-Taste … frei belegen
- **HDMI-CEC** (mit Pulse-Eight USB-CEC-Adapter): normale TV-Fernbedienung steuert **alle** Apps, Fernseher geht mit an/aus
- **Aus-Taste = Standby**: Wiedergabe stoppt, Bild + Fernseher aus; erneut drücken → alles wieder an
- **Web-Apps mit Fernbedienung** (Netflix, Spotify, Prime Video, Disney+ …): Pfeiltasten springen zwischen Schaltflächen
  und Feldern (blauer Rahmen), **OK öffnet in Eingabefeldern eine Bildschirmtastatur**. Läuft ein Video im Vollbild,
  gehen die Tasten direkt an den Player.
- **Mausmodus** (Android/Play Store, schwierige Webseiten): Pfeiltasten bewegen einen Mauszeiger, OK klickt.
  In Android automatisch an; überall sonst **OK lange drücken** zum Umschalten. Auch über die Handy-Fernbedienung (🖱).
- **Text vom Handy in Android** (z. B. Google-Anmeldung im Play Store): Handy-Fernbedienung → „Text senden“.
- **Automatischer Standby** nach 4 Stunden ohne Tastendruck (einstellbar, 5 Minuten vorher ein Hinweis).
- **Notausgang**: *Zurück* 1,5 Sekunden gedrückt halten oder *Home* drücken → Startseite, egal was gerade läuft
- **Handy als Fernbedienung** (Einstellungen → Fernbedienung): QR-Code scannen – Steuerkreuz, OK, Zurück, Home,
  Lautstärke, Ein/Aus und **Text direkt in Netflix & Co. tippen**. Ohne App, im selben WLAN.
- **Web-Oberfläche für den PC** (Einstellungen → Gerät → Web-Oberfläche): Im Browser `http://<IP der Box>:8766/admin`
  öffnen und mit der 6-stelligen PIN vom Fernseher anmelden. Dort lassen sich Live-TV (waipu.tv, M3U/EPG), Apps und
  Reihenfolge, Ton, Gerätename, Design, CEC, Bildschirmschoner und Updates bequem einstellen und Apps am TV starten.
  Passwörter werden nie angezeigt; Werksreset und Installation sind dort bewusst nicht möglich.
- **Infrarot mit der ESP32-S3-BOX-3** (Ordner `esp32-ir`): Die BOX-3 mit Sensor-Dock per USB an die Box – sie empfängt
  die Ein/Aus-Taste der Fernbedienung, schaltet Fernseher und Receiver per Infrarot und weckt die Box aus dem
  Ruhezustand. Einlernen unter Einstellungen → Fernbedienung → Infrarot. Anleitung: `esp32-ir/README.md`.
- **Ruhezustand ohne BIOS-Option:** WangertTV nutzt „Suspend-to-Idle“ (s2idle). Dabei bleiben USB und Bluetooth
  wach – die BOX-3 weckt die Box auch dann, wenn das BIOS kein „Wake on USB“ anbietet (ca. 1–2 W im Ruhezustand).
- **SSH-Zugang** (Einstellungen → Gerät → SSH-Zugang, auch in der Web-Oberfläche): standardmäßig **aus**. Beim
  Einschalten erzeugt die Box ein zufälliges Passwort für den Benutzer `tv` und zeigt es am Fernseher an
  (gilt auch für `sudo`). Anmelden am PC: `ssh tv@<IP der Box>` (Windows: PowerShell). Root-Anmeldung ist gesperrt.
- **Texteingabe per Handy**: QR-Code scannen und lange Texte (WLAN-Passwort, IPTV-Adresse) am Handy tippen
- Farbtasten: Rot = Live-TV, Grün = TV-Programm, Gelb = Apps, Blau = Einstellungen
- Menü-Taste oder OK lange drücken: App verschieben, entfernen, beenden oder **neu starten** (wenn sie hängt)

**Ton**
- **Receiver-Erkennung** (Einstellungen → Bild & Ton): zeigt, welcher AV-Receiver/Fernseher angeschlossen ist und welche
  Formate er kann (z. B. 7.1 PCM, Dolby Digital, DTS, Dolby TrueHD/Atmos, DTS-HD). „Passend zum Receiver einstellen“
  setzt Lautsprecher und Durchreichen mit einem Klick. Kodi reicht nur Formate durch, die der Receiver wirklich kann.
- Hinweis: Der Receiver zeigt als Quelle „Integrated Gfx“ – das ist nur der feste Name des Intel-Grafiktreibers.

**Stabilität**
- Kodi: großer Puffer für Streams, längere Netzwerk-Wartezeiten, Tonverbindung zum Receiver bleibt nach Pausen offen;
  hängt Kodi 45 Sekunden, wird es neu gestartet – nach einem Absturz ebenso (Protokoll unter
  `~/.cache/wangerttv/logs/kodi-crash-*.log`).
- Updates: Die Oberfläche startet danach sauber neu; kommt sie nicht zurück, startet die Box selbst neu.

**Komfort**
- **Updater für alles** mit einem Klick: System, Kodi, Chrome, Spotify/Flatpak-Apps, Android und WangertTV selbst – optional automatisch nachts
- **6 Designs**: WangertTV (Cyan & Tiefblau), OLED-Schwarz, Polarlicht, Sonnenuntergang, Neon, Graphit
- **Bildschirmschoner** mit Uhr, Wetter und Bildern (eingebaute Kunst, Fotos aus dem Internet oder eigene Fotos)
- Wetter auf der Startseite (Open-Meteo, ohne Konto), Sleep-Timer, Deutsch/Englisch, Werksreset
- Offizielle App-Symbole: werden automatisch von den Apps/Diensten selbst übernommen

---

## 1. ISO bauen

### Unter Windows (am einfachsten)
1. **Docker Desktop** installieren (https://www.docker.com/products/docker-desktop/), PC neu starten, Docker Desktop einmal öffnen.
2. Das Projekt z. B. nach `C:\WangertTV` entpacken (**nicht** in OneDrive), ca. 20 GB frei.
3. **`WangertTV-bauen.bat` doppelklicken.** Das Fenster zeigt den Fortschritt:

```
==> ISO-Build gestartet – dauert ca. 20–40 Minuten. Fortschritt in Schritten 1–7:
[1/7] Debian-13-Grundsystem wird heruntergeladen      (nach 0 min)
[2/7] Pakete werden installiert (Kodi, Codecs, …)     (nach 2 min)
      Pakete:  40 %  (298 von 745)
[3/7] Google Chrome, Spotify, Android werden eingerichtet
[4/7] WangertTV wird installiert, Bootanimation …
[5/7] System wird komprimiert – längster Schritt (10–25 Minuten)
      … läuft noch – Schritt 5 seit 6 min (gesamt 14 min)
[6/7] Startmenü (UEFI + BIOS) und ISO-Datei werden erstellt
[7/7] Fertig
```

Danach öffnet sich der Ordner `out` mit `wangerttv-1.0.0-amd64.iso`.
`WangertTV-bauen.bat details` zeigt zusätzlich alle Meldungen; das komplette Protokoll steht immer in `out\build-iso.log`.

### Unter Linux
```bash
./build.sh            # Paket + ISO (Docker oder Debian 13 als root)
./build.sh deb        # nur das WangertTV-Paket
WTV_VERBOSE=1 ./build.sh   # alle Meldungen anzeigen
```

### Ohne eigenen Rechner
Projekt auf GitHub hochladen → **Actions → „WangertTV bauen“ → Run workflow**. Die ISO liegt danach unter den Artefakten des Laufs.

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

**Live-TV:** Zwei Wege, beide unter Einstellungen → Live-TV (oder im Einrichtungsassistenten):
- **waipu.tv** (auch O2 TV): E-Mail + Passwort eintragen – WangertTV richtet den waipu.tv-PVR-Client in Kodi ein und
  übernimmt den Kopierschutz (Widevine) aus Google Chrome.
- **IPTV-Senderliste (M3U)**: von einem IPTV-Anbieter, einer FRITZ!Box Cable, TVHeadend …
Weitere PVR-Clients (Zattoo, TVHeadend, Enigma2/VU+, HDHomeRun, VDR, Octonet) sind vorinstalliert und in Kodi unter
Add-ons → Meine Add-ons → PVR-Clients zu finden. (Unter Linux bietet das Kodi-Repository keine PVR-Clients an – sie kommen aus Debian.)

**Play Store:** Einstellungen → Android & Play Store → einrichten (ca. 1 GB). Danach Geräte-ID abrufen und unter
google.com/android/uncertified eintragen (QR-Code wird angezeigt). 10–30 Minuten warten, dann **„Android neu starten“**.
Der erste Start von Android dauert bis zu 2 Minuten. Protokoll bei Problemen: `~tv/.cache/wangerttv/logs/android-launch.log`.

**Eigene App-Symbole / Apps:** Bilder nach `/etc/wangerttv/icons/<app-id>.png` (quadratisch) oder `<app-id>-banner.png` (16:9).
Eigene Apps als JSON nach `/etc/wangerttv/apps.d/` (Beispiel liegt dort).

**Updates – die Box aktualisiert sich selbst:**
- System, Kodi, Chrome, Codecs, Treiber, Flatpak-Apps (Spotify, Plex …) und Android werden **jede Nacht um 4 Uhr**
  automatisch aktualisiert (Einstellungen → Updates → „Automatische Updates“). Alle 12 Stunden wird nach Neuem gesucht.
- **WangertTV selbst** kommt aus deinem GitHub-Repository (muss **öffentlich** sein):
  1. Projekt auf GitHub hochladen.
  2. **Releases → „Draft a new release“ → Tag z. B. `v1.0.3` → „Publish release“.** GitHub baut dann automatisch
     (ca. 40 Minuten) und hängt `wangerttv_1.0.3_all.deb` an das Release. Die Versionsnummer kommt aus dem Tag.
  3. Boxen, die aus deinem GitHub-Build installiert wurden, kennen ihr Repository automatisch. Bei anderen einmal unter
     **Einstellungen → Updates → „WangertTV-Updates (GitHub)“** `benutzer/repository` eintragen.
- Braucht ein Update einen Neustart, startet die Box nachts **nur im Standby** leise neu und bleibt danach im Standby –
  der Fernseher geht dabei nicht an. Wird sie gerade benutzt, erscheint stattdessen ein Hinweis unter Updates.

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
