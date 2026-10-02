import asyncio

from .base import ToolSpec, ValidationError
from ..monitor_aliases import alias_name, make_binding, resolve_alias
from ..permissions import Permission


async def save_alias(arguments, context):
    try:
        name = alias_name(arguments['name'])
    except ValueError as error:
        raise ValidationError(str(error)) from error
    world = await context.desktop.snapshot(force=True)
    outputs = [output for output in world.get('outputs', []) if output.get('enabled', True)]
    matches = [output for output in outputs if str(output.get('name', '')).casefold() == arguments['output_name'].casefold()]
    if len(matches) != 1:
        raise ValidationError('Choose one exact currently enabled connector from desktop.world')
    if name in {str(output.get('name', '')).casefold() for output in outputs}:
        raise ValidationError('A monitor nickname cannot replace a literal connector name')
    saved = await asyncio.to_thread(context.daily.records, 'monitor_alias')
    if name not in saved and len(saved) >= 64:
        raise ValidationError('At most 64 monitor names can be saved')
    binding = make_binding(matches[0], outputs)
    await asyncio.to_thread(context.daily.save, 'monitor_alias', name, binding)
    verified = (await asyncio.to_thread(context.daily.records, 'monitor_alias')).get(name) == binding
    return {'verified': verified, 'name': name, 'output_name': binding['output_name'],
            'binding': binding['binding'], 'display_layout_changed': False,
            'limitations': ([] if binding['binding'] == 'HARDWARE' else
                            ['No unique serial was available; this alias follows the connector, including a replacement panel']),
            'message': f"Saved monitor name {name}."}


async def list_aliases(arguments, context):
    saved = await asyncio.to_thread(context.daily.records, 'monitor_alias')
    world = await context.desktop.snapshot(force=True)
    outputs = [output for output in world.get('outputs', []) if output.get('enabled', True)]
    aliases = []
    for name, record in list(saved.items())[:64]:
        try:
            output = resolve_alias(name, {name: record}, outputs)
            aliases.append({'name': name, 'resolved': True, 'output_name': output['name'],
                            'binding': record['binding']})
        except ValueError as error:
            aliases.append({'name': name, 'resolved': False, 'error': str(error)})
    labels = [row['name'] + ' (' + (row.get('output_name') if row['resolved'] else 'not resolved') + ')' for row in aliases]
    return {'aliases': aliases, 'truncated': len(saved) > 64, 'live_topology_checked': True,
            'message': ('Saved monitor names: ' + ', '.join(labels))[:1000] if labels else 'No monitor names saved.'}


async def forget_alias(arguments, context):
    try:
        name = alias_name(arguments['name'])
    except ValueError as error:
        raise ValidationError(str(error)) from error
    return await asyncio.to_thread(context.daily.remove, 'monitor_alias', name)


def register_monitor_aliases(registry):
    name = {'type': 'string', 'minLength': 1, 'maxLength': 64}
    schema = lambda properties, required: {'type': 'object', 'properties': properties,
                                          'required': required, 'additionalProperties': False}
    registry.register(ToolSpec('desktop.output.alias.save', 'DESKTOP',
        'Save a monitor nickname ONLY on an explicit request, using an exact live connector. '
        'Unique hardware identity follows the panel; otherwise the binding follows its connector. No layout change.',
        Permission.LOW_RISK, schema({'name': name, 'output_name': {'type': 'string', 'minLength': 1, 'maxLength': 256}}, ['name', 'output_name']),
        save_alias, offline_available=True))
    registry.register(ToolSpec('desktop.output.alias.list', 'DESKTOP',
        'List saved monitor names and check each binding against fresh live topology.',
        Permission.SAFE, schema({}, []), list_aliases, read_only=True, offline_available=True))
    registry.register(ToolSpec('desktop.output.alias.forget', 'DESKTOP',
        'Forget one explicitly named monitor nickname without changing display settings.',
        Permission.LOW_RISK, schema({'name': name}, ['name']), forget_alias, offline_available=True))
