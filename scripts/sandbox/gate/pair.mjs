// One case's sandbox: a comms container and an aify-env container on an internal network of their own, built from the
// attempt's two images, with the stand-in defined as the env's managed agent. Every resource is created through the
// runner's `create` (../env-against-comms.mjs), so it is owned, read back absent at cleanup, and never a live name.
//
// What a case may do to the pair and what it may read from it is all here; the cases (cases.mjs) are scenarios over
// these calls, and the judges (judges.mjs) are pure over what they return.

import fs from "node:fs";
import path from "node:path";
import { create, docker, must, sleep, log } from "../env-against-comms.mjs";

export const AGENT = "gate-agent";
const SENDER = "gate-sender";
const ENV_DAEMON = "node /opt/aify-env/bin/aify-env.mjs";
const MS = (iso) => Date.parse(iso.endsWith("Z") ? iso : `${iso}Z`);

export class Pair {
  #owned;
  #names;
  #images;
  #probe;
  #key;

  constructor({ owned, runId, caseId, images, probePath, key }) {
    this.#owned = owned;
    this.#images = images;
    this.#probe = probePath;
    this.#key = key;
    const id = `${runId}-${caseId}`;
    this.#names = { network: id, comms: `${id}-comms`, env: `${id}-env` };
  }

  get names() { return { ...this.#names }; }

  /** The network and comms, then aify-env with the stand-in defined. Resolves once comms answers /health. */
  async start() {
    const { network, comms, env } = this.#names;
    const withKey = { ...process.env, API_KEY: this.#key, SANDBOX_COMMS_KEY: this.#key, SANDBOX_DEFINE: AGENT };
    create(this.#owned, "network", network, ["network", "create", "--internal", network]);
    // No tmpfs: comms' data must survive `docker restart`. The image's declared volumes are anonymous; each is owned.
    create(this.#owned, "container", comms, ["run", "-d", "--name", comms, "--network", network, "--network-alias",
      "comms", "-e", "API_KEY", "-e", "DATA_DIR=/data", "-e", "TRUSTED_HOSTS=comms,comms:8800", this.#images.comms],
    { env: withKey });
    this.#ownVolumes(comms);
    must(docker(["cp", this.#probe, `${comms}:/tmp/probe.py`]), "copy the probe");
    await this.#healthy();
    create(this.#owned, "container", env, ["run", "-d", "--init", "--name", env, "--hostname", "sandbox-env",
      "--network", network, "-e", "SANDBOX_COMMS_KEY", "-e", "SANDBOX_DEFINE", this.#images.env], { env: withKey });
    this.#ownVolumes(env);
    await this.waitFor(() => /listening on/.test(docker(["logs", env]).out), 120_000, "aify-env to listen");
    const sender = this.api("POST", "/agents", { agentId: SENDER, role: "coder", runtime: "generic",
      machineId: "linux:gate-harness", sessionMode: "resident" });
    if (!sender.ok) throw new Error(`registering the sender answered ${sender.status} ${sender.text}`);
    this.watch();
  }

  #ownVolumes(container) {
    const listed = must(docker(["inspect", "--format", "{{range .Mounts}}{{if eq .Type \"volume\"}}{{.Name}} {{end}}{{end}}",
      container]), "list volumes").out.trim();
    for (const name of listed.split(/\s+/).filter(Boolean)) {
      this.#owned.push({ kind: "volume", ref: name });
      log(`owned volume ${name}`);
    }
  }

  async #healthy() {
    await this.waitFor(() => docker(["exec", this.#names.comms, "curl", "-sf", "http://127.0.0.1:8800/health"]).status === 0,
      120_000, "comms /health");
  }

  /** One /api/v1 call through the probe, the key read inside the container. */
  api(method, route, body) {
    const result = docker(["exec", "-i", this.#names.comms, "python", "/tmp/probe.py", "api", method, route],
      { input: body === undefined ? "" : JSON.stringify(body) });
    const text = result.out.trim();
    const status = Number(text.slice(0, 3));
    let json = null;
    try { json = JSON.parse(text.slice(4)); } catch { /* kept as text */ }
    return { ok: status >= 200 && status < 300, status, json, text: json ? "" : text.slice(0, 500) };
  }

  /** What comms holds now, on comms' clock. */
  snapshot() {
    return JSON.parse(must(docker(["exec", this.#names.comms, "python", "/tmp/probe.py", "snapshot", AGENT]),
      "snapshot").out.trim().split("\n").pop());
  }

  /** The change-only timeline, kept in the container's writable layer so it survives a comms restart. */
  watch() {
    must(docker(["exec", "-d", this.#names.comms, "python", "/tmp/probe.py", "watch", AGENT, "/tmp/timeline.jsonl"]),
      "start the watcher");
  }

  timeline() {
    const text = docker(["exec", this.#names.comms, "cat", "/tmp/timeline.jsonl"]).out;
    return text.split("\n").filter(Boolean).map((line) => JSON.parse(line));
  }

  standin() {
    const text = docker(["exec", this.#names.env, "cat", `/tmp/standin/${AGENT}.log`]).out;
    return text.split("\n").filter((line) => line.startsWith("{")).map((line) => JSON.parse(line));
  }

  lifetime() {
    const text = docker(["exec", this.#names.env, "cat", `/tmp/standin/${AGENT}.lifetime.json`]).out.trim();
    try { return JSON.parse(text).lifetime; } catch { return null; }
  }

  /** The stand-in's instructions: its turn and the status a claimed run is written with. */
  control({ turn = "idle", runStatus = "completed" } = {}) {
    must(docker(["exec", "-i", this.#names.env, "sh", "-c",
      "mkdir -p /tmp/standin && cat > /tmp/standin/control.tmp && mv /tmp/standin/control.tmp /tmp/standin/control.json"],
    { input: JSON.stringify({ turn, runStatus }) }), "write the stand-in control");
  }

  /** A send from the harness's own agent; resolves to the run id comms created, or null. */
  send(subject, extra = {}) {
    const answer = this.api("POST", "/messages/send", { from_agent: SENDER, to: AGENT, type: "request", subject,
      body: subject, trigger: true, ...extra });
    return { answer, runId: answer.json?.dispatchRuns?.[0]?.runId ?? null };
  }

  /** Poll `check(snapshot)` once a second until it answers truthy; its answer is returned. */
  async waitFor(check, timeoutMs, what) {
    const until = Date.now() + timeoutMs;
    for (;;) {
      let value = null;
      try { value = await check(); } catch { value = null; }
      if (value) return value;
      if (Date.now() > until) throw new Error(`timed out after ${timeoutMs / 1000} s waiting for ${what}`);
      await sleep(1000);
    }
  }

  /** Until comms holds a record of the agent from aify-env's own key matching `test`. */
  waitRecord(test, timeoutMs, what) {
    return this.waitFor(() => {
      const seen = this.snapshot();
      const record = seen.records.find((r) => r.instance === "default");
      return record && test(record, seen) ? seen : null;
    }, timeoutMs, what);
  }

  /** The env key's last applied publication time on comms' clock, in ms. */
  static appliedAt(seen) {
    const key = seen.publishers.find((p) => p.instance === "default");
    return key?.data_applied_at ? MS(key.data_applied_at) : null;
  }

  /** Until comms applies a new publication of kind `kind` from aify-env's key (after `afterMs`, comms' clock). */
  waitPublication(kind, afterMs, timeoutMs) {
    return this.waitFor(() => {
      const seen = this.snapshot();
      const key = seen.publishers.find((p) => p.instance === "default");
      return key && key.kind === kind && MS(key.data_applied_at) > afterMs ? seen : null;
    }, timeoutMs, `a ${kind} publication after ${new Date(afterMs).toISOString()}`);
  }

  #daemonPid() {
    const script = `for p in /proc/[0-9]*; do c=$(tr '\\0' ' ' < $p/cmdline 2>/dev/null); `
      + `[ "$c" = "${ENV_DAEMON} " ] && echo \${p#/proc/}; done; true`; // the loop's last test is not the answer
    const pids = must(docker(["exec", this.#names.env, "sh", "-c", script]), "find the env daemon").out.trim().split(/\s+/);
    if (pids.length !== 1 || !/^\d+$/.test(pids[0])) throw new Error(`expected one aify-env daemon, found [${pids}]`);
    return pids[0];
  }

  /** aify-env stops publishing; the stand-in, its own process, keeps claiming. */
  // `kill` is a shell builtin in node:24-slim, with no binary on PATH.
  pauseEnv() { must(docker(["exec", this.#names.env, "sh", "-c", `kill -STOP ${this.#daemonPid()}`]), "pause aify-env"); }
  resumeEnv() { must(docker(["exec", this.#names.env, "sh", "-c", `kill -CONT ${this.#daemonPid()}`]), "resume aify-env"); }

  /** comms restarts in the same container, its data kept; resolves to comms' clock at the new start, in ms. */
  async restartComms() {
    must(docker(["restart", "-t", "10", this.#names.comms], { timeout: 120_000 }), "restart comms");
    await this.#healthy();
    this.watch();
    return MS(must(docker(["inspect", "--format", "{{.State.StartedAt}}", this.#names.comms]), "started at").out.trim());
  }

  /** The cold start, the stand-in's first turn and its end: a warm agent comms holds a fresh idle record of. */
  async warm() {
    const { runId } = this.send("warm-up");
    if (!runId) throw new Error("the warm-up send created no run");
    await this.waitFor(() => this.standin().some((e) => e.event === "delivered" && e.runId === runId), 180_000,
      "the warm-up delivery");
    this.control({ turn: "busy" });
    await this.waitRecord((r) => r.busy === true, 60_000, "the warm-up turn");
    this.control({ turn: "idle" });
    return this.waitRecord((r) => r.busy === false && r.state === "idle", 60_000, "the warm-up turn's end");
  }

  /** Everything a judge reads, gathered once at the end of the scenario. */
  evidence() {
    return { timeline: this.timeline(), standin: this.standin(), lifetime: this.lifetime(),
      envLog: docker(["logs", this.#names.env]).out.split("\n").slice(-40) };
  }

  saveEvidence(dir, extra) {
    fs.mkdirSync(dir, { recursive: true });
    const evidence = { ...this.evidence(), ...extra };
    fs.writeFileSync(path.join(dir, "evidence.json"), JSON.stringify(evidence, null, 1));
    return evidence;
  }
}
