from .system_contracts import integer, number, record, series, text


def item(kind, *, content):
    fields = {'id': text(), 'kind': {'type': 'string', 'enum': [kind]}, 'title': text(),
              'done': {'type': 'integer', 'enum': [0, 1]},
              'archived': {'type': 'integer', 'enum': [0, 1]},
              'created': number(), 'updated': number()}
    if content:
        fields['content'] = text()
    return record(fields)


PERSONAL_OBSERVATION_SCHEMAS = {}
for kind, prefix in (('note', 'notes'), ('task', 'tasks'), ('bookmark', 'bookmarks'), ('snippet', 'snippets')):
    PERSONAL_OBSERVATION_SCHEMAS[prefix + '.list'] = record({
        'items': series(item(kind, content=False)),
        'choice_kind': {'type': 'string', 'enum': [kind]},
        'truncated': {'type': 'boolean'}, 'message': text()})
    PERSONAL_OBSERVATION_SCHEMAS[prefix + '.read'] = record({
        'item': item(kind, content=True), 'message': text()})

PERSONAL_OBSERVATION_SCHEMAS.update({
    'utility.calculate': record({'value': number(), 'expression': text(), 'message': text()}),
    'utility.convert': record({'value': number(), 'unit': text(), 'message': text()}),
    'utility.world_clock': record({'timezone': text(), 'iso': text(), 'message': text()}),
    'utility.clipboard_stats': record({'words': integer(), 'characters': integer(),
                                      'lines': integer(), 'message': text()}),
    'files.recent': record({'items': series(record({'id': text(), 'path': text(), 'title': text(),
                                                   'modified': number(), 'bytes': integer(), 'fingerprint': text()})),
                            'choice_kind': {'type': 'string', 'enum': ['file']},
                            'truncated': {'type': 'boolean'}, 'path': text(), 'message': text()}),
    'interaction.selection_status': record({'verified': {'type': 'boolean', 'enum': [False]},
                                             'message': text()}),
})

PERSONAL_MUTATION_SCHEMAS = {}
for kind, prefix in (('note', 'notes'), ('task', 'tasks'), ('bookmark', 'bookmarks'), ('snippet', 'snippets')):
    operations = ['create', 'archive', 'restore']
    if kind == 'task':
        operations += ['complete', 'reopen']
    elif kind == 'note':
        operations += ['append']
    for operation in operations:
        PERSONAL_MUTATION_SCHEMAS[prefix + '.' + operation] = record({
            'verified': {'type': 'boolean', 'enum': [True]},
            'verification_scope': {'type': 'string', 'enum': ['committed_sqlite_readback']},
            'item': item(kind, content=True), 'message': text()})
