"""Mutation arms for the runner's R1/R2 successor. Each arm edits the driver (each anchor must occur exactly once),
runs the self-check and the successor histories, keeps both full captures, and restores the driver by byte copy,
checked by sha256. An arm is KILLED only when every witness it names fails; a control run before and after must pass.

    python -I arms.py <worktree> <out-dir>
"""
import hashlib, json, os, shutil, subprocess, sys
from pathlib import Path

WT, OUT = Path(sys.argv[1]), Path(sys.argv[2])
DRIVER = WT / "scripts/sandbox/env-against-comms.mjs"
HIST = WT / "docs/evidence/2026-10-21-runner-r1r2/histories.mjs"
ENV = {k: v for k, v in os.environ.items() if not k.startswith(("AIFY_AGENT_ID", "HERDR_", "AIFY_HERDR_"))}
ENV["SANDBOX_SELF_CHECK_KEY"] = "runner-r1r2-synthetic-only-key"
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()

ARMS = [
    ("a01-id-from-combined-output", [('const id = (r.stdout ?? "").trim();', 'const id = r.out.trim().split(/\\s+/).pop();')],
     ["self:a stderr diagnostic never becomes the owned id", "hist:stderr-id-loss"]),
    ("a02-no-id-shape-or-binding", [('if (!bound || bound.status !== 0 || !(bound.stdout ?? "").trim().startsWith(id)) {', "if (!id) {"),
                                    ("ref = bound.stdout.trim();", "ref = id;")],
     ["self:an answer naming no id fails as ownership unknown and is owned by name"]),
    ("a03-no-binding", [('!(bound.stdout ?? "").trim().startsWith(id)', "false")],
     ["self:an id the name does not resolve to is not owned as that id"]),
    ("a04-loose-absence", [('return r.status === 1 && (r.stdout ?? "").trim() === "" && answers.includes((r.stderr ?? "").trim()) ? "absent" : "unknown";',
                            'return r.status !== null && /no such|not found/i.test(r.out) ? "absent" : "unknown";')],
     ["self:a missing daemon socket is not absence (unknown)", "self:a not-found answer for another ref is not absence", "hist:daemon-enoent"]),
    ("a05-absence-not-bound-to-ref", [('answers.includes((r.stderr ?? "").trim())', '/No such|not found|no such volume/.test(r.stderr ?? "")')],
     ["self:a not-found answer for another ref is not absence"]),
    ("a06-no-admission-check", [("if (left <= 0) return", "if (false) return")],
     ["self:no command starts after the run deadline"]),
    ("a07-no-timeout-cap", [("timeout: Math.min(timeout, left)", "timeout: timeout")],
     ["self:a command still running at the run deadline is cut there, not at its own 20 s", "hist:deadline-overrun"]),
    ("a08-no-late-result-check", [("const late = primaryUntil !== null && Date.now() > primaryUntil;", "const late = false;")],
     ["hist:deadline-late-return"]),
    ("a09-cleanup-keeps-the-deadline", [("    primaryUntil = null;\n    const clean = cleanup(owned, workDir);", "    const clean = cleanup(owned, workDir);")],
     # Only late-return reaches it: in deadline-overrun the fixture refuses before the clock passes the deadline, so
     # cleanup runs inside the deadline whether or not it is lifted (first campaign, kept in arms-run1).
     ["hist:deadline-late-return"]),
    ("a10-self-check-child-unbounded", [('["-e", LEAKER], { env: leakEnv, encoding: "utf8", timeout: COMMAND_MS });', '["-e", LEAKER], { env: leakEnv, encoding: "utf8" });')],
     ["self:every spawnSync call in this file names a timeout"]),
    ("a11-failed-create-owned-only-if-present", [('if (r.status !== 0 && exists(kind, name, dock) !== "absent")', 'if (r.status !== 0 && exists(kind, name, dock) === "present")')],
     ["self:a failed create whose name cannot be read back is still owned by name"]),
]


def run(tag):
    d = OUT / tag
    d.mkdir(parents=True, exist_ok=True)
    s = subprocess.run(["node", str(DRIVER), "--self-check"], cwd=WT, env=ENV, capture_output=True, text=True, timeout=300)
    h = subprocess.run(["node", str(HIST), str(DRIVER), "new"], cwd=WT, env=ENV, capture_output=True, text=True, timeout=300)
    (d / "self-check.stdout").write_text(s.stdout, encoding="utf8"); (d / "self-check.stderr").write_text(s.stderr, encoding="utf8")
    (d / "histories.stdout").write_text(h.stdout, encoding="utf8"); (d / "histories.stderr").write_text(h.stderr, encoding="utf8")
    failed_self = [l[5:] for l in s.stdout.splitlines() if l.startswith("FAIL ")]
    failed_hist = []
    for line in h.stdout.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not row.get("matched"):
            failed_hist.append(row["name"])
    return {"selfExit": s.returncode, "histExit": h.returncode, "failedSelf": failed_self, "failedHist": failed_hist}


original = DRIVER.read_bytes()
original_sha = sha(DRIVER)
backup = OUT / "driver.original"
OUT.mkdir(parents=True, exist_ok=True)
backup.write_bytes(original)
summary = {"driverSha256": original_sha, "before": run("control-before"), "arms": []}
for name, edits, witnesses in ARMS:
    text = original.decode("utf8")
    for old, new in edits:
        assert text.count(old) == 1, f"{name}: anchor occurs {text.count(old)} times: {old!r}"
        text = text.replace(old, new)
    DRIVER.write_bytes(text.encode("utf8"))
    mutant_sha = sha(DRIVER)
    try:
        got = run(name)
    finally:
        shutil.copyfile(backup, DRIVER)
    restored = sha(DRIVER) == original_sha
    reached = [w for w in witnesses if (w[5:] in got["failedSelf"]) if w.startswith("self:")] + \
              [w for w in witnesses if w.startswith("hist:") and w[5:] in got["failedHist"]]
    killed = len(reached) == len(witnesses)
    summary["arms"].append({"arm": name, "mutantSha256": mutant_sha, "witnesses": witnesses, "reached": reached, "killed": killed, "restored": restored, **got})
    print(f"{name}: {'KILLED' if killed else 'SURVIVED'} restored={restored} self={got['failedSelf']} hist={got['failedHist']}", flush=True)
summary["after"] = run("control-after")
(OUT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf8")
ok = all(a["killed"] and a["restored"] for a in summary["arms"])
for c in ("before", "after"):
    good = summary[c]["selfExit"] == 0 and summary[c]["histExit"] == 0
    print(f"control-{c}: {'GREEN' if good else 'RED'} {summary[c]}")
    ok = ok and good
print(f"{sum(a['killed'] for a in summary['arms'])}/{len(summary['arms'])} killed; driver restored to {original_sha}: {sha(DRIVER) == original_sha}")
sys.exit(0 if ok else 1)
