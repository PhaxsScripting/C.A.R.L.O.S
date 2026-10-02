import copy
import logging
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from ev.commands import direct_action
from ev.events import PhaxEventBus
from ev.tools import ToolContext, ToolRegistry, register_builtin_tools
from ev.tools import audio_undo, builtin


class AudioUndoTests(unittest.TestCase):
    def setUp(self):
        self.context = ToolContext({}, PhaxEventBus(), logging.getLogger('audio-undo-test'))
        self.server = (1, 2, 3)
        self.accept = True
        self.default = 'original'
        self.writes = []
        self.row = {'name': 'original', 'index': 51, 'channel_map': 'front-left,front-right',
                    'properties': {'object.serial': '51'}, 'mute': False,
                    'volume': {'front-left': {'value': 22938, 'value_percent': '35%'},
                               'front-right': {'value': 42598, 'value_percent': '65%'}}}

        def listing(args):
            return [copy.deepcopy(self.row)]

        def run(args, **kwargs):
            if args[1] == 'get-default-sink':
                return {'ok': True, 'stdout': self.default + '\n'}
            self.writes.append(args)
            if self.accept:
                if args[1] == 'set-sink-mute':
                    self.row['mute'] = args[3] == '1'
                else:
                    for key, requested in zip(self.row['volume'], args[3:] if len(args) > 4 else args[3:] * 2):
                        value = round(float(requested[:-1]) * 65536 / 100) if requested.endswith('%') else int(requested)
                        self.row['volume'][key] = {'value': value,
                                                  'value_percent': f'{round(value * 100 / 65536)}%'}
            return {'ok': self.accept, 'stdout': '', 'stderr': ''}

        for target, value in [('ev.tools.builtin._pactl_json', listing),
                              ('ev.tools.builtin.run_command', run),
                              ('ev.tools.audio_undo.server_identity', lambda: self.server)]:
            mocked = patch(target, side_effect=value)
            mocked.start()
            self.addCleanup(mocked.stop)

    def change(self, percent=30):
        return builtin.set_volume({'percent': percent}, self.context)

    def undo(self):
        return audio_undo.undo_last({}, self.context)

    def test_exact_channel_balance_is_restored_without_unmuting_or_switching_default(self):
        before = copy.deepcopy(self.row['volume'])
        self.assertTrue(self.change()['undo_available'])
        self.row['mute'] = True
        self.default = 'another-output'
        self.assertTrue(self.undo()['verified'])
        self.assertEqual(self.row['volume'], before)
        self.assertTrue(self.row['mute'])
        self.assertEqual(self.default, 'another-output')
        self.assertEqual(self.writes[-1][2:], ['51', '22938', '42598'])
        self.assertFalse(self.context.audio_undo)

    def test_mute_undo_preserves_later_volume_change(self):
        self.assertTrue(builtin.set_mute({'muted': True}, self.context)['undo_available'])
        self.row['volume']['front-left']['value'] = 12000
        self.assertTrue(self.undo()['verified'])
        self.assertFalse(self.row['mute'])
        self.assertEqual(self.row['volume']['front-left']['value'], 12000)

    def test_specific_undo_request_cannot_restore_a_different_setting(self):
        self.change()
        count = len(self.writes)
        result = audio_undo.undo_last({'field': 'mute'}, self.context)
        self.assertFalse(result['verified'])
        self.assertEqual(len(self.writes), count)
        self.assertEqual(len(self.context.audio_undo), 1)
        self.assertTrue(audio_undo.undo_last({'field': 'volume'}, self.context)['verified'])

    def test_chained_adjustments_undo_in_order_and_noop_does_not_hide_previous_change(self):
        original = copy.deepcopy(self.row['volume'])
        self.change(30)
        self.assertTrue(builtin.adjust_volume({'delta': 10}, self.context)['undo_available'])
        self.assertFalse(self.change(40)['undo_available'])
        self.assertEqual(len(self.context.audio_undo), 2)
        self.assertTrue(self.undo()['verified'])
        self.assertEqual(builtin.get_volume({}, None)['percent'], 30)
        self.assertTrue(self.undo()['verified'])
        self.assertEqual(self.row['volume'], original)

    def test_manual_adjustment_refuses_without_any_write(self):
        self.change()
        self.row['volume']['front-right']['value'] += 1
        count = len(self.writes)
        result = self.undo()
        self.assertFalse(result['verified'])
        self.assertTrue(result['discarded'])
        self.assertEqual(len(self.writes), count)

    def test_server_restart_device_replacement_or_channel_change_refuses(self):
        for kind in ('server', 'index', 'serial', 'channels', 'removed'):
            with self.subTest(kind=kind):
                self.context.audio_undo.clear()
                original = copy.deepcopy(self.row)
                self.change(30 if builtin.get_volume({}, None)['percent'] != 30 else 40)
                old_server = self.server
                if kind == 'server':
                    self.server = (1, 2, 4)
                elif kind == 'index':
                    self.row['index'] += 1
                elif kind == 'serial':
                    self.row['properties']['object.serial'] = '999'
                elif kind == 'channels':
                    self.row['channel_map'] = 'front-right,front-left'
                else:
                    self.row['name'] = 'replacement'
                count = len(self.writes)
                self.assertFalse(self.undo()['verified'])
                self.assertEqual(len(self.writes), count)
                self.row = original
                self.server = old_server

    def test_history_expires_is_bounded_and_isolated_between_contexts(self):
        for n in range(40):
            self.change(30 + n % 2)
        self.assertEqual(len(self.context.audio_undo), 32)
        other = ToolContext({}, PhaxEventBus(), logging.getLogger('other-audio-context'))
        self.assertFalse(audio_undo.undo_last({}, other)['verified'])
        self.context.audio_undo[-1]['created'] -= 601
        count = len(self.writes)
        self.assertFalse(self.undo()['verified'])
        self.assertEqual(len(self.writes), count)

    def test_failed_restoration_can_be_inspected_and_retried_without_fake_success(self):
        self.change()
        self.accept = False
        self.assertFalse(self.undo()['verified'])
        self.assertEqual(len(self.context.audio_undo), 1)
        self.accept = True
        self.assertTrue(self.undo()['verified'])

    def test_missing_metadata_or_unsafe_original_volume_does_not_prevent_normal_control(self):
        for value in (None, True, -1, 65537):
            with self.subTest(value=value):
                self.context.audio_undo.clear()
                self.row['volume']['front-left']['value'] = value
                result = self.change(20)
                self.assertTrue(result['verified'])
                self.assertFalse(result['undo_available'])
        self.row.pop('index')
        self.assertTrue(self.change(35)['verified'])
        self.assertFalse(self.context.audio_undo)

    def test_no_change_or_failed_write_does_not_claim_reversibility(self):
        self.accept = False
        self.assertFalse(self.change()['undo_available'])
        self.assertFalse(self.context.audio_undo)
        self.assertFalse(self.undo()['restored'])

    def test_only_explicit_audio_undo_requests_route_to_audio_tool(self):
        for phrase in ('undo volume', 'revert my last mute change', 'undo the audio adjustment'):
            self.assertEqual(direct_action(phrase).tool, 'audio.undo_last')
        self.assertEqual(direct_action('undo volume').arguments, {'field': 'volume'})
        self.assertEqual(direct_action('undo mute').arguments, {'field': 'mute'})
        for phrase in ('undo that', 'undo typing', 'do not undo volume', 'explain undo volume',
                       'undo Spotify volume', 'undo volume and open Firefox', 'type "undo volume"'):
            result = direct_action(phrase)
            self.assertTrue(result is None or result.tool != 'audio.undo_last', phrase)
        registry = ToolRegistry(self.context)
        register_builtin_tools(registry)
        spec, _ = registry.validate('audio.undo_last', {})
        self.assertEqual(spec.public()['contract_gaps'], [])
        self.assertFalse(spec.cancellable)
        self.assertFalse(spec.reversible)
        for name in ('audio.get_volume', 'audio.set_volume', 'audio.adjust_volume', 'audio.set_mute'):
            self.assertEqual(registry.get(name).public()['contract_gaps'], [])


class NativeAudioUndoTests(unittest.TestCase):
    @unittest.skipUnless(all(shutil.which(name) for name in ('pipewire', 'pipewire-pulse', 'pactl', 'pw-metadata')),
                         'Native PipeWire tools unavailable')
    def test_real_isolated_server_volume_balance_mute_and_override(self):
        result = subprocess.run([sys.executable, str(Path(__file__).with_name('fixtures') / 'audio_undo_live.py')],
                                capture_output=True, text=True, timeout=25)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('isolated audio undo passed', result.stdout)
