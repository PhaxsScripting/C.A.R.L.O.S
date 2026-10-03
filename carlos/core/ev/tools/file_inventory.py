import heapq
import os
import stat
import time
from pathlib import Path

from .base import ValidationError

MAXIMUM_ENTRIES = 20000
MAXIMUM_DIRECTORIES = 2000
MAXIMUM_SECONDS = 5
EXCLUDED_DIRECTORIES = frozenset({'.git', 'node_modules', 'build', 'target'})


def find_file(arguments, context, resolve):
    root = resolve(arguments.get('root', str(Path.home() / 'Downloads')), context)
    if not root.is_dir():
        raise ValidationError('search root is not a directory')
    query = arguments['query'].casefold()
    limit = int(arguments.get('limit', 50))
    include_hidden = arguments.get('include_hidden', False)
    deadline = time.monotonic() + MAXIMUM_SECONDS
    pending = [root]
    results = []
    scanned = visited = directories = links = errors = 0

    def finish(reason):
        return {'results': results, 'visited_files': visited, 'visited_directories': directories,
                'scanned_entries': scanned, 'skipped_symlinks': links, 'scan_errors': errors,
                'stop_reason': reason, 'truncated': reason != 'complete'}

    while pending:
        if time.monotonic() >= deadline:
            return finish('time_limit')
        if directories >= MAXIMUM_DIRECTORIES:
            return finish('directory_limit')
        current = pending.pop()
        try:
            if current.is_symlink():
                links += 1
                continue
            current = resolve(str(current), context)
            if not current.is_relative_to(root):
                errors += 1
                continue
            directories += 1
            with os.scandir(current) as entries:
                for entry in entries:
                    if time.monotonic() >= deadline:
                        return finish('time_limit')
                    if scanned >= MAXIMUM_ENTRIES:
                        return finish('entry_limit')
                    scanned += 1
                    try:
                        metadata = entry.stat(follow_symlinks=False)
                    except OSError:
                        errors += 1
                        continue
                    if stat.S_ISLNK(metadata.st_mode):
                        links += 1
                        continue
                    if not include_hidden and entry.name.startswith('.'):
                        continue
                    if stat.S_ISDIR(metadata.st_mode):
                        if entry.name not in EXCLUDED_DIRECTORIES:
                            if directories + len(pending) >= MAXIMUM_DIRECTORIES:
                                return finish('directory_limit')
                            pending.append(Path(entry.path))
                    elif stat.S_ISREG(metadata.st_mode):
                        visited += 1
                        if query in entry.name.casefold():
                            if len(results) == limit:
                                return finish('result_limit')
                            results.append({'name': entry.name, 'path': entry.path,
                                            'size_bytes': metadata.st_size,
                                            'modified_epoch': metadata.st_mtime})
        except (OSError, ValidationError):
            errors += 1
    return finish('scan_errors' if errors else 'complete')


def list_directory(arguments, context, resolve):
    root = resolve(arguments['path'], context)
    if not root.is_dir():
        raise ValidationError('path is not a directory')
    limit = int(arguments.get('limit', 100))
    include_hidden = arguments.get('include_hidden', False)
    deadline = time.monotonic() + MAXIMUM_SECONDS
    scanned = errors = 0
    reason = 'complete'

    def metadata_rows():
        nonlocal scanned, errors, reason
        try:
            with os.scandir(root) as entries:
                for entry in entries:
                    if time.monotonic() >= deadline:
                        reason = 'time_limit'
                        return
                    if scanned >= MAXIMUM_ENTRIES:
                        reason = 'entry_limit'
                        return
                    scanned += 1
                    if not include_hidden and entry.name.startswith('.'):
                        continue
                    try:
                        metadata = entry.stat(follow_symlinks=False)
                    except OSError:
                        errors += 1
                        continue
                    yield {'name': entry.name, 'path': entry.path,
                           'is_file': stat.S_ISREG(metadata.st_mode),
                           'is_directory': stat.S_ISDIR(metadata.st_mode),
                           'is_symlink': stat.S_ISLNK(metadata.st_mode),
                           'size_bytes': metadata.st_size, 'modified_epoch': metadata.st_mtime}
        except OSError:
            errors += 1

    selected = heapq.nsmallest(limit + 1, metadata_rows(),
                              key=lambda item: (not item['is_directory'], item['name'].casefold(), item['name']))
    if reason == 'complete':
        reason = 'scan_errors' if errors else 'result_limit' if len(selected) > limit else 'complete'
    return {'path': str(root), 'entries': selected[:limit], 'count': min(limit, len(selected)),
            'truncated': reason != 'complete', 'scanned_entries': scanned,
            'scan_errors': errors, 'stop_reason': reason}
