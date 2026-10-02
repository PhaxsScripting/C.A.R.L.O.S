import asyncio
import json
import logging
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from ev.commands import direct_action
from ev.daily import DailyStore
from ev.desktop import DesktopWorldModel, KWinBridge
from ev.desktop.world import EntityResolutionError
from ev.events import PhaxEventBus
from ev.monitor_aliases import make_binding
from ev.paths import Paths
from ev.service import CarlosCore
from ev.tools import ToolContext
from ev.tools.base import ValidationError
from ev.tools.builtin import desktop_resolve_output, desktop_move_window_to_output
from ev.tools.monitor_aliases import save_alias, list_aliases, forget_alias
from ev.voice.normalization import normalize_transcript


class MonitorAliasTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = DailyStore(Path(self.temp.name) / 'daily.db')
        self.addCleanup(self.store.close)
        self.model = DesktopWorldModel(KWinBridge(Path('/missing'), logging.getLogger('test')))
        self.outputs = [
            {'name': 'eDP-1', 'enabled': True, 'primary': True, 'manufacturer': 'Dell', 'model': 'Laptop', 'serial_number': '',
             'geometry': {'x': 1920, 'y': 0}},
            {'name': 'HDMI-A-1', 'enabled': True, 'manufacturer': 'Fixture', 'model': 'Panel', 'serial_number': 'SERIAL_A',
             'geometry': {'x': 0, 'y': 0}},
        ]
        self.world = {'outputs': self.outputs, 'active_output': 'eDP-1'}
        self.model.snapshot = AsyncMock(side_effect=lambda **kwargs: self.world)
        self.context = ToolContext({}, PhaxEventBus(), logging.getLogger('test'), daily=self.store, desktop=self.model)

    async def test_saved_hardware_alias_follows_changed_port_and_stays_separate(self):
        result = await save_alias({'name': 'The big monitor', 'output_name': 'hdmi-a-1'}, self.context)
        self.assertTrue(result['verified'])
        self.assertEqual(result['binding'], 'HARDWARE')
        self.assertFalse(result['display_layout_changed'])
        self.outputs[1]['name'] = 'DP-2'
        resolved = await desktop_resolve_output({'description': 'the big monitor'}, self.context)
        self.assertEqual(resolved['output']['name'], 'DP-2')
        self.assertEqual(self.store.records('alias'), {})
        self.assertEqual(self.store.records('preference'), {})
        self.assertEqual(len(DailyStore(self.store.path).records('monitor_alias')), 1)

    async def test_disconnected_or_replaced_hardware_never_falls_back_to_connector(self):
        await save_alias({'name': 'big monitor', 'output_name': 'HDMI-A-1'}, self.context)
        self.outputs[1]['serial_number'] = 'REPLACEMENT_PANEL'
        result = await desktop_resolve_output({'description': 'big monitor'}, self.context)
        self.assertFalse(result['resolved'])
        self.assertIn('no fallback', result['error'])
        self.outputs[1]['enabled'] = False
        self.assertFalse((await desktop_resolve_output({'description': 'big monitor'}, self.context))['resolved'])

    async def test_duplicate_identity_is_ambiguous(self):
        await save_alias({'name': 'big monitor', 'output_name': 'HDMI-A-1'}, self.context)
        self.outputs.append({**self.outputs[1], 'name': 'DP-3'})
        result = await desktop_resolve_output({'description': 'big monitor'}, self.context)
        self.assertFalse(result['resolved'])
        self.assertEqual(len(result['candidates']), 2)

    async def test_missing_serial_uses_explicit_connector_binding(self):
        result = await save_alias({'name': 'small monitor', 'output_name': 'eDP-1'}, self.context)
        self.assertEqual(result['binding'], 'CONNECTOR')
        self.assertTrue(result['limitations'])
        self.outputs[0]['name'] = 'eDP-2'
        self.assertFalse((await desktop_resolve_output({'description': 'small monitor'}, self.context))['resolved'])

    async def test_placeholder_serials_do_not_claim_hardware_identity(self):
        for serial in ('0', '000000', '0x000', 'unknown', 'N/A', ''):
            with self.subTest(serial=serial):
                output = {**self.outputs[1], 'serial_number': serial}
                self.assertEqual(make_binding(output, [output])['binding'], 'CONNECTOR')

    async def test_replaced_or_duplicate_panel_between_resolution_and_move_stops_before_bridge(self):
        from copy import deepcopy

        window = {'id': 'owned', 'title': 'Fixture', 'app_id': 'fixture', 'normal': True, 'special': False, 'output': 'eDP-1',
                  'geometry': {'width': 800, 'height': 600}}
        self.world['windows'] = [window]
        self.model.bridge.request = AsyncMock()
        expected = deepcopy(self.outputs[1])
        for change in ('replacement', 'duplicate'):
            with self.subTest(change=change):
                if change == 'replacement':
                    self.outputs[1]['serial_number'] = 'NEW_PANEL'
                else:
                    self.outputs[1]['serial_number'] = 'SERIAL_A'
                    self.outputs.append({**self.outputs[1], 'name': 'DP-3'})
                with self.assertRaisesRegex(ValidationError, 'identity changed or became ambiguous'):
                    await desktop_move_window_to_output({'window_id': 'owned', 'output': 'HDMI-A-1',
                                                         'expected_output': expected}, self.context)
                self.model.bridge.request.assert_not_awaited()
                self.assertEqual(window['output'], 'eDP-1')

    async def test_identity_change_after_dispatch_cannot_claim_verified_placement(self):
        from copy import deepcopy

        expected = deepcopy(self.outputs[1])
        window = {'id': 'owned', 'title': 'Fixture', 'app_id': 'fixture', 'normal': True, 'special': False, 'output': 'eDP-1',
                  'geometry': {'width': 800, 'height': 600}}
        self.world['windows'] = [window]

        async def move(*args, **kwargs):
            window['output'] = 'HDMI-A-1'
            self.outputs[1]['serial_number'] = 'NEW_PANEL'

        self.model.bridge.request = AsyncMock(side_effect=move)
        result = await desktop_move_window_to_output({'window_id': 'owned', 'output': 'HDMI-A-1',
                                                     'expected_output': expected}, self.context)
        self.assertFalse(result['verified'])
        self.assertTrue(result['identity_guarded'])
        self.assertFalse(result['identity_verified'])
        self.assertEqual(self.model.bridge.request.await_args.args[1]['expected_output_identity']['serial_number'], 'SERIAL_A')

    async def test_aliases_cannot_replace_dynamic_context_or_connector_names(self):
        for name in ('current', 'this monitor', 'other monitor', 'eDP-1', 'x; reboot'):
            with self.subTest(name=name), self.assertRaises(ValidationError):
                await save_alias({'name': name, 'output_name': 'HDMI-A-1'}, self.context)
        self.assertEqual(self.store.records('monitor_alias'), {})

    async def test_list_reports_current_binding_and_forget_only_exact_alias(self):
        await save_alias({'name': 'big monitor', 'output_name': 'HDMI-A-1'}, self.context)
        await save_alias({'name': 'small monitor', 'output_name': 'eDP-1'}, self.context)
        self.outputs[1]['enabled'] = False
        result = await list_aliases({}, self.context)
        self.assertFalse(result['aliases'][0]['resolved'])
        self.assertTrue(result['aliases'][1]['resolved'])
        self.assertTrue((await forget_alias({'name': 'the big monitor'}, self.context))['verified'])
        self.assertEqual(set(self.store.records('monitor_alias')), {'small monitor'})

    async def test_private_alias_changes_do_not_reach_disk_and_guest_snapshot_has_none(self):
        self.store.set_private(True)
        await save_alias({'name': 'big monitor', 'output_name': 'HDMI-A-1'}, self.context)
        self.assertTrue(self.store.records('monitor_alias'))
        self.assertEqual(DailyStore(self.store.path).records('monitor_alias'), {})
        self.store.set_private(False)
        self.assertEqual(self.store.records('monitor_alias'), {})
        await save_alias({'name': 'big monitor', 'output_name': 'HDMI-A-1'}, self.context)
        self.store.set_private(True, guest=True)
        self.assertEqual(self.store.records('monitor_alias'), {})

    async def test_equal_horizontal_edges_do_not_guess_between_vertical_monitors(self):
        self.outputs[0]['geometry']['x'] = 0
        for description in ('left monitor', 'right monitor'):
            with self.assertRaises(EntityResolutionError):
                self.model.resolve_output(description, self.world)

    async def test_unknown_active_monitor_does_not_choose_first_of_multiple_outputs(self):
        self.world['active_output'] = ''
        with self.assertRaises(EntityResolutionError):
            self.model.resolve_output('current monitor', self.world)
        self.outputs.pop()
        self.assertEqual(self.model.resolve_output('current monitor', self.world)['name'], 'eDP-1')

    async def test_explicit_connector_name_precedes_topology_keywords(self):
        self.outputs[1]['name'] = 'left-HDMI-A-1'
        self.outputs[1]['geometry']['x'] = 4000
        self.assertEqual(self.model.resolve_output('left-HDMI-A-1', self.world)['name'], 'left-HDMI-A-1')

    async def test_explicit_natural_naming_routes_without_model_or_os_commands(self):
        for phrase, tool, arguments in [
            ('name monitor HDMI-A-1 big monitor', 'desktop.output.alias.save', {'output_name': 'HDMI-A-1', 'name': 'big monitor'}),
            ('show my monitor names', 'desktop.output.alias.list', {}),
            ('forget monitor name big monitor', 'desktop.output.alias.forget', {'name': 'big monitor'}),
        ]:
            action = direct_action(phrase)
            self.assertIsNotNone(action)
            self.assertEqual(action.tool, tool)
            self.assertEqual(action.arguments, arguments)
        for phrase in ('do not name monitor HDMI-A-1 big monitor', 'how do I name monitor HDMI-A-1 big monitor?'):
            self.assertIsNone(direct_action(phrase))


class MonitorCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_core_commands_follow_alias_and_refuse_disconnected_panel(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            core = CarlosCore(paths=Paths(*(root / name for name in ('config', 'data', 'state', 'cache', 'run'))))
            panel = {'name': 'HDMI-A-1', 'enabled': True, 'manufacturer': 'Fixture',
                     'model': 'Panel', 'serial_number': 'SERIAL_A', 'geometry': {'x': 0, 'y': 0}}
            window = {'id': 'fixture-firefox', 'title': 'Fixture', 'app_id': 'firefox',
                      'resource_class': 'firefox', 'normal': True, 'special': False,
                      'output': 'eDP-1', 'geometry': {'x': 2000, 'y': 0, 'width': 800, 'height': 600}}
            world = {'outputs': [panel, {'name': 'eDP-1', 'enabled': True}],
                     'windows': [window], 'active_window_id': window['id'], 'active_output': 'eDP-1'}
            core.desktop.snapshot = AsyncMock(side_effect=lambda **kwargs: world)
            mutations = []

            async def bridge(action, arguments, **kwargs):
                self.assertEqual(action, 'move_to_output')
                self.assertEqual(arguments['window_id'], window['id'])
                mutations.append(dict(arguments))
                window['output'] = arguments['output']
                return {}

            core.desktop.bridge.request = AsyncMock(side_effect=bridge)
            core.brain.provider.begin = AsyncMock(side_effect=AssertionError('These commands must stay local'))
            writer = None
            try:
                await core.ipc.start()
                reader, writer = await asyncio.open_unix_connection(core.paths.socket)
                await reader.readline()
                count = 0

                async def request(kind, payload):
                    nonlocal count
                    count += 1
                    writer.write((json.dumps({'type': kind, 'id': str(count), 'payload': payload}) + '\n').encode())
                    await writer.drain()
                    while True:
                        reply = json.loads(await asyncio.wait_for(reader.readline(), 3))
                        if reply.get('id') == str(count):
                            self.assertNotEqual(reply['type'], 'error', reply)
                            return reply['payload']

                async def command(text):
                    return await request('command.submit', {'text': text, 'speak': False})

                saved = await command('name monitor HDMI-A-1 big monitor')
                self.assertEqual(saved['status'], 'completed', saved)
                listed = await command('show my monitor names')
                self.assertEqual(listed['status'], 'completed', listed)
                self.assertIn('big monitor', listed['response'])
                panel['name'] = 'DP-2'
                moved = await command(normalize_transcript('Carlos, put Firefox on the big monitor.'))
                self.assertEqual(moved['status'], 'completed', moved)
                self.assertEqual(window['output'], 'DP-2')
                self.assertEqual(moved['plan']['steps'][-1]['actual_result']['result']['identity_verified'], True)
                self.assertEqual(len(mutations), 1)
                panel['enabled'] = False
                refused = await command('put Firefox on the big monitor')
                self.assertNotEqual(refused['status'], 'completed', refused)
                self.assertEqual(len(mutations), 1)
                core.config['carlos']['privacy_mode'] = 'GUEST'
                core.privacy.apply_storage()
                denied = await request('tool.call', {'name': 'desktop.output.alias.list', 'arguments': {}})
                self.assertEqual(denied['status'], 'denied')
                denied_command = await command('name monitor eDP-1 small monitor')
                self.assertEqual(denied_command['status'], 'failed', denied_command)
                self.assertEqual(denied_command['plan']['steps'][0]['actual_result']['status'], 'denied')
                self.assertEqual(core.daily.records('monitor_alias'), {})
                self.assertEqual(len(mutations), 1)
                core.brain.provider.begin.assert_not_awaited()
            finally:
                if writer:
                    writer.close()
                    await writer.wait_closed()
                await core.ipc.stop()
                core.daily.close()
                core.memory.close()
                core.task_journal.close()
