// Whether a credential file is private enough to read a secret from.
//
// WHY THIS EXISTS, and it is a correction of my own judgement. When this bridge learned to read
// aify-env's credential store I implemented the ref grammar and the byte decoding, and skipped the
// CUSTODY checks -- the ACL, ownership, link and hard-link rules -- on the reasoning that they were
// unreachable without aify-env's own helpers. Review demonstrated otherwise, on this host: a
// credential written through aify-env's real secure writer was accepted by both readers; after a real
// `icacls` grant of Everyone read on that file, aify-env returned CREDENTIAL_INSECURE ("readable by
// Everyone (a group)") while this bridge still handed back the key. Missing dependency helpers do not
// waive the store's contract.
//
// SO THE CONTRACT IS CACHED HERE, deliberately, exactly as `credential-ref.mjs` caches the ref
// grammar: aify-env owns these rules and is not a dependency of this package, so the alternative to
// duplicating is to keep reading credentials the owner would refuse.
// `tests/the-credential-custody-agrees-with-aify-env.test.js` drives BOTH implementations over one
// corpus and fails when they disagree -- and fails when the aify-env checkout is absent rather than
// skipping, because a cross-repo proof that quietly does not run is worse than none.
//
// CHMOD PROVES NOTHING ON WINDOWS, measured by aify-env on this same host: a file set to 000 stayed
// fully readable, and Node's chmod there only toggles the read-only attribute. The DACL is the real
// answer, so on win32 this runs `icacls` and parses it. That is a process spawn, and it is paid ONLY
// on the store path -- a process whose environment already carries a key never reaches here.
//
// FAIL CLOSED, ALWAYS. An ACL that cannot be read, a parse that understood nothing, a stat that
// threw: each returns a REASON, never "". Reading a secret on the strength of a check that did not
// run is the failure this module exists to make impossible.

import { execFileSync } from "node:child_process";
import { lstatSync } from "node:fs";

//: aify-env's `MAX_CREDENTIAL_BYTES`, checked here too so an oversized file is refused before it is
//: opened rather than after.
const MAX_CREDENTIAL_BYTES = 4096;

//: Principals that are a GROUP or a broad authority rather than one account.
const BROAD_PRINCIPALS = new Set([
  "everyone", "users", "authenticated users", "interactive", "network", "batch", "service",
  "terminal server user", "remote desktop users", "guests", "guest",
]);

//: Allowed beside the owner, and only this. A service account genuinely needs SYSTEM on hosts where
//: the daemon runs as one. `Administrators` is deliberately NOT here: whether a principal COULD
//: escalate to read the file is a different question from whether the file is currently granted to
//: more accounts than its owner, and on a machine with several administrators the second answer is
//: the operator's to make rather than this check's to assume.
const ALWAYS_ALLOWED = new Set(["system"]);

/** `DOMAIN\\user` -> `user`, lowercased. */
export function aclPrincipalLeaf(principal) {
  const text = String(principal ?? "").trim();
  const cut = text.lastIndexOf("\\");
  return (cut === -1 ? text : text.slice(cut + 1)).trim().toLowerCase();
}

/**
 * The access-control entries in `icacls` output.
 *
 * THE PATH IS REMOVED BY NAME, not guessed at. The first line is `<path> <principal>:(flags)` with no
 * unambiguous delimiter: a Windows path contains backslashes, so a domain-qualified principal cannot
 * be found by "the last backslash", and a path may contain spaces, so not by "the last space" either.
 * With an unqualified principal -- `Everyone` being the obvious one -- the last backslash falls
 * inside the PATH and the whole line parses as one principal named after the directory.
 */
export function parseIcaclsAces(text, { path = "" } = {}) {
  const out = [];
  const target = String(path ?? "");
  for (const rawLine of String(text ?? "").split(/\r?\n/)) {
    let line = rawLine.trim();
    if (!line) continue;
    if (/^Successfully processed|^Failed processing/i.test(line)) continue;
    if (target && line.startsWith(target)) line = line.slice(target.length).trim();
    const match = line.match(/([^:]+):((?:\([^)]*\))+)\s*$/);
    if (!match) continue;
    let principal = match[1].trim();
    const flags = match[2];
    if (!target) {
      const domainCut = principal.lastIndexOf("\\");
      const before = domainCut === -1 ? principal : principal.slice(0, domainCut);
      const space = before.lastIndexOf(" ");
      if (space !== -1) principal = principal.slice(space + 1).trim();
    }
    if (!principal) continue;
    out.push({ principal, leaf: aclPrincipalLeaf(principal), flags, inherited: flags.includes("(I)") });
  }
  return out;
}

/**
 * Who, besides the owner, this file is granted to.
 *
 * AN EMPTY PARSE IS A REFUSAL, not "no grants, therefore private". It is far more likely to mean the
 * output changed shape or the command failed, and reading a secret on the strength of a parser that
 * understood nothing is exactly the false negative this exists to avoid.
 */
export function aclProblem(aces, { owner = "" } = {}) {
  const entries = Array.isArray(aces) ? aces : [];
  if (entries.length === 0) {
    return "no access-control entries could be read, so the permissions are unknown";
  }
  const ownerLeaf = aclPrincipalLeaf(owner);
  const offenders = [];
  for (const ace of entries) {
    const leaf = String(ace?.leaf ?? aclPrincipalLeaf(ace?.principal));
    if (leaf && leaf === ownerLeaf) continue;
    if (ALWAYS_ALLOWED.has(leaf)) continue;
    if (BROAD_PRINCIPALS.has(leaf)) {
      offenders.push(`${ace.principal} (a group)`);
      continue;
    }
    offenders.push(String(ace.principal));
  }
  return offenders.length ? `readable by ${offenders.join(", ")}` : "";
}

/** The account this process runs as, in the form `icacls` prints. */
export function currentWindowsOwner(env = process.env) {
  const user = String(env.USERNAME || "").trim();
  const domain = String(env.USERDOMAIN || "").trim();
  if (!user) return "";
  return domain ? `${domain}\\${user}` : user;
}

/**
 * What is wrong with this file's custody, if anything.
 *
 * PURE. `stats` must come from an `lstat` -- a `stat` follows a symlink, so the very thing being
 * checked for would be checked on the wrong file.
 *
 * @returns {string} a reason, or "" when the file is private to this process
 */
export function custodyProblem({
  platform = process.platform, stats = null, aclText = "", owner = "", uid = -1, aclPath = "",
} = {}) {
  if (!stats) return "no file information";
  if (stats.isSymbolicLink()) return "is a link rather than a regular file";
  if (!stats.isFile()) return "is not a regular file";
  if (stats.size > MAX_CREDENTIAL_BYTES) {
    return `is ${stats.size} bytes, over the ${MAX_CREDENTIAL_BYTES} limit`;
  }
  if (platform === "win32") {
    return aclProblem(parseIcaclsAces(aclText, { path: aclPath }), { owner });
  }
  if (typeof stats.nlink === "number" && stats.nlink > 1) {
    return `has ${stats.nlink} hard links, so the same bytes are reachable under another name`;
  }
  if (uid >= 0 && typeof stats.uid === "number" && stats.uid !== uid) {
    return `is owned by uid ${stats.uid}, not by this process`;
  }
  const mode = stats.mode & 0o777;
  if (mode & 0o077) return `mode ${mode.toString(8).padStart(3, "0")} grants access beyond its owner`;
  return "";
}

/** `icacls <path>`, or its own failure output. Never receives or prints the key. */
function readAclSync(target, run) {
  try {
    return String(run("icacls", [target], {
      encoding: "utf8", timeout: 5000, windowsHide: true, stdio: ["ignore", "pipe", "pipe"],
    }) || "");
  } catch (failure) {
    // Its OWN output on failure, not a throw: an unreadable ACL must reach `aclProblem`, which treats
    // "no entries could be read" as a refusal rather than as an absence of problems.
    return String(failure?.stdout || "");
  }
}

/**
 * The custody verdict for a real path on this host, gathered and judged.
 *
 * @returns {string} a reason to refuse, or "" when the file may be read
 */
export function custodyProblemFor(target, {
  platform = process.platform, env = process.env, lstat = lstatSync, run = execFileSync,
} = {}) {
  let stats;
  try {
    stats = lstat(target);
  } catch (failure) {
    return `could not inspect the file: ${failure?.code || failure}`;
  }
  return custodyProblem({
    platform,
    stats,
    aclPath: target,
    aclText: platform === "win32" ? readAclSync(target, run) : "",
    owner: platform === "win32" ? currentWindowsOwner(env) : "",
    uid: typeof process.getuid === "function" ? process.getuid() : -1,
  });
}
