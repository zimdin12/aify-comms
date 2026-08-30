#!/usr/bin/env node
// The skills check compares BOTH skill trees, not just the Claude one.
//
// The repo keeps two byte-identical skill trees — `.claude/skills` and `.agents/skills`, held equal by
// test_skill_mirror_parity.py — and install.sh copies the second to $CODEX_HOME/skills and to
// $HERMES_HOME/skills/autonomous-ai-agents. `skills-installed` walked only the first, so a Codex or
// hermes agent could run a stale skill for ever with the check reporting green.
//
// That matters more than most stale copies: a SKILL.md is loaded into every agent's context on EVERY
// turn, so a stale one is wrong instructions paid for continuously rather than a doc somebody finds
// later. It is the exact failure this check exists for, on half the surface it was covering.
//
// Measured 2026-08-25 on this host: .claude/skills 17 files vs ~/.claude/skills — 0 missing, 0
// differing; .agents/skills 17 files vs ~/.codex/skills — 0 missing, 0 differing. Both were in sync,
// and only one was being verified.
//
// THE SKIP RULE IS THE INTERESTING PART. A destination is compared only when its runtime HOME exists:
// no ~/.codex means Codex is not installed here and nothing can be stale. But if the home exists and
// the skills under it do not, that is a missing install and is reported. Absence of the runtime is a
// skip; absence of the skills is a finding. Getting that backwards would make the check green on every
// machine that has never installed anything — the false green this repo has fixed twice before.

import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { skillDestinations, skillsInstallVerdict } from "../doctor-predicates.js";

const REPO = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");

// ── the repo really does keep two trees ────────────────────────────────────────────────────────
{
  // The control. If either tree vanished, everything below would pass while covering nothing.
  assert.ok(existsSync(join(REPO, ".claude", "skills", "aify-comms")), "the Claude skill tree is gone");
  assert.ok(existsSync(join(REPO, ".agents", "skills", "aify-comms")), "the Codex mirror is gone");
}

// ── the resolver, CALLED rather than grepped ───────────────────────────────────────────────────
//
// A first version of this asserted that doctor.js CONTAINS the strings '.agents', 'CODEX_HOME' and
// 'autonomous-ai-agents'. I reverted the check to Claude-only, left the strings behind in a comment,
// and the test still passed. A source pin proves a line was written; it does not prove the line
// runs. Every dependency is injected, so these describe hosts without creating a directory.
{
  const norm = (x) => String(x).split(String.fromCharCode(92)).join('/');
  const has = (...frags) => (x) => frags.some((f) => norm(x).includes(f));
  const labels = (o) => skillDestinations(o).map((d) => norm(d.label));

  // A host with both runtimes installed gets all three destinations.
  assert.deepEqual(
    labels({ home: '/h', env: {}, exists: has('/h/.codex', '/h/.hermes') }),
    ['~/.claude/skills', '/h/.codex/skills', '/h/.hermes/skills/autonomous-ai-agents'],
    'the Codex and hermes mirrors are no longer compared against anything',
  );

  // A host with neither gets only the Claude tree. Absence of the RUNTIME is a skip.
  assert.deepEqual(
    labels({ home: '/h', env: {}, exists: has('nothing-exists') }),
    ['~/.claude/skills'],
    'a machine with no Codex or hermes install is asked to have their skills anyway',
  );

  // One runtime present, one absent — the common case, and the one a fixed list gets wrong.
  assert.deepEqual(
    labels({ home: '/h', env: {}, exists: has('/h/.codex') }),
    ['~/.claude/skills', '/h/.codex/skills'],
  );

  // The homes are ENV-resolved, not assumed: this host's real HERMES_HOME is under AppData, not
  // ~/.hermes, and a hardcoded path would have compared nothing while reporting green.
  assert.deepEqual(
    labels({ home: '/h', env: { HERMES_HOME: '/opt/hermes' }, exists: has('/opt/hermes') }),
    ['~/.claude/skills', '/opt/hermes/skills/autonomous-ai-agents'],
    'HERMES_HOME is ignored, so a non-default install is never checked',
  );
  assert.deepEqual(
    labels({ home: '/h', env: { CODEX_HOME: '/custom/codex' }, exists: has('/custom/codex') }),
    ['~/.claude/skills', '/custom/codex/skills'],
    'CODEX_HOME is ignored',
  );

  // The Claude tree is unconditional: it is the one install.sh always writes.
  for (const env of [{}, { CODEX_HOME: '/x' }, { HERMES_HOME: '/y' }]) {
    assert.ok(
      labels({ home: '/h', env, exists: () => false }).includes('~/.claude/skills'),
      'the Claude skill tree stopped being checked',
    );
  }

  // CALLED WITH NO ARGUMENTS, which every assertion above skips. Injecting home, env and exists
  // means the real defaults are never executed — and that is exactly what hid two runtime bugs when
  // this function moved: `home = homedir()` had been mangled to `home = home` (a TDZ error) and
  // `existsSync` was never imported. Both parse cleanly; both throw on the first real call. The
  // bridge suite caught them, this file did not, so the default path is exercised here now.
  {
    const real = skillDestinations();
    assert.ok(Array.isArray(real) && real.length >= 1, 'the default path throws or returns nothing');
    assert.equal(real[0].label, '~/.claude/skills');
    for (const d of real) {
      assert.ok(d.dst && d.label && Array.isArray(d.src), `a destination is malformed: ${d.label}`);
    }
  }

  // And each destination names the SOURCE tree it must match, or the check would compare the
  // Claude tree against the Codex destination and report every file as differing.
  const both = skillDestinations({ home: '/h', env: {}, exists: has('/h/.codex') });
  assert.deepEqual(both[0].src, ['.claude', 'skills']);
  assert.deepEqual(both[1].src, ['.agents', 'skills'], 'the Codex mirror reads the wrong source tree');
}
// ── install.sh writes exactly the destinations the check reads ─────────────────────────────────
{
  // The pairing. A check that compared a directory install.sh never writes would be green for ever
  // and prove nothing, which is worse than not checking.
  const installer = readFileSync(join(REPO, "install.sh"), "utf8");
  // MATCHED ON THE TREE, not on a skill inside it. These named `.agents/skills/aify-comms`, which
  // stopped existing when install.sh went from copying two listed skills to walking the directory --
  // a change made because a third skill was added and silently not installed. A gate that pins the
  // spelling of a fix goes red on the next correct change; the property is that both mirrors are
  // still written.
  assert.match(installer, /install_skill_tree "\$SCRIPT_DIR\/\.agents\/skills"/,
    "install.sh no longer copies the Codex/Hermes mirror");
  assert.match(installer, /install_skill_tree "\$SCRIPT_DIR\/\.claude\/skills"/,
    "install.sh no longer copies the Claude tree");
  assert.match(installer, /skills\/autonomous-ai-agents/, "the hermes destination moved");
  assert.match(installer, /CODEX_HOME.*\/skills/, "the codex destination moved");
}

// ── the verdict still distinguishes the three outcomes ─────────────────────────────────────────
{
  const clean = skillsInstallVerdict({ missing: [], differing: [], total: 17, dest: "a, b" });
  assert.equal(clean.ok, true);
  assert.match(clean.detail, /17/, "the file count stopped being reported");
  assert.match(clean.detail, /a, b/, "the verdict no longer names WHICH mirrors it compared");

  const stale = skillsInstallVerdict({ missing: [], differing: ["x/SKILL.md"], total: 17, dest: "a" });
  assert.equal(stale.ok, false);
  assert.equal(stale.code, "stale");

  // Nothing installed anywhere is NOT ok. A zero total once read as a pass in this family of checks;
  // it means no evidence, and no evidence must never look like agreement.
  const none = skillsInstallVerdict({ missing: [], differing: [], total: 0, dest: "" });
  assert.equal(none.ok, false, "an empty comparison reported success");
  assert.equal(none.code, "not-installed");
}

// ── a stale file names the mirror it is stale in ───────────────────────────────────────────────
{
  // Two mirrors means "SKILL.md differs" is ambiguous. The check prefixes each relative path with its
  // destination label so the operator knows which install.sh invocation to re-run.
  const v = skillsInstallVerdict({
    missing: [], differing: ["~/.codex/skills:aify-comms/SKILL.md"], total: 34, dest: "x",
  });
  assert.match(v.detail, /\.codex/, "a stale file no longer says which mirror it came from");
}

console.log("doctor-skills-both-mirrors.test.js: all assertions passed");
