#!/usr/bin/env node
// Checks hermes-model-probe.mjs's User PATH cleanup (../2026-10-01-p6/user-path.mjs), which the probe imports.
// The real User PATH (HKCU\Environment) is never read or written: the registry cases use a throwaway key,
// HKCU\Software\aify-probe-path-check-<pid>, deleted at the end.
//   node docs/superpowers/plans/evidence/2026-10-02-integration/check-probe-path-cleanup.mjs
//
// Cases from the external review of 0.8.4 (leftover from 0.8.1): the first cleanup removed every entry under the
// home, so a home pointed at the operator's ~/.hermes took their own .hermes\bin, and one pointed at C:/ took every
// C:\ entry; and it wrote back through SetEnvironmentVariable, which expands %VAR% for good. From the review of
// 32184384: removing any NEW entry under the home still took one the operator added during the run, so only
// hermes' own <home>\bin is ever a candidate. From the independent review of its successor, read against hermes'
// source: "already there" is hermes' own presence test (case and trailing separator, NOT slashes), and a home hermes
// would rewrite is refused, since the entry it registers would then be another folder.
// NOT CHECKED, because it cannot be closed here: a write by another process between the re-read and SetValue.
import { spawnSync } from "node:child_process";

import { firstEntryIsNew, hermesBinEntries, homeProblem, readUserPathRaw, withoutEntries, writeUserPathRawIfUnchanged }
  from "../2026-10-01-p6/user-path.mjs";

const H = String.raw`C:\Users\X\.hermes`;
const TEMP = String.raw`C:\Users\X\AppData\Local\Temp\hermes-model-probe-ab12`;
const LOCAL = String.raw`C:\Users\X\AppData\Local`;
let failed = 0;
const check = (name, ok, detail = "") => {
  if (!ok) failed += 1;
  console.log(`${ok ? "ok  " : "FAIL"} ${name}${ok ? "" : `: ${detail}`}`);
};
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// PURE: what is removed.
const cases = [
  ["the operator's own .hermes\\bin, there before, stays", H, `C:\\keep;${H}\\bin`, `C:\\keep;${H}\\bin`, []],
  ["there before in another case and with a trailing \\ (hermes' test sees it): stays", H,
    `C:\\keep;C:\\USERS\\X\\.HERMES\\BIN\\`, `C:\\keep;C:\\USERS\\X\\.HERMES\\BIN\\`, []],
  ["there before only with forward slashes: hermes' test misses it and adds its own, which goes", H,
    `C:\\keep;c:/users/x/.hermes/bin`, `${H}\\bin;C:\\keep;c:/users/x/.hermes/bin`, [`${H}\\bin`]],
  ["this run's home\\bin is removed", TEMP, `C:\\keep`, `${TEMP}\\bin;C:\\keep`, [`${TEMP}\\bin`]],
  ["another NEW entry under the home stays (the operator's, added during the run)", TEMP, `C:\\keep`,
    `${TEMP}\\bin;${TEMP}\\tools;C:\\keep`, [`${TEMP}\\bin`]],
  ["a new forward-slash spelling of home/bin is not what hermes writes: it stays", TEMP, `C:\\keep`,
    `${TEMP}\\bin;${TEMP.replaceAll("\\", "/")}/bin;C:\\keep`, [`${TEMP}\\bin`]],
  ["a new sibling sharing the prefix stays", TEMP, `C:\\keep`, `C:\\keep;${TEMP}-sibling\\bin;${TEMP}\\bin`, [`${TEMP}\\bin`]],
  ["a forward-slash home still names the backslash entry hermes writes", TEMP.replaceAll("\\", "/"), "C:\\keep", `C:\\keep;${TEMP}\\bin`, [`${TEMP}\\bin`]],
  ["case differs", TEMP.toUpperCase(), "C:\\keep", `C:\\keep;${TEMP}\\bin`, [`${TEMP}\\bin`]],
  ["duplicates of hermes' entry go together; empty entries stay", TEMP, "C:\\keep;;", `${TEMP}\\bin;C:\\keep;;${TEMP}\\bin`, [`${TEMP}\\bin`, `${TEMP}\\bin`]],
  ["nothing new: nothing removed", TEMP, "C:\\keep;%USERPROFILE%\\bin", "C:\\keep;%USERPROFILE%\\bin", []],
  ["an empty home removes nothing", "", "C:\\keep", "C:\\keep;\\bin", []],
];
for (const [name, home, before, now, expected] of cases) {
  const got = hermesBinEntries(before, now, home);
  check(name, same(got, expected), `removed ${JSON.stringify(got)}, expected ${JSON.stringify(expected)}`);
}
check("withoutEntries keeps every other entry as stored, in order, empty ones too",
  withoutEntries(`%USERPROFILE%\\bin;;${TEMP}\\bin;C:\\keep`, [`${TEMP}\\bin`]) === "%USERPROFILE%\\bin;;C:\\keep");

// PURE: which homes the probe refuses, because hermes would register another folder for them.
for (const [home, refused] of [
  [TEMP, false], [TEMP.replaceAll("\\", "/"), false], [`${TEMP}\\`, false],
  ["%TEMP%\\x", true], ["~\\x", true], [`${TEMP}\\profiles\\p`, true], [`${LOCAL}\\hermes`, true], [`${LOCAL}\\hermes\\sub`, true],
  ["rel", true], ["C:", true], ["C:\\", true], ["C:/", true], [`${TEMP}\\.\\x`, true], [`C:\\a\\\\b`, true], ["", true],
]) {
  const problem = homeProblem(home, LOCAL);
  check(`home ${JSON.stringify(home)} is ${refused ? "refused" : "accepted"}`, Boolean(problem) === refused, problem || "<accepted>");
}

// PURE: the safety net.
check("a new first entry is flagged", firstEntryIsNew("C:\\keep", `${TEMP}\\other;C:\\keep`));
check("control: the operator's own first entry is not", !firstEntryIsNew("C:\\keep;C:\\b", "C:\\keep;C:\\b"));

// REGISTRY, on a throwaway key.
const key = `Software\\aify-probe-path-check-${process.pid}`;
const ps = (script) => spawnSync("powershell", ["-NoProfile", "-NonInteractive", "-Command", script],
  { env: { ...process.env, CK_KEY: key }, encoding: "utf8" });
const seeded = `%USERPROFILE%\\bin;C:\\keep;${TEMP}\\bin`;
ps(`$k=[Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($env:CK_KEY); $k.SetValue('Path', '${seeded}', 'ExpandString')`);
try {
  check("control: the throwaway key reads back raw, %VAR% unexpanded", readUserPathRaw(key) === seeded, readUserPathRaw(key));
  check("a value changed since the decision writes nothing", writeUserPathRawIfUnchanged("something else", "x", key) === "changed"
    && readUserPathRaw(key) === seeded);
  const next = withoutEntries(seeded, hermesBinEntries("%USERPROFILE%\\bin;C:\\keep", seeded, TEMP));
  check("the write lands", writeUserPathRawIfUnchanged(seeded, next, key) === "written", next);
  check("and keeps %VAR% unexpanded", readUserPathRaw(key) === "%USERPROFILE%\\bin;C:\\keep", readUserPathRaw(key));
  const kind = ps("[Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($env:CK_KEY).GetValueKind('Path')").stdout.trim();
  check("and keeps the value's kind", kind === "ExpandString", kind);
} finally {
  ps("[Microsoft.Win32.Registry]::CurrentUser.DeleteSubKeyTree($env:CK_KEY, $false)");
}
check("the throwaway key is gone", readUserPathRaw(key) === null);
process.exit(failed ? 1 : 0);
