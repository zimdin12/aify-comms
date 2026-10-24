// Imported FIRST: TEMP/TMP/TMPDIR and HOME/USERPROFILE become private paths before product imports.
//
// `tests/run-all.mjs` already gives every file it runs a private temp root. A file run DIRECTLY -- `node --test
// tests/x.test.js`, which is how a failure is chased and how every mutation run works -- had none, and the
// bridge's marker writers default to the real %TEMP%. Measured 2026-09-15, running each hermes test alone
// with TEMP pointed at an empty directory: five files left `aify-hermes-*` markers at its root (a gateway URL,
// a port, a loop-ready file, daemon pids, gateway keys) under ids like `sc-hermes` and `agent-a`. In the real
// %TEMP% those sit beside the live fleet's markers, where a stale port marker makes a real agent's port read
// as another agent's claim.
//
// It must be the first import: module constants such as a marker directory are read when their module loads,
// and ES modules load in import order.

import { mkdirSync, mkdtempSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";

const root = mkdtempSync(path.join(os.tmpdir(), "aify-sealed-temp-"));
// Gateway discovery is per-user, not per-TEMP. Seal both home aliases before imports too.
const home = path.join(root, "home");
mkdirSync(home);
process.env.HOME = home;
process.env.USERPROFILE = home;
process.env.HERMES_HOME = path.join(home, ".hermes");
process.env.TEMP = root;
process.env.TMP = root;
process.env.TMPDIR = root;
process.on("exit", () => {
  try { rmSync(root, { recursive: true, force: true }); } catch { /* best effort: a locked file must not fail a test */ }
});

export const SEALED_TEMP = root;
