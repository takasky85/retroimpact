"""Command line: python -m retroimpact input.prn -o out.pdf [options]   (no arguments -> GUI)."""
from __future__ import annotations

import argparse
import os
import sys

from .model import PROFILE_ORDER, EmuOptions
from .settings import INK_SETS, PIN_SHAPES, LOOK_PRESETS, PAPERS, RIBBONS, RenderSettings


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="retroimpact", description="Convert raw dot-matrix printer data to PDF/TIFF/PNG.")
    p.add_argument("input", nargs="?", help="raw printer data file (omit to open the GUI)")
    p.add_argument("-o", "--output", help="output file (.pdf, .tif/.tiff, .png)")
    p.add_argument("--printer", default="auto", choices=["auto"] + list(PROFILE_ORDER))
    p.add_argument("--look", choices=list(LOOK_PRESETS), help="start from a look preset")
    p.add_argument("--dpi", type=int, default=None)
    p.add_argument("--dot-size", type=float)
    p.add_argument("--density", type=float)
    p.add_argument("--saturation", type=float)
    p.add_argument("--pin-shape", choices=list(PIN_SHAPES), help="footprint of one pin strike")
    p.add_argument("--ribbon", choices=list(RIBBONS))
    p.add_argument("--paper", choices=list(PAPERS))
    p.add_argument("--seed", type=int)
    p.add_argument("--rotate", type=int, choices=[0, 90, 180, 270], default=0,
                   help="rotate the pages clockwise (banners print sideways)")
    p.add_argument("--mono", action="store_true", help="ignore ESC r colour changes (print everything black)")
    p.add_argument("--inks", choices=list(INK_SETS), default=None,
                   help="colour ink set for colour ribbons / Okimate: " + ", ".join(INK_SETS))
    p.add_argument("--tiff-mode", choices=["color", "gray", "mono"], default="color")
    p.add_argument("--codepage", default=None, help="cp437, cp850, latin1, mac_roman, ascii")
    p.add_argument("--page-length", type=float, default=None, help="inches")
    p.add_argument("--blank-lines", choices=["keep", "collapse", "remove"], default="keep",
                   help="what to do with runs of empty lines")
    p.add_argument("--strict-esc-a", action="store_true", help="ESC A only takes effect after ESC 2 (real Epson behaviour)")
    p.add_argument("--mps-gap", choices=["gap", "fill", "close"], default="gap",
                   help="Commodore MPS graphics lines: gap = authentic 1/9 in feed with a thin gap, fill = same spacing "
                        "with the gap filled in, close = feed 7/72 in (seamless but the picture shrinks)")
    p.add_argument("--no-mps-gap", action="store_true", help="same as --mps-gap close")
    p.add_argument("--pages", help="e.g. 1-3,5")
    pb = p.add_argument_group("print-head playback (an animation of one sheet being printed)")
    pb.add_argument("--playback", metavar="FILE", help="write an animation of the print head at work (.gif or .mp4)")
    pb.add_argument("--page", type=int, default=1, help="which sheet to animate (default 1)")
    pb.add_argument("--speed", type=float, help="speed-up over a real printer (default: about a 12 second film)")
    pb.add_argument("--fps", type=int, help="frames per second (default 15 for GIF, 30 for MP4)")
    pb.add_argument("--full-page", action="store_true", help="show the whole sheet instead of following the head")
    pb.add_argument("--no-sound", action="store_true", help="MP4 without the synthesised printer noise")
    v = p.add_argument_group("virtual printer (headless server: every job received is saved and converted)")
    v.add_argument("--listen", type=int, metavar="PORT", help="accept raw print jobs on this TCP port (9100 is the usual one)")
    v.add_argument("--pipe", metavar="PATH", help=r"accept jobs on a named pipe (Windows \\.\pipe\Name, else a FIFO path)")
    v.add_argument("--watch", metavar="DIR", help="hot folder: convert every file that appears in DIR (files already there too)")
    v.add_argument("--all-interfaces", action="store_true", help="let other computers connect (default: this computer only)")
    v.add_argument("--idle", type=float, default=3.0, help="seconds of silence that end a job (default 3)")
    v.add_argument("--outdir", default="retroimpact-jobs", help="folder for received jobs and their converted files")
    v.add_argument("--format", choices=["pdf", "tiff", "png"], default="pdf", help="what each received job is converted to")
    return p


def parse_pages(spec: str | None, n: int):
    if not spec:
        return None
    out = []
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a) - 1, int(b)))
        else:
            out.append(int(part) - 1)
    return out


def render_settings(a) -> RenderSettings:
    s = RenderSettings()
    if a.look:
        s = s.replace(**LOOK_PRESETS[a.look])
    kw = {k: v for k, v in dict(dpi=a.dpi, dot_size=a.dot_size, density=a.density, saturation=a.saturation,
                                ribbon=a.ribbon, pin_shape=a.pin_shape, paper=a.paper, seed=a.seed).items() if v is not None}
    s = s.replace(**kw)
    if a.rotate:
        s = s.replace(rotation=a.rotate)
    if a.mono:
        s = s.replace(color_ribbon=False)
    if a.inks:
        s = s.replace(ink_set=a.inks)
    if a.dpi is None:
        s = s.replace(dpi=200)
    return s


def emu_options(a) -> EmuOptions:
    eo = EmuOptions(printer=a.printer, blank_lines=a.blank_lines, esc_a_immediate=not a.strict_esc_a,
                    mps_gap="close" if a.no_mps_gap else a.mps_gap)
    if a.codepage:
        eo.codepage = a.codepage
    if a.page_length:
        eo.page_length_in = a.page_length
    return eo


def convert(data: bytes, out: str, a, s: RenderSettings, label: str, progress=None) -> int:
    """Emulate `data` and write it to `out` (.pdf / .tif / .png). Returns the page count."""
    from .emulate import emulate
    from .export import export_pdf, export_png, export_tiff

    doc = emulate(data, emu_options(a))
    print(f"{label}: {len(data)} bytes, printer={doc.detected or a.printer}, {doc.n_pages} page(s)", file=sys.stderr)
    pages = parse_pages(a.pages, doc.n_pages)
    ext = os.path.splitext(out)[1].lower()
    if ext in (".tif", ".tiff"):
        export_tiff(doc, out, s, pages, mode=a.tiff_mode, progress=progress)
    elif ext == ".png":
        export_png(doc, out, s, pages, progress=progress)
    else:
        export_pdf(doc, out, s, pages, progress=progress)
    return doc.n_pages


def playback(data: bytes, a, s: RenderSettings) -> int:
    from .emulate import emulate
    from .playback import PagePlayer, auto_speed, export_gif, export_mp4

    doc = emulate(data, emu_options(a))
    if not 1 <= a.page <= doc.n_pages:
        print(f"page {a.page} does not exist: the capture has {doc.n_pages} page(s)", file=sys.stderr)
        return 1
    player = PagePlayer(doc, doc.pages[a.page - 1], s, dpi=min(s.dpi, 100))
    speed = a.speed or auto_speed(player.duration)
    prog = lambda i, n: (print(f"\rframe {i}/{n}", end="", file=sys.stderr) if i % 10 == 0 else None, True)[1]  # noqa: E731
    if a.playback.lower().endswith(".mp4"):
        n = export_mp4(player, a.playback, speed, a.fps or 30, not a.full_page, not a.no_sound, prog)
    else:
        n = export_gif(player, a.playback, speed, a.fps or 15, not a.full_page, prog)
    print(f"\nwrote {a.playback}: {n} frames, {player.duration:.0f} s of printing shown at {speed:.1f}x", file=sys.stderr)
    return 0


def serve(a) -> int:
    """Headless virtual printer: save every received job as .prn and convert it."""
    import queue
    import time

    from .virtual_printer import VirtualPrinter

    s = render_settings(a)
    os.makedirs(a.outdir, exist_ok=True)
    ext = {"pdf": ".pdf", "tiff": ".tif", "png": ".png"}[a.format]
    jobs: "queue.Queue" = queue.Queue()  # convert on the main thread, never inside a listener thread
    vp = VirtualPrinter(jobs.put, port=a.listen, host="0.0.0.0" if a.all_interfaces else "127.0.0.1", pipe=a.pipe,
                        idle=a.idle, on_status=lambda m: print(m, file=sys.stderr), watch=a.watch, include_existing=True)
    try:
        where = vp.start()
    except OSError as e:
        print(f"could not start listening: {e}", file=sys.stderr)
        return 1
    print("listening on " + ", ".join(where) + f"; jobs go to {a.outdir}  (Ctrl+C to stop)", file=sys.stderr)
    try:
        while True:
            try:
                job = jobs.get(timeout=0.5)
            except queue.Empty:
                continue
            if job.path:  # from the hot folder: name the result after the file, keep the original where it is
                base = os.path.join(a.outdir, os.path.splitext(job.label)[0])
            else:
                base = os.path.join(a.outdir, time.strftime("job_%Y%m%d_%H%M%S", time.localtime(job.when)) + f"_{job.number}")
                with open(base + ".prn", "wb") as f:
                    f.write(job.data)
            try:
                n = convert(job.data, base + ext, a, s, job.name)
                print(f"  -> {base + ext} ({n} page(s))", file=sys.stderr)
            except Exception as e:  # one bad job must not stop the server
                print(f"  conversion failed: {e}", file=sys.stderr)
    except KeyboardInterrupt:
        pass
    finally:
        vp.stop()
    return 0


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    if a.listen is not None or a.pipe or a.watch:
        return serve(a)
    if not a.input:
        from .gui.app import run_gui

        return run_gui([])
    s = render_settings(a)
    with open(a.input, "rb") as f:
        data = f.read()
    if a.playback:
        return playback(data, a, s)
    out = a.output or os.path.splitext(a.input)[0] + ".pdf"
    prog = lambda d, t: (print(f"\rpage {d}/{t}", end="", file=sys.stderr), True)[1]  # noqa: E731
    convert(data, out, a, s, a.input, prog)
    print(f"\nwrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
