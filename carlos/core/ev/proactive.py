import asyncio
import time

from .context_age import recent_age
from .state import CoreState


class ProactiveSpeech:
    def __init__(self, core, clock=time.monotonic, wall_clock=time.time):
        self.core, self.clock, self.wall_clock = core, clock, wall_clock
        self.revision = 0
        self.pending = {}
        self.attempted = {}
        self.wake = asyncio.Event()

    def threshold(self):
        return self.core.config.get('personality', {}).get('proactive_speech_threshold', 'off')

    def allowed(self):
        core = self.core
        session = core.presence.state
        age = recent_age(session.get('observed_at'), 10, now=self.wall_clock())
        return (self.threshold() in {'high', 'emergency'}
                and core.privacy.mode in {'NORMAL', 'LOCAL ONLY'} and not core.privacy.changing
                and not core.stop_event.is_set() and not core.voice.privacy_mode
                and not core.voice.gaming_suspended and not core.scenes.current.get('quiet')
                and core.config.get('assistant', {}).get('speak_responses', True)
                and core.voice.tts_available and session.get('session') == 'UNLOCKED' and age is not None)

    def candidate(self, event):
        data = event.payload
        if not isinstance(data, dict) or event.private:
            return None
        if event.type == 'system.warning' and event.source == 'telemetry' and data.get('kind') == 'thermal':
            value = data.get('celsius')
            if type(value) in (int, float) and 90 <= value <= 200:
                return 'thermal', 'The CPU sensor is reporting a high temperature. Check the system panel.'
        if (event.type == 'security.alert' and event.source == 'security_monitor'
                and data.get('severity') == 'HIGH' and type(data.get('count')) is int and data['count'] > 0
                and recent_age(data.get('observed_at'), 30, now=self.wall_clock()) is not None):
            if data.get('kind') == 'smart':
                return 'storage', 'The storage health monitor reported a concern. Check the security panel.'
            if data.get('kind') in {'listeners', 'ssh', 'startup', 'logins', 'firewall', 'updates', 'services'}:
                return 'security', 'The local security monitor reported a concern. Check the security panel.'
        return None

    def clear(self):
        self.revision += 1
        self.pending.clear()
        self.wake.clear()

    def offer(self, event):
        stopped = (event.type == 'tts.interrupted' and isinstance(event.payload, dict)
                   and event.payload.get('reason') != 'proactive_emergency')
        if stopped or event.type in {'personality.updated', 'carlos.privacy_changed', 'carlos.privacy_transition',
                                    'presence.session_changed', 'carlos.scene_changed', 'voice.conversation_ended',
                                    'system.resume_observed', 'core.stopping'} or not self.allowed():
            self.clear()
            return False
        candidate = self.candidate(event)
        age = recent_age(event.monotonic, 10, now=self.clock())
        if candidate is None or age is None:
            return False
        if self.threshold() == 'emergency' and event.priority != 'EMERGENCY':
            return False
        kind, text = candidate
        key = kind, event.priority
        last = self.attempted.get(key)
        if last is not None and self.clock() - last < 120:
            return False
        previous = self.pending.get(kind)
        if previous and (event.monotonic < previous['at'] or event.sequence <= previous['sequence']):
            return False
        if previous and previous['priority'] == 'EMERGENCY' and event.priority != 'EMERGENCY':
            return False
        self.pending[kind] = {'text': text, 'priority': event.priority, 'at': event.monotonic,
                              'sequence': event.sequence, 'correlation': event.correlation_id,
                              'scope': (self.core._action_generation, self.core.privacy.mode, self.threshold(), self.revision),
                              'interrupted': bool(previous and previous.get('interrupted'))}
        self.wake.set()
        return True

    def current(self, item):
        return (self.allowed() and recent_age(item['at'], 10, now=self.clock()) is not None
                and item['scope'] == (self.core._action_generation, self.core.privacy.mode, self.threshold(), self.revision))

    async def deliver_one(self):
        if not self.allowed():
            self.clear()
            return False
        for kind in list(self.pending):
            if not self.current(self.pending[kind]):
                del self.pending[kind]
        if not self.pending:
            return False
        kind = max(self.pending, key=lambda key: self.pending[key]['priority'] == 'EMERGENCY')
        item = self.pending[kind]
        if (self.core.state.current == CoreState.SPEAKING and item['priority'] == 'EMERGENCY'
                and not item['interrupted']):
            item['interrupted'] = True
            await self.core.voice.stop_speaking('proactive_emergency')
            if not self.current(item) or self.pending.get(kind) is not item:
                return False
        if (self.core.state.current != CoreState.DORMANT or self.core.voice.speech_pending
                or self.core.voice.capture_active):
            return False
        del self.pending[kind]
        self.attempted[(kind, item['priority'])] = self.clock()
        speech = asyncio.create_task(self.core.voice.speak(item['text'], item['correlation'], allow_follow_up=False))
        try:
            while not speech.done():
                await asyncio.wait({speech}, timeout=.1)
                if not self.current(item):
                    speech.cancel()
                    await asyncio.gather(speech, return_exceptions=True)
                    return True
            result = speech.result()
        finally:
            if not speech.done():
                speech.cancel()
                await asyncio.gather(speech, return_exceptions=True)
        if result.get('status') == 'completed' and self.current(item):
            self.core.bus.publish('proactive.speech_completed', 'proactive',
                                  {'kind': kind, 'priority': item['priority'], 'scope': 'speech_backend_completion'},
                                  item['correlation'])
        return True

    async def run(self):
        try:
            while True:
                await self.wake.wait()
                try:
                    await self.deliver_one()
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    self.core.logger.warning('Proactive speech failed', extra={'fields': {'error_type': type(error).__name__}})
                if self.pending:
                    await asyncio.sleep(.25)
                else:
                    self.wake.clear()
        finally:
            self.clear()

    def snapshot(self):
        return {'threshold': self.threshold(), 'pending': len(self.pending),
                'delivery': 'Opt-in local alerts; fresh unlocked session, privacy and quiet-mode gates',
                'scope': 'Current process policy; physical hearing is not verified'}
