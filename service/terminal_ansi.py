"""Turning a pyte screen into ANSI text. Pure serialisation, no state.

RELOCATED from `service/terminal_snapshot.py` in v0.5.4, byte-identical. That module does two
different jobs: it MANAGES live screen state (feed it output, resize it, hand back a snapshot) and it
SERIALISES a screen to ANSI. These four functions and their colour table are the second job, they
call nothing outside themselves, and nothing outside the module ever called them.

THE SPLIT IS STATE VERSUS FORMAT. Everything left in `terminal_snapshot.py` owns a mutable screen per
terminal and has to be right about lifetime — when a screen is created, resized, or dropped. Nothing
here owns anything: given a screen it returns a string, and the same screen always produces the same
text. That is a different kind of code to be wrong in, which is the argument for the boundary.

`_NAMED` travelled with them. It maps the sixteen ANSI colour names pyte reports onto SGR codes and
is read by `_color_sgr` alone; leaving it behind would have meant an import back for a lookup table
that means nothing to the state machine.
"""
from __future__ import annotations

from typing import Any


# 16-color ANSI names → SGR foreground base (background = +10). pyte reports the
# standard names; bright variants come through as the base name + the bold flag.
_NAMED = {
    "black": 30, "red": 31, "green": 32, "brown": 33, "yellow": 33,
    "blue": 34, "magenta": 35, "cyan": 36, "white": 37,
    "brightblack": 90, "brightred": 91, "brightgreen": 92, "brightbrown": 93,
    "brightyellow": 93, "brightblue": 94, "brightmagenta": 95, "brightcyan": 96,
    "brightwhite": 97,
}


def _color_sgr(value: str, *, background: bool) -> list[int]:
    """Map a pyte color (a name, or a 6-hex-digit string) to SGR parameters."""
    if not value or value == "default":
        return [49 if background else 39]
    name = str(value).lower()
    if name in _NAMED:
        base = _NAMED[name] + (10 if background else 0)
        return [base]
    # Hex truecolor (pyte gives "rrggbb" with no leading '#').
    if len(name) == 6:
        try:
            r, g, b = int(name[0:2], 16), int(name[2:4], 16), int(name[4:6], 16)
            return [48 if background else 38, 2, r, g, b]
        except ValueError:
            pass
    return [49 if background else 39]


def _cell_sgr(char) -> list[int]:
    """Full SGR parameter list for one pyte Char's attributes."""
    params: list[int] = [0]  # reset first so each run is self-contained
    if getattr(char, "bold", False):
        params.append(1)
    if getattr(char, "italics", False):
        params.append(3)
    if getattr(char, "underscore", False):
        params.append(4)
    if getattr(char, "blink", False):
        params.append(5)
    if getattr(char, "reverse", False):
        params.append(7)
    if getattr(char, "strikethrough", False):
        params.append(9)
    params += _color_sgr(getattr(char, "fg", "default"), background=False)
    params += _color_sgr(getattr(char, "bg", "default"), background=True)
    return params


def _line_to_ansi(line: Any, cols: int) -> str:
    """One screen row as a self-contained SGR-encoded string (trailing blanks trimmed)."""
    out: list[str] = []
    last = -1
    for x in range(cols):
        ch = line.get(x)
        if ch is not None and (ch.data not in ("", " ") or _cell_sgr(ch) != [0, 39, 49]):
            last = x
    if last < 0:
        return ""
    prev: list[int] | None = None
    for x in range(last + 1):
        ch = line.get(x)
        # A wide char (CJK/emoji) occupies TWO cells in pyte: the glyph in one cell + an
        # EMPTY-STRING continuation cell in the next. Emitting a space for that continuation
        # (the old `or " "`) shifted every following column one right per wide char (bughunt
        # 2026-07-03) — mis-aligning exactly the TUIs this snapshot repaints. Skip it.
        if ch is not None and ch.data == "":
            continue
        data = ch.data if ch is not None else " "
        sgr = _cell_sgr(ch) if ch is not None else [0, 39, 49]
        if sgr != prev:
            out.append("\x1b[" + ";".join(str(p) for p in sgr) + "m")
            prev = sgr
        out.append(data)
    out.append("\x1b[0m")
    return "".join(out)


def _screen_to_ansi(screen: Any, cols: int, rows: int, history: Any = ()) -> str:
    """Paint `history` (scrolled-off lines, oldest first) followed by the current screen.

    SCROLLBACK (2026-07-14). The snapshot used to be the current screen and NOTHING else, so an
    operator could not scroll back — and worse, attach/refresh call `term.reset()` (needed to
    un-scramble a reused pane), which WIPES whatever scrollback xterm had accumulated live. So
    the console had history while you watched it and lost it the moment you refreshed.
    Emitting the history lines ABOVE the screen fixes both: xterm scrolls them into its own
    scrollback exactly as a real terminal would, and a reset no longer costs you anything
    because the snapshot carries the history with it.
    """
    out: list[str] = ["\x1b[0m\x1b[2J\x1b[H"]  # reset attrs, clear, cursor home
    for line in history:
        out.append(_line_to_ansi(line, cols))
        out.append("\r\n")
    buffer = screen.buffer
    for y in range(rows):
        out.append(_line_to_ansi(buffer.get(y, {}), cols))
        if y < rows - 1:
            out.append("\r\n")

    # Leave the cursor where the app left it (1-based CUP, relative to the VIEWPORT — after the
    # history has scrolled up, the viewport's first row IS screen row 0).
    try:
        cy = max(0, min(int(screen.cursor.y), rows - 1)) + 1
        cx = max(0, min(int(screen.cursor.x), cols - 1)) + 1
        out.append(f"\x1b[{cy};{cx}H")
    except Exception:
        pass
    return "".join(out)
