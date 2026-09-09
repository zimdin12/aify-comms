"""Is the RUNNING service carrying the console-transport defects this version fixed?

WHY THIS EXISTS. Every hop of B_TRANSPORT was measured on a fixture, and the operator's "our browser
terminal kind of lags sometimes" stayed unattributed through all of them -- because a fixture is not
their machine. The question that closed it was not "where is the time" but "is the mechanism I
already fixed present in what they are RUNNING?". It was, on both ends, and this is the check that
answers it again after any deploy.

WHAT IT ASKS, and every one of them is read from the running system rather than from git:

  1. WHICH BUILD is serving, from `GET /health`.
  2. WHETHER THE THREE FIXES ARE IN IT, by ancestry -- controlled both ways, because an
     `--is-ancestor` that cannot say YES cannot say NO. The stamped build's own parent is the
     positive control; a made-up sha is the negative one.
  3. WHAT THE BROWSER ACTUALLY RECEIVES, fetched from the dashboard rather than read from the
     checkout. `console-cursor.mjs` is the cheapest tell: this version created it, so a 404 means
     the served bundle predates the console work entirely.
  4. WHETHER THE DEPLOYED QUEUE FIRES THE DEFECT, which a code read cannot answer. The container's
     own `terminal_write_queue.py` is copied out and this version's frame-sequence test is pointed
     at it, with the repo's module as the control on both sides of the swap.

WHAT IT IS NOT. It does not observe the lag. A mechanism present in deployed code is a candidate
with its machinery demonstrated, and that is not a reproduction -- the live coalescing RATE, which
decides whether this is constant or occasional, is not measured here or anywhere yet.

    python scripts/check-deployed-console-transport.py

Exit 0 when the running service is clear of all of them, 1 when it carries any, 2 when the check
could not gather its evidence -- because a check that answered nothing must not read as a pass.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUEUE = ROOT / "service" / "terminal_write_queue.py"
FRAME_TEST = "service/tests/test_the_broadcast_seq_counts_frames_not_posts.py"

HEALTH = "http://localhost:8800/health"
DASHBOARD = "http://localhost:8811/assets"
CONTAINER = "aify-comms-service"

#: The three commits this version made to the console transport, with what each one is FOR -- a sha
#: with no subject is a pointer the next reader cannot resolve.
FIXES = {
    "5d6fbabe": "the TRIGGER: the frame sequence counted POSTS, so a coalesced flush faked a gap",
    "815ea767": "the AMPLIFIER: a gap-recovery could loop, dropping every frame that arrived in it",
    "43857ad7": "the resync fetched 147 KB to repaint from 6 KB of it",
}

#: Served modules whose ABSENCE or staleness says the browser predates the console work.
SERVED = ("xterm-mount.mjs", "realtime-socket.mjs", "console-cursor.mjs")


def _key() -> str:
    """The key the SERVICE is configured with, from the resolver that owns that question."""
    done = subprocess.run(["bash", str(ROOT / "scripts" / "api-key.sh")],
                          capture_output=True, text=True)
    return done.stdout.strip().splitlines()[-1].strip() if done.stdout.strip() else ""


def _get(url: str, key: str) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers={"X-API-Key": key} if key else {})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, b""
    except OSError:
        return 0, b""


def _git(*args: str) -> tuple[int, str]:
    done = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    return done.returncode, done.stdout.strip()


def _git_bytes(*args: str) -> tuple[int, bytes]:
    """`git show` for FILE CONTENT, which must never go through `text=True`.

    MEASURED, and it is why the first version of this check reported "matches neither": on Windows
    `text=True` decodes with the locale codec, so a UTF-8 source file comes back mojibake -- 24,383
    bytes for a file that is 24,243. The comparison then fails against every candidate at once,
    which reads like a real finding and is an instrument fault. A file comparison is a BYTE
    comparison; nothing here needs it decoded.
    """
    done = subprocess.run(["git", *args], cwd=ROOT, capture_output=True)
    return done.returncode, done.stdout


def running_build(key: str) -> tuple[str, str]:
    status, body = _get(HEALTH, key)
    if status != 200 or not body:
        return "", ""
    try:
        health = json.loads(body)
    except ValueError:
        return "", ""
    return str(health.get("version") or ""), str(health.get("build") or "")


def ancestry(build: str) -> tuple[dict[str, bool], str]:
    """Which fixes are in `build`, or a reason the answer cannot be trusted.

    CONTROLLED IN THE SAME RUN. `--is-ancestor` returns non-zero both for "no" and for a ref that
    does not resolve, so an uncontrolled sweep of three shas reports the same thing whether the
    build predates them or the build is a typo.
    """
    if _git("rev-parse", "--verify", f"{build}^{{commit}}")[0] != 0:
        return {}, f"the stamped build {build} does not resolve in this checkout"
    code, parent = _git("rev-parse", f"{build}^")
    if code != 0 or _git("merge-base", "--is-ancestor", parent, build)[0] != 0:
        return {}, "the positive control failed: the build's own parent did not report as an ancestor"
    if _git("merge-base", "--is-ancestor", build, parent)[0] == 0:
        return {}, "the negative control failed: a non-ancestor reported as an ancestor"

    present: dict[str, bool] = {}
    for sha in FIXES:
        if _git("rev-parse", "--verify", f"{sha}^{{commit}}")[0] != 0:
            return {}, f"{sha} does not resolve, so its absence would mean nothing"
        present[sha] = _git("merge-base", "--is-ancestor", sha, build)[0] == 0
    return present, ""


def served_modules(key: str, build: str) -> list[str]:
    """What the BROWSER receives, compared against the stamped build rather than the checkout."""
    notes = []
    for name in SERVED:
        status, body = _get(f"{DASHBOARD}/{name}", key)
        if status == 404:
            notes.append(f"{name}: 404 -- the browser has no such module")
            continue
        if status != 200 or not body:
            notes.append(f"{name}: could not be fetched (status {status})")
            continue
        code, stamped = _git_bytes("show", f"{build}:service/new_dashboard/{name}")
        head_code, head = _git_bytes("show", f"HEAD:service/new_dashboard/{name}")
        matches_stamped = code == 0 and body == stamped
        matches_head = head_code == 0 and body == head
        if matches_head:
            notes.append(f"{name}: matches HEAD -- the browser has this version's fixes")
        elif matches_stamped:
            notes.append(f"{name}: byte-identical to {build}, which is behind HEAD")
        else:
            notes.append(f"{name}: matches neither {build} nor HEAD")
    return notes


def deployed_queue_fires(scratch: Path) -> tuple[str, str]:
    """Run this version's own frame-sequence test against the CONTAINER's copy of the queue.

    A code read says the defect is in that file. Only running it says the defect FIRES.
    """
    pulled = scratch / "queue.deployed.py"
    done = subprocess.run(["docker", "cp", f"{CONTAINER}:/app/service/terminal_write_queue.py",
                           str(pulled)], capture_output=True, text=True)
    if done.returncode != 0 or not pulled.exists():
        return "unknown", "the container's queue could not be copied out"

    def run() -> int:
        return subprocess.run([sys.executable, "-m", "pytest", FRAME_TEST, "-q", "--no-header",
                               "-p", "no:cacheprovider"],
                              cwd=ROOT, capture_output=True, text=True).returncode

    backup = scratch / "queue.repo.py"
    shutil.copyfile(QUEUE, backup)
    try:
        if run() != 0:
            return "unknown", "the repo's own module fails this test, so the harness proves nothing"
        shutil.copyfile(pulled, QUEUE)
        deployed = run()
    finally:
        # `cp` BACK, never `git checkout` -- this repo has lost a just-written function that way.
        shutil.copyfile(backup, QUEUE)
    if run() != 0:
        return "unknown", "the module did not restore cleanly; treat this run as void"
    return ("carries", "the deployed queue FAILS this version's frame-sequence test") if deployed \
        else ("clear", "the deployed queue passes it")


def main() -> int:
    # A TEMPORARY DIRECTORY, NOT A DIRECTORY IN THE REPO. These are working copies of a module
    # being swapped in and out, not evidence -- and the gates walk the FILESYSTEM, so a scratch
    # file left in the tree is a file they judge and count. CLAUDE.md records a census reading
    # inflated by exactly that.
    scratch = Path(tempfile.mkdtemp(prefix="aify-deploy-check-"))
    key = _key()

    version, build = running_build(key)
    if not build:
        print("UNKNOWN: the service did not answer /health, so nothing here was measured.")
        return 2
    print(f"RUNNING: version {version}, build {build}")
    print()

    present, why = ancestry(build)
    if why:
        print(f"UNKNOWN: {why}")
        return 2
    missing = [sha for sha, yes in present.items() if not yes]
    print("THE FIXES, by ancestry (controls passed both ways):")
    for sha, subject in FIXES.items():
        print(f"  {'IN ' if present[sha] else 'NOT'}  {sha}  {subject}")
    print()

    print("WHAT THE BROWSER RECEIVES:")
    for note in served_modules(key, build):
        print(f"  {note}")
    print()

    verdict, detail = deployed_queue_fires(scratch)
    print(f"THE DEPLOYED QUEUE, RUN: {detail}")
    print()

    if verdict == "unknown":
        print("UNKNOWN: the decisive check could not run, and a check that gathered no evidence is "
              "not a passed one.")
        return 2
    if missing or verdict == "carries":
        print("THE RUNNING SERVICE CARRIES THE CONSOLE-TRANSPORT DEFECTS. The fixes are in the "
              "checkout and not in the deploy.")
        print("This is a mechanism demonstrated in the deployed artifact. It is NOT a reproduction "
              "of any particular lag, and the live coalescing rate is still unmeasured.")
        return 1
    print("The running service carries none of them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
