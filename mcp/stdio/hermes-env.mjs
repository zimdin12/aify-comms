// Environment identity for the hermes managed host: which machine it is on, which runtime name it reports, and
// its temp directory. (The hermes command left with the gateway start in 0.9.10: the launcher runs hermes now.)
//
// A NEUTRAL owner, created in v0.5.4 because these three constants have readers on BOTH sides of the
// gateway extraction — eight host-side functions read them, and so does the gateway. Leaving them in
// hermes-managed-host.js would force the new gateway module to import UPWARD from the file it is draining,
// which is the dependency inversion the Python `test_leaves_do_not_import_the_carrier` gate exists to stop.
// Moving them into the gateway would be worse in a quieter way: the host would then import its own machine
// id from a module named after gateways.
//
// A constant with readers on both sides of a boundary belongs to neither of them.
//
// Note what is NOT here: the gateway's own timeouts. Those have a single reader each, inside the gateway,
// so they follow it. This module is for shared identity, not for every constant that exists.

import os from "os";

import { defaultMachineId } from "./machine-id.mjs";

export const MACHINE_ID = defaultMachineId();
export const RUNTIME = "hermes";

// v0.5.4: `TMP_DIR` arrived with the active-session slice. Seven host functions read it and one session
// function does, and it is `process.env.TEMP || process.env.TMP || os.tmpdir()` — environment identity with
// no session semantics, which is exactly what this module is for. Compare `ATTACH_WAIT_MS`, which has the
// same two-sided readership and went the OTHER way, into the session module, because attach timing IS a
// session concept. Two-sided readership decides that a constant needs a deliberate owner; it does not decide
// which owner.

export const TMP_DIR = process.env.TEMP || process.env.TMP || os.tmpdir();
