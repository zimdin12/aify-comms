"""The fence that keeps another agent's text from continuing as the reader's own context.

What inbox, search, channel and run-status replies say is held to the JS tools case by case by
`test_tool_replies_match_the_js.py` (0.9.11 slice 2). The fence itself is shared by several of them, so
its edge cases are pinned here once: an absent body still fences, and no inner fence survives.
"""

from __future__ import annotations

import unittest

from service.sse.rendering import fence


class FenceTests(unittest.TestCase):
    def test_fence_handles_none_and_empty(self):
        self.assertEqual("```\n\n```", fence(None))
        self.assertEqual("```\n\n```", fence(""))

    def test_fence_neutralises_every_inner_fence(self):
        fenced = fence("```\nx\n```\ny\n```")
        self.assertEqual(2, fenced.count("```"), fenced)


if __name__ == "__main__":
    unittest.main()
