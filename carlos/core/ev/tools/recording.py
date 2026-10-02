from .base import ToolSpec
from ..permissions import Permission


def register_recording_tools(registry, recorder):
    empty = {'type': 'object', 'properties': {}, 'additionalProperties': False}
    boolean = {'type': 'boolean'}
    identifier = {'type': 'string', 'pattern': '^[0-9a-f]{32}$', 'maxLength': 32}
    entry = {'type': 'object', 'properties': {
        'recording_id': identifier, 'path': {'type': 'string'}, 'bytes': {'type': 'integer', 'minimum': 0}},
        'required': ['recording_id', 'path', 'bytes'], 'additionalProperties': False}
    status_output = {'type': 'object', 'properties': {
        'available': boolean, 'active': boolean, 'verified': boolean, 'capture_verified': boolean,
        'consent_required': boolean, 'continuous_capture': boolean, 'audio_recorded': boolean,
        'network_exposed': boolean, 'reason': {'type': 'string'}, 'retention': {'type': 'string'},
        'maximum_seconds': {'type': 'integer'}, 'maximum_bytes': {'type': 'integer'},
        'portal_version': {'type': 'integer', 'minimum': 0}, 'source_types': {'type': 'integer', 'minimum': 0, 'maximum': 3},
        'encoder': {'type': 'string'}, 'missing': {'type': 'array', 'items': {'type': 'string'}}},
        'required': ['available', 'active', 'verified', 'capture_verified', 'consent_required',
                     'continuous_capture', 'audio_recorded', 'network_exposed', 'reason', 'retention',
                     'maximum_seconds', 'maximum_bytes'], 'additionalProperties': False}
    capture_output = {'type': 'object', 'properties': {**entry['properties'],
        'sha256': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'}, 'verified': boolean,
        'frames': {'type': 'integer', 'minimum': 1, 'maximum': 10000},
        'width': {'type': 'integer', 'minimum': 1, 'maximum': 1280},
        'height': {'type': 'integer', 'minimum': 1, 'maximum': 720},
        'duration_seconds': {'type': 'number', 'minimum': .01, 'maximum': 125},
        'encoder': {'type': 'string', 'enum': ['x264enc', 'vp8enc']},
        'audio_recorded': boolean, 'network_exposed': boolean, 'verification_scope': {'type': 'string'}},
        'required': ['recording_id', 'path', 'bytes', 'sha256', 'verified', 'frames', 'width', 'height',
                     'duration_seconds', 'encoder', 'audio_recorded', 'network_exposed', 'verification_scope'],
        'additionalProperties': False}

    async def status(arguments, context):
        return await recorder.status()

    async def capture(arguments, context):
        return await recorder.capture(arguments.get('seconds', 10))

    def clips(arguments, context):
        return recorder.list()

    def delete(arguments, context):
        return recorder.delete(arguments['recording_id'])

    registry.register(ToolSpec('desktop.recording.status', 'DESKTOP',
        'Probe native screen recording dependencies and portal support without starting capture.',
        Permission.SAFE, empty, status, read_only=True, offline_available=True, reversible=True,
        output_schema=status_output, timeout_seconds=8,
        verification='Encoder and portal advertisement; does not verify a grant or captured content'))
    registry.register(ToolSpec('desktop.recording.capture', 'DESKTOP',
        'Record one explicitly selected screen or window for 1 to 120 seconds, without audio, to a private local Matroska clip.',
        Permission.SENSITIVE, {'type': 'object', 'properties': {
            'seconds': {'type': 'integer', 'minimum': 1, 'maximum': 120}}, 'additionalProperties': False},
        capture, timeout_seconds=200, requires_confirmation=True, offline_available=True, reversible=True,
        output_schema=capture_output,
        confirmation_reason='Screen recordings can contain private information. Select one source in the native dialog; the local clip stays until you delete it.',
        platform_requirements=('XDG ScreenCast portal', 'native Python GI and GStreamer', 'ffprobe',
                               'GStreamer PipeWire, Matroska and x264 or VP8 encoder'),
        verification='Finalized Matroska container, video dimensions, positive duration and frames, size and SHA-256',
        side_effects=('native screen selection dialog', 'writes one private local clip; cancellation discards the unfinished clip'),
        expected_latency_ms=10000))
    registry.register(ToolSpec('desktop.recording.list', 'DESKTOP',
        'List up to twenty completed private Carlos screen recordings, newest first.',
        Permission.SAFE, empty, clips, read_only=True, offline_available=True, reversible=True,
        output_schema={'type': 'object', 'properties': {
            'recordings': {'type': 'array', 'items': entry, 'maxItems': 20},
            'total': {'type': 'integer', 'minimum': 0}, 'verified': boolean},
            'required': ['recordings', 'total', 'verified'], 'additionalProperties': False}))
    registry.register(ToolSpec('desktop.recording.delete', 'DESKTOP',
        'Delete one exact Carlos screen recording by its identifier.', Permission.SENSITIVE,
        {'type': 'object', 'properties': {'recording_id': {
            **identifier}},
         'required': ['recording_id'], 'additionalProperties': False}, delete,
        offline_available=True, reversible=False, requires_confirmation=True,
        output_schema={'type': 'object', 'properties': {
            'recording_id': identifier, 'removed': boolean, 'verified': boolean},
            'required': ['recording_id', 'removed', 'verified'], 'additionalProperties': False},
        confirmation_reason='This permanently deletes this one local recording.',
        verification='Exact recording path absent after deletion', side_effects=('deletes one local clip',)))
