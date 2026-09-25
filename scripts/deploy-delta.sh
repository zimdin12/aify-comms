#!/bin/bash
# DID THE UPDATE TAKE EFFECT, answered by what changed rather than by a list of what to look at.
#
#   bash scripts/deploy-delta.sh capture <file>            # record the doctor's verdicts
#   bash scripts/deploy-delta.sh compare <before> <after>  # say what the update changed
#
# `compare` exits 1 when a check that was PASSING now fails, and 0 otherwise. A check that was
# already failing before the update is reported and does not fail the comparison: the question this
# answers is "what did this update do", not "is this host perfect".
#
# WHY IT EXISTS. CLAUDE.md opens with it: *every deploy path in this repo fails silently*. No error,
# everything looks installed, and what you changed is not what is running. `redeploy.sh` is the
# documented one-command update and it ended by reporting that it had finished -- which is a claim
# about what it ATTEMPTED. The operator's ask was that update be a supported verb rather than a
# reinstall that happens to work, and a verb that cannot say whether it worked is the same reinstall
# with a better name.
#
# NOTHING HERE NAMES A CHECK, and that is the design. The obvious version selects the four or five
# doctor checks that "answer whether a deploy took" -- and a hand-kept list of those is a defect with
# a delay on it: a check added later answers the question and is never consulted, exactly the way
# four scanners hardcoded the doctor's filename and moving one check reddened three while the fourth
# stayed green by no longer looking. Comparing the WHOLE verdict set before and after needs no list
# and cannot go stale, and it also catches the case a list would never have covered: an update that
# breaks something nobody thought to associate with it.
#
# THE DOCTOR IS THE INSTRUMENT because it proves each claim against the RUNNING system rather than
# checking that a file exists. That is the only kind of evidence that can distinguish a deploy which
# landed from one which reported success and changed nothing.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

#: The verifier, by the name the host has it under. `aify-doctor` is the older name and still works;
#: preferring the command means this measures what an operator would measure.
doctor_cmd() {
  if command -v aify-comms >/dev/null 2>&1; then
    printf 'aify-comms doctor'
  elif command -v aify-doctor >/dev/null 2>&1; then
    printf 'aify-doctor'
  fi
}

capture() {
  local out="${1:-}"
  [ -n "$out" ] || { echo "capture needs a file path" >&2; return 2; }
  local cmd; cmd="$(doctor_cmd)"
  if [ -z "$cmd" ]; then
    # A FIRST INSTALL HAS NO DOCTOR YET, which is not a failure -- there is nothing to compare
    # against on a host that has never been installed. An empty capture makes `compare` say "no
    # baseline" instead of inventing one, and absence stays absence.
    : > "$out"
    return 0
  fi
  # `id state` per line, sorted, so `compare` is a join rather than a JSON dependency. The doctor's own
  # --json is the source; nothing here re-derives a verdict. NODE, not python: the installer already
  # requires node, and a stock Ubuntu ships `python3` with no `python`, which left every capture empty
  # and every redeploy UNVERIFIED (v0.7 scan B10). A skipped row is its own state -- the doctor marks
  # it `ok: false` so nothing reads it as a pass, and it is not a failure either.
  $cmd --json 2>/dev/null | node -e '
let input = "";
process.stdin.on("data", (chunk) => { input += chunk; });
process.stdin.on("end", () => {
  let checks = [];
  try { checks = JSON.parse(input).checks || []; } catch { process.exit(0); }
  const state = (c) => (c.skipped ? "skip" : c.ok ? "ok" : "fail");
  const rows = checks.map((c) => `${c.id} ${state(c)}`).sort();
  if (rows.length) process.stdout.write(rows.join("\n") + "\n");
});
' 2>/dev/null | tr -d '\r' > "$out" || : > "$out"
}

compare() {
  local before="${1:-}" after="${2:-}"
  [ -n "$before" ] && [ -n "$after" ] || { echo "compare needs two files" >&2; return 2; }
  if [ ! -s "$after" ]; then
    echo "  (the verifier could not be run, so this update is UNVERIFIED)"
    return 0
  fi
  if [ ! -s "$before" ]; then
    echo "  (no baseline: nothing to compare this update against)"
    return 0
  fi

  local broke=0
  # THREE CATEGORIES AND THEY MUST NOT BE COLLAPSED, plus a skip, which is none of them: a check that
  # could not run on this host (Windows cannot read /proc) is neither broken nor still failing. "Was fine, now broken" is what an update did.
  # "Was broken, now fine" is what it fixed, and an operator who is not told stops believing the
  # tool. "Still broken" is pre-existing and must not be attributed to this update -- misattributing
  # a cause sends the next reader somewhere else entirely.
  # STRIPPED OF CARRIAGE RETURNS, and this is not defensive style -- it is the bug this script
  # nearly shipped with. On Windows the verifier's output arrives CRLF, so `state` was "ok" with a trailing carriage return, and
  # matched neither "ok" nor "fail": every comparison fell through all three branches and printed
  # NOTHING, with exit 0. A tool whose whole job is to say what changed reported "no change" for
  # every update, silently. It looked correct in testing because `sed -i` had normalised the fixture
  # between the first case and the second.
  while read -r id state; do
    id="${id%$'\r'}"; state="${state%$'\r'}"
    [ -n "$id" ] || continue
    local was; was="$(tr -d '\r' < "$before" | awk -v k="$id" '$1 == k { print $2 }')"
    if [ "$state" = "fail" ] && [ "$was" = "ok" ]; then
      echo "  BROKEN BY THIS UPDATE: $id"
      broke=$((broke + 1))
    elif [ "$state" = "ok" ] && [ "$was" = "fail" ]; then
      echo "  fixed by this update:  $id"
    elif [ "$state" = "fail" ]; then
      echo "  still failing (was already): $id"
    elif [ "$state" = "skip" ] && [ "$was" = "ok" ]; then
      echo "  no longer verified here: $id"
    fi
  done < "$after"

  if [ "$broke" -gt 0 ]; then
    echo "  $broke check(s) that passed before this update now fail. Run '$(doctor_cmd)' for detail." >&2
    return 1
  fi
  return 0
}

case "${1:-}" in
  capture) shift; capture "$@" ;;
  compare) shift; compare "$@" ;;
  *) echo "usage: deploy-delta.sh capture <file> | compare <before> <after>" >&2; exit 2 ;;
esac
