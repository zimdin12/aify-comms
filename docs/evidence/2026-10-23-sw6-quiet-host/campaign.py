"""Run the frozen gate serially and retain read-only co-located load samples."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]


def utc():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")


def process_controls(sample, present, absent):
    rows = sample.get("processes", {}).get("rows")
    if not isinstance(rows, list):
        return False
    pids = {row["ProcessId"] for row in rows}
    return present in pids and absent not in pids


class LoadObserver:
    def __init__(self, config, directory):
        self.config = config
        self.directory = directory
        self.stop = threading.Event()
        self.failure = None
        self.sequence = 0

    def probe(self, argv, timeout, parse):
        started = utc()
        try:
            done = subprocess.run(argv, capture_output=True, timeout=timeout)
            stdout = done.stdout.decode("utf-8-sig", errors="replace")
            result = {"started": started, "ended": utc(), "exit": done.returncode,
                      "stdout": stdout, "stderr": done.stderr.decode("utf-8-sig", errors="replace")}
            result["rows"] = parse(stdout) if done.returncode == 0 else None
            return result
        except Exception as error:
            return {"started": started, "ended": utc(), "exit": None,
                    "rows": None, "error": str(error)}

    def sample(self):
        ps = "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
        ps += "@(Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,CreationDate,KernelModeTime,UserModeTime,WorkingSetSize) | ConvertTo-Json -Compress"
        form = '{"id":{{json .ID}},"image":{{json .Image}},"name":{{json .Names}},"state":{{json .State}},"status":{{json .Status}}}'
        return {"at": utc(), "processes": self.probe(
            [self.config["powershell"], "-NoProfile", "-NonInteractive", "-Command", ps],
            30, json.loads), "docker": self.probe(
            [self.config["docker"], "--context", self.config["docker_context"], "ps", "--no-trunc", "--format", form], 20,
            lambda text: [json.loads(line) for line in text.splitlines() if line.strip()])}

    def control(self):
        live = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(90)"])
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        try:
            assert dead.wait(timeout=15) == 0
            sample = self.sample()
            assert live.poll() is None
            checks = {"present_detected_absent_rejected": process_controls(sample, live.pid, dead.pid),
                      "swapped_control_refused": not process_controls(sample, dead.pid, live.pid),
                      "missing_table_refused": not process_controls({}, live.pid, dead.pid),
                      "docker_read_successful": isinstance(sample["docker"]["rows"], list)}
            result = {"known_present_pid": live.pid, "known_exited_pid": dead.pid,
                      "exited_child_exit": dead.returncode, "checks": checks, "sample": sample}
            write_new(self.directory / "observer-control.json", result)
            assert all(checks.values()), checks
        finally:
            if live.poll() is None:
                live.terminate()
            live.wait(timeout=15)
            if dead.poll() is None:
                dead.terminate()
                dead.wait(timeout=15)

    def record(self, phase, due):
        sample = self.sample()
        sample.update(sequence=self.sequence, phase=phase, due_monotonic=due,
                      observed_monotonic=time.monotonic())
        self.sequence += 1
        sample["probe_gap"] = any(sample[k].get("rows") is None for k in ("processes", "docker"))
        with (self.directory / "load.jsonl").open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(sample) + "\n")
            stream.flush()
        return sample

    def loop(self):
        try:
            due = time.monotonic() + self.config["sample_seconds"]
            while not self.stop.wait(max(0, due - time.monotonic())):
                self.record("periodic", due)
                due += self.config["sample_seconds"]
        except Exception as error:
            self.failure = repr(error)


class Campaign:
    def __init__(self):
        self.config = json.loads((HERE / "source-manifest.json").read_text())
        self.env = dict(os.environ)
        for name in list(self.env):
            if name.startswith(("AIFY_", "HARNESS_", "HERDR_", "SANDBOX_")) or name in ("API_KEY", "SERVICE_VERSION", "NODE_OPTIONS", "NODE_PATH", "DOCKER_HOST"):
                del self.env[name]
        self.env["DOCKER_CONTEXT"] = self.config["docker_context"]
        self.env["AIFY_ENV_REPO"] = self.config["env_repo"]
        self.env["TMPDIR"] = self.env["TEMP"] = self.env["TMP"] = self.config["scratch"]
        Path(self.config["scratch"]).mkdir(parents=True, exist_ok=True)
        self.commit = subprocess.check_output(["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True).strip()

    def verify_sources(self):
        for row in self.config["sources"]:
            assert digest(REPO / row["path"]) == row["sha256"], row["path"]
            blob = subprocess.check_output(["git", "-C", str(REPO), "show", self.commit + ":" + row["path"]])
            assert hashlib.sha256(blob).hexdigest() == row["sha256"], row["path"]
        for repo, ref in [(str(REPO), self.config["comms"]), (self.config["env_repo"], self.config["env"])]:
            assert subprocess.check_output(["git", "-C", repo, "rev-parse", ref + "^{commit}"], text=True).strip() == ref
        raw = subprocess.check_output(["git", "-C", str(REPO), "show", self.commit + ":" + str((HERE / "source-manifest.json").relative_to(REPO)).replace("\\", "/")])
        assert raw == (HERE / "source-manifest.json").read_bytes()

    def command(self, label, argv, directory):
        row = {"label": label, "argv": argv, "cwd": str(REPO), "started": utc(), "frozen_commit": self.commit}
        print("START", label, row["started"], flush=True)
        with (directory / (label + ".txt")).open("xb") as output:
            done = subprocess.run(argv, cwd=REPO, env=self.env, stdout=output, stderr=subprocess.STDOUT)
        row.update(exit=done.returncode, ended=utc(), log_sha256=digest(directory / (label + ".txt")))
        write_new(directory / (label + ".json"), row)
        print("EXIT", label, done.returncode, row["ended"], flush=True)
        return row

    def preflight(self):
        self.verify_sources()
        directory = HERE / "preflight"
        directory.mkdir()
        observer = LoadObserver(self.config, directory)
        observer.control()
        rows = [self.command(label, [self.config["node"], entry, "--self-check"], directory)
                for label, entry in [("gate-self-check", "scripts/sandbox/gate/gate.mjs"),
                                     ("sandbox-self-check", "scripts/sandbox/env-against-comms.mjs")]]
        self.verify_sources()
        write_new(directory / "result.json", {"frozen_commit": self.commit, "runs": rows,
                                               "ok": all(row["exit"] == 0 for row in rows)})
        return 0 if all(row["exit"] == 0 for row in rows) else 1

    def run(self):
        self.verify_sources()
        preflight = json.loads((HERE / "preflight/result.json").read_text())
        assert preflight["ok"] and preflight["frozen_commit"] == self.commit
        directory = HERE / "runs"
        directory.mkdir()
        observer = LoadObserver(self.config, directory)
        first = observer.record("before-q1", time.monotonic())
        assert all(first[k].get("rows") is not None for k in ("processes", "docker"))
        thread = threading.Thread(target=observer.loop, daemon=True)
        rows = []
        write_new(directory / "window-start.json", {"at": utc(), "frozen_commit": self.commit,
                  "own_lane_commitment": self.config["own_lane_commitment"],
                  "observer_control_sha256": digest(HERE / "preflight/observer-control.json")})
        thread.start()
        try:
            for label in self.config["attempts"]:
                self.verify_sources()
                argv = [self.config["node"], "scripts/sandbox/gate/gate.mjs", "--comms=" + self.config["comms"],
                        "--env=" + self.config["env"], "--attempt=" + label, "--out=" + str(HERE / label)]
                row = self.command(label, argv, directory)
                rows.append(row)
                text = (directory / (label + ".txt")).read_text(errors="replace")
                if "cleanup: complete, every owned resource read back absent" not in text or "cleanup: FAILED" in text:
                    print("STOP: cleanup is incomplete or unproven; no remaining attempt starts", flush=True)
                    break
        finally:
            observer.stop.set()
            thread.join(timeout=55)
            last = observer.record("after-final-cleanup", time.monotonic())
            self.verify_sources()
            write_new(directory / "result.json", {"frozen_commit": self.commit, "runs": rows,
                "observer_failure": observer.failure, "observer_thread_stopped": not thread.is_alive(),
                "final_probe_gap": last["probe_gap"], "samples": observer.sequence, "ended": utc(),
                "qualification": "PENDING_REVIEW", "own_lanes": "executor commitment plus gate-owner commitment; final owner readback owed"})
        return 0 if len(rows) == len(self.config["attempts"]) and all(row["exit"] == 0 for row in rows) and observer.failure is None and not thread.is_alive() and not last["probe_gap"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["preflight", "run"])
    args = parser.parse_args()
    campaign = Campaign()
    sys.exit(campaign.preflight() if args.mode == "preflight" else campaign.run())
