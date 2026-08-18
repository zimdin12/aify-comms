"""End-to-end suite: a real service process, driven over HTTP, on a scratch database.

WHY A PACKAGE OF ITS OWN. Everything in `service/tests/` above this directory runs IN-PROCESS against
`TestClient`, which shares the interpreter with the code under test. That is the right trade for 4000
unit tests and it structurally cannot catch the three things this suite exists for: startup ordering,
the reconcile sweep firing on its own timer, and a caller talking to the service over real HTTP.

THE RULE THIS SUITE MUST NEVER BREAK: it starts its own service on an ephemeral port against a tmp_path
database, and never points at a running one. The recorded incident is a hostile-env run that set
AIFY_SERVER_URL to 127.0.0.1:8800 and registered six agents into the operator's production registry.
"""
