"""Clean terminal-screen snapshots for console replay.

The service stores each PTY's RAW byte log (trimmed to ~64KB). Replaying that log
into a fresh client xterm scrambles full-screen TUIs (claude/codex Ink UIs): the log
is a stream of cursor-positioning/clear/redraw sequences meant to drive a LIVE screen
at a FIXED size, and it usually starts mid-screen (after the trim) — so replaying it,
especially at a different width than the app drew at, overlaps every historical draw
into garbage. See DECISIONS.md "Console replay uses a server-rendered screen snapshot".

This module replays the raw log through a headless VT emulator (`pyte`) sized to the
VIEWER's cols/rows and emits a clean, self-contained ANSI string that paints the CURRENT
screen state into a fresh terminal. It is used only on attach/refresh (a rare, bounded,
one-shot parse over <=64KB), never on the live streaming path — so it adds no per-frame
cost. Live deltas keep streaming raw to the client xterm, which renders them incrementally.

Degrades safely: if pyte is unavailable, `render_snapshot` returns the raw log unchanged,
so the console behaves exactly as before (never worse, never an error).
"""

from __future__ import annotations

try:
    import pyte  # type: ignore
    _HAVE_PYTE = True
except Exception:  # pragma: no cover - exercised only when the dep is absent
    _HAVE_PYTE = False

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


def render_snapshot(raw_output: str, cols: int, rows: int) -> str:
    """Return a clean, self-contained ANSI string that paints the current screen.

    `raw_output` is the stored raw PTY byte log; `cols`/`rows` are the VIEWER's grid.
    The result begins by resetting + clearing, so it is safe to write into a fresh
    xterm (or after term.reset()). Falls back to the raw log if pyte is unavailable.
    """
    if not raw_output:
        return ""
    cols = max(20, min(int(cols or 80), 500))
    rows = max(5, min(int(rows or 24), 200))
    if not _HAVE_PYTE:
        return raw_output

    screen = pyte.Screen(cols, rows)
    stream = pyte.Stream(screen)
    try:
        stream.feed(raw_output)
    except Exception:
        # A corrupt/clipped byte log must never break replay — fall back to raw.
        return raw_output

    out: list[str] = ["\x1b[0m\x1b[2J\x1b[H"]  # reset attrs, clear, cursor home
    buffer = screen.buffer
    for y in range(rows):
        line = buffer.get(y, {})
        # Trailing-blank trim: find the last non-empty, default-styled cell.
        last = -1
        for x in range(cols):
            ch = line.get(x)
            if ch is not None and (ch.data not in ("", " ") or _cell_sgr(ch) != [0, 39, 49]):
                last = x
        if last >= 0:
            prev: list[int] | None = None
            for x in range(last + 1):
                ch = line.get(x)
                data = (ch.data if ch is not None else " ") or " "
                sgr = _cell_sgr(ch) if ch is not None else [0, 39, 49]
                if sgr != prev:
                    out.append("\x1b[" + ";".join(str(p) for p in sgr) + "m")
                    prev = sgr
                out.append(data)
            out.append("\x1b[0m")
        if y < rows - 1:
            out.append("\r\n")

    # Leave the cursor where the app left it (1-based for the CUP sequence).
    try:
        cy = max(0, min(int(screen.cursor.y), rows - 1)) + 1
        cx = max(0, min(int(screen.cursor.x), cols - 1)) + 1
        out.append(f"\x1b[{cy};{cx}H")
    except Exception:
        pass
    return "".join(out)
