#!/usr/bin/env python3
"""Erzeugt die Bilder der Startanimation, damit Plymouth, Sway und die Oberfläche exakt gleich aussehen:
  * Plymouth-Hintergrund (bg.png)
  * Sway-Hintergrund mit Logo (= letztes Bild der Plymouth-Animation) – überbrückt den Wechsel zur Oberfläche
  * Hintergrund der Startanimation in der Oberfläche (boot-bg.png)
Aufruf: python3 tools/make-boot-images.py   (benötigt Pillow + NumPy)"""
import os

import numpy as np
from PIL import Image

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "package")
THEME = os.path.join(ROOT, "usr/share/plymouth/themes/wangerttv")
W, H = 1920, 1080
CX, CY = W / 2, H / 2 - 52               # Logomitte wie in wangerttv.script


def background():
    """Radialer Verlauf wie in der Oberfläche: Ellipse 800×500 bei 50 %/45 %, #0a1f45 -> #04080f bei 70 %."""
    y, x = np.mgrid[0:H, 0:W].astype(np.float32)
    d = np.sqrt(((x - W * 0.5) / 800) ** 2 + ((y - H * 0.45) / 500) ** 2)
    t = np.clip(d / 0.7, 0, 1)[..., None]
    c0, c1 = np.array([0x0a, 0x1f, 0x45], np.float32), np.array([0x04, 0x08, 0x0f], np.float32)
    img = c0 * (1 - t) + c1 * t
    img += np.random.default_rng(7).uniform(-0.5, 0.5, img.shape)      # leichtes Rauschen gegen Farbstufen
    return Image.fromarray(np.clip(img + 0.5, 0, 255).astype(np.uint8), "RGB").convert("RGBA")


def paste(base, path, size, center, opacity=1.0):
    im = Image.open(path).convert("RGBA")
    w = size
    h = round(size * im.height / im.width)
    im = im.resize((w, h), Image.LANCZOS)
    if opacity < 1:
        a = np.array(im)
        a[..., 3] = (a[..., 3] * opacity).astype(np.uint8)
        im = Image.fromarray(a)
    base.alpha_composite(im, (round(center[0] - w / 2), round(center[1] - h / 2)))


bg = background()
bg.convert("RGB").save(os.path.join(THEME, "bg.png"), optimize=True)
bg.convert("RGB").save(os.path.join(ROOT, "usr/lib/wangerttv/ui/assets/boot-bg.png"), optimize=True)

logo = bg.copy()
paste(logo, os.path.join(THEME, "glow.png"), 520, (CX, CY), 0.85)
paste(logo, os.path.join(THEME, "ring.png"), 200, (CX, CY))
paste(logo, os.path.join(THEME, "mark.png"), 118, (CX, CY))
word = Image.open(os.path.join(THEME, "word.png"))
paste(logo, os.path.join(THEME, "word.png"), 298, (CX, CY + 134 + 298 * word.height / word.width / 2))
os.makedirs(os.path.join(ROOT, "usr/share/wangerttv"), exist_ok=True)
logo.convert("RGB").save(os.path.join(ROOT, "usr/share/wangerttv/boot.png"), optimize=True)
print("ok")
