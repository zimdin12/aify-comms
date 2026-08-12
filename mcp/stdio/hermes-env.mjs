// Environment identity for the hermes managed host: which command runs it, which machine it is on, and
// which runtime name it reports.
//
// A NEUTRAL owner, created in v0.5.4 because these three constants have readers on BOTH sides of the
// gateway extraction — eight host-side functions read them, and so does the gateway. Leaving them in
// hermes-managed-host.js would force the new gateway module to import UPWARD from the file it is draining,
// which is the dependency inversion the Python `test_leaves_do_not_import_the_carrier` gate exists to stop.
// Moving them into the gateway would be worse in a quieter way: the host would then import its own machine
// id from a module named after gateways.
//
// A constant with readers on both sides of a boundary belongs to neither of them. Same call as
// `api_core/liveness.py` owning TURN_BUSY_BACKSTOP_SECONDS on the Python side.
//
// Note what is NOT here: the gateway's own timeouts. Those have a single reader each, inside the gateway,
// so they follow it. This module is for shared identity, not for every constant that exists.

import fs from "fs";
import os from "os";
import path from "path";

import { defaultMachineId } from "./runtimes.js";

export const HERMES_CMD = String(process.env.AIFY_HERMES_COMMAND || "hermes").trim() || "hermes";
export const MACHINE_ID = defaultMachineId();
export const RUNTIME = "hermes";

// v0.5.4: `TMP_DIR` arrived with the active-session slice. Seven host functions read it and one session
// function does, and it is `process.env.TEMP || process.env.TMP || os.tmpdir()` — environment identity with
// no session semantics, which is exactly what this module is for. Compare `ATTACH_WAIT_MS`, which has the
// same two-sided readership and went the OTHER way, into the session module, because attach timing IS a
// session concept. Two-sided readership decides that a constant needs a deliberate owner; it does not decide
// which owner.

export const TMP_DIR = process.env.TEMP || process.env.TMP || os.tmpdir();


// v0.5.4: `resolveHermesPython` came here from `hermes-active-session.mjs`, one slice after arriving there.
//
// It went to the session module first because `ensureStableSession` is its only caller and a transitive
// closure therefore includes it. The reviewer asked the right question — are these members session IDENTITY,
// or merely things a session touches — and the answer for this one is neither ambiguous nor flattering to my
// first placement: it takes the hermes command and looks for the python interpreter beside it. That is
// environment resolution, the same subject as `HERMES_CMD` two declarations up, and it says nothing about
// which session an agent is bound to.
//
// A closure tells you what a function NEEDS. It does not tell you what belongs in a module with it — the
// same rule that kept `_row_capabilities` out of `execution_mode.py` on the Python side.

export function resolveHermesPython(hermesCmd = HERMES_CMD) {
  const cmd = String(hermesCmd || "").trim();
  try {
    if (cmd && (cmd.includes("/") || cmd.includes("\\"))) {
      const dir = path.dirname(cmd);
      const candidates = [
        path.join(dir, "python.exe"),
        path.join(dir, "python3.exe"),
        path.join(dir, "python"),
        path.join(dir, "python3"),
      ];
      for (const c of candidates) {
        try {
          if (fs.existsSync(c)) return c;
        } catch {
          /* ignore */
        }
      }
    }
  } catch {
    /* ignore */
  }
  return process.platform === "win32" ? "python.exe" : "python3";
}
