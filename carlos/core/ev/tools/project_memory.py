"""Approved notes for one exact project, separate from general memories."""

import asyncio
import json

from ..permissions import Permission
from .base import ToolSpec, ValidationError
from .builtin import object_schema, resolve_allowed


async def project_path(args, context):
    path = await asyncio.to_thread(resolve_allowed, args['project'], context)
    if not path.is_dir():
        raise ValidationError('Choose an existing allowed project directory')
    return str(path)


def storage(context):
    return 'RAM_ONLY' if context.memory.private else 'PERSISTENT'


async def search(args, context):
    project = await project_path(args, context)
    memories = await asyncio.to_thread(context.memory.list_project_memories, project,
                                      args.get('query', ''), args.get('limit', 50))
    bounded, size = [], 0
    for note in memories:
        size += len(json.dumps(note, ensure_ascii=False).encode('utf-8'))
        if size > 400000:
            break
        bounded.append(note)
    return {'project': project, 'memories': bounded, 'storage': storage(context),
            'has_more': len(bounded) < len(memories) or len(memories) == args.get('limit', 50)}


async def remember(args, context):
    if not args['content'].strip():
        raise ValidationError('Supply a nonempty project note')
    project = await project_path(args, context)
    note = await asyncio.to_thread(context.memory.remember_project, project,
                                  args['content'], args.get('tags', []))
    verified = await asyncio.to_thread(context.memory.project_memory_exists, project, note['id'])
    return {'memory': note, 'storage': storage(context),
            'verified': verified,
            'scope': 'Explicit project note; project files unchanged'}


async def forget(args, context):
    project = await project_path(args, context)
    removed = await asyncio.to_thread(context.memory.forget_project, project, args['id'])
    return {'removed': removed, 'id': args['id'], 'project': project,
            'storage': storage(context), 'scope': 'One note in this exact project context'}


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
        search, read_only=True, offline_available=True,
    ))
    registry.register(ToolSpec(
        'memory.project.remember', 'MEMORY',
        'Save a note only when the user asks to remember it for this exact project. Private sessions use RAM only.',
        Permission.SENSITIVE, object_schema({'project': project,
            'content': {'type': 'string', 'minLength': 1, 'maxLength': 8000},
            'tags': {'type': 'array', 'items': {'type': 'string', 'maxLength': 64}, 'maxItems': 20}},
            ['project', 'content']), remember, requires_confirmation=True,
        offline_available=True, reversible=True,
        confirmation_reason='This saves the supplied note in the selected project context.',
    ))
    registry.register(ToolSpec(
        'memory.project.forget', 'MEMORY', 'Delete one note in its exact project context.',
        Permission.DESTRUCTIVE, object_schema({'project': project,
            'id': {'type': 'string', 'pattern': '[a-f0-9]{32}'}}, ['project', 'id']),
        forget, requires_confirmation=True, offline_available=True, reversible=False,
        confirmation_reason='This deletes only the selected note in this project context.',
    ))
