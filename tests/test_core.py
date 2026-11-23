"""Headless tests of the Qt-free core. Run:  python -m unittest discover -s tests"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
from PIL import Image

from retroimpact.emulate import detect_printer, emulate, mask_graphics, split_segments
from retroimpact.export import export_pdf, export_png, export_tiff
from retroimpact.model import EmuOptions
from retroimpact.render import render_page
from retroimpact.samples import SAMPLES
from retroimpact.settings import LOOK_PRESETS, RenderSettings

ESC = b"\x1b"


class EmulationTests(unittest.TestCase):
    def test_detection(self):
        self.assertEqual(detect_printer(SAMPLES["Apple ImageWriter II"]()), "imagewriter")
        self.assertEqual(detect_printer(SAMPLES["Commodore MPS (ASCII)"]()), "mps")
        self.assertEqual(detect_printer(SAMPLES["Epson ESC/P text + graphics"]()), "epson_fx")
        self.assertEqual(detect_printer(b"plain text\r\n"), "epson_fx")

    def test_pages_and_form_feed(self):
        d = emulate(b"a\r\n\x0cb\r\n\x0cc\r\n")
        self.assertEqual(d.n_pages, 3)

    def test_page_overflow(self):
        d = emulate(b"line\r\n" * 150)
        self.assertGreaterEqual(d.n_pages, 2)

    def test_newline_auto(self):
        a = emulate(b"x\ry\rz\r")
        b = emulate(b"x\ny\nz\n")
        c = emulate(b"x\r\ny\r\nz\r\n")
        ys = lambda d: sorted(set(np.round(d.pages[0].y / 20)))  # noqa: E731
        self.assertEqual(len(ys(a)), len(ys(b)))
        self.assertEqual(len(ys(b)), len(ys(c)))

    def test_graphics_column_count(self):
        d = emulate(ESC + b"@" + ESC + b"K" + bytes([4, 0]) + b"\xff\xff\xff\xff")
        self.assertEqual(d.pages[0].n_dots, 32)  # 4 columns x 8 pins

    def test_blank_line_modes(self):
        data = b"".join(b"line %d\r\n\r\n" % i for i in range(4)) + b"\r\n\r\npara\r\n"
        span = lambda m: float(emulate(data, EmuOptions(blank_lines=m)).pages[0].y.max())  # noqa: E731
        self.assertLess(span("remove"), span("collapse"))
        self.assertLess(span("collapse"), span("keep"))
        self.assertAlmostEqual(span("remove") / 240.0, 5.0, delta=1.0)  # five lines, single-spaced

    def test_esc_a_immediate(self):
        data = ESC + b"A\x08" + (ESC + b"K\x02\x00\xff\xff\r") * 3
        height = lambda **kw: emulate(data, EmuOptions(**kw)).pages[0].y.max()  # noqa: E731
        self.assertLess(height(), height(esc_a_immediate=False))

    def test_mps_bitimage_persists_across_cr(self):
        d = emulate(b"\x08\x00\r" + (b"\x81\x81\x81\x00\r") * 3 + b"\x0f\x0c", EmuOptions(printer="mps"))
        self.assertEqual(sum(p.n_dots for p in d.pages), 9)  # three lines x three columns x one pin, no PETSCII text

    def test_mps_gap_option(self):
        data = b"\x08" + b"\x81\r" * 3 + b"\x0f"
        span = lambda **kw: emulate(data, EmuOptions(printer="mps", **kw)).pages[0].y.max()  # noqa: E731
        self.assertGreater(span(), span(mps_gap="close"))
        self.assertEqual(span(), span(mps_gap="fill"))  # fill keeps the authentic spacing

    def test_mps_gap_fill_adds_the_missing_row(self):
        # two solid 7-dot lines: the authentic output has an empty row between them, fill puts a row of dots there
        data = b"\x08" + (b"\xff" * 5 + b"\r") * 2 + b"\x0f"
        gap = emulate(data, EmuOptions(printer="mps", mps_gap="gap")).pages[0]
        fill = emulate(data, EmuOptions(printer="mps", mps_gap="fill")).pages[0]
        self.assertEqual(fill.n_dots - gap.n_dots, 5)
        ys = np.unique(np.round(gap.y / 20))
        gapped = [y for y in range(int(ys.min()), int(ys.max())) if y not in set(ys.tolist())]
        self.assertEqual(len(gapped), 1)  # exactly one empty dot row between the two lines
        self.assertTrue(np.any(np.round(fill.y / 20) == gapped[0]))
        self.assertEqual(gap.y.max(), fill.y.max())  # same height: nothing shrinks

    def test_mixed_stream_split(self):
        from retroimpact.emulate import split_segments

        epson = SAMPLES["Epson ESC/P text + graphics"]()
        mps = b"\x08" + b"".join(bytes(0x80 | ((i * 7 + k) % 127) for i in range(400)) + b"\x00\r" for k in range(12)) + b"\x0f\x0c"
        data = epson + mps + epson
        segs = split_segments(data)
        self.assertEqual([k for *_x, k in segs], ["epson_fx", "mps", "epson_fx"])
        self.assertEqual(segs[1][:2], (len(epson), len(epson) + len(mps)))
        d = emulate(data)
        self.assertEqual(d.detected, "mixed")
        self.assertEqual(sorted({p.profile_key for p in d.pages}), ["epson_fx", "mps"])
        self.assertEqual([p.index for p in d.pages], list(range(d.n_pages)))
        self.assertEqual(emulate(data, EmuOptions(printer="epson_fx")).detected, "epson_fx")  # forced: no splitting

    def test_empty(self):
        d = emulate(b"")
        self.assertIsNotNone(d)

    def test_garbage_does_not_crash(self):
        rng = np.random.default_rng(1)
        for i in range(30):
            emulate(bytes(rng.integers(0, 256, 400, dtype=np.uint8)))
        for p in ("epson_fx", "epson_lq", "ibm", "imagewriter", "mps"):
            emulate(bytes(rng.integers(0, 256, 600, dtype=np.uint8)), EmuOptions(printer=p))


class RenderTests(unittest.TestCase):
    def test_deterministic(self):
        d = emulate(SAMPLES["Epson ESC/P text + graphics"]())
        s = RenderSettings(dpi=72)
        a = render_page(d.pages[0], d, s)
        b = render_page(d.pages[0], d, s)
        self.assertTrue(np.array_equal(a, b))

    def test_sliders_do_something(self):
        d = emulate(b"HELLO WORLD\r\n" * 5)
        mean = lambda **kw: render_page(d.pages[0], d, RenderSettings(dpi=100, **kw)).mean()  # noqa: E731
        self.assertLess(mean(saturation=1.4), mean(saturation=0.2))  # more ink -> darker
        self.assertLess(mean(dot_size=1.5), mean(dot_size=0.6))
        self.assertLess(mean(density=1.0), mean(density=0.2))

    def test_all_looks_and_papers(self):
        d = emulate(b"x\r\n")
        for name, kw in LOOK_PRESETS.items():
            im = render_page(d.pages[0], d, RenderSettings(dpi=60, **kw))
            self.assertEqual(im.ndim, 3)

    def test_colour_ribbon(self):
        d = emulate(b"\x1b@\x1br\x01MAGENTA\r\n\x1br\x02CYAN\r\n\x1br\x00BLACK\r\n")
        self.assertEqual(sorted(d.colors_used()), ["black", "cyan", "magenta"])
        im = render_page(d.pages[0], d, RenderSettings(dpi=100)).astype(int)
        chroma = im.max(axis=2) - im.min(axis=2)
        self.assertGreater(chroma.max(), 80)  # coloured ink really is coloured
        mono = render_page(d.pages[0], d, RenderSettings(dpi=100, color_ribbon=False)).astype(int)
        self.assertLess((mono.max(axis=2) - mono.min(axis=2)).max(), chroma.max())
        self.assertEqual(emulate(b"plain\r\n").colors_used(), [])

    def test_pin_shapes_and_mechanics_are_visible(self):
        from retroimpact.settings import PIN_SHAPES
        d = emulate(b"\x1b@Draft Hamburgefonts\r\n")
        pg = d.pages[0]
        self.assertEqual(d.profile.key, "epson_fx")  # 9-pin: big dots, so the shapes are resolved
        r = lambda **kw: render_page(pg, d, RenderSettings(dpi=450, **kw)).astype(float)  # noqa: E731
        base = r(pin_shape="round", misalign=0, impact=0)
        for k in PIN_SHAPES:
            if k != "round":
                self.assertGreater(np.abs(r(pin_shape=k, misalign=0, impact=0) - base).max(), 25, k)  # the text covers a small part of the page
        self.assertGreater(np.abs(r(misalign=0.5, impact=0) - base).max(), 25)
        self.assertGreater(np.abs(r(misalign=0, impact=0.6) - base).max(), 8)
        self.assertEqual(RenderSettings(pin_shape="bogus").clamp().pin_shape, "round")

    def test_tiles_equal_page_with_every_pin_shape_and_impact(self):
        from retroimpact.render import render_region
        from retroimpact.settings import PIN_SHAPES
        d = emulate(b"Hamburgefonts\r\n" * 3)
        pg = d.pages[0]
        for k in PIN_SHAPES:
            s = RenderSettings(dpi=120, pin_shape=k, impact=1.0, misalign=0.6)
            full = render_page(pg, d, s)
            tile = render_region(pg, d, s, 90, 110, 200, 150)
            self.assertTrue(np.array_equal(full[110:260, 90:290], tile), k)

    def test_rotation_setting(self):
        self.assertEqual(RenderSettings(rotation=450).clamp().rotation, 90)
        self.assertEqual(RenderSettings(rotation=-90).clamp().rotation, 270)
        d = emulate(b"x\r\n")
        a = render_page(d.pages[0], d, RenderSettings(dpi=60, paper="letter"))
        b = render_page(d.pages[0], d, RenderSettings(dpi=60, paper="letter", rotation=90))
        self.assertEqual(a.shape[:2], b.shape[:2][::-1])

    def test_dpi_independent_size(self):
        d = emulate(b"x\r\n")
        a = render_page(d.pages[0], d, RenderSettings(dpi=72, paper="letter"))
        b = render_page(d.pages[0], d, RenderSettings(dpi=144, paper="letter"))
        self.assertEqual(a.shape[1] * 2, b.shape[1])


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.doc = emulate(SAMPLES["Epson ESC/P text + graphics"]())
        self.tmp = tempfile.mkdtemp()

    def test_pdf(self):
        p = os.path.join(self.tmp, "a.pdf")
        n = export_pdf(self.doc, p, RenderSettings(dpi=60))
        self.assertEqual(n, 2)
        self.assertTrue(open(p, "rb").read(5) == b"%PDF-")

    def test_tiff_modes(self):
        for mode, pil in (("color", "RGB"), ("gray", "L"), ("mono", "1")):
            p = os.path.join(self.tmp, f"{mode}.tif")
            export_tiff(self.doc, p, RenderSettings(dpi=60), mode=mode)
            im = Image.open(p)
            self.assertEqual((im.mode, im.n_frames), (pil, 2))

    def test_png_and_per_page_tiff(self):
        export_png(self.doc, os.path.join(self.tmp, "p.png"), RenderSettings(dpi=60))
        export_tiff(self.doc, os.path.join(self.tmp, "t.tif"), RenderSettings(dpi=60), per_page=True)
        names = sorted(os.listdir(self.tmp))
        self.assertIn("p_001.png", names)
        self.assertIn("t_002.tif", names)

    def test_cancel(self):
        from retroimpact.export import Cancelled

        with self.assertRaises(Cancelled):
            export_pdf(self.doc, os.path.join(self.tmp, "c.pdf"), RenderSettings(dpi=60), progress=lambda d, t: False)


class TileTests(unittest.TestCase):
    """Tiled rendering must be indistinguishable from a full-page render, whatever the paper, dpi or tile cut."""

    @classmethod
    def setUpClass(cls):
        cls.docs = {n: emulate(f()) for n, f in SAMPLES.items()}

    def test_tiles_equal_page(self):
        from retroimpact.render import render_region

        for name, doc in self.docs.items():
            page = doc.pages[0]
            for paper, dpi in (("tractor", 110), ("greenbar", 230), ("letter", 400)):
                s = RenderSettings(dpi=dpi, paper=paper, impact=0.8)
                full = render_page(page, doc, s, workers=1)
                H, W = full.shape[:2]
                T = 257  # deliberately not a divisor of anything
                for ty in range(0, H, T):
                    for tx in range(0, W, T):
                        t = render_region(page, doc, s, tx, ty, T, T)
                        c = full[ty:ty + T, tx:tx + T]
                        self.assertEqual(t.shape, c.shape)
                        self.assertEqual(int(np.abs(t.astype(int) - c.astype(int)).max()), 0, (name, paper, dpi, tx, ty))

    def test_rotation_tiles_equal_rotated_page(self):
        from retroimpact.render import page_pixel_size, render_region

        doc = self.docs["Epson ESC/P text + graphics"]
        page = doc.pages[0]
        up = RenderSettings(dpi=120, paper="tractor")
        base = render_page(page, doc, up, workers=1)
        for deg, k in ((90, -1), (180, 2), (270, 1)):
            s = up.replace(rotation=deg)
            want = np.rot90(base, k)
            W, H = page_pixel_size(doc, page, s)
            self.assertEqual(want.shape[:2], (H, W))
            self.assertTrue(np.array_equal(render_page(page, doc, s, workers=1), want), deg)
            for rx, ry, rw, rh in ((0, 0, 200, 150), (W - 130, H - 90, 130, 90), (37, 311, 211, 97)):
                t = render_region(page, doc, s, rx, ry, rw, rh)
                self.assertTrue(np.array_equal(t, want[ry:ry + rh, rx:rx + rw]), (deg, rx, ry))

    def test_extreme_zoom_tile_is_cheap(self):
        import time
        from retroimpact.render import render_region

        doc = self.docs["Epson ESC/P text + graphics"]
        s = RenderSettings(dpi=2400)
        render_region(doc.pages[0], doc, s, 9000, 12000, 512, 512)  # warm the noise cache
        t = time.time()
        im = render_region(doc.pages[0], doc, s, 9100, 12100, 512, 512)
        self.assertEqual(im.shape, (512, 512, 3))
        self.assertLess(time.time() - t, 1.5)  # a whole page at 2400 dpi would be ~600 megapixels

    def test_parallel_bands_match_serial(self):
        doc = self.docs["Commodore MPS (ASCII)"]
        s = RenderSettings(dpi=300)
        a = render_page(doc.pages[0], doc, s, workers=1)
        b = render_page(doc.pages[0], doc, s, workers=4)
        self.assertTrue(np.array_equal(a, b))

    def test_noise_is_dpi_independent_and_region_exact(self):
        from retroimpact.paper import base_noise, sample_noise

        n = base_noise(8.5, 11, 0.05, 7)  # 120 samples per inch
        a = sample_noise(n, 1700, 2200, 0, 0, 1700, 2200)  # 200 dpi
        b = sample_noise(n, 3400, 4400, 0, 0, 3400, 4400)  # 400 dpi
        self.assertGreater(float(np.corrcoef(a.ravel(), b[::2, ::2].ravel())[0, 1]), 0.995)
        crop = sample_noise(n, 3400, 4400, 1234, 2345, 300, 200)
        self.assertTrue(np.allclose(crop, b[2345:2545, 1234:1534], atol=1e-5))


class IconArtTests(unittest.TestCase):
    def test_icons_are_vector_and_inside_the_grid(self):
        from retroimpact.gui.icon_art import GRID, ICONS, STROKE, art

        for name in ICONS:
            ic = art(name)
            self.assertTrue(ic.strokes or ic.fills, name)
            for pts, _closed in ic.strokes:
                self.assertGreaterEqual(len(pts), 2, name)
            allp = [p for pts, _ in ic.strokes for p in pts] + [p for pts in ic.fills for p in pts]
            for x, y in allp:  # pen width included, nothing may be clipped by the icon square
                self.assertGreaterEqual(min(x, y) - STROKE / 2, -0.01, name)
                self.assertLessEqual(max(x, y) + STROKE / 2, GRID + 0.01, name)

    def test_every_icon_name_used_by_the_gui_exists(self):
        import re
        from retroimpact.gui.icon_art import ICONS

        src = ""
        for f in ("main_window.py", "settings_panel.py", "page_view.py"):
            src += open(os.path.join(os.path.dirname(__file__), "..", "retroimpact", "gui", f)).read()
        used = set(re.findall(r'icon_name="(\w+)"', src)) | set(re.findall(r'\badd\("(\w+)"', src))
        used |= set(re.findall(r'icon\("(\w+)"\)', src)) | set(re.findall(r'cursor_pixmap\("(\w+)"', src))
        self.assertTrue(used)
        self.assertEqual(used - set(ICONS), set())


if __name__ == "__main__":
    unittest.main()


class PageHidingTests(unittest.TestCase):
    def _page(self, index, n, unknown=0, x=None, y=None):
        from retroimpact.model import DotPage
        x = np.linspace(100, 9000, n).astype(np.float32) if x is None else x
        y = np.linspace(500, 14000, n).astype(np.float32) if y is None else y
        z = np.zeros(n, np.int16)
        return DotPage(index=index, width_in=8.5, height_in=11.0, x=x, y=y, pin=z, pass_no=z.astype(np.int32),
                       dirn=z.astype(np.int8), unknown=unknown)

    def test_classify(self):
        from retroimpact.analyze import BLANK, JUNK, OK, STRAY, classify_page
        self.assertEqual(classify_page(self._page(0, 0)).kind, BLANK)
        self.assertEqual(classify_page(self._page(0, 5)).kind, STRAY)
        self.assertEqual(classify_page(self._page(0, 500)).kind, OK)
        junk = classify_page(self._page(0, 500, unknown=60))
        self.assertEqual(junk.kind, JUNK)
        self.assertIn("60", junk.reason)
        far = np.full(500, 40000, np.float32)  # all the dots land far to the right of the paper
        self.assertEqual(classify_page(self._page(0, 500, x=far)).kind, JUNK)

    def test_real_files_are_not_flagged(self):
        from retroimpact.analyze import classify
        for name in ("Epson ESC/P text + graphics", "Apple ImageWriter II", "Commodore MPS (ASCII)"):
            doc = emulate(SAMPLES[name]())
            self.assertTrue(all(i.kind == "ok" for i in classify(doc)[:1]), name)

    def test_unknown_commands_are_counted_per_sheet(self):
        doc = emulate(b"\x1b@ok\r\n\x0c" + b"\x1b\x01\x1b\x02" * 40 + b"x\r\n")
        self.assertEqual(doc.pages[0].unknown, 0)
        self.assertGreaterEqual(doc.pages[1].unknown, 30)

    def test_runs(self):
        from retroimpact.analyze import runs
        self.assertEqual(runs([9, 10, 11, 20]), [(9, 11), (20, 20)])
        self.assertEqual(runs([]), [])

    def test_pageset_hides_runs_behind_a_marker(self):
        from retroimpact.analyze import JUNK, PageInfo
        from retroimpact.pageset import PageSet
        ps = PageSet(70, {i: PageInfo(i, JUNK, "x") for i in range(9, 60)})
        self.assertEqual(len(ps.visible()), 70)  # nothing is hidden until asked
        ps.set_hide_flagged(True)
        self.assertEqual(ps.items()[:9], [("page", i) for i in range(9)])
        self.assertEqual(ps.items()[9], ("gap", 9, 59))
        self.assertEqual(ps.items()[10], ("page", 60))
        self.assertEqual(len(ps.visible()), 19)
        self.assertEqual(len(ps.hidden()), 51)
        ps.reveal(9, 59)
        self.assertEqual(len(ps.visible()), 70)
        ps.set_hide_flagged(True)
        self.assertEqual(len(ps.hidden()), 51)
        ps.set_hide_flagged(False)  # the default: flagged sheets are shown like any other
        self.assertEqual(len(ps.visible()), 70)

    def test_pageset_delete_hide_and_session(self):
        from retroimpact.pageset import PageSet
        ps = PageSet(6)
        ps.delete([1, 99])  # 99 does not exist
        ps.hide([3, 4])  # hidden by hand: stays hidden whatever the automatic folding is set to
        self.assertEqual(ps.visible(), [0, 2, 5])
        self.assertEqual(ps.items(), [("page", 0), ("page", 2), ("gap", 3, 4), ("page", 5)])
        back = PageSet(6)
        back.apply(ps.state())
        self.assertEqual(back.visible(), [0, 2, 5])
        smaller = PageSet(3)  # the data changed and has fewer pages now: nothing out of range may survive
        smaller.apply(ps.state())
        self.assertEqual(smaller.visible(), [0, 2])
        ps.deleted.clear()
        self.assertEqual(ps.visible(), [0, 1, 2, 5])

    def test_banner_ink_crosses_the_perforation(self):
        line = b"\x1bK\x40\x00" + b"\xff" * 64 + b"\r\n"  # one 8-dot-high band, solid
        doc = emulate(b"\x1b@\x1bA\x08\x1b2" + line * 200)  # 200 bands = 22 in: two and a bit sheets
        self.assertGreaterEqual(doc.n_pages, 3)
        for p in doc.pages:  # nothing is lost below the bottom edge of a sheet; it continues at the top of the next
            self.assertLessEqual(float(p.y.max()), p.height_in * 1440.0 + 1)
        for p in doc.pages[1:]:
            self.assertLess(float(p.y.min()) / 1440.0, 0.12)  # the next sheet starts with the overflow, not 0.17 in blank
        # solid bands: the rows either side of the perforation line up (no white stripe)
        y = np.unique(np.round(doc.pages[0].y / 1440.0 * 72).astype(int))
        top = np.unique(np.round(doc.pages[1].y / 1440.0 * 72).astype(int))
        self.assertLessEqual(int(top.min()), 12)
        self.assertGreaterEqual(int(y.max()), int(doc.pages[0].height_in * 72) - 2)


class PetsciiTextTests(unittest.TestCase):
    GEO = b"          lET'SVIEWDOTMATRIXPRINTDATAASEASILYASpdfS.\r" * 3  # what GEOS sends for "Let's view ... as PDFs."

    def test_case_swapped_commodore_text_is_recognised(self):
        doc = emulate(self.GEO)
        self.assertEqual(doc.detected, "mps")
        forced = emulate(self.GEO, EmuOptions(printer="mps", cbm_charset="lower"))
        self.assertEqual(doc.pages[0].n_dots, forced.pages[0].n_dots)  # same glyphs as the lower/upper set
        wrong = emulate(self.GEO, EmuOptions(printer="mps", cbm_charset="upper"))
        self.assertNotEqual(doc.pages[0].n_dots, wrong.pages[0].n_dots)

    def test_ordinary_text_is_left_alone(self):
        from retroimpact.emulate import detect_printer, looks_like_petscii_text
        for data in (b"The quick brown fox jumps over the lazy dog. " * 5, b"HELLO WORLD THIS IS A LISTING\r" * 5,
                     b"10 PRINT HELLO\r20 GOTO 10\r" * 8, b"short"):
            self.assertFalse(looks_like_petscii_text(data))
            self.assertEqual(detect_printer(data), "epson_fx")


class VirtualPrinterTests(unittest.TestCase):
    def _wait(self, got, n, secs=6.0):
        import time
        end = time.time() + secs
        while len(got) < n and time.time() < end:
            time.sleep(0.05)

    def test_tcp_job_ends_on_close_and_on_idle(self):
        import socket
        from retroimpact.virtual_printer import VirtualPrinter
        got = []
        vp = VirtualPrinter(got.append, port=0, idle=0.5)
        vp.start()
        try:
            with socket.create_connection(("127.0.0.1", vp.bound_port)) as c:
                c.sendall(b"hello ")
                c.sendall(b"world")
            self._wait(got, 1)
            self.assertEqual(got[0].data, b"hello world")
            c = socket.create_connection(("127.0.0.1", vp.bound_port))  # emulator style: stays connected, goes quiet
            c.sendall(b"one")
            self._wait(got, 2)
            c.sendall(b"two")
            self._wait(got, 3)
            c.close()
            self.assertEqual([j.data for j in got], [b"hello world", b"one", b"two"])
            self.assertEqual([j.number for j in got], [1, 2, 3])
        finally:
            vp.stop()

    def test_fifo_job(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("no FIFOs on this platform")
        from retroimpact.virtual_printer import VirtualPrinter
        got = []
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "p.pipe")
            vp = VirtualPrinter(got.append, port=None, pipe=path, idle=0.5)
            vp.start()
            try:
                with open(path, "wb") as f:
                    f.write(b"via fifo")
                self._wait(got, 1)
                self.assertEqual(got[0].data, b"via fifo")
            finally:
                vp.stop()
            self.assertFalse(os.path.exists(path))

    def test_nothing_to_listen_on_is_an_error(self):
        from retroimpact.virtual_printer import VirtualPrinter
        with self.assertRaises(OSError):
            VirtualPrinter(lambda j: None, port=None, pipe=None).start()

    def test_received_job_converts_like_a_file(self):
        import socket
        from retroimpact.cli import build_parser, convert, render_settings
        from retroimpact.virtual_printer import VirtualPrinter
        got = []
        vp = VirtualPrinter(got.append, port=0, idle=0.5)
        vp.start()
        try:
            with socket.create_connection(("127.0.0.1", vp.bound_port)) as c:
                c.sendall(b"\x1b@Hello from the network\r\n\x0c")
            self._wait(got, 1)
        finally:
            vp.stop()
        a = build_parser().parse_args(["--dpi", "100"])
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "job.png")
            n = convert(got[0].data, out, a, render_settings(a), "test")
            self.assertGreaterEqual(n, 1)
            self.assertGreater(os.path.getsize(out), 1000)


class FakeWin32:
    """Stands in for the Win32 pipe calls so the Windows logic can be checked on any platform."""

    def __init__(self, exists=False, script=()):
        self.exists, self.script = exists, list(script)  # script items: bytes (data) or None (the other end closes)
        self.opened = 0
        self.log = []

    def create(self, name):
        self.log.append(("create", name))
        return (None, 231) if self.exists else (7, 0)

    def connect(self, h):
        return "connected" if self.script else "listening"

    def open_client(self, name):
        self.opened += 1
        return 9 if self.opened >= 3 and self.script else None  # the pipe "appears" on the third try

    def peek(self, h):
        if not self.script:
            return 0
        item = self.script[0]
        if item is None:
            self.script.pop(0)
            return None
        return len(item)

    def read(self, h, n):
        return self.script.pop(0)

    def disconnect(self, h):
        self.log.append("disconnect")

    def close(self, h):
        self.log.append("close")


class WindowsPipeLogicTests(unittest.TestCase):
    def _run(self, api, want):
        import threading
        import time
        from retroimpact.virtual_printer import VirtualPrinter
        got = []
        vp = VirtualPrinter(got.append, port=None, pipe="RetroImpact", idle=0.4)
        vp._is_windows = True
        vp._make_win_api = lambda: api
        t = threading.Thread(target=vp._win_pipe_loop, daemon=True)
        t.start()
        end = time.time() + 6
        while len(got) < want and time.time() < end:
            time.sleep(0.05)
        vp.stop_event.set()
        t.join(3)
        return vp, got

    def test_we_create_the_pipe_and_the_emulator_connects(self):
        api = FakeWin32(script=[b"\x1b@abc", b"def", None])
        vp, got = self._run(api, 1)
        self.assertEqual(api.log[0], ("create", r"\\.\pipe\RetroImpact"))
        self.assertEqual([j.data for j in got], [b"\x1b@abcdef"])
        self.assertEqual(vp.bytes_in, 8)
        self.assertIn("disconnect", api.log)

    def test_pipe_made_by_the_emulator_is_joined_as_a_client(self):
        api = FakeWin32(exists=True, script=[b"hello", None])
        vp, got = self._run(api, 1)
        self.assertGreaterEqual(api.opened, 3)  # retried until the pipe appeared
        self.assertEqual([j.data for j in got], [b"hello"])

    def test_pipe_names_get_the_windows_prefix(self):
        from retroimpact.virtual_printer import win_pipe_path
        self.assertEqual(win_pipe_path("LPT1"), "\\\\.\\pipe\\LPT1")
        self.assertEqual(win_pipe_path("\\\\.\\pipe\\X"), "\\\\.\\pipe\\X")

    def test_unusable_pipe_name_is_reported(self):
        class Bad(FakeWin32):
            def create(self, name):
                return None, 123
        from retroimpact.virtual_printer import VirtualPrinter
        vp = VirtualPrinter(lambda j: None, port=None, pipe="bad", idle=0.4)
        vp._is_windows = True
        vp._make_win_api = lambda: Bad()
        with self.assertRaises(OSError):
            vp.start()


class HotFolderTests(unittest.TestCase):
    def test_new_and_changed_files_are_taken_once_they_settle(self):
        import time
        from retroimpact.virtual_printer import VirtualPrinter
        got = []
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "old.prn"), "wb") as f:
                f.write(b"was here before")
            vp = VirtualPrinter(got.append, port=None, watch=d)
            vp.watch_settle = 0.4
            vp.start()
            try:
                p = os.path.join(d, "new.prn")
                with open(p, "wb") as f:
                    f.write(b"part one ")
                time.sleep(0.2)
                with open(p, "ab") as f:  # still being written: must not be taken yet
                    f.write(b"part two")
                with open(os.path.join(d, "page.pdf"), "wb") as f:
                    f.write(b"output, not input")
                end = time.time() + 6
                while not got and time.time() < end:
                    time.sleep(0.05)
                time.sleep(1.0)
                self.assertEqual([(j.label, j.data) for j in got], [("new.prn", b"part one part two")])
                self.assertEqual(got[0].path, p)
                self.assertEqual(got[0].name, "new.prn")
            finally:
                vp.stop()

    def test_existing_files_can_be_included(self):
        import time
        from retroimpact.virtual_printer import VirtualPrinter
        got = []
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "a.prn"), "wb") as f:
                f.write(b"x")
            vp = VirtualPrinter(got.append, port=None, watch=d, include_existing=True)
            vp.watch_settle = 0.3
            vp.start()
            try:
                end = time.time() + 5
                while not got and time.time() < end:
                    time.sleep(0.05)
            finally:
                vp.stop()
        self.assertEqual([j.label for j in got], ["a.prn"])


class CaptureFixTests(unittest.TestCase):
    STREAM = b"\x1b@Hello, doubled world\r\n\x0c" + b"\x1b*\x00\x04\x00\xff\xff\x81\x00" * 3

    @staticmethod
    def doubled(b, lead=b"\x00"):
        return lead + bytes(x for c in b for x in (c, c))

    def test_doubled_stream_is_collapsed(self):
        from retroimpact.capture_fix import undouble
        out, note = undouble(self.doubled(self.STREAM))
        self.assertEqual(out, self.STREAM)
        self.assertTrue(note)
        out, note = undouble(self.doubled(self.STREAM, lead=b""))
        self.assertEqual(out, self.STREAM)

    def test_normal_data_is_left_alone(self):
        from retroimpact.capture_fix import undouble
        for data in (self.STREAM, b"Plain text, with a few  double spaces and ll.\r\n", b"short", os.urandom(500)):
            self.assertEqual(undouble(data), (data, ""))

    def test_emulate_gives_same_result_for_doubled_capture(self):
        a = emulate(self.STREAM)
        b = emulate(self.doubled(self.STREAM))
        self.assertEqual(a.n_pages, b.n_pages)
        self.assertEqual(a.pages[0].n_dots, b.pages[0].n_dots)
        self.assertTrue(b.fixes and not a.fixes)
        self.assertEqual(emulate(self.doubled(self.STREAM), EmuOptions(fix_capture=False)).n_unknown >= 0, True)

    def test_port_probes_are_not_jobs(self):
        from retroimpact.capture_fix import is_probe
        from retroimpact.virtual_printer import VirtualPrinter
        for p in (b"\x55\xaa\x00", b"\x04", b"\x00"):
            self.assertTrue(is_probe(p))
        self.assertFalse(is_probe(b"A\x0c"))
        got = []
        vp = VirtualPrinter(got.append, port=None)
        vp._emit(b"\x55\xaa\x00", "pipe")
        vp._emit(b"\x1b@hi", "pipe")
        self.assertEqual([j.data for j in got], [b"\x1b@hi"])
        self.assertEqual((vp.probes, vp.jobs), (1, 1))


class GraphicsDetectionTests(unittest.TestCase):
    def test_24pin_graphics_full_of_commodore_lookalike_bytes_is_still_epson(self):
        import random
        rnd = random.Random(7)
        out = bytearray(b"\x1b@\x1bt\x01\x1b2\x1bCF")
        for _ in range(40):
            out += b"\x1b$\x00\x00\x1b*'" + bytes([200, 0])
            for _c in range(600):  # 200 columns x 3 bytes; lots of 0x08 / 0x0C / high bytes, as real bitmaps contain
                out.append(rnd.choice([0x00, 0x08, 0x0C, 0x80, 0x91, 0xC1, 0xFF, 0x18, 0x10]))
            out += b"\x1b3\x18\r\n"
        data = bytes(out) * 3
        self.assertEqual(detect_printer(data), "epson_lq")
        doc = emulate(data)
        self.assertLess(doc.n_pages, 10)  # not thousands of "form feeds" taken from bitmap bytes
        self.assertEqual(doc.n_unknown, 0)


class PlaybackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        text = b"\x1b@" + b"".join(b"Line %d of the playback test page\r\n" % i for i in range(1, 9)) + b"\x0c"
        cls.doc = emulate(text)
        cls.s = RenderSettings().replace(dpi=60)

    def test_timeline_follows_the_head(self):
        from retroimpact.playback import build_timeline
        page = self.doc.pages[0]
        tl = build_timeline(page, 0.5)
        self.assertGreaterEqual(len(tl.passes), 8)
        starts = [p.t0 for p in tl.passes]
        self.assertEqual(starts, sorted(starts))
        self.assertTrue(all(p.t1 > p.t0 for p in tl.passes))
        # within a pass, dots are struck in the head's direction of travel
        for p in tl.passes[:3]:
            idx = np.flatnonzero((page.pass_no == page.pass_no[np.argmin(np.abs(tl.t_dot - p.t0))]))
            xs, ts = page.x[idx], tl.t_dot[idx]
            order = np.argsort(ts)
            d = np.diff(xs[order])
            self.assertTrue((d >= 0).all() if p.dirn == 0 else (d <= 0).all())
        x, y, printing, j = tl.state(tl.passes[2].t0 + 0.01)
        self.assertTrue(printing)
        self.assertEqual(j, 2)

    def test_frames_fill_in_as_time_passes(self):
        from retroimpact.playback import PagePlayer
        pl = PagePlayer(self.doc, self.doc.pages[0], self.s, dpi=60)
        blank = pl.frame(0.0, follow=False, head=False).astype(int)  # the bare sheet (tractor holes are dark too)
        ink = lambda t: int((np.abs(pl.frame(t, follow=False, head=False).astype(int) - blank).sum(axis=2) > 150).sum())  # noqa: E731
        a, b, c = ink(0.0), ink(pl.duration * 0.5), ink(pl.duration)
        self.assertEqual(a, 0)
        self.assertGreater(b, 20)
        self.assertGreater(c, b)
        self.assertEqual(pl.frame(0.5, follow=True).shape[1], pl.w)
        self.assertLess(pl.frame(0.5, follow=True).shape[0], pl.h)

    def test_gif_and_sound(self):
        from retroimpact.playback import PagePlayer, export_gif, synth_audio, write_wav
        pl = PagePlayer(self.doc, self.doc.pages[0], self.s, dpi=40)
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "p.gif")
            n = export_gif(pl, out, speed=4.0, fps=8)
            self.assertGreater(n, 3)
            with Image.open(out) as im:
                self.assertTrue(3 < im.n_frames <= n)  # identical neighbouring frames are merged by the GIF writer
            wav = synth_audio(pl.timeline, 4.0)
            self.assertAlmostEqual(len(wav) / 22050, pl.duration / 4.0 + 0.5, delta=0.1)
            self.assertGreater(int(np.abs(wav).max()), 1000)
            write_wav(os.path.join(d, "p.wav"), wav)
            self.assertGreater(os.path.getsize(os.path.join(d, "p.wav")), 1000)


class ImageWriterColourTests(unittest.TestCase):
    def _stream(self, rows):
        out = b"\x1bT16"
        for colour, data, announced in rows:
            out += b"\r\x1bK" + str(colour).encode() + b"\x1b>\x1bP\x1bG%04d" % announced + data
        return out + b"\r\x1bK0\r\n"

    def test_ribbon_colours_are_used(self):
        # yellow, red and blue passes over one line: ESC K 1 / 2 / 3
        data = self._stream([(1, b"\xff" * 40, 40), (2, b"\x0f" * 40, 40), (3, b"\xf0" * 40, 40)])
        doc = emulate(data, EmuOptions(printer="imagewriter"))
        p = doc.pages[0]
        self.assertEqual(sorted(set(p.color.tolist())), [1, 2, 4])  # magenta (red), cyan (blue), yellow
        self.assertEqual(doc.warnings, [])

    def test_short_graphics_row_resyncs_instead_of_eating_the_next_commands(self):
        # the second row announces 1280 bytes but only 100 arrive before the next colour change
        data = self._stream([(1, b"\xff" * 1280, 1280), (2, b"\x0f" * 100, 1280), (3, b"\xf0" * 1280, 1280)])
        doc = emulate(data, EmuOptions(printer="imagewriter"))
        self.assertEqual(sorted(set(doc.pages[0].color.tolist())), [1, 2, 4])  # the blue pass was not swallowed
        self.assertEqual(len(doc.warnings), 1)
        self.assertEqual(doc.n_unknown, 0)


class ImageWriterDetectionTests(unittest.TestCase):
    def _colour_print(self, bands=40):
        out = b"\x1bT14"
        rnd = bytes((i * 37 + 11) % 128 for i in range(1280))  # 7-pin bytes, <= 0x7F, with the odd 0x1b in them
        for _ in range(bands):
            for k in "1230":
                out += b"\r\x1bK" + k.encode() + b"\x1b>\x1bP\x1bG1280" + (rnd if k != "0" else bytes(1280))
            out += b"\r\n"
        return out

    def test_colour_ribbon_stream_is_not_epson(self):
        data = self._colour_print()
        self.assertEqual(detect_printer(data), "imagewriter")
        self.assertEqual(split_segments(data), [(0, len(data), "imagewriter")])

    def test_digit_counts_are_masked(self):
        data = b"\x1bG0004\x1bK\x00\x00\x1b@ab"
        self.assertEqual(mask_graphics(data)[:11], b"\x1bG0004" + bytes(4) + b"\x1b")


class RealCaptureTests(unittest.TestCase):
    """Captures from real emulator runs (tests/data); they guard detection and layout against regressions."""

    def _load(self, name):
        with open(os.path.join(os.path.dirname(__file__), "data", name), "rb") as fh:
            return fh.read()

    def test_geos_imagewriter_text(self):
        # GEOS ImageWriter driver over VICE userport RS-232 (2400 baud): text as 10 bands of ESC S bitmaps, 8-bit data
        data = self._load("geos_imagewriter_text.prn")
        self.assertEqual(detect_printer(data), "imagewriter")
        self.assertEqual(split_segments(data), [(0, len(data), "imagewriter")])
        doc = emulate(data)
        self.assertEqual(len(doc.pages), 1)
        self.assertEqual(doc.warnings, [])
        self.assertEqual(doc.n_unknown, 0)
        self.assertGreater(doc.pages[0].n_dots, 3000)

    def test_geos_imagewriter_ii_ds_is_not_epson(self):
        # GEOS "ImageWriter II DS" (double strike): every band is ESC P ESC S1280 <bitmap>, repeated. The ESC P / ESC S
        # of a long job used to outweigh the ImageWriter giveaways and the whole page was read as an Epson.
        data = self._load("geos_imagewriter_ds_text.prn")
        self.assertEqual(detect_printer(data), "imagewriter")
        self.assertEqual(split_segments(data), [(0, len(data), "imagewriter")])
        doc = emulate(data)
        self.assertEqual(doc.detected, "imagewriter")
        self.assertEqual(doc.n_unknown, 0)

    def test_applewin_printshop_colour(self):
        # Print Shop Color from AppleWin: 266 complete ESC G1280 rows, four ribbon passes per band (the last is blank)
        data = self._load("applewin_printshop_colour.prn")
        self.assertEqual(detect_printer(data), "imagewriter")
        doc = emulate(data)
        self.assertEqual(len(doc.pages), 1)
        self.assertEqual(doc.warnings, [])
        self.assertEqual(sorted(set(doc.pages[0].color.tolist())), [1, 2, 4])


class InkSetTests(unittest.TestCase):
    def test_default_inks_are_the_original_ribbon_colours(self):
        from retroimpact.settings import COLOR_INKS
        s = RenderSettings()
        for ink in (1, 2, 4):
            self.assertEqual(s.ink_rgb(ink), COLOR_INKS[ink][1])

    def test_custom_and_bad_inks(self):
        s = RenderSettings(ink_set="custom", custom_inks="#ff0000,#00ff00,#0000ff").clamp()
        self.assertEqual([s.ink_rgb(i) for i in (1, 2, 4)], [(255, 0, 0), (0, 255, 0), (0, 0, 255)])
        bad = RenderSettings(ink_set="custom", custom_inks="nonsense").clamp()
        self.assertEqual(bad.ink_rgb(1), RenderSettings().ink_rgb(1))  # falls back instead of crashing
        self.assertEqual(RenderSettings(ink_set="nope").clamp().ink_set, "ribbon")
        self.assertEqual(RenderSettings.from_dict(s.to_dict()).ink_rgb(2), (0, 255, 0))

    def test_ink_set_changes_the_colour_page(self):
        with open(os.path.join(os.path.dirname(__file__), "data", "geos_okimate20_colour.prn"), "rb") as fh:
            doc = emulate(fh.read())
        base = RenderSettings(dpi=72, ink_set="ribbon", paper="letter", paper_texture=0)
        a = render_page(doc.pages[0], doc, base)
        b = render_page(doc.pages[0], doc, base.replace(ink_set="vivid"))
        self.assertEqual(a.shape, b.shape)
        self.assertGreater(int(np.abs(a.astype(int) - b.astype(int)).sum()), 1000)
        c = render_page(doc.pages[0], doc, base.replace(ink_set="ribbon"))
        self.assertTrue(np.array_equal(a, c))


class PrinterPreviewTests(unittest.TestCase):
    """The Printers tab: first page as every printer type, with a plain-language verdict for each."""

    def _load(self, name):
        with open(os.path.join(os.path.dirname(__file__), "data", name), "rb") as fh:
            return fh.read()

    def test_every_printer_gets_a_tile_and_the_right_one_looks_fine(self):
        from retroimpact.preview import KEYS, preview_all
        res = preview_all(self._load("geos_imagewriter_ds_text.prn"), EmuOptions(), RenderSettings(), width=120)
        self.assertEqual(list(res), KEYS)
        self.assertEqual(res["auto"].detected, "imagewriter")
        self.assertEqual(res["imagewriter"].level, "ok")
        self.assertEqual(res["imagewriter"].summary, "Looks fine")
        self.assertEqual(res["auto"].summary, "Picked ImageWriter")
        for k in ("epson_fx", "ibm", "star"):  # ImageWriter data read as Epson loses almost all of its dots
            self.assertEqual(res[k].level, "warn", k)
            self.assertIn("far less", res[k].summary)
        self.assertEqual(res["imagewriter"].image.shape[1], 120)

    def test_garbled_reading_is_explained(self):
        from retroimpact.preview import preview_all
        res = preview_all(self._load("applewin_printshop_colour.prn"), EmuOptions(), RenderSettings(), width=100)
        self.assertEqual(res["epson_fx"].level, "bad")
        self.assertEqual(res["epson_fx"].summary, "Graphics out of step")
        self.assertIn("row lengths", res["epson_fx"].detail)
        self.assertEqual(res["imagewriter"].level, "ok")

    def test_identical_pages_are_named(self):
        from retroimpact.preview import preview_all
        res = preview_all(SAMPLES["Epson ESC/P text + graphics"](), EmuOptions(), RenderSettings(), width=100)
        self.assertEqual(res["epson_fx"].signature, res["star"].signature)
        self.assertIn("same as", res["epson_fx"].summary)
        self.assertIn("exactly the same page", res["epson_fx"].detail)

    def test_nothing_printed(self):
        from retroimpact.preview import preview_one
        r = preview_one(b"", "epson_fx", EmuOptions(), RenderSettings(), width=80)
        self.assertEqual((r.level, r.summary), ("bad", "Nothing printed"))
        self.assertIsNone(r.image)

    def test_only_the_start_of_a_big_capture_is_converted(self):
        from retroimpact.preview import PREVIEW_BYTES, preview_one
        big = b"hello world\r\n" * (PREVIEW_BYTES // 5)
        r = preview_one(big, "epson_fx", EmuOptions(), RenderSettings(), width=80)
        self.assertEqual(r.level, "ok")
        self.assertIsNotNone(r.image)


class PausedPrintTests(unittest.TestCase):
    """A print that arrives in bursts must not be cut into several jobs in the middle of a bitmap row."""

    def test_unfinished_graphics(self):
        from retroimpact.capture_fix import unfinished_graphics as u
        row = b"\x1bS0004" + b"\x01\x02\x03\x04"
        self.assertFalse(u(b"hello"))
        self.assertFalse(u(row + b"\r\n"))
        self.assertTrue(u(row[:-1]))  # one byte short
        self.assertTrue(u(row[:4]))  # header cut off
        self.assertTrue(u(b"\x1bg002" + bytes(15)))  # ESC g 002 announces 16 bytes
        self.assertFalse(u(b"\x1bg002" + bytes(16)))
        self.assertTrue(u(b"\x1b*\x00\x04\x00" + bytes(3)))  # Epson ESC * m n1 n2
        self.assertFalse(u(b"\x1b*\x00\x04\x00" + bytes(4)))
        self.assertFalse(u(b"\r\x1bK1\x1bK2\r"))  # ImageWriter ribbon colours are not Epson ESC K n1 n2

    def test_tcp_job_waits_for_the_rest_of_a_row(self):
        import socket
        import time
        from retroimpact.virtual_printer import VirtualPrinter
        got = []
        vp = VirtualPrinter(got.append, port=0, idle=0.4)
        vp.start()
        try:
            c = socket.create_connection(("127.0.0.1", vp.bound_port))
            c.sendall(b"\x1bS0010" + bytes(4))  # row announced as 10 bytes, 4 sent
            time.sleep(1.2)  # well past the idle time
            self.assertEqual(got, [])
            c.sendall(bytes(6) + b"\r\n")
            end = time.time() + 5
            while not got and time.time() < end:
                time.sleep(0.05)
            c.close()
            self.assertEqual(len(got), 1)
            self.assertEqual(got[0].data, b"\x1bS0010" + bytes(10) + b"\r\n")
        finally:
            vp.stop()


class GeosFontSamplerTests(unittest.TestCase):
    def test_geos_font_sampler_renders_without_warnings(self):
        # a GEOS print that arrived in six bursts, joined: every cut fell in the middle of a bitmap row
        with open(os.path.join(os.path.dirname(__file__), "data", "geos_imagewriter_fonts.prn"), "rb") as fh:
            data = fh.read()
        self.assertEqual(detect_printer(data), "imagewriter")
        doc = emulate(data)
        self.assertEqual(len(doc.pages), 1)
        self.assertEqual(doc.warnings, [])
        self.assertEqual(doc.n_unknown, 0)


class OkimateTests(unittest.TestCase):
    """Okimate 20 colour capture from GeoPaint under GEOS (VICE): three ribbon passes per line, bare CRs between."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(os.path.dirname(__file__), "data", "geos_okimate20_colour.prn"), "rb") as f:
            cls.data = f.read()
        cls.doc = emulate(cls.data)

    def test_detected_as_okimate(self):
        self.assertEqual(detect_printer(self.data), "okimate")
        self.assertEqual(self.doc.detected, "okimate")
        self.assertEqual(split_segments(self.data), [(0, len(self.data), "okimate")])

    def test_three_ribbon_colours_and_pages(self):
        self.assertEqual(set(self.doc.colors_used()), {"yellow", "magenta", "cyan"})
        self.assertEqual(self.doc.n_pages, 3)  # two pages of banner, then the GeoPaint picture sent in the colour-band format
        self.assertGreater(self.doc.pages[0].n_dots, 100000)
        self.assertFalse(self.doc.warnings)

    def test_geos_colour_bands(self):
        with open(os.path.join(os.path.dirname(__file__), "data", "geos_okimate20_geopaint_colour.prn"), "rb") as f:
            data = f.read()
        self.assertEqual(detect_printer(data), "okimate")
        doc = emulate(data)
        self.assertEqual(doc.n_pages, 1)
        self.assertEqual(set(doc.colors_used()), {"yellow", "magenta", "cyan"})
        x0, y0, x1, y1 = doc.pages[0].extent_in()
        self.assertTrue(2.5 < x1 - x0 < 4.5 and 1.4 < y1 - y0 < 2.2)  # a picture of about 3.3 x 1.8 in

    def test_passes_of_one_line_overprint(self):
        blk = b"\x1bK\x08\x00" + b"\xfe" * 8
        raw = b"\x1bA\x08" + (blk + b"\r") * 12
        doc = emulate(raw)
        self.assertEqual(doc.detected, "okimate")
        p = doc.pages[0]
        self.assertEqual(set(int(c) for c in p.color), {4, 1, 2})
        rows = sorted({round(float(y) / 20.0) for y in p.y})  # dot rows in 1/72 in
        self.assertEqual(len(rows), 4 * 7)  # four lines of 7 dots; each Y/M/C triple overprints
        self.assertEqual(rows[7] - rows[0], 8)  # the next line is ESC A 8 = 8/72 in lower


class OkimateEscapeTests(unittest.TestCase):
    def test_doubled_03_is_one_data_byte(self):
        # a pass is 03, 3-byte columns, 03 02, 8D; a data byte of 03 is sent as 03 03
        col = b"\x03\x03\x00\x00"  # byte 0 = 0x03 (top pixel row set), bytes 1 and 2 = 0
        data = b"\x1c\x1bP" + b"\x03" + b"\x00\x00\x00" * 5 + col * 3 + b"\x03\x02\x8d\x1b\x0a\x10"
        data += (b"\x03\x00\x00\x00\x03\x02\x8d\x1b\x0a\x10") * 6
        doc = emulate(data)
        self.assertEqual(doc.detected, "okimate")
        self.assertEqual(sum(p.n_dots for p in doc.pages), 3)  # three columns, one dot each; not shifted by the escape


class OkimateGeowriteTests(unittest.TestCase):
    def test_black_text_passes_are_okimate_not_epson(self):
        with open(os.path.join(os.path.dirname(__file__), "data", "geos_okimate20_geowrite_text.prn"), "rb") as f:
            data = f.read()
        self.assertEqual(detect_printer(data), "okimate")
        doc = emulate(data)
        self.assertEqual(doc.n_pages, 1)
        self.assertGreater(doc.pages[0].n_dots, 5000)
