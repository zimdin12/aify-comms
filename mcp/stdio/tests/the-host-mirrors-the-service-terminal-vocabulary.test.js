#!/usr/bin/env node
// aify-env's end-of-terminal vocabulary must be the SERVICE's, measured rather than claimed.
//
// EXTERNAL REVIEW, Round 8 M5. `lib/plugins/aify-comms/terminal-controls.mjs` declares
// `TERMINAL_ENDED` and its own comment says it "MIRRORS `_TERMINAL_END_STATUSES` IN THE SERVICE".
// It did not: the host read `["stopped", "failed", "exited", "killed"]` while the service says
// `{stopped, failed, lost, ended, completed, cancelled}`.
//
// WHAT THAT COSTS. A terminal the service has declared `lost`, `ended`, `completed` or `cancelled`
// reads as LIVE on the host, so its worker is never recognised as unaddressable and runs until
// something else notices -- which, for the case this rule exists for, was two hours. And `exited`
// and `killed` are names the service never sends, so two of the four entries could never match.
//
// LATENT WHEN FOUND, and fixed anyway: no current writer produces those four. The value of a mirror
// is precisely that it holds before the day something starts using a name, and a mirror nobody
// measures is a comment.
//
// THIS TEST LIVES HERE because the vocabulary lives here -- the service owns it, and a check that
// read only the host's copy could not tell agreement from two identical mistakes. It is the same
// arrangement as `doctor-sources`: one side declares, the other is measured against it.
//
// IT FAILS RATHER THAN SKIPS when the aify-env checkout is missing, which is this repo's rule for a
// cross-repo proof: "unverified" must not read as green. `env-client-against-real-aify-env` and
// `delegated-terminal-against-real-aify-env` both behave this way and say why.

import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "..", "..", "..");

/** The service's own list, read from the module that owns the vocabulary. */
function serviceEndStatuses() {
  const source = readFileSync(
    path.join(REPO, "service", "api_core", "terminal_status.py"), "utf8");
  const match = /_TERMINAL_END_STATUSES\s*=\s*\{([^}]*)\}/.exec(source);
  assert.ok(match, "the service no longer declares _TERMINAL_END_STATUSES as a set literal — this "
    + "test reads it textually, so a change of shape needs a change here rather than a silent pass");
  return new Set([...match[1].matchAll(/"([a-z-]+)"/g)].map((m) => m[1]));
}

/** aify-env's copy. */
function hostEndStatuses() {
  // The sibling checkout, beside this one or under the operator's projects directory. Named
  // candidates rather than a search: a walk that found some OTHER `terminal-controls.mjs` would
  // compare the wrong file and pass.
  const candidates = [
    path.join(REPO, "..", "aify-env"),
    path.join(os.homedir(), "projects", "aify-env"),
  ];
  const root = candidates.find((dir) => existsSync(path.join(dir, "package.json")));
  assert.ok(root,
    "the aify-env checkout was not found, so this cross-repo agreement was NOT verified. That is "
    + "not a pass: a vocabulary mirror that nobody measured is exactly the state this test exists "
    + `to end. Looked in: ${candidates.join(", ")}`);
  const source = readFileSync(
    path.join(root, "lib", "plugins", "aify-comms", "terminal-controls.mjs"), "utf8");
  const match = /export const TERMINAL_ENDED = Object\.freeze\(\[([\s\S]*?)\]\)/.exec(source);
  assert.ok(match, "aify-env no longer declares TERMINAL_ENDED as a frozen array literal");
  return new Set([...match[1].matchAll(/"([a-z-]+)"/g)].map((m) => m[1]));
}

test("both lists were actually read, so an empty comparison cannot pass", () => {
  // POSITIVE CONTROL. Every assertion below compares two sets, and two EMPTY sets agree perfectly --
  // which is what a broken regex on either side produces.
  const service = serviceEndStatuses();
  const host = hostEndStatuses();
  assert.ok(service.size >= 4, `only ${service.size} service end status(es) read`);
  assert.ok(host.size >= 4, `only ${host.size} host end status(es) read`);
  assert.ok(service.has("stopped"), "the service list does not contain `stopped`; the read is wrong");
  assert.ok(host.has("stopped"), "the host list does not contain `stopped`; the read is wrong");
});

test("THE HOST RECOGNISES EVERY END STATUS THE SERVICE CAN SEND", () => {
  const service = serviceEndStatuses();
  const host = hostEndStatuses();
  const missing = [...service].filter((name) => !host.has(name)).sort();
  assert.deepEqual(missing, [],
    `aify-env does not recognise ${missing.join(", ")} as an ended terminal. The service can put a `
    + "terminal into any of these, and one the host does not recognise reads as LIVE -- so its "
    + "worker is never seen as unaddressable and runs on. That is the two-hour orphan this rule was "
    + "written for, arriving through a name rather than a bug.");
});

test("AND IT INVENTS NONE, because a name the service never sends can never match", () => {
  const service = serviceEndStatuses();
  const host = hostEndStatuses();
  const invented = [...host].filter((name) => !service.has(name)).sort();
  assert.deepEqual(invented, [],
    `aify-env lists ${invented.join(", ")}, which the service does not send. Dead entries are not `
    + "harmless: they make a short list look complete, which is how `exited` and `killed` sat there "
    + "while four real statuses were missing.");
});
