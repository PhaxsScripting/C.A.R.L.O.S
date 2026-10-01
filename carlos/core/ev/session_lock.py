"""Read desktop lock state over the existing session bus."""
import asyncio
import time
from contextlib import ExitStack

from jeepney import DBusAddress, MatchRule, new_method_call
from jeepney.wrappers import unwrap_msg
from jeepney.bus_messages import message_bus
from jeepney.io.asyncio import open_dbus_router
from jeepney.low_level import HeaderFields


SERVICES = (
    ('org.freedesktop.ScreenSaver', '/ScreenSaver'),
    ('org.freedesktop.ScreenSaver', '/org/freedesktop/ScreenSaver'),
    *((name, '/' + name.replace('.', '/')) for name in (
        'org.gnome.ScreenSaver', 'org.cinnamon.ScreenSaver',
        'org.mate.ScreenSaver', 'org.xfce.ScreenSaver')),
)


class SessionLockMonitor:
    def __init__(self, observe):
        self.observe = observe
        self.router = None
        self.selected = None
        self.owner = None
        self.locked = None
        self.idle = None
        self.idle_supported = None
        self.last_checked = 0.0
        self.probe_timeout = 2.0

    async def call(self, message):
        reply = await asyncio.wait_for(self.router.send_and_get_reply(message), self.probe_timeout)
        return unwrap_msg(reply)

    def publish(self):
        self.observe(self.locked, self.idle, self.selected[0] if self.selected else None)

    def clear(self):
        self.selected = self.owner = self.locked = self.idle = self.idle_supported = None
        self.publish()

    async def discover(self):
        self.clear()
        names, = await self.call(message_bus.ListNames())
        for service, path in SERVICES:
            if service not in names:
                continue
            try:
                owner, = await self.call(message_bus.GetNameOwner(service))
                active, = await self.call(new_method_call(DBusAddress(path, owner, service), 'GetActive'))
                current_owner, = await self.call(message_bus.GetNameOwner(service))
                if type(active) is not bool or owner != current_owner:
                    continue
            except asyncio.CancelledError:
                raise
            except Exception:
                continue
            self.selected, self.owner, self.locked = (service, path), owner, active
            self.last_checked = time.monotonic()
            await self.read_idle()
            self.publish()
            return

    async def read_idle(self):
        if not self.selected or self.idle_supported is False or self.locked:
            self.idle = None
            return
        service, path = self.selected
        try:
            idle, = await self.call(new_method_call(DBusAddress(path, self.owner, service), 'GetSessionIdleTime'))
            if type(idle) is not int or idle < 0:
                raise ValueError('Invalid idle duration')
            self.idle, self.idle_supported = idle, True
        except asyncio.CancelledError:
            raise
        except Exception:
            self.idle, self.idle_supported = None, False

    async def refresh(self):
        if not self.selected:
            await self.discover()
            return
        service, path = self.selected
        if time.monotonic() - self.last_checked >= 30:
            owner, = await self.call(message_bus.GetNameOwner(service))
            if owner != self.owner:
                await self.discover()
                return
            active, = await self.call(new_method_call(DBusAddress(path, self.owner, service), 'GetActive'))
            if type(active) is not bool:
                raise ValueError('Invalid lock state')
            self.locked = active
            self.last_checked = time.monotonic()
        await self.read_idle()
        self.publish()

    async def receive(self, message):
        fields = message.header.fields
        interface, member = fields.get(HeaderFields.interface), fields.get(HeaderFields.member)
        if (interface == 'org.freedesktop.DBus' and member == 'NameOwnerChanged'
                and fields.get(HeaderFields.sender) == 'org.freedesktop.DBus'):
            if len(message.body) == 3 and message.body[0] in {s for s, _ in SERVICES}:
                await self.discover()
            return
        if not self.selected:
            return
        service, path = self.selected
        if (fields.get(HeaderFields.sender) != self.owner or interface != service
                or fields.get(HeaderFields.path) != path or member != 'ActiveChanged'):
            return
        if len(message.body) != 1 or type(message.body[0]) is not bool:
            self.clear()
            return
        self.locked = message.body[0]
        self.idle = None
        self.publish()

    async def run(self):
        try:
            while True:
                try:
                    async with open_dbus_router() as router:
                        self.router = router
                        queue = asyncio.Queue(maxsize=64)
                        rules = [MatchRule(type='signal', sender='org.freedesktop.DBus',
                                           interface='org.freedesktop.DBus', member='NameOwnerChanged')]
                        rules += [MatchRule(type='signal', interface=service, member='ActiveChanged', path=path)
                                  for service, path in SERVICES]
                        with ExitStack() as filters:
                            for rule in rules:
                                filters.enter_context(router.filter(rule, queue=queue))
                                await self.call(message_bus.AddMatch(rule))
                            await self.discover()
                            next_refresh = time.monotonic() + 5
                            while True:
                                try:
                                    message = await asyncio.wait_for(queue.get(), max(0, next_refresh - time.monotonic()))
                                except TimeoutError:
                                    await self.refresh()
                                    next_refresh = time.monotonic() + 5
                                else:
                                    await self.receive(message)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.clear()
                    await asyncio.sleep(5)
                finally:
                    self.router = None
        finally:
            self.clear()
