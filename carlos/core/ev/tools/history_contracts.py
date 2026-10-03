from .system_contracts import integer, number, record, series, text


HISTORICAL = {'type': 'boolean', 'enum': [True]}
SUMMARY_FIELDS = {'id': text(), 'request': text(), 'parent_id': text(True), 'status': text(),
                  'created': number(), 'updated': number(), 'project': text()}
SUMMARY = record(SUMMARY_FIELDS)
STEP = record({'id': integer(), 'task_id': text(), 'tool': text(), 'arguments': {'type': 'object'},
               'status': text(), 'started': number(), 'finished': number(True), 'receipt': {'type': 'object'}})
TASK = record({**SUMMARY_FIELDS, 'detail': {'type': 'object'}, 'detail_receipts_omitted': {'type': 'boolean'},
               'steps': series(STEP), 'steps_total': integer(), 'steps_offset': integer(),
               'steps_limit': integer(), 'steps_through_id': integer(), 'next_step_offset': integer(True),
               'history_partial': {'type': 'boolean'}, 'observed_at': number()})

HISTORY_OUTPUT = record({'tasks': series(SUMMARY), 'project': text(), 'historical': HISTORICAL,
                         'requires_fresh_observation_before_actions': HISTORICAL})
TASK_STATUS_OUTPUT = {'type': 'object', 'oneOf': [
    record({'ok': {'type': 'boolean', 'enum': [False]}, 'error': text()}),
    record({'task': TASK, 'project': text(), 'historical': HISTORICAL,
            'saved_approvals_reusable': {'type': 'boolean', 'enum': [False]},
            'requires_fresh_observation_before_actions': HISTORICAL}),
]}
