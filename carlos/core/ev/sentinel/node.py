import ipaddress
import json
import os
import stat
import threading
import time
from pathlib import Path

from .protocol import (Verifier, clock_seconds, devices_config, magic_packet, send_wake,
                       signed_response)

CONFIG_LIMIT = 32768


def private_bytes(path, maximum=CONFIG_LIMIT):
    path = Path(path)
    parent = path.parent.lstat()
    if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid()
            or stat.S_IMODE(parent.st_mode) & 0o077):
        raise ValueError('Node secrets require an owner-only directory')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as handle:
        info = os.fstat(handle.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) & 0o077 or info.st_size > maximum):
            raise ValueError('Node secrets require a private bounded regular file')
        data = handle.read(maximum + 1)
        if len(data) > maximum:
            raise ValueError('Node configuration is too large')
        return data


def load_config(path):
    try:
        config = json.loads(private_bytes(path))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError('Invalid node configuration') from None
    if (not isinstance(config, dict) or set(config) - {'devices', 'target_mac', 'broadcast', 'host_device'}
            or not {'devices', 'target_mac', 'broadcast'} <= set(config)):
        raise ValueError('Invalid node configuration fields')
    checked = devices_config(config['devices'])
    magic_packet(config['target_mac'])
    if not isinstance(config['broadcast'], str):
        raise ValueError('Invalid fixed broadcast')
    address = ipaddress.IPv4Address(config['broadcast'])
    if address.is_unspecified or address.is_multicast or address.is_loopback:
        raise ValueError('Invalid fixed broadcast')
    host = config.get('host_device')
    if host is not None and (not isinstance(host, str) or host not in checked
                             or 'heartbeat' not in checked[host]['capabilities']):
        raise ValueError('Configure the exact authenticated host heartbeat identity')
    if any('heartbeat' in row['capabilities'] for name, row in checked.items() if name != host):
        raise ValueError('Only the configured host identity may send heartbeats')
    return config


class ReferenceNode:
    def __init__(self, config_path, *, allow_wake=False, sender=send_wake,
                 clock=time.monotonic, wall_clock=time.time):
        if type(allow_wake) is not bool:
            raise ValueError('Wake dispatch requires an explicit boolean option')
        self.config_path = Path(config_path)
        config = load_config(self.config_path)
        self.verifier = Verifier(self.config_path.with_suffix('.nonces.db'), config['devices'])
        self.allow_wake, self.sender = allow_wake, sender
        self.clock, self.wall_clock = clock, wall_clock
        self._lock = threading.RLock()
        self._heartbeat = None

    def host_state(self, config):
        if self._heartbeat is None:
            return 'UNKNOWN'
        identity, key, observed = self._heartbeat
        device = config['devices'].get(identity)
        now = self.clock()
        if (config.get('host_device') != identity or not device or device.get('revoked', False)
                or device['key'].lower() != key or not 0 <= now - observed <= 15):
            return 'UNKNOWN'
        return 'ONLINE'

    def handle(self, envelope):
        with self._lock:
            config = load_config(self.config_path)
            self.verifier.devices = config['devices']
            now = clock_seconds(self.wall_clock())
            action = self.verifier.verify(envelope, now=now)
            packet_sent = False
            if action == 'wake':
                if not self.allow_wake:
                    raise PermissionError('Wake dispatch is disabled')
                result = self.sender(config['target_mac'], config['broadcast'])
                if not isinstance(result, dict) or result.get('packet_sent') is not True:
                    raise OSError('Wake dispatch was not acknowledged')
                packet_sent = True
            elif action == 'heartbeat':
                if envelope['device'] != config.get('host_device'):
                    raise PermissionError('Host identity denied')
                self._heartbeat = (envelope['device'], config['devices'][envelope['device']]['key'].lower(), self.clock())
            return signed_response(envelope, bytes.fromhex(config['devices'][envelope['device']]['key']),
                                   packet_sent=packet_sent, host_state=self.host_state(config), now=now)

    def close(self):
        self.verifier.close()
