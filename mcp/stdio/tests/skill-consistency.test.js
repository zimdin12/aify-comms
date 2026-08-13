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
for (const path of markdownFiles) {
  const inlineCode = [...readFileSync(path, "utf8").matchAll(/`([^`]+)`/g)].map((match) => match[1]).join("\n");
  for (const match of inlineCode.matchAll(/\b(comms_[a-z0-9_]+)\b/g)) {
    const tool = match[1];
    assert.ok(registeredTools.has(tool), `${relative(repo, path)} documents unknown tool ${tool}`);
  }
}

const main = readFileSync(join(agentRoot, "aify-comms/SKILL.md"), "utf8");
assert.match(main, /comms_interrupt/, "main skill must distinguish agent-native interrupt from run interrupt");
assert.match(main, /Delivered ≠ consumer turn started/, "main skill must carry the execution evidence ladder");

const conciseFiles = new Map([
  ["aify-comms/SKILL.md", 16_000],
  ["aify-comms/references/operations.md", 20_000],
  ["aify-comms/references/teamwork.md", 16_000],
  ["aify-comms-debug/SKILL.md", 3_500],
]);
for (const [relativePath, maxBytes] of conciseFiles) {
  const content = readFileSync(join(agentRoot, relativePath), "utf8");
  assert.ok(content.length <= maxBytes, `${relativePath} exceeds its ${maxBytes}-byte context budget`);
}

const operations = readFileSync(join(agentRoot, "aify-comms/references/operations.md"), "utf8");
assert.doesNotMatch(operations, /20\d\d-\d\d-\d\d|\b[0-9a-f]{7,40}\b/, "operations reference contains incident history");

console.log("skill consistency: mirrors, links, tool names, and proof contract passed");
