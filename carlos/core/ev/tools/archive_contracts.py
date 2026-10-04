from .system_contracts import record, text


def count(maximum):
    return {'type': 'integer', 'minimum': 0, 'maximum': maximum}


SUMMARY = {'path': text(), 'format': {'type': 'string', 'enum': ['zip', 'tar', 'tar.gz']},
           'entries_count': count(2000), 'expanded_bytes': count(256 * 1024 * 1024)}
ARCHIVE_SCHEMAS = {
    'files.archive_inspect': record({**SUMMARY,
        'verification_scope': {'type': 'string', 'const': 'archive_metadata_only'},
        'payload_verified': {'type': 'boolean', 'const': False},
        'entries': {'type': 'array', 'maxItems': 50, 'items': record({
            'path': text(), 'directory': {'type': 'boolean'}, 'bytes': count(256 * 1024 * 1024)})},
        'listing_limited': {'type': 'boolean'}}),
    'files.archive_extract': record({**SUMMARY, 'destination': text(),
        'verified': {'type': 'boolean', 'const': True},
        'verification_scope': {'type': 'string', 'const': 'staged_archive_publication'},
        'files_verified': count(2000),
        'verification': {'type': 'string', 'const': 'Complete payload read, staged SHA-256 readback, atomic no-overwrite publication'},
        'executable_permissions_preserved': {'type': 'boolean', 'const': False}}),
}


def validate_archive_result(name, data):
    from .base import ValidationError, validate_schema
    validate_schema(data, ARCHIVE_SCHEMAS[name], 'result')
    entries = data['entries_count']
    if name == 'files.archive_inspect':
        preview = data['entries']
        valid = (len(preview) == min(entries, 50) and data['listing_limited'] == (entries > 50)
                 and len({item['path'] for item in preview}) == len(preview)
                 and all(not item['directory'] or item['bytes'] == 0 for item in preview))
        total = sum(item['bytes'] for item in preview)
        valid = valid and (total <= data['expanded_bytes'] if entries > 50 else total == data['expanded_bytes'])
    else:
        valid = (data['files_verified'] <= entries
                 and (data['expanded_bytes'] == 0 or data['files_verified'] > 0))
    if not valid:
        raise ValidationError('Archive counters disagree with the declared evidence')
    return data
