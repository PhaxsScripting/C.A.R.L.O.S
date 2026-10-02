from __future__ import annotations

import re
import time

APIS = (
    ('org.freedesktop.UPower.PowerProfiles', '/org/freedesktop/UPower/PowerProfiles'),
    ('net.hadess.PowerProfiles', '/net/hadess/PowerProfiles'),
)
PROFILES = frozenset({'power-saver', 'balanced', 'performance'})
BACKEND = 'Power Profiles Daemon D-Bus'


class PowerProfiles:
    def __enter__(self):
        from jeepney.io.blocking import open_dbus_connection
        from jeepney.wrappers import DBusErrorResponse, DBusAddress, Properties
        from jeepney.bus_messages import message_bus

        self.connection = open_dbus_connection(bus='SYSTEM', auth_timeout=1)
        try:
            self.bus_id = self._call(message_bus.GetId())[0]
            if not isinstance(self.bus_id, str) or not re.fullmatch(r'[a-fA-F0-9]{32}', self.bus_id):
                raise ValueError('Invalid system bus identity')
            for service, path in APIS:
                try:
                    owner = self._call(message_bus.GetNameOwner(service))[0]
                except DBusErrorResponse as error:
                    if error.name != 'org.freedesktop.DBus.Error.NameHasNoOwner':
                        raise
                    continue
                if not isinstance(owner, str) or not re.fullmatch(r':\d+\.\d+', owner):
                    raise ValueError('Invalid power daemon owner')
                self.service, self.owner = service, owner
                self.properties = Properties(DBusAddress(path, owner, service))
                return self
            raise RuntimeError('No running Power Profiles Daemon')
        except BaseException:
            self.connection.close()
            raise

    def __exit__(self, *unused):
        self.connection.close()

    def _call(self, message):
        from jeepney.wrappers import unwrap_msg
        return unwrap_msg(self.connection.send_and_get_reply(message, timeout=2))

    def _check_owner(self):
        from jeepney.bus_messages import message_bus
        if self._call(message_bus.GetNameOwner(self.service))[0] != self.owner:
            raise RuntimeError('Power daemon changed; no automatic retry')

    @staticmethod
    def _string(value):
        if not isinstance(value, tuple) or len(value) != 2 or value[0] != 's' or not isinstance(value[1], str):
            raise ValueError('Invalid power profile string')
        return value[1]

    def read(self):
        captured = time.monotonic()
        self._check_owner()
        data = self._call(self.properties.get_all())[0]
        if not isinstance(data, dict):
            raise ValueError('Invalid power profile properties')
        variant = data.get('Profiles')
        if not isinstance(variant, tuple) or len(variant) != 2 or variant[0] != 'aa{sv}':
            raise ValueError('Invalid power profile list')
        rows = variant[1]
        if not isinstance(rows, list) or not 1 <= len(rows) <= 3 or any(not isinstance(row, dict) for row in rows):
            raise ValueError('Invalid power profile rows')
        profiles = [self._string(row.get('Profile')) for row in rows]
        current = self._string(data.get('ActiveProfile'))
        if any(profile not in PROFILES for profile in profiles) or len(set(profiles)) != len(profiles) or current not in profiles:
            raise ValueError('Power profile state is inconsistent')
        degraded = self._string(data['PerformanceDegraded']) if 'PerformanceDegraded' in data else ''
        self._check_owner()
        return dict(available=True, profiles=profiles, current=current,
                    backend=BACKEND, reason='Native profile state observed',
                    captured_at_monotonic=captured, owner=self.owner,
                    bus_id=self.bus_id, service=self.service, performance_degraded=degraded[:256])

    def set(self, profile, expected):
        before = self.read()
        for key in ('bus_id', 'owner', 'service', 'current', 'profiles'):
            if before.get(key) != expected.get(key):
                raise ValueError('Power backend or profile changed before dispatch; no profile changed')
        if profile not in before['profiles']:
            raise ValueError('Power profile is unavailable')
        self._check_owner()
        self._call(self.properties.set('ActiveProfile', 's', profile))
        try:
            after = self.read()
        except Exception:
            return dict(available=False, profiles=[], current=None, backend=BACKEND,
                        verified=False, previous=before['current'],
                        reason='Profile request dispatched; daemon readback unavailable. Inspect before retrying.')
        return dict(after, verified=after['current'] == profile, previous=before['current'])


def status():
    try:
        with PowerProfiles() as daemon:
            return daemon.read()
    except Exception:
        return dict(available=False, profiles=[], current=None, backend=BACKEND,
                    reason='No usable running Power Profiles Daemon; no service was started')
