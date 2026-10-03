import os
import stat
from pathlib import Path

from .base import ValidationError

MAXIMUM_ENTRIES = 10000


def manifest(path, checksum):
    def inspect(entry):
        metadata = entry.stat(follow_symlinks=False)
        if stat.S_ISDIR(metadata.st_mode):
            return ('directory',)
        if stat.S_ISREG(metadata.st_mode):
            return ('file', checksum(entry), metadata.st_size)
        raise ValidationError('transfer contains a symbolic link or special file')

    first = inspect(path)
    rows = {'': first}
    if first[0] == 'file':
        return rows

    def refused(_error):
        raise ValidationError('transfer inventory could not read every directory')

    for current, directories, files in os.walk(path, followlinks=False, onerror=refused):
        for name in [*directories, *files]:
            if len(rows) > MAXIMUM_ENTRIES:
                raise ValidationError('transfer exceeds the 10000-entry verification limit')
            entry = Path(current) / name
            rows[str(entry.relative_to(path))] = inspect(entry)
    return rows


def matches(path, expected, checksum):
    try:
        return manifest(path, checksum) == expected
    except (OSError, ValidationError):
        return False
