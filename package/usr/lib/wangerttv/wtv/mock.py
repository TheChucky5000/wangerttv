"""Testdaten für den Entwicklungsmodus (WTV_DEV=1) – damit die Oberfläche ohne TV-Box läuft."""


def network_status():
    return {
        "online": True,
        "ethernet": {"device": "enp2s0", "connected": False, "connection": "", "ip": ""},
        "wifi": {"device": "wlo1", "connected": True, "connection": "Zuhause-WLAN", "ip": "192.168.178.42", "enabled": True},
    }


def wifi_scan():
    return [
        {"ssid": "Zuhause-WLAN", "signal": 86, "secure": True, "active": True},
        {"ssid": "FRITZ!Box 7590 XY", "signal": 64, "secure": True, "active": False},
        {"ssid": "Nachbar 5G", "signal": 38, "secure": True, "active": False},
        {"ssid": "Gast", "signal": 30, "secure": False, "active": False},
    ]


def bt_devices():
    return [
        {"mac": "AA:BB:CC:11:22:33", "name": "TV-Fernbedienung G20", "paired": True, "connected": True, "icon": "input-keyboard"},
        {"mac": "AA:BB:CC:44:55:66", "name": "Kopfhörer WH-1000", "paired": False, "connected": False, "icon": "audio-headphones"},
        {"mac": "AA:BB:CC:77:88:99", "name": "Xbox Controller", "paired": False, "connected": False, "icon": "input-gaming"},
    ]


def audio_status():
    return {
        "sinks": [
            {"name": "alsa_output.pci-0000_00_1f.3.hdmi-stereo", "label": "HDMI – Fernseher"},
            {"name": "alsa_output.pci-0000_00_1f.3.analog-stereo", "label": "Kopfhörerausgang"},
        ],
        "default": "alsa_output.pci-0000_00_1f.3.hdmi-stereo", "volume": 70, "muted": False,
    }


def display_status():
    return {"outputs": [{
        "name": "HDMI-A-1", "label": "Samsung QE55", "active": True, "scale": 2,
        "current": {"width": 3840, "height": 2160, "refresh": 60},
        "modes": [
            {"width": 3840, "height": 2160, "refresh": 60}, {"width": 3840, "height": 2160, "refresh": 50},
            {"width": 2560, "height": 1440, "refresh": 60}, {"width": 1920, "height": 1080, "refresh": 60},
            {"width": 1920, "height": 1080, "refresh": 50}, {"width": 1280, "height": 720, "refresh": 60},
        ],
    }]}


def sysinfo():
    return {
        "version": "1.0.0", "debian": "13.7", "kernel": "6.12.48+deb13-amd64", "hostname": "wangerttv",
        "cpu": "11th Gen Intel(R) Core(TM) i7-1165G7 @ 2.80GHz", "gpu": "Intel Iris Xe Graphics",
        "mem_total": 16 * 1024 ** 3, "mem_used": 3.2 * 1024 ** 3,
        "disk_total": 512 * 1000 ** 3, "disk_used": 21 * 1000 ** 3, "uptime": 7260,
        "ip": "192.168.178.42", "kodi": "2:21.2+dfsg-4", "chrome": "142.0.7444.59-1", "live": True,
    }


def disks():
    return [
        {"path": "/dev/nvme0n1", "name": "nvme0n1", "size": 512110190592, "model": "WD PC SN530 512GB",
         "tran": "nvme", "removable": False, "serial": "21XXXX", "too_small": False},
        {"path": "/dev/sda", "name": "sda", "size": 1000204886016, "model": "Samsung 870 EVO",
         "tran": "sata", "removable": False, "serial": "S5XXXX", "too_small": False},
    ]


def channels():
    names = ["Das Erste HD", "ZDF HD", "WDR HD", "RTL", "SAT.1", "ProSieben", "VOX", "kabel eins",
             "arte HD", "3sat HD", "phoenix HD", "n-tv", "WELT", "tagesschau24", "ZDFneo HD", "ONE HD",
             "KiKA HD", "DMAX", "sixx", "Tele 5"]
    groups = ["Öffentlich-Rechtlich"] * 3 + ["Privat"] * 5 + ["Öffentlich-Rechtlich"] * 3 + ["Nachrichten"] * 3 + \
             ["Öffentlich-Rechtlich"] * 3 + ["Privat"] * 3
    import time
    shows = ["Tagesschau", "Wer weiß denn sowas?", "Lokalzeit", "Wer wird Millionär?", "Navy CIS", "The Voice of Germany",
             "Das perfekte Dinner", "Mein Lokal, Dein Lokal", "Geheimnisvolle Natur", "Kulturzeit", "Der Tag",
             "Nachrichten", "Reportage", "tagesschau24 extra", "Bares für Rares", "Die Simpsons", "Logo!", "Steel Buddies",
             "Grey's Anatomy", "Star Trek"]
    now = int(time.time())
    out = []
    for i, (n, g) in enumerate(zip(names, groups)):
        start = now - (i * 7 % 40 + 5) * 60
        end = start + (45 + i % 3 * 30) * 60
        out.append({"name": n, "logo": "", "group": g, "number": i + 1,
                    "now": {"title": shows[i], "start": start, "end": end},
                    "next": {"title": shows[(i + 5) % len(shows)], "start": end, "end": end + 3600}})
    return out


def android_apps():
    return [
        {"package": "com.android.vending", "name": "Play Store"},
        {"package": "com.disney.disneyplus", "name": "Disney+"},
        {"package": "de.zdf.android.mediathek", "name": "ZDFmediathek"},
    ]


def weather():
    return {
        "ok": True, "place": "Musterstadt", "temp": 12, "code": 3, "is_day": 1,
        "daily": [{"date": "2026-11-01", "max": 13, "min": 7, "code": 61},
                  {"date": "2026-11-02", "max": 11, "min": 5, "code": 3},
                  {"date": "2026-11-03", "max": 10, "min": 4, "code": 1}],
    }
