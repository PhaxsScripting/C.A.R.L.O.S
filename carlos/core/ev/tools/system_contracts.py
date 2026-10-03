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

AUDIO_ENDPOINT = record({'index': integer(), 'name': text(), 'description': text(),
                         'state': text(), 'muted': {'type': 'boolean'}, 'percent': integer(True),
                         'device_bus': text(), 'device_description': text()})
AUDIO_DEVICES = record({'backend': text(), 'default_input': text(), 'default_output': text(),
                        'inputs': series(AUDIO_ENDPOINT), 'outputs': series(AUDIO_ENDPOINT)})
INVENTORY_STATE = record({'available': {'type': 'boolean'}, 'detail': text()})
PROCESS = record({'pid': integer(), 'ppid': integer(), 'name': text(), 'username': text(True),
                  'rss_bytes': integer(), 'cpu_percent': number(), 'started_at_epoch': number()})
INTERFACE = record({'name': text(), 'up': {'type': 'boolean'}, 'speed_mbps': integer(),
                    'addresses': series(text())})


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
    'system.get_processes': record({'processes': series(PROCESS), 'inspected': integer(),
                                    'sort': {'type': 'string', 'enum': ['cpu', 'memory']}}),
    'system.get_network_status': record({'interfaces': series(INTERFACE),
                                        'bytes_sent': integer(), 'bytes_received': integer()}),
    'audio.devices': AUDIO_DEVICES,
    'system.devices': record({'ok': {'type': 'boolean'}, 'complete': {'type': 'boolean'},
                              'usb': {'type': ['array', 'null'], 'items': text()},
                              'bluetooth': {'type': ['array', 'null'], 'items': text()},
                              'audio': {**AUDIO_DEVICES, 'type': ['object', 'null']},
                              'inventories': record({key: INVENTORY_STATE for key in ('usb', 'bluetooth', 'audio')}),
                              'limitations': series(text())}),
    'system.openrc_services': record({'ok': {'type': 'boolean'},
                                      'services': series(record({key: text() for key in ('name', 'status', 'runlevel')})),
                                      'count': integer(), 'detail': text()}),
}
