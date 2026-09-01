// The bridge's copy of the shared vocabulary must agree with the contract.
//
// Finding H1 of the v0.2 review: the core vocabulary has no single home and is hand-copied across
// the language boundary. `service/contracts/vocabulary.json` is now that home.
//
// THE BRIDGE DELIBERATELY KEEPS ITS OWN LITERAL MAP. `install.sh` copies only `mcp/stdio/` into
// ~/.aify-comms, so anything under `service/` is simply ABSENT on the host where this code actually
// runs. Making the bridge read the contract at runtime would break the native-copy install — the
// very thing that exists because the repo checkout is too slow to load over a 9p/WSL2 bind mount.
// So the copy is allowed, and its agreement is enforced instead: the repo's standing rule that a
// duplication finding becomes an agreement test, not a forced refactor.
//
// The Python half of this agreement is `service/tests/test_vocabulary_contract.py`. Both halves are
// needed — that one catches Python drifting, this one catches the bridge drifting.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { LAUNCHABLE_RUNTIMES, RUNTIME_ALIASES } from "../runtimes.js";

const here = dirname(fileURLToPath(import.meta.url));
const contractPath = join(here, "..", "..", "..", "service", "contracts", "vocabulary.json");
const contract = JSON.parse(readFileSync(contractPath, "utf8"));

const expected = contract.runtimes.aliases;
const actual = Object.fromEntries(RUNTIME_ALIASES);

assert.deepEqual(
  actual,
  expected,
  "mcp/stdio/runtimes.js RUNTIME_ALIASES has diverged from service/contracts/vocabulary.json.\n" +
    "The bridge keeps its own copy on purpose (install.sh does not ship service/ to ~/.aify-comms),\n" +
    "so both must be updated in the same commit."
);

// Every alias must land on a runtime the contract calls canonical. A bridge alias pointing at an id
// the service does not know would normalize a real agent onto a runtime nothing can launch.
for (const [alias, target] of RUNTIME_ALIASES) {
  assert.ok(
    contract.runtimes.canonical.includes(target),
    `bridge alias ${alias} -> ${target}, which is not a canonical runtime in the contract`
  );
}

// THE LAUNCHABLE SET, which this file did not compare until 2026-09-01. Aliases and canonical ids
// were checked and the set that decides whether a spawn is even attempted was not, so the bridge's
// copy -- written inline inside `canLaunchRuntime` -- was gated by nothing on either side of the
// language boundary. The python freeze ledger does not scan JS, so nothing anywhere saw it.
assert.deepEqual(
  [...LAUNCHABLE_RUNTIMES].sort(),
  [...contract.runtimes.launchable].sort(),
  "mcp/stdio/runtimes.js LAUNCHABLE_RUNTIMES has diverged from service/contracts/vocabulary.json. " +
    "A bridge that thinks a runtime is launchable when the service does not (or the reverse) either " +
    "refuses a spawn the service would accept, or attempts one it cannot complete."
);

// Launchable must be a SUBSET of canonical, the same invariant the python contract test asserts.
// A launchable id that is not canonical could never be normalized onto by any alias.
for (const runtime of LAUNCHABLE_RUNTIMES) {
  assert.ok(
    contract.runtimes.canonical.includes(runtime),
    `bridge calls ${runtime} launchable, but the contract does not list it as canonical`
  );
}

// The agreement is only worth something if the file it reads is real. An unreadable or truncated
// contract must fail loudly rather than compare an empty object against an empty object.
assert.ok(
  Object.keys(expected).length >= 15,
  "the contract looks truncated - refusing to certify agreement against it"
);
assert.ok(
  Array.isArray(contract.runtimes.launchable) && contract.runtimes.launchable.length >= 3,
  "the contract's launchable list looks empty or missing - refusing to certify agreement against it"
);

console.log("vocabulary agreement: bridge RUNTIME_ALIASES and LAUNCHABLE_RUNTIMES match the contract");
