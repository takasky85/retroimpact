"""Procedural paper: stock colour, mottling, fibres, tractor holes, perforations, greenbar.

Everything here can be generated for just a *region* of the sheet (``make_paper_region``), which is what makes
tile-based rendering possible: the random fields live on a coarse physical grid that is generated once per sheet
size and seed (cached), and a region simply resamples it. Neighbouring regions therefore join seamlessly, and the
cost of a region does not depend on how far you zoom in.
"""
from __future__ import annotations

import threading
from collections import OrderedDict

import cv2
import numpy as np

from .settings import PAPERS

STOCK = {
    "white": (246, 245, 240),
    "cream": (240, 232, 207),
    "aged": (226, 208, 166),
    "greenbar": (247, 246, 240),
    "bluebar": (247, 246, 242),
}
BAND = {"greenbar": (206, 232, 206), "bluebar": (206, 220, 238)}
_BASE_PPI = 120

_noise_cache: "OrderedDict[tuple, np.ndarray]" = OrderedDict()
_noise_lock = threading.Lock()
_NOISE_BUDGET = 700 * 1024 * 1024  # bytes of cached coarse noise


def base_noise(w_in: float, h_in: float, sigma_in: float, seed: int, ppi: int = _BASE_PPI) -> np.ndarray:
    """Unit-variance smooth noise on a coarse physical grid (``ppi`` samples per inch). Cached."""
    bw, bh = max(4, int(w_in * ppi)), max(4, int(h_in * ppi))
    key = (bw, bh, round(sigma_in, 5), int(seed), ppi)
    with _noise_lock:
        n = _noise_cache.get(key)
        if n is not None:
            _noise_cache.move_to_end(key)
            return n
        rng = np.random.default_rng(int(seed) & 0xFFFFFFFFFFFF)
        n = rng.standard_normal((bh, bw)).astype(np.float32)
        n = cv2.GaussianBlur(n, (0, 0), max(0.3, sigma_in * ppi))
        n -= n.mean()
        sd = float(n.std())
        if sd > 0:
            n /= sd
        _noise_cache[key] = n
        total = sum(v.nbytes for v in _noise_cache.values())
        while total > _NOISE_BUDGET and len(_noise_cache) > 1:
            _, old = _noise_cache.popitem(last=False)
            total -= old.nbytes
        return n


def sample_noise(base: np.ndarray, full_w: int, full_h: int, rx: int, ry: int, rw: int, rh: int) -> np.ndarray:
    """Bilinear-resample the region (rx, ry, rw, rh) of a full_w x full_h image of ``base`` (identical to resizing
    the whole field and cropping, without ever making the whole field)."""
    bh, bw = base.shape
    xs = ((rx + np.arange(rw, dtype=np.float32) + 0.5) * (bw / float(full_w)) - 0.5).astype(np.float32)
    ys = ((ry + np.arange(rh, dtype=np.float32) + 0.5) * (bh / float(full_h)) - 0.5).astype(np.float32)
    mx = np.ascontiguousarray(np.broadcast_to(xs[None, :], (rh, rw)))
    my = np.ascontiguousarray(np.broadcast_to(ys[:, None], (rh, rw)))
    return cv2.remap(base, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def physical_noise(w_in: float, h_in: float, w: int, h: int, sigma_in: float, seed: int, ppi: int = _BASE_PPI):
    """Whole-sheet noise at a fixed physical sampling, identical (up to resampling) at every dpi."""
    return sample_noise(base_noise(w_in, h_in, sigma_in, seed, ppi), w, h, 0, 0, w, h)


def paper_geometry(key: str, page_w_in: float, page_h_in: float, carriage_in: float):
    """-> (paper_w_in, paper_h_in, kind, tractor, origin_x_in)"""
    label, pw, ph, kind, tractor = PAPERS[key]
    if pw is None:
        pw = 14.875 if carriage_in > 9 else page_w_in
        ph = page_h_in
    if carriage_in > pw - (1.0 if tractor else 0.0):
        pw = max(pw, carriage_in + (1.0 if tractor else 0.25))
    return pw, ph, kind, tractor, (pw - carriage_in) / 2.0


def make_paper_region(w_in: float, h_in: float, dpi: int, kind: str, tractor: bool, texture: float,
                      holes: bool, seed: int, rx: int, ry: int, rw: int, rh: int) -> np.ndarray:
    """float32 (rh, rw, 3) in 0..1: the region (rx, ry, rw, rh) in pixels of the sheet at ``dpi``."""
    res = float(dpi)
    W, H = int(round(w_in * res)), int(round(h_in * res))
    base = np.array(STOCK.get(kind, STOCK["white"]), np.float32) / 255.0
    img = np.empty((rh, rw, 3), np.float32)
    img[:] = base
    yy = (ry + np.arange(rh, dtype=np.float32) + 0.5) / res  # inches
    xx = (rx + np.arange(rw, dtype=np.float32) + 0.5) / res
    if kind in BAND:
        band = np.array(BAND[kind], np.float32) / 255.0
        lines = np.floor((yy - 0.25) / 0.5).astype(int)  # 3 lines of 6 lpi
        on = (lines % 2 == 0) & (yy > 0.25)
        edge = np.clip(np.minimum(np.abs(((yy - 0.25) % 0.5)), np.abs(0.5 - ((yy - 0.25) % 0.5))) * res / 1.2, 0, 1)
        k = on.astype(np.float32) * (0.35 + 0.65 * edge)
        img[:] = img * (1 - k[:, None, None]) + band * k[:, None, None]
        rule = np.zeros(rh, np.float32)  # fine rules at the band edges
        for t in np.arange(0.25, h_in, 0.5):
            r = int(round(t * res)) - ry
            if 0 <= r < rh:
                rule[r] = 1
        img *= 1 - 0.06 * rule[:, None, None]
    if texture > 0:
        t = texture
        m1 = sample_noise(base_noise(w_in, h_in, 0.5, seed * 7 + 1), W, H, rx, ry, rw, rh)  # mottling, ~half inch
        m2 = sample_noise(base_noise(w_in, h_in, 0.06, seed * 7 + 2), W, H, rx, ry, rw, rh)  # cloudiness
        m3 = sample_noise(base_noise(w_in, h_in, 0.012, seed * 7 + 3, 300), W, H, rx, ry, rw, rh)  # fibres
        lum = 1 + t * (0.012 * m1 + 0.010 * m2 + 0.016 * m3)
        img *= lum[:, :, None]
        if kind in ("cream", "aged"):
            img[:, :, 2] *= 1 + t * 0.012 * m1  # blotchy yellowing
        edge = np.minimum(xx, w_in - xx)  # gentle darkening at the left and right edges
        img *= (1 - t * 0.05 * np.exp(-edge / 0.18))[None, :, None]
    if tractor:
        strip = 0.5
        hole_d, pitch = 0.156, 0.5
        ys = (ry + np.arange(rh, dtype=np.float32)) / res
        for side in (strip, w_in - strip):  # perforation columns (tear-off lines)
            c = int(round(side * res))
            width = max(1, int(round(0.012 * res)))
            dash = ((ys % 0.08) < 0.04).astype(np.float32)  # 0.04 in cuts, 0.04 in ties
            lo, hi = max(rx, c - width), min(rx + rw, c + width)
            if hi > lo:
                img[:, lo - rx:hi - rx] *= (1 - 0.18 * dash[:, None, None])
                img[:, lo - rx:hi - rx] = img[:, lo - rx:hi - rx] * 0.97
        if holes:
            r = hole_d / 2 * res
            for cx in (strip / 2, w_in - strip / 2):
                cx_px = cx * res
                if cx_px + r + 4 < rx or cx_px - r - 4 > rx + rw:
                    continue
                for cy in np.arange(pitch / 2, h_in, pitch):
                    cy_px = cy * res
                    x0, x1 = int(cx_px - r - 3), int(cx_px + r + 3)
                    y0, y1 = int(cy_px - r - 3), int(cy_px + r + 3)
                    x0, y0 = max(rx, x0), max(ry, y0)
                    x1, y1 = min(rx + rw, x1), min(ry + rh, y1)
                    if x1 <= x0 or y1 <= y0:
                        continue
                    gx = (np.arange(x0, x1, dtype=np.float32) + 0.5 - cx_px)[None, :]
                    gy = (np.arange(y0, y1, dtype=np.float32) + 0.5 - cy_px)[:, None]
                    d = np.sqrt(gx * gx + gy * gy)
                    inside = np.clip(r - d + 0.5, 0, 1)  # antialiased hole mask
                    rim = np.clip(1 - np.abs(d - r - 0.6) / 1.5, 0, 1) * (1 - inside) * 0.10
                    shade = (0.30 + 0.10 * (gy / max(r, 1)).clip(-1, 1) * -1).astype(np.float32)  # darker at top
                    blk = img[y0 - ry:y1 - ry, x0 - rx:x1 - rx]
                    blk *= (1 - rim)[:, :, None]
                    blk[:] = blk * (1 - inside)[:, :, None] + (inside * shade)[:, :, None] * np.array([1, 1, 1.05], np.float32)
    return np.clip(img, 0, 1)


def make_paper(w_in: float, h_in: float, dpi: int, kind: str, tractor: bool, texture: float,
               holes: bool, seed: int, ss: int = 1) -> np.ndarray:
    """The whole sheet: float32 HxWx3 in 0..1."""
    res = dpi * ss
    W, H = int(round(w_in * res)), int(round(h_in * res))
    return make_paper_region(w_in, h_in, res, kind, tractor, texture, holes, seed, 0, 0, W, H)
