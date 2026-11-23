import sys, time
sys.path.insert(0, '.')
from PIL import Image
from retroimpact.emulate import emulate
from retroimpact.model import EmuOptions
from retroimpact.render import render_page
from retroimpact.settings import RenderSettings
txt = b"\x1b@\x1bE RETRO IMPACT \x1bF test page\r\n\r\nThe quick brown fox jumps over the lazy dog. 0123456789 {}[]()\r\n" + b"Lorem ipsum dolor sit amet, consectetur adipiscing elit.\r\n" * 6
txt += b"\x1bx1NLQ: The quick brown fox jumps over the lazy dog.\x1bx0\r\n"
doc = emulate(txt, EmuOptions())
for name, kw in {"fresh": dict(density=0.97, saturation=1.1), "worn": dict(density=0.7, saturation=0.5, ribbon_wear=0.65, paper="tractor_cream")}.items():
    s = RenderSettings(dpi=144, **kw)
    t = time.time(); im = render_page(doc.pages[0], doc, s); print(name, im.shape, round(time.time()-t, 2), "s")
    Image.fromarray(im).save(f"/tmp/{name}.png")
    Image.fromarray(im).crop((0, 0, 700, 500)).resize((1400, 1000), Image.NEAREST).save(f"/tmp/{name}_crop.png")
