from __future__ import annotations

import asyncio
from typing import Any

from ..context_age import recent_age
from ..permissions import Permission
from .base import ToolContext, ToolRegistry, ToolSpec, ValidationError

RECT = ('x', 'y', 'width', 'height')
GUARDS = ('id', 'pid', 'app_id', 'resource_class', 'geometry', 'output', 'desktops',
          'on_all_desktops', 'minimized', 'fullscreen', 'maximize_mode', 'tiled')
OUTPUT_GUARDS = ('name', 'manufacturer', 'model', 'serial_number', 'geometry', 'scale')


def _usable(window: dict, desktop: str) -> bool:
    geometry = window.get('geometry', {})
    return (window.get('normal') is True and not window.get('special')
            and not window.get('deleted') and not window.get('tiled')
            and window.get('moveable') is True and window.get('resizeable') is True
            and not window.get('unresponsive')
            and (window.get('on_all_desktops') is True or desktop in window.get('desktops', []))
            and all(type(geometry.get(k)) is int and -32768 <= geometry[k] <= 32768 for k in RECT)
            and geometry['width'] > 0 and geometry['height'] > 0)


async def beside(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
    model = context.desktop
    if model is None:
        raise RuntimeError('The KDE desktop world model is unavailable')
    ids = {'anchor': arguments['anchor_id'], 'window': arguments['window_id']}
    if ids['anchor'] == ids['window']:
        raise ValidationError('Choose two different windows; no window moved')
    world = await model.snapshot(force=True)
    desktop = world.get('current_desktop', '')
    if (not desktop or not isinstance(world.get('windows'), list)
            or world.get('windows_truncated')
            or recent_age(world.get('captured_at_monotonic'), 2) is None):
        raise ValidationError('Fresh complete window state is unavailable; no window moved')
    initial = {}
    for role, window_id in ids.items():
        matches = [w for w in world['windows'] if w.get('id') == window_id]
        if len(matches) != 1 or not _usable(matches[0], desktop):
            raise ValidationError('Window is missing or cannot be arranged on this workspace; no window moved')
        initial[role] = matches[0]
    output_name = initial['anchor'].get('output')
    outputs = [o for o in world.get('outputs', []) if o.get('name') == output_name and o.get('enabled', True)]
    if not output_name or len(outputs) != 1 or initial['window'].get('output') != output_name:
        raise ValidationError('Both windows must be on the same enabled monitor; no window moved')
    output = {k: outputs[0].get(k) for k in OUTPUT_GUARDS}
    for role in ('anchor', 'window'):
        model.remember_window(initial[role], 'beside:' + role)
    requested = await model.bridge.request('beside', {
        **arguments, 'expected_windows': {role: {k: w.get(k) for k in GUARDS} for role, w in initial.items()},
        'expected_output': output, 'expected_desktop': desktop,
    })
    await asyncio.sleep(.2)
    final = await model.snapshot(force=True)
    actual = {role: next((w for w in final.get('windows', []) if w.get('id') == window_id), None)
              for role, window_id in ids.items()}
    expected = requested.get('target_geometries', {})
    final_output = next((o for o in final.get('outputs', []) if o.get('name') == output_name), {})
    verified = (not final.get('windows_truncated') and final.get('current_desktop') == desktop
                and recent_age(final.get('captured_at_monotonic'), 2) is not None
                and final_output.get('enabled', True)
                and all(final_output.get(k) == output[k] for k in OUTPUT_GUARDS))
    for role, before in initial.items():
        after, rect = actual[role], expected.get(role, {})
        verified = bool(verified and after and _usable(after, desktop)
                        and all(after.get(k) == before.get(k) for k in ('pid', 'app_id', 'resource_class', 'output', 'desktops', 'on_all_desktops'))
                        and not any(after.get(k) for k in ('minimized', 'fullscreen', 'maximized', 'tiled'))
                        and after.get('maximize_mode') in (None, 0)
                        and all(type(rect.get(k)) is int and type(after.get('geometry', {}).get(k)) is int
                                and abs(after['geometry'][k] - rect[k]) <= 2 for k in RECT))
    return {'verified': verified, 'expected': expected, 'anchor': actual['anchor'],
            'window': actual['window'], 'output': output_name,
            'undo_steps': 2, 'message': 'Both windows arranged side by side' if verified else
            'Both window positions could not be verified; no retry was issued. Undo restores one window per request.'}


def register_window_pair_tool(registry: ToolRegistry) -> None:
    registry.register(ToolSpec(
        'desktop.window.beside', 'DESKTOP',
        'Arrange two exact normal KWin windows on the same current workspace and monitor: reference on the left, requested window on the right. Undo restores each separately.',
        Permission.LOW_RISK,
        {'type': 'object', 'properties': {k: {'type': 'string', 'minLength': 1, 'maxLength': 100}
                                         for k in ('window_id', 'anchor_id')},
         'required': ['window_id', 'anchor_id'], 'additionalProperties': False}, beside,
        offline_available=True, reversible=True,
        output_schema={'type': 'object', 'properties': {
            'verified': {'type': 'boolean'}, 'expected': {'type': 'object'},
            'anchor': {'type': ['object', 'null']}, 'window': {'type': ['object', 'null']},
            'output': {'type': 'string'}, 'undo_steps': {'type': 'integer'}, 'message': {'type': 'string'}},
            'required': ['verified', 'expected', 'anchor', 'window', 'output', 'undo_steps', 'message']},
        platform_requirements=('KDE Plasma 6', 'Wayland', 'KWin scripting', 'same-user session D-Bus'),
        verification='Fresh readback of both identities, geometry, workspace, monitor and restored state',
        side_effects=('moves and resizes two windows', 'restores fullscreen, maximized and minimized states'),
        expected_latency_ms=500,
    ))
