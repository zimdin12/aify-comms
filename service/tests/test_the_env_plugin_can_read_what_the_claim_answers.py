"""The RETURN LEG of the seam: what aify-env's plugin reads off a response, this service sends.

X-5 CLOSED THE DIRECTION THE PLUGIN WRITES. This is the other one, and it is the only layer here
with a defect already on the record rather than a hypothetical. `claim.mjs` says it in its own
words: six spawn requests were claimed within seconds and all six failed with "a start request must
name a launcher to run", because the plugin built a start spec from `request.launcher` -- a field
the wire has never carried. Nothing in either suite would catch that today. The plugin's tests hand
it a fixture the plugin's own author wrote; this service's tests never run the plugin.

THE ASSERTION IS BEHAVIOURAL, AND THAT IS THE WHOLE DESIGN. "Every property the plugin reads is one
the response contains" is the wrong claim twice over: it is satisfied by a name (the failure this
seam has now produced four times), and it is FALSE of correct code -- `request.workspace ||
request.workspaceRoot` reads a fallback that is absent by design, and flagging it would make the
gate cry wolf until somebody switched it off. So what is asserted is the OUTCOME: the plugin's own
claim pass, driven on this service's real answer, must reach the state a claim is supposed to reach.
A missing field cannot be argued past that.

THE RECORDING PROXY IS THE DIAGNOSIS, NOT THE VERDICT. Every property read off the spawn request is
recorded, and the ones absent from the real payload are printed WITH the failure -- so a red test
names the field instead of leaving somebody to bisect the plugin. On a green run they are not
judged at all, because a fallback read is not a defect.

IT DRIVES NO DAEMON AND REACHES NO NETWORK. `runClaimPass` takes its `api` by injection, which is
what makes this possible at all; the injected one answers with what this service really said and
records what the plugin asks it for. Starting aify-env supersedes the one serving this host, so
nothing here imports its entrypoint.

WHAT IT DOES NOT COVER: the terminal-launch response (`GET /terminals/{id}/launch`), whose consumer
takes a larger dependency set, and every response neither this nor X-3 reads. Stated so a green run
is not mistaken for the return leg being closed.

IT SKIPS BY NAME rather than passing when the checkout is absent, like its siblings.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from service.routers.api_v2 import router  # noqa: F401 — the base builds the app from it
from service.tests._base import FastApiTestCase
from service.tests.test_the_env_plugin_addresses_routes_this_service_serves import (
    PLUGIN_DIR, env_repo,
)

MACHINE_ID = "linux:test-host"
ENVIRONMENT_ID = f"{MACHINE_ID}:default"
AGENT_ID = "ef-claimed"
WORKSPACE = "/workspace/project"
RUNTIME = "codex"

#: DELIBERATELY NOT THE PLUGIN'S DEFAULT, and a surviving mutant is why. The plugin builds its
#: runtime state as `request.resumePolicy || "native_first"`, so seeding the default would make a
#: renamed read indistinguishable from a working one -- the fallback would supply the same value
#: and the fidelity check would agree with a broken plugin. `resumePolicy` is free-form on this
#: side (`req.resumePolicy or "native_first"`), so a distinct value is a thing a real caller can
#: send.
RESUME_POLICY = "resume_only"

#: The one outcome `runClaimPass` returns by falling through rather than by naming a failure.
#: READ OFF `claim.mjs` ITSELF, not copied out of a failure message: every other return there is a
#: named refusal (`unreachable`, `idle`, `refused`, `report-refused`, `failed`), and this is the
#: only path that reaches the end. Taking the value from a red test's diff would record whatever
#: the code happens to do, which is the inversion this repo keeps writing down.
CLAIM_REGISTERED = "registered"

#: Node, driving the plugin's OWN claim pass on what this service answered.
#:
#: THE `api` IS INJECTED, WHICH IS WHY THIS CAN EXIST. `runClaimPass` already takes it that way, so
#: no daemon runs and no socket opens: `claim()` replays this service's real response and `report()`
#: records what the plugin decided to send back.
#:
#: THE SPAWN REQUEST IS WRAPPED IN A RECORDING PROXY. Its `get` trap notes every property the plugin
#: asks for, so a failure can name the field rather than leaving somebody to bisect the plugin. The
#: trap returns the real value and invents nothing -- an absent property still reads `undefined`,
#: exactly as it would in production, which is what lets the OUTCOME be the thing under test.
HARNESS = """
import { readFileSync } from 'node:fs';
import { runClaimPass } from '%(claim)s';

const answered = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const asked = [];
const reported = [];

function recording(target) {
  return new Proxy(target, {
    get(object, property) {
      if (typeof property === 'string') asked.push(property);
      return object[property];
    },
  });
}

const api = {
  claim: async () => ({
    ...answered,
    spawnRequest: answered.spawnRequest ? recording(answered.spawnRequest) : answered.spawnRequest,
  }),
  report: async (id, patch) => { reported.push({ id: String(id), patch }); return { ok: true }; },
};

const outcome = await runClaimPass({
  api,
  environmentId: '%(env)s',
  cwdRoots: ['%(workspace)s'],
  windows: false,
  log: () => {},
  pid: 4242,
});

console.log(JSON.stringify({
  outcome,
  asked,
  reported,
  present: answered.spawnRequest ? Object.keys(answered.spawnRequest) : [],
}));
"""


class TheEnvPluginCanReadWhatTheClaimAnswers(FastApiTestCase):
    def setUp(self):
        super().setUp()
        self.repo, reason = env_repo()
        if self.repo is None:
            self.skipTest(f"{reason}, so the plugin's claim pass was NOT run against this "
                          "service's answer")

    # ── seeding, through the real routes ─────────────────────────────────────────────────────

    def _heartbeat(self) -> None:
        """A host advertising the runtime and the root, because the route REFUSES otherwise.

        Both are load-bearing rather than decoration: creating a spawn request answers 400 with
        "does not advertise runtime" against a host that claimed none, and the plugin's own
        `workspaceWithinRoots` refuses a workspace outside the roots THIS environment advertised.
        Seeding either loosely would have this test proving something a real host cannot reach.
        """
        beat = self.client.post(
            "/api/v1/environments/heartbeat",
            json={"id": ENVIRONMENT_ID, "machineId": MACHINE_ID, "bridgeId": "bridge-under-test",
                  "cwdRoots": [WORKSPACE],
                  # The shape a real host advertises: a runtime is an OBJECT with
                  # modes, not a bare name. Sent as a string the route answers 422.
                  "runtimes": [{"runtime": RUNTIME, "modes": ["managed-warm"],
                                "available": True}]},
        )
        self.assertEqual(beat.status_code, 200, beat.text)

    def _a_spawn_request(self) -> str:
        created = self.client.post(
            "/api/v1/spawn-requests",
            json={"createdBy": "dashboard", "environmentId": ENVIRONMENT_ID, "agentId": AGENT_ID,
                  "role": "coder", "runtime": RUNTIME, "workspace": WORKSPACE,
                  "resumePolicy": RESUME_POLICY},
        )
        self.assertEqual(created.status_code, 200, created.text)
        return created.json()["spawnRequest"]["id"]

    def _claim_answer(self) -> dict:
        """What this service really answers a claiming host — through the real route."""
        claimed = self.client.post(
            "/api/v1/spawn-requests/claim",
            json={"environmentId": ENVIRONMENT_ID, "bridgeId": "bridge-under-test",
                  "machineId": MACHINE_ID},
        )
        self.assertEqual(claimed.status_code, 200, claimed.text)
        return claimed.json()

    # ── the plugin's own pass, on that answer ────────────────────────────────────────────────

    def _read_by_the_plugin(self, answer: dict) -> dict:
        claim = (self.repo / PLUGIN_DIR / "claim.mjs").as_uri()
        script = Path(tempfile.mkdtemp()) / "drive-the-claim-pass.mjs"
        payload = script.with_name("answer.json")
        payload.write_text(json.dumps(answer), encoding="utf-8")
        script.write_text(
            HARNESS % {"claim": claim, "env": ENVIRONMENT_ID, "workspace": WORKSPACE},
            encoding="utf-8")
        done = subprocess.run(["node", str(script), str(payload)],
                              cwd=self.repo, capture_output=True, text=True)
        # A PAYLOAD FROM A PROCESS THAT DID NOT SUCCEED IS NOT EVIDENCE.
        if done.returncode != 0:
            raise AssertionError(
                f"the plugin's claim pass could not be run (exit {done.returncode}): "
                f"{done.stdout[-400:]}{done.stderr[-400:]}")
        lines = [line for line in done.stdout.splitlines() if line.startswith("{")]
        if not lines:
            raise AssertionError(f"the pass printed nothing: {done.stdout[-400:]}")
        return json.loads(lines[-1])

    @staticmethod
    def _absent_reads(read: dict) -> list[str]:
        """Properties the plugin asked for that the payload did not carry, in order, deduplicated.

        DIAGNOSIS ONLY. A fallback read (`request.workspace || request.workspaceRoot`) lands here
        on perfectly correct code, so this never decides anything — it is printed beside a failure
        so the reader knows which field to look at.
        """
        present = set(read.get("present") or [])
        seen: list[str] = []
        for name in read.get("asked") or []:
            if name not in present and name not in seen:
                seen.append(name)
        return seen

    # ── the control ──────────────────────────────────────────────────────────────────────────

    def test_this_service_answered_a_claim_for_the_plugin_to_read(self):
        """POSITIVE CONTROL. An empty answer makes the pass return `idle` and every check vacuous."""
        self._heartbeat()
        spawn_id = self._a_spawn_request()
        answer = self._claim_answer()
        self.assertTrue(answer.get("spawnRequest"),
                        f"this service claimed nothing, so nothing below judges anything: {answer}")
        self.assertEqual(answer["spawnRequest"].get("id"), spawn_id,
                         "the claim returned a different request than the one seeded")

    # ── the claim ────────────────────────────────────────────────────────────────────────────

    def test_the_plugin_can_carry_this_services_claim_answer_to_running(self):
        """THE OUTCOME, which a missing field cannot be argued past.

        `runClaimPass` reads the spawn request this service composed and reports the agent as
        running. That is the transition the whole spawn path turns on — the service's own words for
        it are "convert a spawn request into a live agent" — so a field the plugin expects and this
        service does not send stops an agent from ever appearing, with the queue draining and
        nothing anywhere reporting an error.

        Asserted on the outcome rather than on the field names, because a name check is satisfied by
        a name and is false of correct code: the plugin legitimately reads `workspaceRoot` as a
        fallback that is absent by design.
        """
        self._heartbeat()
        self._a_spawn_request()
        read = self._read_by_the_plugin(self._claim_answer())

        self.assertEqual(
            read["outcome"].get("outcome"), CLAIM_REGISTERED,
            "the plugin could not carry this service's own claim answer through to a registered "
            "agent. Fields it asked for that the answer did not carry: "
            f"{self._absent_reads(read)}. Its outcome was {read['outcome']}")

    def test_the_plugin_reports_RUNNING_against_the_id_this_service_issued(self):
        """The write-back half, and it is a separate obligation from reaching the outcome.

        Reporting `running` is what creates the agent, so a pass that reached its outcome while
        reporting against the wrong id — or not reporting at all — leaves the request claimed and
        never started. That is `spawn-queue`'s whole reason for existing as a doctor row.
        """
        self._heartbeat()
        spawn_id = self._a_spawn_request()
        read = self._read_by_the_plugin(self._claim_answer())

        running = [entry for entry in read["reported"]
                   if (entry.get("patch") or {}).get("status") == "running"]
        self.assertTrue(
            running,
            f"the plugin never reported this spawn as running, so the service would hold it "
            f"claimed and unstarted. It reported: {read['reported']}")
        self.assertEqual(
            [entry["id"] for entry in running], [spawn_id],
            "the plugin reported running against an id this service did not issue")

    def test_the_state_the_plugin_reports_BACK_carries_what_this_service_sent(self):
        """The value has to TRAVEL, not merely be defaulted, and a mutant is why this exists.

        `runClaimPass` builds its runtime state as `request.mode || "managed-warm"`. Renaming the
        field it reads was driven against the outcome check above and SURVIVED: the read returned
        undefined, the default took over, and the pass registered exactly as before. So an outcome
        alone cannot see a mode that stopped arriving -- it can only see one that stopped the pass.

        The default is correct on its own terms (every spawn this system issues is managed-warm),
        which is what makes the silence dangerous rather than the default wrong: a service sending
        some OTHER mode would have it replaced by this one with nothing reporting a problem. So
        what is asserted is agreement between the two sides, on the value this service actually
        sent, for every field the plugin echoes back.
        """
        self._heartbeat()
        self._a_spawn_request()
        answer = self._claim_answer()
        sent = answer["spawnRequest"]
        read = self._read_by_the_plugin(answer)

        running = [entry for entry in read["reported"]
                   if (entry.get("patch") or {}).get("status") == "running"]
        self.assertTrue(running, f"nothing was reported running: {read['reported']}")
        state = (running[-1]["patch"] or {}).get("runtimeState") or {}
        self.assertTrue(
            state, f"the plugin reported no runtime state at all: {running[-1]['patch']}")

        # DERIVED FROM THE OVERLAP, not from a list typed here: whatever the plugin echoes AND this
        # service sent must agree. A field the plugin invents is its own business; a field it
        # echoes with a different value than it was handed is this seam's.
        disagreed = {name: {"service sent": sent.get(name), "plugin reported": value}
                     for name, value in state.items()
                     if name in sent and value != sent.get(name)}
        self.assertEqual(disagreed, {}, (
            "the plugin reported back a value this service did not send. A default silently "
            "replacing a field that stopped arriving looks identical to one that works:" + chr(10)
            + f"  {disagreed}"))
        self.assertIn(
            "resumePolicy", set(state) & set(sent),
            "neither side names `resumePolicy` any more, so the check above no longer covers a "
            f"field whose value can differ from its default. Reported: {sorted(state)}; "
            f"sent: {sorted(sent)}")
        # `mode` IS ECHOED AND CANNOT BE JUDGED HERE, which is worth stating rather than leaving
        # as an unexplained gap. `spawn_requests.py` allows exactly one spawn mode
        # (`_SPAWN_MODES = {"managed-warm"}`) and it is character-for-character the plugin's own
        # fallback, so a renamed read is replaced by the identical value and NOTHING can observe
        # it. That mutant was driven and survives by construction. It stops being harmless the day
        # a second mode exists, and the assertion above will cover it then with no edit -- the
        # comparison is over the overlap, not over a list.
        self.assertEqual(
            sent.get("mode"), state.get("mode"),
            "the echoed mode disagrees with the one sent")

    # ── the round trip: the plugin's own reports, through this service's own route ─────────

    def _replay(self, reports: list[dict]) -> list[int]:
        """Send the plugin's recorded reports to the REAL route, in the order it sent them."""
        statuses = []
        for entry in reports:
            answered = self.client.patch(
                f"/api/v1/spawn-requests/{entry['id']}", json=entry["patch"])
            statuses.append(answered.status_code)
        return statuses

    def test_the_plugins_own_reports_REGISTER_the_agent_through_this_services_route(self):
        """THE HALF A RECORDER CANNOT SHOW, and review was right to name it.

        `api.report` in the harness answers success whatever it is handed, so the checks above
        establish what the plugin would SEND. Whether this service accepts those bodies, and
        whether an agent exists afterwards, is a different question -- and it is the one an
        operator is actually asking. The service's own words for the transition are "convert a
        spawn request into a live agent", so the agent is what gets asserted.

        NOT A LIVE SOCKET: the bodies are carried between processes rather than sent over HTTP.
        What runs the real transport is X-4, over a real header set.
        """
        self._heartbeat()
        spawn_id = self._a_spawn_request()
        read = self._read_by_the_plugin(self._claim_answer())
        self.assertTrue(read["reported"],
                        "the plugin reported nothing, so this replays nothing")

        statuses = self._replay(read["reported"])
        self.assertEqual(
            [code for code in statuses if code != 200], [],
            f"this service REFUSED reports its own claim answer led the plugin to send: "
            f"{list(zip([entry['patch'].get('status') for entry in read['reported']], statuses))}")

        listed = self.client.get("/api/v1/agents")
        self.assertEqual(listed.status_code, 200, listed.text)
        agents = listed.json().get("agents") or {}
        self.assertIn(
            AGENT_ID, agents,
            "the spawn was claimed and reported running and no agent exists, which is the state "
            "the `spawn-queue` doctor row was written for: work taken and not done, with every "
            f"other instrument reading healthy. Agents present: {sorted(agents)}")

        # AND THE REQUEST IS NO LONGER OUTSTANDING. An agent that exists while its request still
        # reads claimed is the same stranded state seen from the other side.
        requests = self.client.get("/api/v1/spawn-requests")
        self.assertEqual(requests.status_code, 200, requests.text)
        mine = [row for row in requests.json().get("spawnRequests", [])
                if row.get("id") == spawn_id]
        self.assertEqual(len(mine), 1, f"the seeded spawn request is not listed: {mine}")
        self.assertEqual(
            mine[0].get("status"), "running",
            f"the request the plugin reported running reads {mine[0].get('status')!r} instead")

    def test_the_route_REFUSES_a_report_from_a_bridge_that_did_not_claim_it(self):
        """NEGATIVE CONTROL for the replay, driven by removing what the route watches.

        A route that accepted every body would make the run above pass without judging anything,
        and a probe that cannot return ABSENT cannot return PRESENT. The plugin's own reports are
        replayed with the `bridgeId` changed to one that never claimed this request, and the
        service must refuse -- that guard is what stops a bridge reporting on work another host
        is doing.
        """
        self._heartbeat()
        self._a_spawn_request()
        read = self._read_by_the_plugin(self._claim_answer())
        self.assertTrue(read["reported"], "nothing was reported, so this control replays nothing")

        stolen = [{"id": entry["id"],
                   "patch": {**entry["patch"], "bridgeId": "some-other-bridge"}}
                  for entry in read["reported"]]
        statuses = self._replay(stolen)
        self.assertTrue(
            any(code == 409 for code in statuses),
            "this service accepted a report from a bridge that never claimed the request, so the "
            "run above cannot be read as evidence that it judged the reports at all: "
            f"{statuses}")

    # ── the negative control, driven by REMOVING what the claim watches ──────────────────────

    def test_removing_BOTH_workspace_carriers_breaks_the_pass(self):
        """Proof the run above is sensitive to the payload rather than to anything else.

        A pass that reached its outcome whatever it was handed would satisfy the claim without
        reading a single field, and a probe that cannot return ABSENT cannot return PRESENT.

        BOTH CARRIERS, and the first version of this control removed one. This service sends the
        workspace TWICE -- `workspace` and `workspaceRoot`, measured carrying the same value -- so
        what reads in the plugin as a defensive fallback (`request.workspace ||
        request.workspaceRoot`) is a live second carrier. Deleting one left the pass registering
        exactly as before, so the control passed nothing through the guard it was aiming at. That
        is the shape this repo records as pairing a carrier with a control that must STILL
        publish: remove one of two and nothing is being tested.

        Both removed, `workspaceWithinRoots` gets an empty target, refuses on its own
        `if (!target) return false`, and the pass must not come through.
        """
        self._heartbeat()
        self._a_spawn_request()
        answer = self._claim_answer()
        carriers = [name for name in ("workspace", "workspaceRoot")
                    if answer["spawnRequest"].get(name)]
        self.assertEqual(
            sorted(carriers), ["workspace", "workspaceRoot"],
            "this service no longer sends the workspace on both carriers, so this control is "
            f"removing something different from what it was written against: {carriers}")

        answer["spawnRequest"] = {key: value for key, value
                                  in answer["spawnRequest"].items() if key not in carriers}
        read = self._read_by_the_plugin(answer)
        self.assertNotEqual(
            read["outcome"].get("outcome"), CLAIM_REGISTERED,
            "the plugin registered an agent for a spawn request carrying NO workspace at all, so "
            "the run above cannot be read as evidence that this service's fields are what got it "
            "there — and the roots guard, which is what stops a service launching a process "
            "anywhere on this host, did not fire")

    def test_the_pass_goes_IDLE_when_this_service_claims_nothing(self):
        """The other branch of the same sensitivity, and the one a real host meets constantly.

        `runClaimPass` returns `idle` on an answer with no request, which is the usual case on a
        quiet queue. Asserted so that a payload this service genuinely sends is known to produce a
        distinct outcome from the one above — two answers that both read `registered` would mean
        the outcome is not carrying information.
        """
        self._heartbeat()
        read = self._read_by_the_plugin(self._claim_answer())
        self.assertEqual(
            read["outcome"].get("outcome"), "idle",
            f"an empty claim did not read as idle: {read['outcome']}")
        self.assertEqual(read["reported"], [],
                         "the plugin reported something about a spawn it was never given")

if __name__ == "__main__":
    unittest.main()
