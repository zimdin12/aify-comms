"""How a hermes agent finds the session it is actually sitting in.

`discover_session_id` decides what gets registered as the agent's `sessionHandle`, and the handle is
what every later resume, console attach and dispatch resolves against. Getting it wrong does not
fail loudly — it binds the agent to a session that is not the one on the operator's screen.

THE ORDER IS THE CONTRACT, and each step is a different kind of evidence:
  1. the active-session file — what the TUI says it is running RIGHT NOW;
  2. the env handle — durable, but inherited, and a shell that launched a previous session exports a
     stale one (DECISIONS.md, "launch-time inheritance chain");
  3. with a gateway configured: STOP, and answer None. Not "keep looking" — the gateway can serve
     historical DB state, and a scan of the sessions directory would bind a hidden session while a
     visible one is running.
  4. only with no gateway at all: the newest file in `~/.hermes/sessions`.

EVERY AMBIENT INPUT IS SEALED. These functions read four environment variables and `Path.home()`,
and the tests that existed for them read the OPERATOR'S real values — one asserted only
"str-or-None", which passes against the developer's own live hermes session and would pass just as
well if discovery were deleted. Each test below seals env and home, and asserts the seal held.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from service.runtimes.hermes import HermesAdapter

HERMES_ENV_VARS = (
    "AIFY_HERMES_ACTIVE_SESSION_FILE",
    "HERMES_TUI_ACTIVE_SESSION_FILE",
    "HERMES_SESSION_ID",
    "HERMES_SESSION",
    "AIFY_HERMES_GATEWAY_URL",
)

GATEWAY = "ws://127.0.0.1:9999/api/ws?token=x"


@pytest.fixture
def sealed(monkeypatch, tmp_path):
    """No hermes env var, and `Path.home()` inside a fresh temp directory."""
    for name in HERMES_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    assert Path.home() == home, "the home seal did not take"
    return home


def discover() -> str | None:
    return asyncio.run(HermesAdapter().discover_session_id())


def write_active_file(monkeypatch, tmp_path, content: str, *,
                      var="AIFY_HERMES_ACTIVE_SESSION_FILE") -> Path:
    path = tmp_path / "active-session.json"
    path.write_text(content, encoding="utf-8")
    monkeypatch.setenv(var, str(path))
    return path


def write_session_file(home: Path, name: str, content: str = "") -> Path:
    sessions = home / ".hermes" / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    path = sessions / name
    path.write_text(content, encoding="utf-8")
    return path


class TestTheSeal:
    def test_a_sealed_environment_discovers_nothing(self, sealed):
        """The baseline the rest of the file depends on. If this returns a session id, some ambient
        input is still reachable and every assertion below is suspect."""
        assert discover() is None

    def test_the_sessions_directory_under_the_sealed_home_is_the_one_read(self, sealed, monkeypatch):
        write_session_file(sealed, "9d1c4b7a-0000-4000-8000-000000000001.jsonl")
        assert discover() == "9d1c4b7a-0000-4000-8000-000000000001"


class TestTheActiveSessionFile:
    def test_a_bare_session_id_is_taken_as_written(self, sealed, monkeypatch, tmp_path):
        """The common shape: the wrapper writes the id and nothing else."""
        write_active_file(monkeypatch, tmp_path, "20260603120000")
        assert discover() == "20260603120000"

    def test_a_NUMERIC_id_survives_the_json_attempt(self, sealed, monkeypatch, tmp_path):
        """A hermes timestamp id is valid JSON — it parses as a number. Anything that is not a JSON
        OBJECT has to come back as the raw text, or the most ordinary id shape hermes produces
        would be discarded by the parser that exists for a different shape entirely."""
        write_active_file(monkeypatch, tmp_path, "20260603")
        assert discover() == "20260603"

    def test_surrounding_whitespace_is_stripped(self, sealed, monkeypatch, tmp_path):
        write_active_file(monkeypatch, tmp_path, "  7afed304  \n")
        assert discover() == "7afed304"

    @pytest.mark.parametrize("key", ["session_id", "sessionId", "id"])
    def test_each_recognized_json_key_is_read(self, sealed, monkeypatch, tmp_path, key):
        write_active_file(monkeypatch, tmp_path, json.dumps({key: "visible-session"}))
        assert discover() == "visible-session"

    def test_session_id_wins_over_the_other_two(self, sealed, monkeypatch, tmp_path):
        write_active_file(monkeypatch, tmp_path, json.dumps(
            {"id": "wrong", "sessionId": "also-wrong", "session_id": "right"}))
        assert discover() == "right"

    def test_a_json_OBJECT_WITH_NO_ID_KEY_IS_NOT_A_SESSION_ID(self, sealed, monkeypatch, tmp_path):
        """FIXED 2026-08-17. This fell through to "return the raw text", so a file whose shape
        changed handed back the whole JSON document as the session handle — producing a resume
        against `{"session": "..."}` that can never resolve. The JS original returns "" here, with
        the comment "JSON object with no recognized id key"; this side did not.

        Falling through to the env handle is the right failure: it is a real id or it is nothing."""
        write_active_file(monkeypatch, tmp_path, json.dumps({"session": "unrecognized-shape"}))
        monkeypatch.setenv("HERMES_SESSION_ID", "env-session")
        assert discover() == "env-session"

    def test_a_json_object_whose_id_is_not_a_string_is_not_a_session_id(self, sealed, monkeypatch,
                                                                       tmp_path):
        write_active_file(monkeypatch, tmp_path, json.dumps({"session_id": 42}))
        assert discover() is None

    def test_an_empty_id_in_the_json_is_not_a_session_id(self, sealed, monkeypatch, tmp_path):
        write_active_file(monkeypatch, tmp_path, json.dumps({"session_id": "   "}))
        assert discover() is None

    def test_the_TUI_variable_is_read_as_well(self, sealed, monkeypatch, tmp_path):
        """FIXED 2026-08-17. `HERMES_TUI_ACTIVE_SESSION_FILE` is what hermes' own TUI exports, and
        the JS adapter has always read both. A host that set only this one had a live active-session
        file this side could not see, so discovery fell through to the (inherited, possibly stale)
        env handle without anything reporting a problem."""
        write_active_file(monkeypatch, tmp_path, "tui-written-session",
                          var="HERMES_TUI_ACTIVE_SESSION_FILE")
        assert discover() == "tui-written-session"

    def test_the_AIFY_variable_wins_when_both_are_set(self, sealed, monkeypatch, tmp_path):
        """aify writes its own file deliberately; the TUI variable may be inherited from an
        unrelated hermes the operator started earlier."""
        aify = tmp_path / "aify.json"
        aify.write_text("aify-session", encoding="utf-8")
        tui = tmp_path / "tui.json"
        tui.write_text("tui-session", encoding="utf-8")
        monkeypatch.setenv("AIFY_HERMES_ACTIVE_SESSION_FILE", str(aify))
        monkeypatch.setenv("HERMES_TUI_ACTIVE_SESSION_FILE", str(tui))
        assert discover() == "aify-session"

    def test_a_MISSING_file_is_not_an_error(self, sealed, monkeypatch, tmp_path):
        """The variable is exported before the TUI writes the file. A crash here would fail
        registration for a race that resolves in milliseconds."""
        monkeypatch.setenv("AIFY_HERMES_ACTIVE_SESSION_FILE", str(tmp_path / "not-written-yet"))
        monkeypatch.setenv("HERMES_SESSION_ID", "env-session")
        assert discover() == "env-session"

    def test_an_EMPTY_file_falls_through(self, sealed, monkeypatch, tmp_path):
        """Written but not yet filled — the same race, one step later."""
        write_active_file(monkeypatch, tmp_path, "   \n  ")
        monkeypatch.setenv("HERMES_SESSION_ID", "env-session")
        assert discover() == "env-session"

    def test_a_DIRECTORY_where_the_file_should_be_falls_through(self, sealed, monkeypatch, tmp_path):
        directory = tmp_path / "a-directory"
        directory.mkdir()
        monkeypatch.setenv("AIFY_HERMES_ACTIVE_SESSION_FILE", str(directory))
        assert discover() is None

    def test_the_active_file_BEATS_a_stale_env_handle(self, sealed, monkeypatch, tmp_path):
        """The whole reason for the ordering. A shell that ran a previous hermes exports its id;
        the file is what the session on screen right now says."""
        write_active_file(monkeypatch, tmp_path, "visible-session")
        monkeypatch.setenv("HERMES_SESSION_ID", "stale-inherited-session")
        assert discover() == "visible-session"


class TestTheEnvHandle:
    def test_HERMES_SESSION_ID_is_used_when_there_is_no_file(self, sealed, monkeypatch):
        monkeypatch.setenv("HERMES_SESSION_ID", "env-session")
        assert discover() == "env-session"

    def test_HERMES_SESSION_is_the_second_variable(self, sealed, monkeypatch):
        monkeypatch.setenv("HERMES_SESSION", "fallback-session")
        assert discover() == "fallback-session"

    def test_a_PLACEHOLDER_env_handle_is_not_a_session(self, sealed, monkeypatch):
        """What a shell writes when a variable was set from an empty expansion. Registering one
        produces an agent bound to a session literally named "none".

        NOT COVERED, and deliberately not asserted either way: the string `undefined`, which is what
        JavaScript writes when an unset value is interpolated into an env var — the likeliest way
        this happens in a fleet whose bridges are all Node. Both mirrors carry the identical set
        (`base.py` and `adapters/base.js`: unknown, default, none, null), so this is not a
        divergence to fix on one side; widening it changes handle normalisation for every runtime
        and is a reviewer's call. Left unasserted so that widening the set does not fail a test."""
        for placeholder in ("none", "null", "unknown", "default", "NONE", "  Null  "):
            monkeypatch.setenv("HERMES_SESSION_ID", placeholder)
            assert discover() is None, placeholder


class TestTheGatewayStop:
    def test_a_configured_gateway_STOPS_discovery_rather_than_scanning(self, sealed, monkeypatch):
        """Not a fallthrough — a full stop. The sessions directory holds every session this host has
        ever run, and with a gateway configured the agent is a managed/hidden one whose newest file
        is very likely somebody else's visible TUI."""
        write_session_file(sealed, "9d1c4b7a-0000-4000-8000-000000000002.jsonl")
        monkeypatch.setenv("AIFY_HERMES_GATEWAY_URL", GATEWAY)
        assert discover() is None

    def test_the_env_handle_still_wins_over_the_gateway_stop(self, sealed, monkeypatch):
        monkeypatch.setenv("HERMES_SESSION_ID", "env-session")
        monkeypatch.setenv("AIFY_HERMES_GATEWAY_URL", GATEWAY)
        assert discover() == "env-session"

    def test_a_gateway_url_that_is_not_a_websocket_does_not_stop_discovery(self, sealed, monkeypatch):
        """A half-configured host with `http://` in the variable has no gateway. Treating it as one
        would silently disable the only remaining source of a session id."""
        session = write_session_file(sealed, "9d1c4b7a-0000-4000-8000-000000000003.jsonl")
        monkeypatch.setenv("AIFY_HERMES_GATEWAY_URL", "http://127.0.0.1:9999")
        assert discover() == session.stem

    def test_an_EMPTY_gateway_variable_does_not_stop_discovery(self, sealed, monkeypatch):
        session = write_session_file(sealed, "9d1c4b7a-0000-4000-8000-000000000004.jsonl")
        monkeypatch.setenv("AIFY_HERMES_GATEWAY_URL", "   ")
        assert discover() == session.stem


class TestTheSessionsDirectoryScan:
    def test_no_sessions_directory_at_all(self, sealed):
        assert discover() is None

    def test_an_EMPTY_sessions_directory(self, sealed):
        (sealed / ".hermes" / "sessions").mkdir(parents=True)
        assert discover() is None

    def test_a_sessions_directory_holding_only_subdirectories(self, sealed):
        (sealed / ".hermes" / "sessions" / "archive").mkdir(parents=True)
        assert discover() is None

    def test_the_NEWEST_file_is_the_one_read(self, sealed):
        """Mtime order, not name order. The scan is a guess at "the session this shell is in", and
        the only evidence available is which file was touched last."""
        import os

        old = write_session_file(sealed, "9d1c4b7a-0000-4000-8000-00000000000a.jsonl")
        new = write_session_file(sealed, "9d1c4b7a-0000-4000-8000-00000000000b.jsonl")
        os.utime(old, (1_600_000_000, 1_600_000_000))
        os.utime(new, (1_700_000_000, 1_700_000_000))
        assert discover() == "9d1c4b7a-0000-4000-8000-00000000000b"

    def test_a_UUID_ANYWHERE_in_the_name_is_extracted(self, sealed):
        """hermes writes `rollout-<uuid>.jsonl`-shaped names; the uuid is the session, the rest is
        decoration."""
        write_session_file(sealed, "session-9d1c4b7a-0000-4000-8000-00000000000c-final.jsonl")
        assert discover() == "9d1c4b7a-0000-4000-8000-00000000000c"

    def test_a_name_with_no_uuid_falls_back_to_the_basename(self, sealed):
        """Hermes' own ids are timestamps, not uuids. Stripping the extension is the whole
        transformation."""
        write_session_file(sealed, "20260603120000.jsonl")
        assert discover() == "20260603120000"

    def test_the_json_extension_is_stripped_as_well(self, sealed):
        write_session_file(sealed, "20260603120001.json")
        assert discover() == "20260603120001"

    def test_a_name_with_no_extension_is_used_whole(self, sealed):
        write_session_file(sealed, "20260603120002")
        assert discover() == "20260603120002"

    def test_an_ABSURD_name_falls_through_to_the_first_json_line(self, sealed):
        """The third fallback, and it is nearly unreachable on purpose: the basename rule above
        accepts anything from 1 to 127 characters, so only a name that strips to nothing or runs
        past 128 gets here. Kept and pinned rather than deleted — this is the branch that reads the
        file's CONTENT, and it is the only one that would survive hermes renaming its files."""
        name = ("z" * 130) + ".jsonl"
        write_session_file(sealed, name, json.dumps({"session_id": "from-inside-the-file"}) + "\n")
        assert discover() == "from-inside-the-file"

    def test_an_absurd_name_with_unreadable_content_is_not_a_session(self, sealed):
        write_session_file(sealed, ("z" * 130) + ".jsonl", "not json at all\n")
        assert discover() is None


class TestTheRetiredGatewayQuery:
    def test_the_adapter_no_longer_carries_a_most_recent_query(self):
        """`_query_gateway_most_recent` was a 38-line WebSocket client that nothing called and
        nothing may call: `discover_session_id` returns None instead of asking the gateway, both
        debug skills say not to use `session.most_recent` as the current visible session because it
        can be historical DB state, and the JS adapter this file mirrors has no counterpart — the
        tui_gateway path it spoke to was retired in `11ba0cd`.

        Asserted so that re-adding it is a decision somebody makes on purpose, with this test in
        front of them, rather than a plausible-looking helper reappearing next to a comment two
        methods up saying don't."""
        assert not hasattr(HermesAdapter, "_query_gateway_most_recent")
