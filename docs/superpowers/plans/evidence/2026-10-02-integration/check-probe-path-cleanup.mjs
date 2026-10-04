#!/usr/bin/env node
// Checks hermes-model-probe.mjs's User PATH cleanup (../2026-10-01-p6/user-path.mjs), which the probe imports.
// The real User PATH (HKCU\Environment) is never read or written: the registry cases use a throwaway key,
// HKCU\Software\aify-probe-path-check-<pid>, deleted at the end.
//   node docs/superpowers/plans/evidence/2026-10-02-integration/check-probe-path-cleanup.mjs
//
// Cases from the external review of 0.8.4 (leftover from 0.8.1): the old cleanup removed every entry under
// the home, so a home pointed at the operator's ~/.hermes took their own .hermes\bin, and one pointed at C:/
// took every C:\ entry; and it wrote back through SetEnvironmentVariable, which expands %VAR% for good.
// The sibling/slash/case cases are kept from comms-senior-dev's review of 10be3da6.
import { spawnSync } from "node:child_process";

import { entriesHermesAdded, readUserPathRaw, withoutEntries, writeUserPathRawIfUnchanged }
  from "../2026-10-01-p6/user-path.mjs";

const H = String.raw`C:\Users\X\.hermes`;
const TEMP = String.raw`C:\Users\X\AppData\Local\Temp\hermes-model-probe-ab12`;
let failed = 0;
const check = (name, ok, detail = "") => {
  if (!ok) failed += 1;
  console.log(`${ok ? "ok  " : "FAIL"} ${name}${ok ? "" : `: ${detail}`}`);
};
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// PURE: what is removed.
const cases = [
  ["the operator's own .hermes\\bin, there before, stays", H, `C:\\keep;${H}\\bin`, `C:\\keep;${H}\\bin`, []],
  ["a home at C:/ removes only what this run added", "C:/", `C:\\keep;C:\\tools`, `C:\\keep;C:\\tools;C:\\new\\bin`, ["C:\\new\\bin"]],
  ["this run's home\\bin is removed", TEMP, `C:\\keep`, `${TEMP}\\bin;C:\\keep`, [`${TEMP}\\bin`]],
  ["a new sibling sharing the prefix stays", TEMP, `C:\\keep`, `C:\\keep;${TEMP}-sibling\\bin;${TEMP}\\bin`, [`${TEMP}\\bin`]],
  ["a forward-slash home matches a backslash entry", TEMP.replaceAll("\\", "/"), "C:\\keep", `C:\\keep;${TEMP}\\bin`, [`${TEMP}\\bin`]],
  ["case differs", TEMP.toUpperCase(), "C:\\keep", `C:\\keep;${TEMP}\\bin`, [`${TEMP}\\bin`]],
  ["nothing new: nothing removed", TEMP, "C:\\keep;%USERPROFILE%\\bin", "C:\\keep;%USERPROFILE%\\bin", []],
];
for (const [name, home, before, now, expected] of cases) {
  const got = entriesHermesAdded(before, now, home);
  check(name, same(got, expected), `removed ${JSON.stringify(got)}, expected ${JSON.stringify(expected)}`);
}
check("withoutEntries keeps every other entry as stored, in order",
  withoutEntries(`%USERPROFILE%\\bin;${TEMP}\\bin;C:\\keep`, [`${TEMP}\\bin`]) === "%USERPROFILE%\\bin;C:\\keep");

// REGISTRY, on a throwaway key.
const key = `Software\\aify-probe-path-check-${process.pid}`;
const ps = (script) => spawnSync("powershell", ["-NoProfile", "-NonInteractive", "-Command", script],
  { env: { ...process.env, CK_KEY: key }, encoding: "utf8" });
const seeded = `%USERPROFILE%\\bin;C:\\keep;${TEMP}\\bin`;
ps(`$k=[Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($env:CK_KEY); $k.SetValue('Path', '${seeded}', 'ExpandString')`);
try {
  check("control: the throwaway key reads back raw, %VAR% unexpanded", readUserPathRaw(key) === seeded, readUserPathRaw(key));
  check("a stale expectation writes nothing", writeUserPathRawIfUnchanged("something else", "x", key) === "changed"
    && readUserPathRaw(key) === seeded);
  const next = withoutEntries(seeded, entriesHermesAdded("%USERPROFILE%\\bin;C:\\keep", seeded, TEMP));
  check("the write lands", writeUserPathRawIfUnchanged(seeded, next, key) === "written", next);
  check("and keeps %VAR% unexpanded", readUserPathRaw(key) === "%USERPROFILE%\\bin;C:\\keep", readUserPathRaw(key));
  const kind = ps("[Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($env:CK_KEY).GetValueKind('Path')").stdout.trim();
  check("and keeps the value's kind", kind === "ExpandString", kind);
} finally {
  ps("[Microsoft.Win32.Registry]::CurrentUser.DeleteSubKeyTree($env:CK_KEY, $false)");
}
check("the throwaway key is gone", readUserPathRaw(key) === null);
process.exit(failed ? 1 : 0);
