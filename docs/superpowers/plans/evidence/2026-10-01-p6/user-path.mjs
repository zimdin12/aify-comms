// The Windows User PATH, read and written raw, for hermes-model-probe.mjs's cleanup.
//
// hermes registers `get_default_hermes_root() / "bin"` on the persistent User PATH when it publishes its launchers,
// with no opt-out (hermes_cli/_launchers.py `_register_windows_user_path`). For a home that is already a plain,
// resolved path outside hermes' own folder, that root is the home itself; hermes_constants.py rewrites any other
// (`%VAR%`, `~`, a profile under `profiles\`, a home under `%LOCALAPPDATA%\hermes`), so the probe refuses those
// homes (`homeProblem`) instead of guessing what hermes made of them.
//
// The probe's first cleanup removed EVERY entry under its home: pointed at the operator's real ~/.hermes that took
// their own .hermes\bin, and pointed at C:/ every C:\ entry. It also wrote back through SetEnvironmentVariable,
// which on Windows PowerShell 5.1 stores a plain string, so %VAR% entries were expanded for good (external review
// of 0.8.4). Its second removed any NEW entry under the home, which took one the operator added during the run
// (review of 32184384).
//
// NOW: the raw value (DoNotExpandEnvironmentNames) is read before hermes runs. At the end the only candidate is the
// entry hermes writes, `<home>\bin` in backslashes, and it counts as hermes' only if hermes' own presence test
// (`_merge_user_path`: trailing `\` and `/` stripped, case ignored, slashes NOT folded) found it absent before.
// Every other entry stays as stored. The value is written back with its own registry kind, and only if it still
// reads exactly what the decision was made on.
//
// NOT ATOMIC. The registry has no compare-and-set: the re-read and the write are separate PowerShell method calls
// (GetValue, GetValueKind, SetValue), and the gap between them is not measured. A write by another process in it is
// overwritten. That residual is stated, not closed; a write that finds the value changed before it writes nothing.

import path from "node:path";
import { spawnSync } from "node:child_process";

/** hermes' own presence test for a PATH entry (`_merge_user_path`): trailing separators stripped, case ignored. */
function hermesKey(entry) {
  return String(entry).replace(/[\\/]+$/, "").toLowerCase();
}

/**
 * Why hermes would register some other folder than `<home>\bin` for this home, or "" when it registers exactly
 * that. `localAppData` is the operator's %LOCALAPPDATA%.
 */
export function homeProblem(home, localAppData = process.env.LOCALAPPDATA || "") {
  const text = String(home || "");
  if (!text) return "no home";
  if (/[%~]/.test(text)) return `${text}: hermes expands % and ~ itself`;
  const resolved = path.win32.resolve(text);
  if (resolved.replace(/\\+$/, "") !== text.replaceAll("/", "\\").replace(/\\+$/, "")) return `${text}: not a resolved path (${resolved})`;
  if (path.win32.parse(resolved).root === resolved) return `${text}: a drive root`;
  if (/\\profiles\\[^\\]+$/i.test(resolved)) return `${text}: a profile home, which hermes maps to its parent`;
  const own = localAppData ? path.win32.join(localAppData, "hermes").toLowerCase() : "";
  if (own && (resolved.toLowerCase() === own || resolved.toLowerCase().startsWith(`${own}\\`))) return `${text}: inside hermes' own folder`;
  return "";
}

/**
 * PURE. The stored entries that are hermes' registration of `<home>\bin` from this run: present now in exactly the
 * form hermes would match, and absent before by hermes' own presence test. `home` must pass `homeProblem`.
 */
export function hermesBinEntries(beforeRaw, nowRaw, home) {
  const trimmed = String(home || "").replaceAll("/", "\\").replace(/\\+$/, "");
  if (!trimmed) return [];
  const bin = hermesKey(`${trimmed}\\bin`);
  if (String(beforeRaw).split(";").some((entry) => hermesKey(entry) === bin)) return [];
  return String(nowRaw).split(";").filter((entry) => entry && hermesKey(entry) === bin);
}

/**
 * PURE. Whether the value's first entry is one that was not there before. hermes puts its entry first, so this is
 * the cleanup's safety net: an entry it did not recognise as hermes' is still worth a look by hand.
 */
export function firstEntryIsNew(beforeRaw, afterRaw) {
  const first = String(afterRaw).split(";")[0];
  return Boolean(first) && !String(beforeRaw).split(";").includes(first);
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
 * Write `nextRaw` as `Path` under HKCU\<key>, keeping the value's registry kind, only if a re-read just before the
 * write still gives exactly `expectedRaw`. Returns "written", "changed" (it no longer read `expectedRaw`: nothing
 * written) or "failed". NOT atomic (see the header): a write landing between the re-read and SetValue is lost.
 */
export function writeUserPathRawIfUnchanged(expectedRaw, nextRaw, key = "Environment") {
  const r = powershell("$k=[Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($env:UP_KEY, $true); if (-not $k) { exit 4 }; "
    + "$now=[string]$k.GetValue('Path', '', 'DoNotExpandEnvironmentNames'); if ($now -cne $env:UP_EXPECTED) { exit 5 }; "
    + "$kind=$k.GetValueKind('Path'); $k.SetValue('Path', $env:UP_NEXT, $kind)",
  { UP_KEY: key, UP_EXPECTED: expectedRaw, UP_NEXT: nextRaw });
  return r.status === 0 ? "written" : r.status === 5 ? "changed" : "failed";
}
