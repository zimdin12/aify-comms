// The Windows User PATH, read and written raw, for hermes-model-probe.mjs's cleanup.
//
// hermes registers <HERMES_HOME>\bin on the persistent User PATH when it publishes its launchers, with no
// opt-out. The probe's earlier cleanup removed EVERY entry under its home. Pointed at the operator's real
// ~/.hermes, that took their own .hermes\bin; pointed at C:/, every C:\ entry. It also wrote the value back
// through [Environment]::SetEnvironmentVariable, which on Windows PowerShell 5.1 stores a plain string, so
// %VAR% entries were expanded for good (external review of 0.8.4, leftover from the 0.8.1 review).
//
// NOW: the raw value (DoNotExpandEnvironmentNames) is read before hermes runs. At the end, only entries
// that were NOT there before AND name the home or a path under it are removed. The value is written back
// with its own registry kind, and only if it is still exactly what was read for the decision; otherwise
// nothing is written and the caller is told.

import { spawnSync } from "node:child_process";

/** The home itself or a path under it, never a sibling sharing its prefix; either slash, any case. */
export function isUnderHome(entry, home) {
  const norm = (x) => String(x).replaceAll("/", "\\").replace(/\\+$/, "").toLowerCase();
  const e = norm(entry);
  const h = norm(home);
  return Boolean(h) && (e === h || e.startsWith(`${h}\\`));
}

/**
 * PURE. The entries to remove: present now, absent before, and under the home. Compared exactly as stored,
 * so an entry the operator had before (even under the home) is never taken.
 */
export function entriesHermesAdded(beforeRaw, nowRaw, home) {
  const before = new Set(String(beforeRaw).split(";"));
  return String(nowRaw).split(";").filter((entry) => entry && !before.has(entry) && isUnderHome(entry, home));
}

/** PURE. `nowRaw` without `drop`, every other entry kept as stored, in order. */
export function withoutEntries(nowRaw, drop) {
  const gone = new Set(drop);
  return String(nowRaw).split(";").filter((entry) => !gone.has(entry)).join(";");
}

const powershell = (script, env) => spawnSync("powershell", ["-NoProfile", "-NonInteractive", "-Command",
  `[Console]::OutputEncoding=[Text.Encoding]::UTF8; ${script}`], { env: { ...process.env, ...env }, encoding: "utf8" });

/** The raw value of `Path` under HKCU\<key> ("Environment" is the User PATH), or null when it cannot be read. */
export function readUserPathRaw(key = "Environment") {
  const r = powershell("$k=[Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($env:UP_KEY); "
    + "if (-not $k) { exit 4 }; [Console]::Out.Write([string]$k.GetValue('Path', '', 'DoNotExpandEnvironmentNames'))",
  { UP_KEY: key });
  return r.status === 0 ? r.stdout : null;
}

/**
 * Write `nextRaw` as `Path` under HKCU\<key>, keeping the value's registry kind, only if it still reads
 * exactly `expectedRaw`. Returns "written", "changed" (someone else wrote it meanwhile: nothing written) or
 * "failed".
 */
export function writeUserPathRawIfUnchanged(expectedRaw, nextRaw, key = "Environment") {
  const r = powershell("$k=[Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($env:UP_KEY, $true); if (-not $k) { exit 4 }; "
    + "$now=[string]$k.GetValue('Path', '', 'DoNotExpandEnvironmentNames'); if ($now -cne $env:UP_EXPECTED) { exit 5 }; "
    + "$kind=$k.GetValueKind('Path'); $k.SetValue('Path', $env:UP_NEXT, $kind)",
  { UP_KEY: key, UP_EXPECTED: expectedRaw, UP_NEXT: nextRaw });
  return r.status === 0 ? "written" : r.status === 5 ? "changed" : "failed";
}
