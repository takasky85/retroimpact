"""Print-head playback: watch a sheet being printed, pass by pass, with the head sweeping and the paper feeding.

Nothing here needs Qt, so it works from the command line (GIF / MP4 export) as well as in the playback window.

How it works
* ``build_timeline`` turns the dot list into a schedule: every head pass gets a start and end time from a head speed, a
  paper-feed speed and a turn-around delay; each dot gets the moment the head passes over it.
* ``PagePlayer`` renders the finished sheet once (F) and the bare paper once (B), and works out for every pixel the time at
  which the first dot lands on it. A frame at time t is then "F where that time has passed, B elsewhere", with the head drawn
  on top - so any moment of the print costs a few milliseconds, not a re-render of the page.
"""
from __future__ import annotations

import dataclasses
import math
import shutil
import subprocess
import wave
from dataclasses import dataclass
from typing import Callable, List, Optional

import cv2
import numpy as np

from .model import PROFILES, U_PER_INCH, Document, DotPage
from .paper import paper_geometry
from .render import render_page
from .settings import RenderSettings

BIG = 1e9


@dataclass
class Pass:
    g0: float  # the head starts travelling to this pass / the paper starts feeding
    t0: float  # printing starts
    t1: float  # printing ends
    x_from: float  # inches: where the head was when the gap began
    x0: float  # where printing starts / ends (inches on the sheet)
    x1: float
    y_from: float  # inches: the line the paper was at before
    y: float  # this line
    feed_s: float  # how long of the gap the paper feed takes
    dirn: int
    n_dots: int
    top: float  # inches: extent of the dots of this pass
    bottom: float


@dataclass
class Timeline:
    passes: List[Pass]
    t_dot: np.ndarray  # seconds, per dot (same order as the page's dot arrays)
    duration: float
    origin_x: float
    speed_ips: float

    def state(self, t: float):
        """(head x, paper line y, printing?, pass index) in inches at time t."""
        if not self.passes:
            return 0.0, 0.0, False, -1
        starts = [p.g0 for p in self.passes]
        lo, hi = 0, len(starts)
        while lo < hi:  # last pass whose gap has begun
            mid = (lo + hi) // 2
            if starts[mid] <= t:
                lo = mid + 1
            else:
                hi = mid
        j = max(0, lo - 1)
        p = self.passes[j]
        if t >= p.t1:
            return p.x1, p.y, False, j
        if t >= p.t0:
            u = (t - p.t0) / max(1e-6, p.t1 - p.t0)
            return p.x0 + (p.x1 - p.x0) * u, p.y, True, j
        gap = max(1e-6, p.t0 - p.g0)
        u = min(1.0, max(0.0, (t - p.g0) / gap))
        v = min(1.0, max(0.0, (t - p.g0) / max(1e-6, p.feed_s)))
        v = v * v * (3 - 2 * v)  # the paper eases in and out
        return p.x_from + (p.x0 - p.x_from) * u, p.y_from + (p.y - p.y_from) * v, False, j


def build_timeline(page: DotPage, origin_x: float, speed_ips: float = 16.0, feed_ips: float = 3.5,
                   turn_s: float = 0.07) -> Timeline:
    n = page.n_dots
    t_dot = np.zeros(n, np.float32)
    if n == 0:
        return Timeline([], t_dot, 0.0, origin_x, speed_ips)
    xs = page.x.astype(np.float64) / U_PER_INCH + origin_x
    ys = page.y.astype(np.float64) / U_PER_INCH
    order = np.argsort(page.pass_no, kind="stable")
    pn = page.pass_no[order]
    cuts = np.flatnonzero(np.diff(pn)) + 1
    groups = np.split(order, cuts)
    passes: List[Pass] = []
    t = 0.3  # a moment of calm before the first line
    head_x, paper_y = float(xs[groups[0]].min()), float(ys[groups[0]].mean())
    for idx in groups:
        gx, gy = xs[idx], ys[idx]
        xmin, xmax, yc = float(gx.min()), float(gx.max()), float(gy.mean())
        d = int(page.dirn[idx[0]])
        sx, ex = (xmin, xmax) if d == 0 else (xmax, xmin)
        move_s = abs(head_x - sx) / (speed_ips * 1.5)
        dy = abs(yc - paper_y)
        feed_s = (0.035 + dy / feed_ips) if dy > 0.002 else 0.0
        gap = max(move_s, feed_s) + turn_s
        g0, t0 = t, t + gap
        t1 = t0 + (xmax - xmin) / speed_ips + 0.012
        passes.append(Pass(g0, t0, t1, head_x, sx, ex, paper_y, yc, feed_s, d, len(idx), float(gy.min()), float(gy.max())))
        t_dot[idx] = (t0 + (gx - xmin) / speed_ips if d == 0 else t0 + (xmax - gx) / speed_ips).astype(np.float32)
        t, head_x, paper_y = t1, ex, yc
    return Timeline(passes, t_dot, t + 0.6, origin_x, speed_ips)


class PagePlayer:
    """Frames of one sheet being printed."""

    def __init__(self, doc: Document, page: DotPage, settings: RenderSettings, dpi: int = 90,
                 speed_ips: float = 16.0, feed_ips: float = 3.5):
        s = settings.clamp().replace(dpi=int(dpi), rotation=0, show_holes=True)
        self.s, self.page, self.doc = s, page, doc
        pw, ph, _kind, _tractor, origin_x = paper_geometry(s.paper, page.width_in, page.height_in, doc.carriage_in)
        self.timeline = build_timeline(page, origin_x, speed_ips, feed_ips)
        self.dpi = int(dpi)
        self.final = render_page(page, doc, s)
        empty = dataclasses.replace(page, x=page.x[:0], y=page.y[:0], pin=page.pin[:0], pass_no=page.pass_no[:0],
                                    dirn=page.dirn[:0], color=None)
        self.bare = render_page(empty, doc, s)
        self.h, self.w = self.final.shape[:2]
        prof = PROFILES.get(page.profile_key) or doc.profile
        self.pins = prof.pins
        # the moment the first dot lands on each pixel
        tm = np.full((self.h, self.w), BIG, np.float32)
        if page.n_dots:
            ix = np.clip(((page.x / U_PER_INCH + origin_x) * self.dpi).astype(np.int32), 0, self.w - 1)
            iy = np.clip((page.y / U_PER_INCH * self.dpi).astype(np.int32), 0, self.h - 1)
            o = np.argsort(-self.timeline.t_dot, kind="stable")  # earliest written last, so it wins on a shared pixel
            tm[iy[o], ix[o]] = self.timeline.t_dot[o]
            rr = max(1, int(math.ceil(prof.dot_diam_in * s.dot_size * self.dpi / 2.0)) + 1)
            tm = cv2.erode(tm, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * rr + 1, 2 * rr + 1)))
        self.tmap = tm

    @property
    def duration(self) -> float:
        return self.timeline.duration

    def frame_size(self, follow: bool, win_in: float = 3.0):
        return (self.w, min(self.h, int(win_in * self.dpi)) if follow else self.h)

    def frame(self, t: float, follow: bool = True, win_in: float = 3.0, head: bool = True) -> np.ndarray:
        hx, hy, printing, _j = self.timeline.state(t)
        dpi = self.dpi
        if follow:
            wh = min(self.h, int(win_in * dpi))
            y0 = int(round(min(max(0.0, hy * dpi - 0.62 * wh), self.h - wh)))
        else:
            wh, y0 = self.h, 0
        sl = slice(y0, y0 + wh)
        shown = (self.tmap[sl] <= t)[:, :, None]
        img = np.where(shown, self.final[sl], self.bare[sl])
        if head:
            img = self._draw_head(np.ascontiguousarray(img), hx * dpi, hy * dpi - y0, printing)
        return img

    def _draw_head(self, img: np.ndarray, cx: float, cy: float, printing: bool) -> np.ndarray:
        dpi = self.dpi
        over = img.copy()
        W = int(0.62 * dpi)
        top, bot = int(cy - 0.50 * dpi), int(cy - 0.05 * dpi)
        x0, x1 = int(cx - W / 2), int(cx + W / 2)
        # shadow, body, a lighter top edge, the ribbon mask over the line being printed, and the strike point
        cv2.rectangle(over, (x0 + 4, top + 5), (x1 + 4, bot + 5), (40, 40, 40), -1)
        cv2.rectangle(over, (x0, top), (x1, bot), (66, 70, 80), -1)
        cv2.rectangle(over, (x0, top), (x1, top + max(2, dpi // 30)), (120, 128, 140), -1)
        cv2.rectangle(over, (x0 + W // 4, top + dpi // 10), (x1 - W // 4, bot - dpi // 14), (45, 48, 56), -1)
        out = cv2.addWeighted(over, 0.88, img, 0.12, 0)
        r2 = out.copy()
        mx0, mx1 = int(cx - 0.17 * dpi), int(cx + 0.17 * dpi)
        cv2.rectangle(r2, (mx0, int(cy - 0.07 * dpi)), (mx1, int(cy + 0.07 * dpi)), (96, 84, 50), -1)
        out = cv2.addWeighted(r2, 0.42, out, 0.58, 0)
        if printing:
            cv2.circle(out, (int(cx), int(cy)), max(2, dpi // 36), (255, 150, 60), -1)
        return out


# ---------------------------------------------------------------------------------------------------- sound
def synth_audio(tl: Timeline, speed: float, rate: int = 22050, volume: float = 0.5) -> np.ndarray:
    """A plausible printer noise for the (time-compressed) playback: a buzz while the head prints, a stepper ratchet while the
    paper feeds, a soft whirr while the head repositions. int16 mono."""
    total = int((tl.duration / speed + 0.5) * rate)
    out = np.zeros(total, np.float32)
    rng = np.random.default_rng(3)

    def span(a, b):
        i0, i1 = int(a / speed * rate), int(b / speed * rate)
        return max(0, i0), min(total, max(i0 + 1, i1))

    for p in tl.passes:
        i0, i1 = span(p.t0, p.t1)  # printing: dot-firing buzz, louder and higher for dense lines
        n = i1 - i0
        if n > 8:
            tt = np.arange(n, dtype=np.float32) / rate
            dens = min(1.0, p.n_dots / max(1.0, abs(p.x1 - p.x0) * 120.0))
            f = (150 + 90 * dens) * (1.0 if p.dirn == 0 else 1.07)
            saw = 2 * ((tt * f) % 1.0) - 1
            hiss = rng.standard_normal(n).astype(np.float32)
            env = np.minimum(1.0, np.minimum(tt, tt[-1] - tt) * 120.0)
            out[i0:i1] += (0.20 * saw + 0.10 * hiss) * (0.35 + 0.65 * dens) * env
        j0, j1 = span(p.g0, p.t0)  # between lines: head whirr and paper stepper
        n = j1 - j0
        if n > 8:
            tt = np.arange(n, dtype=np.float32) / rate
            out[j0:j1] += 0.05 * np.sin(2 * np.pi * 70 * tt) * np.minimum(1.0, tt * 80)
            k1 = min(j1, j0 + int(p.feed_s / speed * rate))
            m = k1 - j0
            if m > 8:
                clicks = (((np.arange(m) / rate * 55.0) % 1.0) < 0.12).astype(np.float32)  # stepper ratchet
                out[j0:k1] += 0.16 * clicks * (2 * ((np.arange(m) / rate * 180.0) % 1.0) - 1)
    peak = float(np.max(np.abs(out))) or 1.0
    return (np.clip(out / max(peak, 0.6) * volume, -1, 1) * 32767).astype(np.int16)


def write_wav(path: str, samples: np.ndarray, rate: int = 22050) -> None:
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())


# ---------------------------------------------------------------------------------------------------- export
def auto_speed(duration: float, target_seconds: float = 12.0) -> float:
    """A speed-up that makes the film about `target_seconds` long (never slower than real time)."""
    return max(1.0, duration / target_seconds)


def _frame_times(player: PagePlayer, speed: float, fps: int):
    n = max(2, int(math.ceil(player.duration / speed * fps)) + 1)
    return [min(player.duration, i * speed / fps) for i in range(n)]


def export_gif(player: PagePlayer, path: str, speed: float, fps: int = 15, follow: bool = True,
               progress: Optional[Callable[[int, int], bool]] = None) -> int:
    from PIL import Image

    times = _frame_times(player, speed, fps)
    frames = []
    for i, t in enumerate(times):
        if progress and progress(i, len(times)) is False:
            raise InterruptedError("cancelled")
        frames.append(Image.fromarray(player.frame(t, follow=follow)).quantize(colors=128, method=Image.Quantize.FASTOCTREE))
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=int(round(1000 / fps)), loop=0, disposal=1)
    return len(frames)


def ffmpeg_path() -> Optional[str]:
    return shutil.which("ffmpeg")


def export_mp4(player: PagePlayer, path: str, speed: float, fps: int = 30, follow: bool = True, sound: bool = True,
               progress: Optional[Callable[[int, int], bool]] = None) -> int:
    ff = ffmpeg_path()
    if not ff:
        raise RuntimeError("MP4 export needs ffmpeg on the PATH")
    import os
    import tempfile

    times = _frame_times(player, speed, fps)
    w, h = player.frame_size(follow)
    w, h = w - w % 2, h - h % 2  # H.264 wants even sizes
    wav = None
    cmd = [ff, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-"]
    if sound:
        fd, wav = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        write_wav(wav, synth_audio(player.timeline, speed))
        cmd += ["-i", wav]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-preset", "veryfast"]
    cmd += (["-c:a", "aac", "-b:a", "96k", "-shortest"] if sound else ["-an"]) + [path]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for i, t in enumerate(times):
            if progress and progress(i, len(times)) is False:
                raise InterruptedError("cancelled")
            fr = player.frame(t, follow=follow)[:h, :w]
            proc.stdin.write(np.ascontiguousarray(fr).tobytes())
        proc.stdin.close()
        err = proc.stderr.read().decode("utf-8", "replace")
        if proc.wait() != 0:
            raise RuntimeError("ffmpeg failed: " + err[-400:])
    finally:
        if proc.poll() is None:
            proc.kill()
        if wav:
            try:
                os.unlink(wav)
            except OSError:
                pass
    return len(times)
