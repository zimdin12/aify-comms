// Dedupe a list, keeping the FIRST occurrence of each value.
//
// Two unrelated callers in this bridge want exactly this and want the order preserved, which is why it is a
// leaf of its own rather than a private helper of either: `cwdRootsForEnvironment` builds the workspace
// roots an environment advertises, and message fan-out builds a recipient list. Order matters in both — the
// first cwd root is the default workspace a spawn lands in, and recipients are reported back to the sender
// in the order they were addressed — so `[...new Set(values)]` would be correct only by accident of V8's
// insertion order, and a sort would be actively wrong.
//
// FALSY VALUES ARE DROPPED, not merely deduped, and that is deliberate rather than incidental. Both callers
// build their input by splitting and trimming strings, so an empty entry is the residue of a trailing
// delimiter or a blank env var — never a meaningful member. A `""` cwd root would advertise the process's
// working directory as a workspace root under a name nothing can match; a `""` recipient would be an
// address. Dropping them at the join is cheaper than every caller filtering first, and the tests pin it so
// a future caller that needs `0` or `""` preserved has to make that a decision.

export function dedupePreserveOrder(values) {
  const seen = new Set();
  const result = [];
  for (const value of values || []) {
    if (!value || seen.has(value)) continue;
    seen.add(value);
    result.push(value);
  }
  return result;
}
