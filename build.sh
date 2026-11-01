#!/bin/bash
# WangertTV – Build-Skript
#
#   ./build.sh          Paket (.deb) + ISO bauen
#   ./build.sh deb      nur das WangertTV-Paket bauen
#   ./build.sh iso      Paket + ISO bauen
#   ./build.sh clean    Build-Reste löschen
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

log() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }

build_deb() {
    log "Baue wangerttv_${VERSION}_all.deb"
    local stage
    stage="$(mktemp -d)"
    cp -a package/. "$stage/"
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
        apt-get update -q
        apt-get install -y -q live-build debootstrap squashfs-tools xorriso grub-efi-amd64-bin grub-pc-bin \
            mtools dosfstools syslinux-utils isolinux syslinux-common ca-certificates curl gnupg
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
    cd "$work"
    lb clean --purge >/dev/null 2>&1 || true
    lb config
    lb build
    local iso
    iso="$(find . -maxdepth 1 -name "*.iso" | head -n1)"
    if [ -z "$iso" ]; then
        echo "FEHLER: live-build hat keine ISO erzeugt – siehe out/build-iso.log" >&2
        cp build.log "$OUT/build-iso.log" 2>/dev/null || true
        exit 1
    fi
    mv "$iso" "$OUT/wangerttv-${VERSION}-amd64.iso"
    (cd "$OUT" && sha256sum "wangerttv-${VERSION}-amd64.iso" > "wangerttv-${VERSION}-amd64.iso.sha256")
    cp build.log "$OUT/build-iso.log" 2>/dev/null || true
}

native_debian() { [ -f /etc/debian_version ] && grep -q '^13' /etc/debian_version && [ "$(id -u)" = 0 ]; }

in_container() {
    log "Baue in einem Debian-13-Container (ISO dauert ca. 20–40 Minuten)"
    local extra=()
    if [ -n "${WTV_EXTRA_CA:-}" ] && [ -f "$WTV_EXTRA_CA" ]; then
        extra=(-v "$(readlink -f "$WTV_EXTRA_CA"):/extra-ca.crt:ro" -e WTV_EXTRA_CA=/extra-ca.crt)
    fi
    docker run --rm --privileged -v "$PWD:/src" -w /src -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" "${extra[@]}" debian:trixie \
        bash -c "./build.sh $1"
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
    log "ISO fertig: out/wangerttv-${VERSION}-amd64.iso"
}

fix_owner() { if [ -n "${HOST_UID:-}" ]; then chown -R "$HOST_UID:${HOST_GID:-$HOST_UID}" "$OUT"; fi; }

case "$MODE" in
    deb)
        if command -v dpkg-deb >/dev/null; then build_deb
        elif command -v docker >/dev/null; then in_container __deb_inside
        else echo "dpkg-deb oder Docker wird benötigt." >&2; exit 1; fi ;;
    iso) build_iso ;;
    __deb_inside) build_deb; fix_owner ;;
    __all_inside) build_deb; iso_inside; fix_owner ;;
    clean) rm -rf out /tmp/wangerttv-build; log "aufgeräumt" ;;
    *) sed -n '2,11p' "$0"; exit 1 ;;
esac
