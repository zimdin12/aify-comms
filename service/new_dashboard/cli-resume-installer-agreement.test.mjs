// Every runtime the drawer offers a resume command for must have a wrapper install.sh actually
// installs — and every one it withholds must be one install.sh actually withholds.
//
// `CLI_RESUME_RUNTIMES` decides whether the agent drawer renders a "Continue in CLI" command. Its
// own rule is that a command which does not work is WORSE than an honest "not supported", and the
// thing that makes a command work is a wrapper on the operator's PATH — installed by `install.sh`,
// 4,370 lines of shell no suite reads.
//
// So the set has two drift directions, and neither fails anything today:
//
//   * A runtime IN the set whose wrapper install.sh stopped installing → the drawer hands the
//     operator a command for a binary they do not have. Exactly the failure the module's comment
//     says is worse than silence.
//   * A runtime OUT of the set that install.sh has since ENABLED → the drawer silently withholds a
//     command that would now work. `pi` is the live instance: install.sh CONTAINS a full pi-aify
//     wrapper install path and disables it by default, so the exclusion is correct only for as long
//     as that stays true. Enabling it would leave this file the only thing that notices.
//
// The installer is read for VALUES — which wrappers it writes, which clients it refuses — not for
// where any of that is written, so it survives the shell being reorganised.

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { CLI_RESUME_RUNTIMES } from "./cli-resume.mjs";

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const INSTALLER = fs.readFileSync(path.join(REPO, "install.sh"), "utf-8");

// runtime -> the wrapper the drawer's command depends on existing.
const WRAPPER_FOR = {
  "claude-code": "claude-aify",
  hermes: "hermes-aify",
  codex: "codex-aify",
  pi: "pi-aify",
};

// `--client <name> is intentionally disabled` is the installer's own way of saying it refuses one.
const disabledClients = new Set(
  [...INSTALLER.matchAll(/--client\s+(\w+)\s+is intentionally disabled/g)].map((m) => m[1]),
);

test("the installer's disabled-client list is readable and non-empty", () => {
  // Anti-vacuity: if this regex stops matching, every assertion below turns into "nothing is
  // disabled" and the whole file silently agrees with anything.
  assert.ok(
    disabledClients.size > 0,
    "found no `--client X is intentionally disabled` lines in install.sh — the marker changed shape, "
      + "and without it this file cannot tell an enabled wrapper from a withheld one",
  );
  assert.ok(disabledClients.has("pi"), "pi is expected to be among them");
});

test("every runtime offered a resume command has a wrapper the installer installs", () => {
  for (const runtime of CLI_RESUME_RUNTIMES) {
    const wrapper = WRAPPER_FOR[runtime];
    assert.ok(wrapper, `no wrapper mapping for '${runtime}' — add one, or it cannot be checked here`);
    assert.ok(
      INSTALLER.includes(wrapper),
      `the drawer offers a resume command for '${runtime}' but install.sh never writes ${wrapper}`,
    );
  }
});

test("no runtime offered a resume command is one the installer refuses", () => {
  const offeredButDisabled = [...CLI_RESUME_RUNTIMES].filter((runtime) => disabledClients.has(runtime));
  assert.deepEqual(
    offeredButDisabled,
    [],
    "the drawer would hand the operator a command for a wrapper install.sh declines to install — "
      + "a command that does not work is worse than an honest 'not supported'",
  );
});

test("pi is excluded HERE only because the installer disables it THERE", () => {
  // The exclusion is a consequence, not a preference. If pi is ever enabled, this fails and points
  // at the set that needs updating rather than leaving the drawer quietly withholding a command
  // that would now work.
  assert.ok(!CLI_RESUME_RUNTIMES.has("pi"), "pi has no resident wake surface, so no command is offered");
  assert.ok(
    disabledClients.has("pi"),
    "install.sh no longer disables --client pi, so pi-aify may now exist — CLI_RESUME_RUNTIMES needs "
      + "revisiting, and so does the comment in cli-resume.mjs that cites this as the reason",
  );
});

test("the set is exactly the runtimes with an installed resident wrapper", () => {
  const installable = Object.entries(WRAPPER_FOR)
    .filter(([runtime, wrapper]) => INSTALLER.includes(wrapper) && !disabledClients.has(runtime))
    .map(([runtime]) => runtime)
    .sort();
  assert.deepEqual(
    [...CLI_RESUME_RUNTIMES].sort(),
    installable,
    "the offered set and the installable set have diverged — one of them is now wrong, and which "
      + "one is a decision rather than something this test can infer",
  );
});
