from __future__ import annotations

import logging
import unittest

from ev.events import PhaxEventBus
from ev.permissions import Permission
from ev.planner import TaskPlan, TaskPlanner
from ev.state import StateMachine
from ev.tools import ToolContext, ToolRegistry, ToolSpec


class PlannerFailureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bus = PhaxEventBus()
        registry = ToolRegistry(ToolContext({}, self.bus, logging.getLogger("test")))
        for name, permission in (
            ("desktop.world", Permission.SAFE),
            ("desktop.window.resolve", Permission.SAFE),
            ("vision.candidate.click", Permission.HIGH),
        ):
            registry.register(ToolSpec(name, "TEST", name, permission,
                                       {"type": "object"}, lambda _args, _context: {}))
        self.calls = []
        self.planner = TaskPlanner(registry, self.requester, self.bus, StateMachine(self.bus))

    async def requester(self, payload, _request_id):
        self.calls.append(payload)
        if self.error:
            raise RuntimeError(self.error)
        return {"status": "completed", "result": {"windows": []}}

    async def fail(self, reason, tool="desktop.world", returned=False):
        self.error = reason
        if returned:
            async def requester(payload, _request_id):
                self.calls.append(payload)
                return {"status": "failed", "result": {"error": reason}}
            self.planner.request_tool = requester
        plan = TaskPlan("failure", "failure", "Inspect the desktop", "Observe",
                        [self.planner._step("observe", tool, {}, "Observed state")])
        result = await self.planner.execute(plan)
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["goal_verified"])
        self.assertEqual(plan.failure_report["error"], reason)
        self.assertEqual(self.planner.state.current.value, "DORMANT")
        return plan

    async def test_policy_refusal_is_not_replayed_or_proposed_as_a_repair(self):
        reason = "Cloud provider disabled by privacy policy; select Carlos local routing"
        plan = await self.fail(reason)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(plan.recovery, [])
        gap = plan.capability_gaps[0]
        self.assertEqual(gap["type"], "PRIVACY_RESTRICTION")
        self.assertFalse(gap["engineering_task_available"])
        self.assertFalse(gap["requires_user_approval"])
        self.assertIn("keep that policy", gap["possible_solution"])

    async def test_returned_permission_refusal_is_not_replayed(self):
        plan = await self.fail("Permission denied by the desktop", returned=True)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(plan.recovery, [])
        gap = plan.capability_gaps[0]
        self.assertEqual(gap["type"], "MISSING_PERMISSION")
        self.assertFalse(gap["engineering_task_available"])

    async def test_missing_native_authorization_does_not_offer_a_coding_task(self):
        plan = await self.fail("Desktop authorization is required")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(plan.capability_gaps[0]["type"], "MISSING_AUTHORIZATION")
        self.assertFalse(plan.capability_gaps[0]["engineering_task_available"])
        gap = self.planner.capability_gap("desktop.input", "Disconnected", "MISSING_AUTHORIZATION")
        self.assertIn("native authorization dialog", gap["possible_solution"])

    async def test_stale_visual_candidate_retains_its_real_refusal(self):
        plan = await self.fail("Visual candidate changed; no click was sent", "vision.candidate.click")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(plan.recovery, [])
        self.assertEqual(plan.capability_gaps[0]["type"], "STALE_TARGET")
        self.assertFalse(plan.capability_gaps[0]["engineering_task_available"])

    async def test_closed_native_window_is_not_resolved_again(self):
        plan = await self.fail("The previously selected window is no longer available",
                               "desktop.window.resolve", returned=True)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(plan.recovery, [])
        self.assertEqual(plan.capability_gaps[0]["type"], "STALE_TARGET")
        self.assertFalse(plan.capability_gaps[0]["engineering_task_available"])

    async def test_unknown_click_delivery_is_not_treated_as_a_verified_effect(self):
        plan = await self.fail("Desktop input did not acknowledge delivery", "vision.candidate.click")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(plan.capability_gaps[0]["type"], "UNVERIFIED_RESULT")
        self.assertFalse(plan.capability_gaps[0]["engineering_task_available"])
        self.assertIn("not replay", plan.capability_gaps[0]["possible_solution"])

    async def test_transient_idempotent_observation_keeps_one_bounded_retry(self):
        async def requester(payload, _request_id):
            self.calls.append(payload)
            if len(self.calls) == 1:
                raise RuntimeError("Temporary bridge disconnect")
            return {"status": "completed", "result": {"windows": []}}
        self.planner.request_tool = requester
        plan = TaskPlan("retry", "retry", "Inspect the desktop", "Observe",
                        [self.planner._step("observe", "desktop.world", {}, "Observed state")])
        result = await self.planner.execute(plan)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(plan.recovery[0]["outcome"], "SUCCEEDED")
        self.assertEqual(plan.capability_gaps, [])

    async def test_unexpected_defect_still_retains_evidence_for_explicit_repair(self):
        plan = await self.fail("Unexpected bridge protocol mismatch")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(plan.capability_gaps[0]["type"], "BUG")
        self.assertTrue(plan.capability_gaps[0]["engineering_task_available"])
        self.assertTrue(plan.capability_gaps[0]["requires_user_approval"])

    def test_missing_dependencies_and_backend_are_setup_gaps(self):
        for kind in ("MISSING_DEPENDENCY", "MISSING_DESKTOP_BACKEND", "MISSING_PROVIDER"):
            gap = self.planner.capability_gap("fixture", "Unavailable", kind)
            self.assertFalse(gap["engineering_task_available"])
            self.assertFalse(gap["requires_user_approval"])
        unknown = self.planner.capability_gap("fixture", "Unknown", "UNRECOGNIZED")
        self.assertFalse(unknown["engineering_task_available"])


if __name__ == "__main__":
    unittest.main()
