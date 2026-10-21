// The reviewer's full-run histories (runner-acceptance-1792556712812, CLOCK-COHERENT-OBSERVATIONS.json), replayed
// against any runner driver. The driver's whole sandbox() body runs with a synthetic spawnSync and clock in place of
// the real ones, so no Docker, git or tar runs. Adapted from the reviewer's parent-probes-clock-coherent.mjs: same
// seam (the driver up to its CLI, with the child binding and Date replaced), same fixture laws, same history names.
//
//   node histories.mjs <driver.mjs> <old|new>     prints one JSON line per history and exits 1 on any mismatch
//
// "old" checks the reviewer's recorded observations on a5c658ff (the violations reproduce, his three private
// controls hold). "new" checks the successor: every history ends sound. A run is SOUND when exit 0 implies nothing
// was left behind, no primary command started after the run deadline, and no primary result came back after it.

import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

const [driverPath, mode] = process.argv.slice(2);
if (!driverPath || !["old", "new"].includes(mode)) { console.error("usage: histories.mjs <driver.mjs> <old|new>"); process.exit(2); }
const DEADLINE = 1_800_000;
const original = fs.readFileSync(driverPath, "utf8");
const boundary = original.indexOf("const arg = (name) =>");
if (boundary < 0) throw new Error("missing the CLI boundary");
const childImport = 'import { spawnSync } from "node:child_process";';
if (original.split(childImport).length !== 2) throw new Error("child binding not unique");
const prefix = `const Date = globalThis.__runnerFixture.Date;\n${original.slice(0, boundary).replace(childImport, "const spawnSync = globalThis.__runnerFixture.spawnSync;")}`;
const seamDir = fs.mkdtempSync(path.join(os.tmpdir(), "runner-histories-"));
const HERE = path.join(seamDir, "scripts", "sandbox");
fs.mkdirSync(HERE, { recursive: true });
for (const helper of ["env.Dockerfile", "sandbox-entry.sh"]) fs.writeFileSync(path.join(HERE, helper), fs.readFileSync(path.join(path.dirname(driverPath), helper)));

const result = (status = 0, stdout = "", stderr = "") => ({ status, stdout, stderr });
const before = { publishers: [
  { machine: "synthetic-machine", instance: "default", generation: 7, publication: 1, kind: "snapshot", reason: null, dataGeneration: 7, applied: true, rows: 1 },
  { machine: "synthetic-machine", instance: "residents-fixture", generation: 8, publication: 1, kind: "snapshot", reason: null, dataGeneration: 8, applied: true, rows: 0 }] };
const after = { publishers: [{ ...before.publishers[0], kind: "retired", reason: "shutdown", publication: 2, rows: 0 }, before.publishers[1]] };
const SOCKET_ERROR = "error during connect: dial unix /synthetic/docker.sock: connect: no such file or directory";

function fixture(options) {
  const state = { clock: 0, calls: [], images: new Map(), networks: new Map(), containers: new Map(), logs: [], observations: 0, cleanup: false, lateResults: [] };
  const createID = (name) => crypto.createHash("sha256").update(`SYNTHETIC ONLY ${name}`).digest("hex");
  const find = (map, ref) => map.get(ref) ?? [...map.values()].find((x) => x.id === ref);
  const findKind = (kind) => ({ image: state.images, network: state.networks, container: state.containers })[kind];
  state.Date = { now: () => state.clock };
  const raw = (command, args, opts = {}) => {
    if (command === "git") {
      state.calls.push({ command, args, at: state.clock, phase: "primary" });
      if (args.includes("rev-parse")) return result(0, "synthetic-ref-not-a-git-receipt\n");
      if (args.includes("archive")) { fs.writeFileSync(args[args.indexOf("-o") + 1], "inert synthetic archive"); return result(); }
    }
    if (command === "tar") { state.calls.push({ command, args, at: state.clock, phase: "primary" }); return result(); }
    if (command !== "docker") throw new Error(`unadmitted fake command ${command}`);
    if (args[0] === "rm" || args[0] === "rmi" || (args[0] === "network" && args[1] === "rm")) state.cleanup = true;
    state.calls.push({ command, args, at: state.clock, timeout: opts.timeout, phase: state.cleanup ? "cleanup" : "primary" });
    if (state.cleanup && options.daemonError) return result(1, "", options.daemonError);
    if (args[1] === "inspect") {
      const hit = find(findKind(args[0]), args.at(-1));
      return hit ? result(0, `${hit.id}\n`) : result(1, "", `Error: No such ${args[0]}: ${args.at(-1)}`);
    }
    if (args[0] === "build") {
      const name = args[args.indexOf("-t") + 1]; const row = { name, id: createID(name) }; state.images.set(name, row);
      if (options.readinessLatency != null) state.clock += 899_500;
      return result(0, `${row.id}\n`);
    }
    if (args[0] === "network" && args[1] === "create") {
      const name = args.at(-1), row = { name, id: createID(name) }; state.networks.set(name, row);
      return result(0, `${row.id}\n`, options.warnings ? "WARNING: benign diagnostic\n" : "");
    }
    if (args[0] === "run") {
      const name = args[args.indexOf("--name") + 1], row = { name, id: createID(name), network: args[args.indexOf("--network") + 1] };
      state.containers.set(name, row);
      return result(0, `${row.id}\n`, options.warnings ? "WARNING: benign diagnostic\n" : "");
    }
    if (args[0] === "exec") {
      if (args.includes("curl")) return result();
      if (args.includes("python")) {
        state.observations += 1;
        if (state.observations === 2 && options.readinessLatency != null) {
          // The reviewer's law: a command never outlives its timeout. "lateReturn" breaks it on purpose, the way a
          // kill that lands late does, so the driver's own acceptance check is what has to refuse the result.
          if (options.readinessLatency > opts.timeout && !options.lateReturn) throw new Error("fixture command violated configured timeout");
          state.clock += options.readinessLatency;
        }
        return result(0, `${JSON.stringify(state.observations === 1 ? { publishers: [] } : state.observations === 2 ? before : after)}\n`);
      }
    }
    if (args[0] === "kill") return result(0, `${args.at(-1)}\n`);
    if (args[0] === "wait") return result(0, "0\n");
    if (args[0] === "logs") return result(0, "synthetic fixture log\n");
    if (args[0] === "rm") {
      const hit = find(state.containers, args.at(-1)); if (!hit) return result(1, "", "Error: No such container");
      state.containers.delete(hit.name); return result(0, hit.id);
    }
    if (args[0] === "network" && args[1] === "rm") {
      const hit = find(state.networks, args.at(-1)); if (!hit) return result(1, "", "Error: No such network");
      if ([...state.containers.values()].some((c) => c.network === hit.name)) return result(1, "", "network has active endpoints");
      state.networks.delete(hit.name); return result(0, hit.id);
    }
    if (args[0] === "rmi") { state.images.delete(args.at(-1)); return result(); }
    throw new Error(`unhandled fake docker command ${JSON.stringify(args)}`);
  };
  const removes = (command, args) => command === "docker" && (args[0] === "rm" || args[0] === "rmi" || (args[0] === "network" && args[1] === "rm"));
  state.spawnSync = (command, args, opts = {}) => {
    // Classified the same way the fixture flips into cleanup, so the first removal is not counted as primary.
    const phase = state.cleanup || removes(command, args) ? "cleanup" : "primary";
    const answer = raw(command, args, opts);
    if (phase === "primary" && state.clock > DEADLINE) state.lateResults.push({ command, args, at: state.clock });
    return answer;
  };
  return state;
}

// The reviewer's three private causal controls apply to a5c658ff's text only.
function transform(text, which) {
  const replace = (old, value) => { if (text.split(old).length !== 2) throw new Error(`nonunique control ${which}`); text = text.replace(old, value); };
  if (which === "stdout-control") {
    replace("leaked: raw.includes(KEY), out: redact(raw) }", 'leaked: raw.includes(KEY), out: redact(raw), stdout: redact(done.stdout ?? "") }');
    replace("r.out.trim().split(/\\s+/).pop()", "r.stdout.trim().split(/\\s+/).pop()");
  }
  if (which === "daemon-classification-control") {
    replace('return r.status !== null && /no such|not found/i.test(r.out) ? "absent" : "unknown";',
      'if (/error during connect/i.test(r.out)) return "unknown";\n  return r.status !== null && /no such|not found/i.test(r.out) ? "absent" : "unknown";');
  }
  if (which === "completion-deadline-control") replace("const seen = observe(names.comms);", "const seen = observe(names.comms);\n      deadline();");
  return text;
}

// [name, fixture options, control, expected on a5c658ff [exit, remaining], expected on the successor [exit, remaining,
// text a verdict line must carry]]. The text names the mechanism that refused: the fixture throws only when the
// command's timeout was cut below its latency (the cap reached spawnSync), and the late-result check marks its refusal.
const SPECS = [
  ["healthy", {}, null, [0, 0], [0, 0]],
  ["stderr-id-loss", { warnings: true }, null, [0, 3], [0, 0]],
  ["daemon-enoent", { daemonError: SOCKET_ERROR }, null, [0, 5], [1, 5, "could not be read back"]],
  ["daemon-generic-control", { daemonError: "daemon connection refused" }, null, [1, 5], [1, 5]],
  ["deadline-in-budget", { readinessLatency: 500 }, null, [0, 0], [0, 0]],
  ["deadline-overrun", { readinessLatency: 2000 }, null, [0, 0], [1, 0, "violated configured timeout"]],
  ["deadline-late-return", { readinessLatency: 2000, lateReturn: true }, null, [0, 0], [1, 0, "came back after the run deadline"]],
  ["stderr-id-control", { warnings: true }, "stdout-control", [0, 0], null],
  ["daemon-classification-control", { daemonError: SOCKET_ERROR }, "daemon-classification-control", [1, 5], null],
  ["deadline-rejection-control", { readinessLatency: 2000 }, "completion-deadline-control", [1, 0], null],
];
const exportText = "\nexport {sandbox};\n";
const ORIGINAL_LOG = console.log;
let mismatches = 0;
for (const [name, options, control, onOld, onNew] of SPECS) {
  const want = mode === "old" ? onOld : onNew;
  if (!want) continue;
  const state = fixture(options);
  globalThis.__runnerFixture = state;
  console.log = (...args) => state.logs.push(args.join(" "));
  let exit, error = null;
  try {
    const file = path.join(HERE, `${mode}-${name}.mjs`);
    fs.writeFileSync(file, transform(prefix, control) + exportText);
    const api = await import(pathToFileURL(file).href);
    exit = await api.sandbox({ commsRef: "synthetic-comms-ref", envRef: "synthetic-env-ref", expect: "retired", agent: "synthetic-managed-agent" });
  } catch (e) { error = e.message; } finally { console.log = ORIGINAL_LOG; }
  const remaining = state.containers.size + state.networks.size + state.images.size;
  const latePrimaryStarts = state.calls.filter((c) => c.phase === "primary" && c.at > DEADLINE).length;
  const sound = exit !== 0 || (remaining === 0 && latePrimaryStarts === 0 && state.lateResults.length === 0);
  const verdictLines = state.logs.filter((l) => /primary:|cleanup:|terminal:/.test(l)).map((l) => l.replace(/^\+\S+ /, ""));
  const named = !want[2] || verdictLines.some((l) => l.includes(want[2]));
  const matched = error === null && exit === want[0] && remaining === want[1] && named && (mode === "old" || sound);
  if (!matched) mismatches += 1;
  console.log(JSON.stringify({ mode, name, control, exit, remaining, latePrimaryStarts, lateResults: state.lateResults.length, sound, want, named, matched, error, verdictLines }));
}
fs.rmSync(seamDir, { recursive: true, force: true });
console.log(`${mode}: ${mismatches === 0 ? "every history matched" : `${mismatches} history(ies) did not match`}`);
process.exit(mismatches === 0 ? 0 : 1);
