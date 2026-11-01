#!/bin/bash
# WangertTV – Build-Skript
#
#   ./build.sh          Paket (.deb) + ISO bauen
#   ./build.sh deb      nur das WangertTV-Paket bauen
#   ./build.sh iso      Paket + ISO bauen
#   ./build.sh clean    Build-Reste löschen
#
# Fortschritt: Der Build zeigt Schritt 1–7 mit Prozent- und Zeitangaben. Alle Details: WTV_VERBOSE=1 ./build.sh
# Vollständiges Protokoll danach in out/build-iso.log. Unter Windows einfach WangertTV-bauen.bat doppelklicken.
#
# Optional: WTV_EXTRA_CA=/pfad/ca.crt ./build.sh  (zusätzliches Zertifikat nur für den Build, z. B. Firmen-Proxy)
# Läuft auf jedem Linux mit Docker (auch WSL2 unter Windows) oder direkt auf Debian 13 mit live-build.
# Ergebnis: out/wangerttv-<version>-amd64.iso (auf USB-Stick schreiben, z. B. mit Rufus oder balenaEtcher)
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"
VERSION="$(tr -d '[:space:]' < VERSION)"
OUT="$PWD/out"
MODE="${1:-iso}"
mkdir -p "$OUT"

if [ -t 1 ]; then C1=$'\033[1;36m'; C2=$'\033[1;32m'; C3=$'\033[1;31m'; C0=$'\033[0m'; else C1=; C2=; C3=; C0=; fi
log() { printf '%s==>%s %s\n' "$C1" "$C0" "$*"; }

# Zeilenenden reparieren (falls das Projekt unter Windows entpackt/bearbeitet wurde – .bat bleibt unverändert)
fix_line_endings() {
    grep -rlI $'\r' --exclude-dir=out --exclude-dir=.git --exclude='*.bat' . 2>/dev/null | xargs -r sed -i 's/\r$//' || true
}

# Übersetzt die Ausgabe von live-build in eine verständliche Fortschrittsanzeige
progress() {
    local line rc step=0 total=7 t0=$SECONDS ts=$SECONDS newpk=0 donepk=0 lastpct=-1 pct
    stage() {
        step=$1; ts=$SECONDS
        printf '\n%s[%d/%d]%s %s   %s(nach %d min)%s\n' "$C1" "$1" "$total" "$C0" "$2" "$C1" $(( (SECONDS - t0) / 60 )) "$C0"
    }
    while true; do
        if IFS= read -r -t 60 line; then
            if [ -n "${WTV_VERBOSE:-}" ]; then printf '      %s\n' "$line"; fi
            case "$line" in
                "P: Begin bootstrapping system"*) stage 1 "Debian-13-Grundsystem wird heruntergeladen" ;;
                "P: Begin installing packages (install pass)"*)
                    stage 2 "Pakete werden installiert (Kodi, Codecs, Treiber, Firmware …)" ;;
                *" newly installed,"*)
                    if [ "$step" = 2 ] && [ "$newpk" = 0 ] && [[ "$line" =~ ([0-9]+)\ upgraded,\ ([0-9]+)\ newly ]]; then
                        newpk=$(( BASH_REMATCH[1] + BASH_REMATCH[2] )); donepk=0; lastpct=-1
                    fi ;;
                "Setting up "*)
                    if [ "$step" = 2 ] && [ "$newpk" -gt 0 ]; then
                        donepk=$(( donepk + 1 )); pct=$(( donepk * 100 / newpk )); [ "$pct" -gt 100 ] && pct=100
                        if [ $(( pct / 10 )) -gt $(( lastpct / 10 )) ]; then
                            lastpct=$pct; printf '      Pakete: %3d %%  (%d von %d)\n' "$pct" "$donepk" "$newpk"
                        fi
                    fi ;;
                "P: Executing hook config/hooks/normal/0100-wangerttv"*)
                    stage 3 "Google Chrome, Spotify, Android (Waydroid) werden geladen und eingerichtet" ;;
                "P: Executing hook config/hooks/normal/0500-wangerttv"*)
                    stage 4 "WangertTV wird installiert, Bootanimation wird eingerichtet" ;;
                "P: Preparing squashfs image"*)
                    stage 5 "System wird komprimiert – längster Schritt (ca. 10–25 Minuten ohne Zwischenstand)" ;;
                "P: Begin installing config for grub"*)
                    stage 6 "Startmenü (UEFI + BIOS) und ISO-Datei werden erstellt" ;;
                "P: Build completed successfully"*) stage 7 "Fertig" ;;
                "E: "*|"W: "*|"I: Google"*|"I: raspotify"*|"I: Waydroid"*|"I: Flathub"*) printf '      %s\n' "$line" ;;
            esac
        else
            rc=$?
            [ "$rc" -gt 128 ] || break   # Ende der Ausgabe
            if [ "$step" -gt 0 ]; then
                printf '      … läuft noch – Schritt %d seit %d min (gesamt %d min)\n' "$step" $(( (SECONDS - ts) / 60 )) $(( (SECONDS - t0) / 60 ))
            fi
        fi
    done
}

build_deb() {
    log "Baue wangerttv_${VERSION}_all.deb"
    local stage
    stage="$(mktemp -d)"
    cp -a package/. "$stage/"
    ln -sf ../lib/wangerttv/bin/wtvctl "$stage/usr/bin/wtvctl"
    sed -i "s/@VERSION@/${VERSION}/" "$stage/DEBIAN/control"
    cp VERSION "$stage/usr/lib/wangerttv/VERSION"
    find "$stage" -name '__pycache__' -prune -exec rm -rf {} +
    find "$stage" -name '.keep' -delete
    find "$stage" -type d -exec chmod 0755 {} +
    find "$stage" -type f -exec chmod 0644 {} +
    chmod 0755 "$stage"/usr/lib/wangerttv/bin/* "$stage"/DEBIAN/postinst "$stage"/DEBIAN/postrm
    dpkg-deb --root-owner-group -Zxz -b "$stage" "$OUT/wangerttv_${VERSION}_all.deb" >/dev/null
    rm -rf "$stage"
    (cd "$OUT" && sha256sum "wangerttv_${VERSION}_all.deb" > "wangerttv_${VERSION}_all.deb.sha256")
    log "Paket fertig: out/wangerttv_${VERSION}_all.deb"
}

iso_inside() {
    # läuft als root auf Debian 13 (nativ oder im Container)
    export DEBIAN_FRONTEND=noninteractive
    if ! command -v lb >/dev/null; then
        log "Build-Werkzeuge werden vorbereitet (1–2 Minuten) …"
        apt-get update -qq >/dev/null
        apt-get install -y -qq live-build debootstrap squashfs-tools xorriso grub-efi-amd64-bin grub-pc-bin \
            mtools dosfstools syslinux-utils isolinux syslinux-common ca-certificates curl gnupg >/dev/null
    fi
    local work=/tmp/wangerttv-build
    rm -rf "$work"
    cp -a live "$work"
    cp "$OUT/wangerttv_${VERSION}_all.deb" "$work/config/includes.chroot/root/"
    rm -f "$work/config/includes.chroot/root/.keep"
    # Optional: zusätzliches Stammzertifikat nur für den Build (z. B. Firmen-Proxy). Wird am Ende wieder entfernt.
    if [ -n "${WTV_EXTRA_CA:-}" ] && [ -f "$WTV_EXTRA_CA" ]; then
        install -D -m 0644 "$WTV_EXTRA_CA" "$work/config/includes.chroot/usr/local/share/ca-certificates/wtv-build-extra.crt"
    fi
    chmod 0755 "$work"/auto/* "$work"/config/hooks/normal/*
    cd "$work"
    lb clean --purge >/dev/null 2>&1 || true
    lb config >/dev/null 2>&1
    log "ISO-Build gestartet – dauert ca. 20–40 Minuten. Fortschritt in Schritten 1–7:"
    local rc=0 iso
    lb build 2>&1 | progress || rc=$?
    cp build.log "$OUT/build-iso.log" 2>/dev/null || true
    iso="$(find . -maxdepth 1 -name "*.iso" | head -n1)"
    if [ "$rc" != 0 ] || [ -z "$iso" ]; then
        printf '\n%sFEHLER: Die ISO konnte nicht gebaut werden.%s Letzte Meldungen:\n' "$C3" "$C0" >&2
        tail -n 25 build.log >&2 || true
        echo "Vollständiges Protokoll: out/build-iso.log" >&2
        exit 1
    fi
    mv "$iso" "$OUT/wangerttv-${VERSION}-amd64.iso"
    (cd "$OUT" && sha256sum "wangerttv-${VERSION}-amd64.iso" > "wangerttv-${VERSION}-amd64.iso.sha256")
}

native_debian() { [ -f /etc/debian_version ] && grep -q '^13' /etc/debian_version && [ "$(id -u)" = 0 ]; }

in_container() {
    log "Baue in einem Debian-13-Container (ISO dauert ca. 20–40 Minuten)"
    local extra=()
    if [ -n "${WTV_EXTRA_CA:-}" ] && [ -f "$WTV_EXTRA_CA" ]; then
        extra=(-v "$(readlink -f "$WTV_EXTRA_CA"):/extra-ca.crt:ro" -e WTV_EXTRA_CA=/extra-ca.crt)
    fi
    docker run --rm --privileged -v "$PWD:/src" -w /src -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" "${extra[@]}" debian:trixie \
        bash -c "bash build.sh $1"
}

build_iso() {
    if native_debian; then
        log "Baue ISO direkt auf diesem Debian-System"
        build_deb
        iso_inside
    elif command -v docker >/dev/null; then
        in_container __all_inside
    else
        echo "Für die ISO wird Docker (oder Debian 13 als root) benötigt." >&2
        exit 1
    fi
    printf '\n%sISO fertig:%s out/wangerttv-%s-amd64.iso\n' "$C2" "$C0" "$VERSION"
}

fix_owner() { if [ -n "${HOST_UID:-}" ]; then chown -R "$HOST_UID:${HOST_GID:-$HOST_UID}" "$OUT"; fi; }

case "$MODE" in
    deb)
        if command -v dpkg-deb >/dev/null; then build_deb
        elif command -v docker >/dev/null; then in_container __deb_inside
        else echo "dpkg-deb oder Docker wird benötigt." >&2; exit 1; fi ;;
    iso) build_iso ;;
    __deb_inside) fix_line_endings; build_deb; fix_owner ;;
    __all_inside) fix_line_endings; build_deb; iso_inside; fix_owner
        printf '\n%sISO fertig:%s out/wangerttv-%s-amd64.iso\n' "$C2" "$C0" "$VERSION" ;;
    clean) rm -rf out /tmp/wangerttv-build; log "aufgeräumt" ;;
    *) sed -n '2,11p' "$0"; exit 1 ;;
esac
