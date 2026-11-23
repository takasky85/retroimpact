import sys, time
sys.path.insert(0, '.')
from retroimpact.emulate import emulate
from retroimpact.model import EmuOptions

def show(name, data, **kw):
    t = time.time()
    try:
        doc = emulate(data, EmuOptions(**kw))
    except Exception:
        import traceback; print("FAIL", name); traceback.print_exc(); return None
    print(f"{name:14s} printer={doc.detected:12s} pages={len(doc.pages)} dots={[p.n_dots for p in doc.pages]} {time.time()-t:.2f}s")
    return doc

txt = b"Hello, World!\r\nThe quick brown fox jumps over the lazy dog 0123456789\r\n" * 5
show("epson text", b"\x1b@" + txt)
show("epson styled", b"\x1b@\x1bE bold \x1bF\x1b4 italic \x1b5\x0e wide\x14\x0f cond\x12 \x1b-1under\x1b-0\r\n\x1bx1\x1bk0 NLQ roman\r\n")
show("plain lf", b"line1\nline2\nline3\n")
show("plain cr", b"line1\rline2\rline3\r")
g = b"\x1b@\x1bK\x40\x01" + bytes([0xAA, 0x55] * 32) + b"\r\n\x1b*\x00\x20\x00" + bytes(range(32)) + b"\r\n"
show("epson gfx", g)
show("epson lq gfx", b"\x1b@\x1b*\x27\x10\x00" + bytes(range(48)) + b"\r\n\x1bx1 LQ text\r\n")
show("ff pages", b"page1\r\n\x0cpage2\r\n\x0cpage3\r\n")
show("imagewriter", b"\x1bT16\x1bG0016" + bytes([0xFF, 0x81] * 8) + b"\rHello ImageWriter\r\n")
show("mps", b"\x0e\x08\x80\x81\x82\x83\x84\x85\x86\x87\x1b\x10\x00\x20" + b"HELLO\r\n")
show("mps petscii", b"\x93\xc8\xc5\xcc\xcc\xcf\r\xc1\xc2\r")
show("empty", b"")
