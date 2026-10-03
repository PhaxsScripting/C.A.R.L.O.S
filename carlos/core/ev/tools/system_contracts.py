def record(fields, required=None):
    return {'type': 'object', 'properties': fields,
            'required': list(fields) if required is None else required,
            'additionalProperties': False}


def text(nullable=False):
    return {'type': ['string', 'null'] if nullable else 'string'}


def number(nullable=False):
    return {'type': ['number', 'null'] if nullable else 'number'}


def integer(nullable=False):
    return {'type': ['integer', 'null'] if nullable else 'integer'}


def series(item):
    return {'type': 'array', 'items': item}


CAPACITY = {key: integer() for key in ('total_bytes', 'used_bytes', 'free_bytes')}
MOUNT = record({'device': text(), 'mountpoint': text(), 'filesystem': text(),
                'options': text(), 'total_bytes': integer(), 'free_bytes': integer(), 'percent': number()})
CELL = record({'status': text(), 'power_watts': number(True), 'power_source': text(True),
               'temperature_celsius': number(True)})

SYSTEM_OBSERVATION_SCHEMAS = {
    'system.clock': record({'iso8601': text(), 'local_time': text(), 'local_date': text(),
                            'timezone': text(True), 'source': {'type': 'string', 'enum': ['operating_system_clock']}}),
    'system.get_cpu_usage': record({'percent': number(), 'per_cpu_percent': series(number()),
                                    'load_average': series(number()), 'logical_cpus': integer(True),
                                    'physical_cpus': integer(True), 'frequency_mhz': integer(True)}),
    'system.get_memory_usage': record({
        'memory': record({'total_bytes': integer(), 'available_bytes': integer(),
                          'used_bytes': integer(), 'percent': number()}),
        'swap': record({**CAPACITY, 'percent': number()})}),
    'system.get_temperature': record({'celsius': number(True), 'sensor': text(True),
                                      'measurement': {'type': 'string', 'enum': ['CPU_CONTROL', 'CPU_TEMPERATURE']}},
                                     ['celsius', 'sensor']),
    'system.get_disk_usage': record({'path': text(), **CAPACITY, 'percent': number()}),
    'system.get_battery': record({'present': {'type': 'boolean'}, 'percent': number(),
                                  'plugged': {'type': 'boolean'}, 'seconds_left': integer(),
                                  'status': text(), 'charging': {'type': ['boolean', 'null']},
                                  'cells': series(CELL), 'status_source': text()}, ['present']),
    'system.identity': record({key: text() for key in ('operating_system', 'kernel', 'architecture',
                                                     'hostname', 'desktop', 'session_type')}),
    'system.mounts': record({'mounts': series(MOUNT), 'count': integer()}),
}
