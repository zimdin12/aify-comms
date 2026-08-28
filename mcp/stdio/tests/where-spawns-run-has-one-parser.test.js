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

// ---- the gate above compares SPELLING, which is not agreement -----------------------------------
//
// Both files spelled that pattern identically and answered differently. `env-client.mjs` -- the code
// that actually decides whether a spawn is delegated -- accepts only `1|true|yes|on`, while the
// reporters treated ANY non-blank value as on. Measured 2026-08-28 over eleven spellings, five
// disagreed, including `"0"`, `"false"` and `"off"`: an operator who turned delegation off the obvious
// way got local spawns (right) and a doctor reporting `delegated` that went on to probe aify-env and
// fail `unreachable` for a setting not in effect.
//
// A shared predicate fixes the two JS readers. The shell one cannot import it, so it is held to the
// same TABLE OF VERDICTS instead of the same regex text. That is the difference between checking that
// two things look alike and checking that they answer alike.
{
  const { execFileSync } = await import("node:child_process");
  const os = await import("node:os");
  const { AFFIRMATIVE, delegationOptedIn } = await import("../delegation-setting.mjs");
  const { isEnabled } = await import("../env-client.mjs");

  const ENDPOINT = "http://127.0.0.1:8899";
  //: Every spelling worth pinning: the four that mean yes, the ways people write no, and a value
  //: nobody declared. Casing and surrounding space are included because a launcher is hand-editable.
  const SPELLINGS = ["1", "true", "yes", "on", "ON", " on ", "", " ", "0", "false", "no", "off", "maybe"];

  const launcherText = (value) =>
    `#!/bin/bash
export AIFY_COMMS_DELEGATE_SPAWNS="${value}"
export AIFY_ENV_ENDPOINT="${ENDPOINT}"
`;

  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "delegation-"));
  const shellSaysOn = (value) => {
    fs.writeFileSync(path.join(tmp, "aify-comms"), launcherText(value));
    try {
      execFileSync("bash", [path.join(STDIO, "..", "..", "scripts", "installed-delegation.sh"), tmp],
        { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] });
      return true;
    } catch {
      return false;
    }
  };

  const disagreements = [];
  let onCount = 0;
  let offCount = 0;
  for (const value of SPELLINGS) {
    const decider = isEnabled({ AIFY_COMMS_DELEGATE_SPAWNS: value, AIFY_ENV_ENDPOINT: ENDPOINT });
    const reporter = launcherDelegation(launcherText(value)).on;
    const shell = shellSaysOn(value);
    if (decider) onCount += 1; else offCount += 1;
    if (decider !== reporter || decider !== shell) {
      disagreements.push(`${JSON.stringify(value)}: decider=${decider} doctor=${reporter} shell=${shell}`);
    }
  }
  fs.rmSync(tmp, { recursive: true, force: true });

  // CONTROLS. Three readers that all said "on" to everything would agree perfectly and mean nothing,
  // and so would three that said "off". The table has to exercise both answers to be a comparison.
  assert.ok(onCount >= 4, `only ${onCount} spellings read as ON; the table is not exercising yes`);
  assert.ok(offCount >= 4, `only ${offCount} spellings read as OFF; the table is not exercising no`);
  assert.deepStrictEqual(disagreements, [],
    "the readers of AIFY_COMMS_DELEGATE_SPAWNS do not agree on what it means");

  // The predicate itself, so a reader that stops importing it is not silently re-deciding.
  assert.strictEqual(delegationOptedIn("0"), false);
  assert.strictEqual(delegationOptedIn(" ON "), true);
  assert.strictEqual(delegationOptedIn(undefined), false);

  // The shell's word list is DERIVED from the exported one, not retyped here. A fifth word added to
  // the predicate and not to the shell reader is a disagreement that only shows up on whichever host
  // types it -- and a test that hardcoded `1|true|yes|on` would agree with itself while the two
  // files diverged. The launcher's own banner is held to the same list from the python side.
  const shellSource = fs.readFileSync(path.join(STDIO, "..", "..", "scripts", "installed-delegation.sh"), "utf8");
  const shellCase = /^\s*([a-z0-9|]+)\)\s*;;/m.exec(shellSource);
  assert.ok(shellCase, "the shell reader no longer decides with a case list");
  assert.deepStrictEqual(shellCase[1].split("|"), AFFIRMATIVE,
    "the shell reader accepts a different set of words than the predicate does");

  console.log("where-spawns-run-has-one-parser.test.js: verdict table agrees across all three readers");
}
