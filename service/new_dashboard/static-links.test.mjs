// The static links and the Help card's install snippet, tested by CALLING them.
//
// Both write the ORIGIN the operator actually opened the dashboard on into the page. The install
// snippet used to hard-code one machine's LAN IP, which was wrong for every other reader — so the value
// being live rather than baked in is the property worth guarding, and it is the kind that fails
// silently: the command is copyable, looks right, and points somewhere the reader cannot reach.

import assert from "node:assert/strict";
import test from "node:test";

import { setApiBase } from "./api-client.mjs";
import { renderInstallSnippet, updateStaticLinks } from "./static-links.mjs";

/** Point api-client at a known origin and install a DOM that records what is written. */
function withOrigin(origin, { missing = false } = {}, run) {
  const els = {
    "help-install-cmd": { textContent: "" },
    "legacy-dashboard-link": { href: "" },
  };
  const hadDoc = "document" in globalThis;
  const prev = globalThis.document;
  globalThis.document = { getElementById: (id) => (missing ? null : els[id] || null) };
  setApiBase(`${origin}/api/v1`, origin);
  try {
    return run(els);
  } finally {
    if (hadDoc) globalThis.document = prev; else delete globalThis.document;
  }
}

test("the install snippet carries the LIVE origin, not a baked-in host", () => {
  // The defect this replaced: a hard-coded LAN IP that was correct for exactly one reader. Two
  // different origins must produce two different commands.
  withOrigin("http://10.0.0.5:8800", {}, (els) => {
    renderInstallSnippet();
    assert.match(els["help-install-cmd"].textContent, /10\.0\.0\.5:8800/);
  });
  withOrigin("https://comms.example", {}, (els) => {
    renderInstallSnippet();
    assert.match(els["help-install-cmd"].textContent, /comms\.example/);
    assert.doesNotMatch(els["help-install-cmd"].textContent, /10\.0\.0\.5/, "no stale host may survive");
  });
});

test("the snippet is a complete, runnable install command", () => {
  // It exists to be copied. A missing client flag or hook argument produces a command that runs and
  // installs the wrong thing, which is worse than one that fails.
  withOrigin("http://host:8800", {}, (els) => {
    renderInstallSnippet();
    const cmd = els["help-install-cmd"].textContent;
    assert.match(cmd, /bash install\.sh/);
    assert.match(cmd, /--client claude/);
    assert.match(cmd, /--with-hook/);
  });
});

test("updateStaticLinks points the legacy dashboard link at the same origin", () => {
  withOrigin("http://host:8800", {}, (els) => {
    updateStaticLinks();
    assert.equal(els["legacy-dashboard-link"].href, "http://host:8800/api/v1/dashboard");
  });
});

test("both are silent no-ops when their elements are absent", () => {
  // `if (el)` / `if (legacy)`. Both run during boot and on pages that do not contain these nodes;
  // throwing would abort the init sequence that follows them.
  withOrigin("http://host:8800", { missing: true }, () => {
    assert.doesNotThrow(() => renderInstallSnippet());
    assert.doesNotThrow(() => updateStaticLinks());
  });
});
