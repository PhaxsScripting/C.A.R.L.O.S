import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from ev.security_monitor import SecurityMonitor
from ev.security_sources import SecuritySources, command, fingerprint, observation


class SecuritySourceTests(unittest.IsolatedAsyncioTestCase):
    def sources(self, code=0, out="", manager="openrc"):
        runner = AsyncMock(return_value={"code": code, "out": out, "err": ""})
        return SecuritySources(SimpleNamespace(), runner=runner, manager=manager), runner

    async def test_loopback_range_and_restarts_are_not_new_network_listeners(self):
        source, runner = self.sources(out='tcp LISTEN 0 128 127.0.3.4:9000 0.0.0.0:*\ntcp LISTEN 0 128 [::1]:9001 [::]:*\ntcp LISTEN 0 128 0.0.0.0:80 0.0.0.0:* users:(("server",pid=111,fd=4))\n')
        first = await source.listeners()
        self.assertEqual(first["count"], 1)
        runner.return_value["out"] = runner.return_value["out"].replace("pid=111", "pid=222")
        self.assertEqual((await source.listeners())["items"], first["items"])

    async def test_malformed_and_failed_listener_read_is_not_empty_known_inventory(self):
        source, runner = self.sources(out="error banner")
        self.assertEqual((await source.listeners())["status"], "INCOMPLETE")
        runner.return_value["code"] = 1
        self.assertEqual((await source.listeners())["status"], "UNAVAILABLE")

    async def test_tailscale_tracks_stable_ids_not_online_changes(self):
        data = {"BackendState": "Running", "Self": {"ID": "self-id"},
                "Peer": {"nodekey:old": {"ID": "peer-id", "Online": True, "HostName": "PRIVATE_CANARY"}}}
        source, runner = self.sources(out=json.dumps(data))
        first = await source.tailscale()
        data["Peer"] = {"nodekey:new": {"ID": "peer-id", "Online": False}}
        runner.return_value["out"] = json.dumps(data)
        self.assertEqual(first["items"], (await source.tailscale())["items"])
        self.assertNotIn("PRIVATE_CANARY", json.dumps(first))
        data["BackendState"] = "Stopped"
        runner.return_value["out"] = json.dumps(data)
        self.assertEqual((await source.tailscale())["status"], "UNAVAILABLE")

    async def test_missing_btmp_is_unknown_and_failed_rows_are_fingerprinted(self):
        source, runner = self.sources(code=1)
        self.assertEqual((await source.failed_logins())["status"], "UNAVAILABLE")
        runner.return_value = {"code": 0, "err": "", "out": "PRIVATE_CANARY ssh:notty 192.0.2.8 2026-10-01T12:00:00+00:00 - 2026-10-01T12:00:00+00:00 (00:00)\n\nbtmp begins 2026-10-01T12:00:00+00:00\n"}
        result = await source.failed_logins()
        self.assertEqual(result["count"], 1)
        self.assertNotIn("PRIVATE_CANARY", json.dumps(result))

    async def test_openrc_no_crashed_exit_and_systemd_failures(self):
        source, runner = self.sources(code=1)
        self.assertEqual((await source.services())["status"], "OK")
        runner.return_value = {"code": 0, "err": "", "out": "smartd\nchronyd\n"}
        self.assertEqual((await source.services())["items"], {"smartd": "CRASHED", "chronyd": "CRASHED"})
        source.manager = "systemd"
        runner.return_value["out"] = "example.service loaded failed failed Example daemon\n"
        self.assertEqual((await source.services())["items"], {"example.service": "FAILED"})

    async def test_readable_nft_does_not_claim_effective_protection(self):
        source, runner = self.sources(out='{"nftables": [{"metainfo": {}}, {"rule": {"handle": 2}}]}')
        result = await source.firewall()
        self.assertEqual(result["rule_count"], 1)
        self.assertNotIn("protected", result)
        runner.return_value["code"] = 1
        self.assertEqual((await source.firewall())["status"], "UNAVAILABLE")

    async def test_firewall_service_fallback_retains_the_unverified_filtering_boundary(self):
        async def run(argv):
            if argv[0] == 'nft':
                return {'code': 1, 'out': '', 'err': 'Permission denied'}
            if argv[1] == 'nftables':
                return {'code': 0, 'out': 'nftables [ started ]', 'err': ''}
            return {'code': 1, 'out': '', 'err': 'service does not exist'}
        source = SecuritySources(SimpleNamespace(), runner=run, manager='openrc')
        result = await source.firewall()
        self.assertEqual(result['status'], 'OK')
        self.assertEqual(result['active_service_count'], 1)
        self.assertIsNone(result['filtering_verified'])

    async def test_running_tailnet_with_no_peers_is_a_known_empty_inventory(self):
        source, _ = self.sources(out='{"BackendState":"Running","Self":{"ID":"self"},"Peer":null}')
        result = await source.tailscale()
        self.assertEqual(result['status'], 'OK')
        self.assertEqual(result['count'], 0)

    async def test_smart_nonzero_health_failure_and_unreadable_second_disk(self):
        source, runner = self.sources()
        runner.side_effect = [
            {"code": 0, "out": json.dumps({"devices": [{"name": "/dev/nvme0", "type": "nvme"}, {"name": "/dev/sda", "type": "ata"}]}), "err": ""},
            {"code": 8, "out": '{"smart_status":{"passed":false}}', "err": ""},
            {"code": 2, "out": '{"smart_status":{"passed":true}}', "err": ""},
        ]
        result = await source.smart()
        self.assertEqual(result["failed_count"], 1)
        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertEqual(result["items"][fingerprint("/dev/sda")], "UNAVAILABLE")
        for call in runner.call_args_list[1:]:
            self.assertIn("standby,2,2", call.args[0])
            self.assertIn("-d", call.args[0])
            self.assertNotIn("sudo", call.args[0])

    async def test_smart_unknown_power_and_missing_health_are_not_passed(self):
        source, runner = self.sources()
        runner.side_effect = [{"code": 0, "out": '{"devices":[{"name":"/dev/sda","type":"ata"}]}', "err": ""},
                              {"code": 0, "out": '{}', "err": ""}]
        self.assertEqual((await source.smart())["items"][fingerprint("/dev/sda")], "UNAVAILABLE")

    async def test_startup_reads_are_bounded_and_do_not_follow_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            source = SecuritySources(SimpleNamespace(), home=Path(directory))
            (source.home / ".bashrc").write_text("PRIVATE_CANARY")
            (source.home / ".profile").symlink_to(source.home / "secret")
            first = source.startup()
            self.assertEqual(first["status"], "OK")
            self.assertNotIn("PRIVATE_CANARY", json.dumps(first))
            (source.home / ".bashrc").write_bytes(b"x" * 65537)
            self.assertEqual(source.startup()["status"], "INCOMPLETE")

    async def test_real_probe_timeout_output_cap_and_cancellation_leave_no_tasks(self):
        initial = asyncio.all_tasks()
        result = await command([sys.executable, "-c", "import time; time.sleep(10)"], timeout=.05)
        self.assertIsNone(result["code"])
        result = await command([sys.executable, "-c", "print('x'*100000)"], maximum=100)
        self.assertIsNone(result["code"])
        task = asyncio.create_task(command([sys.executable, "-c", "import time; time.sleep(10)"]))
        await asyncio.sleep(.05)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)
        self.assertEqual(asyncio.all_tasks(), initial)


class SecurityMonitorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.bus = Mock()
        self.token = ("NORMAL", False, 0)
        self.monitor = SecurityMonitor(None, self.bus, {"critical_services": ["smartd"]},
                                       Path(self.directory.name) / "baseline.json", lambda: self.token, sources=Mock())

    async def test_new_observations_are_info_and_not_repeated(self):
        for name in ("listeners", "tailscale", "failed_logins"):
            self.monitor.accept(name, observation("fixture", {"old": "VISIBLE"}))
            self.monitor.accept(name, observation("fixture", {"old": "VISIBLE", "new": "VISIBLE"}))
            self.monitor.accept(name, observation("fixture", {"old": "VISIBLE", "new": "VISIBLE"}))
        self.assertEqual(self.bus.publish.call_count, 3)
        self.assertTrue(all(call.args[0] == "security.observed" for call in self.bus.publish.call_args_list))

    async def test_unknown_inventory_never_replaces_known_baseline(self):
        self.monitor.accept("listeners", observation("fixture", {"a": "NETWORK_BOUND"}))
        self.monitor.accept("listeners", observation("fixture", status="UNAVAILABLE"))
        self.monitor.accept("listeners", observation("fixture", {"a": "NETWORK_BOUND"}))
        self.bus.publish.assert_not_called()

    async def test_tailscale_account_change_establishes_new_baseline(self):
        self.monitor.accept("tailscale", observation("fixture", {"old": "VISIBLE"}, scope="account-a"))
        self.monitor.accept("tailscale", observation("fixture", {"new": "VISIBLE"}, scope="account-b"))
        self.bus.publish.assert_not_called()

    async def test_critical_service_failure_deduplicates_and_rearms_after_recovery(self):
        for items in ({"smartd": "CRASHED"}, {"smartd": "CRASHED"}, {}, {"smartd": "CRASHED"}):
            self.monitor.accept("services", observation("fixture", items))
        self.assertEqual(self.bus.publish.call_count, 2)
        self.assertTrue(all(call.args[0] == "security.alert" for call in self.bus.publish.call_args_list))

    async def test_partial_smart_failure_alerts_without_claiming_other_disk_health(self):
        result = observation("fixture", {"a": "FAILED", "b": "UNAVAILABLE"}, status="INCOMPLETE")
        self.monitor.accept("smart", result)
        self.monitor.accept("smart", result)
        self.bus.publish.assert_called_once()
        self.assertEqual(self.bus.publish.call_args.args[0], "security.alert")
        self.assertEqual(self.monitor.snapshot()["sources"]["smart"]["status"], "INCOMPLETE")

    async def test_startup_baseline_survives_restart_with_only_private_fingerprints(self):
        self.monitor.accept("startup", observation("fixture", {fingerprint("path"): fingerprint("PRIVATE_CANARY")}))
        self.bus.publish.assert_not_called()
        content = self.monitor.path.read_text()
        self.assertNotIn("PRIVATE_CANARY", content)
        self.assertEqual(os.stat(self.monitor.path).st_mode & 0o777, 0o600)
        next_monitor = SecurityMonitor(None, self.bus, {}, self.monitor.path, lambda: self.token, sources=Mock())
        next_monitor.load_baseline()
        next_monitor.accept("startup", observation("fixture", {fingerprint("path"): fingerprint("new content")}))
        self.bus.publish.assert_called_once()
        self.assertEqual(self.bus.publish.call_args.args[2]["count"], 1)

    async def test_insecure_baseline_is_not_trusted(self):
        self.monitor.path.write_text(json.dumps({"version": 1, "startup": {}}))
        self.monitor.path.chmod(0o644)
        self.monitor.load_baseline()
        self.assertEqual(self.monitor.snapshot()["startup_baseline"], "UNAVAILABLE")

    async def test_private_guest_and_transition_skip_probes_and_hide_old_status(self):
        self.monitor.observed["services"] = {"status": "OK", "count": 3}
        for token in (("PRIVATE SESSION", False, 1), ("GUEST", False, 2), ("NORMAL", True, 3)):
            self.token = token
            await self.monitor.poll()
            self.assertEqual(self.monitor.snapshot()["sources"], {})
        self.monitor.sources.assert_not_called()
        self.assertFalse(self.monitor.path.exists())

    async def test_privacy_change_during_probe_discards_all_results_and_writes(self):
        async def collect():
            self.token = ("NORMAL", False, 99)
            return observation("fixture", {fingerprint("path"): fingerprint("content")})
        for name in ("listeners", "tailscale", "failed_logins", "services", "firewall", "smart", "updates"):
            setattr(self.monitor.sources, name, AsyncMock(side_effect=collect))
        self.monitor.sources.startup.return_value = observation("fixture", {fingerprint("path"): fingerprint("content")})
        await self.monitor.poll()
        self.assertEqual(self.monitor.observed, {})
        self.assertFalse(self.monitor.path.exists())
        self.bus.publish.assert_not_called()

    async def test_disable_during_probe_discards_results(self):
        async def collect():
            self.monitor.config['enabled'] = False
            return observation('fixture')
        for name in ('listeners', 'tailscale', 'failed_logins', 'services', 'firewall', 'smart', 'updates'):
            setattr(self.monitor.sources, name, AsyncMock(side_effect=collect))
        self.monitor.sources.startup.return_value = observation('fixture')
        await self.monitor.poll()
        self.assertFalse(self.monitor.path.exists())
        self.assertEqual(self.monitor.snapshot()['state'], 'DISABLED')
        self.bus.publish.assert_not_called()
