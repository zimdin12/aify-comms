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
//
// The MAPPING is a naming convention and cannot be derived; the POPULATION can, and a test below
// requires every template the wrapper package ships to appear here. A fifth wrapper arriving would
// otherwise be invisible to the "exactly" assertion, which only compares runtimes it already knows.
const WRAPPER_FOR = {
  "claude-code": "claude-aify",
  hermes: "hermes-aify",
  codex: "codex-aify",
  pi: "pi-aify",
};

// Which wrappers install.sh ACTUALLY WRITES, read from the render calls rather than from mentions.
//
// This was `INSTALLER.includes(wrapper)`, and a mention is not an installation. PROVEN by mutation
// 2026-08-28: deleting `render_wrapper_template "claude-aify.sh.in"` outright left all five cases in
// this file green, because six other lines name claude-aify -- among them a comment about the live
// incident where the wrapper went missing after an install that said "Installation complete". The
// gate whose whole subject is "the drawer must not offer a command for a wrapper you do not have"
// could not see the wrapper stop being written.
const RENDERED = new Set(
  [...INSTALLER.matchAll(/render_wrapper_template\s+"([\w-]+)\.sh\.in"/g)].map((m) => m[1]),
);

const TEMPLATE_DIR = path.join(REPO, "mcp", "stdio", "node_modules", "aify-wrapper", "wrappers");

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
      RENDERED.has(wrapper),
      `the drawer offers a resume command for '${runtime}' but install.sh never renders ${wrapper}`,
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
    .filter(([runtime, wrapper]) => RENDERED.has(wrapper) && !disabledClients.has(runtime))
    .map(([runtime]) => runtime)
    .sort();
  assert.deepEqual(
    [...CLI_RESUME_RUNTIMES].sort(),
    installable,
    "the offered set and the installable set have diverged — one of them is now wrong, and which "
      + "one is a decision rather than something this test can infer",
  );
});

test("the render-call scan finds wrappers, and does not find one that is absent", () => {
  // Controls in both directions. An empty set makes "no runtime is installable" agree with a drawer
  // that offers nothing, and a set matching anything makes every assertion vacuous.
  assert.ok(RENDERED.size >= 3, `only ${RENDERED.size} render call(s) found; the scan has drifted`);
  assert.ok(RENDERED.has("codex-aify"), "a wrapper install.sh demonstrably renders was not found");
  assert.ok(!RENDERED.has("nonexistent-aify"), "the scan matches names install.sh never renders");
});

test("every wrapper template the package ships is accounted for here", () => {
  // A list standing in for a population is a defect with a delay on it. The templates are the
  // population: a fifth one arriving must force a decision about whether the drawer offers it,
  // rather than sliding past an "exactly" check that only knows the four names typed above.
  const shipped = fs.readdirSync(TEMPLATE_DIR)
    .filter((name) => name.endsWith(".sh.in"))
    .map((name) => name.replace(/\.sh\.in$/, ""))
    .sort();
  assert.ok(shipped.length >= 4, `only ${shipped.length} templates found in ${TEMPLATE_DIR}`);
  const mapped = new Set(Object.values(WRAPPER_FOR));
  assert.deepEqual(
    shipped.filter((wrapper) => !mapped.has(wrapper)),
    [],
    "the wrapper package ships a template no runtime here maps to — decide whether the drawer "
      + "should offer a resume command for it, then add it to WRAPPER_FOR",
  );
});
