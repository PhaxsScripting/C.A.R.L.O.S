"""Only run inside the isolated test session bus."""
import asyncio
from jeepney import DBusAddress, MatchRule, new_method_return, new_signal, new_error
from jeepney.bus_messages import message_bus
from jeepney.io.asyncio import open_dbus_router
from jeepney.low_level import HeaderFields
from ev.session_lock import SessionLockMonitor, SERVICES


class Saver:
    def __init__(self,service,path):
        self.service,self.path=service,path
        self.context=open_dbus_router()
        self.active=False

    async def start(self):
        self.router=await self.context.__aenter__()
        self.filter=self.router.filter(MatchRule(type='method_call'),bufsize=32)
        self.queue=self.filter.__enter__()
        self.task=asyncio.create_task(self.serve())
        await self.router.send_and_get_reply(message_bus.RequestName(self.service,4))
        return self

    async def serve(self):
        while True:
            message=await self.queue.get()
            member=message.header.fields.get(HeaderFields.member)
            path=message.header.fields.get(HeaderFields.path)
            if path != self.path:
                reply=new_error(message,'org.freedesktop.DBus.Error.UnknownObject')
            elif member=='GetActive':reply=new_method_return(message,'b',(self.active,))
            elif member=='GetSessionIdleTime':reply=new_method_return(message,'u',(12,))
            else:reply=new_error(message,'org.freedesktop.DBus.Error.UnknownMethod')
            await self.router.send(reply)

    async def emit(self,active):
        self.active=active
        await self.router.send(new_signal(DBusAddress(self.path,interface=self.service),'ActiveChanged','b',(active,)))

    async def close(self):
        await self.router.send_and_get_reply(message_bus.ReleaseName(self.service))
        self.task.cancel();await asyncio.gather(self.task,return_exceptions=True)
        self.filter.__exit__(None,None,None)
        await self.context.__aexit__(None,None,None)


async def eventually(predicate):
    for _ in range(150):
        if predicate():return
        await asyncio.sleep(.02)
    raise AssertionError('Expected fresh session observation did not arrive')


async def main():
    for service,path in SERVICES:
        observed=[]
        saver=await Saver(service,path).start()
        monitor=SessionLockMonitor(lambda *row:observed.append(row))
        task=asyncio.create_task(monitor.run())
        try:
            await eventually(lambda:(False,12,service) in observed)
            await saver.emit(True)
            await eventually(lambda:observed[-1]==(True,None,service))
            await saver.close();saver=None
            await eventually(lambda:observed[-1]==(None,None,None))
            saver=await Saver(service,path).start()
            await eventually(lambda:observed[-1]==(False,12,service))
        finally:
            task.cancel();await asyncio.gather(task,return_exceptions=True)
            if saver is not None:await saver.close()
    print('desktop lock transports passed')


asyncio.run(main())
