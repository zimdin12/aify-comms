r"""What agents are TOLD `requireReply=false` does, against what the Work Loop actually does.

THE FLAG IS HONOURED, AND THE REMINDER LOOP IGNORES IT FOR THREE TYPES. `require_reply=0` is real:
the run auto-completes on delivery rather than staying open (`dispatch_launch.py` records an
operator-reported consequence of exactly that), and `active_run_lookup` keys "is this run active" on
`require_reply = 1`. But `_contract_list_query` re-derives obligation from the message TYPE:

    r.require_reply = 1
    OR r.message_type IN ('request','review','error')      <- REGARDLESS of require_reply

So a sender who opts out of a reply on a request, review or error is enrolled and chased anyway.
`reply_contract.py` documents this at length and leaves the BEHAVIOUR question open on purpose: the
choice belongs to whoever owns the dispatch contract.

WHAT WAS NOT OPEN was the text. Four agent-facing places told agents to set `requireReply=false` for
"an intentionally fire-and-forget request/review/error" -- naming exactly the three types for which
it does not exempt them -- including `SKILL.md`, which is in every agent's context on every turn.
Describing what happens today decides nothing about what should happen; leaving agents believing in
an opt-out they do not have was the worse of the two states.

MEASURED on the live database 2026-08-29: 155 runs carry `require_reply = 0` and are enrolled by the
type or priority clause anyway (68 error/normal, 29 error/urgent, 24 error/high, 17 request/normal,
8 request/high, 8 review/high, 1 request/normal failed), against 5,733 runs with `require_reply = 1`
and 21,781 runs in total. Real, and 0.7% of runs -- worth correcting the text, not worth alarm.

WHAT THIS GATE CAN AND CANNOT DO, said plainly because a text gate claiming to verify meaning would
be a lie. It pins the type set DERIVED from the SQL, so a decision to honour the flag makes these
tests fail and drags the text along with it. It forbids the exact phrasing that was wrong. It
requires every agent-facing mention to name the bound types. It cannot read the sentence.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CONTRACT = REPO / "service" / "api_core" / "reply_contract.py"

#: Where an AGENT reads about the flag. Developer-facing prose (docstrings explaining the defect,
#: DECISIONS.md, the weak-points doc) is deliberately out of scope: it describes the disagreement,
#: which is the opposite of promising it away.
AGENT_FACING = (
    ".claude/skills/aify-comms/SKILL.md",
    ".agents/skills/aify-comms/SKILL.md",
    "mcp/stdio/send-tools.mjs",
    "service/sse/send_tools.py",
)

#: The phrase that carried the false promise. Kept as a literal because it is a regression guard on
#: one specific wording, not a semantic check dressed up as one.
BANNED_NEAR_THE_FLAG = "fire-and-forget"

#: The clause in `_contract_list_query` that binds by type. Read from the source, never retyped.
TYPE_CLAUSE = re.compile(r"message_type IN \(([^)]*)\)")


def types_bound_regardless_of_the_flag() -> set[str]:
    """The message types `_contract_list_query` enrols whatever `require_reply` says."""
    source = CONTRACT.read_text(encoding="utf-8")
    start = source.index("r.require_reply = 1")
    match = TYPE_CLAUSE.search(source, start)
    assert match, "the type clause is gone from _contract_list_query; the contract changed"
    return {piece.strip().strip("'\"") for piece in match.group(1).split(",") if piece.strip()}


def agent_facing_texts() -> dict[str, str]:
    return {rel: (REPO / rel).read_text(encoding="utf-8") for rel in AGENT_FACING}


def sentences_mentioning_the_flag(text: str) -> list[str]:
    """Every sentence that talks about setting the flag false.

    Split on full stops and newlines only. NOT on semicolons: a semicolon joins one sentence,
    and splitting there cut `requireReply=false drops X; it does not stop Y` in half, leaving
    the half that names the bound types with no mention of the flag to match on. The gate
    caught that on its first run, against text this same change had just written.
    """
    out = []
    for chunk in re.split(r"(?<=\.)\s+|\n", text):
        low = chunk.lower()
        if "requirereply" not in low:
            continue
        if "false" not in low:
            continue
        out.append(chunk.strip())
    return out


class TheReplyFlagTextMatchesTheContractTests(unittest.TestCase):
    def test_the_type_clause_still_says_what_this_file_assumes(self):
        """Pins today's answer. If the contract owner decides to honour the flag, this fails first
        and the texts below have to be revisited in the same change."""
        self.assertEqual({"request", "review", "error"}, types_bound_regardless_of_the_flag())

    def test_the_scan_finds_the_texts_and_the_sentences(self):
        """Anti-vacuity: every assertion below is over sentences this finds, so finding none would
        pass the file by looking at nothing."""
        texts = agent_facing_texts()
        self.assertEqual(len(AGENT_FACING), len(texts))
        found = {rel: sentences_mentioning_the_flag(body) for rel, body in texts.items()}
        for rel, sentences in found.items():
            self.assertTrue(sentences, f"{rel} no longer mentions setting requireReply false at all")

    def test_no_agent_facing_text_calls_it_fire_and_forget(self):
        offenders = []
        for rel, body in agent_facing_texts().items():
            for sentence in sentences_mentioning_the_flag(body):
                if BANNED_NEAR_THE_FLAG in sentence.lower():
                    offenders.append(f"{rel}: {sentence[:110]}")
        self.assertEqual(
            [], offenders,
            "these tell an agent the flag buys fire-and-forget. The Work Loop enrols "
            + "/".join(sorted(types_bound_regardless_of_the_flag()))
            + " by type whatever the flag says:\n  " + "\n  ".join(offenders),
        )

    def test_every_agent_facing_mention_names_the_types_it_does_not_exempt(self):
        """Naming them is not proof the sentence is right, and it is the floor: a reader told the
        flag exists and not told which types ignore it cannot act on the difference."""
        bound = types_bound_regardless_of_the_flag()
        missing = []
        for rel, body in agent_facing_texts().items():
            joined = " ".join(sentences_mentioning_the_flag(body)).lower()
            absent = sorted(t for t in bound if t not in joined)
            if absent:
                missing.append(f"{rel}: does not name {', '.join(absent)}")
        self.assertEqual([], missing, "\n  ".join(missing))

    def test_the_banned_phrase_check_can_see_a_planted_one(self):
        """The detector, on input the test builds, because the assertion above is a negative and a
        broken splitter would satisfy it silently."""
        planted = "Set requireReply=false only for an intentionally fire-and-forget request."
        sentences = sentences_mentioning_the_flag(planted)
        self.assertEqual(1, len(sentences))
        self.assertIn(BANNED_NEAR_THE_FLAG, sentences[0].lower())
        # And a sentence about the flag that does NOT carry the phrase is not reported.
        clean = "requireReply=false drops the contract on info/response/approval only."
        self.assertEqual(1, len(sentences_mentioning_the_flag(clean)))
        self.assertNotIn(BANNED_NEAR_THE_FLAG, sentences_mentioning_the_flag(clean)[0].lower())

    def test_the_splitter_ignores_prose_that_is_not_about_setting_it_false(self):
        """`requireReply=true` guidance sits beside the false guidance everywhere, and pulling it in
        would make the type-naming assertion pass for the wrong reason."""
        self.assertEqual([], sentences_mentioning_the_flag("Set requireReply=true to track a reply."))
        self.assertEqual([], sentences_mentioning_the_flag("Nothing about the flag here."))


if __name__ == "__main__":
    unittest.main()
