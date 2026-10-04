"""Approved notes for one exact project, separate from general memories."""

import asyncio
import json

from ..permissions import Permission
from .base import ToolSpec, ValidationError
from .builtin import object_schema, resolve_allowed
from .project_memory_contracts import PROJECT_MEMORY_SCHEMAS


async def project_path(args, context):
    path = await asyncio.to_thread(resolve_allowed, args['project'], context)
    if not path.is_dir():
        raise ValidationError('Choose an existing allowed project directory')
    return str(path)


async def search(args, context):
    project = await project_path(args, context)
    limit = args.get('limit', 50)
    observation = await asyncio.to_thread(context.memory.observe_project_memories, project,
                                          args.get('query', ''), limit + 1)
    memories = observation['memories']
    bounded, size = [], 0
    for note in memories[:limit]:
        size += len(json.dumps(note, ensure_ascii=False).encode('utf-8'))
        if size > 400000:
            break
        bounded.append(note)
    return {'project': project, 'memories': bounded, 'storage': observation['storage'],
            'has_more': len(bounded) < len(memories)}


async def remember(args, context):
    if not args['content'].strip():
        raise ValidationError('Supply a nonempty project note')
    project = await project_path(args, context)
    result = await asyncio.to_thread(context.memory.remember_project_verified, project,
                                    args['content'], args.get('tags', []))
    return {**result,
            'scope': 'Explicit project note; project files unchanged'}


async def forget(args, context):
    project = await project_path(args, context)
    result = await asyncio.to_thread(context.memory.forget_project_verified, project, args['id'])
    return {**result, 'scope': 'One note in this exact project context'}


async def select_project(args, context):
    from .preferences import set_preference
    if args['project']:
        return await set_preference({'key': 'project', 'value': args['project']}, context)
    await asyncio.to_thread(context.daily.remove, 'preference', 'project')
    if context.project_changed:
        context.project_changed('')
    return {'verified': 'project' not in await asyncio.to_thread(context.daily.records, 'preference'),
            'project': '', 'scope': 'General conversation; saved notes and past conversations kept'}


def register_project_memory(registry):
    project = {'type': 'string', 'minLength': 1, 'maxLength': 4096}
    registry.register(ToolSpec(
        'memory.project.select', 'MEMORY',
        'Select the explicitly requested existing project for conversation context, or an empty path for general conversation. Keeps saved notes and past conversations.',
        Permission.LOW_RISK, object_schema({'project': {'type': 'string', 'maxLength': 4096}}, ['project']),
        select_project, offline_available=True, reversible=True,
    ))
    registry.register(ToolSpec(
        'memory.project.search', 'MEMORY', 'Read explicit notes for one exact project directory.',
        Permission.SAFE, object_schema({'project': project,
            'query': {'type': 'string', 'maxLength': 256},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100}}, ['project']),
        search, read_only=True, offline_available=True, reversible=False,
        output_schema=PROJECT_MEMORY_SCHEMAS['memory.project.search'],
    ))
    registry.register(ToolSpec(
        'memory.project.remember', 'MEMORY',
        'Save a note only when the user asks to remember it for this exact project. Private sessions use RAM only.',
        Permission.SENSITIVE, object_schema({'project': project,
            'content': {'type': 'string', 'minLength': 1, 'maxLength': 8000},
            'tags': {'type': 'array', 'items': {'type': 'string', 'maxLength': 64}, 'maxItems': 20}},
            ['project', 'content']), remember, requires_confirmation=True,
        offline_available=True, reversible=True,
        output_schema=PROJECT_MEMORY_SCHEMAS['memory.project.remember'],
        confirmation_reason='This saves the supplied note in the selected project context.',
    ))
    registry.register(ToolSpec(
        'memory.project.forget', 'MEMORY', 'Delete one note in its exact project context.',
        Permission.DESTRUCTIVE, object_schema({'project': project,
            'id': {'type': 'string', 'pattern': '[a-f0-9]{32}'}}, ['project', 'id']),
        forget, requires_confirmation=True, offline_available=True, reversible=False,
        output_schema=PROJECT_MEMORY_SCHEMAS['memory.project.forget'],
        confirmation_reason='This deletes only the selected note in this project context.',
    ))
