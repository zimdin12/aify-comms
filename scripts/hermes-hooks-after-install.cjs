#!/usr/bin/env node
// What install.sh does to hermes' hooks once it has written them (0.9.1): one entry per aify hook and event, and the
// operator's approval carried to the command it now writes.
//
// ONE ENTRY. install.sh rewrites every entry naming its script in place and never removed a second one, so older
// installs left each aify hook listed two or three times per event, and hermes ran it that many times on every fire
// (measured 2026-10-12: five hooks, eleven entries). The first entry for a script in an event is kept; entries that
// name no aify script are the operator's and are never touched.
//
// APPROVAL CARRIED. Hermes approves a hook by its exact (event, command) pair and asks only at a prompt, which a
// managed hermes never shows, so a changed command would silently stop the hook. The commands changed on 2026-10-12:
// `bash "<hook>"` ran WSL's bash.exe on Windows and now names Git Bash by its path. Only an approval the operator
// already gave for the same event and the same aify-*.sh script is carried; nothing new is approved.
//
//   node hermes-hooks-after-install.cjs <hermes config root>

const fs = require("fs");
const path = require("path");

//: A command exactly as install.sh writes it: `bash "<hook>"` or `"<...bash.exe>" "<hook>"`, the hook in an `agent-hooks`
//: folder. Anything else is the operator's, so an approval never moves to a command with something prefixed.
const INSTALLER_COMMAND = /^(?:bash|"[^"]*[\\/]bash(?:\.exe)?") "([^"]*[\\/]agent-hooks[\\/]aify-[a-z-]+\.sh)"$/;

/** The hook an installer-written command runs, by its whole path: a basename alone let an approval move to another file
 *  of the same name. A drive-letter path is folded for slashes and case, as Windows reads it; any other is kept exact. */
const scriptOf = (command) => {
  const hook = (String(command).trim().match(INSTALLER_COMMAND) || [])[1];
  if (!hook) return undefined;
  return /^[A-Za-z]:/.test(hook) ? hook.replace(/\\/g, "/").toLowerCase() : hook;
};

/** The aify script a config line's quoted `command:` names, or undefined. */
function scriptOnLine(line) {
  const quoted = String(line).match(/command:\s*(".*")\s*$/);
  if (!quoted) return undefined;
  try { return scriptOf(JSON.parse(quoted[1])); } catch { return undefined; }
}

/** config.yaml's text with each aify hook listed once per event; `removed` counts the entries dropped. PURE. */
function withoutDuplicateHooks(text) {
  const eol = String(text).includes("\r\n") ? "\r\n" : "\n";
  const lines = String(text).split(/\r?\n/);
  const start = lines.findIndex((line) => /^hooks:\s*$/.test(line));
  if (start < 0) return { text, removed: 0 };
  const out = lines.slice(0, start + 1);
  let removed = 0;
  let seen = new Set();
  let item = null;
  const flush = () => {
    if (!item) return;
    const script = item.map(scriptOnLine).find(Boolean);
    if (script && seen.has(script)) removed += 1;
    else {
      if (script) seen.add(script);
      out.push(...item);
    }
    item = null;
  };
  let i = start + 1;
  for (; i < lines.length && (lines[i] === "" || /^\s/.test(lines[i])); i += 1) {
    const line = lines[i];
    if (/^ {2}\S/.test(line)) { flush(); seen = new Set(); out.push(line); continue; }
    if (/^\s*- /.test(line)) { flush(); item = [line]; continue; }
    if (item) item.push(line); else out.push(line);
  }
  flush();
  out.push(...lines.slice(i));
  return { text: out.join(eol), removed };
}

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
  const configFile = path.join(root, "config.yaml");
  const config = fs.readFileSync(configFile, "utf8");
  const deduped = withoutDuplicateHooks(config);
  if (deduped.removed) fs.writeFileSync(configFile, deduped.text);
  console.log(`  hermes hooks: ${deduped.removed} duplicate aify hook entr${deduped.removed === 1 ? "y" : "ies"} removed.`);
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
  const added = carried(allow.approvals || [], currentCommands(deduped.text), new Date().toISOString(), mtimeOf);
  if (added.length) {
    allow.approvals = [...(allow.approvals || []), ...added];
    const eol = raw.includes("\r\n") ? "\r\n" : "\n";
    fs.writeFileSync(file, JSON.stringify(allow, null, 2).replace(/\n/g, eol) + eol);
  }
  console.log(`  hermes hooks: ${added.length} approval(s) carried to the Git Bash commands; any hook never approved`
    + " runs once approved at hermes' prompt (see 'hermes hooks list').");
}

if (require.main === module) main(process.argv[2]);
module.exports = { carried, currentCommands, withoutDuplicateHooks };
