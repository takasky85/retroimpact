# RetroImpact

View raw data from 80s dot matrix printers as easily as you can view a PDF.

Turns raw 1980s dot-matrix printer data (as captured from VICE, MAME, Basilisk II, SheepShaver, a serial
logger, a `.prn` file…) into PDF / TIFF / PNG pages that look like they came off a real, tired impact printer:
individual ribbon-struck dots, ink variation, bidirectional head misalignment, weak pins, paper grain,
tractor-feed holes and greenbar stock.

Python 3.10+ · Windows / Linux / macOS · GUI (PySide6) + command line.

## Install & run

    pip install -r requirements.txt
    python run_retroimpact.py                 # GUI
    python run_retroimpact.py capture.prn     # GUI with a file
    python -m retroimpact capture.prn -o out.pdf --look "Worn ribbon" --dpi 300   # no GUI
    python -m retroimpact capture.prn -o out.tif --tiff-mode mono --dpi 300

`File ▸ Load Sample` generates Epson, ImageWriter and Commodore streams so you can try it without a capture.
Drag & drop a file onto the window to open it. F5 reloads.

## The window

* **Left quarter** - *Print head* (dot size, density), *Ribbon* (saturation, wear, colour), *Mechanics*
  (head misalignment, jitter, paper impact), *Paper* (stock, texture, tractor holes), seed, look presets.
* **Right** - scrollable pages. Each page is cut into 512 px tiles rendered on background threads at the exact zoom, and only
  the tiles on screen (plus a ring around them) are drawn, so zooming to 2400 dpi costs the same as fit-to-width. A low-res
  preview shows underneath until a tile arrives.
  Toolbar: Hand tool (drag to pan; middle-mouse always pans), zoom −/+/box (Fit width, Fit page, 25–400 %, or type a
  value up to 800 %), single-page or two-page continuous layout. Ctrl+wheel zooms.
* **Thumbnails and page organiser** (View > Thumbnails, F4) - small page pictures with the page number underneath. They
  reflow into as many columns as the panel is wide (drag the splitter), and the slider or Ctrl + mouse wheel changes their
  size. Click one to jump to it;
  select several (Ctrl/Shift-click) and press **Delete** to drop them from the view and from every export, **Hide** to fold
  them away, **Restore** to bring everything back. Deletions survive quitting with the session.
* **Blank and garbled pages** - sheets with nothing on them, a handful of stray dots, a flood of commands the printer would
  not understand, or dots landing off the paper (the typical result of missing or damaged data) are folded into one dashed
  marker such as "Pages 10-60 hidden (garbled)" with a **Show** button, so you can see that something is missing without
  scrolling through 50 pages of junk. This is **off by default** (every sheet is shown); turn it on with View > "Hide blank
  and garbled pages". Nothing is thrown away. The test
  is conservative: a damaged page that still looks like plausible text is not caught, so select it in the rail and use Hide.
* **Zoom tool** (Z) - drag a box around what you want to see and the view zooms to fill the window with it; a click still zooms
  in, Shift+click zooms out.
* **Rotate & banners** - View ▸ Rotate view clockwise / counter-clockwise (Ctrl+] / Ctrl+[), or the two toolbar buttons.
  Print Shop and PrintMaster banners print sideways, so turning the view 90° makes them readable. The banner button
  ("all pages in one row") lays the sheets edge to edge in a single horizontal strip, scrolling sideways. Export has a
  "rotate pages like the view" checkbox; the rotation and layout are restored with the session.
* **Colour ribbon** - `ESC r n` colour changes (magenta, cyan, yellow, and the overprinted violet, red and green) are rendered
  as separate inks with their own slight misregistration, multiplied onto the paper like real ribbon. The "Colour
  ribbon" checkbox (CLI: `--mono`) turns it off to get the black-only look. The three colour inks can be changed under
  *Ribbon ▸ Colour inks*: pick a set (standard ribbon, vivid, process CMY, faded, thermal transfer) or click the
  Magenta / Cyan / Yellow swatches to choose your own. Commodore / Okimate colours are mixed from these three inks
  (there is no separate C64 palette: a real Okimate could only print what its three ribbon colours could make), so
  this is where the "realistic" look is tuned. The default is the original ribbon colours.
* **Commodore graphics gap** - MPS bit-image lines are 7 pins high but the line feed is 1/9 in, which leaves a blank
  row between graphics lines. *Options ▸ Commodore graphics gap*: **Authentic** keeps the stripe, **Fill** keeps the
  spacing but fills the stripe by blending the dots above and below it, **Close** removes the stripe (shrinks the picture).
  CLI: `--mps-gap gap|fill|close`.
* **Printers tab** - the left column has two tabs, *Look* and *Printers*. *Printers* shows page 1 of the current file as
  every printer type would draw it (Auto-detect plus one tile per type), each with a one-line verdict and a tooltip
  that explains it: "Looks fine", "Graphics out of step", "Doesn't understand the data", "Prints far less than others",
  "N commands ignored", "Looks fine, same as Epson FX" and so on. Click a tile to use that printer for the tab. Previews
  are made in the background only while the tab is open, from the first ~120 KB of the file, in a plain clean look
  that keeps your colour inks.
* **Printer buttons** - bottom-left, pinned under both tabs of the window, always visible: Auto-detect plus one button per printer type, and a
  "Try next printer" button (F8) that steps through them until the page looks right. The choice belongs to the tab
  that is showing (every tab keeps its own, including Auto-detect) and is remembered with the session.
* **Options** - printer (auto-detect or force, this tab only), character set, Commodore charset, newline handling, blank-line handling (keep / collapse / remove), ESC A immediate vs strict-Epson, blank
  form-feed skipping, page-length handling, wide carriage, forced NLQ.
* **Tabs** - open several captures at once (File ▸ Open takes multiple files, or drag and drop); Ctrl+W closes, Ctrl+Tab switches.
* **Mixed captures** - VICE keeps appending to one file when you switch printer type. In auto mode the stream is split at the
  point where the printer changes (Epson ↔ Commodore ↔ ImageWriter) and each part is emulated with its own printer.
* **Session** - the files you had open (with zoom, page and view mode) come back on the next launch; File ▸ Open Recent
  lists the last ten. Turn it off with File ▸ "Reopen last files on startup".
* **Toolbar** - crisp vector line icons (drawn in your theme's text colour, so they work on dark and light themes and any HiDPI
  scale): open, export, reload, hand and zoom tools, zoom −/+/fit, 1-page / 2-page, continuous / single,
  page navigation, tractor holes, re-roll dots.
* **File ▸ Export** PDF (JPEG or lossless), TIFF (colour, grey, 1-bit CCITT G4, optionally one file per page), PNG.

### What the sliders mean

| Slider | Meaning |
|---|---|
| Dot size | pin diameter relative to the real pin of the selected printer |
| Pin shape | footprint of one strike: round, oval (head moving), rough (worn pin), square (blunt pin), ring (heavy strike) |
| Density | how completely the dots strike: 1 = clean; lower = weak and missing strikes |
| Saturation | ribbon ink load: pale grey ↔ dense black (or colour richness for coloured ribbons) |

## Virtual printer (receive jobs over TCP or a named pipe)

RetroImpact can pretend to be a printer. **File ▸ Virtual printer…** (Ctrl+Shift+P) starts a listener; every job that
arrives opens in its own tab (and, if you leave "keep a copy" on, is saved as a `.prn` in `~/RetroImpact Jobs`).

* **TCP, raw / JetDirect, port 9100** - this computer only unless you tick "accept jobs from other computers".
  * MAME: `-bitb socket.127.0.0.1:9100` (or point the emulated printer's bitbanger at that socket)
  * Windows: Add a printer > Standard TCP/IP Port > `127.0.0.1`, raw, port 9100
  * Linux / macOS: `nc 127.0.0.1 9100 < capture.prn`
* **Named pipe** - Windows `\\.\pipe\RetroImpact`; Linux/macOS a FIFO such as `/tmp/retroimpact.pipe` (`cat capture.prn > /tmp/retroimpact.pipe`).
* A job ends when the sender closes the connection, or after a few seconds of silence (emulators keep the connection
  open), so two prints a few seconds apart arrive as two jobs. A print that pauses in the middle of a bitmap row (the
  GEOS ImageWriter driver on VICE sends a few hundred bytes at a time, with gaps of several seconds) is not cut there: the
  job waits for the rest of the row, up to 30 s of silence (or 8x the idle time, if that is longer).
* **VICE + GEOS (ImageWriter)**: userport RS232 on, device = a socket row such as `127.0.0.1:9100`, **IP232 off**, the same
  baud (2400) in VICE's userport and device rows and in GEOS's "Choose RS-232 Serial Transfer Rate" box.
* Jobs are capped at 64 MB.
* **Hot folder** - tick "Watch a folder" and every file that appears there (or changes) opens as a tab once it has stopped
  growing. From the command line: `python -m retroimpact --watch inbox --outdir converted` converts each file (including
  ones already there) and skips `.pdf/.tif/.png` so the output can live in the same folder.

### 86Box

Set the LPT port's device to **Named Pipe**, mode **Auto**, the pipe path to the one shown in the dialog
(`\\.\pipe\RetroImpact`, or just `RetroImpact`), and the cable type to **Raw SPP with handshaking** - that is the one that
behaves like a printer cable; the LapLink / Bidirectional / DirectParallel types are meant for Direct Cable Connection and
leave a printer driver waiting forever. Either program may be started first (RetroImpact creates the pipe, or joins the one
86Box made). In the guest, use an Epson FX/LQ driver (or print from DOS). The dialog shows bytes received and the pipe state,
so you can see whether anything arrives. If the virtual printer is already running, "Apply changes" restarts it with the new settings; "Stop listening" is a separate button.
86Box's pipe can deliver **every byte twice** (`1B 1B 40 40`...) and may send a stray first byte; RetroImpact detects a
capture made entirely of identical byte pairs and collapses it automatically (the info panel says "Repaired: ..."), and it
ignores the 1-4 byte port probes (`55 AA 00`, `04`) that a Windows driver sends before printing. Printer-type detection ignores the bytes inside graphics commands, so a bitmap-heavy Windows print (which can contain any byte value) is not mistaken for another printer. Thumbnails are drawn only for the pages in view, so captures with thousands of sheets stay responsive. **File ▸ Save raw data as...**
keeps the bytes exactly as received.
On Linux/macOS 86Box's pipe is a pair of `.in`/`.out` FIFOs, which the FIFO listener here does not match; use 86Box's
**File** device (raw data written to a file) together with the hot folder instead.

Headless server mode, converting each job as it arrives (all the usual look options apply):

    python -m retroimpact --listen 9100 --pipe /tmp/retroimpact.pipe --outdir jobs --format pdf --look worn

**Apple ImageWriter II colour** (Print Shop on an Apple II): `ESC K 0-3` selects the ribbon panel (black, yellow, red, blue) and
each colour is its own pass over the line, so overprinted colours mix (yellow+red = orange ...). If a graphics row carries fewer
bytes than its header announces (a capture that dropped data), the row is ended where the next commands appear instead of
swallowing them, and the info panel says how many rows were short.

**Okidata Okimate 10 / 20 colour** (GeoPaint and friends under GEOS, Commodore MCS-820): the three-panel ribbon prints each line as
three passes, and a carriage return steps the ribbon on (yellow, magenta, cyan) - the CR after cyan also feeds the paper. A stream of
`ESC K n1 n2` bit images chained only by bare CRs is recognised as an Okimate. Checked against the Okimate 20 handbook: ESC K/L/Y/Z
(8 dots per column, 72 dpi down), ESC A n (n/72 in), ESC 3, ESC J, ESC EM, ESC LF n. GEOS's Okimate 20 colour driver also sends bands as `ESC EM`, three passes of doubled 24-dot columns
(3 bytes per column, each ending `03 02 8D`) and `ESC LF 16`; that format was read from the 6502 code of the GEOS "Okimate 20" printer driver (a data byte of 03
is sent twice; bit 0 is the top pixel). The horizontal density (80 columns/in, the GEOS screen resolution) is assumed. The sample
`tests/data/geos_okimate20_colour.prn` is a sideways banner ("Artificial Intelligence" with a robot; use `--rotate 270` to read it)
followed by a GeoPaint picture; `geos_okimate20_geopaint_colour.prn` is that picture on its own.

## Print-head playback

**View ▸ Print-head playback…** (Ctrl+Shift+H) shows the current sheet being printed: the head sweeps (bidirectionally, as the
real one does), the paper feeds between lines, and the ink appears where the head has been. "Follow the head" scrolls the
paper through a window like a real printer; untick it to watch the whole sheet. Speeds run from real time to 100x; the clock
shows real printer time. Export a GIF, or an MP4 with synthesised printer noise (needs `ffmpeg`). Live sound uses Qt
Multimedia if your install has it.

From the command line: `python -m retroimpact capture.prn --playback out.gif --page 2 --speed 10` (`.mp4` for video with sound,
`--full-page`, `--no-sound`, `--fps`).

## Supported streams

* **Epson ESC/P** (FX/MX/LX 9-pin, LQ 24-pin): text attributes, pitches, NLQ, line spacing, margins, tabs,
  national and IBM character sets, bit-image graphics (ESC K/L/Y/Z/*/^/., raster).
* **IBM Proprinter / Star** streams that use the Epson-compatible command set (select the profile manually).
  Star-specific extension commands are not implemented.
* **Apple ImageWriter I/II** (ESC T/G/g/V/H/L/F, pitches, bold, underline, quality, graphics).
* **Commodore MPS-801/803/1525**: PETSCII text (plain text in the lower/upper set, as GEOS sends it, is recognised even
  without any escape codes), bit-image graphics, double width, dot addressing.

Unknown commands are counted (shown in the info box) and skipped, never fatal.

## Layout

    retroimpact/emulate.py        auto-detection + entry point  emulate(bytes, EmuOptions) -> Document
    retroimpact/escp.py, imagewriter.py, commodore.py, engine.py   emulators; they output *dots*, not pixels
    retroimpact/glyphs.py, charsets.py                              hand-built 5x7 draft font, NLQ fonts, code pages
    retroimpact/render.py, paper.py, settings.py                    ribbon/paper simulation (render_region = one tile)
    retroimpact/gui/icon_art.py, icons.py                           vector icons (geometry / QPainter)
    tests/icon_preview.py                                           contact sheet of the icons (Pillow)
    retroimpact/export.py, cli.py, gui/                             output and front ends
    tests/                                                          python -m unittest discover -s tests

## Honest status

The core (emulation, rendering, PDF/TIFF/PNG export, CLI) is covered by headless tests. The **PySide6 GUI was
written without being able to launch Qt in the build environment** - it passes a syntax check and a
name check, but expect to fix a few small issues on first run. Please report tracebacks.
Draft fonts are original; NLQ output uses bundled Liberation/DejaVu fonts (see `retroimpact/fonts/FONT-LICENSES.txt`)
rather than scans of the real printers' ROM fonts. The virtual printer's TCP, FIFO and hot-folder listeners are tested; the
Windows named-pipe listener's logic is tested against a stand-in for the Windows calls, but the real ctypes calls have
not been run on Windows yet. Star and IBM are approximations of Epson mode.

## Porting to Rust + egui + wgpu

The emulators emit a flat list of dot impulses per page (x, y, pin, pass, direction) - already the right shape
for a GPU instance buffer. `render.py` is written GPU-style: additive splat of per-dot amplitude → one soft-disc
kernel → `ink = 1 - exp(-gain·coverage)` → multiply onto procedural paper. In wgpu: instanced quads with
additive blending into an `R16Float` target, then a composite fragment shader for paper and ribbon colour.
Port order: (1) `Document`/`DotPage` + `escp`/`engine` state machines, (2) glyph tables (dump them from
`glyphs.py`), (3) the wgpu renderer, (4) egui shell. Parity can be checked against `tests/` and the Python output.

## Packaging

    pip install pyinstaller
    pyinstaller --noconfirm --windowed --name RetroImpact --add-data "retroimpact/fonts:retroimpact/fonts" run_retroimpact.py
