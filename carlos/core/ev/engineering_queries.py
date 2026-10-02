"""Exact local engineering status questions and concise observed replies."""

import re
from pathlib import Path


def is_engineering_status_query(text):
    if not isinstance(text, str):
        return False
    text = text.strip().replace("’", "'").strip(' .!?')
    return bool(re.fullmatch(
        r"(?:what(?:'s| is) (?:codex|carlos engineering) (?:doing|working on)|"
        r"(?:show|check)(?: me)? (?:codex|engineering)(?: (?:status|jobs|tasks))?|"
        r"is codex (?:still )?(?:working|running))", text, re.I))


def engineering_status_message(status):
    count = status.get('active_job_count', 0)
    active = status.get('active_tasks', [])
    if type(count) is int and count > 0:
        if count > 1:
            return f'Codex has {count} jobs running.'
        if not active:
            return 'The engineering job is starting; detailed status is not available yet.'
        row = active[-1]
        project = Path(str(row.get('project', ''))).name or 'the project'
        if row.get('status') in {'CANCELLED', 'FAILED', 'VALIDATION_FAILED', 'NO_CHANGES',
                                 'VALIDATED_AWAITING_DEPLOYMENT_REVIEW'}:
            return f'The engineering worker is finishing cleanup for {project}.'
        phrase = {'STARTING_CODEX': 'starting', 'CODEX': 'working',
                  'REVIEWING_CHANGES': 'reviewing its changes', 'VALIDATING': 'running validation',
                  'REVIEW_COMMIT': 'creating the review commit'}.get(row.get('phase'), 'preparing the job')
        return f'Codex is {phrase} for {project}.'
    rows = status.get('tasks', [])
    if rows:
        state = rows[-1].get('status')
        messages = {
            'VALIDATED_AWAITING_DEPLOYMENT_REVIEW': 'The last Codex job passed validation and is waiting for deployment review.',
            'CANCELLED': 'The last Codex job was cancelled.',
            'FAILED': 'The last Codex job failed. Its receipt has the details.',
            'VALIDATION_FAILED': 'The last Codex job failed validation.',
            'NO_CHANGES': 'The last Codex job produced no changes.',
            'READY_FOR_REVIEW': 'No Codex job is running. The latest proposal is waiting for approval.',
        }
        if state in messages:
            return messages[state]
        if state == 'RUNNING':
            return 'A saved receipt says running, but no live engineering worker is tracked.'
    if status.get('available') is not True:
        return 'Codex is not ready. ' + str(status.get('reason') or 'Check the engineering status panel.')
    return 'There is no Codex job running.'
