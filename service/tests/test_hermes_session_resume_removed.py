"""Plan 4 Task 17: hermes-session-resume wake-mode removed. Hermes resident
agents use the gateway path (hermes-live wake-mode); fresh handles
captured via discoverSessionId.

v0.5.4 — THIS TEST WAS GUARDING NOTHING, and the way it broke is the reason no absence-assertion in
this suite may name a single file.

It grepped exactly one path, `service/routers/api_v2.py`. That file held the whole helper library
when the test was written. It is now 53 lines of `include_router` calls and cannot contain a wake-mode
return at all, so the loop had no lines to iterate and the test passed unconditionally. A
PRESENCE-assertion degrades safely — `src.index(...)` raises when its subject moves, which is why the
other source-scanning probes in this suite survived the same refactor. An ABSENCE-assertion degrades
into a permanent pass, silently. The reviewer caught this class in the JS suite
(native-managed-sync.test.js) and it was here in the Python suite too.

So the scan is now tree-wide: every `service/**/*.py` except this suite. That is strictly stronger
than the original intent — the deprecated mode could be returned from any module, and after v0.5.x
the module it would live in is not predictable — and it cannot be defeated by moving code.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SERVICE = REPO / "service"

MODE = "hermes-session-resume"


def _service_sources() -> list[Path]:
    return [
        p for p in sorted(SERVICE.rglob("*.py"))
        if "__pycache__" not in p.parts and "tests" not in p.parts
    ]


def test_hermes_session_resume_not_returned_as_wake_mode():
    """Plan 4: verify no live code path returns 'hermes-session-resume' as
    a wake_mode value. Comments referencing the deprecated mode are OK;
    string literal returned as wake mode is not."""
    sources = _service_sources()
    assert sources, f"no service sources found under {SERVICE} — the scan proved nothing"

    offenders = []
    for path in sources:
        for lineno, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            stripped = raw.strip()
            if MODE not in stripped or stripped.startswith("#"):
                continue
            # Heuristic unchanged: "return" and the literal on the same line (or in a
            # triple-quoted return value) is the bug. Comments about the deprecated mode are fine.
            if "return" in stripped and (f'"{MODE}"' in stripped or f"'{MODE}'" in stripped):
                offenders.append(f"{path.relative_to(REPO)}:{lineno}: {stripped}")

    assert not offenders, (
        f"{MODE} still returned as wake mode:\n  " + "\n  ".join(offenders)
    )


def test_the_scan_can_actually_see_an_offender():
    """Without this the gate above passes by scanning nothing — the exact defect it was just fixed for.

    Asserting a NEGATIVE means a green run and an unreachable subject look identical, so the
    detector is exercised against a synthetic offending line instead of trusted.
    """
    line = f'        return "{MODE}"'
    stripped = line.strip()
    assert MODE in stripped and not stripped.startswith("#")
    assert "return" in stripped and f'"{MODE}"' in stripped, "the detector no longer matches a return"
