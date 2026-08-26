/**
 * "Where do managed spawns run" is parsed in ONE place, and a file that is not the launcher says so.
 *
 * THE DUPLICATION. Three implementations answered this: `doctor-predicates.js`, `doctor.js` (which
 * re-ran both regexes to decide whether to probe aify-env), and `scripts/installed-delegation.sh`.
 * `doctor.js` states the principle four lines below the function that broke it -- "a second
 * implementation of one question does not agree for free, it agrees until one of them is fixed" --
 * written about the four checks that left this tool for exactly that reason.
 *
 * THE ASYMMETRY IS THE HARM. The copy in doctor.js decided whether to PROBE; the copy in the
 * predicate decided the VERDICT. Fix the launcher's shape and update only doctor.js and it probes
 * aify-env, gets a real answer, and hands it to a verdict whose own stale regex returns
 * `pre-contract` -- ok: TRUE. A false green assembled from a probe it paid for and threw away.
 *
 * MEASURED against the operator's installed launcher on 2026-08-27: `export
 * AIFY_COMMS_DELEGATE_SPAWNS="1"` and `export AIFY_ENV_ENDPOINT="http://127.0.0.1:8802"`, four
 * `export AIFY_` lines, shebang `#!/bin/bash`. The .cmd beside it is six lines of `@echo off` and
 * carries none of them.
 */
import assert from "node:assert";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { launcherDelegation, spawnDelegationVerdict } from "../doctor-predicates.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const STDIO = path.dirname(HERE);

{

  // The shape install.sh:1496 renders, kept verbatim so this test fails if the producer moves.
  const LAUNCHER = [
    "#!/bin/bash",
    'export AIFY_SERVER_URL="http://127.0.0.1:8800"',
    'export AIFY_COMMS_DELEGATE_SPAWNS="1"',
    'export AIFY_ENV_ENDPOINT="http://127.0.0.1:8802"',
    "",
  ].join("\n");

  // The real .cmd shim, verbatim in shape: it execs the bash file and carries no settings at all.
  const CMD_SHIM = ['@echo off', 'setlocal', '"bash.exe" "%~dp0aify-comms" %*', ""].join("\n");

  // ---- the parser ---------------------------------------------------------
  {
    const p = launcherDelegation(LAUNCHER);
    assert.strictEqual(p.isLauncher, true);
    assert.strictEqual(p.present, true);
    assert.strictEqual(p.on, true);
    assert.strictEqual(p.endpoint, "http://127.0.0.1:8802");
  }
  {
    // Delegation explicitly OFF is a real, supported answer and must not read as absent -- `local`
    // and `pre-contract` are different verdicts with different fixes.
    const p = launcherDelegation('#!/bin/bash\nexport AIFY_COMMS_DELEGATE_SPAWNS=""\n');
    assert.strictEqual(p.present, true, "an empty setting is PRESENT and off, not missing");
    assert.strictEqual(p.on, false);
  }
  {
    const p = launcherDelegation("#!/bin/bash\necho old launcher\n");
    assert.strictEqual(p.isLauncher, true);
    assert.strictEqual(p.present, false, "a genuinely pre-contract launcher is still a launcher");
  }
  for (const junk of [null, undefined, 0, [], {}, ""]) {
    assert.strictEqual(launcherDelegation(junk).isLauncher, false, `threw or lied on ${JSON.stringify(junk)}`);
  }

  // ---- the false green this closes ----------------------------------------
  {
    // THE CASE. Reading the .cmd shim yielded "no delegation line", which was indistinguishable from
    // an old launcher and reported ok:true -- "the bridge hosts managed spawns itself". On a host
    // where delegation is ON that sends an operator chasing spawn failures in the wrong tier, since
    // delegation makes aify-env REQUIRED and a down aify-env presents as spawns failing with no cause.
    const v = spawnDelegationVerdict({ launcherText: CMD_SHIM, endpointAnswered: null });
    assert.strictEqual(v.ok, false, "a non-launcher still produced a passing verdict");
    assert.strictEqual(v.code, "unknown-all");
    assert.ok(!/hosts managed spawns itself/.test(v.detail), "it still claims to know where spawns run");
  }
  {
    // And the honest cases still behave. A real pre-contract launcher stays a PASS -- the control must
    // not turn legitimate old installs red, which would be the opposite failure.
    const v = spawnDelegationVerdict({ launcherText: "#!/bin/bash\necho old\n", endpointAnswered: null });
    assert.strictEqual(v.ok, true);
    assert.strictEqual(v.code, "pre-contract");
  }
  {
    const v = spawnDelegationVerdict({ launcherText: LAUNCHER, endpointAnswered: true });
    assert.strictEqual(v.code, "delegated");
    assert.ok(v.detail.includes("http://127.0.0.1:8802"), "the verdict lost the endpoint");
  }
  {
    const v = spawnDelegationVerdict({ launcherText: LAUNCHER, endpointAnswered: false });
    assert.strictEqual(v.ok, false);
    assert.strictEqual(v.code, "unreachable");
  }
  {
    const v = spawnDelegationVerdict({ launcherText: null, endpointAnswered: null });
    assert.strictEqual(v.code, "unknown-all");
  }

  // ---- the gate: one implementation, and the shell one agrees -------------
  const PATTERN = 'AIFY_COMMS_DELEGATE_SPAWNS="';
  const jsFiles = fs.readdirSync(STDIO).filter((f) => /\.(js|mjs)$/.test(f));
  assert.ok(jsFiles.length > 20, `positive control: only ${jsFiles.length} bridge files found`);
  const carriers = jsFiles.filter((f) =>
    fs.readFileSync(path.join(STDIO, f), "utf8").includes("^export " + PATTERN));
  assert.deepStrictEqual(carriers, ["doctor-predicates.js"],
    `the delegation regex must live in exactly one bridge file; found: ${carriers.join(", ")}`);

  // The shell answer to the same question, which cannot import the JS one. A gate is the only thing
  // that keeps them spelling the same pattern -- and `scripts/installed-endpoint.sh` exists because
  // two copies of one endpoint regex both silently stopped matching.
  const sh = fs.readFileSync(path.join(STDIO, "..", "..", "scripts", "installed-delegation.sh"), "utf8");
  assert.ok(sh.includes("^export " + PATTERN), "the shell parser no longer spells the same pattern");

  console.log("where-spawns-run-has-one-parser.test.js: all assertions passed");
}
