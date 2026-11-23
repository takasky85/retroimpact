"""A virtual printer: receive raw printer data over a TCP socket and/or a named pipe, one print job at a time.

Emulators and old computers can be pointed at it instead of a file:

* TCP (all platforms): a "raw / JetDirect" port, default 9100. Windows can use it through a *Standard TCP/IP Port*, MAME with
  ``-bitb socket.127.0.0.1:9100``, DOSBox-X / VICE / netcat with plain sockets.
* Named pipe: a FIFO on Linux and macOS, ``\\\\.\\pipe\\name`` on Windows.

A job ends when the sender closes the connection, or when nothing has arrived for ``idle`` seconds (emulators like MAME keep
the connection open for ever). It is handed to ``on_job(PrintJob)`` on a background thread.

Only the local machine can connect unless you pass ``host="0.0.0.0"``.
"""
from __future__ import annotations

import os
import select
import socket
import stat
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from .capture_fix import is_probe, unfinished_graphics

MAX_JOB_BYTES = 64 * 1024 * 1024  # a job bigger than this is cut and sent on as several (protects memory)
DEFAULT_PORT = 9100
POLL = 0.2  # seconds between checks for "stop" and "idle"
HARD_IDLE = 30.0  # seconds of silence after which even a half-received bitmap row is given up on


@dataclass
class PrintJob:
    data: bytes
    source: str  # who sent it: "127.0.0.1:50321", "pipe"
    when: float = field(default_factory=time.time)
    number: int = 0
    label: str = ""  # a better name than the generic one (hot-folder jobs use the file name)
    path: str = ""  # the file the data came from, if it already lives in one

    @property
    def name(self) -> str:
        if self.label:
            return self.label
        return f"Print job {self.number} ({time.strftime('%H:%M:%S', time.localtime(self.when))}, {self.source})"


def default_pipe_name() -> str:
    return r"\\.\pipe\RetroImpact" if sys.platform == "win32" else os.path.join(
        os.environ.get("XDG_RUNTIME_DIR") or "/tmp", "retroimpact.pipe")


class _Collector:
    """Gathers the chunks of one connection and decides when a job is complete."""

    def __init__(self, owner: "VirtualPrinter", source: str):
        self.owner, self.source = owner, source
        self.buf = bytearray()
        self.last = time.monotonic()
        self._row_len, self._row = -1, False

    def add(self, chunk: bytes):
        self.owner.bytes_in += len(chunk)
        self.buf += chunk
        self.last = time.monotonic()
        if len(self.buf) >= MAX_JOB_BYTES:
            self.flush()

    def idle_check(self):
        if not self.buf:
            return
        quiet = time.monotonic() - self.last
        if quiet < self.owner.idle:
            return
        # a print that arrives in bursts can pause in the middle of a bitmap row; keep waiting for the rest, but not forever
        if quiet < max(self.owner.idle * 8, HARD_IDLE) and self._mid_row():
            return
        self.flush()

    def _mid_row(self) -> bool:
        if self._row_len != len(self.buf):  # scan again only when something new has arrived
            self._row_len, self._row = len(self.buf), unfinished_graphics(bytes(self.buf))
        return self._row

    def flush(self):
        if self.buf:
            data, self.buf = bytes(self.buf), bytearray()
            self.owner._emit(data, self.source)


class VirtualPrinter:
    def __init__(self, on_job: Callable[[PrintJob], None], port: Optional[int] = DEFAULT_PORT, host: str = "127.0.0.1",
                 pipe: Optional[str] = None, idle: float = 3.0, on_status: Optional[Callable[[str], None]] = None,
                 watch: Optional[str] = None, include_existing: bool = False):
        self.on_job, self.port, self.host, self.pipe = on_job, port, host, pipe
        self.watch, self.include_existing = watch, include_existing
        self.idle = max(0.2, float(idle))
        self.on_status = on_status or (lambda _s: None)
        self.stop_event = threading.Event()
        self.threads: List[threading.Thread] = []
        self.jobs = 0
        self.errors: List[str] = []
        self._lock = threading.Lock()
        self._sock: Optional[socket.socket] = None
        self._made_fifo = False
        self._ready = threading.Event()
        self.pipe_error = ""
        self.pipe_state = ""  # what the pipe is doing right now, for the status display
        self.bytes_in = 0
        self.probes = 0  # tiny port-probe "jobs" that were ignored
        self.watch_taken = 0  # files picked up from the hot folder
        self.watch_settle = 1.5  # seconds a watched file must stay unchanged before it is read
        self.bound_port: Optional[int] = None

    # ------------------------------------------------------------------ control
    def start(self) -> List[str]:
        """Open the listeners. Returns a description of each one that is now listening; raises OSError if none could start."""
        started: List[str] = []
        if self.port is not None:
            started.append(self._start_tcp())
        if self.pipe:
            started.append(self._start_pipe())
        if self.watch:
            started.append(self._start_watch())
        if not started:
            raise OSError("nothing to listen on: give a TCP port, a pipe name and/or a folder to watch")
        return started

    def stop(self):
        self.stop_event.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        for t in self.threads:
            t.join(timeout=2.0)
        if self._made_fifo and self.pipe:
            try:
                os.unlink(self.pipe)
            except OSError:
                pass
        self.threads = []

    def _emit(self, data: bytes, source: str, label: str = "", path: str = ""):
        if not path and is_probe(data):  # a program testing the port (55 AA 00 ...), not a print job
            self.probes += 1
            return
        with self._lock:
            self.jobs += 1
            job = PrintJob(data, source, number=self.jobs, label=label, path=path)
        try:
            self.on_job(job)
        except Exception as e:  # a broken consumer must not kill the listener
            self.errors.append(f"{type(e).__name__}: {e}")

    def _spawn(self, fn, *a):
        t = threading.Thread(target=fn, args=a, daemon=True)
        t.start()
        self.threads.append(t)

    # ------------------------------------------------------------------ TCP
    def _start_tcp(self) -> str:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, int(self.port)))
        srv.listen(8)
        srv.settimeout(POLL)
        self._sock = srv
        self.bound_port = srv.getsockname()[1]
        self._spawn(self._tcp_accept, srv)
        return f"TCP {self.host}:{self.bound_port}"

    def _tcp_accept(self, srv: socket.socket):
        while not self.stop_event.is_set():
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            self._spawn(self._tcp_conn, conn, f"{addr[0]}:{addr[1]}")

    def _tcp_conn(self, conn: socket.socket, source: str):
        col = _Collector(self, source)
        conn.settimeout(POLL)
        try:
            while not self.stop_event.is_set():
                try:
                    chunk = conn.recv(65536)
                except socket.timeout:
                    col.idle_check()
                    continue
                except OSError:
                    break
                if not chunk:  # the sender closed the connection: that is the end of the job
                    break
                col.add(chunk)
        finally:
            col.flush()
            try:
                conn.close()
            except OSError:
                pass

    # ------------------------------------------------------------------ named pipe
    def _start_pipe(self) -> str:
        if self._is_windows:
            self._ready.clear()
            self.pipe_error = ""
            self._spawn(self._win_pipe_loop)
            self._ready.wait(3.0)
            if self.pipe_error:
                raise OSError(self.pipe_error)
            return f"pipe {win_pipe_path(self.pipe)}"
        path = self.pipe
        if os.path.exists(path):
            if not stat.S_ISFIFO(os.stat(path).st_mode):
                raise OSError(f"{path} exists and is not a named pipe")
        else:
            os.mkfifo(path, 0o600)
            self._made_fifo = True
        self._spawn(self._fifo_loop, path)
        return f"pipe {path}"

    def _fifo_loop(self, path: str):
        # opened read+write so the pipe never reports "end of file" between writers; jobs are separated by silence
        fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        col = _Collector(self, "pipe")
        try:
            while not self.stop_event.is_set():
                r, _, _ = select.select([fd], [], [], POLL)
                if r:
                    try:
                        chunk = os.read(fd, 65536)
                    except BlockingIOError:
                        continue
                    if chunk:
                        col.add(chunk)
                else:
                    col.idle_check()
        finally:
            col.flush()
            os.close(fd)

    # ------------------------------------------------------------------ hot folder
    def _start_watch(self) -> str:
        self.watch = os.path.normpath(os.path.expanduser(self.watch))
        os.makedirs(self.watch, exist_ok=True)
        self._spawn(self._watch_loop)
        return f"folder {self.watch}"

    def _watch_loop(self):
        """Poll the folder. A file is taken once its size and time have stopped changing for `settle` seconds, so a file
        that an emulator is still writing is not read half-way. A file that changes again later is taken again."""
        settle = self.watch_settle
        taken: dict = {}  # name -> signature already handed on
        pending: dict = {}  # name -> (signature, when it was first seen with that signature)
        if not self.include_existing:
            for name, sig in _scan(self.watch).items():
                taken[name] = sig
        while not self.stop_event.is_set():
            now = time.monotonic()
            current = _scan(self.watch)
            for name, sig in current.items():
                if taken.get(name) == sig:
                    continue
                seen = pending.get(name)
                if seen is None or seen[0] != sig:
                    pending[name] = (sig, now)
                elif now - seen[1] >= settle and sig[0] > 0:
                    full = os.path.join(self.watch, name)
                    try:
                        with open(full, "rb") as f:
                            data = f.read(MAX_JOB_BYTES)
                    except OSError:
                        continue
                    taken[name] = sig
                    self.watch_taken += 1
                    pending.pop(name, None)
                    self._emit(data, "folder", label=name, path=full)
            for name in [n for n in pending if n not in current]:
                pending.pop(name)
            self.stop_event.wait(0.25)

    # -- Windows ------------------------------------------------------------------------------------------------
    # 86Box (and others) can either create the pipe themselves or connect to one that already exists. To work with
    # both, we first try to create it; if it is already there we connect to it instead and keep retrying until it is.
    _is_windows = sys.platform == "win32"

    def _make_win_api(self):  # replaced by a fake in the tests
        return _Win32()

    def _set_state(self, text: str):
        if text != self.pipe_state:
            self.pipe_state = text
            self.on_status(text)

    def _win_pipe_loop(self, api=None):
        api = api or self._make_win_api()
        name = win_pipe_path(self.pipe)
        h, err = api.create(name)
        if h is not None:
            self._set_state("pipe created, waiting for the emulator to connect")
            self._ready.set()
            try:
                self._win_serve(api, h)
            finally:
                api.close(h)
        elif err in _WIN_PIPE_EXISTS:
            self._set_state("pipe already exists, connecting to it")
            self._ready.set()
            self._win_client(api, name)
        else:
            self.pipe_error = f"could not create the pipe {name}: Windows error {err}"
            self._ready.set()

    def _win_drain(self, api, h, col) -> None:
        """Copy data from a connected pipe handle until the other end goes away; ends the job when it does."""
        self._set_state("connected")
        while not self.stop_event.is_set():
            avail = api.peek(h)
            if avail is None:  # the other end closed
                break
            if avail:
                chunk = api.read(h, avail)
                if chunk:
                    col.add(chunk)
            else:
                time.sleep(POLL / 4)
                col.idle_check()
        col.flush()

    def _win_serve(self, api, h) -> None:
        while not self.stop_event.is_set():
            st = api.connect(h)
            if st == "connected":
                self._win_drain(api, h, _Collector(self, "pipe"))
                api.disconnect(h)
                self._set_state("pipe created, waiting for the emulator to connect")
            else:
                if st == "gone":  # a client came and went between our checks: get ready for the next one
                    api.disconnect(h)
                time.sleep(POLL / 4)

    def _win_client(self, api, name: str) -> None:
        while not self.stop_event.is_set():
            h = api.open_client(name)
            if h is None:
                time.sleep(POLL * 2)  # the other program has not (re)created it yet
                continue
            try:
                self._win_drain(api, h, _Collector(self, "pipe"))
            finally:
                api.close(h)
            self._set_state("pipe closed by the other end, reconnecting")


_IGNORED_EXT = (".pdf", ".tif", ".tiff", ".png", ".jpg", ".jpeg", ".tmp", ".part", ".crdownload")


def _scan(folder: str) -> dict:
    """name -> (size, mtime_ns) of the plain files in `folder` that could be print data."""
    out = {}
    try:
        with os.scandir(folder) as it:
            for e in it:
                if e.name.startswith(".") or e.name.lower().endswith(_IGNORED_EXT):
                    continue
                try:
                    if e.is_file():
                        st = e.stat()
                        out[e.name] = (st.st_size, st.st_mtime_ns)
                except OSError:
                    pass
    except OSError:
        pass
    return out


def win_pipe_path(name: str) -> str:
    """86Box accepts a bare name for its pipe; so do we."""
    name = name.strip()
    return name if name.startswith("\\\\") else "\\\\.\\pipe\\" + name


_WIN_PIPE_EXISTS = (5, 231, 183)  # access denied / pipe busy / already exists: someone else made it


class _Win32:  # pragma: no cover - needs Windows
    """The few Win32 calls the pipe loop needs, with their ctypes plumbing kept out of the logic."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        self.ct, self.wt = ctypes, wintypes
        k = self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateNamedPipeW.restype = wintypes.HANDLE
        k.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                       wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
        k.CreateFileW.restype = wintypes.HANDLE
        k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                  wintypes.DWORD, wintypes.HANDLE]
        k.ConnectNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
        k.PeekNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                                    ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        k.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                               ctypes.c_void_p]
        k.DisconnectNamedPipe.argtypes = [wintypes.HANDLE]
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        self.invalid = ctypes.c_void_p(-1).value
        self.buf = ctypes.create_string_buffer(65536)

    def create(self, name):
        # DUPLEX: a program that connects to us may open the pipe for reading and writing (86Box does); an inbound-only
        # pipe would refuse it with "access denied". NOWAIT so that waiting for a client never blocks stop().
        PIPE_ACCESS_DUPLEX, PIPE_TYPE_BYTE, PIPE_NOWAIT = 0x3, 0x0, 0x1
        h = self.k.CreateNamedPipeW(name, PIPE_ACCESS_DUPLEX, PIPE_TYPE_BYTE | PIPE_NOWAIT, 1, 65536, 65536, 0, None)
        if h is None or h == self.invalid:
            return None, self.ct.get_last_error()
        return h, 0

    def connect(self, h):
        if self.k.ConnectNamedPipe(h, None):
            return "connected"
        err = self.ct.get_last_error()
        if err == 535:  # ERROR_PIPE_CONNECTED
            return "connected"
        if err == 232:  # ERROR_NO_DATA: the client has already gone
            return "gone"
        return "listening"  # 536 ERROR_PIPE_LISTENING and anything unexpected

    def open_client(self, name):
        GENERIC_READ, SHARE_RW, OPEN_EXISTING = 0x80000000, 0x3, 3
        h = self.k.CreateFileW(name, GENERIC_READ, SHARE_RW, None, OPEN_EXISTING, 0, None)
        return None if h is None or h == self.invalid else h

    def peek(self, h):
        avail = self.wt.DWORD(0)
        if not self.k.PeekNamedPipe(h, None, 0, None, self.ct.byref(avail), None):
            return None
        return avail.value

    def read(self, h, n):
        got = self.wt.DWORD(0)
        if self.k.ReadFile(h, self.buf, min(len(self.buf), n), self.ct.byref(got), None) and got.value:
            return self.buf.raw[:got.value]
        return b""

    def disconnect(self, h):
        self.k.DisconnectNamedPipe(h)

    def close(self, h):
        self.k.CloseHandle(h)
