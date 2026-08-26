"""The service must run as ONE uvicorn worker, and nothing enforced it.

Six product modules hold process-global state that is correct only under a single worker, and each
one says so in its own comment: `reconcilers/status_cache.py` (`_LIVE_STATE_CACHE`, the derived agent
status), `usage_cache.py`, `terminal_snapshot.py` (`_LIVE_SCREENS`), `terminal_write_queue.py`,
`ntfy.py` (the relay and its dedup window) and `longpoll.py`. CLAUDE.md and DECISIONS.md both record
it as a hard constraint.

WHAT IT IS SATISFIED BY TODAY IS A DEFAULT. No launch command anywhere sets `--workers`, so uvicorn
runs one. Adding it would take one word in a Dockerfile, pass review as an obvious throughput win,
and break every one of those caches in the same way: each worker gets its OWN dict, so an agent's
status, quota and console screen depend on which worker happened to answer. Nothing raises. The
dashboard shows a plausible answer that is stale or absent at random, which is precisely the
"database is locked" era's symptom the in-memory cache was introduced to end — reintroduced from the
other side.

RAISING THE WORKER COUNT IS A DECISION, NOT A CONFIG CHANGE. It requires moving the shared state to
Redis or adding sticky routing first. This test exists so that decision cannot be made by accident;
if it is made deliberately, this file is what gets updated alongside the state.

SCOPE: every uvicorn invocation the repo ships — Dockerfiles, compose files, shell — plus a check
that no module calls `uvicorn.run` with a worker count. The scan asserts it FOUND the launch commands,
because a scan that matches nothing reports clean exactly like a scan that matches everything.
"""
from __future__ import annotations

import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[2]
# `tests` is pruned like every other gate here: this file carries the shapes it forbids as fixtures,
# and without the prune the scan finds its own anti-vacuity cases and fails on them. That happened.
PRUNE = {"node_modules", "__pycache__", ".git", ".venv", "fixtures", "tests"}

# Files that can start a server: container definitions, compose, shell, and Python.
LAUNCH_SUFFIXES = {".yml", ".yaml", ".sh", ".py"}
LAUNCH_NAMES = {"Dockerfile"}

# THE JSON EXEC FORM IS THE ONE THAT MATTERS. A Dockerfile CMD is a JSON array, so the flag and its
# value are separate elements — `"--workers", "4"` — with a quote and a comma between them. My first
# pattern only allowed `=` or whitespace and would have missed exactly the spelling this repo's own
# CMD line would use. The anti-vacuity test below is what caught it.
WORKER_FLAG = re.compile(r"--workers[\"']?\s*[,=\s]\s*[\"']?(\d+)")
WORKER_KWARG = re.compile(r"uvicorn\.run\([^)]*\bworkers\s*=\s*(\d+)", re.DOTALL)
WORKER_ENV = re.compile(r"\b(?:UVICORN_WORKERS|WEB_CONCURRENCY)\s*[=:]\s*[\"']?(\d+)")


def _candidate_files() -> list[pathlib.Path]:
    found = []
    for path in sorted(REPO.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(REPO)
        if PRUNE & set(rel.parts):
            continue
        if _is_launch_capable(path):
            found.append(path)
    return found


#: Suffixes that make a file a TEMPLATE of something else. Stripping one and re-testing is what keeps
#: `docker-compose.override.yml.example` in scope: its suffix is `.example`, so a plain suffix test
#: skipped it entirely -- and it is a compose override, which is precisely where somebody adds
#: `--workers` while adapting the example they were invited to copy. It carries a commented-out
#: uvicorn command line today, with `--reload` and no workers, so nothing is wrong right now; what was
#: wrong is that the gate could not have told us either way.
TEMPLATE_SUFFIXES = {".example", ".template", ".sample", ".dist"}


def _is_launch_capable(path: pathlib.Path) -> bool:
    """Whether a file could start a server, by shape rather than by a list of names.

    Three ways in, and the first two were the only ones until 2026-08-26:
      * a launch suffix (`.yml`, `.sh`, `.py`, ...)
      * the exact name `Dockerfile`
      * NEW: a template of either -- `x.yml.example` -- or a Dockerfile VARIANT such as
        `Dockerfile.dev`, which the exact-name test would also have missed. Neither exists in the tree
        today except the compose override example, which is the point: this gate guards an invariant
        whose violation silently corrupts the in-memory live-status cache, so it should be complete
        BEFORE the file that breaks it arrives, not after.
    """
    if path.name in LAUNCH_NAMES or path.suffix in LAUNCH_SUFFIXES:
        return True
    if path.name.startswith("Dockerfile"):
        return True
    if path.suffix in TEMPLATE_SUFFIXES and pathlib.Path(path.stem).suffix in LAUNCH_SUFFIXES:
        return True
    return False


def _read(path: pathlib.Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return ""


def test_no_launch_command_asks_for_more_than_one_worker():
    offenders: list[str] = []
    for path in _candidate_files():
        text = _read(path)
        if not text:
            continue
        for pattern, label in (
            (WORKER_FLAG, "--workers"),
            (WORKER_KWARG, "uvicorn.run(workers=)"),
            (WORKER_ENV, "UVICORN_WORKERS/WEB_CONCURRENCY"),
        ):
            for match in pattern.finditer(text):
                count = int(match.group(1))
                if count > 1:
                    line = text[: match.start()].count("\n") + 1
                    offenders.append(
                        f"{path.relative_to(REPO).as_posix()}:{line} sets {label}={count}"
                    )

    assert not offenders, (
        "the service is configured for more than one uvicorn worker.\n"
        "Six modules hold process-global state that is correct ONLY with one worker — the derived "
        "agent-status cache, the usage cache, live terminal screens, the terminal write queue, the "
        "ntfy relay and the long-poll registry. With two workers each gets its own copy and the "
        "answer depends on which one replied, silently.\n"
        "Moving that state to a shared store (or adding sticky routing) comes FIRST; then update "
        "this test deliberately.\n  " + "\n  ".join(offenders)
    )


def test_the_scan_actually_sees_the_real_launch_commands():
    """A scan that matches nothing reports clean exactly like a scan that matches everything."""
    launchers = [
        path.relative_to(REPO).as_posix()
        for path in _candidate_files()
        if "uvicorn" in _read(path)
    ]
    assert "Dockerfile" in launchers, "the service Dockerfile is not being scanned"
    assert any(name.startswith("docker-compose") for name in launchers), "no compose file scanned"
    assert len(launchers) >= 3, f"only {launchers} carry a uvicorn command — the walk is too narrow"
    # THE TEMPLATE, named because it was outside the walk until 2026-08-26. A compose OVERRIDE is
    # where an operator adapting the example would add `--workers`, and `.example` is not a launch
    # suffix, so the walk skipped the one file most likely to acquire the flag this gate forbids.
    assert "docker-compose.override.yml.example" in launchers, (
        "the compose override EXAMPLE is not being scanned. It carries a uvicorn command line and is "
        "meant to be copied, so it is where the forbidden flag would arrive first."
    )


def test_a_dockerfile_variant_and_a_template_are_both_in_scope():
    """The shapes the walk now covers, asserted directly rather than through what happens to exist.

    Neither `Dockerfile.dev` nor a `.sh.example` is in the tree today, so the assertion above cannot
    speak for them — and a gate guarding an invariant whose violation silently corrupts the
    live-status cache should be complete BEFORE such a file arrives.
    """
    assert _is_launch_capable(REPO / "Dockerfile.dev"), "a Dockerfile variant would not be scanned"
    assert _is_launch_capable(REPO / "entrypoint.sh.template"), "a shell template would not be scanned"
    assert _is_launch_capable(REPO / "docker-compose.override.yml.example")
    # And the walk must not widen into everything: a template of a non-launch file stays out.
    assert not _is_launch_capable(REPO / ".env.example"), "the walk now matches non-launch templates"
    assert not _is_launch_capable(REPO / "README.md"), "the walk now matches prose"


def test_the_patterns_detect_the_shapes_they_claim_to():
    """Anti-vacuity on the regexes: a shape they cannot see is a shape they cannot forbid."""
    # The JSON exec form FIRST — it is how this repo's Dockerfile and compose files spell a command,
    # so a pattern that misses it forbids nothing where it matters.
    assert WORKER_FLAG.search('CMD ["uvicorn", "app", "--workers", "4"]').group(1) == "4"
    assert WORKER_FLAG.search("command: ['uvicorn', 'app', '--workers', '2']").group(1) == "2"
    assert WORKER_FLAG.search("uvicorn app --workers=8").group(1) == "8"
    assert WORKER_FLAG.search("uvicorn app --workers 2").group(1) == "2"
    assert WORKER_KWARG.search("uvicorn.run(app, host='0.0.0.0', workers=3)").group(1) == "3"
    assert WORKER_ENV.search("UVICORN_WORKERS=4").group(1) == "4"
    assert WORKER_ENV.search("  WEB_CONCURRENCY: '2'").group(1) == "2"

    # A single worker stated explicitly is allowed and must not be flagged by the assertion above.
    assert int(WORKER_FLAG.search("uvicorn app --workers 1").group(1)) == 1
    # And prose mentioning the flag is not a setting.
    assert WORKER_FLAG.search("never add --workers > 1 without moving the cache") is None


def test_the_modules_that_depend_on_the_constraint_still_say_so():
    """The constraint is only followable if the code that needs it explains why. If a module stops
    documenting it, either the state moved (and this test should shrink) or the warning was lost."""
    expected = {
        "service/reconcilers/status_cache.py",
        "service/usage_cache.py",
        "service/terminal_snapshot.py",
        "service/terminal_write_queue.py",
        "service/ntfy.py",
        "service/longpoll.py",
    }
    silent = []
    for rel in sorted(expected):
        text = _read(REPO / rel).lower()
        if not text:
            silent.append(f"{rel} (missing)")
        elif "worker" not in text and "uvicorn" not in text:
            silent.append(rel)
    assert not silent, (
        "these modules hold single-worker-only state but no longer mention the constraint: "
        + ", ".join(silent)
    )
