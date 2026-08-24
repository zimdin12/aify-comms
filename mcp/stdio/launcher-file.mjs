// Which FILE to hand aify-env when delegating a spawn.
//
// aify-env executes an allowlisted launcher and refuses anything without a `HARNESS_WRAPPER_VERSION`
// marker and a shebang. That guarantee is the point: it is what stops an environment being asked to
// run an arbitrary program.
//
// On Windows, resolving `claude-aify` the way you resolve an executable returns the generated
// `claude-aify.cmd` shim, which carries neither -- so a delegated spawn was refused before it started,
// on every Windows host, while resolving perfectly well. Measured 2026-08-25:
//
//   resolveExecutable("claude-aify") -> C:\Users\...\.local\bin\claude-aify.cmd   REFUSED
//   the sibling with no extension    -> C:\Users\...\.local\bin\claude-aify       ACCEPTED
//
// So delegation asks a different question from "what would Windows run": it asks which file IS the
// launcher. The shim exists so `claude-aify` works from cmd.exe and PowerShell; the file beside it is
// the launcher itself, and that is the one an environment runs through its own interpreter.

/** Extensions Windows adds to make a script callable, none of which are the launcher. */
const SHIM_EXTENSIONS = [".cmd", ".bat", ".exe", ".ps1"];

/** The marker aify-env requires. Matched loosely here: aify-env is the authority, this only ranks. */
const MARKER = /^[ \t]*HARNESS_WRAPPER_VERSION[ \t]*=/m;

/**
 * Candidate paths for one resolved command, best first.
 *
 * Pure and separately testable, because the ordering is the whole decision: preferring the shim is
 * what made delegation impossible on Windows, and preferring the launcher is invisible on Linux where
 * the two are the same path.
 */
export function launcherCandidates(resolvedPath) {
  const path = String(resolvedPath ?? "");
  if (!path) return [];
  const lower = path.toLowerCase();
  const shim = SHIM_EXTENSIONS.find((extension) => lower.endsWith(extension));
  // The extensionless sibling FIRST: it is the launcher, the shim merely calls it.
  return shim ? [path.slice(0, -shim.length), path] : [path];
}

/**
 * The candidate that actually carries the launcher marker.
 *
 * @param {string} resolvedPath what resolving the command produced
 * @param {(path: string) => string} readFile throws when a path is not readable
 * @returns {{path: string, checked: string[]}|null} null when no candidate is a launcher, with the
 *   list of what was tried so the caller can say which files it looked at rather than only that it
 *   failed.
 */
export function launcherFileFor(resolvedPath, readFile) {
  const checked = [];
  for (const candidate of launcherCandidates(resolvedPath)) {
    checked.push(candidate);
    let text = "";
    try {
      text = readFile(candidate);
    } catch {
      continue;
    }
    if (MARKER.test(text)) return { path: candidate, checked };
  }
  return null;
}
