"""Real qdbus property writes on a disposable session bus; no audio output."""
import asyncio
import os
from jeepney import MatchRule, new_method_return, new_error
from jeepney.bus_messages import message_bus
from jeepney.io.asyncio import open_dbus_router
from jeepney.low_level import HeaderFields
from ev.voice.media_focus import MediaFocus, MediaDucking

PLAYER = 'org.mpris.MediaPlayer2.Player'
XML = '''<node>
<interface name="org.freedesktop.DBus.Introspectable"><method name="Introspect"><arg direction="out" type="s"/></method></interface>
<interface name="org.freedesktop.DBus.Properties">
<method name="Get"><arg direction="in" type="s"/><arg direction="in" type="s"/><arg direction="out" type="v"/></method>
<method name="Set"><arg direction="in" type="s"/><arg direction="in" type="s"/><arg direction="in" type="v"/></method>
</interface>
<interface name="org.mpris.MediaPlayer2.Player"><property name="Volume" type="d" access="readwrite"/><property name="PlaybackStatus" type="s" access="read"/></interface>
</node>'''


class Player:
    volume = .8

    async def start(self):
        self.context = open_dbus_router()
        self.router = await self.context.__aenter__()
        self.filter = self.router.filter(MatchRule(type='method_call'), bufsize=32)
        self.queue = self.filter.__enter__()
        self.task = asyncio.create_task(self.serve())
        await self.router.send_and_get_reply(message_bus.RequestName('org.mpris.MediaPlayer2.duckfixture', 4))
        return self

    async def serve(self):
        while True:
            message = await self.queue.get()
            member = message.header.fields.get(HeaderFields.member)
            if member == 'Introspect':
                reply = new_method_return(message, 's', (XML,))
            elif member == 'Get' and message.body[0] == PLAYER:
                value = ('d', self.volume) if message.body[1] == 'Volume' else ('s', 'Playing')
                reply = new_method_return(message, 'v', (value,))
            elif member == 'Set' and message.body[:2] == (PLAYER, 'Volume'):
                signature, value = message.body[2]
                if signature != 'd':
                    print('Wrong Volume signature:', message.body, flush=True)
                    await self.router.send(new_error(message, 'org.freedesktop.DBus.Error.InvalidArgs'))
                    continue
                self.volume = value
                reply = new_method_return(message)
            else:
                reply = new_error(message, 'org.freedesktop.DBus.Error.UnknownMethod')
            await self.router.send(reply)

    async def close(self):
        await self.router.send_and_get_reply(message_bus.ReleaseName('org.mpris.MediaPlayer2.duckfixture'))
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        self.filter.__exit__(None, None, None)
        await self.context.__aexit__(None, None, None)


async def main():
    player = await Player().start()
    focus = MediaFocus(None, os.environ.copy)
    duck = MediaDucking(focus)
    try:
        await duck.begin()
        assert abs(player.volume - .28) < 1e-6, (player.volume, duck.saved)
        assert await duck.restore()
        assert player.volume == .8
        await duck.begin()
        player.volume = .6
        assert await duck.restore()
        assert player.volume == .6
        print('MPRIS duck, restore and user override passed on isolated bus')
    finally:
        await player.close()


asyncio.run(main())
