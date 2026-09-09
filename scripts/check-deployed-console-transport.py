"""Is the RUNNING service carrying the console-transport defects this version fixed?

WHY THIS EXISTS. Every hop of B_TRANSPORT was measured on a fixture, and the operator's "our browser
terminal kind of lags sometimes" is STILL UNATTRIBUTED -- a fixture is not their machine. So this
asks a narrower question that can actually be answered: "is the mechanism I already fixed present in
what they are RUNNING?". It is, on both ends. That is not the same as explaining the lag, and an
earlier version of this paragraph said the question "closed it", which claims exactly what has not
been shown -- a first reader meets that sentence and not the withdrawal three commits later. What
this check does is answer the narrow question again after any deploy.

WHAT IT ASKS, and every one of them is read from the running system rather than from git:

  1. WHICH BUILD is serving, from `GET /health`.
  2. WHETHER THE THREE FIXES ARE IN IT, by ancestry -- controlled both ways, because an
     `--is-ancestor` that cannot say YES cannot say NO. The stamped build's own parent is the
     positive control; a made-up sha is the negative one.
  3. WHAT THE BROWSER ACTUALLY RECEIVES, fetched from the dashboard rather than read from the
     checkout. `console-cursor.mjs` is the cheapest tell, because this version created it -- but a
     404 proves absence AT THAT URL and not, on its own, an old bundle. It BLOCKS a clear verdict
     rather than asserting a defect; the version that printed the 404 and then concluded "carries
     none of them" is the shape this check exists to avoid.
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
import re
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

#: What THIS test's own failure says. An exit 1 whose output names none of these is some other
#: failure -- a fixture, an import, a collection error dressed as one -- and must not be reported
#: as the deployed queue carrying the defect.
#: What pytest prints for a FAILING ASSERTION, as opposed to any other exception reaching `E `.
ASSERTION_LINE = re.compile(r"E\s+(AssertionError\b|assert\b)")

FRAME_FAILURE_MARKS = (
    "counts POSTS, not frames",
    "coalescing is supposed to emit exactly one frame",
    "consecutive frames carried",
)

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
    # THE NEGATIVE CONTROL NEEDS GIT'S OWN "NO", WHICH IS EXIT 1. Reading "non-zero" as "the
    # control passed" let an error 128 -- git declining to answer at all -- satisfy the very
    # check that exists to prove git can say no.
    reverse = _git("merge-base", "--is-ancestor", build, parent)[0]
    if reverse != 1:
        return {}, (f"the negative control did not get a clean 'no' (git exited {reverse}), so "
                    f"this run cannot tell an absence from an error")

    present: dict[str, bool] = {}
    for sha in FIXES:
        if _git("rev-parse", "--verify", f"{sha}^{{commit}}")[0] != 0:
            return {}, f"{sha} does not resolve, so its absence would mean nothing"
        # 0 IS YES AND 1 IS NO; ANYTHING ELSE IS GIT DECLINING TO ANSWER. Treating 128 as "not
        # an ancestor" reports a confirmed absence on the strength of an error.
        code = _git("merge-base", "--is-ancestor", sha, build)[0]
        if code not in (0, 1):
            return {}, f"git exited {code} deciding whether {sha} is in {build}"
        present[sha] = code == 0
    return present, ""


def served_modules(key: str, build: str) -> tuple[list[str], str]:
    """What the BROWSER receives, and whether that evidence can be read at all.

    RETURNS ITS OWN VERDICT NOW. These notes used to be printed and then ignored: a module
    answering 500, or bytes matching NEITHER the stamped build nor HEAD, described a browser
    running something unaccountable and the run still concluded "carries none of them", exit 0.
    A 404 is different and is EVIDENCE rather than a failure -- `console-cursor.mjs` does not
    exist before this version's console work, so its absence is the cheapest tell that the
    bundle predates it.
    """
    notes: list[str] = []
    unreadable: list[str] = []
    missing: list[str] = []
    for name in SERVED:
        status, body = _get(f"{DASHBOARD}/{name}", key)
        if status == 404:
            # MISSING, WHICH IS EVIDENCE AND NOT A PASS. `console-cursor.mjs` does not exist
            # before this version's console work, so a 404 is the cheapest tell that the bundle
            # predates it -- but it proves absence AT THIS URL and nothing on its own. With
            # every fix present and a passing queue, 404s for all three still reached "carries
            # none of them", exit 0. It blocks CLEAR now, kept distinct from a transport failure.
            notes.append(f"{name}: 404 -- the browser has no such module")
            missing.append(name)
            continue
        if status != 200 or not body:
            notes.append(f"{name}: could not be fetched (status {status})")
            unreadable.append(f"{name} answered {status}")
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
            unreadable.append(f"{name} matches neither build")
    return notes, ("; ".join(unreadable) if unreadable else ""), missing


def _isolated_tree(scratch: Path) -> Path | None:
    """A throwaway copy of the `service` package, so nothing here writes to the real checkout.

    THE FIRST VERSION OF THIS CHECK WROTE TO `service/terminal_write_queue.py` IN THE SHARED
    TREE -- the container's copy in, a backup back out -- and called itself read-only because it
    touched the live service only for reads. Read-only toward the service is not read-only
    toward the checkout: between those two writes a concurrent edit is erased, and the script
    exited 0 having said nothing. A diagnostic that silently reverts somebody's work is worse
    than what it diagnoses.

    Python files only, which is what the package and its tests are; the caller's control run
    catches a tree missing anything the test actually needs.
    """
    tree = scratch / "tree"
    try:
        shutil.copytree(ROOT / "service", tree / "service",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "node_modules"))
    except OSError:
        return None
    return tree


def deployed_queue_fires(scratch: Path) -> tuple[str, str]:
    """Run this version's own frame-sequence test against the CONTAINER's copy of the queue.

    A code read says the defect is in that file. Only running it says the defect FIRES -- and it
    is run in a COPY of the tree, never in the checkout.
    """
    pulled = scratch / "queue.deployed.py"
    done = subprocess.run(["docker", "cp", f"{CONTAINER}:/app/service/terminal_write_queue.py",
                           str(pulled)], capture_output=True, text=True)
    if done.returncode != 0 or not pulled.exists():
        return "unknown", "the container's queue could not be copied out"

    tree = _isolated_tree(scratch)
    if tree is None:
        return "unknown", "an isolated copy of the service tree could not be made"
    target = tree / "service" / "terminal_write_queue.py"

    def run() -> tuple[int, str]:
        done = subprocess.run([sys.executable, "-m", "pytest", FRAME_TEST, "-q", "--no-header",
                               "-p", "no:cacheprovider"],
                              cwd=tree, capture_output=True, text=True)
        return done.returncode, done.stdout + done.stderr

    # THE COPY IS CONTROLLED BEFORE IT IS USED. A tree missing something the test needs fails for
    # reasons that have nothing to do with the deployed module, and that failure would otherwise
    # read as "the deployed queue is broken".
    baseline, baseline_out = run()
    if baseline != 0:
        return "unknown", ("the repo's own module fails this test inside the isolated tree, so "
                           "the harness proves nothing about the deployed one")
    shutil.copyfile(pulled, target)
    deployed, output = run()
    # PYTEST'S EXIT CODES ARE A VOCABULARY. 0 passed, 1 tests FAILED, 2 collection error, 3
    # internal, 4 usage, 5 nothing collected. Reading "non-zero" as "the deployed queue is
    # defective" turns a broken harness into a confident finding -- and 5, no tests collected,
    # is exactly what a mis-copied tree produces.
    if deployed == 0:
        return "clear", "the deployed queue passes it"
    if deployed != 1:
        return "unknown", (f"pytest exited {deployed} against the deployed module -- that is not "
                           f"a test failure, so nothing was established either way")
    # AND EXIT 1 IS NOT ENOUGH EITHER. It says SOMETHING failed, not that the frame-sequence
    # assertions did: a fixture setup error inside the isolated tree exits 1 and would be
    # reported as a deployed defect. The failure has to name the property being claimed.
    # PYTEST'S OWN ATTRIBUTION, NOT SUBSTRING SOUP. Both earlier versions were fooled by the same
    # transcript: a FIXTURE raising `RuntimeError("setup failed while checking consecutive frames
    # carried")` prints `E RuntimeError: ...`, which starts with `E ` AND contains the mark, and the
    # word "failed" sits inside the exception text. Reproduced with real pytest: 1 error, exit 1,
    # and this reported CARRIES.
    #
    # pytest distinguishes them in its OWN summary: a failing assertion is `FAILED <file>::<test>`
    # and a fixture blowing up is `ERROR <file>::<test>`. So the summary must name a FAILURE in this
    # file, no ERROR for it, and the mark must sit on an assertion line.
    name = Path(FRAME_TEST).name
    summary = [l.strip() for l in output.splitlines()]
    failed_here = any(l.startswith("FAILED") and name in l for l in summary)
    errored_here = any(l.startswith("ERROR") and name in l for l in summary)
    # AN ASSERTION LINE, not merely an `E ` line. `AssertionError` and a bare `assert` are the
    # assertion; `RuntimeError` and friends are something else blowing up. My first filter excluded
    # anything containing "Error" and so threw away `E AssertionError:` itself -- which broke the
    # TRUE positive while fixing the false one, and the live run caught it immediately.
    assertion_lines = [l for l in output.splitlines()
                       if ASSERTION_LINE.match(l.lstrip())]
    named = any(mark in line for line in assertion_lines for mark in FRAME_FAILURE_MARKS)
    if errored_here or not failed_here or not named:
        return "unknown", (f"pytest exited 1 with failed={failed_here} errored={errored_here} "
                           f"assertion-named={named} -- that is not this test's frame-sequence "
                           f"assertions failing, so it is not what this check reports on")
    return "carries", "the deployed queue FAILS this version's frame-sequence test"


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
    notes, browser_unreadable, browser_missing = served_modules(key, build)
    for note in notes:
        print(f"  {note}")
    print()
    if browser_unreadable:
        print(f"UNKNOWN: the browser evidence could not be read -- {browser_unreadable}. This")
        print("used to print and then be ignored while the run concluded anyway.")
        return 2

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
              "of any particular lag.")
        # THE LIVE COALESCING RATE IS NO LONGER UNMEASURED, and this line said it was for a week.
        # Because THIS queue emits one frame per flush numbered with the cumulative POST count --
        # which is exactly what the test above establishes by running the container's own module
        # -- an observed sequence step of N IS the number of posts that flush carried.
        print()
        print("AND THE LIVE COALESCING RATE IS MEASURED, which this line denied. Because THIS")
        print("queue numbers ONE frame per flush with the cumulative POST count, an observed")
        print("sequence step of N IS the posts that flush carried -- so the wire reads the rate")
        print("directly. `measure-live-frame-gaps.mjs` saw ZERO steps over one across 83,097")
        print("recorded comparisons in four guarded windows: no flush coalesced in any of them.")
        print("That is bounded windows on one fleet, not a statement about the day, and it")
        print("attributes no lag -- nothing there observed a console, a fetch or a repaint.")
        return 1
    # A MISSING MODULE BLOCKS CLEAR WITHOUT ASSERTING THE DEFECT. A 404 proves absence at that
    # URL; it does not by itself prove an old bundle, and the browser could be served from
    # somewhere this check does not know about. What it must never do is what it used to:
    # print the 404 and then report "carries none of them", exit 0.
    if browser_missing:
        print(f"UNKNOWN: the browser did not serve {', '.join(browser_missing)}, so what it is",
              "running was not established. Everything else here is clear, which is not the",
              "same as the console being current.")
        return 2
    print("The running service carries none of them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
