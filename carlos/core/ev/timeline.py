"""Optional local activity metadata. Live events do not need disk history."""

import threading

from .logging_utils import redact
from .permissions import Permission
from .tools.base import ToolSpec, ValidationError
from .tools.builtin import object_schema

HISTORY_LIMIT = 2000
ACTIVITY_TYPES = frozenset({
    'core.started', 'core.stopping', 'core.state_changed',
    'system.resume_observed', 'system.warning', 'system.error',
    'system.resource_mode_changed', 'task.started', 'task.completed',
    'task.failed', 'task.recovered', 'tool.completed', 'tool.failed',
    'voice.conversation_ended', 'wake.detected', 'plan.failed',
    'coding.started', 'coding.completed', 'coding.failed', 'coding.cancelled', 'coding.waiting',
    'health.repair_started', 'health.repair_finished', 'health.repair_failed',
    'health.probe_failed', 'power.scheduled', 'power.dispatching',
    'power.requested', 'power.cancelled',
})
METADATA_FIELDS = frozenset({
    'tool', 'status', 'component', 'phase', 'from', 'to', 'reason',
    'error_type', 'verified', 'attempt', 'state', 'actions_replayed',
    'interrupted_tasks', 'action', 'cancelled', 'project', 'proposal_id', 'branch', 'exit_code', 'ok',
})


def metadata(payload):
    if not isinstance(payload, dict):
        return {}
    return redact({key: value for key, value in payload.items()
                   if key in METADATA_FIELDS and type(value) in (str, int, float, bool)})


class ActivityTimeline:
    def __init__(self, core):
        self.core = core
        self._history_lock = threading.RLock()
        self._cleared_through = 0

    @property
    def enabled(self):
        return self.core.config.get('memory', {}).get('activity_timeline') is True

    def should_record(self, event):
        return (self.enabled and not event.private and event.type in ACTIVITY_TYPES
                and event.sequence > self._cleared_through)

    def record(self, event):
        with self._history_lock:
            if not self.should_record(event):
                return
            row = event.as_dict()
            row['payload'] = metadata(row['payload'])
            self.core.memory.record_event(row, limit=HISTORY_LIMIT)

    def read(self, args, context):
        rows = self.core.memory.list_events(args.get('limit', 50), args.get('event_type', ''))
        for row in rows:
            row['payload'] = metadata(row['payload'])
        return {'recording_enabled': self.enabled, 'events': rows,
                'retained_event_limit': HISTORY_LIMIT,
                'scope': 'Local activity metadata; not recordings, screenshots or task contents'}

    def clear(self, args, context):
        if self.core.privacy.ephemeral:
            raise ValidationError('Leave the private or guest session before clearing saved activity history')
        with self._history_lock:
            self._cleared_through = self.core.bus.sequence
            removed = self.core.memory.clear_events()
            return {'removed_events': removed,
                    'verified': not self.core.memory.list_events(1),
                    'scope': 'Activity through this clear request; later events can be recorded. Saved memories, conversations and permission audit kept'}

    def register(self, registry):
        registry.register(ToolSpec(
            'memory.timeline', 'MEMORY',
            'Read optional local activity history. Empty history is not evidence that nothing happened.',
            Permission.SAFE,
            object_schema({'limit': {'type': 'integer', 'minimum': 1, 'maximum': 200},
                           'event_type': {'type': 'string', 'maxLength': 96}}),
            self.read, read_only=True, offline_available=True,
        ))
        registry.register(ToolSpec(
            'memory.timeline.clear', 'MEMORY', 'Clear only the recorded activity timeline.',
            Permission.DESTRUCTIVE, object_schema({}, []), self.clear,
            requires_confirmation=True, offline_available=True, reversible=False,
            confirmation_reason='This deletes recorded activity events, keeping saved memories and conversations.',
        ))
