import unittest
from types import SimpleNamespace
from unittest.mock import patch
from ev.events import PhaxEventBus
from ev.telemetry import TelemetrySampler


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        self.bus = PhaxEventBus()
        self.sampler = TelemetrySampler(self.bus)

    def test_loopback_is_not_a_network_connection(self):
        interfaces = {"lo": SimpleNamespace(isup=True, flags="up,loopback,running"),
                      "wlan0": SimpleNamespace(isup=False, flags="broadcast")}
        with patch("ev.telemetry.psutil.net_if_stats", return_value=interfaces):
            network = self.sampler.sample()["network"]
        self.assertEqual(network["link_state"], "DOWN")
        self.assertEqual(network["connected_interfaces"], 0)

    def test_link_up_does_not_claim_internet_access(self):
        with patch("ev.telemetry.psutil.net_if_stats", return_value={"eth0": SimpleNamespace(isup=True, flags="broadcast")}):
            network = self.sampler.sample()["network"]
        self.assertEqual(network["link_state"], "UP")
        self.assertEqual(network["internet_reachability"], "UNVERIFIED")

    def test_counter_reset_cannot_report_negative_traffic(self):
        self.sampler._last_network = SimpleNamespace(bytes_recv=1000, bytes_sent=1000)
        with patch("ev.telemetry.psutil.net_io_counters", return_value=SimpleNamespace(bytes_recv=0, bytes_sent=0)):
            network = self.sampler.sample()["network"]
        self.assertEqual(network["download_bytes_per_second"], 0)
        self.assertEqual(network["upload_bytes_per_second"], 0)

    def test_transitions_report_only_changes_not_initial_state(self):
        def observe(link, plugged):
            self.sampler.observe_transitions({"network":{"link_state":link}, "battery":{"plugged":plugged}})
        observe("UP", True); observe("UP", True)
        self.assertEqual(self.bus.history(), [])
        observe("DOWN", False); observe("DOWN", False); observe("UP", True)
        history = self.bus.history()
        self.assertEqual([e["type"] for e in history], ["system.network_link_changed", "system.power_source_changed"]*2)
        self.assertEqual(history[0]["payload"]["to"], "DOWN")
        self.assertEqual(history[1]["payload"]["to"], "BATTERY")

    def test_missing_battery_is_unavailable_not_external_power(self):
        self.sampler.observe_transitions({"network":{"link_state":"UP"}, "battery":None})
        self.assertEqual(self.sampler._last_power, "UNAVAILABLE")
