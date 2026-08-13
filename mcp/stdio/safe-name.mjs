// What counts as a legal agent id, channel name, or shared-file name.
//
// v0.5.4 layer 0 of the server.js decomposition. `validateName` gates twelve of the bridge's tools and
// is the check standing between a caller-supplied string and the places those strings end up: URL path
// segments, shared-artifact filenames, and agent registry keys. It lived in `server.js`, the bin entry
// point, which nothing imports — so the repo's input-validation boundary had no direct test at all.
//
// It moved as a pair with its regex, which has no other reader: `SAFE_NAME_RE` was referenced exactly
// twice in server.js, its own declaration and the one test inside this function. A constant with no
// authority outside a single function belongs with that function.
//
// WHAT THE PATTERN ACTUALLY BUYS. The first character must be alphanumeric, which is what stops a name
// from beginning with `.` — so `..` and `.hidden` are rejected outright. No separator character is
// permitted at all, so no accepted name can traverse a directory or split a URL path. The 128-character
// ceiling keeps a name inside filesystem limits once it is joined to a directory prefix.

const SAFE_NAME_RE = /^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$/;

export function validateName(name, label = "name") {
  if (!SAFE_NAME_RE.test(name)) {
    throw new Error(`Invalid ${label}: must be 1-128 alphanumeric chars, dots, hyphens, underscores. Got: "${name}"`);
  }
}

export { SAFE_NAME_RE };
