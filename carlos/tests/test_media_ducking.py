import asyncio
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
from ev.events import PhaxEventBus
from ev.state import StateMachine
from ev.voice.manager import VoiceManager
from ev.voice.media_focus import MediaFocus, MediaDucking


class DuckingTests(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(shutil.which('dbus-run-session') and shutil.which('qdbus6')
                         and shutil.which('dbus-send'), 'D-Bus tools unavailable')
    def test_real_bus_volume_write_restore_and_override(self):
        result = subprocess.run([
            'dbus-run-session', '--', sys.executable,
            str(Path(__file__).with_name('fixtures') / 'media_ducking_live.py')],
            env=os.environ, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('user override passed', result.stdout)

    def setUp(self):
        self.focus = MediaFocus(AsyncMock(), dict)
        self.service = 'org.mpris.MediaPlayer2.fixture'
        self.owner = ':1.22'
        self.volume = .8
        self.status = 'Playing'
        self.names = '\n'.join([f':1.{i}' for i in range(40)] + [self.service])
        self.actions = []

        async def run(*args):
            if len(args) == 1:
                return self.names
            self.actions.append(args)
            self.volume = float(args[-1].rsplit(':', 1)[-1])
            return ''

        self.focus._run = AsyncMock(side_effect=run)
        self.focus._owner = AsyncMock(side_effect=lambda _: self.owner)
        self.focus._property = AsyncMock(side_effect=lambda _, key:
                                        self.status if key == 'PlaybackStatus' else str(self.volume))
        self.duck = MediaDucking(self.focus)

    async def test_lower_restore_only_volume_and_exact_owner(self):
        await self.duck.begin()
        self.assertAlmostEqual(self.volume, .28)
        await self.duck.begin()
        self.assertEqual(len(self.actions), 1)
        self.assertTrue(await self.duck.restore())
        self.assertEqual(self.volume, .8)
        self.assertEqual(len(self.actions), 2)
        for action in self.actions:
            self.assertIn('--dest=:1.22', action)
            self.assertEqual(action[-2], 'string:Volume')

    async def test_user_volume_change_is_preserved(self):
        await self.duck.begin()
        self.volume = .6
        self.assertTrue(await self.duck.restore())
        self.assertEqual(self.volume, .6)
        self.assertEqual(len(self.actions), 1)

    async def test_replacement_and_closed_players_are_not_restored(self):
        await self.duck.begin()
        self.owner = ':1.99'
        self.assertTrue(await self.duck.restore())
        self.assertEqual(len(self.actions), 1)
        await self.duck.begin()
        self.names = ''
        self.assertTrue(await self.duck.restore())
        self.assertEqual(len(self.actions), 2)

    async def test_muted_paused_and_invalid_volumes_unchanged(self):
        for volume, state in [(0, 'Playing'), (.8, 'Paused'), (float('nan'), 'Playing'),
                              (float('inf'), 'Playing'), (1.5, 'Playing')]:
            self.volume, self.status = volume, state
            await self.duck.begin()
            self.assertEqual(self.actions, [])
            self.assertEqual(self.duck.saved, {})

    async def test_restoration_failure_can_retry_without_losing_original(self):
        await self.duck.begin()
        self.focus._owner.side_effect = OSError('bus unavailable')
        self.assertFalse(await self.duck.restore())
        await self.duck.begin()
        self.assertEqual(len(self.actions), 1)
        self.focus._owner.side_effect = lambda _: self.owner
        self.assertTrue(await self.duck.restore())
        self.assertEqual(self.volume, .8)

    async def test_cancel_after_write_still_restores(self):
        original = self.focus._run.side_effect

        async def interrupted(*args):
            result = await original(*args)
            if len(args) > 1:
                raise asyncio.CancelledError
            return result

        self.focus._run.side_effect = interrupted
        with self.assertRaises(asyncio.CancelledError):
            await self.duck.begin()
        self.focus._run.side_effect = original
        self.assertTrue(await self.duck.restore())
        self.assertEqual(self.volume, .8)

    async def test_voice_cleanup_restores_on_error_and_cancellation(self):
        bus = PhaxEventBus()
        manager = VoiceManager({}, bus, StateMachine(bus))
        manager.media_ducking = self.duck
        for error in (RuntimeError('player failed'), asyncio.CancelledError()):
            with self.assertRaises(type(error)):
                async with manager._speech_cleanup('fixture'):
                    await self.duck.begin()
                    raise error
            self.assertEqual(self.volume, .8)
            self.assertEqual(manager.diagnostics['media_ducking'], 'RELEASED')
