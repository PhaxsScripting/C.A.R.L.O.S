from __future__ import annotations

import os
import stat
import time
from pathlib import Path


def server_identity():
    from .builtin import _pactl_json

    address = _pactl_json(['info']).get('server_string', '')
    if isinstance(address, str) and address.startswith('unix:'):
        address = address[5:]
    if not isinstance(address, str) or not address.startswith('/'):
        return None
    info = Path(address).stat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
        return None
    return (info.st_dev, info.st_ino, info.st_ctime_ns)


def snapshot(sink):
    from .builtin import _pactl_json

    try:
        server = server_identity()
        if server is None:
            return None
        rows = [row for row in _pactl_json(['list', 'sinks']) if row.get('name') == sink]
        if len(rows) != 1:
            return None
        row = rows[0]
        channels = row.get('channel_map', '').split(',')
        volume = row.get('volume', {})
        if not 1 <= len(channels) <= 32 or len(set(channels)) != len(channels):
            return None
        values = tuple(volume[channel]['value'] for channel in channels)
        if any(type(value) is not int or not 0 <= value <= 65536 for value in values):
            return None
        index = row.get('index')
        if type(index) is not int or index < 0 or type(row.get('mute')) is not bool:
            return None
        serial = str(row.get('properties', {}).get('object.serial', ''))
        if server_identity() != server:
            return None
        return {'identity': (server, sink, index, serial, tuple(channels)),
                'sink': sink, 'index': index, 'volume': values, 'mute': row['mute']}
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, AttributeError):
        return None


def remember(context, before, field, verified):
    if context is None or before is None or not verified:
        return False
    after = snapshot(before['sink'])
    if after is None or before['identity'] != after['identity']:
        return False
    if before[field] == after[field]:
        return False
    now = time.monotonic()
    context.audio_undo[:] = [item for item in context.audio_undo
                            if now - item['created'] <= 600][-31:]
    context.audio_undo.append({'before': before, 'after': after,
                               'field': field, 'created': now})
    return True


def undo_last(arguments, context):
    from ev.platform import executable
    from .builtin import _OUTPUT_CONTROL_LOCK, run_command

    def refused(reason, *, discarded=False):
        return {'verified': False, 'restored': False, 'reason': reason,
                'discarded': discarded}

    with _OUTPUT_CONTROL_LOCK:
        if not context.audio_undo:
            return refused('No recent audio change is available to undo.')
        saved = context.audio_undo[-1]
        if arguments.get('field') and arguments['field'] != saved['field']:
            return refused('The latest audio change affects ' + saved['field']
                           + '; no different setting was restored.')
        if time.monotonic() - saved['created'] > 600:
            context.audio_undo.pop()
            return refused('The audio undo record expired after ten minutes.', discarded=True)
        before, after, field = saved['before'], saved['after'], saved['field']
        current = snapshot(before['sink'])
        if current is None or current['identity'] != after['identity']:
            context.audio_undo.pop()
            return refused('The original audio device or server changed; nothing was restored.',
                           discarded=True)
        if current[field] != after[field]:
            context.audio_undo.pop()
            return refused('The audio setting changed after Carlos adjusted it; that change was kept.',
                           discarded=True)
        # Use the original index so a new default never gets dragged into this.
        command = [executable('/usr/bin/pactl'), 'set-sink-' + field, str(before['index'])]
        command += [str(value) for value in before[field]] if field == 'volume' else [
            '1' if before[field] else '0']
        result = run_command(command, timeout=2)
        actual = snapshot(before['sink'])
        verified = bool(actual and actual['identity'] == before['identity']
                        and actual[field] == before[field])
        if verified:
            context.audio_undo.pop()
        return {'verified': verified, 'restored': verified, 'field': field,
                'sink': before['sink'], 'command_ok': bool(result['ok']),
                'message': ('Previous audio setting restored.' if verified else
                            'The previous audio setting could not be verified; no retry was made.')}
