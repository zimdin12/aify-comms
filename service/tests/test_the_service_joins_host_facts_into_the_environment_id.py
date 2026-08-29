r"""The environment id is built once, in the tier whose table it keys.

`environmentHeartbeatPayload` on the host builds `${kind}:${hostname}:default` from a raw
`os.hostname()`. A second advertiser -- aify-env, under `docs/ENVIRONMENT_ADVERTISEMENT.md` -- would
have to build the identical string, and "identical" is a property that holds on the day it is written
and stops holding the first time either copy of the rule is edited.

WHAT THAT FAILURE LOOKS LIKE, which is why it is worth removing rather than documenting: nothing
errors. A slightly different rule does not fail to register, it registers a SECOND environment beside
the real one -- same host, same runtimes, two ids -- and the managed agents stay bound to whichever
one the bridge wrote. Both rows look plausible for as long as anyone cares to look.

So the host sends the two facts it owns and the service performs the join. `kind` distinguishes wsl,
docker, windows, macos and linux by reading environment variables and `/.dockerenv` ON THE HOST, so
the service genuinely cannot compute it and already receives it. `hostname` is the only new field.

THE CASING IS INHERITED, NOT CHOSEN. The live row is `windows:StevenZ-L:default` while its
`machineId` is `win32:stevenz-l`: the service normalises machineId with a field validator and has
never normalised the id. Lowercasing the derivation would mint a new id for every existing
environment and orphan the agents bound to the old one, so the raw form is preserved deliberately.
"""

from __future__ import annotations

from service.routers.environments import _derived_environment_id
from service.tests._base import FastApiTestCase


class TheServiceJoinsHostFactsIntoTheEnvironmentIdTests(FastApiTestCase):
    def _beat(self, **body) -> dict:
        response = self.client.post("/api/v1/environments/heartbeat", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["environment"]

    def _ids(self) -> list[str]:
        response = self.client.get("/api/v1/environments")
        self.assertEqual(response.status_code, 200, response.text)
        return sorted(e["id"] for e in response.json()["environments"])

    # -- the join -------------------------------------------------------------------------------

    def test_a_caller_that_sends_host_facts_needs_no_id(self):
        row = self._beat(kind="windows", hostname="StevenZ-L", machineId="win32:stevenz-l")
        self.assertEqual("windows:StevenZ-L:default", row["id"])

    def test_the_derived_id_matches_what_a_bridge_would_have_sent(self):
        """The whole point. A bridge sending the id and a tier sending the facts must land on ONE
        row, or the change creates the duplicate it exists to prevent."""
        self._beat(id="windows:StevenZ-L:default", bridgeId="bridge-A",
                   metadata={"bridgeStartedAt": "2026-08-29T10:00:00Z"})
        self._beat(kind="windows", hostname="StevenZ-L")
        self.assertEqual(["windows:StevenZ-L:default"], self._ids(),
                         "the tier's facts produced a second environment beside the bridge's")

    def test_the_hostname_keeps_its_casing(self):
        """Lowercasing would be tidier and would orphan every environment registered before it."""
        row = self._beat(kind="windows", hostname="StevenZ-L")
        self.assertEqual("windows:StevenZ-L:default", row["id"])
        self.assertNotEqual("windows:stevenz-l:default", row["id"])

    def test_an_explicit_id_still_wins(self):
        """A bridge sends `id` and is unaffected by any of this. If the derivation could override
        it, `AIFY_ENVIRONMENT_ID` would stop working and every custom environment name with it."""
        row = self._beat(id="windows:custom-name:default", kind="windows", hostname="StevenZ-L")
        self.assertEqual("windows:custom-name:default", row["id"])

    # -- the refusal ----------------------------------------------------------------------------

    def test_neither_an_id_nor_the_facts_is_refused_BY_THE_MODEL(self):
        """422, not 400, and the layer is the point.

        Making `id` optional so the service could derive it moved this refusal from the request model
        into the handler — `test_an_absent_field_is_the_models_job_not_the_handlers` caught that and
        says why in its own words: the two look identical to a caller and "nobody notices which check
        is now doing the work". A model validator demanding `id` OR both host facts puts it back.

        My first version of this test asserted 400 and would have ratified the wrong layer."""
        for body in ({"kind": "windows"}, {"hostname": "StevenZ-L"}, {}):
            with self.subTest(body=sorted(body)):
                response = self.client.post("/api/v1/environments/heartbeat", json=body)
                self.assertEqual(422, response.status_code, response.text)

    def test_a_BLANK_id_is_still_the_handlers_refusal(self):
        """The other direction, and it must not move either. `""` and `"   "` are strings the model
        accepts and the handler strips to nothing. Treating whitespace as absent in the validator
        would drag this 400 up into a 422 — the same mistake, pointing the other way."""
        for blank in ("", "   ", "\t"):
            with self.subTest(blank=repr(blank)):
                response = self.client.post("/api/v1/environments/heartbeat", json={"id": blank})
                self.assertEqual(400, response.status_code, response.text)

    # -- the join, driven directly ---------------------------------------------------------------

    def test_the_join_refuses_a_missing_half_rather_than_building_a_gap(self):
        """The helper on its own inputs, because the route's 400 above would also pass if the helper
        returned something truthy and the route rejected it for a different reason."""
        self.assertEqual("windows:StevenZ-L:default", _derived_environment_id("windows", "StevenZ-L"))
        self.assertEqual("", _derived_environment_id("windows", ""))
        self.assertEqual("", _derived_environment_id("", "StevenZ-L"))
        self.assertEqual("", _derived_environment_id(None, None))
        self.assertEqual("", _derived_environment_id("  ", "  "))

    def test_the_join_covers_the_kinds_the_host_can_report(self):
        """`kind` is host knowledge -- wsl and docker are detected from env vars and `/.dockerenv`,
        which the service cannot see. Whatever the host says is what keys the row."""
        for kind in ("windows", "wsl", "docker", "macos", "linux"):
            self.assertEqual(f"{kind}:box:default", _derived_environment_id(kind, "box"))
