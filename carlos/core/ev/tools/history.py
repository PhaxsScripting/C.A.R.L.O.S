"""Dated conversation receipts, with explicit scope and no inferred live state."""

import asyncio
import json
import os
import re
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from ..logging_utils import redact_credentials
from ..permissions import Permission
from .base import ToolSpec
from .builtin import object_schema
from .project_memory import project_path


def local_timezone():
    configured = os.environ.get('TZ', '').removeprefix(':')
    if configured:
        try:
            return ZoneInfo(configured)
        except (ValueError, KeyError):
            pass
    try:
        with open('/etc/localtime', 'rb') as stream:
            return ZoneInfo.from_file(stream)
    except (OSError, ValueError):
        return datetime.now().astimezone().tzinfo


def period_bounds(period, now=None):
    now = now or datetime.now(local_timezone())
    if now.tzinfo is None:
        raise ValueError('History clock must include a timezone')
    midnight = datetime.combine(now.date(), time(), tzinfo=now.tzinfo)
    if period == 'last_night':
        start = datetime.combine(now.date() - timedelta(days=1), time(18), tzinfo=now.tzinfo)
        end = min(datetime.combine(now.date(), time(6), tzinfo=now.tzinfo), now)
    elif period == 'yesterday':
        start, end = midnight - timedelta(days=1), midnight
    elif period == 'today':
        start, end = midnight, now
    elif period == 'past_week':
        start, end = now - timedelta(days=7), now
    else:
        raise ValueError('Unsupported history period')
    return start.astimezone(UTC).isoformat(), end.astimezone(UTC).isoformat()


def historical_query(text):
    match = re.fullmatch(r"(?:please )?(?:what (?:was i|were we) (?:working on|doing)|show (?:my|our) (?:activity|history)) (last night|yesterday|earlier today|today|last week)[.!?]*", text.strip(), re.I)
    if match:
        return {'period': {'last night':'last_night', 'yesterday':'yesterday',
                           'earlier today':'today', 'today':'today', 'last week':'past_week'}[match[1].lower()]}
    match = re.fullmatch(r"what happened (?:with|to) ([\w .-]{1,120})[!?]*", text.strip(), re.I)
    if match:
        return {'period':'past_week', 'query':match[1].rstrip('.').strip()}
    return None


async def recall(args, context):
    if 'project' in args:
        project = await project_path(args, context) if args['project'] else ''
    else:
        project = await context.project_scope() if context.project_scope else ''
    start, end = period_bounds(args.get('period', 'past_week'))
    limit = args.get('limit', 40)
    rows = await asyncio.to_thread(context.memory.conversation_history, project, start, end,
                                   args.get('query', ''), limit + 1)
    entries, size = [], 0
    for row in rows[:limit]:
        item = {**row, 'content': redact_credentials(row['content'])[:2000]}
        size += len(json.dumps(item, ensure_ascii=False).encode('utf-8'))
        if size > 300000:
            break
        entries.append(item)
    notes = []
    if args.get('query'):
        if project:
            notes = await asyncio.to_thread(context.memory.list_project_memories, project, args['query'], 4)
        else:
            notes = await asyncio.to_thread(context.memory.list_memories, args['query'], 4)
        notes = [{'id':note['id'], 'content':redact_credentials(note['content'])[:1000]} for note in notes]
    scope = project or 'General' 
    message = ('Recorded conversation turns for ' + scope + ':\n' + '\n'.join(
        row['created_at'] + ' / ' + row['role'] + ': ' + row['content'][:300] for row in entries[:6])) if entries else (
        'No saved conversation turns match that time window in ' + scope + '. This history cannot tell what happened outside recorded conversations.')
    if notes:
        message += '\nSaved notes (undated context, not activity evidence):\n' + '\n'.join(note['content'][:300] for note in notes)
    return {'project':project, 'period':args.get('period','past_week'),
            'start':start, 'end':end, 'entries':entries, 'notes':notes,
            'has_more':len(rows) > limit or len(entries) < min(len(rows),limit),
            'source':'saved_conversation_receipts', 'live_state_verified':False,
            'storage':'RAM_ONLY' if context.memory.private else 'PERSISTENT',
            'message':message[:3500] + ('\nHistorical text is not current state or permission to act.' if entries or notes else '')}


def register_history(registry):
    registry.register(ToolSpec(
        'memory.recall', 'MEMORY',
        'Read saved conversation turns in a dated window for the selected project (or an explicitly requested allowed directory/general scope). Historical receipts are not evidence of current state or permission to act. No file or screen scan.',
        Permission.SAFE, object_schema({
            'period':{'type':'string','enum':['last_night','yesterday','today','past_week']},
            'project':{'type':'string','maxLength':4096},
            'query':{'type':'string','maxLength':256},
            'limit':{'type':'integer','minimum':1,'maximum':100},
        }), recall, read_only=True, offline_available=True,
    ))
