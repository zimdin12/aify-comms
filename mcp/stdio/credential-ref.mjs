// Whether a `credentialRef` is one aify-env will actually resolve.
//
// CRED-L2, external review round 7, and it is NOT the hole it was filed as. `service-registry.mjs`
// checked only that the ref was a non-empty string, while the comment beside that check claims "a
// registry entry cannot point that daemon at a path of its choosing". That claim is TRUE -- but it is
// aify-env that makes it true, by refusing a bad ref at READ time. Nothing here prevented writing one.
//
// SO THE DEFECT IS TIMING, NOT AUTHORITY. The installer wrote whatever `CREDENTIAL_REF` held, reported
// success, and aify-env then declined to resolve it. Two components each behaving correctly, and an
// operator with a credential that does not work, a green install, and nothing connecting the two.
// Refusing at write time turns that into one legible error at the moment somebody can still fix it.
//
// A SECOND COPY OF SOMEBODY ELSE'S GRAMMAR, DELIBERATELY. aify-env owns this rule -- it is the daemon
// that resolves the name under its own root -- and aify-comms cannot import it: they are separate
// packages and aify-env is not a dependency. The alternative to duplicating is to keep writing refs
// the consumer rejects, which is the defect. So this is a cache of a decision made elsewhere, and the
// agreement test beside it drives BOTH implementations over the same corpus and fails when they
// disagree. That is this project's standing answer to duplication: an agreement test, not a refactor.
//
// It also fails when the aify-env checkout is absent, rather than skipping. A cross-repo proof that
// quietly does not run is worse than no proof, because the report still reads green.

//: One name, no path separators. Anything else is a path, and a path is what this refuses to be.
const REF_PATTERN = /^[A-Za-z0-9._-]+$/;

//: Reserved on Windows whatever the extension: writing to `CON` or `PRN` talks to a device rather
//: than creating a file, so a store that accepted one would read back something it never wrote.
const WINDOWS_DEVICE_NAMES = new Set([
  "con", "prn", "aux", "nul",
  "com1", "com2", "com3", "com4", "com5", "com6", "com7", "com8", "com9",
  "lpt1", "lpt2", "lpt3", "lpt4", "lpt5", "lpt6", "lpt7", "lpt8", "lpt9",
]);

/**
 * The reason aify-env would refuse this reference, or "" if it would accept it.
 *
 * A reason rather than a boolean, for the same purpose it serves there: "the registry names a
 * credential I refuse to resolve" has to reach an operator as something more useful than a key that
 * silently is not there.
 *
 * @param {unknown} ref
 * @returns {string} empty when acceptable
 */
export function credentialRefProblem(ref) {
  const text = String(ref ?? "");
  if (text === "") return "empty";
  if (text.length > 64) return "too long";
  if (!REF_PATTERN.test(text)) {
    return "must be one name of letters, digits, dot, dash or underscore -- no path separators";
  }
  // The charset accepts both of these, and both resolve outside the file they name.
  if (text === "." || text === "..") return "must name a file, not a directory";
  // A leading dot is not traversal, but it hides the file from an operator listing the store while
  // wondering what holds their secrets.
  if (text.startsWith(".")) return "must not start with a dot";
  const stem = text.split(".")[0].toLowerCase();
  if (WINDOWS_DEVICE_NAMES.has(stem)) return `'${stem}' is a reserved device name on Windows`;
  return "";
}
