"""The gate's collector (scripts/sandbox/gate/probe.py `watch`) stamps each frame so the judges can prove absence
(review F1): `acquiredFrom` before the read, `t` after it, and `previousSeenFrom`, the acquisition of the read just
before the frame, which returned the previous frame's content. A judge asserts "still absent after the end" only from
those lower bounds, so a stamp taken after the read would let a stale frame pass a claim made inside a hold.

Runs the real `watch` loop against a scripted clock and a stand-in `snapshot`; no database, no network."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

PROBE = Path(__file__).resolve().parents[2] / "scripts" / "sandbox" / "gate" / "probe.py"


class Stop(Exception):
    pass


def load_probe():
    spec = importlib.util.spec_from_file_location("gate_probe", PROBE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WatchStampsTests(unittest.TestCase):
    def run_watch(self, states):
        """Drive `watch` through `states` (a dict is a snapshot, an Exception a failed read). Each read starts 30 ms
        after the loop's turn begins and its API call ends 200 ms later. Returns the frames and each read's times."""
        probe = load_probe()
        clock = {"ms": 1_000_000}
        reads = []
        script = iter(states)

        def snapshot(_agent):
            clock["ms"] += 30
            reads.append({"readAt": clock["ms"]})
            state = next(script)
            clock["ms"] += 200
            if isinstance(state, Exception):
                raise state
            return dict(state)

        def sleep(_seconds):
            clock["ms"] += 500
            if len(reads) == len(states):
                raise Stop()

        with tempfile.TemporaryDirectory(prefix="aify-gate-probe-") as tmp:
            out = Path(tmp) / "timeline.jsonl"
            with mock.patch.object(probe, "snapshot", snapshot), \
                    mock.patch.object(probe.time, "time", lambda: clock["ms"] / 1000), \
                    mock.patch.object(probe.time, "sleep", sleep):
                with self.assertRaises(Stop):
                    probe.watch("agent", str(out))
            frames = [json.loads(line) for line in out.read_text().splitlines()]
        return frames, reads

    def test_a_frame_is_acquired_before_its_read_and_emitted_after_it(self):
        frames, reads = self.run_watch([{"runs": ["queued"]}, {"runs": ["claimed"]}])
        self.assertEqual(len(frames), 2)
        for frame, read in zip(frames, reads):
            self.assertLessEqual(frame["acquiredFrom"], read["readAt"])
            self.assertGreater(frame["t"], read["readAt"])

    def test_previous_seen_from_is_the_last_read_of_the_previous_content(self):
        queued, claimed = {"runs": ["queued"]}, {"runs": ["claimed"]}
        frames, reads = self.run_watch([queued, queued, queued, claimed])
        # Change-only: the three queued reads are one frame, so the claimed frame carries the third read's start.
        self.assertEqual([f["runs"] for f in frames], [["queued"], ["claimed"]])
        self.assertIsNone(frames[0]["previousSeenFrom"])
        self.assertLess(frames[1]["previousSeenFrom"], reads[2]["readAt"])
        self.assertGreater(frames[1]["previousSeenFrom"], reads[1]["readAt"])
        self.assertGreater(frames[1]["previousSeenFrom"], frames[0]["acquiredFrom"])

    def test_an_error_read_is_its_own_frame_with_no_inventory(self):
        queued = {"runs": ["queued"], "controls": []}
        frames, reads = self.run_watch([queued, OSError("database is locked"), queued])
        self.assertEqual(len(frames), 3)
        self.assertIn("error", frames[1])
        self.assertNotIn("controls", frames[1])
        self.assertNotIn("runs", frames[1])
        # The frame after the error names the error read as its predecessor, never the earlier successful one.
        self.assertEqual(frames[2]["previousSeenFrom"], frames[1]["acquiredFrom"])


if __name__ == "__main__":
    unittest.main()
