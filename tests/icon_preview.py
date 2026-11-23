"""Rasterise the vector icons with Pillow (same geometry Qt paints) into a contact sheet: python tests/icon_preview.py"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from PIL import Image, ImageDraw

from retroimpact.gui.icon_art import GRID, ICONS, STROKE, art


def raster(name, px=96, fg=(235, 235, 235), bg=None, ss=6):
    n = px * ss
    k = n / GRID
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    w = STROKE * k
    for pts, closed in art(name).strokes:
        P = [(x * k, y * k) for x, y in pts] + ([(pts[0][0] * k, pts[0][1] * k)] if closed else [])
        d.line(P, fill=fg + (255,), width=int(round(w)), joint="curve")
        for x, y in P:
            d.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill=fg + (255,))
    for pts in art(name).fills:
        d.polygon([(x * k, y * k) for x, y in pts], fill=fg + (255,))
    im = im.resize((px, px), Image.LANCZOS)
    if bg is not None:
        base = Image.new("RGBA", (px, px), bg + (255,))
        base.alpha_composite(im)
        im = base
    return im


def sheet(path, px=96, cols=7):
    names = list(ICONS)
    rows = (len(names) + cols - 1) // cols
    cw, ch = px + 28, px + 36
    out = Image.new("RGB", (cols * cw * 2, rows * ch), (0, 0, 0))
    d = ImageDraw.Draw(out)
    for half, (bg, fg) in enumerate((((43, 45, 49), (232, 232, 232)), ((240, 240, 240), (35, 35, 35)))):
        d.rectangle([half * cols * cw, 0, (half + 1) * cols * cw, rows * ch], fill=bg)
        for i, nm in enumerate(names):
            x, y = half * cols * cw + (i % cols) * cw + 14, (i // cols) * ch + 10
            out.paste(raster(nm, px, fg), (x, y), raster(nm, px, fg))
            d.text((x, y + px + 6), nm, fill=fg)
    out.save(path)
    return path


if __name__ == "__main__":
    print(sheet(sys.argv[1] if len(sys.argv) > 1 else "/tmp/retroimpact_icons.png"))
