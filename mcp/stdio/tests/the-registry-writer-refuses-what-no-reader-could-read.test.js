// The writer could produce a file that uninstalls every service on the host, including other people's.
//
// `upsertService` already REFUSES to rewrite a registry it cannot parse, and its comment says why:
// replacing it "would uninstall that service from every launcher installed afterwards -- silently,
// and at the moment somebody reinstalls something unrelated." The same harm arrived through the other
// door. The reader refuses the WHOLE file when ONE entry is invalid, so writing a malformed
// aify-comms entry took every other service down with it.
//
// MEASURED before the guard: seed a registry with `other-service`, then write an aify-comms entry
// whose mcp server has no `name`. The write reports ok:true, and the reader then returns
// ok:false with zero services -- `other-service` is gone from every launcher that reads the file.
//
// REACHABILITY, stated rather than implied: `register-service-cli.mjs` builds the entry from
// constants and `ENDPOINT_ENV_NAMES`, so today's only caller cannot produce these shapes. This is a
// guard on the join, not a live bug. It is worth having because the entry is assembled in one repo
// and judged in another, and the two only meet on a user's machine at install time.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { parseRegistry, REGISTRY_VERSION } from "aify-wrapper/lib/registry.mjs";

import { upsertService } from "../service-registry.mjs";

const GOOD = {
  endpoint: "http://127.0.0.1:8800",
  endpointEnv: ["AIFY_SERVER_URL"],
  mcp: [{ name: "aify-comms", command: "node", args: ["server.js"] }],
};

/** A host where somebody else's service is already registered. */
function seeded() {
  const seed = upsertService("", "other-service", {
    endpoint: "http://127.0.0.1:9000",
    endpointEnv: ["OTHER_URL"],
    mcp: [{ name: "other", command: "node", args: ["o.js"] }],
  });
  assert.equal(seed.ok, true, "the seed itself was refused; the fixture is wrong");
  return seed.text;
}

test("CONTROL: a good entry is written, and both services survive", () => {
  // Anti-vacuity. A guard that refused everything would pass every test below while making the
  // installer unable to register anything at all.
  const out = upsertService(seeded(), "aify-comms", GOOD);
  assert.equal(out.ok, true, out.errors.join("; "));
  const back = parseRegistry(out.text);
  assert.equal(back.ok, true, back.errors?.join("; "));
  assert.deepEqual(Object.keys(back.registry.services).sort(), ["aify-comms", "other-service"]);
});

// Derived from the reader's OWN rules rather than from shapes that occurred to me: every field it
// validates gets a case, so a rule the reader gains is a rule this notices is untested.
const REFUSED = {
  "an mcp server with no name": { ...GOOD, mcp: [{ command: "node" }] },
  "an mcp server with no command": { ...GOOD, mcp: [{ name: "a" }] },
  "mcp args that are not strings": { ...GOOD, mcp: [{ name: "a", command: "node", args: [7] }] },
  "endpointEnv holding a non-string": { ...GOOD, endpointEnv: [7] },
};

for (const [label, entry] of Object.entries(REFUSED)) {
  test(`the writer refuses ${label}`, () => {
    const out = upsertService(seeded(), "aify-comms", entry);
    assert.equal(out.ok, false, `${label} was written; the reader would then refuse the whole file`);
    assert.match(out.errors.join(" "), /no launcher could read/);
  });

  test(`refusing ${label} leaves the other service intact`, () => {
    // The consequence, not the verdict. A refusal that still wrote the file would pass the test above.
    const before = seeded();
    const out = upsertService(before, "aify-comms", entry);
    assert.equal(out.text, undefined, "a refused write still produced text for the caller to save");
    assert.deepEqual(Object.keys(parseRegistry(before).registry.services), ["other-service"]);
  });
}

test("a THIRD PARTY's damaged entry does not block our registration", () => {
  // The failure the narrow validation avoids. Judging the merged file would make somebody else's bad
  // entry uninstall ours -- worse than the harm being prevented, and much harder to explain.
  const damaged = JSON.stringify({
    version: REGISTRY_VERSION,
    services: { "other-service": { endpoint: "http://x", mcp: [{ command: "node" }] } },
  });
  assert.equal(parseRegistry(damaged).ok, false, "the fixture is not actually damaged");
  const out = upsertService(damaged, "aify-comms", GOOD);
  assert.equal(out.ok, true, `our own correct entry was refused: ${out.errors.join("; ")}`);
});

test("the reader is the only authority — the writer states no rule of its own", () => {
  // Two copies of one rule set agree until one is fixed. If a validation list ever appears here, this
  // fails and names the file, because the next reader will not know which copy is current.
  const source = readSource();
  assert.doesNotMatch(source, /must be a non-empty string/,
    "service-registry.mjs has started restating the reader's rules");
  assert.match(source, /from "aify-wrapper\/lib\/registry\.mjs"/,
    "the writer no longer validates through the reader");
});

function readSource() {
  return readFileSync(new URL("../service-registry.mjs", import.meta.url), "utf8");
}
