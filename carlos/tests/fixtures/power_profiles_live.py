import asyncio
import os
from unittest.mock import patch
from jeepney import MatchRule, new_method_return, new_error
from jeepney.bus_messages import message_bus
from jeepney.io.asyncio import open_dbus_router
from jeepney.low_level import HeaderFields
from ev.tools.power_profiles import PowerProfiles, status, APIS
from ev.tools.native_settings import power_profile_set


class Daemon:
    def __init__(self, service):
        self.service = service
        self.current = 'balanced'
        self.writes = []
        self.ignore_write = False
        self.invalid = False
        self.release_after_write = False

    async def start(self):
        self.context = open_dbus_router()
        self.router = await self.context.__aenter__()
        self.filter = self.router.filter(MatchRule(type='method_call'), bufsize=32)
        self.queue = self.filter.__enter__()
        self.task = asyncio.create_task(self.serve())
        reply = await self.router.send_and_get_reply(message_bus.RequestName(self.service, 4))
        assert reply.body[0] == 1
        return self

    async def serve(self):
        while True:
            message = await self.queue.get()
            member = message.header.fields.get(HeaderFields.member)
            if member == 'GetAll' and message.body == (self.service,):
                props = {'ActiveProfile': ('s', self.current),
                         'Profiles': ('aa{sv}', [{'Profile': ('s', name)} for name in ('power-saver', 'balanced', 'performance')]),
                         'PerformanceDegraded': ('s', '')}
                if self.invalid:
                    props['Profiles'] = ('s', 'not a profile list')
                reply = new_method_return(message, 'a{sv}', (props,))
            elif member == 'Set' and message.body[:2] == (self.service, 'ActiveProfile'):
                signature, value = message.body[2]
                assert signature == 's' and value in {'power-saver', 'balanced', 'performance'}
                self.writes.append(value)
                if not self.ignore_write:
                    self.current = value
                if self.release_after_write:
                    await self.router.send_and_get_reply(message_bus.ReleaseName(self.service))
                reply = new_method_return(message)
            else:
                reply = new_error(message, 'org.freedesktop.DBus.Error.UnknownMethod')
            await self.router.send(reply)

    async def close(self):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        self.filter.__exit__(None, None, None)
        await self.context.__aexit__(None, None, None)


def apply(expected, profile):
    with PowerProfiles() as daemon:
        return daemon.set(profile, expected)


async def main():
    os.environ['DBUS_SYSTEM_BUS_ADDRESS'] = os.environ['DBUS_SESSION_BUS_ADDRESS']
    checks = 0
    for service, _path in APIS:
        daemon = await Daemon(service).start()
        try:
            before = await asyncio.to_thread(status)
            assert before['available'] and before['service'] == service and before['current'] == 'balanced'
            changed = await asyncio.to_thread(apply, before, 'power-saver')
            assert changed['verified'] and daemon.current == 'power-saver' and daemon.writes == ['power-saver']
            checks += 1
            expected = await asyncio.to_thread(status)
            daemon.current = 'performance'
            try:
                await asyncio.to_thread(apply, expected, 'balanced')
            except ValueError:
                pass
            else:
                raise AssertionError('Manual profile override overwritten')
            assert daemon.writes == ['power-saver']
            checks += 1
            expected = await asyncio.to_thread(status)
            daemon.ignore_write = True
            result = await asyncio.to_thread(apply, expected, 'balanced')
            assert result['verified'] is False and result['current'] == 'performance'
            checks += 1
            daemon.invalid = True
            assert (await asyncio.to_thread(status))['available'] is False
            daemon.invalid = False
            checks += 1
            daemon.ignore_write = False
            with patch('ev.tools.native_settings.checked', side_effect=RuntimeError('No KDE')):
                native = await asyncio.to_thread(power_profile_set, {'profile': 'balanced', 'expected_current': 'performance'}, None)
            assert native['verified'] and native['current'] == 'balanced'
            checks += 1
            expected = await asyncio.to_thread(status)
            daemon.release_after_write = True
            lost = await asyncio.to_thread(apply, expected, 'power-saver')
            assert lost['verified'] is False and lost['current'] is None
            checks += 1
        finally:
            await daemon.close()
        replacement = await Daemon(service).start()
        try:
            try:
                await asyncio.to_thread(apply, expected, 'power-saver')
            except ValueError:
                pass
            else:
                raise AssertionError('Replacement daemon inherited authorization')
            assert replacement.writes == []
            checks += 1
        finally:
            await replacement.close()
    assert (await asyncio.to_thread(status))['available'] is False
    print(f'{checks} modern/legacy D-Bus checks passed; real system bus untouched')


asyncio.run(main())
