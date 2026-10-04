import unittest
import time

from ev.activity import AgentActivity
from ev.events import Event
from ev.ipc.server import _is_responsive_request


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.activity = AgentActivity()

    def send(self, kind, **payload):
        return self.activity.consume(Event(1, kind, "test", payload, "task"))

    def test_model_prose_and_dispatch_are_not_verified_completion(self):
        self.send("task.started", task_id="task")
        self.send("ai.request_complete", response="I finished everything.")
        self.assertEqual(self.activity.snapshot()["phase"], "UNDERSTANDING")
        self.send("task.updated", status="completed", execution_status="EXECUTED_UNVERIFIED")
        self.assertEqual(self.activity.snapshot()["phase"], "FINISHED_UNVERIFIED")
        self.assertFalse(self.activity.snapshot()["goal_verified"])
        self.send("task.updated", status="completed", goal_verified=True)
        self.assertEqual(self.activity.snapshot()["phase"], "COMPLETED")

    def test_waiting_is_not_overwritten_by_child_observation_events(self):
        self.send("task.wait_state", phase="WAITING")
        self.send("tool.started", tool="audio.get_volume")
        self.send("core.state_changed", to="THINKING")
        self.assertEqual(self.activity.snapshot()["phase"], "WAITING")
        self.send("task.wait_state", phase="WAIT_FINISHED")
        self.send("task.updated", status="cancelled")
        self.assertEqual(self.activity.snapshot()["phase"], "CANCELLED")

    def test_progress_is_observed_steps_not_a_time_estimate(self):
        self.send("plan.created", steps=[{}, {}])
        self.send("plan.step_completed")
        self.send("voice.audio_level", waveform=[0.5])
        self.assertEqual(self.activity.snapshot()["steps_completed"], 1)
        self.assertEqual(self.activity.snapshot()["steps_total"], 2)
        self.send("plan.goal_verifying")
        self.assertEqual(self.activity.snapshot()["phase"], "VERIFYING")
        self.send("plan.recovering")
        self.assertEqual(self.activity.snapshot()["phase"], "RECOVERING")

    def test_dormant_audio_and_activity_echo_do_not_trigger_redraw(self):
        self.assertFalse(self.send("voice.audio_level", waveform=[0.4]))
        self.assertFalse(self.send("agent.activity", phase="FAKE"))
        self.send("core.state_changed", to="SPEAKING")
        self.send("core.state_changed", to="DORMANT")
        self.assertEqual(self.activity.snapshot()["phase"], "IDLE")

    def test_direct_diagnostic_does_not_leave_an_endless_processing_indicator(self):
        self.send("tool.started", tool="desktop.observe")
        self.assertEqual(self.activity.snapshot()["phase"], "OBSERVING")
        self.send("tool.completed", tool="desktop.observe")
        self.assertEqual(self.activity.snapshot()["phase"], "IDLE")

    def test_registered_observations_use_actual_readonly_metadata(self):
        for name in ("software.search", "audio.players", "browser.document", "new_backend.inspect"):
            self.send("tool.started", tool=name, read_only=True)
            self.assertEqual(self.activity.snapshot()["phase"], "OBSERVING")
        self.send("tool.started", tool="audio.media", read_only=False)
        self.assertEqual(self.activity.snapshot()["phase"], "EXECUTING")

    def test_goal_verification_remains_verifying_during_child_readbacks(self):
        self.send("task.started", task_id="task")
        self.send("plan.goal_verifying")
        self.send("tool.started", tool="files.hash", read_only=True)
        self.assertEqual(self.activity.snapshot()["phase"], "VERIFYING")
        self.send("tool.completed", tool="files.hash")
        self.assertEqual(self.activity.snapshot()["phase"], "VERIFYING")
        self.send("tool.started", tool="files.text.create", read_only=False)
        self.assertEqual(self.activity.snapshot()["phase"], "EXECUTING")

    def test_stop_interrupts_share_connection_without_reordering_normal_commands(self):
        for text in ("stop everything", "please cancel all tasks!", "abort all"):
            self.assertTrue(
                _is_responsive_request({"type": "command.submit", "payload": {"text": text}})
            )
        for text in (
            "open Firefox",
            "stop everything and delete my folder",
            "cancel my music subscription",
        ):
            self.assertFalse(
                _is_responsive_request({"type": "command.submit", "payload": {"text": text}})
            )

    def test_engineering_card_uses_only_observed_job_events(self):
        self.send("coding.started", proposal_id="job", project="/fixture", started_epoch=1)
        self.assertEqual(self.activity.snapshot()["engineering"]["state"], "RUNNING")
        self.send(
            "coding.progress", proposal_id="job", message="Updating the parser", files_changed=2
        )
        self.assertEqual(self.activity.snapshot()["engineering"]["files_changed"], 2)
        self.send("coding.failed", proposal_id="other", failure="Unrelated")
        self.assertEqual(self.activity.snapshot()["engineering"]["state"], "RUNNING")
        self.send("coding.cancelled", proposal_id="job")
        self.assertEqual(self.activity.snapshot()["engineering"]["state"], "CANCELLED")

    def waiting(self, **changes):
        payload = {"proposal_id": "a" * 32, "phase": "REVIEW_PROPOSAL",
                   "approval_required": True, "observed_at": time.time()}
        payload.update(changes)
        return Event(1, "coding.waiting", "coding_agent", payload, "a" * 32)

    def test_review_before_any_job_and_after_validation_is_truthful(self):
        self.assertTrue(self.activity.consume(self.waiting()))
        card = self.activity.snapshot()["engineering"]
        self.assertEqual(card["state"], "WAITING_FOR_USER")
        self.assertEqual(card["review_phase"], "REVIEW_PROPOSAL")
        self.send("coding.started", proposal_id="a" * 32)
        self.send("coding.completed", proposal_id="a" * 32,
                  status="VALIDATED_AWAITING_DEPLOYMENT_REVIEW")
        self.assertTrue(self.activity.consume(self.waiting(phase="REVIEW_DEPLOYMENT")))
        self.assertEqual(self.activity.snapshot()["engineering"]["review_phase"], "REVIEW_DEPLOYMENT")
        self.assertNotIn("project", self.activity.snapshot()["engineering"])

    def test_waiting_cannot_replace_a_live_job_or_revive_a_cancelled_job(self):
        self.send("coding.started", proposal_id="a" * 32)
        self.assertFalse(self.activity.consume(self.waiting()))
        self.assertFalse(self.activity.consume(self.waiting(proposal_id="b" * 32)))
        self.send("coding.cancelled", proposal_id="a" * 32)
        self.assertFalse(self.activity.consume(self.waiting()))

    def test_untrusted_private_stale_or_malformed_review_events_are_ignored(self):
        for field, value in (("phase", []), ("phase", "FAKE"), ("observed_at", True),
                             ("observed_at", time.time()-31), ("observed_at", time.time()+100),
                             ("observed_at", float("nan")), ("observed_at", 10**400),
                             ("proposal_id", "bad"), ("approval_required", 1), ("request", "secret")):
            self.assertFalse(self.activity.consume(self.waiting(**{field: value})))
        event = self.waiting()
        event.source = "model"
        self.assertFalse(self.activity.consume(event))
        event.source, event.private = "coding_agent", True
        self.assertFalse(self.activity.consume(event))

    def test_snapshot_cannot_mutate_nested_engineering_state(self):
        self.activity.consume(self.waiting())
        self.activity.snapshot()["engineering"]["state"] = "FAKE"
        self.assertEqual(self.activity.snapshot()["engineering"]["state"], "WAITING_FOR_USER")

    def test_codex_event_names_preserve_legacy_routing(self):
        for suffix, canonical in (("started", "started"), ("progress", "output"),
                                  ("waiting", "waiting"), ("completed", "finished"),
                                  ("failed", "failed"), ("cancelled", "cancelled")):
            event = Event(1, "coding." + suffix, "coding_agent", {}, "fixture").as_dict()
            self.assertEqual(event["type"], "coding." + suffix)
            self.assertEqual(event["name"], "codex." + canonical)
