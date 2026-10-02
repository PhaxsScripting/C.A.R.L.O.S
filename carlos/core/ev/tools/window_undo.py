from __future__ import annotations

import asyncio
from typing import Any

from .base import ToolContext


async def undo_window_change(
    arguments: dict[str, Any], context: ToolContext
) -> dict[str, Any]:
    from .builtin import _desktop

    model = _desktop(context)
    restore = model.peek_window_restore()
    if restore is None:
        return {"verified": False, "reason": "No reversible window change is available"}
    previous = restore["window"]
    window_id = str(previous["id"])
    if arguments.get('window_id') and arguments['window_id'] != window_id:
        return {'verified': False, 'reason': 'The latest undo record belongs to a different window; no window moved'}
    world = await model.snapshot(force=True)
    current = next((item for item in model.visible_windows(world)
                    if str(item.get('id')) == window_id), None)
    if current is None:
        return {
            "verified": False,
            "reason": "The changed window no longer exists",
            "window_id": window_id,
        }
    for key in ('pid', 'app_id', 'resource_class'):
        if previous.get(key) and current.get(key) != previous[key]:
            return {'verified': False, 'reason': 'The saved window identity changed; no window moved'}
    if previous.get('tiled'):
        return {'verified': False, 'reason': 'The saved compositor tile cannot be restored exactly; no window moved'}
    mode = previous.get('maximize_mode')
    if mode is not None and (type(mode) is not int or mode not in (0, 1, 2, 3)):
        return {'verified': False, 'reason': 'The saved maximize mode is invalid; no window moved'}
    geometry = previous.get('geometry', {})
    if any(type(geometry.get(key)) is not int for key in ('x', 'y', 'width', 'height')) or min(
            geometry.get('width', 0), geometry.get('height', 0)) <= 0:
        return {'verified': False, 'reason': 'The saved geometry is invalid; no window moved'}
    from ..monitor_aliases import identity, usable_identity

    output = str(previous.get("output", ""))
    saved_output = restore.get('output', {})
    guarded = usable_identity(saved_output)
    outputs = [item for item in world.get('outputs', []) if item.get('enabled', True)]
    destinations = [item for item in outputs if identity(item) == identity(saved_output)] if guarded else [
        item for item in outputs if item.get('name') == output]
    if output and len(destinations) != 1:
        return {'verified': False, 'reason': 'The original monitor is missing or ambiguous; no window moved'}
    if output:
        destination = destinations[0]
        output = str(destination['name'])
        area = destination.get('geometry', {})
        center_x, center_y = geometry['x'] + geometry['width'] / 2, geometry['y'] + geometry['height'] / 2
        if not (area.get('x', 0) <= center_x < area.get('x', 0) + area.get('width', 0)
                and area.get('y', 0) <= center_y < area.get('y', 0) + area.get('height', 0)):
            return {'verified': False, 'reason': 'The original placement no longer fits that monitor; no window moved'}
    desktops = [str(item) for item in previous.get("desktops", [])]
    all_desktops = bool(previous.get('on_all_desktops'))
    if not all_desktops and (not desktops or len(set(desktops)) != len(desktops) or not
                            set(desktops).issubset({str(item.get('id')) for item in world.get('desktops', [])})):
        return {'verified': False, 'reason': 'The original workspace assignment is unavailable; no window moved'}
    if output:
        destination_args = {"window_id": window_id, "output": output}
        if guarded:
            destination_args['expected_output_identity'] = {key: saved_output.get(key, '') for key in
                                                           ('manufacturer', 'model', 'serial_number')}
        await model.bridge.request("move_to_output", destination_args)
        await asyncio.sleep(0.12)
    await model.bridge.request('move_to_desktop', {'window_id': window_id, 'desktop_ids': desktops,
                                                 'all_desktops': all_desktops})
    await model.bridge.request("activate", {"window_id": window_id})
    await model.bridge.request("restore", {"window_id": window_id})
    await asyncio.sleep(0.12)
    await model.bridge.request("move_resize", {"window_id": window_id, **geometry})
    if mode:
        await model.bridge.request('maximize', {'window_id': window_id, 'mode': mode})
    elif mode is None and previous.get('maximized'):
        await model.bridge.request('maximize', {'window_id': window_id})
    if previous.get("fullscreen"):
        await model.bridge.request("fullscreen", {"window_id": window_id, "enabled": True})
    if previous.get("minimized"):
        await model.bridge.request("minimize", {"window_id": window_id})
    await asyncio.sleep(.25)
    final_world = await model.snapshot(force=True)
    actual = next((item for item in model.visible_windows(final_world)
                   if str(item.get('id')) == window_id), None)
    actual_geometry = (actual or {}).get("geometry", {})
    state_keys = ('minimized', 'fullscreen') if mode is not None else ('minimized', 'fullscreen', 'maximized')
    state_ok = bool(actual) and all(
        bool(actual.get(key)) == bool(previous.get(key))
        for key in state_keys
    )
    if mode is not None:
        state_ok = state_ok and type(actual.get('maximize_mode') if actual else None) is int and actual['maximize_mode'] == mode
    placement_ok = (
        bool(actual)
        and (not output or actual.get("output") == output)
        and all(
            abs(int(actual_geometry.get(key, -999999)) - value) <= 2
            for key, value in geometry.items()
        )
    )
    desktop_ok = bool(actual) and bool(actual.get('on_all_desktops')) == all_desktops and (
        not actual.get('desktops', []) if all_desktops else set(actual.get('desktops', [])) == set(desktops))
    matching_outputs = [item for item in final_world.get('outputs', [])
                        if item.get('enabled', True) and identity(item) == identity(saved_output)] if guarded else []
    output_identity_ok = not guarded or (len(matching_outputs) == 1 and matching_outputs[0].get('name') == output)
    window_identity_ok = bool(actual) and all(not previous.get(key) or actual.get(key) == previous[key]
                                            for key in ('pid', 'app_id', 'resource_class'))
    verified = state_ok and placement_ok and desktop_ok and output_identity_ok and window_identity_ok
    if verified:
        model.consume_window_restore()
    return {
        "verified": verified,
        "restored_action": restore["action"],
        "expected": previous,
        "window": actual,
        "output_identity_guarded": guarded,
        "output_identity_verified": output_identity_ok if guarded else None,
    }
