from .system_contracts import record, series, text

NOTE = record({'id': {'type': 'string', 'pattern': '[a-f0-9]{32}'},
               'project': text(), 'content': text(), 'tags': series(text()), 'created_at': text()})
STORAGE = {'type': 'string', 'enum': ['PERSISTENT', 'RAM_ONLY']}
READBACK = {'type': 'string', 'const': 'committed_sqlite_readback'}

PROJECT_MEMORY_SCHEMAS = {
    'memory.project.search': record({'project': text(), 'memories': series(NOTE),
                                     'storage': STORAGE, 'has_more': {'type': 'boolean'}}),
    'memory.project.remember': record({'memory': NOTE, 'storage': STORAGE,
                                       'verified': {'type': 'boolean', 'const': True},
                                       'verification_scope': READBACK,
                                       'scope': {'type': 'string', 'const': 'Explicit project note; project files unchanged'}}),
    'memory.project.forget': record({'removed': {'type': 'boolean'}, 'id': {'type': 'string', 'pattern': '[a-f0-9]{32}'},
                                     'project': text(), 'storage': STORAGE,
                                     'verified': {'type': 'boolean', 'const': True},
                                     'verification_scope': READBACK,
                                     'scope': {'type': 'string', 'const': 'One note in this exact project context'}}),
}

GENERAL_NOTE = record({'id': text(), 'content': text(), 'tags': series(text()), 'created_at': text()})
GENERAL_MEMORY_SCHEMAS = {
    'memory.search': record({'memories': series(record({**GENERAL_NOTE['properties'], 'updated_at': text()})),
                            'storage': STORAGE}),
    'memory.remember': record({'memory': GENERAL_NOTE, 'storage': STORAGE,
                               'verified': {'type': 'boolean', 'const': True}, 'verification_scope': READBACK}),
    'memory.forget': record({'removed': {'type': 'boolean'}, 'id': text(), 'storage': STORAGE,
                             'verified': {'type': 'boolean', 'const': True}, 'verification_scope': READBACK}),
}
