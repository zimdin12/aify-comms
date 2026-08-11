"""Two installer hazards found on 2026-08-11, one of them by causing an outage.

1. THE BRIDGE COPY WAS NOT ATOMIC. `copy_bridge_to_native_dir` used to `rm -rf` the live bridge
   directory and then spend ~4 seconds copying node_modules back. Interrupting that 1.2s in left
   720 of 4,200 files — server.js present, node_modules gutted — which is precisely the state that
   made `aify-doctor` unrunnable after the parallel-install incident. The copy now happens in a
   staging directory beside the live tree and is swapped in with two renames; the same interrupt
   leaves all 4,200 files intact.

2. A BARE `aify-comms` IS NOT A SMOKE TEST. The launcher execs the stdio server with
   `--environment-bridge`, so running it starts a REAL environment bridge, which by design
   supersedes whatever bridge is already serving that environment. I ran it for four seconds to
   confirm the launcher still started after editing it; the live bridge was superseded and exited,
   reaped its managed gateway hosts, my four-second process then died, and the host was left with
   no environment bridge and nine managed agents down mid-work. `--check` is the validation that
   was actually wanted, and the banner now says what starting it does.

Static-text checks against install.sh and the wrapper it emits, the same pattern as the other
test_install_*.py files — the behaviour lives in generated bash, so the shape is what can be
pinned here. The runtime proof for (1) is the interrupt measurement recorded in the source.
"""

from __future__ import annotations

from pathlib import Path

from service.tests._source import code_only as _code_only

REPO = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO / "install.sh"


def _install_sh() -> str:
    return INSTALL_SH.read_text(encoding="utf-8")


def _copy_fn() -> str:
    text = _install_sh()
    start = text.index("copy_bridge_to_native_dir()")
    return text[start : text.index("\n}\n", start)]


def _launcher() -> str:
    text = _install_sh()
    start = text.index("install_bridge_launcher()")
    return text[start : start + 12000]


# --- 1: the copy must never empty the live directory -----------------------


def test_the_copy_targets_a_staging_dir_not_the_live_one():
    fn = _copy_fn()
    assert "staging=" in fn, "the copy must land beside the live tree, not in it"
    for cmd in ("rsync -a --delete", "cp -RL"):
        idx = fn.index(cmd)
        line_end = fn.index("\n", idx)
        assert "$staging" in fn[idx:line_end], f"{cmd} must write into the staging dir"


def test_the_staged_tree_is_checked_before_it_can_replace_a_working_install():
    """Promoting a half-copied staging dir over a good install would be the same outage."""
    fn = _copy_fn()
    assert 'if [ ! -f "$staging/server.js" ] || [ ! -d "$staging/node_modules" ]' in fn
    assert "keeping the" in fn, "an incomplete stage must leave the existing install untouched"


def test_the_live_dir_is_moved_aside_rather_than_deleted():
    fn = _code_only(_copy_fn())
    assert 'mv "$AIFY_BRIDGE_DIR" "$retired"' in fn
    assert 'mv "$staging" "$AIFY_BRIDGE_DIR"' in fn
    # The in-place delete may still happen, but ONLY as the announced fallback when the rename is
    # blocked — never as the normal path.
    delete_at = fn.index('rm -rf "$AIFY_BRIDGE_DIR"')
    warn_at = fn.index("could not move the existing bridge dir aside")
    assert warn_at < delete_at, "the in-place delete must be reachable only after the warning"


def test_the_fallback_says_running_bridges_may_break():
    fn = _copy_fn()
    at = fn.index("could not move the existing bridge dir aside")
    branch = fn[at : at + 500]
    assert "may crash" in branch
    assert "restart your bridges" in branch


# --- 2: `aify-comms --check` must not register anything --------------------


def test_the_launcher_offers_a_check_that_starts_nothing():
    body = _launcher()
    assert '= "--check" ]' in body, "there must be a way to validate the launcher without starting it"
    at = body.index('= "--check" ]')
    branch = body[at : body.index('exit "\\$rc"', at)]
    assert "--environment-bridge" not in branch, "--check must never start the environment bridge"
    assert "node --check" in branch, "it should actually verify the script parses"


def test_check_is_dispatched_before_the_bridge_starts():
    # Anchored on `--environment-bridge`, not on "exec node": the doctor subcommand also execs
    # node, and matching that instead would make this assertion true for the wrong reason.
    body = _code_only(_launcher())
    assert body.index('= "--check" ]') < body.index("--environment-bridge")


def test_the_banner_states_that_starting_it_supersedes_the_live_bridge():
    """The old banner said 'aify-comms bridge', which reads like a client, not a takeover."""
    body = _launcher()
    at = body.index("echo \"aify-comms ENVIRONMENT BRIDGE")
    banner = body[at : body.index("exec node", at)]
    assert "SUPERSEDES" in banner
    assert "managed workers are reaped" in banner
    assert "--check" in banner, "the banner should point at the non-destructive alternative"
