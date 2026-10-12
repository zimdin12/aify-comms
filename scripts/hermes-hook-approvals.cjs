#!/usr/bin/env node
// Carry the operator's approval of an aify hermes hook over to the command install.sh now writes for it (0.9.1).
//
// Hermes approves a hook by its exact (event, command) pair and asks only at a prompt, which a managed hermes never
// shows, so a changed command would silently stop the hook. The commands changed on 2026-10-12: `bash "<hook>"` ran
// WSL's bash.exe on Windows and now names Git Bash by its path. Only an approval the operator already gave for the
// same event and the same aify-*.sh script is carried; nothing new is approved.
//
//   node hermes-hook-approvals.cjs <hermes config root>

const fs = require("fs");
const path = require("path");

const scriptOf = (command) => (String(command).match(/(aify-[a-z-]+\.sh)"?\s*$/) || [])[1];

/** The approvals to add: one per approved (event, script) whose current command is not approved yet. PURE. */
function carried(approvals, currentByScript, nowIso, mtimeOf) {
  const have = new Set(approvals.map((a) => `${a.event}\0${a.command}`));
  const added = [];
  for (const old of approvals) {
    const command = currentByScript[scriptOf(old.command)];
    if (!command || have.has(`${old.event}\0${command}`)) continue;
    have.add(`${old.event}\0${command}`);
    added.push({ approved_at: nowIso, command, event: old.event, script_mtime_at_approval: mtimeOf(command) });
  }
  return added;
}

/** Each aify hook script's command as config.yaml declares it now. PURE. */
function currentCommands(configText) {
  const byScript = {};
  for (const line of String(configText).split(/\r?\n/)) {
    const quoted = line.match(/command:\s*(".*")\s*$/);
    if (!quoted) continue;
    let command;
    try { command = JSON.parse(quoted[1]); } catch { continue; }
    const script = scriptOf(command);
    if (script) byScript[script] = command;
  }
  return byScript;
}

function main(root) {
  const file = path.join(root, "shell-hooks-allowlist.json");
  let raw;
  let allow;
  try { raw = fs.readFileSync(file, "utf8"); allow = JSON.parse(raw); } catch {
    console.log("  hermes hooks: nothing approved yet; each runs once approved at hermes' prompt (see 'hermes hooks list').");
    return;
  }
  const mtimeOf = (command) => {
    const hook = (command.match(/"([^"]+\.sh)"\s*$/) || [])[1];
    try { return new Date(fs.statSync(hook).mtimeMs).toISOString(); } catch { return null; }
  };
  const added = carried(allow.approvals || [], currentCommands(fs.readFileSync(path.join(root, "config.yaml"), "utf8")),
    new Date().toISOString(), mtimeOf);
  if (added.length) {
    allow.approvals = [...(allow.approvals || []), ...added];
    const eol = raw.includes("\r\n") ? "\r\n" : "\n";
    fs.writeFileSync(file, JSON.stringify(allow, null, 2).replace(/\n/g, eol) + eol);
  }
  console.log(`  hermes hooks: ${added.length} approval(s) carried to the Git Bash commands; any hook never approved`
    + " runs once approved at hermes' prompt (see 'hermes hooks list').");
}

if (require.main === module) main(process.argv[2]);
module.exports = { carried, currentCommands };
