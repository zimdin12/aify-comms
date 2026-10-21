// One case's sandbox: a comms container, a relay and an aify-env container on an internal network of their own, built
// from the attempt's two images, with the stand-in defined as the env's managed agent. aify-env reaches comms only
// through the relay (relay.mjs), which can silence its publications alone and records every report it carries. Every resource is created through the
// runner's `create` (../env-against-comms.mjs), so it is owned, read back absent at cleanup, and never a live name.
//
// What a case may do to the pair and what it may read from it is all here; the cases (cases.mjs) are scenarios over
// these calls, and the judges (judges.mjs) are pure over what they return.

import fs from "node:fs";
import path from "node:path";
import { create, docker, must, sleep, log } from "../env-against-comms.mjs";

export const AGENT = "gate-agent";
const SENDER = "gate-sender";
// comms folds a send into its sender's queued run (_find_mergeable_queued_run), so a case that needs two runs sends
// the second from here.
export const SECOND_SENDER = "gate-sender-2";
const ENV_DAEMON = "node /opt/aify-env/bin/aify-env.mjs";
const MS = (iso) => Date.parse(iso.endsWith("Z") ? iso : `${iso}Z`);

export class Pair {
  #owned;
  #names;
  #images;
  #probe;
  #key;
  #dock; // every Docker call goes through it: the runner's docker, or a test's stand-in

  constructor({ owned, runId, caseId, images, probePath, key, dock = docker }) {
    this.#dock = dock;
    this.#owned = owned;
    this.#images = images;
    this.#probe = probePath;
    this.#key = key;
    const id = `${runId}-${caseId}`;
    this.#names = { network: id, comms: `${id}-comms`, relay: `${id}-relay`, env: `${id}-env` };
  }

  get names() { return { ...this.#names }; }

  /** The network, comms, the relay, then aify-env with the stand-in defined. `silenced` starts the relay refusing
   *  aify-env's publications before aify-env exists, so comms has held no record from it at all (the cold start). */
  async start({ silenced = false } = {}) {
    const { network, comms, relay, env } = this.#names;
    const withKey = { ...process.env, API_KEY: this.#key, SANDBOX_COMMS_KEY: this.#key, SANDBOX_DEFINE: AGENT };
    create(this.#owned, "network", network, ["network", "create", "--internal", network], undefined, this.#dock);
    // No tmpfs: comms' data must survive `docker restart`. The image's declared volumes are anonymous; each is owned.
    create(this.#owned, "container", comms, ["run", "-d", "--name", comms, "--network", network, "--network-alias",
      "comms-real", "-e", "API_KEY", "-e", "DATA_DIR=/data", "-e", "TRUSTED_HOSTS=comms,comms:8800", this.#images.comms],
    { env: withKey }, this.#dock);
    this.#ownVolumes(comms);
    must(this.#dock(["cp", this.#probe, `${comms}:/tmp/probe.py`]), "copy the probe");
    await this.#healthy();
    // The relay answers as `comms`, the only name aify-env and the stand-in know; it forwards Host unchanged.
    create(this.#owned, "container", relay, ["run", "-d", "--init", "--name", relay, "--network", network,
      "--network-alias", "comms", "--entrypoint", "node", this.#images.env, "/opt/gate/relay.mjs"], undefined, this.#dock);
    await this.waitFor(() => this.contact().some((e) => e.event === "listening"), 60_000, "the relay to listen");
    if (silenced) this.silence(true);
    create(this.#owned, "container", env, ["run", "-d", "--init", "--name", env, "--hostname", "sandbox-env",
      "--network", network, "-e", "SANDBOX_COMMS_KEY", "-e", "SANDBOX_DEFINE", this.#images.env], { env: withKey }, this.#dock);
    this.#ownVolumes(env);
    await this.waitFor(() => /listening on/.test(this.#dock(["logs", env]).out), 120_000, "aify-env to listen");
    for (const agentId of [SENDER, SECOND_SENDER]) {
      const sender = this.api("POST", "/agents", { agentId, role: "coder", runtime: "generic",
        machineId: "linux:gate-harness", sessionMode: "resident" });
      if (!sender.ok) throw new Error(`registering ${agentId} answered ${sender.status} ${sender.text}`);
    }
    this.watch();
  }

  #ownVolumes(container) {
    const listed = must(this.#dock(["inspect", "--format", "{{range .Mounts}}{{if eq .Type \"volume\"}}{{.Name}} {{end}}{{end}}",
      container]), "list volumes").stdout.trim();
    for (const name of listed.split(/\s+/).filter(Boolean)) {
      this.#owned.push({ kind: "volume", ref: name });
      log(`owned volume ${name}`);
    }
  }

  async #healthy() {
    await this.waitFor(() => this.#dock(["exec", this.#names.comms, "curl", "-sf", "http://127.0.0.1:8800/health"]).status === 0,
      120_000, "comms /health");
  }

  /** One /api/v1 call through the probe, the key read inside the container. */
  api(method, route, body) {
    const result = this.#dock(["exec", "-i", this.#names.comms, "python", "/tmp/probe.py", "api", method, route],
      { input: body === undefined ? "" : JSON.stringify(body) });
    const text = result.stdout.trim();
    const status = Number(text.slice(0, 3));
    let json = null;
    try { json = JSON.parse(text.slice(4)); } catch { /* kept as text */ }
    return { ok: status >= 200 && status < 300, status, json, text: json ? "" : text.slice(0, 500) };
  }

  /** What comms holds now, on comms' clock. */
  snapshot() {
    return JSON.parse(must(this.#dock(["exec", this.#names.comms, "python", "/tmp/probe.py", "snapshot", AGENT]),
      "snapshot").stdout.trim().split("\n").pop());
  }

  /** The change-only timeline, kept in the container's writable layer so it survives a comms restart. */
  watch() {
    must(this.#dock(["exec", "-d", this.#names.comms, "python", "/tmp/probe.py", "watch", AGENT, "/tmp/timeline.jsonl"]),
      "start the watcher");
  }

  timeline() {
    // A read that fails is UNKNOWN, never an empty history: must() throws, and a scenario reports the error.
    const text = must(this.#dock(["exec", this.#names.comms, "cat", "/tmp/timeline.jsonl"]), "read the timeline").stdout;
    return text.split("\n").filter(Boolean).map((line) => JSON.parse(line));
  }

  standin() {
    const text = must(this.#dock(["exec", this.#names.env, "cat", `/tmp/standin/${AGENT}.log`]), "read the stand-in log").stdout;
    return text.split("\n").filter((line) => line.startsWith("{")).map((line) => JSON.parse(line));
  }

  lifetime() {
    const text = this.#dock(["exec", this.#names.env, "cat", `/tmp/standin/${AGENT}.lifetime.json`]).stdout.trim();
    try { return JSON.parse(text).lifetime; } catch { return null; }
  }

  /** The stand-in's instructions: its turn and the status a claimed run is written with. */
  control({ turn = "idle", runStatus = "completed" } = {}) {
    must(this.#dock(["exec", "-i", this.#names.env, "sh", "-c",
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
    const pids = must(this.#dock(["exec", this.#names.env, "sh", "-c", script]), "find the env daemon").stdout.trim().split(/\s+/);
    if (pids.length !== 1 || !/^\d+$/.test(pids[0])) throw new Error(`expected one aify-env daemon, found [${pids}]`);
    return pids[0];
  }

  /** Publication-only silence (review R4): the relay refuses aify-env's agent-state bodies while every other request,
   *  its claimer heartbeat included, still reaches comms. */
  silence(on) {
    must(this.#dock(["exec", this.#names.relay, "sh", "-c", on ? "touch /tmp/relay/silence" : "rm -f /tmp/relay/silence"]),
      on ? "silence publications" : "lift the silence");
  }

  /** The relay's record of every report aify-env sent: when, which, the interval it announced, and what became of it. */
  contact() {
    const text = must(this.#dock(["exec", this.#names.relay, "cat", "/tmp/relay/log.jsonl"]), "read the relay log").stdout;
    return text.split("\n").filter((line) => line.startsWith("{")).map((line) => JSON.parse(line));
  }

  /** The whole aify-env daemon stops (a separate fault history, not page 2's publication silence); the stand-in, its
   *  own process, keeps claiming. */
  // `kill` is a shell builtin in node:24-slim, with no binary on PATH.
  pauseEnv() { must(this.#dock(["exec", this.#names.env, "sh", "-c", `kill -STOP ${this.#daemonPid()}`]), "pause aify-env"); }
  resumeEnv() { must(this.#dock(["exec", this.#names.env, "sh", "-c", `kill -CONT ${this.#daemonPid()}`]), "resume aify-env"); }

  /** aify-env's container gets SIGTERM, as the runner sends it; `--init` forwards it to the daemon. Resolves to the
   *  container's exit code. */
  stopEnv() {
    must(this.#dock(["kill", "-s", "TERM", this.#names.env]), "SIGTERM aify-env");
    return Number(must(this.#dock(["wait", this.#names.env], { timeout: 60_000 }), "wait for aify-env").stdout.trim());
  }

  /** The stopped container starts again, its files kept, so the stand-in's log can still be read. */
  startEnv() { must(this.#dock(["start", this.#names.env]), "start aify-env again"); }

  /** comms restarts in the same container, its data kept; resolves to comms' clock at the new start, in ms. */
  async restartComms() {
    must(this.#dock(["restart", "-t", "10", this.#names.comms], { timeout: 120_000 }), "restart comms");
    await this.#healthy();
    this.watch();
    return MS(must(this.#dock(["inspect", "--format", "{{.State.StartedAt}}", this.#names.comms]), "started at").stdout.trim());
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
    return { timeline: this.timeline(), standin: this.standin(), contact: this.contact(), lifetime: this.lifetime(),
      envLog: this.#dock(["logs", this.#names.env]).out.split("\n").slice(-40) };
  }

  saveEvidence(dir, extra) {
    fs.mkdirSync(dir, { recursive: true });
    const evidence = { ...this.evidence(), ...extra };
    fs.writeFileSync(path.join(dir, "evidence.json"), JSON.stringify(evidence, null, 1));
    return evidence;
  }
}
