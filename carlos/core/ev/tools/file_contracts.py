from .system_contracts import integer, number, record, series, text

BOOLEAN = {'type': 'boolean'}
FILE_METADATA = {'name': text(), 'path': text(), 'size_bytes': integer(), 'modified_epoch': number()}
SCAN_REASON = {'type': 'string', 'enum': ['complete', 'result_limit', 'entry_limit',
                                      'directory_limit', 'time_limit', 'scan_errors']}
FILE_OBSERVATION_SCHEMAS = {
    'files.find': record({'results': series(record(FILE_METADATA)), 'visited_files': integer(),
                          'visited_directories': integer(), 'scanned_entries': integer(),
                          'skipped_symlinks': integer(), 'scan_errors': integer(),
                          'stop_reason': SCAN_REASON, 'truncated': BOOLEAN}),
    'files.info': record({'path': text(), 'name': text(), 'is_file': BOOLEAN,
                         'is_directory': BOOLEAN, 'size_bytes': integer(),
                         'modified_epoch': number(), 'mode': text()}),
    'files.list': record({'path': text(), 'entries': series(record({**FILE_METADATA,
                         'is_file': BOOLEAN, 'is_directory': BOOLEAN, 'is_symlink': BOOLEAN})),
                         'count': integer(), 'truncated': BOOLEAN, 'scanned_entries': integer(),
                         'scan_errors': integer(), 'stop_reason': SCAN_REASON}),
    'files.read': record({'path': text(), 'content': text(), 'bytes': integer(), 'truncated': BOOLEAN}),
    'files.hash': record({'path': text(), 'algorithm': {'type': 'string', 'enum': ['sha256']},
                         'sha256': {'type': 'string', 'pattern': '^[a-f0-9]{64}$'}, 'size_bytes': integer()}),
}
