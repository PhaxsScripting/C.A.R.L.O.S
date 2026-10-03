import asyncio
import time

from ..permissions import Permission
from ..process_runner import settle
from .base import ToolSpec, ValidationError
from .builtin import desktop_entries, object_schema, open_application


def matching_windows(world, desktop_id, entries):
    if not isinstance(world.get('windows'), list) or world.get('windows_truncated'):
        raise ValidationError('Complete native window inventory unavailable; no additional launch issued')
    captured = world.get('captured_at_monotonic')
    if type(captured) not in (int, float) or not 0 <= time.monotonic() - captured <= 2:
        raise ValidationError('Fresh native window inventory unavailable; no additional launch issued')
    result = []
    for window in world['windows']:
        if not window.get('normal') or window.get('special') or window.get('deleted'):
            continue
        app = str(window.get('app_id', '')).casefold().removesuffix('.desktop')
        wmclass = str(window.get('resource_class', '')).casefold()
        identities = [key for key in entries if app and key.casefold() == app]
        if not identities:
            identities = [key for key, entry in entries.items()
                          if wmclass and str(entry.get('_startup_wm_class', '')).casefold() == wmclass]
        if desktop_id not in identities:
            continue
        if len(identities) != 1:
            raise ValidationError('Ambiguous installed application identity; no additional launch issued')
        if not isinstance(window.get('id'), str) or not window['id']:
            raise ValidationError('Native application window identity unavailable')
        result.append(window)
    if len(result) > 1:
        raise ValidationError('Multiple application windows; choose an exact window instead. No additional launch issued')
    return result


async def ensure_window(arguments, context):
    entries = await asyncio.to_thread(desktop_entries)
    desktop_id = arguments['desktop_id']
    if desktop_id not in entries:
        raise ValidationError('Unknown installed desktop application; no launch issued')
    if context.desktop is None:
        raise ValidationError('Native desktop backend unavailable; no launch issued')
    before = await context.desktop.snapshot(force=True)
    matches = matching_windows(before, desktop_id, entries)
    if matches:
        return {'verified': True, 'window': matches[0], 'desktop_id': desktop_id,
                'reused': True, 'launch_requested': False, 'activation_status': 'not_requested',
                'verification_scope': 'native_window_presence', 'replay_allowed': False}
    previous = {window.get('id') for window in before['windows']}
    # Once dispatched, wait for the bounded launcher even if the caller cancels.
    launching = asyncio.create_task(asyncio.to_thread(open_application, {'desktop_id': desktop_id}, context))
    try:
        activation = await asyncio.shield(launching)
    except asyncio.CancelledError:
        await settle(launching)
        raise
    deadline = time.monotonic() + arguments.get('timeout_seconds', 8)
    while True:
        matches = matching_windows(await context.desktop.snapshot(force=True), desktop_id, entries)
        if matches and matches[0]['id'] not in previous:
            return {'verified': True, 'window': matches[0], 'desktop_id': desktop_id,
                    'reused': False, 'launch_requested': True,
                    'activation_status': activation['activation_status'],
                    'verification_scope': 'native_window_presence', 'replay_allowed': False}
        if activation['activation_status'] == 'failed' or time.monotonic() >= deadline:
            return {'verified': False, 'window': None, 'desktop_id': desktop_id,
                    'reused': False, 'launch_requested': True,
                    'activation_status': activation['activation_status'], 'replay_allowed': False,
                    'verification_scope': 'native_window_presence',
                    'error': 'Application window presence unverified after one launch request. The app may still open; no retry issued.'}
        await asyncio.sleep(.1)


def register_application_window_tools(registry):
    lock = asyncio.Lock()

    async def serialized(arguments, context):
        async with lock:
            return await ensure_window(arguments, context)

    registry.register(ToolSpec(
        'applications.ensure_window', 'APPLICATIONS',
        'Reuse one exact installed-application window, or launch once and wait for one new native window. '
        'Multiple windows or shared application identities require an exact window choice. '
        'No title-only matching, automatic replay, focus change or launch rollback.',
        Permission.SAFE,
        object_schema({'desktop_id': {'type': 'string', 'minLength': 1, 'maxLength': 200},
                       'timeout_seconds': {'type': 'integer', 'minimum': 1, 'maximum': 10}}, ['desktop_id']),
        serialized, timeout_seconds=15, offline_available=True, reversible=False,
        platform_requirements=('Native compositor window inventory', 'gtk-launch desktop activation'),
        verification='Exact installed identity and fresh native window presence, not application readiness',
        side_effects=('Launch an installed application only when no matching native window exists',),
        output_schema={'type': 'object',
                       'required': ['verified', 'window', 'desktop_id', 'reused', 'launch_requested',
                                    'activation_status', 'verification_scope', 'replay_allowed'],
                       'properties': {'verified': {'type': 'boolean'}, 'window': {'type': ['object', 'null']},
                                      'desktop_id': {'type': 'string'}, 'reused': {'type': 'boolean'},
                                      'launch_requested': {'type': 'boolean'}, 'activation_status': {'type': 'string'},
                                      'verification_scope': {'type': 'string', 'enum': ['native_window_presence']},
                                      'replay_allowed': {'type': 'boolean', 'enum': [False]}, 'error': {'type': 'string'}}},
    ))
