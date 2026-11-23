"""Which sheets of a document are shown: deleted by the user, hidden because they look wrong, or revealed again.

Pure Python (no Qt) so the rules can be tested. ``PageView`` owns one of these and lays out ``items()``.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .analyze import JUNK, PageInfo

MANUAL = "hidden by you"


class PageSet:
    def __init__(self, n: int = 0, flags: Optional[Dict[int, PageInfo]] = None):
        self.n = n
        self.deleted: set = set()  # removed by the user: not shown, not exported
        self.flags: Dict[int, PageInfo] = dict(flags or {})  # sheets that look wrong (automatic) or were hidden by hand
        self.revealed: set = set()  # flagged sheets that were opened up again
        self.hide_flagged = False  # off unless the user asks for flagged sheets to be folded away

    def is_hidden(self, i: int) -> bool:
        f = self.flags.get(i)
        return f is not None and i not in self.revealed and (self.hide_flagged or f.reason == MANUAL)

    def live(self) -> List[int]:
        return [i for i in range(self.n) if i not in self.deleted]

    def visible(self) -> List[int]:
        return [i for i in self.live() if not self.is_hidden(i)]

    def hidden(self) -> List[int]:
        return [i for i in self.live() if self.is_hidden(i)]

    def items(self) -> List[tuple]:
        """("page", i) for a shown sheet, ("gap", first, last) for a run of hidden ones, in page order."""
        out: List[tuple] = []
        run: Optional[Tuple[int, int]] = None
        for i in self.live():
            if self.is_hidden(i):
                run = (run[0] if run else i, i)
            else:
                if run:
                    out.append(("gap",) + run)
                    run = None
                out.append(("page", i))
        if run:
            out.append(("gap",) + run)
        return out

    def delete(self, indices) -> None:
        self.deleted |= {i for i in indices if 0 <= i < self.n}

    def hide(self, indices) -> None:
        for i in indices:
            if 0 <= i < self.n and i not in self.deleted:
                self.flags[i] = PageInfo(i, JUNK, MANUAL)
                self.revealed.discard(i)

    def reveal(self, first: int, last: int) -> None:
        self.revealed |= {i for i in range(first, last + 1) if i in self.flags}

    def set_hide_flagged(self, on: bool) -> None:
        """Fold the automatically flagged sheets away (True) or show them (False). Sheets hidden by hand stay hidden."""
        self.hide_flagged = bool(on)
        if on:
            self.revealed.clear()

    def reveal_all(self) -> None:
        self.revealed |= set(self.flags)

    def state(self) -> dict:
        return {"deleted": sorted(self.deleted), "hidden": sorted(i for i, f in self.flags.items() if f.reason == MANUAL),
                "revealed": sorted(self.revealed)}

    def apply(self, d: dict) -> None:
        """Take saved state back; page numbers that no longer exist are ignored."""
        ok = lambda v: {int(i) for i in (v or []) if 0 <= int(i) < self.n}  # noqa: E731
        self.deleted = ok(d.get("deleted"))
        for i in ok(d.get("hidden")):
            self.flags[i] = PageInfo(i, JUNK, MANUAL)
        self.revealed = ok(d.get("revealed"))
