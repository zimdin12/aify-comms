// Codex trust for the resident state hooks install.sh writes into ~/.codex/hooks.json.
//
// WHY. Codex skips a hooks.json hook whose `[hooks.state.'<file>:<event>:<group>:<hook>']` holds no
// matching trusted_hash, and a managed worker RESUMING a thread stops at codex's "hooks are new or
// changed" screen with nobody there to answer it (measured by aify-wrapper, lib/codex-herdr-hooks.mjs).
// Every reinstall that changes these command strings did that to hooks the operator had already trusted.
// So the installer records trust for exactly the hooks it wrote, and for nothing else in the file.
//
// THE HASH is codex's own: sha256 over compact JSON with sorted keys of the hook's identity. For a plain
// command hook that is {event_name, hooks:[{async:false, command, timeout, type:"command"}]}. It
// reproduced the values codex-cli 0.153.4 itself wrote on 2026-09-15 for this installer's earlier stop
// and user_prompt_submit hooks. A hook with a matcher, a statusMessage or no explicit timeout hashes
// differently or needs a default this module does not assume, so none of those is trusted here.
//
// A config.toml codex cannot parse stops every codex session on the host, so a key the file already
// defines some other way (a dotted key, an inline table) is left alone and reported.

import crypto from "node:crypto";
import fs from "node:fs";
import { fileURLToPath } from "node:url";

/** What marks a hooks.json command as one install.sh wrote. */
export const AIFY_HOOK_MARKER = "agent-state-event.mjs";

const SQ = "'";

export function snakeEvent(name) {
  return String(name).replace(/[A-Z]/g, (c, i) => (i ? "_" : "") + c.toLowerCase());
}

export function codexHookHash(event, command, timeout) {
  const identity = { event_name: event, hooks: [{ async: false, command, timeout, type: "command" }] };
  return `sha256:${crypto.createHash("sha256").update(JSON.stringify(identity)).digest("hex")}`;
}

/** The trust entries for the aify hooks in a parsed hooks.json, keyed as codex keys them. */
export function aifyHookTrust(hooksJson, hooksPath) {
  const entries = [];
  for (const [eventName, groups] of Object.entries(hooksJson?.hooks || {})) {
    if (!Array.isArray(groups)) continue;
    groups.forEach((group, groupIndex) => {
      if (!group || group.matcher !== undefined || !Array.isArray(group.hooks)) return;
      group.hooks.forEach((hook, hookIndex) => {
        const plain = hook && hook.type === "command" && typeof hook.command === "string"
          && Number.isInteger(hook.timeout) && Object.keys(hook).every((k) => ["type", "command", "timeout"].includes(k));
        if (!plain || !hook.command.includes(AIFY_HOOK_MARKER)) return;
        const event = snakeEvent(eventName);
        entries.push({ key: `${hooksPath}:${event}:${groupIndex}:${hookIndex}`, hash: codexHookHash(event, hook.command, hook.timeout) });
      });
    });
  }
  return entries;
}

/** `tomlText` with a trusted_hash recorded for each entry. Pure. */
export function withTrust(tomlText, entries) {
  const text = String(tomlText || "");
  const eol = text.includes("\r\n") ? "\r\n" : "\n";
  const lines = text.split(/\r?\n/);
  if (lines.length && lines[lines.length - 1] === "") lines.pop();
  const untouched = [];
  for (const { key, hash } of entries) {
    const headers = [`[hooks.state.${SQ}${key}${SQ}]`, `[hooks.state.${JSON.stringify(key)}]`];
    const at = lines.findIndex((line) => headers.includes(line.trim()));
    const escaped = JSON.stringify(key).slice(1, -1);
    if (key.includes(SQ) || lines.some((line, i) => i !== at && (line.includes(key) || line.includes(escaped)))) {
      untouched.push(key);
      continue;
    }
    const record = `trusted_hash = ${JSON.stringify(hash)}`;
    if (at < 0) {
      if (lines.length && lines[lines.length - 1].trim() !== "") lines.push("");
      lines.push(headers[0], record);
      continue;
    }
    let end = lines.findIndex((line, i) => i > at && line.trim().startsWith("["));
    if (end < 0) end = lines.length;
    const hashAt = lines.findIndex((line, i) => i > at && i < end && /^\s*trusted_hash\s*=/.test(line));
    if (hashAt < 0) lines.splice(at + 1, 0, record);
    else lines[hashAt] = record;
  }
  return { text: lines.join(eol) + eol, untouched };
}

function main([hooksPath, configPath]) {
  const entries = aifyHookTrust(JSON.parse(fs.readFileSync(hooksPath, "utf8")), hooksPath);
  let toml = "";
  try { toml = fs.readFileSync(configPath, "utf8"); } catch {}
  const { text, untouched } = withTrust(toml, entries);
  fs.writeFileSync(configPath, text);
  if (untouched.length) {
    console.error(`[aify-install] WARN: codex trust not recorded for ${untouched.join(", ")}: ${configPath} already defines that key another way; codex will ask once.`);
  }
}

if (process.argv[1] && fs.realpathSync(fileURLToPath(import.meta.url)) === fs.realpathSync(process.argv[1])) {
  try {
    main(process.argv.slice(2));
  } catch (err) {
    // Trust is a convenience on top of the hooks, which are already written: never fail the install for it.
    console.error(`[aify-install] WARN: codex hook trust not recorded: ${err.message}`);
  }
}
