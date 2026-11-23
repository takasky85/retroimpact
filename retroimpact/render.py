"""CPU reference renderer: dot list -> ribbon-struck dots on paper.

The algorithm is deliberately GPU-shaped (impulses splatted additively, one blur-like kernel, one
composite) so it can be ported to a wgpu instanced-quad shader later.
"""
from __future__ import annotations

import math
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Tuple

import cv2
import numpy as np

from .model import PROFILES, U_PER_INCH, DotPage, Document
from .paper import base_noise, make_paper_region, paper_geometry, sample_noise
from .settings import COLOR_CODES, RIBBONS, RenderSettings

_geom_lock = threading.Lock()
TILE = 512  # default tile edge in pixels for banded / tiled rendering


def page_pixel_size(doc: Document, page: DotPage, s: RenderSettings) -> Tuple[int, int]:
    """(width, height) in pixels of the sheet as displayed, i.e. after the view rotation."""
    pw, ph, *_ = paper_geometry(s.paper, page.width_in, page.height_in, doc.carriage_in)
    w, h = int(round(pw * s.dpi)), int(round(ph * s.dpi))
    return (h, w) if (s.rotation // 90) % 2 else (w, h)


def _kernel(radius_px: float, softness: float, rim: float, shape: str = "round") -> np.ndarray:
    """Unit-peak ink-coverage profile of one pin strike (soft disc with a slightly darker rim by default)."""
    r = max(radius_px, 0.6)
    half = int(math.ceil(r * 1.9)) + 1
    ax = np.arange(-half, half + 1, dtype=np.float32)
    xx, yy = ax[None, :], ax[:, None]
    if shape == "oval":  # wider than tall, same area
        d = np.sqrt((xx / 1.14) ** 2 + (yy / 0.88) ** 2)
    elif shape == "square":  # a squircle: |x|^4 + |y|^4 = r^4 scaled so the area matches the disc
        d = ((np.abs(xx) ** 4 + np.abs(yy) ** 4) ** 0.25) * 1.08
    else:
        d = np.sqrt(xx ** 2 + yy ** 2)
    if shape == "ring":
        rim = max(rim, 0.55)
    soft = max(0.35, softness * r)
    prof = np.clip((r + soft * 0.5 - d) / soft, 0, 1)
    ring = 1 + rim * np.clip(d / r, 0, 1) ** 2 - rim * 0.5  # slightly darker rim than the centre
    if shape == "ring":
        ring = ring * (1 - 0.7 * np.exp(-((d / (r * 0.45)) ** 2)))  # pale centre
    k = prof * ring
    # faint outer bleed (ink wicking into paper fibres)
    bleed = np.exp(-((d / (r * 1.25)) ** 2)) * 0.10
    k = np.clip(k + bleed * (d > r * 0.5), 0, 1.4).astype(np.float32)
    return k


def _splat(buf: np.ndarray, px: np.ndarray, py: np.ndarray, amp: np.ndarray) -> None:
    H, W = buf.shape
    ok = (px >= 0) & (px < W - 1) & (py >= 0) & (py < H - 1)
    px, py, amp = px[ok], py[ok], amp[ok]
    if px.size == 0:
        return
    x0 = np.floor(px).astype(np.int64)
    y0 = np.floor(py).astype(np.int64)
    fx, fy = px - x0, py - y0
    base = y0 * W + x0
    idx = np.concatenate([base, base + 1, base + W, base + W + 1])
    wts = np.concatenate([amp * (1 - fx) * (1 - fy), amp * fx * (1 - fy), amp * (1 - fx) * fy, amp * fx * fy])
    buf.reshape(-1)[:] += np.bincount(idx, weights=wts, minlength=W * H).astype(np.float32)


def dot_amplitudes(page: DotPage, s: RenderSettings, n_pins: int):
    """Per-dot (amplitude, dx_in, dy_in). Deterministic in (seed, page index) and independent of dpi."""
    n = page.n_dots
    rng = np.random.default_rng((s.seed * 1000003 + page.index * 7919) & 0x7FFFFFFF)
    dens = s.density
    u = rng.random(n).astype(np.float32)
    amp = rng.normal(1.0, 0.06 + 0.06 * (1 - dens), n).astype(np.float32)
    weak_frac = (1 - dens) * 0.8
    drop_frac = (1 - dens) ** 2 * 0.25
    weak = u < weak_frac
    amp[weak] *= rng.uniform(0.2, 0.65, int(weak.sum())).astype(np.float32)
    amp[rng.random(n) < drop_frac] = 0.0

    wear = s.ribbon_wear
    # line-to-line ink variation (ribbon advancing / head pressure / ink distribution)
    _, inv = np.unique(page.pass_no, return_inverse=True)
    n_pass = int(inv.max()) + 1 if n else 0
    pass_gain = np.exp(rng.normal(0, 0.12 * wear + 0.02, n_pass)).astype(np.float32)
    # slow ribbon fade over the page (ribbon cycle)
    amp *= pass_gain[inv] if n else 1
    # per-pin wear: some pins hit softer
    pin_gain = np.exp(rng.normal(0, 0.10 * wear + 0.02, max(n_pins, int(page.pin.max()) + 1 if n else 1)))
    weak_pins = rng.random(pin_gain.size) < (0.08 * wear + 0.03 * (1 - dens))
    pin_gain[weak_pins] *= rng.uniform(0.45, 0.8, int(weak_pins.sum()))
    amp *= pin_gain[page.pin].astype(np.float32) if n else 1

    jit = 0.0016 * s.jitter  # inches
    dx = rng.normal(0, jit, n).astype(np.float32)
    dy = rng.normal(0, jit * 0.8, n).astype(np.float32)
    # bidirectional misalignment: left-to-right and right-to-left passes land apart (up to 1/50 in at the maximum, which is
    # what a badly adjusted printer does), and every pass starts a hair off from where it should
    mis = 0.010 * s.misalign
    dx += np.where(page.dirn == 1, np.float32(mis), np.float32(-mis))
    dy += np.where(page.dirn == 1, np.float32(0.25 * mis), np.float32(-0.25 * mis))
    dx += (rng.normal(0, 0.0012 * s.misalign, n_pass)[inv].astype(np.float32) if n else 0)
    return np.clip(amp, 0, 2.0), dx, dy




class _Geom:
    """Per-page dot positions (inches, before the carriage offset) and strengths, sorted by y for fast tile lookup."""

    __slots__ = ("x", "y", "amp", "ys", "col")

    def __init__(self, page: DotPage, s: RenderSettings, pins: int):
        amp, dx, dy = dot_amplitudes(page, s, pins)
        x = page.x.astype(np.float32) / U_PER_INCH + dx
        y = page.y.astype(np.float32) / U_PER_INCH + dy
        order = np.argsort(y, kind="stable")
        self.x, self.y, self.amp = x[order], y[order], amp[order]
        self.col = page.color[order] if page.color is not None else None  # ESC r colour per dot (None = all black)
        self.ys = self.y  # sorted


def _geom(page: DotPage, s: RenderSettings, pins: int) -> _Geom:
    key = (s.seed, round(s.density, 4), round(s.ribbon_wear, 4), round(s.jitter, 4), round(s.misalign, 4), pins)
    with _geom_lock:
        cache = page.__dict__.setdefault("_geom_cache", {})
        g = cache.get(key)
        if g is None:
            if len(cache) >= 2:
                cache.clear()
            g = cache[key] = _Geom(page, s, pins)
        return g


def _margins(prof, s: RenderSettings, ss: int) -> Tuple[int, int]:
    """(kernel half-size in output px, total safe margin in output px) for a region render."""
    r = max(prof.dot_diam_in * s.dot_size * s.dpi * ss / 2.0, 0.6)
    half = int(math.ceil(r * 1.9)) + 1
    half_out = int(math.ceil(half / ss))
    sigma = max(0.7, s.dpi / 150, 1.2 if s.impact > 0 else 0.0, s.dpi / 60 if s.impact > 0 else 0.0)  # widest blur used
    return half_out, half_out + int(math.ceil(4 * sigma)) + 4


def render_region(page: DotPage, doc: Document, s: RenderSettings, rx: int, ry: int, rw: int, rh: int) -> np.ndarray:
    """Render the pixel rectangle (rx, ry, rw, rh) of the *displayed* sheet (after s.rotation, clockwise).

    A rotated tile is just the matching upright rectangle turned by a multiple of 90 degrees, so rotation costs
    nothing extra and rotated tiles join as seamlessly as upright ones."""
    q = (int(s.rotation) // 90) % 4
    if q == 0:
        return _render_upright(page, doc, s, rx, ry, rw, rh)
    s = s.clamp()
    Wu, Hu = page_pixel_size(doc, page, s.replace(rotation=0))
    W, H = (Hu, Wu) if q % 2 else (Wu, Hu)
    rx, ry = max(0, int(rx)), max(0, int(ry))
    rw, rh = max(1, min(int(rw), W - rx)), max(1, min(int(rh), H - ry))
    if q == 1:  # clockwise 90: displayed (x, y) comes from upright (y, Hu-1-x)
        arr = _render_upright(page, doc, s, ry, Hu - rx - rw, rh, rw)
        return np.ascontiguousarray(np.rot90(arr, -1))
    if q == 2:
        arr = _render_upright(page, doc, s, Wu - rx - rw, Hu - ry - rh, rw, rh)
        return np.ascontiguousarray(np.rot90(arr, 2))
    arr = _render_upright(page, doc, s, Wu - ry - rh, rx, rh, rw)  # q == 3: counter-clockwise 90
    return np.ascontiguousarray(np.rot90(arr, 1))


def _render_upright(page: DotPage, doc: Document, s: RenderSettings, rx: int, ry: int, rw: int, rh: int) -> np.ndarray:
    """Render the pixel rectangle (rx, ry, rw, rh) of the sheet at s.dpi -> uint8 (rh, rw, 3).

    The result is identical to cropping the same rectangle out of a full-page render, so tiles join seamlessly.
    Cost depends on the rectangle, not on the page size or the zoom level.
    """
    s = s.clamp().replace(rotation=0) if s.rotation else s.clamp()
    pw, ph, kind, tractor, origin_x = paper_geometry(s.paper, page.width_in, page.height_in, doc.carriage_in)
    dpi = int(s.dpi)
    W, H = int(round(pw * dpi)), int(round(ph * dpi))
    rx, ry = max(0, int(rx)), max(0, int(ry))
    rw, rh = max(1, min(int(rw), W - rx)), max(1, min(int(rh), H - ry))
    ss = 2 if dpi <= 200 else 1
    prof = PROFILES.get(page.profile_key) or doc.profile
    half, m = _margins(prof, s, ss)
    ex0, ey0 = max(0, rx - m), max(0, ry - m)
    ex1, ey1 = min(W, rx + rw + m), min(H, ry + rh + m)
    ew, eh = ex1 - ex0, ey1 - ey0
    res = dpi * ss
    bw, bh = ew * ss, eh * ss
    n = page.n_dots
    # coverage per ink: 0 = the black ribbon, 1/2/4 = magenta / cyan / yellow of a colour ribbon (ESC r)
    covs: dict = {}
    if n:
        g = _geom(page, s, prof.pins)
        pad = (half + 2) / float(res)
        lo = int(np.searchsorted(g.ys, ey0 * ss / float(res) - pad, side="left"))
        hi = int(np.searchsorted(g.ys, ey1 * ss / float(res) + pad, side="right"))
        if hi > lo:
            gx = (g.x[lo:hi] + np.float32(origin_x)) * np.float32(res)
            gy = g.y[lo:hi] * np.float32(res)
            sel = (gx > ex0 * ss - half - 2) & (gx < ex1 * ss + half + 2)
            gx, gy, ga = gx[sel], gy[sel], g.amp[lo:hi][sel]
            gc = g.col[lo:hi][sel] if (g.col is not None and s.color_ribbon) else None
            if gc is None:
                layers = {0: None}
            else:
                layers = {}
                for ink in (0, 1, 2, 4):
                    m_ = np.zeros(len(gc), bool)
                    for code, inks in COLOR_CODES.items():
                        if ink in inks:
                            m_ |= gc == code
                    if m_.any():
                        layers[ink] = m_
            # each ribbon colour is its own pass, so they sit a hair out of register with each other
            reg = {0: (0.0, 0.0), 1: (1.0, -0.5), 2: (-1.0, 0.6), 4: (0.4, 1.0)}
            rs = 0.006 * s.misalign * res
            for ink, m_ in layers.items():
                cov = np.zeros((bh, bw), np.float32)
                ox, oy = (reg[ink][0] * rs, reg[ink][1] * rs) if len(layers) > 1 else (0.0, 0.0)
                px, py, pa = (gx, gy, ga) if m_ is None else (gx[m_], gy[m_], ga[m_])
                _splat(cov, (px - ex0 * ss + ox).astype(np.float32), (py - ey0 * ss + oy).astype(np.float32), pa)
                covs[ink] = cov
        diam = prof.dot_diam_in * s.dot_size * res
        k = _kernel(diam / 2.0, 0.55 - 0.25 * s.saturation / 1.5, 0.10 + 0.25 * max(0.0, 1 - s.saturation), s.pin_shape)
        for ink in covs:
            covs[ink] = cv2.filter2D(covs[ink], -1, k, borderType=cv2.BORDER_CONSTANT)
    # fibre / ribbon-weave modulation of the ink transfer, in physical units (so it is the same at every zoom)
    full_w, full_h = W * ss, H * ss
    sd = s.seed + page.index * 31
    fib = sample_noise(base_noise(pw, ph, 0.006, sd + 11, 300), full_w, full_h, ex0 * ss, ey0 * ss, bw, bh)
    cloud = sample_noise(base_noise(pw, ph, 0.05, sd + 12), full_w, full_h, ex0 * ss, ey0 * ss, bw, bh)
    tex = np.clip(1 + 0.22 * fib * (0.4 + 0.6 * s.ribbon_wear) + 0.10 * cloud * s.ribbon_wear, 0.2, 2.0)
    gain = 0.9 + 3.6 * s.saturation
    if s.pin_shape == "rough":
        # a worn pin and the ribbon weave break the dot edge up: strong grain at a quarter of a dot, same at every zoom
        grain = sample_noise(base_noise(pw, ph, 0.0012, sd + 13, 600), full_w, full_h, ex0 * ss, ey0 * ss, bw, bh)
        tex = np.clip(tex * (1 + 0.42 * grain), 0.05, 3.0)
        gain *= 1.25
    inks = {}
    for ink, cov in covs.items():
        a = 1 - np.exp(-gain * np.clip(cov, 0, None) * tex)
        inks[ink] = cv2.resize(a, (ew, eh), interpolation=cv2.INTER_AREA) if ss > 1 else a
    ink = np.zeros((eh, ew), np.float32)
    for a in inks.values():
        ink = np.maximum(ink, a)
    paper = make_paper_region(pw, ph, dpi, kind, tractor, s.paper_texture, s.show_holes, s.seed, ex0, ey0, ew, eh)
    out = paper
    for which, a in inks.items():
        tone = RIBBONS[s.ribbon][1] if which == 0 else s.ink_rgb(which)
        t = np.array(tone, np.float32) / 255.0
        out = out * (1 - a[:, :, None] * (1 - t))  # inks multiply, so overprinted inks mix like real ones
    if s.impact > 0:
        # ink gently dents the paper: light on one side, dark on the other
        # (a directional gradient lit from the top left; scaled by dpi so the shading looks the same at every zoom)
        gb = cv2.GaussianBlur(ink, (0, 0), max(0.7, dpi / 150))
        gx = cv2.Sobel(gb, cv2.CV_32F, 1, 0, ksize=3) / 8.0
        gy = cv2.Sobel(gb, cv2.CV_32F, 0, 1, ksize=3) / 8.0
        lit = -(gx + gy) * 0.7071 * (dpi / 150.0)  # +1 on the lit side of a dent, -1 on the shadowed side
        halo = cv2.GaussianBlur(ink, (0, 0), max(1.2, dpi / 60.0))
        out *= (1 + 0.55 * s.impact * lit - 0.10 * s.impact * halo)[:, :, None]
    cx, cy = rx - ex0, ry - ey0
    out = out[cy:cy + rh, cx:cx + rw]
    return (np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8)


def render_page(page: DotPage, doc: Document, s: RenderSettings, workers: int | None = None) -> np.ndarray:
    """-> uint8 array (H, W, 3). Rendered in horizontal bands (in parallel for big pages) so memory stays bounded."""
    s = s.clamp()
    W, H = page_pixel_size(doc, page, s)
    out = np.empty((H, W, 3), np.uint8)
    band = max(96, int(2_000_000 // max(1, W)))
    bands = [(y, min(band, H - y)) for y in range(0, H, band)]

    def one(b):
        y, h = b
        out[y:y + h] = render_region(page, doc, s, 0, y, W, h)

    nw = workers if workers is not None else min(4, os.cpu_count() or 1)
    if len(bands) == 1 or nw <= 1:
        for b in bands:
            one(b)
    else:
        with ThreadPoolExecutor(max_workers=nw) as ex:
            list(ex.map(one, bands))
    return out
