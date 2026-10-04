import hashlib
import hmac
import ipaddress
import json
import math
import os
import re
import secrets
import socket
import sqlite3
import stat
import threading
import time
from copy import deepcopy
from pathlib import Path

from . import PROTOCOL_VERSION

ACTIONS = frozenset({'status', 'wake', 'heartbeat'})
ENVELOPE_FIELDS = frozenset({'version', 'device', 'action', 'timestamp', 'nonce', 'signature'})
RESPONSE_FIELDS = frozenset({'version', 'device', 'action', 'request_nonce', 'timestamp',
                            'packet_sent', 'host_state', 'scope', 'signature'})
MAX_DEVICES = 64
MAX_NONCES = 4096
REPLAY_RETENTION = 120
MAX_ENVELOPE_BYTES = 2048
DEVICE_PATTERN = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}')
HEX_KEY_PATTERN = re.compile(r'[a-fA-F0-9]{64}')
NONCE_PATTERN = re.compile(r'[a-f0-9]{32}')
SIGNATURE_PATTERN = re.compile(r'[a-f0-9]{64}')
RESPONSE_DOMAIN = b'carlos-sentinel-response-v1\0'


def canonical(message):
    return json.dumps(message, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def clock_seconds(now=None):
    value = time.time() if now is None else now
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and 0 <= value < 2**53
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError('Invalid local clock')
    return int(value)


def checked_key(key):
    if type(key) is not bytes or len(key) != 32:
        raise ValueError('A node key must contain exactly 32 bytes')
    return key


def device_name(device):
    if not isinstance(device, str) or not DEVICE_PATTERN.fullmatch(device):
        raise ValueError('Invalid node identity')
    return device


def sign(device, action, key, *, now=None):
    device_name(device)
    if not isinstance(action, str) or action not in ACTIONS:
        raise ValueError('Unsupported node action')
    key = checked_key(key)
    message = {'version': PROTOCOL_VERSION, 'device': device, 'action': action,
               'timestamp': clock_seconds(now), 'nonce': secrets.token_hex(16)}
    return {**message, 'signature': hmac.new(key, canonical(message), hashlib.sha256).hexdigest()}


def devices_config(devices):
    if not isinstance(devices, dict) or not 1 <= len(devices) <= MAX_DEVICES:
        raise ValueError('Configure between one and 64 approved node identities')
    result = {}
    keys = set()
    for name, device in devices.items():
        device_name(name)
        if not isinstance(device, dict) or set(device) - {'key', 'capabilities', 'revoked'}:
            raise ValueError('Invalid node configuration')
        key, capabilities = device.get('key'), device.get('capabilities')
        if not isinstance(key, str) or not HEX_KEY_PATTERN.fullmatch(key):
            raise ValueError('Invalid configured node key')
        if (not isinstance(capabilities, list) or not capabilities or len(capabilities) > len(ACTIONS)
                or any(not isinstance(item, str) or item not in ACTIONS for item in capabilities)
                or len(set(capabilities)) != len(capabilities) or type(device.get('revoked', False)) is not bool):
            raise ValueError('Invalid node capabilities or revocation flag')
        if key.lower() in keys:
            raise ValueError('Approved node identities require distinct keys')
        keys.add(key.lower())
        result[name] = {'key': key.lower(), 'capabilities': tuple(capabilities), 'revoked': device.get('revoked', False)}
    return result


class Verifier:
    def __init__(self, path, devices, *, max_nonces=MAX_NONCES):
        if type(max_nonces) is not int or not 1 <= max_nonces <= MAX_NONCES:
            raise ValueError('Invalid replay-storage limit')
        self.devices = devices
        devices_config(devices)
        self.max_nonces = max_nonces
        self._lock = threading.RLock()
        self._closed = False
        path = Path(path)
        parent = path.parent.lstat()
        if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid()
                or stat.S_IMODE(parent.st_mode) & 0o077):
            raise ValueError('Replay storage requires an owner-only directory')
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) & 0o077 or info.st_size > 8 * 1024 * 1024):
                raise ValueError('Replay storage must be a private bounded regular file')
            self.db = sqlite3.connect(path, timeout=.2, check_same_thread=False)
            actual = path.lstat()
            if (actual.st_dev, actual.st_ino) != (info.st_dev, info.st_ino):
                self.db.close()
                raise ValueError('Replay storage changed while opening it')
        finally:
            os.close(fd)
        try:
            self.db.executescript('''CREATE TABLE IF NOT EXISTS nonces(device TEXT,nonce TEXT,timestamp INTEGER,PRIMARY KEY(device,nonce));
          CREATE TABLE IF NOT EXISTS wake_rate(device TEXT PRIMARY KEY,timestamp INTEGER);
          CREATE TABLE IF NOT EXISTS request_rate(device TEXT PRIMARY KEY,timestamp INTEGER);
          CREATE TABLE IF NOT EXISTS node_rate(id INTEGER PRIMARY KEY,timestamp INTEGER);''')
        except BaseException:
            self.db.close()
            raise

    def verify(self, envelope, *, now=None):
        now = clock_seconds(now)
        if not isinstance(envelope, dict) or set(envelope) != ENVELOPE_FIELDS:
            raise ValueError('Invalid envelope')
        try:
            if len(canonical(envelope)) > MAX_ENVELOPE_BYTES:
                raise ValueError('Envelope too large')
        except (TypeError, OverflowError):
            raise ValueError('Invalid envelope values') from None
        data = envelope.copy()
        signature = data.pop('signature')
        device_name(data['device'])
        configured = devices_config(deepcopy(self.devices))
        device = configured.get(data['device'])
        if not device or device['revoked']:
            raise ValueError('Unknown or revoked node')
        if (type(data['version']) is not int or data['version'] != PROTOCOL_VERSION
                or type(data['timestamp']) is not int or abs(now - data['timestamp']) > 30):
            raise ValueError('Expired or unsupported envelope')
        if not isinstance(data['action'], str) or data['action'] not in device['capabilities']:
            raise ValueError('Capability denied')
        if not isinstance(data['nonce'], str) or not NONCE_PATTERN.fullmatch(data['nonce']):
            raise ValueError('Invalid nonce')
        if not isinstance(signature, str) or not SIGNATURE_PATTERN.fullmatch(signature):
            raise ValueError('Invalid signature')
        expected = hmac.new(bytes.fromhex(device['key']), canonical(data), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError('Invalid signature')
        with self._lock:
            if self._closed:
                raise ValueError('Verifier is closed')
            try:
                self.db.execute('BEGIN IMMEDIATE')
                self.db.execute('DELETE FROM nonces WHERE timestamp < ?', (now - REPLAY_RETENTION,))
                previous = self.db.execute('SELECT timestamp FROM node_rate WHERE id=0').fetchone()
                if previous and now < previous[0]:
                    raise ValueError('Local clock moved backwards')
                last_request = self.db.execute('SELECT timestamp FROM request_rate WHERE device=?', (data['device'],)).fetchone()
                if last_request and now - last_request[0] < 1:
                    raise ValueError('Node request rate limit')
                if self.db.execute('SELECT 1 FROM nonces WHERE device=? AND nonce=?', (data['device'], data['nonce'])).fetchone():
                    raise ValueError('Replay rejected')
                if self.db.execute('SELECT count(*) FROM nonces').fetchone()[0] >= self.max_nonces:
                    raise ValueError('Replay storage is full')
                if data['action'] == 'wake':
                    last_wake = self.db.execute('SELECT timestamp FROM wake_rate WHERE device=?', (data['device'],)).fetchone()
                    global_wake = self.db.execute('SELECT timestamp FROM node_rate WHERE id=1').fetchone()
                    if (last_wake and now - last_wake[0] < 10) or (global_wake and now - global_wake[0] < 2):
                        raise ValueError('Wake rate limit')
                    self.db.execute('INSERT OR REPLACE INTO wake_rate VALUES(?,?)', (data['device'], now))
                    self.db.execute('INSERT OR REPLACE INTO node_rate VALUES(1,?)', (now,))
                self.db.execute('INSERT INTO nonces VALUES(?,?,?)', (data['device'], data['nonce'], now))
                self.db.execute('INSERT OR REPLACE INTO request_rate VALUES(?,?)', (data['device'], now))
                self.db.execute('INSERT OR REPLACE INTO node_rate VALUES(0,?)', (now,))
                self.db.execute('DELETE FROM wake_rate WHERE timestamp < ?', (now - REPLAY_RETENTION,))
                self.db.execute('DELETE FROM request_rate WHERE timestamp < ?', (now - REPLAY_RETENTION,))
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise
        return data['action']

    def close(self):
        with self._lock:
            if not self._closed:
                self.db.close()
                self._closed = True


def signed_response(request, key, *, packet_sent=False, host_state='UNKNOWN', now=None):
    checked_key(key)
    if type(packet_sent) is not bool or not isinstance(host_state, str) or host_state not in {'ONLINE', 'UNKNOWN'}:
        raise ValueError('Invalid observed node state')
    device_name(request['device'])
    if request['action'] not in ACTIONS or not NONCE_PATTERN.fullmatch(request['nonce']):
        raise ValueError('Invalid request binding')
    if packet_sent and request['action'] != 'wake':
        raise ValueError('Only a wake request can send a packet')
    message = {'version': PROTOCOL_VERSION, 'device': request['device'], 'action': request['action'],
               'request_nonce': request['nonce'], 'timestamp': clock_seconds(now),
               'packet_sent': packet_sent, 'host_state': host_state,
               'scope': 'authenticated_packet_dispatch_and_recent_host_heartbeat'}
    return {**message, 'signature': hmac.new(key, RESPONSE_DOMAIN + canonical(message), hashlib.sha256).hexdigest()}


def verify_response(response, request, key, *, now=None):
    checked_key(key)
    now = clock_seconds(now)
    if not isinstance(response, dict) or set(response) != RESPONSE_FIELDS:
        raise ValueError('Invalid response')
    data = response.copy()
    signature = data.pop('signature')
    if (type(data['version']) is not int or data['version'] != PROTOCOL_VERSION
            or type(data['timestamp']) is not int or abs(now - data['timestamp']) > 30
            or data['device'] != request['device'] or data['action'] != request['action']
            or data['request_nonce'] != request['nonce'] or type(data['packet_sent']) is not bool
            or not isinstance(data['host_state'], str) or data['host_state'] not in {'ONLINE', 'UNKNOWN'}
            or data['scope'] != 'authenticated_packet_dispatch_and_recent_host_heartbeat'
            or (data['packet_sent'] and data['action'] != 'wake')
            or not isinstance(signature, str) or not SIGNATURE_PATTERN.fullmatch(signature)):
        raise ValueError('Unbound, stale or inconsistent response')
    expected = hmac.new(key, RESPONSE_DOMAIN + canonical(data), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError('Invalid response signature')
    return data


def magic_packet(mac):
    if not isinstance(mac, str) or not re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', mac):
        raise ValueError('Invalid fixed target MAC')
    address = bytes.fromhex(mac.replace(':', ''))
    if address == b'\0' * 6 or address[0] & 1:
        raise ValueError('Target must be a nonzero unicast Ethernet address')
    return b'\xff' * 6 + address * 16


def send_wake(mac, broadcast):
    packet = magic_packet(mac)
    address = ipaddress.IPv4Address(broadcast)
    if address.is_unspecified or address.is_multicast or address.is_loopback:
        raise ValueError('Invalid configured LAN broadcast address')
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
        connection.settimeout(1)
        connection.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        if connection.sendto(packet, (str(address), 9)) != len(packet):
            raise OSError('The wake packet was not dispatched completely')
    return {'packet_sent': True, 'host_state': 'UNKNOWN', 'scope': 'packet_dispatch_only'}
