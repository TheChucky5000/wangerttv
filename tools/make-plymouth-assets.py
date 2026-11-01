#!/usr/bin/env python3
"""Erzeugt die Bilder der WangertTV-Bootanimation (Plymouth) aus dem Logo-SVG der Oberfläche.
Benötigt: python3-playwright + Chromium/Chrome. Aufruf: python3 tools/make-plymouth-assets.py"""
import asyncio
import os
import sys

from playwright.async_api import async_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "package/usr/share/plymouth/themes/wangerttv")
CHROME = os.environ.get("CHROME", "/usr/bin/google-chrome")

MARK = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="{s}" height="{s}">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#22e1ff"/><stop offset=".55" stop-color="#1477ff"/><stop offset="1" stop-color="#0b2f8a"/></linearGradient></defs>
<rect x="2" y="2" width="60" height="60" rx="17" fill="url(#g)"/>
<path d="M14 21h6.5l4.4 15.2L29.6 21h4.8l4.7 15.2L43.5 21H50l-8 24h-6l-4-12.6L28 45h-6z" fill="#fff"/>
<rect x="20" y="49" width="24" height="3.4" rx="1.7" fill="#fff" opacity=".85"/></svg>"""
RING = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200" width="{s}" height="{s}">
<defs><linearGradient id="r" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#22e1ff" stop-opacity="0"/><stop offset="1" stop-color="#22e1ff"/></linearGradient></defs>
<circle cx="100" cy="100" r="92" fill="none" stroke="url(#r)" stroke-width="5" stroke-linecap="round" stroke-dasharray="380 600"/></svg>"""
GLOW = """<div style="width:{s}px;height:{s}px;border-radius:50%;background:radial-gradient(circle, rgba(34,216,255,.30), rgba(34,216,255,0) 65%)"></div>"""
WORD = """<div style="font:600 108px Inter, 'Noto Sans', sans-serif;letter-spacing:-2px;color:#eef4ff;white-space:nowrap;padding:10px 6px">
Wangert<b style="font-weight:800;background:linear-gradient(90deg,#22d8ff,#1477ff);-webkit-background-clip:text;color:transparent">TV</b></div>"""


async def render(page, html, path, size=None):
    await page.set_content(f"<html><body style='margin:0;background:transparent'><div id=x style='display:inline-block'>{html}</div></body></html>")
    await page.wait_for_timeout(150)
    await page.locator("#x").screenshot(path=path, omit_background=True)


async def main():
    os.makedirs(OUT, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        page = await browser.new_page(device_scale_factor=2)
        await render(page, MARK.format(s=118), os.path.join(OUT, "mark.png"))
        await render(page, RING.format(s=200), os.path.join(OUT, "ring.png"))
        await render(page, GLOW.format(s=520), os.path.join(OUT, "glow.png"))
        await render(page, WORD, os.path.join(OUT, "word.png"))
        # Hintergrund für das Bootmenü des USB-Sticks (GRUB 1280x800, isolinux 640x480)
        live = os.path.join(ROOT, "live/config/includes.binary/wangerttv")
        os.makedirs(live, exist_ok=True)
        for name, (w, h) in {"splash-grub.png": (1280, 800), "splash-isolinux.png": (640, 480)}.items():
            p1 = await browser.new_page(viewport={"width": w, "height": h}, device_scale_factor=1)
            k = h / 800
            await p1.set_content(f"""<html><body style="margin:0;width:{w}px;height:{h}px;overflow:hidden;
              background:radial-gradient({int(700*k)}px {int(420*k)}px at 50% 26%, #0a2350 0%, #04080f 70%);
              display:flex;flex-direction:column;align-items:center;font-family:Inter,sans-serif">
              <div style="margin-top:{int(120*k)}px">{MARK.format(s=int(110*k))}</div>
              <div style="margin-top:{int(18*k)}px;font:600 {int(46*k)}px Inter,sans-serif;color:#eef4ff;letter-spacing:-1px">Wangert<b style="font-weight:800;color:#22d8ff">TV</b></div>
              </body></html>""")
            await p1.wait_for_timeout(150)
            await p1.screenshot(path=os.path.join(live, name))
            await p1.close()
        await browser.close()
    print("Bootanimation-Bilder erzeugt in", OUT)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
