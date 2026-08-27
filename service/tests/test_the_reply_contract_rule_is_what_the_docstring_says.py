"""What is owed a reply, pinned — because the module said one thing and the query asked another.

`reply_contract.py`'s docstring read "A dispatch with `require_reply=1` is owed an answer". The
overdue query asks three clauses:

    require_reply = 1
    OR message_type IN ('request', 'review', 'error')        <- REGARDLESS of require_reply
    OR priority IN ('high','urgent') AND type NOT IN ('info','response','approval')

So for those three types the flag decides nothing: a sender setting `requireReply=false` is bound
anyway. MEASURED on the operator's live database, grouping dispatch_runs by (message_type,
require_reply): 26 `request` runs and 121 `error` runs carry require_reply=0. All 147 are bound.

THE RULE IS NOT CHANGED HERE, and that is deliberate. Honouring the flag would let a notice opt out,
which is plainly what a caller setting it intends; ignoring it means an error or a request always
gets acknowledged, which is what the reminder machinery is for. Both are defensible, and the choice
belongs to whoever owns the dispatch contract. This file pins today's answer so a flip is a visible
diff rather than a drift — the same treatment `test_status_with_dispatch.py` gave the promotion guard
before half of it was resolved.

It reads the SQL rather than executing it. The clause is a string in a query builder; running it
would need a populated database and would test the fixture as much as the rule.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from service.api_core import reply_contract

SQL = reply_contract._contract_list_query()


def _owed_clause(sql):
    """The WHERE clause that decides what is owed a reply, whitespace flattened.

    Extracted by BALANCING parentheses. The first version used a non-greedy regex and stopped at
    the `)` inside `IN ('request','review','error')`, capturing one clause of three -- so two
    assertions failed on a truncated string rather than on the code.
    """
    start = sql.index("WHERE (") + len("WHERE ")
    depth = 0
    for i in range(start, len(sql)):
        if sql[i] == "(":
            depth += 1
        elif sql[i] == ")":
            depth -= 1
            if depth == 0:
                return " ".join(sql[start + 1:i].split())
    raise AssertionError("unbalanced WHERE clause in the contract query")


#: Just the WHERE clause that decides what is owed a reply, with whitespace flattened.
OWED = _owed_clause(SQL)


class TheReplyContractRuleIsWhatTheDocstringSays(unittest.TestCase):
    def test_the_probe_found_a_real_clause(self):
        """POSITIVE CONTROL. A regex that matched nothing would make every assertion below pass on an
        empty string."""
        self.assertGreater(len(OWED), 40, OWED)
        self.assertIn("require_reply", OWED)

    def test_the_FLAG_binds_a_contract(self):
        self.assertIn("r.require_reply = 1", OWED)

    def test_and_so_does_the_TYPE_alone(self):
        """The half the docstring omitted. These three bind regardless of the flag, which is why
        `requireReply=false` does not opt out of them."""
        self.assertIn("r.message_type IN ('request','review','error')", OWED)

    def test_the_type_clause_is_an_OR_not_an_AND(self):
        """The distinction that makes the flag ineffective for those types. As an AND it would
        NARROW the flag; as an OR it overrides it."""
        before = OWED.split("r.message_type IN")[0]
        self.assertTrue(before.rstrip().endswith("OR"), OWED)

    def test_PRIORITY_binds_too_but_excludes_three_types(self):
        """The third clause, pinned so its exclusion list cannot drift silently: an `info` at urgent
        priority must not start owing a reply."""
        self.assertIn("r.priority IN ('high','urgent')", OWED)
        self.assertIn("r.message_type NOT IN ('info','response','approval')", OWED)

    def test_the_DOCSTRING_now_states_all_three(self):
        """The defect this file records. Prose that describes one clause of three sent a reader --
        and a reporter -- looking for a flag that does not decide what they thought."""
        doc = reply_contract.__doc__ or ""
        self.assertIn("REGARDLESS of require_reply", doc)
        self.assertIn("message_type IN ('request', 'review', 'error')", doc)
        self.assertIn("priority IN ('high','urgent')", doc)

    def test_the_docstring_no_longer_claims_the_flag_is_the_whole_rule(self):
        """ANTI-VACUITY for the test above: the old sentence could sit alongside the new paragraph
        and the reader would still be told something false."""
        doc = reply_contract.__doc__ or ""
        self.assertNotIn("A dispatch with `require_reply=1` is owed an answer, and", doc)

    def test_the_OPEN_QUESTION_is_written_down(self):
        """This is the part a future reader needs most: the rule is pinned, not endorsed. Without the
        note, the next person finds a test asserting the behaviour and reads it as a decision."""
        doc = reply_contract.__doc__ or ""
        self.assertIn("OPEN QUESTION", doc.upper())


if __name__ == "__main__":
    unittest.main()


class ThePythonTwinAgreesWithTheSql(unittest.TestCase):
    """`a_reply_is_owed` answers the same question as the WHERE clause above, from Python values.

    IT EXISTS BECAUSE THE CLAUSE IS A SQL STRING. `terminal_runs` holds three column values and has to
    decide the same thing when it stamps a reason on a run whose terminal died; it cannot ask the
    query. Two implementations of one rule is the divergence this repo keeps paying for, so the clause
    lists live in `reply_contract` and BOTH read them.

    These assertions check the predicate against the clause TEXT rather than against a second copy of
    my own understanding: each name the SQL mentions must be a name the predicate agrees on.
    """

    def test_every_ALWAYS_OWED_type_in_the_sql_is_owed_by_the_predicate(self):
        for kind in reply_contract._TYPES_ALWAYS_OWED:
            self.assertIn(f"'{kind}'", OWED, f"the SQL no longer lists {kind} as always owed")
            self.assertTrue(reply_contract.a_reply_is_owed(kind, 0, "normal"), kind)

    def test_the_flag_alone_owes_a_reply(self):
        self.assertTrue(reply_contract.a_reply_is_owed("response", 1, "normal"))

    def test_a_type_the_sql_EXCLUDES_on_priority_is_not_owed_at_urgent(self):
        for kind in reply_contract._TYPES_NEVER_OWED_ON_PRIORITY:
            self.assertIn(f"'{kind}'", OWED, f"the SQL no longer excludes {kind} on priority")
            self.assertFalse(reply_contract.a_reply_is_owed(kind, 0, "urgent"), kind)

    def test_an_unlisted_type_at_a_HIGH_priority_is_owed(self):
        """The third clause, which is the one a reader forgets: priority owes a reply for any type the
        exclusion list does not name."""
        for priority in reply_contract._PRIORITIES_THAT_OWE:
            self.assertIn(f"'{priority}'", OWED)
            self.assertTrue(reply_contract.a_reply_is_owed("question", 0, priority), priority)

    def test_the_ordinary_case_owes_NOTHING(self):
        """ANTI-VACUITY. A predicate returning True for everything would satisfy every assertion above."""
        self.assertFalse(reply_contract.a_reply_is_owed("response", 0, "normal"))
        self.assertFalse(reply_contract.a_reply_is_owed("info", 0, "normal"))
