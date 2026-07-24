"""Clean terminal-screen snapshots for console replay.

The service stores each PTY's RAW byte log (trimmed to ~64KB). Replaying that log
into a fresh client xterm scrambles full-screen TUIs (claude/codex Ink UIs): the log
is a stream of cursor-positioning/clear/redraw sequences meant to drive a LIVE screen
at a FIXED size, and it usually starts mid-screen (after the trim) — so replaying it,
especially at a different width than the app drew at, overlaps every historical draw
into garbage. See DECISIONS.md "Console replay uses a server-rendered screen snapshot".

This module replays the raw log through a headless VT emulator (`pyte`) sized to the
VIEWER's cols/rows and emits a clean, self-contained ANSI string that paints the CURRENT
screen state into a fresh terminal. It also keeps a bounded live screen per ANSI/TUI PTY so
text-only console-tail readers receive the rendered screen instead of cursor-motion deltas.
Plain logs bypass emulation and retain their original lines. Browser clients still receive
live deltas raw and render them incrementally in xterm.

Degrades safely: if pyte is unavailable, `render_snapshot` returns the raw log unchanged,
so the console behaves exactly as before (never worse, never an error).
"""

from __future__ import annotations

import re
from typing import Any, Optional

try:
    import pyte  # type: ignore
    _HAVE_PYTE = True
except Exception:  # pragma: no cover - exercised only when the dep is absent
    _HAVE_PYTE = False


# Balanced alternate-screen regions (DECSET 1049/1047/47 enter ... matching exit).
# Claude/Codex draw transient full-screen overlays (compaction/resume dialog, menus)
# on the ALT screen, then restore the main screen on exit. pyte does NOT swap buffers,
# so it paints the overlay into its single buffer and never restores it — a dismissed
# dialog stays "baked in" to every snapshot forever (operator-reported stuck compaction
# prompt, 2026-07-06). Strip only BALANCED (enter...exit) regions before replay; an
# UNCLOSED trailing enter means the overlay is CURRENTLY live and must be shown as-is.
_BALANCED_ALT_SCREEN_RE = re.compile(
    r"\x1b\[\?(?:1049|1047|47)h.*?\x1b\[\?(?:1049|1047|47)l",
    re.DOTALL,
)


def _strip_balanced_alt_screens(raw_output: str) -> str:
    return _BALANCED_ALT_SCREEN_RE.sub("", raw_output)

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


def infer_source_width(raw_output: str, probe: int = 400, rows: int = 120) -> int:
    """Best-effort estimate of the terminal WIDTH the raw log was drawn at.

    A resident wrapper mirrors the operator's REAL terminal, whose width we never
    stored (terminal_sessions.cols is 0/None for residents). Replaying that log at a
    NARROWER viewer width wraps + mangles every full-screen-TUI line (the "gappy /
    bugged console"). We replay once at a generous probe width (no clamping, so the
    app's absolute cursor moves land untouched) and take the furthest column any cell
    actually reaches — full-screen TUIs draw a full-width frame/rule, so that max IS
    the source width. Returns 0 when unknown (pyte absent / empty / parse error) so
    callers fall back to the viewer width and behave exactly as before.
    """
    if not raw_output or not _HAVE_PYTE:
        return 0
    probe = max(80, min(int(probe or 400), 500))
    rows = max(5, min(int(rows or 120), 200))
    screen = pyte.Screen(probe, rows)
    stream = pyte.Stream(screen)
    try:
        stream.feed(_strip_balanced_alt_screens(raw_output))
    except Exception:
        return 0
    max_col = 0
    buffer = screen.buffer
    for y in range(rows):
        line = buffer.get(y, {})
        for x in range(probe - 1, -1, -1):
            ch = line.get(x)
            if ch is not None and (ch.data not in ("", " ")):
                if x + 1 > max_col:
                    max_col = x + 1
                break
    return max_col


def render_snapshot(raw_output: str, cols: int, rows: int) -> str:
    """Return a clean, self-contained ANSI string that paints the current screen.

    `raw_output` is the stored raw PTY byte log; `cols`/`rows` are the VIEWER's grid.
    The result begins by resetting + clearing, so it is safe to write into a fresh
    xterm (or after term.reset()). Falls back to the raw log if pyte is unavailable.

    NOTE (2026-07-14): replaying the STORED log is a fundamentally lossy way to learn the
    screen, and it is why consoles render scrambled / half-empty. The stored log is a 64KB
    TAIL, and claude's TUI never clears the screen (measured: ZERO `ESC[2J` across the live
    fleet) — it paints differentially, one line at a time, homing the cursor between frames.
    Replaying a suffix into a BLANK screen therefore leaves every row last painted before the
    window blank or half-written, and a suffix can even begin mid-escape-sequence. Measured on
    a live console: a full 64KB replay reconstructed 11 of 30 rows, with text from different
    frames overlapping; replaying just the last frame reconstructed ONE row. No amount of
    buffer is enough, and no repaint nudge helps (claude re-renders only its small footer on
    SIGWINCH, which the keepalive proves every 4s).

    The fix is `feed_live_screen` below: keep ONE screen per terminal and feed it every chunk
    as it arrives, exactly as a real terminal does. This function remains the FALLBACK for
    terminals with no live screen yet (and when pyte is unavailable), so behaviour never
    regresses below what it is today.
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
        stream.feed(_strip_balanced_alt_screens(raw_output))
    except Exception:
        # A corrupt/clipped byte log must never break replay — fall back to raw.
        return raw_output
    return _screen_to_ansi(screen, cols, rows)


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


# ─────────────────────────── LIVE SCREENS (2026-07-14) ───────────────────────────
#
# One persistent screen per terminal, fed every chunk as it arrives — i.e. what a real
# terminal does. This exists because reconstructing the screen by REPLAYING the stored byte
# log cannot work (see render_snapshot's note): the log is a truncated tail, claude never
# clears the screen, and a suffix can start mid-escape. A live pyte Stream is also stateful,
# so an escape sequence SPLIT ACROSS CHUNKS is handled correctly — replay-from-offset can cut
# one in half and emit garbage (the "____ everywhere" class).
#
# Cost is small and was measured before building this: across the live fleet only 2 of 160
# terminals emit at any moment (~261 small chunks / 30s), and pyte feeds at ~0.9 MB/s.
#
# Single-worker only, like _LIVE_STATE_CACHE (see DECISIONS.md): this is process-global
# in-memory state and is only correct with ONE uvicorn process.
_LIVE_SCREENS: dict[str, "_LiveScreen"] = {}
_MAX_LIVE_SCREENS = 256

# Lines of SCROLLBACK kept per terminal. The operator could not scroll the console at all: the
# snapshot carried only the current screen, and the term.reset() that attach/refresh need (to
# un-scramble a reused pane) wiped whatever xterm had accumulated live. pyte.HistoryScreen keeps
# the scrolled-off lines server-side, so the snapshot can ship them and a reset costs nothing.
# 400 is ~14 screens — deliberately bounded: pyte stores history as Char objects, so this is the
# memory knob (roughly a few MB per BUSY terminal, and only terminals that emit are tracked).
_HISTORY_LINES = 400

# Alt-screen enter/leave. pyte has no alt-screen buffer, so a dialog drawn in one would be
# painted onto the MAIN screen and still be there after it was dismissed — the "stuck dialog"
# the replay path works around by stripping balanced alt regions. Emulate it properly instead:
# while in the alt screen we paint a THROWAWAY screen (so the dialog is visible while it is up)
# and simply drop it on leave, restoring the untouched main screen.
_ALT_ENTER_RE = re.compile(r"\x1b\[\?(?:1049|1047|47)h")
_ALT_LEAVE_RE = re.compile(r"\x1b\[\?(?:1049|1047|47)l")


class _LiveScreen:
    __slots__ = ("cols", "rows", "screen", "stream", "alt_screen", "alt_stream", "in_alt")

    def __init__(self, cols: int, rows: int) -> None:
        self.cols = cols
        self.rows = rows
        # HistoryScreen (not Screen): keeps the lines that scroll off the top, which IS the
        # console's scrollback. Without it there is nothing to scroll back to after a reset.
        self.screen = pyte.HistoryScreen(cols, rows, history=_HISTORY_LINES, ratio=0.5)
        self.stream = pyte.Stream(self.screen)
        self.alt_screen = None
        self.alt_stream = None
        self.in_alt = False

    def _enter_alt(self) -> None:
        self.alt_screen = pyte.Screen(self.cols, self.rows)
        self.alt_stream = pyte.Stream(self.alt_screen)
        self.in_alt = True

    def _leave_alt(self) -> None:
        self.alt_screen = None
        self.alt_stream = None
        self.in_alt = False

    def feed(self, chunk: str) -> None:
        # Route bytes to the main or alt screen, splitting exactly at the switch sequences.
        while chunk:
            if self.in_alt:
                m = _ALT_LEAVE_RE.search(chunk)
                if not m:
                    self.alt_stream.feed(chunk)
                    return
                self.alt_stream.feed(chunk[: m.start()])
                self._leave_alt()
                chunk = chunk[m.end():]
            else:
                m = _ALT_ENTER_RE.search(chunk)
                if not m:
                    self.stream.feed(chunk)
                    return
                self.stream.feed(chunk[: m.start()])
                self._enter_alt()
                chunk = chunk[m.end():]

    def render(self) -> str:
        # While a full-screen dialog is up, show IT (no history — an alt screen has none, and a
        # real terminal shows no scrollback behind one either).
        if self.in_alt and self.alt_screen is not None:
            return _screen_to_ansi(self.alt_screen, self.cols, self.rows)
        history = []
        try:
            history = list(getattr(self.screen, "history").top)  # oldest first
        except Exception:
            history = []
        return _screen_to_ansi(self.screen, self.cols, self.rows, history=history)

    def resize(self, cols: int, rows: int) -> None:
        """Resize in place, keeping the stream.

        REVERTED (2026-07-14) from "rebuild the screen clean on every size change". That looked
        right — content painted for 100 columns is meaningless on 94 — but it made the console
        WORSE, verified in a real browser: wiping the screen left it BLANK, because the app does
        not necessarily repaint its whole frame just because SIGWINCH arrived, and rebuilding the
        pyte Stream mid-flight also cut an escape sequence in half, leaking `[38;5;178m4` into the
        screen as literal text. Reflowing is imperfect for an absolutely-positioned TUI, but the
        app's next repaint overwrites it — a briefly-imperfect screen beats an empty one."""
        self.cols, self.rows = cols, rows
        try:
            self.screen.resize(rows, cols)  # pyte takes (lines, columns)
            if self.alt_screen is not None:
                self.alt_screen.resize(rows, cols)
        except Exception:
            self.__init__(cols, rows)  # type: ignore[misc]  # never carry a corrupt screen


def _clamp_grid(cols: Any, rows: Any) -> tuple[int, int]:
    return (
        max(20, min(int(cols or 100), 500)),
        max(5, min(int(rows or 28), 200)),
    )


def feed_live_screen(terminal_id: str, chunk: str, *, cols: Any = 0, rows: Any = 0, seed: str = "") -> bool:
    """Feed one live PTY chunk into this terminal's persistent screen.

    `seed` is used ONLY when creating the screen for a terminal we have not been tracking
    (service restart, or a PTY that predates this code): we replay the stored log into it so
    the console is never WORSE than the old behaviour, and it then self-heals as new output
    scrolls the imperfect rows away. A PTY started after this code is correct from byte 0.
    Best-effort throughout: any failure drops the live screen and the caller falls back to the
    replay path. Returns True when the chunk was accepted.
    """
    if not _HAVE_PYTE or not terminal_id:
        return False
    tid = str(terminal_id)
    c, r = _clamp_grid(cols, rows)
    try:
        live = _LIVE_SCREENS.get(tid)
        if live is None:
            # Plain logs must remain byte-for-byte logs, not terminal screen state: creating a
            # screen would wrap long lines and cap history. Once ANSI starts, seed preserves
            # any preceding plain startup output and subsequent plain chunks continue the TUI.
            if "\x1b" not in chunk and "\x1b" not in seed:
                return False
            if len(_LIVE_SCREENS) >= _MAX_LIVE_SCREENS:
                return False  # bounded: never grow without limit
            live = _LiveScreen(c, r)
            if seed:
                live.feed(_strip_balanced_alt_screens(seed))
            _LIVE_SCREENS[tid] = live
        elif (c, r) != (live.cols, live.rows):
            live.resize(c, r)
        if chunk:
            live.feed(chunk)
        return True
    except Exception:
        _LIVE_SCREENS.pop(tid, None)  # never serve a corrupt screen
        return False


def render_live_screen(terminal_id: str) -> Optional[tuple[str, int, int]]:
    """(ansi, cols, rows) for a tracked terminal, or None when we have no live screen."""
    if not _HAVE_PYTE or not terminal_id:
        return None
    live = _LIVE_SCREENS.get(str(terminal_id))
    if live is None:
        return None
    try:
        return live.render(), live.cols, live.rows
    except Exception:
        _LIVE_SCREENS.pop(str(terminal_id), None)
        return None


def resize_live_screen(terminal_id: str, cols: Any, rows: Any) -> bool:
    """Align an existing live screen when the bridge confirms a PTY resize."""
    tid = str(terminal_id or "")
    live = _LIVE_SCREENS.get(tid)
    if live is None:
        return False
    try:
        c, r = _clamp_grid(cols, rows)
        if (c, r) != (live.cols, live.rows):
            live.resize(c, r)
        return True
    except Exception:
        _LIVE_SCREENS.pop(tid, None)
        return False


def drop_live_screen(terminal_id: str) -> None:
    _LIVE_SCREENS.pop(str(terminal_id or ""), None)


def live_screen_count() -> int:
    return len(_LIVE_SCREENS)
