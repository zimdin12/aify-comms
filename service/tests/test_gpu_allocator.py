"""The GPU allocator, which decides whether a container is allowed to start.

Every one of its methods was in the 71 the suite never entered. It is pure bookkeeping — no docker,
no nvidia-smi, no I/O of any kind — so there was never a reason it could not be tested, only that
nothing had.

WHAT IT IS PROTECTING. A GPU that is over-committed does not refuse gracefully: the container starts,
runs, and dies part-way through with a CUDA out-of-memory the operator has to attribute to whichever
of several tenants happened to allocate last. `can_allocate` is the one place that can say no while
saying no is still cheap.

THE THREE RULES ARE INDEPENDENT and each has its own way of going wrong:
  * an EXCLUSIVE lock held by someone else blocks everyone, including a later exclusive request;
  * an exclusive REQUEST is blocked by any other tenant already sharing the device;
  * the memory fractions must sum inside 100%, with a stated 5% tolerance for rounding.
They are tested apart, because a single "device is busy" test passes with any two of them deleted.

RE-ALLOCATION IS THE SUBTLE CASE. A container asking again for what it already holds must not be
counted twice — that is a restart, and double-counting it turns a healthy 60% tenant into a refused
120% one. The subtraction that handles it is asserted directly.
"""

from __future__ import annotations

import unittest

from service.containers.gpu import DeviceState, GPUAllocator
from service.containers.models import GPUConfig


def gpu(*, devices=("0",), fraction: float = 0.5, exclusive: bool = False) -> GPUConfig:
    return GPUConfig(device_ids=list(devices), memory_fraction=fraction, exclusive=exclusive)


class GpuAllocatorTests(unittest.TestCase):
    def setUp(self):
        self.allocator = GPUAllocator()

    # ── a request against a free device ──────────────────────────────────────────────────────

    def test_a_free_device_accepts_a_request(self):
        ok, reason = self.allocator.can_allocate("a", gpu())
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_a_container_asking_for_NO_device_is_always_allowed(self):
        """CPU-only containers go through the same path. Refusing them because a GPU is busy would
        block work that never wanted one."""
        self.allocator.allocate("hog", gpu(fraction=1.0, exclusive=True))
        ok, reason = self.allocator.can_allocate("cpu-only", gpu(devices=()))
        self.assertTrue(ok, reason)

    def test_asking_about_a_device_does_not_reserve_it(self):
        """`can_allocate` creates the device record lazily. If the mere question counted as a
        tenant, a status page refreshing would fill the device up."""
        self.allocator.can_allocate("a", gpu(fraction=1.0))
        self.allocator.can_allocate("b", gpu(fraction=1.0))
        ok, _ = self.allocator.can_allocate("c", gpu(fraction=1.0))
        self.assertTrue(ok, "asking twice consumed the device")
        self.assertEqual(self.allocator.get_status()["0"]["active_containers"], {})

    # ── memory fractions ─────────────────────────────────────────────────────────────────────

    def test_two_halves_fit_and_a_third_does_not(self):
        self.allocator.allocate("a", gpu(fraction=0.5))
        self.allocator.allocate("b", gpu(fraction=0.5))
        ok, reason = self.allocator.can_allocate("c", gpu(fraction=0.5))
        self.assertFalse(ok)
        self.assertIn("GPU 0", reason)
        self.assertIn("100%", reason, "the refusal has to say what was exceeded")

    def test_the_rounding_TOLERANCE_is_5_percent_and_no_more(self):
        """Fractions arrive as floats from config, so 0.33 x 3 is not exactly 1. The tolerance
        absorbs that; it is not a licence to over-commit, so 1.10 is refused."""
        self.allocator.allocate("a", gpu(fraction=1.0))
        ok, _ = self.allocator.can_allocate("b", gpu(fraction=0.05))
        self.assertTrue(ok, "the documented 5% rounding tolerance was not honoured")
        ok, _ = self.allocator.can_allocate("c", gpu(fraction=0.10))
        self.assertFalse(ok, "10% over budget was admitted as rounding")

    def test_RE_ALLOCATING_the_same_container_does_not_double_count_it(self):
        """A restart asks again for what it already holds. Counting it twice turns a healthy 60%
        tenant into a refused 120% one, and the container never comes back."""
        self.allocator.allocate("a", gpu(fraction=0.6))
        ok, reason = self.allocator.can_allocate("a", gpu(fraction=0.6))
        self.assertTrue(ok, f"a container could not re-claim its own allocation: {reason}")

    def test_a_re_allocation_that_GROWS_past_the_budget_is_still_refused(self):
        """The subtraction must not become a blanket exemption for anyone already on the device."""
        self.allocator.allocate("a", gpu(fraction=0.5))
        self.allocator.allocate("b", gpu(fraction=0.5))
        ok, _ = self.allocator.can_allocate("a", gpu(fraction=0.9))
        self.assertFalse(ok, "a tenant grew past the budget because it was already present")

    # ── exclusivity ──────────────────────────────────────────────────────────────────────────

    def test_an_exclusive_lock_blocks_everyone_else(self):
        self.allocator.allocate("owner", gpu(fraction=0.1, exclusive=True))
        ok, reason = self.allocator.can_allocate("other", gpu(fraction=0.1))
        self.assertFalse(ok)
        self.assertIn("exclusively locked by 'owner'", reason,
                      "the operator needs to know WHO holds it")

    def test_the_lock_HOLDER_can_still_re_allocate(self):
        """Otherwise an exclusive container can never restart — it would be blocked by its own
        lock, which nothing else can release."""
        self.allocator.allocate("owner", gpu(fraction=0.1, exclusive=True))
        ok, reason = self.allocator.can_allocate("owner", gpu(fraction=0.1, exclusive=True))
        self.assertTrue(ok, reason)

    def test_an_exclusive_REQUEST_is_refused_while_anyone_else_is_sharing(self):
        """The mirror of the lock: exclusivity has to be refused on the way IN as well, or the
        second container gets a lock over a device someone is already using."""
        self.allocator.allocate("sharer", gpu(fraction=0.1))
        ok, reason = self.allocator.can_allocate("wants-all", gpu(fraction=0.1, exclusive=True))
        self.assertFalse(ok)
        self.assertIn("sharer", reason, "the refusal must name who is in the way")

    def test_an_exclusive_request_is_fine_when_only_THIS_container_is_present(self):
        self.allocator.allocate("solo", gpu(fraction=0.1))
        ok, reason = self.allocator.can_allocate("solo", gpu(fraction=0.1, exclusive=True))
        self.assertTrue(ok, reason)

    def test_releasing_clears_the_lock_so_the_next_container_can_have_it(self):
        """A lock that outlives its holder makes the device permanently unusable, and nothing else
        can clear it."""
        config = gpu(fraction=0.1, exclusive=True)
        self.allocator.allocate("owner", config)
        self.allocator.release_with_fraction("owner", config)
        ok, reason = self.allocator.can_allocate("next", gpu(fraction=1.0, exclusive=True))
        self.assertTrue(ok, reason)
        self.assertIsNone(self.allocator.get_status()["0"]["exclusive_lock"])

    def test_releasing_a_container_that_never_allocated_is_harmless(self):
        """Release runs on every stop, including for containers that failed before allocating."""
        self.allocator.release_with_fraction("ghost", gpu())
        self.assertEqual(self.allocator.get_status()["0"]["active_containers"], {})

    def test_releasing_one_tenant_leaves_the_others_alone(self):
        self.allocator.allocate("a", gpu(fraction=0.3))
        self.allocator.allocate("b", gpu(fraction=0.3))
        self.allocator.release_with_fraction("a", gpu(fraction=0.3))
        self.assertEqual(self.allocator.get_status()["0"]["active_containers"], {"b": 0.3})

    def test_releasing_does_NOT_clear_another_containers_lock(self):
        """A stopping container must not free a lock it does not hold — that would hand the device
        to a third party while the real owner is still running on it."""
        self.allocator.allocate("owner", gpu(fraction=0.1, exclusive=True))
        self.allocator.release_with_fraction("other", gpu(fraction=0.1))
        self.assertEqual(self.allocator.get_status()["0"]["exclusive_lock"], "owner")

    # ── several devices ──────────────────────────────────────────────────────────────────────

    def test_a_multi_device_request_is_refused_if_ANY_device_is_full(self):
        """All-or-nothing: a container given three of its four GPUs is a container that will fail
        at run time, having taken three from everyone else on the way."""
        self.allocator.allocate("a", gpu(devices=("1",), fraction=1.0))
        ok, reason = self.allocator.can_allocate("multi", gpu(devices=("0", "1"), fraction=0.5))
        self.assertFalse(ok)
        self.assertIn("GPU 1", reason, "the refusal must name the device that is full")

    def test_devices_account_independently(self):
        self.allocator.allocate("a", gpu(devices=("0",), fraction=1.0))
        ok, reason = self.allocator.can_allocate("b", gpu(devices=("1",), fraction=1.0))
        self.assertTrue(ok, reason)

    def test_a_multi_device_allocation_is_recorded_on_every_device(self):
        self.allocator.allocate("multi", gpu(devices=("0", "1"), fraction=0.5))
        status = self.allocator.get_status()
        self.assertEqual(status["0"]["active_containers"], {"multi": 0.5})
        self.assertEqual(status["1"]["active_containers"], {"multi": 0.5})

    def test_a_multi_device_release_frees_every_device(self):
        config = gpu(devices=("0", "1"), fraction=0.5)
        self.allocator.allocate("multi", config)
        self.allocator.release_with_fraction("multi", config)
        status = self.allocator.get_status()
        self.assertEqual(status["0"]["active_containers"], {})
        self.assertEqual(status["1"]["active_containers"], {})

    # ── status ───────────────────────────────────────────────────────────────────────────────

    def test_status_reports_what_an_operator_needs_to_place_work(self):
        self.allocator.allocate("a", gpu(fraction=0.25))
        self.allocator.allocate("b", gpu(fraction=0.5, exclusive=True))
        status = self.allocator.get_status()["0"]
        self.assertEqual(status["active_containers"], {"a": 0.25, "b": 0.5})
        self.assertEqual(status["total_memory_fraction"], 0.75)
        self.assertEqual(status["exclusive_lock"], "b")

    def test_status_is_a_COPY_not_the_live_bookkeeping(self):
        """It is serialised into an API response. Handing out the live dict would let a caller — or
        a later mutation of the response — edit the allocator's own state."""
        self.allocator.allocate("a", gpu(fraction=0.25))
        status = self.allocator.get_status()
        status["0"]["active_containers"]["injected"] = 9.0
        self.assertEqual(self.allocator.get_status()["0"]["active_containers"], {"a": 0.25})

    def test_status_is_empty_before_anything_is_asked(self):
        self.assertEqual(GPUAllocator().get_status(), {})

    def test_the_device_total_is_the_sum_of_its_tenants(self):
        state = DeviceState(active_containers={"a": 0.25, "b": 0.5})
        self.assertAlmostEqual(state.total_memory_fraction, 0.75)
        self.assertEqual(DeviceState().total_memory_fraction, 0)
