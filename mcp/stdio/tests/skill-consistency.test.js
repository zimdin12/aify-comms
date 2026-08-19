#!/usr/bin/env node
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const repo = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const agentRoot = join(repo, ".agents/skills");
const claudeRoot = join(repo, ".claude/skills");
const skills = ["aify-comms", "aify-comms-debug"];

function filesUnder(root) {
  return readdirSync(root, { withFileTypes: true }).flatMap((entry) => {
    const path = join(root, entry.name);
    return entry.isDirectory() ? filesUnder(path) : [path];
  });
}

for (const skill of skills) {
  const source = join(agentRoot, skill);
  const mirror = join(claudeRoot, skill);
  const sourceFiles = filesUnder(source).map((path) => relative(source, path)).sort();
  const mirrorFiles = filesUnder(mirror).map((path) => relative(mirror, path)).sort();
  assert.deepEqual(mirrorFiles, sourceFiles, `${skill} mirror file list drifted`);

  for (const relativePath of sourceFiles) {
    assert.equal(
      readFileSync(join(mirror, relativePath), "utf8"),
      readFileSync(join(source, relativePath), "utf8"),
      `${skill}/${relativePath} mirror drifted`,
    );
  }
}

const markdownFiles = skills.flatMap((skill) => filesUnder(join(agentRoot, skill)))
  .filter((path) => path.endsWith(".md"));
for (const path of markdownFiles) {
  const markdown = readFileSync(path, "utf8");
  for (const match of markdown.matchAll(/\[[^\]]*\]\(([^)]+)\)/g)) {
    const target = match[1].split("#", 1)[0];
    if (!target || /^[a-z]+:/i.test(target)) continue;
    const resolved = resolve(dirname(path), target);
    assert.ok(statSync(resolved).isFile(), `${relative(repo, path)} links missing ${target}`);
  }
}

// Reads every bridge source that registers tools, not just server.js. The dispatch group moved to
// `dispatch-tools.mjs` in v0.5.4 and more groups will follow; a fixed file list would silently shrink
// this check's coverage each time, and the failure mode — a skill documenting a tool that no longer
// exists — is one nobody hits until an agent tries to call it.
const stdioDir = join(repo, "mcp/stdio");
const toolSources = readdirSync(stdioDir)
  .filter((name) => /\.(js|mjs)$/.test(name))
  .map((name) => readFileSync(join(stdioDir, name), "utf8"))
  .filter((src) => /server\.tool\(/.test(src));
assert.ok(toolSources.length >= 2, "the tool-source scan should reach past server.js");
const registeredTools = new Set(
  toolSources.flatMap((src) =>
    // `\s*` after `(` absorbs the indentation a registration gains inside a `registerXTools` wrapper.
    [...src.matchAll(/server\.tool\(\s*["'](comms_[a-z0-9_]+)["']/g)].map((match) => match[1]),
  ),
);
assert.ok(registeredTools.size >= 25, `tool inventory looks wrong: ${registeredTools.size}`);
// FENCED BLOCKS ARE STRIPPED FIRST, and that is not tidiness. The inline-code scan below pairs
// single backticks; a ``` fence makes the pairing walk off, so everything after the first fenced
// example was effectively unchecked. Measured on `.agents/skills/aify-comms/SKILL.md` before this
// fix: 144 "spans" yielded 9 tool names out of the ~30 the file actually names — including the whole
// Tool Map. A gate that inspects a fraction of its input reports green exactly like one that
// inspects all of it.
const withoutFences = (text) => text.replace(/```[\s\S]*?```/g, "");

const documentedTools = new Set();
for (const path of markdownFiles) {
  const text = readFileSync(path, "utf8");
  const inlineCode = [...withoutFences(text).matchAll(/`([^`]+)`/g)].map((match) => match[1]).join("\n");
  for (const match of inlineCode.matchAll(/\b(comms_[a-z0-9_]+)\b/g)) {
    const tool = match[1];
    assert.ok(registeredTools.has(tool), `${relative(repo, path)} documents unknown tool ${tool}`);
  }
  // The REVERSE direction reads the whole document, fences included: a tool shown in a worked
  // example is documented, whatever the prose does.
  for (const match of text.matchAll(/\b(comms_[a-z0-9_]+)\b/g)) documentedTools.add(match[1]);
}

// EVERY REGISTERED TOOL MUST BE DOCUMENTED SOMEWHERE. The check above catches a doc that outlives
// its tool; this catches the opposite, which is what actually happened: `comms_unshare` and
// `comms_channel_delete` were added on 2026-08-18 and reached agents with no mention in any skill.
// An agent cannot use a tool it has never been told about, so an undocumented tool is a tool that
// does not exist for most of the fleet.
const undocumented = [...registeredTools].filter((tool) => !documentedTools.has(tool)).sort();
assert.deepEqual(
  undocumented, [],
  `these tools are registered on the bridge but named in no skill file, so agents will never learn `
  + `they exist: ${undocumented.join(", ")}. Add them to the Tool Map in .agents/skills (the mirror `
  + `check above keeps .claude in step).`,
);

const main = readFileSync(join(agentRoot, "aify-comms/SKILL.md"), "utf8");
assert.match(main, /comms_interrupt/, "main skill must distinguish agent-native interrupt from run interrupt");
assert.match(main, /Delivered ≠ consumer turn started/, "main skill must carry the execution evidence ladder");

// Size now lives in skill-size-ratchet.test.js, which covers ALL 17 skill files at measured ceilings
// that may only go down. The four round numbers that used to sit here (16,000 / 20,000 / 3,500)
// governed a quarter of the corpus and left slack an agent could grow into — and a cap with slack is
// a cap you are allowed to grow into. The replacement is stricter on every file it inherited.

const operations = readFileSync(join(agentRoot, "aify-comms/references/operations.md"), "utf8");
assert.doesNotMatch(operations, /20\d\d-\d\d-\d\d|\b[0-9a-f]{7,40}\b/, "operations reference contains incident history");

console.log("skill consistency: mirrors, links, tool names, and proof contract passed");
